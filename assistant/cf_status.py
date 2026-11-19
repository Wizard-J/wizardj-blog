"""Produce a sanitized snapshot of the latest Cloudflare selector run."""
import json
import re

UNIT = "wizardj-cf-select.service"
TIMER = "wizardj-cf-select.timer"

SOURCES = {"current", "dns", "template", "official_sample"}
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
            keys = ("tested", "passed", "cutoff_ms", "connectivity", "jitter", "slow", "relative")
            summary["totals"] = dict(zip(keys, map(int, match.groups())))
        elif match := SELECTED.fullmatch(message):
            keys = ("count", "openai_min", "openai_max", "client_min", "client_max", "client_worst")
            summary["selected"] = dict(zip(keys, map(int, match.groups())))
        elif message == "subscription published and public HTTPS bytes verified":
            summary["published"] = True
    return summary


def collect(cmd):
    def properties(unit, *names):
        output = cmd("env", "TZ=Asia/Shanghai", "LC_ALL=C", "systemctl", "show",
                     "--timestamp=unix", unit, *("-p" + key for key in names))
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    service = properties(UNIT, "ActiveState", "LoadState", "Result", "ExecMainStartTimestamp")
    timer = properties(TIMER, "ActiveState", "NextElapseUSecRealtime")
    start = service.get("ExecMainStartTimestamp", "")
    lines = []
    if re.fullmatch(r"@\d{1,12}", start):
        lines = cmd("journalctl", "-u", UNIT, "--since=" + start,
                    "-n", "250", "-o", "json", "--no-pager").splitlines()
    return {
        **run_summary(lines),
        "service": service.get("ActiveState", "unknown") if service.get("LoadState") == "loaded" else "unknown",
        "result": service.get("Result") if lines else None,
        "started": int(start[1:]) if re.fullmatch(r"@\d{1,12}", start) else None,
        "timer_active": timer.get("ActiveState") == "active",
        "next_run": timer.get("NextElapseUSecRealtime") if timer.get("NextElapseUSecRealtime") not in {None, "n/a"} else None,
    }
