from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "cf-select"))
from cf_policy import choose, history_candidates, update_history


class SelectionTests(unittest.TestCase):
    def test_regular_selection_caps_at_six(self):
        ranked = [(str(i), i) for i in range(8)]
        self.assertEqual(choose(ranked, ranked, set(), 8), ranked[:6])

    def test_previous_nodes_fill_to_eight_only_after_retest(self):
        candidates = [(str(i), i) for i in range(9)]
        previous = {row[0] for row in candidates[3:8]}
        self.assertEqual(choose(candidates[:3], candidates, previous, 8), candidates[:8])
        self.assertEqual(choose(candidates[:3], candidates[:7], previous, 8), [])

    def test_history_gives_repeated_success_priority(self):
        history = update_history({"a": {"passes": 2, "worst": 2}}, [("a", 2), ("b", 1)])
        self.assertEqual(history_candidates(history), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
