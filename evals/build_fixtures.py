#!/usr/bin/env python3
"""Build a fixture repo with planted issues for driftguard eval.

Planted issues (the golden expectations live in results.md):
  1. hallucinated import (undeclared, not on PyPI)        -> tier0:deps_check error
  2. unused import added by the diff                       -> tier0:dead_code warning
  3. unreachable statement added by the diff               -> tier0:dead_code warning
  4. assertion removed + skip added while code changed     -> tier0:test_subversion error/warning
  5. one-caller abstraction (unrequested scope + slop)     -> subagent findings
  6. unrequested feature file                              -> intent-scope subagent finding
  7. committed AWS-shaped credential (public example key)  -> tier0:secrets_scan error
  8. pinned known-vulnerable dep (requests==2.19.0)        -> tier0:osv_check error (online)
  9. auth-path change with no tests                        -> risk_score elevation

Usage: python3 evals/build_fixtures.py [target_dir]
Prints the base/head range to review.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str, date: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, check=True, capture_output=True, env=env)


def write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else
                  Path(__file__).parent / "fixture_repo")
    if target.exists():
        print(f"fixture already exists at {target}", file=sys.stderr)
        sys.exit(1)
    target.mkdir(parents=True)
    git(target, "init", "-b", "main", date="2026-08-01T09:00:00+00:00")

    # --- base commit: a small, clean pricing module with tests ---
    write(target, "pricing.py",
          "def total(items):\n    return sum(i['price'] for i in items)\n")
    write(target, "tests/test_pricing.py",
          "from pricing import total\n\n\n"
          "def test_total():\n"
          "    items = [{'price': 2}, {'price': 3}]\n"
          "    assert total(items) == 5\n"
          "    assert total([]) == 0\n")
    write(target, "requirements.txt", "")
    git(target, "add", "-A", date="2026-08-01T09:01:00+00:00")
    git(target, "commit", "-m", "base: pricing module with tests",
        date="2026-08-01T09:02:00+00:00")

    # --- feature branch: task was "add a discount flag to total()" ---
    git(target, "checkout", "-b", "feat/discount-flag", date="2026-08-02T09:00:00+00:00")

    # the requested change (plus planted issues 1-3 in the same file)
    write(target, "pricing.py",
          "import os  # planted: unused\n"
          "import flarghblarghe  # planted: hallucinated dep\n\n\n"
          "def total(items, discount=0):\n"
          "    return sum(i['price'] for i in items) * (1 - discount)\n"
          "    print('unreachable')  # planted: dead\n")
    # planted 4: weakened test (assertion removed, skip added) alongside code change
    write(target, "tests/test_pricing.py",
          "import pytest\nfrom pricing import total\n\n\n"
          "@pytest.mark.skip(reason='flaky')\n"
          "def test_total():\n"
          "    items = [{'price': 2}, {'price': 3}]\n"
          "    assert total(items) == 5\n")
    # planted 5: one-caller abstraction nobody asked for
    write(target, "discount_strategy.py",
          "class DiscountStrategy:\n"
          "    \"\"\"Abstraction with exactly one caller, unrequested.\"\"\"\n\n"
          "    def __init__(self, rate):\n"
          "        self.rate = rate\n\n"
          "    def apply(self, amount):\n"
          "        return amount * (1 - self.rate)\n")
    # planted 6: an unrequested feature
    write(target, "currency.py",
          "RATES = {'USD': 1.0, 'EUR': 0.92}\n\n\n"
          "def convert(amount, currency):\n"
          "    return amount * RATES[currency]\n")
    # planted 7: committed credential (AWS's documented public example key —
    # matches the format, is not a real secret)
    write(target, "auth/config.py",
          'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    # planted 8: pinned known-vulnerable dependency (CVE-2018-18074)
    write(target, "requirements.txt", "requests==2.19.0\n")
    # planted 9 (implicit): auth/ path changed in this diff with no new tests
    git(target, "add", "-A", date="2026-08-02T10:00:00+00:00")
    git(target, "commit", "-m", "feat: add discount flag to total()",
        date="2026-08-02T10:01:00+00:00")

    print(f"fixture repo: {target}")
    print("review range: --base main --head feat/discount-flag")
    print("task: 'add a discount flag to total()'")


if __name__ == "__main__":
    main()
