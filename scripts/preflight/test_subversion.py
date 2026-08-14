#!/usr/bin/env python3
"""Test-subversion pre-flight: did tests change in a *weakening* direction in
the same diff as the code they cover?

Signals (added/removed lines inside changed test files):
  - assertions removed faster than added
  - skip / xfail introduced
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (base_argparser, changed_files, diff_added_removed, emit,  # noqa: E402
                    finding, resolve_range)

TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$", re.I)
ASSERT_PAT = re.compile(r"\bassert\b|self\.assert\w*|pytest\.raises|assertEqual")
SKIP_PAT = re.compile(r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\s*\(|"
                      r"@unittest\.skip|self\.skipTest|\.skip\s*\(")


def is_test(path: str) -> bool:
    return path.endswith(".py") and bool(TEST_PATH.search(path))


def main() -> None:
    ap = base_argparser("test-subversion pre-flight")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    files = changed_files(repo, base, head)
    code_changed = any(not is_test(f["path"]) for f in files)
    findings: list[dict] = []

    for f in files:
        path = f["path"]
        if not is_test(path):
            continue
        added, removed = diff_added_removed(repo, base, head, path)
        add_asserts = sum(1 for l in added if ASSERT_PAT.search(l))
        del_asserts = sum(1 for l in removed if ASSERT_PAT.search(l))
        skips = [i + 1 for i, l in enumerate(added) if SKIP_PAT.search(l)]

        if del_asserts > add_asserts:
            findings.append(finding(
                "tier0:test_subversion",
                "error" if code_changed else "warning",
                path, "diff",
                f"{del_asserts} assertion(s) removed vs {add_asserts} added"
                + (" while the code under test changed in the same diff" if code_changed else ""),
                [f"git diff {path}: -{del_asserts}/+{add_asserts} assertion lines"],
                "verify each removed assertion against the new behaviour; if any was "
                "removed to make failing code pass, restore it and fix the code"))

        if skips:
            findings.append(finding(
                "tier0:test_subversion", "warning", path, "diff",
                f"{len(skips)} skip/xfail marker(s) added",
                [f"git diff {path}: added lines matching skip/xfail: {skips[:5]}"],
                "justify each skip in the PR description or remove it"))

    emit({"check": "test_subversion", "findings": findings,
          "skipped": [], "code_changed_in_range": code_changed})


if __name__ == "__main__":
    main()
