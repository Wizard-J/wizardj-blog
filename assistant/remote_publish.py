"""Send a staged Hugo release over a dedicated, restricted SSH identity."""

import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

REVISION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,99}\Z")
IDENTIFIER = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
ORIGIN = "https://wizardj.cn"


class RemotePublishError(RuntimeError):
    """Deployment preflight, transfer, or ECS activation failed."""


class StaleRevisionError(RemotePublishError):
    """ECS changed and has no release for this publication ID."""


class RemotePublisher:
    def __init__(self, key_file, known_hosts, host, user="root"):
        self.command = [
            "/usr/bin/ssh", "-F", "/dev/null", "-T",
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none", "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ClearAllForwardings=yes",
            "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2",
            "-o", "UserKnownHostsFile=" + str(Path(known_hosts).expanduser()),
            "-i", str(Path(key_file).expanduser()), f"{user}@{host}",
        ]

    def _request(self, arguments, stream=None):
        try:
            result = subprocess.run(
                [*self.command, *arguments],
                stdin=stream if stream is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=100, check=False,
            )
            payload = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise RemotePublishError("未收到 ECS 发布确认，请保留草稿后重试") from None
        if not isinstance(payload, dict):
            raise RemotePublishError("ECS 返回了无效的发布结果")
        errors = {
            "stale": "线上版本已变化，请刷新后重新发布",
            "invalid_archive": "ECS 拒绝了无效的站点构建",
            "incomplete": "构建缺少现有文章路径或必要文件",
            "verification": "线上页面校验失败，ECS 已尝试恢复旧版本",
            "conflict": "ECS 上已有不同内容使用同一发布编号",
            "busy": "ECS 正在执行另一项发布，请稍后重试",
        }
        if result.returncode or not payload.get("ok"):
            code = payload.get("error")
            if code == "stale_unpublished":
                raise StaleRevisionError("线上版本已变化，本次发布尚未上线")
            raise RemotePublishError(errors.get(code, "ECS 未完成发布，请稍后重试"))
        return payload

    def revision(self):
        value = self._request(["revision"]).get("revision")
        if not isinstance(value, str) or not REVISION.fullmatch(value):
            raise RemotePublishError("无法识别 ECS 当前版本")
        return value

    def deploy(self, release_dir, identifier, public_path, base_revision):
        if (not IDENTIFIER.fullmatch(identifier) or not REVISION.fullmatch(base_revision)
                or public_path != f"/archives/{identifier}/"):
            raise ValueError("无效的发布编号、文章地址或基准版本")
        release_dir = Path(release_dir)
        article = release_dir / public_path.strip("/") / "index.html"
        digest = hashlib.sha256(article.read_bytes()).hexdigest()
        with tempfile.TemporaryFile() as bundle:
            with tarfile.open(fileobj=bundle, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(release_dir.rglob("*")):
                    relative = path.relative_to(release_dir)
                    if any(part.startswith(".") for part in relative.parts):
                        continue
                    if path.is_symlink() or not (path.is_dir() or path.is_file()):
                        raise RemotePublishError("待发布构建包含无效的文件")
                    if path.is_file():
                        archive.add(path, arcname=relative.as_posix(), recursive=False)
                        if bundle.tell() > MAX_ARCHIVE_BYTES:
                            raise RemotePublishError("待发布构建超出大小上限")
            if bundle.tell() > MAX_ARCHIVE_BYTES:
                raise RemotePublishError("待发布构建超出大小上限")
            bundle.seek(0)
            payload = self._request(["deploy", identifier, base_revision, digest], bundle)
        url = ORIGIN + public_path
        revision = "publish-" + identifier
        if payload.get("url") != url or payload.get("revision") != revision:
            raise RemotePublishError("ECS 返回的文章地址与本次发布不符")
        return {"url": url, "revision": revision}
