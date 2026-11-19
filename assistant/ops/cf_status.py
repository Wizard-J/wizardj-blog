"""Produce a sanitized snapshot of the latest Cloudflare selector run."""
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

UNIT = "wizardj-cf-select.service"
TIMER = "wizardj-cf-select.timer"

SOURCES = {"current", "historical", "dns", "template", "official_sample"}
RESULT_FILE = Path("/var/lib/home-weixin-status/cf-run-result.json")
TOTAL_FIELDS = ("tested", "passed", "cutoff_ms", "connectivity", "jitter", "slow", "relative")
SELECTED_FIELDS = ("count", "openai_min", "openai_max", "client_min", "client_max", "client_worst")
SOURCE = re.compile(r"candidate_source=([a-z_]+) tested=(\d+) passed=(\d+)")
TOTAL = re.compile(
    r"tested=(\d+) passed=(\d+) client_worst_cutoff_ms=(\d+) "
    r"rejected_connectivity=(\d+) rejected_jitter=(\d+) rejected_slow=(\d+) rejected_relative=(\d+)"
)
SELECTED = re.compile(
    r"nodes_selected=(\d+) openai_median_ms=(\d+)-(\d+) "
    r"client_median_ms=(\d+)-(\d+) client_worst_ms=(\d+)"
)


def run_summary(lines):
    summary = {"sources": {}, "totals": None, "selected": None, "published": False}
    for line in lines:
        message = json.loads(line).get("MESSAGE")
        if not isinstance(message, str):
            continue
        if match := SOURCE.fullmatch(message):
            source, tested, passed = match.groups()
            if source in SOURCES:
                summary["sources"][source] = {"tested": int(tested), "passed": int(passed)}
        elif match := TOTAL.fullmatch(message):
            summary["totals"] = dict(zip(TOTAL_FIELDS, map(int, match.groups())))
        elif match := SELECTED.fullmatch(message):
            summary["selected"] = dict(zip(SELECTED_FIELDS, map(int, match.groups())))
        elif message == "subscription published and public HTTPS bytes verified":
            summary["published"] = True
    return summary


def validate_counts(row, fields):
    if not isinstance(row, dict) or set(row) != set(fields):
        raise ValueError("invalid result fields")
    if any(type(value) is not int or not 0 <= value <= 10**9 for value in row.values()):
        raise ValueError("invalid result count")
    return row


def validate_summary(summary):
    if not isinstance(summary, dict) or set(summary) != {"sources", "totals", "selected", "published"}:
        raise ValueError("invalid summary")
    sources = summary["sources"]
    if not isinstance(sources, dict) or not set(sources) <= SOURCES:
        raise ValueError("invalid sources")
    for source in sources.values():
        validate_counts(source, ("tested", "passed"))
    for key, fields in (("totals", TOTAL_FIELDS), ("selected", SELECTED_FIELDS)):
        if summary[key] is not None:
            validate_counts(summary[key], fields)
    if type(summary["published"]) is not bool:
        raise ValueError("invalid publication state")
    return summary


def write_result(started, summary, path=RESULT_FILE):
    """Write a run result atomically after the selector and publication step finish."""
    if type(started) is not int or started <= 0:
        raise ValueError("invalid start time")
    result = {"version": 1, "started": started, "summary": validate_summary(summary)}
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump(result, handle)
            handle.flush()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def read_result(started, path):
    if started is None:
        return None
    try:
        result = json.loads(path.read_text())
        if isinstance(result, dict) and result.get("version") == 1 and result.get("started") == started:
            return validate_summary(result.get("summary"))
    except (OSError, ValueError):
        pass
    return None


def collect(cmd, result_file=RESULT_FILE):
    def properties(unit, *names):
        output = cmd("env", "TZ=Asia/Shanghai", "LC_ALL=C", "systemctl", "show",
                     "--timestamp=unix", unit, *("-p" + key for key in names))
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    service = properties(UNIT, "ActiveState", "LoadState", "Result", "ExecMainStartTimestamp")
    timer = properties(TIMER, "ActiveState", "NextElapseUSecRealtime")
    start = service.get("ExecMainStartTimestamp", "")
    started = int(start[1:]) if re.fullmatch(r"@\d{1,12}", start) else None
    summary = read_result(started, result_file)
    if summary is None:
        lines = cmd("journalctl", "-u", UNIT, "--since=" + start,
                    "-n", "250", "-o", "json", "--no-pager").splitlines() if started else []
        summary = run_summary(lines)
    return {
        **summary,
        "service": service.get("ActiveState", "unknown") if service.get("LoadState") == "loaded" else "unknown",
        "result": service.get("Result") if started else None,
        "started": started,
        "timer_active": timer.get("ActiveState") == "active",
        "next_run": timer.get("NextElapseUSecRealtime") if timer.get("NextElapseUSecRealtime") not in {None, "n/a"} else None,
    }
