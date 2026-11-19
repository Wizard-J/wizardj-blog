import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assistant" / "ops"))
from cf_status import collect, run_summary, write_result


def rows(*messages):
    return [json.dumps({"MESSAGE": message}) for message in messages]


class CloudflareStatusTests(unittest.TestCase):
    def test_legacy_logs_only_expose_known_fields(self):
        summary = run_summary(rows(
            "candidate_source=official_sample tested=23 passed=0",
            "candidate_source=private tested=10 passed=8",
            "tested=36 passed=2 client_worst_cutoff_ms=1492 rejected_connectivity=28 "
            "rejected_jitter=6 rejected_slow=0 rejected_relative=0",
            "nodes_selected=2 openai_median_ms=1486-1921 client_median_ms=1242-1310 client_worst_ms=1434",
            "subscription published and public HTTPS bytes verified",
        ))
        self.assertEqual(summary["sources"], {"official_sample": {"tested": 23, "passed": 0}})
        self.assertEqual(summary["totals"]["connectivity"], 28)
        self.assertEqual(summary["selected"]["client_worst"], 1434)
        self.assertTrue(summary["published"])

    def test_current_result_wins_and_stale_or_invalid_result_falls_back(self):
        def cmd(*args):
            if "wizardj-cf-select.service" in args and "systemctl" in args:
                return "LoadState=loaded\nActiveState=inactive\nResult=success\nExecMainStartTimestamp=@100\n"
            if "systemctl" in args:
                return "ActiveState=active\nNextElapseUSecRealtime=n/a\n"
            return "\n".join(rows("candidate_source=dns tested=1 passed=0"))

        with TemporaryDirectory() as tmp:
            path = Path(tmp, "summary.json")
            summary = run_summary(rows("candidate_source=current tested=8 passed=1"))
            write_result(100, summary, path)
            self.assertEqual(collect(cmd, path)["sources"], summary["sources"])
            write_result(99, summary, path)
            self.assertEqual(collect(cmd, path)["sources"], {"dns": {"tested": 1, "passed": 0}})
            path.write_text('{"version":1,"started":100,"summary":{"secret":"not a result"}}')
            self.assertEqual(collect(cmd, path)["sources"], {"dns": {"tested": 1, "passed": 0}})
            self.assertFalse(collect(cmd, path)["published"])


if __name__ == "__main__":
    unittest.main()
