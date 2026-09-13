"""Stop gate: circuit breaker, tests, TDD order, LOC final, learnings, metrics."""

import json
import subprocess
import time
from datetime import date
from pathlib import Path

from agent.tools import test_argv, test_file

TEST_TIMEOUT_S = 120
MAX_GATE_FAILURES = 3
RELEASED = "circuit breaker: released to human"


def check(contract: dict, state: dict, repo: Path) -> tuple[bool, str]:
    if state["gate_failures"] >= MAX_GATE_FAILURES:
        return True, RELEASED
    try:
        proc = subprocess.run(
            test_argv(contract), cwd=repo, capture_output=True, text=True,
            timeout=TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _fail(state, repo, "tests", f"tests timed out after {TEST_TIMEOUT_S}s")
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
        return _fail(state, repo, "tests", "tests failing:\n" + tail)
    if contract.get("tdd", True):
        test_ts = state["first_writes"].get(test_file(contract))
        for impl in contract.get("files_create", []):
            impl_ts = state["first_writes"].get(impl)
            if test_ts is None or impl_ts is None or impl_ts < test_ts:
                return _fail(state, repo, "tdd", f"test written after impl: {impl}")
    if state["added_loc"] > contract["loc_budget"] * 1.5:
        return _fail(
            state, repo, "budget",
            f"over budget: {state['added_loc']}/{contract['loc_budget']}",
        )
    _append_metrics(contract, state, repo)
    return True, "gate passed"


def _fail(state: dict, repo: Path, category: str, reason: str) -> tuple[bool, str]:
    state["gate_failures"] += 1
    learnings = repo / ".driftguard" / "learnings.md"
    with learnings.open("a") as fh:
        fh.write(f"- [harness] {date.today().isoformat()} {category}: {reason.splitlines()[0]}\n")
    return False, reason


def _append_metrics(contract: dict, state: dict, repo: Path) -> None:
    entry = {
        "task": contract["task"],
        "loc_budget": contract["loc_budget"],
        "actual_loc": state["added_loc"],
        "iterations": state["iterations"],
        "denials": state["denials"],
        "gate_failures": state["gate_failures"],
        "outcome": "pass",
        "ts": time.time(),
    }
    with (repo / ".driftguard" / "metrics.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
