"""Phase 2 acceptance: contract build/validate/propose with FakeBackend."""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent import contract as contract_mod

VALID = {
    "task": "add add() to calc",
    "base": "main",
    "files_allowed": ["calc.py", "test_calc.py"],
    "files_create": ["calc.py"],
    "loc_budget": 50,
    "tests_required": ["test_calc.py::test_add"],
    "test_cmd": "python3 -m pytest {tests} -q",
    "forbidden": ["new class"],
    "tdd": True,
    "max_iterations": 40,
}


@contextmanager
def cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class ValidateTest(unittest.TestCase):
    def test_valid_contract(self):
        self.assertEqual(contract_mod.validate(dict(VALID)), [])

    def test_reject_budget_over_300(self):
        bad = dict(VALID, loc_budget=400)
        self.assertIn("split the task", "; ".join(contract_mod.validate(bad)))

    def test_reject_test_outside_allowed(self):
        bad = dict(VALID, tests_required=["test_other.py::test_x"])
        self.assertIn("outside files_allowed", "; ".join(contract_mod.validate(bad)))

    def test_reject_test_cmd_without_placeholder(self):
        bad = dict(VALID, test_cmd="pytest")
        self.assertIn("{tests}", "; ".join(contract_mod.validate(bad)))

    def test_reject_test_cmd_placeholder_glued_to_shell(self):
        bad = dict(VALID, test_cmd="pytest x{tests}")
        self.assertIn("{tests}", "; ".join(contract_mod.validate(bad)))

    def test_reject_non_positive_token_and_cost_caps(self):
        self.assertEqual(contract_mod.validate(dict(VALID, max_tokens=None, max_cost_usd=None)), [])
        errors = "; ".join(contract_mod.validate(dict(VALID, max_tokens=0, max_cost_usd="1")))
        self.assertIn("max_tokens must be a positive number", errors)
        self.assertIn("max_cost_usd must be a positive number", errors)

    def test_reject_escaping_paths(self):
        for path in ("../evil.py", "/etc/passwd", "a/../../b.py"):
            bad = dict(VALID, files_allowed=VALID["files_allowed"] + [path])
            self.assertIn("repo-relative", "; ".join(contract_mod.validate(bad)), path)

    def test_reject_creates_outside_allowed(self):
        bad = dict(VALID, files_create=["nope.py"])
        self.assertIn("subset", "; ".join(contract_mod.validate(bad)))

    def test_reject_missing_key(self):
        self.assertIn("missing key", "; ".join(contract_mod.validate({})))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_round_trip(self):
        with cwd(self.tmp.name):
            rc = contract_mod.main(["--json", json.dumps(VALID)])
            written = json.loads(
                (Path(self.tmp.name) / ".driftguard" / "contract.json").read_text()
            )
        self.assertEqual(rc, 0)
        self.assertEqual(written, VALID)

    def test_flags_build(self):
        with cwd(self.tmp.name):
            rc = contract_mod.main([
                "--task", "add add()", "--files", "calc.py", "test_calc.py",
                "--creates", "calc.py", "--tests", "test_calc.py::test_add",
                "--budget", "50",
            ])
        self.assertEqual(rc, 0)

    def test_invalid_json_rejected(self):
        with cwd(self.tmp.name):
            rc = contract_mod.main(["--json", json.dumps(dict(VALID, loc_budget=999))])
        self.assertEqual(rc, 1)

    def test_propose_approved_writes(self):
        backend_fn = lambda system, transcript: json.dumps(VALID)
        with cwd(self.tmp.name), patch("builtins.input", return_value="y"):
            rc = contract_mod.main(
                ["--propose", "add add() to calc"], backend_fn=backend_fn
            )
            written = (Path(self.tmp.name) / ".driftguard" / "contract.json").exists()
        self.assertEqual(rc, 0)
        self.assertTrue(written)

    def test_propose_declined_writes_nothing(self):
        backend_fn = lambda system, transcript: json.dumps(VALID)
        with cwd(self.tmp.name), patch("builtins.input", return_value="n"):
            rc = contract_mod.main(
                ["--propose", "add add() to calc"], backend_fn=backend_fn
            )
            written = (Path(self.tmp.name) / ".driftguard" / "contract.json").exists()
        self.assertEqual(rc, 1)
        self.assertFalse(written)

    def test_propose_invalid_then_retry_then_abort(self):
        calls = []

        def backend_fn(system, transcript):
            calls.append(system)
            return json.dumps(dict(VALID, loc_budget=999))

        with cwd(self.tmp.name):
            rc = contract_mod.main(["--propose", "add add()"], backend_fn=backend_fn)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("previous draft was invalid", calls[1])


if __name__ == "__main__":
    unittest.main()
