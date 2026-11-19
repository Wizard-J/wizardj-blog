"""Responses client for ChatGPT subscription authorization or a project API key."""
import datetime as dt
import fcntl
from functools import lru_cache
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from state import require_private

RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"


class ModelError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, data=None, headers=None):
    try:
        return build_opener(NoRedirects()).open(Request(url, data=data, headers=headers or {}), timeout=60)
    except HTTPError as error:
        if os.getenv("WIZARDJ_OPENAI_API_KEY_FILE"):
            try:
                code = json.loads(error.read(16_384)).get("error", {}).get("code")
            except (ValueError, AttributeError, OSError):
                code = None
            if code in {"insufficient_quota", "credit_balance_exhausted"}:
                raise ModelError("OpenAI API 项目额度不可用，请核对所属组织的余额和额度状态") from None
            if error.code in {401, 403}:
                raise ModelError(f"OpenAI API 密钥或项目权限校验失败（HTTP {error.code}）") from None
            raise ModelError(f"OpenAI API 请求被拒绝（HTTP {error.code}）") from None
        raise ModelError(f"ChatGPT 请求被拒绝（HTTP {error.code}）") from None
    except (URLError, TimeoutError):
        raise ModelError("无法连接 ChatGPT 服务，请检查服务端网络") from None


def access_token():
    directory = Path(os.getenv("WIZARDJ_CHATGPT_DIR", "/var/lib/wizardj-assistant"))
    profile = directory / "credentials.json"
    if not profile.is_file():
        raise ModelError("博客助手尚未导入 ChatGPT 订阅授权")
    if profile.is_symlink() or profile.stat().st_mode & 0o077:
        raise ModelError("ChatGPT 授权文件的权限无效")
    with (directory / ".refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = json.loads(profile.read_text())
        if PLAN_SCOPE not in record.get("scopes", []):
            raise ModelError("未授予 ChatGPT 套餐使用权限")
        now = dt.datetime.now(dt.timezone.utc)
        expires_at = dt.datetime.fromisoformat(record["saved_at"]) + dt.timedelta(seconds=record["expires_in"])
        if expires_at > now + dt.timedelta(seconds=60):
            return record["access_token"]
        form = urlencode({
            "grant_type": "refresh_token", "client_id": record["client_id"],
            "refresh_token": record["refresh_token"], "resource": RESOURCE,
        }).encode()
        with request(TOKEN_URL, form, {"Content-Type": "application/x-www-form-urlencoded"}) as response:
            replacement = json.load(response)
        if not (replacement.get("access_token") and replacement.get("refresh_token")
                and replacement.get("expires_in", 0) > 0):
            raise ModelError("ChatGPT 续期结果不完整")
        record.update({k: replacement[k] for k in ("access_token", "refresh_token", "expires_in")})
        record["saved_at"] = now.isoformat()
        if "scope" in replacement:
            record["scopes"] = replacement["scope"].split()
        if PLAN_SCOPE not in record["scopes"]:
            raise ModelError("ChatGPT 套餐使用权限已撤销")
        fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(record, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, profile)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return record["access_token"]


@lru_cache(maxsize=1)
def subscribed_model(token):
    for _ in range(3):
        try:
            with request(RESOURCE + "/models", headers={"Authorization": "Bearer " + token}) as response:
                catalog = json.load(response).get("models") or []
        except (HTTPException, TimeoutError, json.JSONDecodeError):
            # The catalog is a read-only GET and safe to retry after an interrupted transfer.
            continue
        model = next((entry["slug"] for entry in catalog if entry.get("visibility") == "list"), None)
        if not model:
            raise ModelError("ChatGPT 账户没有返回可用模型")
        return model
    raise ModelError("ChatGPT 模型列表下载中断，请重试")


def response_settings():
    key_path = os.getenv("WIZARDJ_OPENAI_API_KEY_FILE")
    if not key_path:
        token = access_token()
        return os.getenv("WIZARDJ_MODEL") or subscribed_model(token), {"Authorization": "Bearer " + token}

    try:
        descriptor = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            require_private(os.fstat(handle.fileno()), stat.S_ISREG, "API key")
            key = handle.read(1024).strip()
    except (OSError, UnicodeError):
        raise ModelError("无法读取博客助手专用的 API 密钥文件或其权限无效") from None
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{10,900}", key):
        raise ModelError("博客助手的 API 密钥格式无效")
    project = os.getenv("WIZARDJ_OPENAI_PROJECT", "")
    if not re.fullmatch(r"proj_[A-Za-z0-9]+", project):
        raise ModelError("博客助手的 API 项目未配置")
    return os.getenv("WIZARDJ_MODEL") or "gpt-6.1-sol", {
        "Authorization": "Bearer " + key, "OpenAI-Project": project,
    }


def stream(messages, *, max_tokens):
    model, bearer = response_settings()
    payload = json.dumps({
        "model": model, "input": messages, "store": False, "stream": True,
    }).encode()
    headers = dict(bearer, **{"Content-Type": "application/json", "Accept": "text/event-stream"})
    parts = 0
    with request(RESOURCE + "/responses", payload, headers) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                break
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "response.output_text.delta":
                delta = event.get("delta", "")
                parts += len(delta)
                if parts > max_tokens * 6:
                    raise ModelError("模型输出超过长度限制")
                yield delta
            elif kind == "response.completed":
                if not parts:
                    for item in event.get("response", {}).get("output", []):
                        for block in item.get("content", []):
                            if block.get("type") == "output_text" and block.get("text"):
                                parts += len(block["text"])
                                if parts > max_tokens * 6:
                                    raise ModelError("模型输出超过长度限制")
                                yield block["text"]
                if not parts:
                    raise ModelError("模型没有返回文本")
                return
            elif kind == "error" and not event.get("code") and not event.get("message"):
                continue  # A bare event can precede the detailed response.failed event.
            elif kind in {"response.failed", "response.incomplete", "error"}:
                detail = event.get("response", {}).get("error") or event
                if detail.get("code") in {"insufficient_quota", "credit_balance_exhausted"}:
                    raise ModelError("OpenAI API 项目额度不可用，请核对所属组织的余额和额度状态")
                if detail.get("code") in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}:
                    raise ModelError("ChatGPT 套餐额度当前不可用")
                raise ModelError("ChatGPT 未完成此次生成")
    raise ModelError("ChatGPT 响应中断")


def complete(messages, *, max_tokens):
    try:
        return "".join(stream(messages, max_tokens=max_tokens))
    except (HTTPException, TimeoutError, ConnectionError, json.JSONDecodeError):
        raise ModelError("ChatGPT 响应中断，请重试") from None
