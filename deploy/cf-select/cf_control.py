"""Same-origin management requests for two fixed systemd actions."""
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import subprocess
import time

from cf_workflow import RUN_ID, STATUS, lock, read_draft

SOCKET = Path("/run/wizardj-cf-control/control.sock")
PREFIX = "/assistant/cf-select/"
SYSTEMCTL = "/usr/bin/systemctl"
ORIGIN = os.environ["WIZARDJ_CF_ORIGIN"]


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(6)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != PREFIX + "operation.json":
            return self.reply(404, {})
        try:
            status = json.loads(STATUS.read_bytes())
            if status["state"] in {"testing", "publishing"} and time.time() - status["updated_at"] > 400:
                status["state"] = "failed"
                status["error"] = "Timeout"
            if status["state"] == "ready":
                read_draft(status["run"])
        except (OSError, ValueError, KeyError):
            status = {"state": "idle"}
        return self.reply(200, status)

    def do_POST(self):
        if self.headers.get("Origin") != ORIGIN or self.headers.get("X-WizardJ-CF-Action") != "1":
            return self.reply(403, {})
        if self.headers.get("Content-Length", "0") != "0" or self.headers.get("Transfer-Encoding"):
            return self.reply(400, {})
        if self.path == PREFIX + "test":
            unit = "wizardj-cf-test.service"
            run = None
        elif self.path.startswith(PREFIX + "publish/"):
            run = self.path.removeprefix(PREFIX + "publish/")
            if not RUN_ID.fullmatch(run):
                return self.reply(400, {})
            unit = f"wizardj-cf-publish@{run}.service"
        else:
            return self.reply(404, {})
        try:
            with lock():
                jobs = subprocess.check_output((SYSTEMCTL, "list-jobs", "--plain", "--no-legend"),
                                               text=True, timeout=4)
                if any(name in jobs for name in ("wizardj-cf-test.service", "wizardj-cf-select.service",
                                                "wizardj-cf-publish@")):
                    return self.reply(409, {"error": "busy"})
                if run:
                    read_draft(run)
            subprocess.run((SYSTEMCTL, "start", "--no-block", unit), check=True,
                           timeout=4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (BlockingIOError, FileNotFoundError, ValueError, KeyError):
            return self.reply(409, {"error": "busy_or_stale"})
        except (OSError, subprocess.SubprocessError):
            return self.reply(503, {"error": "unavailable"})
        return self.reply(202, {"accepted": True})


if __name__ == "__main__":
    SOCKET.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()
