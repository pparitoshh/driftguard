"""Stop gate. Phase 1: tests must pass. Phase 3 adds TDD order, LOC final,
circuit breaker, learnings and metrics."""

import subprocess
from pathlib import Path

TEST_TIMEOUT_S = 120


def check(contract: dict, state: dict, repo: Path) -> tuple[bool, str]:
    cmd = contract["test_cmd"].format(tests=" ".join(contract["tests_required"]))
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=repo, capture_output=True, text=True,
            timeout=TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"tests timed out after {TEST_TIMEOUT_S}s"
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
        return False, "tests failing:\n" + tail
    return True, "gate passed"
