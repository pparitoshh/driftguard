"""Tests for scripts/contract.py — the harness task ledger (stdlib unittest)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import contract  # noqa: E402


def task(tid: int, **over) -> dict:
    base = {
        "id": tid,
        "goal": f"do thing {tid}",
        "files": [f"src/thing{tid}.py"],
        "max_loc": 80,
        "test": f"python3 -m unittest tests.test_thing{tid}",
        "depends_on": [],
        "status": "todo",
        "base_sha": None,
        "attempts": 0,
        "summary": None,
    }
    base.update(over)
    return base


def ledger(*tasks, **over) -> dict:
    data = {"spec": "SPEC.md", "tasks": list(tasks)}
    data.update(over)
    return data


@contextmanager
def cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class ValidateTest(unittest.TestCase):
    def test_valid_ledger(self):
        self.assertEqual(contract.validate(ledger(task(1), task(2, depends_on=[1]))), [])

    def test_reports_every_problem_not_just_first(self):
        bad = ledger(task(1, goal="", max_loc=0), task(1, status="nope"))
        problems = contract.validate(bad)
        text = "\n".join(problems)
        self.assertGreaterEqual(len(problems), 3)
        self.assertIn("'goal' must be a non-empty string", text)
        self.assertIn("'max_loc' must be a positive integer", text)
        self.assertIn("duplicate id 1", text)
        self.assertIn("'status' must be one of", text)

    def test_not_a_dict(self):
        self.assertEqual(contract.validate([1, 2]), ["top level must be a JSON object"])

    def test_missing_tasks_and_unknown_top_key(self):
        problems = contract.validate({"spec": "SPEC.md", "taskz": []})
        text = "\n".join(problems)
        self.assertIn("unknown top-level key 'taskz'", text)
        self.assertIn("'tasks' must be a non-empty list", text)

    def test_missing_and_unknown_task_keys(self):
        t = task(1)
        del t["goal"]
        t["gaol"] = "typo"
        text = "\n".join(contract.validate(ledger(t)))
        self.assertIn("missing keys: goal", text)
        self.assertIn("unknown key 'gaol'", text)

    def test_reject_escaping_and_absolute_files(self):
        for f in ("../evil.py", "/etc/passwd", "a/../../b.py", ""):
            t = task(1, files=["src/ok.py", f])
            text = "\n".join(contract.validate(ledger(t)))
            self.assertIn("repo-relative", text, f)

    def test_globs_are_allowed(self):
        self.assertEqual(contract.validate(ledger(task(1, files=["src/**/*.py"]))), [])

    def test_bad_depends_on(self):
        text = "\n".join(contract.validate(ledger(
            task(1, depends_on=[99]), task(2, depends_on=[2]), task(3, depends_on=["x"]))))
        self.assertIn("depends on unknown task 99", text)
        self.assertIn("cannot depend on itself", text)
        self.assertIn("must be an integer", text)

    def test_dependency_cycle_detected(self):
        text = "\n".join(contract.validate(ledger(
            task(1, depends_on=[2]), task(2, depends_on=[3]), task(3, depends_on=[1]))))
        self.assertIn("dependency cycle", text)

    def test_at_most_one_in_progress(self):
        text = "\n".join(contract.validate(ledger(
            task(1, status="in_progress"), task(2, status="in_progress"))))
        self.assertIn("multiple in_progress tasks", text)

    def test_scalars_type_checked(self):
        text = "\n".join(contract.validate(ledger(task(
            1, id=True, max_loc=True, attempts=-1, base_sha=5, summary=[]))))
        self.assertIn("'id' must be a positive integer", text)
        self.assertIn("'max_loc' must be a positive integer", text)
        self.assertIn("'attempts' must be a non-negative integer", text)
        self.assertIn("'base_sha' must be a string or null", text)
        self.assertIn("'summary' must be a string or null", text)

    def test_top_level_types(self):
        text = "\n".join(contract.validate(
            ledger(task(1), spec=5, tests_excluded_from_budget="yes")))
        self.assertIn("'spec' must be a string", text)
        self.assertIn("'tests_excluded_from_budget' must be a boolean", text)


class QueryTest(unittest.TestCase):
    def test_next_task_order_and_deps(self):
        c = ledger(task(1), task(2, depends_on=[1]), task(3))
        self.assertEqual(contract.next_task(c)["id"], 1)
        contract.set_status(c, 1, "done")
        self.assertEqual(contract.next_task(c)["id"], 2)  # file order beats id order
        contract.set_status(c, 2, "done")
        self.assertEqual(contract.next_task(c)["id"], 3)
        contract.set_status(c, 3, "done")
        self.assertIsNone(contract.next_task(c))

    def test_next_task_skips_blocked_and_in_progress(self):
        c = ledger(task(1, status="blocked"), task(2, status="in_progress"), task(3))
        self.assertEqual(contract.next_task(c)["id"], 3)

    def test_current_task(self):
        c = ledger(task(1), task(2, status="in_progress"))
        self.assertEqual(contract.current_task(c)["id"], 2)
        self.assertIsNone(contract.current_task(ledger(task(1))))

    def test_set_status_errors(self):
        c = ledger(task(1))
        with self.assertRaises(ValueError):
            contract.set_status(c, 1, "nope")
        with self.assertRaises(KeyError):
            contract.set_status(c, 99, "done")


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".driftguard" / "tasks.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_creates_dirs_and_round_trips(self):
        c = ledger(task(1))
        contract.save(c, self.path)
        self.assertEqual(contract.load(self.path), c)

    def test_save_is_atomic_no_temp_left(self):
        contract.save(ledger(task(1)), self.path)
        leftovers = [p.name for p in self.path.parent.iterdir()]
        self.assertEqual(leftovers, ["tasks.json"])

    def test_load_missing_and_malformed(self):
        with self.assertRaises(FileNotFoundError):
            contract.load(self.path)
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        with self.assertRaises(ValueError):
            contract.load(self.path)
        self.path.write_text("[1, 2]")
        with self.assertRaises(ValueError):
            contract.load(self.path)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with cwd(self.tmp.name), redirect_stdout(out), redirect_stderr(err):
            rc = contract.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def write_ledger(self, c: dict) -> None:
        contract.save(c, Path(self.tmp.name) / ".driftguard" / "tasks.json")

    def test_validate_ok_and_bad(self):
        self.write_ledger(ledger(task(1)))
        rc, out, _ = self.run_cli("validate")
        self.assertEqual(rc, 0)
        self.assertIn("ok", out)
        self.write_ledger(ledger(task(1, goal=""), task(1)))
        rc, out, _ = self.run_cli("validate")
        self.assertEqual(rc, 1)
        self.assertIn("problem(s)", out)  # all problems listed, not just first

    def test_validate_missing_file(self):
        rc, _, err = self.run_cli("validate")
        self.assertEqual(rc, 1)
        self.assertIn("no ledger", err)

    def test_next_and_null(self):
        self.write_ledger(ledger(task(1)))
        rc, out, _ = self.run_cli("next")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["id"], 1)
        self.write_ledger(ledger(task(1, status="done")))
        rc, out, _ = self.run_cli("next")
        self.assertEqual(rc, 0)
        self.assertIsNone(json.loads(out))

    def test_status_updates_and_persists(self):
        self.write_ledger(ledger(task(1)))
        rc, out, _ = self.run_cli("status", "1", "in_progress")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "in_progress")
        rc, out, _ = self.run_cli("show")
        self.assertEqual(json.loads(out)["tasks"][0]["status"], "in_progress")

    def test_status_unknown_task(self):
        self.write_ledger(ledger(task(1)))
        rc, _, err = self.run_cli("status", "99", "done")
        self.assertEqual(rc, 1)
        self.assertIn("no task with id 99", err)

    def test_commands_refuse_invalid_ledger(self):
        self.write_ledger(ledger(task(1, goal="")))
        for argv in (("next",), ("show",), ("status", "1", "done")):
            rc, _, err = self.run_cli(*argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("invalid", err)


if __name__ == "__main__":
    unittest.main()
