"""Run selection and publication under the same host-wide lock."""
from contextlib import contextmanager
import fcntl
import grp
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import cf_select as selector
from cf_result import record
from cf_status import validate_summary
import subscription_publish

DRAFT = selector.ETC / "prepared-run.json"
LOCK = selector.ETC / ".selection.lock"
STATUS = Path("/var/lib/home-weixin-status/cf-operation.json")
RUN_ID = re.compile(r"[0-9a-f]{32}")
DRAFT_LIFETIME = 3600


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def baseline():
    return {key: fingerprint(path) for key, path in (
        ("live", selector.LIVE), ("template", selector.PROFILE), ("artifact", selector.ARTIFACT)
    )}


@contextmanager
def lock():
    with LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_status(run, state, summary=None, error=None):
    data = {"run": run, "state": state, "updated_at": int(time.time()),
            "summary": validate_summary(summary) if summary is not None else None, "error": error}
    selector.replace(STATUS, selector.encode(data), 0o644)


def read_draft(run):
    data = json.loads(DRAFT.read_bytes())
    if data["run"] != run or time.time() - data["created"] > DRAFT_LIFETIME:
        raise ValueError("stale run")
    if data["baseline"] != baseline():
        raise ValueError("deployment changed since test")
    validate_summary(data["summary"])
    return data


def prepare(run, scheduled):
    DRAFT.unlink(missing_ok=True)
    write_status(run, "testing")
    base = baseline()
    summary = None

    def save(result, live, artifact):
        nonlocal summary
        summary = result
        if scheduled:
            record(result)
        if live is None:
            return
        data = {"run": run, "created": int(time.time()), "baseline": base, "summary": result,
                "live": live.decode(), "artifact": artifact.decode()}
        selector.replace(DRAFT, selector.encode(data), 0o600)

    if selector.run(False, prepare=save):
        write_status(run, "ready", summary)
        return True
    write_status(run, "held", summary)
    return False


def publish(run, scheduled):
    data = read_draft(run)
    summary = data["summary"]
    write_status(run, "publishing", summary)
    raw = selector.LIVE.read_bytes()
    prior_artifact = selector.ARTIFACT.read_bytes() if selector.ARTIFACT.exists() else None
    gid = grp.getgrnam(selector.USER).gr_gid
    try:
        if not selector.activate(raw, data["live"].encode(), data["artifact"].encode(), gid):
            raise RuntimeError("egress validation failed")
        subscription_publish.main(record_receipt=False)
    except Exception:
        # The publisher handles the public rollback. Restore the local state as well.
        if selector.LIVE.read_bytes() != raw:
            selector.replace(selector.LIVE, raw, 0o640, gid)
            selector.restart()
        if prior_artifact is None:
            selector.ARTIFACT.unlink(missing_ok=True)
        else:
            selector.replace(selector.ARTIFACT, prior_artifact, 0o600)
        raise
    summary["published"] = True
    if scheduled:
        record(summary)
    write_status(run, "published", summary)
    DRAFT.unlink(missing_ok=True)


def main(mode, run):
    with lock():
        try:
            if mode in {"scheduled", "test"}:
                ready = prepare(run, mode == "scheduled")
                if mode == "test" or not ready:
                    return
            publish(run, mode == "scheduled")
        except Exception as error:
            # Do not replace a newer run's status when an old browser submits its ID.
            try:
                latest = json.loads(STATUS.read_bytes())
            except (OSError, ValueError):
                latest = {}
            if latest.get("run") == run:
                write_status(run, "failed", latest.get("summary"), type(error).__name__)
            raise


if __name__ == "__main__":
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in {"scheduled", "test", "publish"}:
        raise SystemExit(2)
    run = sys.argv[2] if mode == "publish" and len(sys.argv) == 3 else uuid.uuid4().hex
    if not RUN_ID.fullmatch(run) or len(sys.argv) != (3 if mode == "publish" else 2):
        raise SystemExit(2)
    try:
        main(mode, run)
    except Exception as error:
        print(f"node selection operation failed ({type(error).__name__})", flush=True)
        raise SystemExit(1)
