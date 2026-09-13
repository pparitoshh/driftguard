"""Contract authoring: explicit flags, --json, or --propose with approval.

The human always authors the contract. --propose only drafts; nothing is
written before explicit approval. Validation is path-based, never
existence-based, so greenfield projects work (files_create need not exist).
"""

import argparse
import json
import shlex
import sys
from pathlib import Path

from agent import backend, tools

MAX_BUDGET = 300
MAX_ITERATIONS = 100

PLANNER_PROMPT = """You draft a driftguard task contract. Reply with the contract JSON
object and nothing else.
Schema: {{"task": str, "base": "main", "files_allowed": [paths],
"files_create": [paths], "loc_budget": int (<= 300),
"tests_required": ["tests/test_x.py::test_y" or dotted unittest ids],
"test_cmd": "python3 -m pytest {{tests}} -q" (or unittest equivalent),
"forbidden": ["new class", "try/except", "logging"], "tdd": true,
"max_iterations": 15, "max_tokens": 1500000, "max_cost_usd": 2.0,
"max_deleted_loc": 0 (raise only if the task removes/rewrites code)}}
Rules: list only files the task names or obviously requires; never add
helpers/config/utils; files_create must be a subset of files_allowed; every
test file must appear in files_allowed.

Task: {task}"""


def validate(contract: dict) -> list[str]:
    errors = []
    for key in ("task", "files_allowed", "loc_budget", "tests_required", "test_cmd"):
        if key not in contract:
            errors.append(f"missing key: {key}")
    if errors:
        return errors
    if contract["loc_budget"] > MAX_BUDGET:
        errors.append(f"loc_budget {contract['loc_budget']} > {MAX_BUDGET}: split the task")
    allowed = set(contract["files_allowed"])
    for path in allowed | set(contract.get("files_create", [])):
        if Path(path).is_absolute() or ".." in Path(path).parts:
            errors.append(f"path must be repo-relative without '..': {path}")
    for entry in contract["tests_required"]:
        if tools.test_path(entry) not in allowed:
            errors.append(f"test outside files_allowed: {entry}")
    try:
        tokens = shlex.split(contract["test_cmd"])
    except ValueError:
        tokens = []
    if "{tests}" not in tokens:
        errors.append("test_cmd must contain {tests} as a standalone argument")
    if not set(contract.get("files_create", [])) <= allowed:
        errors.append("files_create must be a subset of files_allowed")
    for key in ("max_tokens", "max_cost_usd"):
        value = contract.get(key)
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            errors.append(f"{key} must be a positive number")
    if contract.get("max_iterations", 15) > MAX_ITERATIONS:
        errors.append(f"max_iterations > {MAX_ITERATIONS}")
    return errors


def build(args: argparse.Namespace) -> dict:
    return {
        "task": args.task,
        "base": args.base,
        "files_allowed": args.files,
        "files_create": args.creates or [],
        "loc_budget": args.budget,
        "tests_required": args.tests,
        "test_cmd": args.test_cmd,
        "forbidden": args.forbidden,
        "tdd": not args.no_tdd,
        "max_iterations": args.max_iterations,
        "max_deleted_loc": args.max_deleted,
        "max_tokens": args.max_tokens,
        "max_cost_usd": args.max_cost_usd,
    }


def propose(task_text: str, backend_fn=backend.chat) -> dict:
    """Draft a contract via the LLM backend; validate with one retry."""
    prompt = PLANNER_PROMPT.format(task=task_text)
    last_errors = []
    for _ in range(2):
        draft = backend.loads_json(backend_fn(prompt, []))
        last_errors = validate(draft)
        if not last_errors:
            return draft
        prompt = PLANNER_PROMPT.format(task=task_text) + (
            "\nYour previous draft was invalid: " + "; ".join(last_errors)
        )
    raise ValueError("invalid contract after retry: " + "; ".join(last_errors))


def write(contract: dict, cwd: Path) -> Path:
    drift = cwd / ".driftguard"
    drift.mkdir(exist_ok=True)
    state = drift / "state.json"
    if state.exists():
        state.unlink()
    if not (cwd / ".gitignore").exists() or ".driftguard" not in (cwd / ".gitignore").read_text():
        print("hint: add .driftguard/ to your .gitignore")
    path = drift / "contract.json"
    path.write_text(json.dumps(contract, indent=2) + "\n")
    return path


def main(argv=None, backend_fn=backend.chat) -> int:
    parser = argparse.ArgumentParser(description="Author a driftguard contract.")
    parser.add_argument("--task")
    parser.add_argument("--files", nargs="+", default=[])
    parser.add_argument("--creates", nargs="*", default=[])
    parser.add_argument("--tests", nargs="+", default=[])
    parser.add_argument("--budget", type=int, default=150)
    parser.add_argument("--base", default="main")
    parser.add_argument("--test-cmd", default="python3 -m pytest {tests} -q")
    parser.add_argument("--forbidden", nargs="*",
                        default=["new class", "try/except", "logging"])
    parser.add_argument("--no-tdd", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=15)
    parser.add_argument("--max-deleted", type=int, default=0)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-cost", type=float, dest="max_cost_usd")
    parser.add_argument("--json", dest="raw_json")
    parser.add_argument("--propose", metavar="TASK_TEXT")
    args = parser.parse_args(argv)

    if args.propose:
        try:
            contract = propose(args.propose, backend_fn)
        except ValueError as exc:
            print(exc)
            return 1
        print(json.dumps(contract, indent=2))
        answer = input("approve? (y/n/edit): ").strip().lower()
        if answer == "edit":
            try:
                contract = backend.loads_json(input("paste corrected contract JSON: "))
            except ValueError as exc:
                print(f"unparseable: {exc}")
                return 1
        elif answer != "y":
            print("discarded")
            return 1
    elif args.raw_json:
        try:
            contract = backend.loads_json(args.raw_json)
        except ValueError as exc:
            print(f"unparseable: {exc}")
            return 1
    else:
        contract = build(args)

    errors = validate(contract)
    if errors:
        print("invalid contract: " + "; ".join(errors))
        return 1
    path = write(contract, Path.cwd())
    print(json.dumps(contract, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
