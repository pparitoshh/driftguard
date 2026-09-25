"""Guard hook (PreToolUse): block edits outside the active task's file allowlist.

Reads the hook event JSON from stdin. Inert unless `.driftguard/current` names
an active task in `.driftguard/tasks.json`. Never crashes: any internal error
is logged to .driftguard/traces/hook-errors.log and the edit is allowed.

Decisions: exit 0 = no opinion (edit proceeds); exit 2 = blocked, stderr is
shown to the agent as the denial reason.

Patterns in the task's `files` use fnmatch semantics (`*` may cross `/`).
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

import contract

HOOK_EVENT = "PreToolUse"


def allowed(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        contract.log_hook_error(Path.cwd(), f"guard: malformed input: {raw[:200]!r}")
        return 0
    root = Path(event.get("cwd") or Path.cwd())

    task = contract.active_task(root)
    if task is None:
        return 0  # harness off: hooks are inert

    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        contract.log_hook_error(
            root, f"guard: no tool_input.file_path in event: {raw[:200]!r}")
        return 0

    rel = os.path.relpath(Path(file_path), root).replace(os.sep, "/")
    patterns = task.get("files", [])
    if allowed(rel, patterns):
        return 0

    allowed_list = "\n".join(f"  - {p}" for p in patterns)
    print(
        f"driftguard guard: '{rel}' is outside the file allowlist of task "
        f"{task.get('id')} ({task.get('goal')!r}). This task may only touch:\n"
        f"{allowed_list}\n"
        "If the task genuinely needs more files, STOP and report back to the "
        "orchestrator — do not work around this guard.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never crash: allow and log
        contract.log_hook_error(Path.cwd(), f"guard: crashed: {e!r}")
        sys.exit(0)
