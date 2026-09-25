"""Tests for the harness hooks (guard.py, budget.py) — pipe hook JSON to stdin."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
GUARD = SCRIPTS / "guard.py"
BUDGET = SCRIPTS / "budget.py"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout


def task(tid: int = 1, **over) -> dict:
    base = {
        "id": tid, "goal": "add cache", "files": ["src/cache.py", "tests/test_cache.py"],
        "max_loc": 10, "test": "true", "depends_on": [], "status": "in_progress",
        "base_sha": None, "attempts": 0, "summary": None,
    }
    base.update(over)
    return base


def write_ledger(repo: Path, t: dict, **top) -> None:
    dg = repo / ".driftguard"
    dg.mkdir(exist_ok=True)
    (dg / "tasks.json").write_text(json.dumps({"tasks": [t], **top}))
    (dg / "current").write_text(str(t["id"]))


def run_hook(script: Path, repo: Path, payload=None, raw: str | None = None):
    data = raw if raw is not None else json.dumps(
        payload if payload is not None else
        {"hook_event_name": "x", "cwd": str(repo), "tool_input": {}})
    return subprocess.run([sys.executable, str(script)], input=data, cwd=repo,
                          capture_output=True, text=True)


def edit_event(repo: Path, rel: str) -> dict:
    return {"hook_event_name": "PreToolUse", "cwd": str(repo),
            "tool_name": "Write", "tool_input": {"file_path": str(repo / rel)}}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inert_without_current_file(self):
        p = run_hook(GUARD, self.repo, edit_event(self.repo, "src/evil.py"))
        self.assertEqual(p.returncode, 0)

    def test_allows_listed_file(self):
        write_ledger(self.repo, task())
        p = run_hook(GUARD, self.repo, edit_event(self.repo, "src/cache.py"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_allows_new_file_if_listed(self):
        write_ledger(self.repo, task(files=["src/new_module.py"]))
        p = run_hook(GUARD, self.repo, edit_event(self.repo, "src/new_module.py"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_allows_glob_match(self):
        write_ledger(self.repo, task(files=["src/**/*.py"]))
        p = run_hook(GUARD, self.repo, edit_event(self.repo, "src/deep/nest.py"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_blocks_unlisted_file_and_names_allowlist(self):
        write_ledger(self.repo, task())
        p = run_hook(GUARD, self.repo, edit_event(self.repo, "src/api.py"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("outside the file allowlist", p.stderr)
        self.assertIn("src/cache.py", p.stderr)  # allowed files are named
        self.assertIn("STOP and report", p.stderr)

    def test_blocks_path_outside_repo(self):
        write_ledger(self.repo, task())
        p = run_hook(GUARD, self.repo, {
            "hook_event_name": "PreToolUse", "cwd": str(self.repo),
            "tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}})
        self.assertEqual(p.returncode, 2)

    def test_malformed_input_allows_and_logs(self):
        write_ledger(self.repo, task())
        p = run_hook(GUARD, self.repo, raw="{not json")
        self.assertEqual(p.returncode, 0)
        log = self.repo / ".driftguard" / "traces" / "hook-errors.log"
        self.assertIn("malformed input", log.read_text())

    def test_missing_file_path_allows_and_logs(self):
        write_ledger(self.repo, task())
        p = run_hook(GUARD, self.repo)  # empty tool_input
        self.assertEqual(p.returncode, 0)
        log = self.repo / ".driftguard" / "traces" / "hook-errors.log"
        self.assertIn("file_path", log.read_text())


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "cache.py").write_text("old line\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def stop_event(self, **over):
        event = {"hook_event_name": "Stop", "cwd": str(self.repo),
                 "stop_hook_active": False}
        event.update(over)
        return event

    def test_inert_without_current_file(self):
        (self.repo / "src" / "cache.py").write_text("x\n" * 50)
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0)

    def test_under_budget_allows(self):
        write_ledger(self.repo, task(base_sha=self.base))
        (self.repo / "src" / "cache.py").write_text("a\nb\nc\n")  # +3 -1 = 4
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_exactly_at_budget_allows(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=4))
        (self.repo / "src" / "cache.py").write_text("a\nb\nc\n")  # +3 -1 = 4
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_over_budget_blocks_with_counts(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=3))
        (self.repo / "src" / "cache.py").write_text("a\nb\nc\n")  # +3 -1 = 4
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 2)
        self.assertIn("4 lines changed", p.stderr)
        self.assertIn("budget is 3", p.stderr)

    def test_stop_hook_active_never_blocks(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=1))
        (self.repo / "src" / "cache.py").write_text("x\n" * 50)
        p = run_hook(BUDGET, self.repo, self.stop_event(stop_hook_active=True))
        self.assertEqual(p.returncode, 0)

    def test_untracked_file_in_files_is_counted(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=3))
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_cache.py").write_text("t\n" * 5)  # untracked
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 2)
        self.assertIn("5 lines changed", p.stderr)

    def test_untracked_file_outside_files_not_counted(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=1))
        (self.repo / "notes.txt").write_text("n\n" * 20)
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_tests_excluded_from_budget(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=1),
                     tests_excluded_from_budget=True)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_cache.py").write_text("t\n" * 50)
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_tests_counted_by_default(self):
        write_ledger(self.repo, task(base_sha=self.base, max_loc=1))
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_cache.py").write_text("t\n" * 50)
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 2)

    def test_malformed_input_allows_and_logs(self):
        write_ledger(self.repo, task(base_sha=self.base))
        p = run_hook(BUDGET, self.repo, raw="garbage{")
        self.assertEqual(p.returncode, 0)
        log = self.repo / ".driftguard" / "traces" / "hook-errors.log"
        self.assertIn("malformed input", log.read_text())

    def test_no_base_sha_allows(self):
        write_ledger(self.repo, task())  # base_sha=None
        (self.repo / "src" / "cache.py").write_text("x\n" * 50)
        p = run_hook(BUDGET, self.repo, self.stop_event())
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
