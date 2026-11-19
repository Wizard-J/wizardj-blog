"""Manually run Wizard Frontier and keep the resulting draft private."""

import fcntl
import json
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from frontier import collect_sources, generate_article, review_article, write_draft
from model_client import ModelError
from state import require_private


INTERRUPTED = "上次试跑中断，请重新发起。"


class FrontierRunner:
    def __init__(self, directory, model_lock):
        self.directory = Path(directory).expanduser().absolute()
        if self.directory.is_symlink() or {"content", "public", "static", "releases"}.intersection(
            self.directory.resolve().parts
        ):
            raise ValueError("前沿站草稿目录必须位于发布目录之外")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private(self.directory.lstat(), stat.S_ISDIR, "Frontier draft directory")
        self.model_lock = model_lock

    def _lock(self):
        descriptor = os.open(self.directory / ".run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            require_private(os.fstat(descriptor), stat.S_ISREG, "Frontier run lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _state_path(self, identifier):
        if not isinstance(identifier, str) or str(UUID(identifier, version=4)) != identifier:
            raise ValueError("试跑编号无效")
        return self.directory / (identifier + ".json")

    def _save_state(self, state):
        destination = self._state_path(state["id"])
        fd, temp_path = tempfile.mkstemp(prefix=".run-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, destination)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @staticmethod
    def _read_private(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            require_private(os.fstat(stream.fileno()), stat.S_ISREG, "Frontier output")
            return stream.read()

    def _read_state(self, identifier):
        try:
            return json.loads(self._read_private(self._state_path(identifier)))
        except FileNotFoundError:
            return None

    def _filename(self, identifier):
        self._state_path(identifier)
        return f"wizard-frontier-{identifier}.md"

    def start(self):
        try:
            lock = self._lock()
        except BlockingIOError:
            raise ValueError("巫师前沿站已有一次试跑正在进行") from None
        if not self.model_lock.acquire(blocking=False):
            os.close(lock)
            raise ValueError("助手正在使用模型，请稍后再试")
        state = {"id": str(uuid4()), "status": "running"}
        try:
            self._save_state(state)
            Thread(target=self._run, args=(state["id"], lock), daemon=True).start()
        except BaseException:
            self.model_lock.release()
            os.close(lock)
            raise
        return state

    def _run(self, identifier, lock):
        try:
            sources = collect_sources(4)
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            title, content = generate_article(sources, now)
            issues = review_article(title, content, sources)

            with tempfile.TemporaryDirectory(prefix=".draft-", dir=self.directory) as staging:
                draft = Path(staging) / "draft.md"
                write_draft(draft, title, content, sources, issues, now)
                with draft.open("rb") as article:
                    os.fsync(article.fileno())
                os.link(draft, self.directory / self._filename(identifier))
            self._save_state({
                "id": identifier,
                "status": "done",
                "title": title,
                "review": "needs-review" if issues else "passed",
            })
        except ModelError as error:
            self._save_state({"id": identifier, "status": "failed", "error": str(error)})
        except Exception:
            self._save_state({
                "id": identifier, "status": "failed",
                "error": "未能完成前沿站试跑，请稍后重试。",
            })
        finally:
            self.model_lock.release()
            os.close(lock)

    def status(self, identifier):
        state = self._read_state(identifier)
        if not state or state["status"] != "running":
            return state
        try:
            lock = self._lock()
        except BlockingIOError:
            return state
        try:
            state = self._read_state(identifier)
            if state["status"] == "running":
                try:
                    data = self._read_private(self.directory / self._filename(identifier))
                    meta, _ = json.JSONDecoder().raw_decode(data.decode("utf-8"))
                    if meta["draft"] is not True:
                        raise ValueError
                    state = {
                        "id": identifier, "status": "done", "title": meta["title"],
                        "review": meta["params"]["review"],
                    }
                except (OSError, ValueError, KeyError):
                    state = {"id": identifier, "status": "failed", "error": INTERRUPTED}
                self._save_state(state)
            return state
        finally:
            os.close(lock)

    def download(self, identifier):
        state = self.status(identifier)
        if not state or state["status"] != "done":
            raise ValueError("试跑还没有可下载的草稿")
        filename = self._filename(identifier)
        try:
            return filename, self._read_private(self.directory / filename)
        except (OSError, ValueError):
            raise ValueError("未能读取私有草稿") from None
