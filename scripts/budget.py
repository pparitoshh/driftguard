"""Budget hook (Stop / SubagentStop): block when the active task's diff is too big.

LOC = added + removed lines from `git diff --numstat <base_sha>`, plus the line
count of untracked files matched by the task's `files`. Test files are skipped
when the ledger sets `tests_excluded_from_budget: true` (default false).

Inert unless `.driftguard/current` names an active task. Respects the hook's
`stop_hook_active` loop-prevention flag: if Claude Code is already continuing
because of a stop hook, we never block again. Never crashes: internal errors
are logged to .driftguard/traces/hook-errors.log and the stop is allowed.

Decisions: exit 0 = allow stop; exit 2 = block, stderr goes back to the agent.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import contract


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _is_test(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    return (any(p in {"test", "tests"} for p in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py"))


def loc_changed(root: Path, base_sha: str, files: list[str],
                exclude_tests: bool) -> int:
    """Added+removed lines vs base_sha, plus untracked in-scope file lines."""
    total = 0
    out = _git(root, "diff", "--numstat", "-z", base_sha)
    for record in out.split("\0"):
        record = record.strip()
        if not record:
            continue
        counts, _, path = record.rpartition("\t")
        added, _, removed = counts.partition("\t")
        if not (added.isdigit() and removed.isdigit()):
            continue  # binary file: numstat prints "-"
        if exclude_tests and _is_test(path):
            continue
        total += int(added) + int(removed)
    for path in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not path or (exclude_tests and _is_test(path)):
            continue
        if not any(fnmatch.fnmatchcase(path, p) for p in files):
            continue
        try:
            total += len((root / path).read_text(errors="replace").splitlines())
        except OSError:
            pass
    return total


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        contract.log_hook_error(Path.cwd(), f"budget: malformed input: {raw[:200]!r}")
        return 0
    root = Path(event.get("cwd") or Path.cwd())

    task = contract.active_task(root)
    if task is None:
        return 0  # harness off: hooks are inert
    if event.get("stop_hook_active"):
        return 0  # already continuing due to a stop hook: cannot block forever
    base = task.get("base_sha")
    if not base:
        return 0  # no baseline recorded: nothing to measure

    exclude_tests = False
    try:
        exclude_tests = bool(contract.load(root / contract.DEFAULT_PATH)
                             .get("tests_excluded_from_budget", False))
        changed = loc_changed(root, base, task.get("files", []), exclude_tests)
    except (RuntimeError, ValueError, OSError) as e:
        contract.log_hook_error(root, f"budget: could not measure LOC: {e!r}")
        return 0

    budget = task.get("max_loc", 0)
    if changed <= budget:
        return 0
    print(
        f"driftguard budget: task {task.get('id')} ({task.get('goal')!r}) is "
        f"over its LOC budget: {changed} lines changed (added+removed) since "
        f"{base}, budget is {budget}. Shrink the change, or STOP and report to "
        "the orchestrator why it cannot fit — do not pad or split commits to "
        "hide lines.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never crash: allow and log
        contract.log_hook_error(Path.cwd(), f"budget: crashed: {e!r}")
        sys.exit(0)
