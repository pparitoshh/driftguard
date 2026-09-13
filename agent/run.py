"""ReAct loop controller: owns the transcript, counters, and circuit breaker.

The agent never touches the filesystem directly; it emits one JSON action
per turn and tools.dispatch validates, executes, and returns the observation.
"""

import argparse
import json
from pathlib import Path

from agent import backend, gate, tools

MAX_MALFORMED = 3
EXIT_RELEASED = 2

SYSTEM_TEMPLATE = """You are a coding agent executing one task under a contract.
Reply with EXACTLY one JSON object, no prose, no code fences:
{{"action": "read_file|write_file|run_tests|done", "args": {{...}}}}
write_file args: {{"path": "...", "content": "..."}} (full file content).
read_file args: {{"path": "..."}}. run_tests args: {{}}. done args: {{"summary": "..."}}.
After each action you receive {{"observation": {{"ok": true|false, "output": "..."}}}}.
Denied actions are feedback: adjust and continue. Call done only when tests pass.

Task: {task}
Allowed files: {files}
Required tests (must exist with exactly these ids): {tests}
Test command: {test_cmd}
unittest ids are module.Class.method: test_calc.T.test_x means class T with method
test_x in test_calc.py. write_file replaces the whole file: read existing files first
and keep their code; deleting existing lines is denied beyond {max_deleted} LOC.
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
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
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
    state.setdefault("usage", {})
    max_iterations = contract.get("max_iterations", 15)
    system = SYSTEM_TEMPLATE.format(
        task=contract["task"],
        files=", ".join(contract["files_allowed"]),
        budget=contract["loc_budget"],
        tests=", ".join(contract["tests_required"]),
        test_cmd=contract["test_cmd"],
        max_deleted=contract.get("max_deleted_loc", 0),
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
    outcome = None
    while outcome is None and state["iterations"] < max_iterations:
        over = over_budget(contract, state["usage"])
        if over:
            print(over)
            record("observation", over)
            break
        state["iterations"] += 1
        try:
            action = backend.parse_action(backend_fn(system, transcript, state["usage"]))
            malformed = 0
        except (ValueError, RuntimeError) as exc:
            malformed += 1
            record(
                "observation",
                f"malformed reply ({exc}); reply with one JSON object only",
            )
            state_file.write_text(json.dumps(state, indent=2))
            if malformed >= MAX_MALFORMED:
                break
            continue
        record("agent", json.dumps(action))
        result = tools.dispatch(action, contract, state, repo_path)
        record("observation", json.dumps(result))
        if result.get("done"):
            ok, reason = gate.check(contract, state, repo_path)
            record("observation", f"gate: {reason}")
            if ok:
                print(reason)
                outcome = EXIT_RELEASED if reason == gate.RELEASED else 0
        state_file.write_text(json.dumps(state, indent=2))
    state_file.write_text(json.dumps(state, indent=2))
    if outcome is None:
        print(f"aborted after {state['iterations']} iterations; see {log}")
        outcome = 1
    print(stats_line(contract, state))
    return outcome


def over_budget(contract: dict, usage: dict) -> str:
    """Reason string when the token or cost cap is spent, else ''."""
    max_tokens = contract.get("max_tokens")
    if max_tokens and backend.total_tokens(usage) >= max_tokens:
        return f"token budget spent: {backend.total_tokens(usage)}/{max_tokens}"
    max_cost = contract.get("max_cost_usd")
    if max_cost and usage.get("cost_usd", 0) >= max_cost:
        return f"cost budget spent: ${usage['cost_usd']:.4f}/${max_cost}"
    return ""


def stats_line(contract: dict, state: dict) -> str:
    usage = state.get("usage", {})
    caps = []
    if contract.get("max_tokens"):
        caps.append(f"max {contract['max_tokens']}")
    if contract.get("max_cost_usd"):
        caps.append(f"max ${contract['max_cost_usd']}")
    return (
        f"stats: iterations {state['iterations']}/{contract.get('max_iterations', 15)}"
        f" · calls {usage.get('calls', 0)}"
        f" · tokens in {usage.get('input_tokens', 0)} out {usage.get('output_tokens', 0)}"
        f" · cost ${usage.get('cost_usd', 0):.4f}"
        + (f" ({', '.join(caps)})" if caps else "")
        + f" · LOC {state['added_loc']}/{contract['loc_budget']}"
        f" · denials {state['denials']} · gate failures {state['gate_failures']}"
    )

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
