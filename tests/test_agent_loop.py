"""Phase 1 acceptance: ReAct loop with a scripted FakeBackend in a tmp repo."""

import json
import tempfile
import unittest
from pathlib import Path

from agent import run as runner

TEST_SRC = (
    "import unittest\n"
    "from calc import add\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(add(1, 2), 3)\n"
)
IMPL_SRC = "def add(a, b):\n    return a + b\n"


def action(name, **args):
    return json.dumps({"thought": "t", "action": name, "args": args})


class FakeBackend:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, system, transcript):
        if self.replies:
            return self.replies.pop(0)
        return action("done", summary="script exhausted")


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        repo = Path(self.tmp.name)
        (repo / ".driftguard").mkdir()
        contract = {
            "task": "implement calc.add",
            "base": "main",
            "files_allowed": ["calc.py", "test_calc.py"],
            "files_create": ["calc.py"],
            "loc_budget": 30,
            "tests_required": ["test_calc.T.test_add"],
            "test_cmd": "python3 -m unittest {tests}",
            "forbidden": ["logging"],
            "tdd": True,
            "max_iterations": 20,
        }
        (repo / ".driftguard" / "contract.json").write_text(json.dumps(contract))
        self.repo = repo
        self.contract_path = str(repo / ".driftguard" / "contract.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_loop(self, script):
        return runner.run(self.contract_path, str(self.repo), FakeBackend(script))

    def transcript(self):
        log = self.repo / ".driftguard" / "agent_transcript.jsonl"
        return log.read_text()

    def state(self):
        return json.loads((self.repo / ".driftguard" / "state.json").read_text())

    def test_happy_path_reaches_gate(self):
        rc = self.run_loop([
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("run_tests"),
            action("done", summary="implemented"),
        ])
        self.assertEqual(rc, 0)
        self.assertEqual((self.repo / "calc.py").read_text(), IMPL_SRC)
        self.assertGreater(self.state()["added_loc"], 0)

    def test_out_of_scope_write_denied_then_recovered(self):
        rc = self.run_loop([
            action("write_file", path="evil.py", content="x = 1\n"),
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertEqual(rc, 0)
        self.assertFalse((self.repo / "evil.py").exists())
        self.assertIn("outside contract: evil.py", self.transcript())
        self.assertEqual(self.state()["denials"], 1)

    def test_tdd_first_write_must_be_test(self):
        rc = self.run_loop([
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertEqual(rc, 0)
        self.assertIn("TDD: first write must be test_calc.py", self.transcript())

    def test_forbidden_pattern_denied(self):
        rc = self.run_loop([
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content="import logging\ndef add(a, b):\n    return a + b\n"),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertEqual(rc, 0)
        self.assertIn("forbidden: logging", self.transcript())

    def test_budget_denied_at_one_and_half_x(self):
        contract = json.loads(Path(self.contract_path).read_text())
        contract["loc_budget"] = 3
        Path(self.contract_path).write_text(json.dumps(contract))
        self.run_loop([
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("done", summary="done"),
        ])
        self.assertIn("over budget:", self.transcript())
        self.assertFalse((self.repo / "test_calc.py").exists())

    def test_malformed_reply_retried_once_then_aborts(self):
        rc = self.run_loop(["not json at all", "still not json"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.transcript().count("malformed reply"), 2)

    def test_gate_failure_returns_agent_to_loop(self):
        rc = self.run_loop([
            action("done", summary="too early"),
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertEqual(rc, 0)
        self.assertIn("gate: tests failing:", self.transcript())

    def test_max_iterations_circuit_breaker(self):
        contract = json.loads(Path(self.contract_path).read_text())
        contract["max_iterations"] = 3
        Path(self.contract_path).write_text(json.dumps(contract))
        rc = self.run_loop([action("read_file", path="calc.py")] * 3)
        self.assertEqual(rc, 1)
        self.assertEqual(self.state()["iterations"], 3)

    def test_gate_circuit_breaker_releases_to_human(self):
        rc = self.run_loop([action("done", summary="too early")])
        self.assertEqual(rc, 0)
        self.assertEqual(self.state()["gate_failures"], 3)
        self.assertIn("released to human", self.transcript())


if __name__ == "__main__":
    unittest.main()
