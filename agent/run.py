"""ReAct loop controller: owns the transcript, counters, and circuit breaker.

The agent never touches the filesystem directly; it emits one JSON action
per turn and tools.dispatch validates, executes, and returns the observation.
"""

import argparse
import json
from pathlib import Path

from agent import backend, gate, tools

SYSTEM_TEMPLATE = """You are a coding agent executing one task under a contract.
Reply with EXACTLY one JSON object, no prose, no code fences:
{{"thought": "...", "action": "read_file|write_file|run_tests|done", "args": {{...}}}}
write_file args: {{"path": "...", "content": "..."}} (full file content).
read_file args: {{"path": "..."}}. run_tests args: {{}}. done args: {{"summary": "..."}}.
After each action you receive {{"observation": {{"ok": true|false, "output": "..."}}}}.
Denied actions are feedback: adjust and continue. Call done only when tests pass.

Task: {task}
Allowed files: {files}
LOC budget: {budget} (pure LOC added; writes over 1.5x are denied)
{tdd}
Forbidden patterns: {forbidden}"""


def fresh_state(contract: dict, repo: Path) -> dict:
    baseline = {}
    for path in contract["files_allowed"]:
        target = repo / path
        baseline[path] = target.read_text() if target.is_file() else ""
    return {
        "first_writes": {},
        "added_loc": 0,
        "iterations": 0,
        "gate_failures": 0,
        "denials": 0,
        "baseline": baseline,
    }


def run(contract_path: str, repo: str = ".", backend_fn=backend.chat) -> int:
    repo_path = Path(repo).resolve()
    contract = json.loads(Path(contract_path).read_text())
    drift = repo_path / ".driftguard"
    drift.mkdir(exist_ok=True)
    state_file = drift / "state.json"
    state = (
        json.loads(state_file.read_text())
        if state_file.is_file()
        else fresh_state(contract, repo_path)
    )
    system = SYSTEM_TEMPLATE.format(
        task=contract["task"],
        files=", ".join(contract["files_allowed"]),
        budget=contract["loc_budget"],
        tdd="TDD: your first write must be " + tools.test_file(contract)
        if contract.get("tdd", True)
        else "",
        forbidden=", ".join(contract.get("forbidden", [])) or "none",
    )
    transcript: list[dict] = []
    log = drift / "agent_transcript.jsonl"

    def record(role: str, content: str) -> None:
        transcript.append({"role": role, "content": content})
        with log.open("a") as fh:
            fh.write(json.dumps({"role": role, "content": content}) + "\n")

    malformed = 0
    while state["iterations"] < contract.get("max_iterations", 40):
        state["iterations"] += 1
        try:
            action = backend.parse_action(backend_fn(system, transcript))
            malformed = 0
        except (ValueError, RuntimeError) as exc:
            malformed += 1
            record(
                "observation",
                f"malformed reply ({exc}); reply with one JSON object only",
            )
            if malformed >= 2:
                break
            state_file.write_text(json.dumps(state, indent=2))
            continue
        record("agent", json.dumps(action))
        result = tools.dispatch(action, contract, state, repo_path)
        record("observation", json.dumps(result))
        if result.get("done"):
            ok, reason = gate.check(contract, state, repo_path)
            record("observation", f"gate: {reason}")
            if ok:
                state_file.write_text(json.dumps(state, indent=2))
                print(reason)
                return 0
        state_file.write_text(json.dumps(state, indent=2))
    print(f"aborted after {state['iterations']} iterations; see {log}")
    return 1


def main(argv=None, backend_fn=backend.chat) -> int:
    parser = argparse.ArgumentParser(
        description="Run one task under a driftguard contract."
    )
    parser.add_argument("--contract", required=True)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    return run(args.contract, args.repo, backend_fn)


if __name__ == "__main__":
    raise SystemExit(main())
