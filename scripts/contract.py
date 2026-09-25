"""Harness task ledger (.driftguard/tasks.json): contracts + status. Stdlib only.

The ledger is the source of truth for harness runs. The orchestrator and the
guard/budget hooks read it; coder agents never write it directly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from common import emit

STATUSES = ("todo", "in_progress", "review", "done", "blocked")
DEFAULT_PATH = Path(".driftguard") / "tasks.json"
CURRENT_PATH = Path(".driftguard") / "current"

_TOP_KEYS = {"spec", "tests_excluded_from_budget", "tasks"}
_TASK_KEYS = {
    "id", "goal", "files", "max_loc", "test", "depends_on",
    "status", "base_sha", "attempts", "summary",
}


def load(path: str | Path = DEFAULT_PATH) -> dict:
    """Parse the ledger. Raises FileNotFoundError / ValueError on bad input."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no ledger at {path} (harness not started?)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    return data


def save(contract: dict, path: str | Path = DEFAULT_PATH) -> None:
    """Atomic write: temp file in the same directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tasks.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(contract, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _repo_relative(p) -> bool:
    if not isinstance(p, str) or not p.strip():
        return False
    pp = Path(p)
    return not pp.is_absolute() and ".." not in pp.parts


def validate(contract) -> list[str]:
    """All problems with the ledger, human-readable. Empty list means valid."""
    problems: list[str] = []
    if not isinstance(contract, dict):
        return ["top level must be a JSON object"]
    for key in sorted(set(contract) - _TOP_KEYS):
        problems.append(f"unknown top-level key {key!r}")
    if "spec" in contract and not isinstance(contract["spec"], str):
        problems.append("'spec' must be a string (path to SPEC.md)")
    if "tests_excluded_from_budget" in contract and not isinstance(
            contract["tests_excluded_from_budget"], bool):
        problems.append("'tests_excluded_from_budget' must be a boolean")
    tasks = contract.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return problems + ["'tasks' must be a non-empty list"]

    ids: list[int] = []
    all_ids = [x.get("id") for x in tasks if isinstance(x, dict)]
    for i, t in enumerate(tasks):
        where = f"task[{i}]"
        if not isinstance(t, dict):
            problems.append(f"{where}: must be an object")
            continue
        tid = t.get("id")
        if _is_int(tid):
            where = f"task id={tid}"
        missing = _TASK_KEYS - set(t)
        if missing:
            problems.append(f"{where}: missing keys: {', '.join(sorted(missing))}")
        for key in sorted(set(t) - _TASK_KEYS):
            problems.append(f"{where}: unknown key {key!r}")
        if not _is_int(tid) or tid <= 0:
            problems.append(f"{where}: 'id' must be a positive integer")
        elif tid in ids:
            problems.append(f"{where}: duplicate id {tid}")
        else:
            ids.append(tid)
        for key in ("goal", "test"):
            if not isinstance(t.get(key), str) or not t.get(key, "").strip():
                label = "command string" if key == "test" else "string"
                problems.append(f"{where}: {key!r} must be a non-empty {label}")
        files = t.get("files")
        if not isinstance(files, list) or not files:
            problems.append(f"{where}: 'files' must be a non-empty list")
        else:
            problems += [f"{where}: file {f!r} must be a repo-relative path"
                         " (no absolute paths, no '..')"
                         for f in files if not _repo_relative(f)]
        if not _is_int(t.get("max_loc")) or t.get("max_loc", 0) <= 0:
            problems.append(f"{where}: 'max_loc' must be a positive integer")
        deps = t.get("depends_on")
        if not isinstance(deps, list):
            problems.append(f"{where}: 'depends_on' must be a list of task ids")
        else:
            for d in deps:
                if not _is_int(d):
                    problems.append(f"{where}: depends_on entry {d!r} must be an integer")
                elif d == tid:
                    problems.append(f"{where}: cannot depend on itself")
                elif d not in all_ids:
                    problems.append(f"{where}: depends on unknown task {d}")
        if t.get("status") not in STATUSES:
            problems.append(f"{where}: 'status' must be one of {', '.join(STATUSES)}")
        if not _is_int(t.get("attempts")) or t.get("attempts", 0) < 0:
            problems.append(f"{where}: 'attempts' must be a non-negative integer")
        for key in ("base_sha", "summary"):
            if t.get(key) is not None and not isinstance(t.get(key), str):
                problems.append(f"{where}: {key!r} must be a string or null")

    in_progress = [t.get("id") for t in tasks
                   if isinstance(t, dict) and t.get("status") == "in_progress"]
    if len(in_progress) > 1:
        problems.append(f"multiple in_progress tasks: {in_progress} (at most one allowed)")
    return problems + _cycle_problems(tasks)


def _cycle_problems(tasks: list) -> list[str]:
    deps = {t["id"]: [d for d in t.get("depends_on", []) if _is_int(d)]
            for t in tasks if isinstance(t, dict) and _is_int(t.get("id"))}
    problems, state = [], {}  # state: id -> "visiting" | "done"

    def visit(tid: int, trail: list[int]) -> None:
        if state.get(tid) == "visiting":
            cycle = trail[trail.index(tid):]
            problems.append(f"dependency cycle: {' -> '.join(map(str, cycle))}")
            return
        if state.get(tid) == "done":
            return
        state[tid] = "visiting"
        for d in deps.get(tid, []):
            if d in deps:
                visit(d, trail + [d])
        state[tid] = "done"

    for tid in deps:
        visit(tid, [tid])
    return problems


# --- queries -----------------------------------------------------------------

def find_task(contract: dict, task_id: int) -> dict | None:
    return next((t for t in contract.get("tasks", []) if t.get("id") == task_id), None)


def next_task(contract: dict) -> dict | None:
    """First task (file order) that is todo with all dependencies done."""
    tasks = contract.get("tasks", [])
    done = {t["id"] for t in tasks if t.get("status") == "done"}
    for t in tasks:
        if t.get("status") == "todo" and all(d in done for d in t.get("depends_on", [])):
            return t
    return None


def current_task(contract: dict) -> dict | None:
    """The single in_progress task, if any."""
    return next(
        (t for t in contract.get("tasks", []) if t.get("status") == "in_progress"), None)


def set_status(contract: dict, task_id: int, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    task = find_task(contract, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    task["status"] = status
    return task


def active_task(root: str | Path = ".") -> dict | None:
    """Task named by .driftguard/current under root; None when harness is off."""
    current = Path(root) / CURRENT_PATH
    if not current.exists():
        return None
    task_id = int(current.read_text().strip())
    return find_task(load(Path(root) / DEFAULT_PATH), task_id)


def log_hook_error(root: str | Path, message: str) -> None:
    """Append to traces/hook-errors.log; never raises (hooks must not crash)."""
    try:
        log = Path(root) / ".driftguard" / "traces" / "hook-errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(message.rstrip() + "\n")
    except OSError:
        pass


# --- CLI ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="contract.py",
        description="driftguard harness task ledger (.driftguard/tasks.json)")
    p.add_argument("--file", default=str(DEFAULT_PATH), help="ledger path")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("init", help="create the ledger from JSON on stdin (validated)")
    sub.add_parser("validate", help="check the ledger, print all problems")
    sub.add_parser("next", help="print the next runnable task as JSON (null if none)")
    sub.add_parser("show", help="pretty-print the ledger")
    sp = sub.add_parser("status", help="set a task's status")
    sp.add_argument("id", type=int)
    sp.add_argument("status", choices=STATUSES)
    sp = sub.add_parser("attempt", help="increment a task's attempt counter")
    sp.add_argument("id", type=int)
    sp = sub.add_parser("summary", help="set a task's summary text")
    sp.add_argument("id", type=int)
    sp.add_argument("text", nargs="+")
    sp = sub.add_parser("base", help="record a task's base sha")
    sp.add_argument("id", type=int)
    sp.add_argument("sha")
    args = p.parse_args(argv)

    if args.cmd == "init":
        try:
            data = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            print(f"error: stdin is not valid JSON: {e}", file=sys.stderr)
            return 1
        problems = validate(data)
        if problems:
            print(f"stdin: {len(problems)} problem(s):")
            for p in problems:
                print(f"  - {p}")
            return 1
        save(data, args.file)
        print(f"{args.file}: ok")
        return 0

    try:
        contract = load(args.file)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    problems = validate(contract)
    if args.cmd == "validate":
        if problems:
            print(f"{args.file}: {len(problems)} problem(s):")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"{args.file}: ok")
        return 0
    if problems:  # all other commands refuse to run on an invalid ledger
        print(f"error: {args.file} is invalid:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if args.cmd == "next":
        emit(next_task(contract))
        return 0
    if args.cmd == "show":
        emit(contract)
        return 0
    # status | attempt | summary | base: mutate one task and persist
    task = find_task(contract, args.id)
    if task is None:
        print(f"error: no task with id {args.id}", file=sys.stderr)
        return 1
    if args.cmd == "status":
        task["status"] = args.status
    elif args.cmd == "attempt":
        task["attempts"] += 1
    elif args.cmd == "base":
        task["base_sha"] = args.sha
    else:  # summary
        task["summary"] = " ".join(args.text)
    save(contract, args.file)
    emit(task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
