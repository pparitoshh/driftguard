"""Phase 3 acceptance: gate checks against a tmp repo, no git involved."""

import json
import tempfile
import unittest
from pathlib import Path

from agent import gate

TEST_SRC = (
    "import unittest\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_ok(self):\n"
    "        self.assertEqual(1 + 1, 2)\n"
)
CONTRACT = {
    "task": "t",
    "base": "main",
    "files_allowed": ["thing.py", "test_thing.py"],
    "files_create": ["thing.py"],
    "loc_budget": 20,
    "tests_required": ["test_thing.T.test_ok"],
    "test_cmd": "python3 -m unittest {tests}",
    "forbidden": [],
    "tdd": True,
    "max_iterations": 40,
}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".driftguard").mkdir()
        (self.repo / "test_thing.py").write_text(TEST_SRC)
        self.state = {
            "first_writes": {"test_thing.py": 1.0, "thing.py": 2.0},
            "added_loc": 5,
            "iterations": 4,
            "gate_failures": 0,
            "denials": 0,
            "baseline": {},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def learnings(self):
        path = self.repo / ".driftguard" / "learnings.md"
        return path.read_text() if path.exists() else ""

    def test_passing_tests_pass_gate_and_write_metrics(self):
        ok, reason = gate.check(CONTRACT, self.state, self.repo)
        self.assertTrue(ok)
        self.assertEqual(reason, "gate passed")
        metrics = json.loads(
            (self.repo / ".driftguard" / "metrics.jsonl").read_text().strip()
        )
        self.assertEqual(metrics["actual_loc"], 5)
        self.assertEqual(metrics["outcome"], "pass")

    def test_failing_tests_block_and_log_learning(self):
        (self.repo / "test_thing.py").write_text(
            TEST_SRC.replace("1 + 1, 2", "1 + 1, 3")
        )
        ok, reason = gate.check(CONTRACT, self.state, self.repo)
        self.assertFalse(ok)
        self.assertIn("tests failing:", reason)
        self.assertEqual(self.state["gate_failures"], 1)
        self.assertIn("[harness]", self.learnings())
        self.assertIn("tests:", self.learnings())

    def test_impl_written_before_test_blocks(self):
        self.state["first_writes"] = {"thing.py": 1.0, "test_thing.py": 2.0}
        ok, reason = gate.check(CONTRACT, self.state, self.repo)
        self.assertFalse(ok)
        self.assertIn("test written after impl: thing.py", reason)
        self.assertIn("tdd:", self.learnings())

    def test_missing_write_entries_block(self):
        self.state["first_writes"] = {"test_thing.py": 1.0}
        ok, _ = gate.check(CONTRACT, self.state, self.repo)
        self.assertFalse(ok)

    def test_over_budget_blocks(self):
        self.state["added_loc"] = 31
        ok, reason = gate.check(CONTRACT, self.state, self.repo)
        self.assertFalse(ok)
        self.assertIn("over budget: 31/20", reason)

    def test_circuit_breaker_releases_to_human(self):
        self.state["gate_failures"] = 3
        ok, reason = gate.check(CONTRACT, self.state, self.repo)
        self.assertTrue(ok)
        self.assertEqual(reason, "circuit breaker: released to human")


if __name__ == "__main__":
    unittest.main()
