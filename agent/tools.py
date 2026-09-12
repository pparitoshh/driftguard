"""Tool dispatch with contract enforcement.

Denials are ok:false observations, not crashes: the agent sees the reason
in-loop and can self-correct. Every deny increments state["denials"].
"""

import re
import subprocess
import time
from pathlib import Path, PurePosixPath

from agent import loc

PROTECTED_PREFIXES = ("agent/", ".driftguard/", ".claude/")
READ_CAP = 6000
TEST_CAP = 4000
TEST_TIMEOUT_S = 120

FORBIDDEN_PATTERNS = {
    "new class": re.compile(r"^\s*class\s", re.M),
    "try/except": re.compile(r"^\s*(try|except)\b", re.M),
    "logging": re.compile(r"\bimport\s+logging\b|\blogging\."),
}


def dispatch(action: dict, contract: dict, state: dict, repo: Path) -> dict:
    name = action.get("action")
    args = action.get("args") or {}
    if name == "read_file":
        return read_file(args.get("path", ""), repo)
    if name == "write_file":
        return write_file(
            args.get("path", ""), args.get("content", ""), contract, state, repo
        )
    if name == "run_tests":
        return run_tests(contract, repo)
    if name == "done":
        return {"ok": True, "done": True, "output": args.get("summary", "")}
    return {"ok": False, "output": f"unknown action: {name}"}


def test_file(contract: dict) -> str:
    """Path of the primary test file. v1 heuristic: pytest `file::id` form
    uses the file part; dotted unittest ids map top module to `<module>.py`."""
    entry = contract["tests_required"][0]
    if "::" in entry:
        return entry.split("::")[0]
    return entry.split(".")[0] + ".py"


def is_test(path: str, contract: dict) -> bool:
    return path == test_file(contract) or PurePosixPath(path).name.startswith("test")


def read_file(path: str, repo: Path) -> dict:
    target = (repo / path).resolve()
    if target != repo and repo not in target.parents:
        return {"ok": False, "output": f"outside repo: {path}"}
    if not target.is_file():
        return {"ok": False, "output": f"not a file: {path}"}
    return {"ok": True, "output": target.read_text()[:READ_CAP]}


def write_file(path: str, content: str, contract: dict, state: dict, repo: Path) -> dict:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    if normalized.startswith(PROTECTED_PREFIXES):
        return _deny(state, f"protected path: {normalized}")
    if normalized not in contract["files_allowed"]:
        return _deny(state, f"outside contract: {normalized}")
    test_written = any(is_test(p, contract) for p in state["first_writes"])
    if contract.get("tdd", True) and not is_test(normalized, contract) and not test_written:
        return _deny(state, f"TDD: first write must be {test_file(contract)}")
    for name in contract.get("forbidden", []):
        pattern = FORBIDDEN_PATTERNS.get(name)
        if pattern and pattern.search(content):
            return _deny(state, f"forbidden: {name}")
    current = {}
    for allowed in contract["files_allowed"]:
        target = repo / allowed
        current[allowed] = (
            content if allowed == normalized
            else target.read_text() if target.is_file() else ""
        )
    added = loc.added_loc(state["baseline"], current)
    budget = contract["loc_budget"]
    if added > budget * 1.5:
        return _deny(
            state,
            f"over budget: {added}/{budget}. Remove code or stop and ask to split.",
        )
    target = repo / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    state["first_writes"].setdefault(normalized, time.time())
    state["added_loc"] = added
    result = {"ok": True, "output": f"wrote {normalized} ({added}/{budget} LOC)"}
    if added > budget:
        result["warning"] = f"over budget: {added}/{budget}"
    return result


def run_tests(contract: dict, repo: Path) -> dict:
    cmd = contract["test_cmd"].format(tests=" ".join(contract["tests_required"]))
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=repo, capture_output=True, text=True,
            timeout=TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"tests timed out after {TEST_TIMEOUT_S}s"}
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "output": tail[-TEST_CAP:]}


def _deny(state: dict, reason: str) -> dict:
    state["denials"] += 1
    return {"ok": False, "output": f"denied: {reason}"}
