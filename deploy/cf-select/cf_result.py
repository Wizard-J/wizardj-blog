import subprocess

from cf_status import RESULT_FILE, read_result, write_result


def started():
    value = subprocess.check_output((
        "/usr/bin/systemctl", "show", "wizardj-cf-select.service",
        "--timestamp=unix", "-p", "ExecMainStartTimestamp", "--value",
    ), text=True, timeout=4).strip()
    if not value.startswith("@"):
        raise ValueError("missing unit start time")
    return int(value[1:])


def record(summary):
    try:
        write_result(started(), summary)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"structured result unavailable ({type(error).__name__})", flush=True)


def confirm_publication():
    try:
        timestamp = started()
        summary = read_result(timestamp, RESULT_FILE)
        if summary is None:
            raise ValueError("missing current result")
        summary["published"] = True
        write_result(timestamp, summary)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"structured publication receipt unavailable ({type(error).__name__})", flush=True)
