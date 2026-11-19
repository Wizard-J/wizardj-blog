"""Forced SSH command for atomic deployment of a complete, static Hugo release."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

BASE = Path("/srv/wizardj.cn")
DOMAIN = "wizardj.cn"
ORIGIN = "https://" + DOMAIN
REVISION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,99}\Z")
IDENTIFIER = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_ENTRIES = 4096
PUBLIC_SUFFIXES = {
    ".html", ".xml", ".css", ".js", ".txt", ".json", ".webmanifest", ".svg",
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".pdf", ".mp4", ".webm", ".mp3", ".ogg",
}


class Rejected(RuntimeError):
    pass


def current_revision(base):
    releases = base / "releases"
    pointer = base / "current"
    if not pointer.is_symlink() or not pointer.is_dir():
        raise Rejected("invalid_revision")
    path = pointer.resolve(strict=True)
    if path.parent != releases.resolve(strict=True) or not REVISION.fullmatch(path.name):
        raise Rejected("invalid_revision")
    return path.name


def archive_path(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or name != path.as_posix()
            or len(name.encode("utf-8")) > 1024 or len(path.parts) > 12
            or any(part.startswith(".") or len(part.encode("utf-8")) > 255 for part in path.parts)
            or any(ord(char) < 32 or ord(char) == 127 or char in "\\" for char in name)
            or path.parts[0] in {"assistant", ".well-known"}
            or path.suffix.lower() not in PUBLIC_SUFFIXES):
        raise Rejected("invalid_archive")
    return path


def extract_release(stream, staging):
    with tempfile.TemporaryFile() as bundle:
        total = 0
        while True:
            data = stream.read(1024 * 1024)
            if not data:
                break
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise Rejected("invalid_archive")
            bundle.write(data)
        bundle.seek(0)
        total_extracted = 0
        seen = set()
        try:
            with tarfile.open(fileobj=bundle, mode="r:") as archive:
                for entry in archive:
                    path = archive_path(entry.name)
                    if (path in seen or len(seen) >= MAX_ENTRIES
                            or entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                            or entry.size > MAX_FILE_BYTES or entry.size < 0):
                        raise Rejected("invalid_archive")
                    seen.add(path)
                    total_extracted += entry.size
                    if total_extracted > MAX_ARCHIVE_BYTES:
                        raise Rejected("invalid_archive")
                    target = staging.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with archive.extractfile(entry) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=64 * 1024)
                    target.chmod(0o644)
        except (tarfile.TarError, UnicodeError, OSError, EOFError):
            raise Rejected("invalid_archive") from None


@contextmanager
def deployment_lock(base):
    with (base / ".assistant-publish.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("busy") from None
        yield


def switch_current(base, target):
    with tempfile.TemporaryDirectory(dir=base, prefix=".assistant-switch.") as directory:
        staged_pointer = Path(directory) / "current"
        staged_pointer.symlink_to(target)
        os.replace(staged_pointer, base / "current")


def tree_snapshot(directory):
    return {
        path.relative_to(directory): hashlib.sha256(path.read_bytes()).digest()
        for path in directory.rglob("*") if path.is_file()
    }


def verify_live(path, expected):
    try:
        response = subprocess.run([
            "/usr/bin/curl", "-fsS", "--noproxy", "*", "--proto", "=https", "--max-time", "8",
            "--max-filesize", str(MAX_FILE_BYTES),
            "--resolve", f"{DOMAIN}:443:127.0.0.1", ORIGIN + path,
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return hashlib.sha256(response.stdout).digest() == expected


def deploy(base, identifier, expected_revision, digest, stream, verify=verify_live):
    with deployment_lock(base):
        return deploy_locked(base, identifier, expected_revision, digest, stream, verify)


def deploy_locked(base, identifier, expected_revision, digest, stream, verify):
    releases = base / "releases"
    revision = "publish-" + identifier
    destination = releases / revision
    public_path = f"/archives/{identifier}/"
    article_path = Path(public_path.strip("/")) / "index.html"
    result = {"ok": True, "revision": revision, "url": ORIGIN + public_path}
    staging = Path(tempfile.mkdtemp(dir=releases, prefix=".assistant-stage."))
    try:
        extract_release(stream, staging)
        for path in (Path("index.html"), Path("index.xml"), Path("sitemap.xml"), article_path):
            file = staging / path
            if not file.is_file() or not file.stat().st_size:
                raise Rejected("incomplete")
        if hashlib.sha256((staging / article_path).read_bytes()).hexdigest() != digest:
            raise Rejected("invalid_archive")
        article_digest = bytes.fromhex(digest)
        home_digest = hashlib.sha256((staging / "index.html").read_bytes()).digest()
        if f"{ORIGIN}{public_path}".encode() not in (staging / "sitemap.xml").read_bytes():
            raise Rejected("incomplete")

        active = current_revision(base)
        if active not in {revision, expected_revision}:
            raise Rejected("stale" if destination.exists() else "stale_unpublished")
        if destination.exists():
            if destination.is_symlink() or tree_snapshot(staging) != tree_snapshot(destination):
                raise Rejected("conflict")
        elif active == revision:
            raise Rejected("conflict")
        if active == revision:
            if not verify(public_path, article_digest) or not verify("/", home_digest):
                raise Rejected("verification")
            return result

        previous = releases / active
        for existing in (previous / "archives").rglob("index.html"):
            if not (staging / existing.relative_to(previous)).is_file():
                raise Rejected("incomplete")
        if not destination.exists():
            staging.chmod(0o755)
            staging.rename(destination)
        if current_revision(base) != active:
            raise Rejected("stale")
        try:
            switch_current(base, destination)
            if not verify(public_path, article_digest) or not verify("/", home_digest):
                raise Rejected("verification")
        except BaseException:
            if current_revision(base) == revision:
                switch_current(base, previous)
            raise
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def deadline(_signal, _frame):
    raise Rejected("timeout")


def run(command, stream, base=BASE):
    if command == "revision":
        return {"ok": True, "revision": current_revision(base)}
    arguments = command.split(" ")
    if (len(arguments) != 4 or arguments[0] != "deploy" or not IDENTIFIER.fullmatch(arguments[1])
            or not REVISION.fullmatch(arguments[2]) or not DIGEST.fullmatch(arguments[3])):
        raise Rejected("invalid_command")
    return deploy(base, *arguments[1:], stream)


def main():
    os.umask(0o022)
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(90)
    try:
        response = run(os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdin.buffer)
    except Rejected as error:
        print(json.dumps({"ok": False, "error": str(error)}), flush=True)
        return 1
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error": "server_error"}), flush=True)
        return 1
    finally:
        signal.alarm(0)
    print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
