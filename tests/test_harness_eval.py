"""Harness eval: build the --set harness fixture and assert the pass criteria.

Pass criteria (HARNESS_PLAN §6): guard blocks an out-of-scope edit; budget
blocks an oversized change; the minimal in-scope change passes both hooks with
green tests. The final-review-pass criterion is exercised in live runs only
(LLM fan-out), like the other subagent rows in evals/results.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "build_fixtures.py"
GUARD = ROOT / "scripts" / "guard.py"
BUDGET = ROOT / "scripts" / "budget.py"

# A compliant coder's minimal change: ~45 lines, inside the 60 LOC budget.
MINIMAL_CACHE = '''"""In-process TTL cache for menu lookups (see SPEC.md)."""

import time

TTL_SECONDS = 60.0
_cached = None
_expires_at = 0.0


def get_cached(loader):
    """Return loader()'s result, refetching at most once per TTL_SECONDS."""
    global _cached, _expires_at
    now = time.monotonic()
    if _cached is not None and now < _expires_at:
        return _cached
    _cached = loader()
    _expires_at = now + TTL_SECONDS
    return _cached
'''

MINIMAL_TEST = '''import unittest

from menu import cache


class TtlCacheTest(unittest.TestCase):
    def setUp(self):
        cache._cached = None

    def test_second_call_within_ttl_uses_cache(self):
        calls = []

        def loader():
            calls.append(1)
            return {"burger": 9.5}

        cache.get_cached(loader)
        cache.get_cached(loader)
        self.assertEqual(len(calls), 1)

    def test_expired_entry_refetches(self):
        calls = []

        def loader():
            calls.append(1)
            return len(calls)

        cache.get_cached(loader)
        cache._expires_at = 0.0
        self.assertEqual(cache.get_cached(loader), 2)


if __name__ == "__main__":
    unittest.main()
'''


def run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


class HarnessEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name) / "menu_repo"
        proc = run([sys.executable, str(FIXTURES), str(cls.repo), "--set", "harness"],
                   Path.cwd())
        assert proc.returncode == 0, proc.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def hook(self, script: Path, payload: dict) -> subprocess.CompletedProcess:
        return run([sys.executable, str(script)], self.repo,
                   input=json.dumps(payload))

    def edit(self, rel: str) -> dict:
        return {"hook_event_name": "PreToolUse", "cwd": str(self.repo),
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / rel)}}

    def stop(self) -> dict:
        return {"hook_event_name": "Stop", "cwd": str(self.repo),
                "stop_hook_active": False}

    def test_fixture_is_armed_and_valid(self):
        self.assertEqual(
            (self.repo / ".driftguard" / "current").read_text().strip(), "1")
        p = run([sys.executable, str(ROOT / "scripts" / "contract.py"), "validate"],
                self.repo)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

    def test_guard_blocks_naive_out_of_scope_edit(self):
        # the naive solution edits menu/api.py directly — outside task 1's allowlist
        p = self.hook(GUARD, self.edit("menu/api.py"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("outside the file allowlist", p.stderr)
        p = self.hook(GUARD, self.edit("menu/cache.py"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_budget_blocks_oversized_change(self):
        (self.repo / "menu" / "cache.py").write_text("x = 1  # padding\n" * 100)
        p = self.hook(BUDGET, self.stop())
        self.assertEqual(p.returncode, 2)
        self.assertIn("budget is 60", p.stderr)

    def test_minimal_in_scope_change_passes_hooks_and_tests(self):
        (self.repo / "menu" / "cache.py").write_text(MINIMAL_CACHE)
        (self.repo / "tests" / "test_cache.py").write_text(MINIMAL_TEST)
        for rel in ("menu/cache.py", "tests/test_cache.py"):
            p = self.hook(GUARD, self.edit(rel))
            self.assertEqual(p.returncode, 0, (rel, p.stderr))
        p = self.hook(BUDGET, self.stop())
        self.assertEqual(p.returncode, 0, p.stderr)
        p = run([sys.executable, "-m", "unittest", "tests.test_cache"], self.repo)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
