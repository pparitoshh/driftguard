"""Phase 1 acceptance: ReAct loop with a scripted FakeBackend in a tmp repo."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from agent import backend
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

    def __call__(self, system, transcript, usage=None):
        if usage is not None:
            backend.add_usage(usage, {"usage": {"input_tokens": 100, "output_tokens": 10},
                                      "total_cost_usd": 0.01})
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

    def test_protected_path_write_denied(self):
        self.run_loop([
            action("write_file", path="agent/evil.py", content="x = 1\n"),
            action("done", summary="done"),
        ])
        self.assertFalse((self.repo / "agent" / "evil.py").exists())
        self.assertIn("protected path: agent/evil.py", self.transcript())

    def test_write_escaping_repo_denied(self):
        outside = Path(self.tmp.name).parent / "escaped_calc.py"
        contract = json.loads(Path(self.contract_path).read_text())
        contract["files_allowed"].append("../escaped_calc.py")
        contract["tdd"] = False
        Path(self.contract_path).write_text(json.dumps(contract))
        self.run_loop([
            action("write_file", path="../escaped_calc.py", content="x = 1\n"),
            action("done", summary="done"),
        ])
        self.assertFalse(outside.exists())
        self.assertIn("outside repo: ../escaped_calc.py", self.transcript())

    def test_symlink_escaping_repo_denied(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.repo / "calc.py").symlink_to(Path(outside.name) / "calc.py")
        self.run_loop([
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertFalse((Path(outside.name) / "calc.py").exists())
        self.assertIn("outside repo: calc.py", self.transcript())

    def test_test_cmd_not_run_through_shell(self):
        marker = self.repo / "pwned"
        contract = json.loads(Path(self.contract_path).read_text())
        contract["test_cmd"] = f"python3 -m unittest {{tests}}; touch {marker}"
        Path(self.contract_path).write_text(json.dumps(contract))
        self.run_loop([action("run_tests"), action("done", summary="done")])
        self.assertFalse(marker.exists())

    def test_main_cli_wires_contract_and_repo(self):
        rc = runner.main(
            ["--contract", self.contract_path, "--repo", str(self.repo)],
            FakeBackend([
                action("write_file", path="test_calc.py", content=TEST_SRC),
                action("write_file", path="calc.py", content=IMPL_SRC),
                action("done", summary="done"),
            ]),
        )
        self.assertEqual(rc, 0)
        self.assertEqual((self.repo / "calc.py").read_text(), IMPL_SRC)

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

    def set_contract(self, **changes):
        contract = json.loads(Path(self.contract_path).read_text())
        contract.update(changes)
        Path(self.contract_path).write_text(json.dumps(contract))

    def test_usage_tracked_and_stats_printed(self):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.run_loop([
                action("write_file", path="test_calc.py", content=TEST_SRC),
                action("write_file", path="calc.py", content=IMPL_SRC),
                action("done", summary="done"),
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(self.state()["usage"]["calls"], 3)
        self.assertEqual(self.state()["usage"]["input_tokens"], 300)
        self.assertIn("stats: iterations 3/20 · calls 3 · tokens in 300 out 30 · cost $0.0300",
                      out.getvalue())

    def test_token_budget_aborts(self):
        self.set_contract(max_tokens=200)
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.run_loop([action("read_file", path="calc.py")] * 5)
        self.assertEqual(rc, 1)
        self.assertEqual(self.state()["usage"]["calls"], 2)
        self.assertIn("token budget spent: 220/200", out.getvalue())

    def test_cost_budget_aborts(self):
        self.set_contract(max_cost_usd=0.015)
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = self.run_loop([action("read_file", path="calc.py")] * 5)
        self.assertEqual(rc, 1)
        self.assertIn("cost budget spent: $0.0200/$0.015", self.transcript())

    def test_malformed_reply_retried_then_aborts(self):
        rc = self.run_loop(["not json at all", "still not json", "nope"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.transcript().count("malformed reply"), 3)
        self.assertEqual(self.state()["iterations"], 3)

    def test_backend_error_is_surfaced_and_retried(self):
        replies = iter([action("write_file", path="test_calc.py", content=TEST_SRC),
                        action("write_file", path="calc.py", content=IMPL_SRC),
                        action("done", summary="done")])

        def flaky(system, transcript, usage=None, calls=[0]):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("claude CLI error: API Error: flagged")
            return next(replies)

        rc = runner.run(self.contract_path, str(self.repo), flaky)
        self.assertEqual(rc, 0)
        self.assertIn("claude CLI error: API Error: flagged", self.transcript())

    def test_prompt_names_required_tests_and_command(self):
        seen = []

        def capture(system, transcript, usage=None):
            seen.append(system)
            return action("done", summary="x")

        runner.run(self.contract_path, str(self.repo), capture)
        self.assertIn("test_calc.T.test_add", seen[0])
        self.assertIn("python3 -m unittest {tests}", seen[0])

    def test_deleting_existing_code_denied(self):
        (self.repo / "calc.py").write_text("def mul(a, b):\n    return a * b\n")
        rc = self.run_loop([
            action("write_file", path="test_calc.py", content=TEST_SRC),
            action("write_file", path="calc.py", content=IMPL_SRC),
            action("write_file", path="calc.py",
                   content="def mul(a, b):\n    return a * b\n\n\n" + IMPL_SRC),
            action("done", summary="done"),
        ])
        self.assertEqual(rc, 0)
        self.assertIn("deletes 2 existing LOC (max 0)", self.transcript())
        self.assertIn("def mul", (self.repo / "calc.py").read_text())

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
        self.assertEqual(rc, runner.EXIT_RELEASED)
        self.assertEqual(self.state()["gate_failures"], 3)
        self.assertIn("released to human", self.transcript())


class BackendTest(unittest.TestCase):
    def fake_proc(self, rc, out, err=""):
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": err})()

    def reply(self, result, is_error=False):
        return json.dumps({"result": result, "is_error": is_error, "total_cost_usd": 0.02,
                           "usage": {"input_tokens": 5, "cache_read_input_tokens": 1000,
                                     "cache_creation_input_tokens": 200, "output_tokens": 30}})

    def test_usage_accumulated_and_result_returned(self):
        usage = {}
        with patch.object(backend.subprocess, "run",
                          return_value=self.fake_proc(0, self.reply('{"action": "done"}'))):
            self.assertEqual(backend.chat("s", [], usage), '{"action": "done"}')
            backend.chat("s", [], usage)
        self.assertEqual(usage, {"calls": 2, "input_tokens": 2410,
                                 "output_tokens": 60, "cost_usd": 0.04})

    def test_is_error_reply_raises_and_still_counts_usage(self):
        usage = {}
        with patch.object(backend.subprocess, "run",
                          return_value=self.fake_proc(0, self.reply("API Error: flagged", True))):
            with self.assertRaisesRegex(RuntimeError, "API Error: flagged"):
                backend.chat("s", [], usage)
        self.assertEqual(usage["calls"], 1)

    def test_non_json_output_raises(self):
        with patch.object(backend.subprocess, "run",
                          return_value=self.fake_proc(0, "API Error: flagged\n")):
            with self.assertRaisesRegex(RuntimeError, "API Error: flagged"):
                backend.chat("s", [])

    def test_nonzero_exit_raises_with_stderr(self):
        with patch.object(backend.subprocess, "run",
                          return_value=self.fake_proc(1, "", "Not logged in\n")):
            with self.assertRaisesRegex(RuntimeError, "Not logged in"):
                backend.chat("s", [])

    def test_model_pinned_and_overridable(self):
        with patch.object(backend.subprocess, "run",
                          return_value=self.fake_proc(0, self.reply("{}"))) as run:
            backend.chat("s", [])
            self.assertEqual(run.call_args.args[0][-2:], ["--model", "sonnet"])
            with patch.dict("os.environ", {"DRIFTGUARD_MODEL": "opus"}):
                backend.chat("s", [])
            self.assertEqual(run.call_args.args[0][-1], "opus")


if __name__ == "__main__":
    unittest.main()
