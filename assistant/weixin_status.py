"""Read-only status view for the cloud Weixin bot."""
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import urlsplit

UNIT = "home-weixin-chat.service"
ROOT = Path(__file__).resolve().parent
PAGE = "/assistant/weixin/"
SNAPSHOT = Path("/var/lib/home-weixin-status/status.json")
EVENT = re.compile(
    r"(openai_api_reply_generated|weixin_reply_accepted|gpt_reply_failed|"
    r"weixin_reply_failed|weixin_poll_failed)(?: ([a-zA-Z0-9_.-]{1,64}))?"
)
LABELS = {
    "openai_api_reply_generated": ("模型已生成回复", ""),
    "weixin_reply_accepted": ("微信接口已接受回复", ""),
    "gpt_reply_failed": ("模型请求失败", "GPT 请求未完成"),
    "weixin_reply_failed": ("微信发送失败", "微信发送接口返回错误"),
    "weixin_poll_failed": ("微信收取失败", "微信收取接口返回错误"),
}


def cmd(*argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=4).stdout


def snapshot():
    values = dict(line.split("=", 1) for line in cmd(
        "systemctl", "show", UNIT, "-p", "LoadState", "-p", "ActiveState",
        "-p", "Environment",
    ).splitlines() if "=" in line)
    env = dict(item.split("=", 1) for item in shlex.split(values.get("Environment", "")) if "=" in item)
    model = env.get("WEIXIN_OPENAI_MODEL", "")
    project = env.get("WEIXIN_OPENAI_PROJECT", "")
    events = deque(maxlen=18)
    latest = {}
    journal = subprocess.run(
        ("journalctl", "-u", UNIT, "--since=7 days ago", "--reverse",
         "--grep=^(?:" + EVENT.pattern + ")$",
         "-n", "100", "-o", "json", "--no-pager"),
        capture_output=True, text=True, timeout=4,
    )
    if journal.returncode != 0 and not (journal.returncode == 1 and not journal.stderr):
        journal.check_returncode()
    for line in reversed(journal.stdout.splitlines()):
        row = json.loads(line)
        match = EVENT.fullmatch(row.get("MESSAGE", "")) if isinstance(row.get("MESSAGE"), str) else None
        if not match:
            continue
        key, detail = match.groups()
        label, reason = LABELS[key]
        if key in {"weixin_reply_failed", "weixin_poll_failed"} and detail and re.fullmatch(r"-?[0-9]{1,9}|[A-Za-z]{1,30}Error|Error", detail):
            reason += f"（{detail}）"
        timestamp = datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, timezone.utc)
        event = {"time": timestamp.isoformat(), "label": label, "reason": reason}
        events.appendleft(event)
        latest["failure" if reason else key] = event
    return {
        "state": values.get("ActiveState", "unknown") if values.get("LoadState") == "loaded" else "unknown",
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "model": model if re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", model) else None,
        "project": project if re.fullmatch(r"proj_[a-zA-Z0-9]{1,80}", project) else None,
        "last_generated": latest.get("openai_api_reply_generated"),
        "last_accepted": latest.get("weixin_reply_accepted"),
        "last_failure": latest.get("failure"),
        "events": list(events),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        routes = {
            PAGE: ("weixin-status.html", "text/html; charset=utf-8"),
            PAGE + "status.css": ("weixin-status.css", "text/css; charset=utf-8"),
            PAGE + "status.js": ("weixin-status.js", "text/javascript; charset=utf-8"),
        }
        if path in routes:
            name, content_type = routes[path]
            return self.send_data(200, content_type, (ROOT / name).read_bytes())
        if path != PAGE + "status.json":
            return self.send_data(404, "text/plain", b"Not found")
        try:
            data = json.loads(SNAPSHOT.read_bytes())
            age = datetime.now(timezone.utc) - datetime.fromisoformat(data["sampled_at"])
            if age.total_seconds() > 90:
                raise ValueError("stale snapshot")
            status = 200
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            data, status = {"error": "暂时无法读取机器人状态。"}, 503
        self.send_data(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def send_data(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if sys.argv[1:] == ["--collect"]:
        data = snapshot()
        tmp = SNAPSHOT.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.chmod(tmp, 0o640)
        tmp.replace(SNAPSHOT)
    else:
        ThreadingHTTPServer(("127.0.0.1", 18767), Handler).serve_forever()
