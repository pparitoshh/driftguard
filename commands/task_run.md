---
description: Run one small coding task through the contract-enforced agent runner, then review it
argument-hint: "\"task description\" [--spec PATH] [--repo PATH] [--base B] [--budget N] [--no-review]"
allowed-tools: Bash, Read, Write, Glob, AskUserQuestion, SlashCommand, Skill
---

# /driftguard:task_run — contract mode agent runner

Executes one small task via `agent/run.py`: a ReAct loop where headless
`claude -p` emits JSON actions and the runner enforces the contract (file
allowlist, TDD-first, forbidden patterns, 1.5x LOC hard stop, stop gate).
You orchestrate; you do **not** write the task's code yourself.

## 0. Parse arguments and resolve paths

From `$ARGUMENTS`:
- Quoted text → `TASK`. If empty, ask the user for the task — do not invent one.
- `--repo PATH` → target repo (default: current working directory). `REPO` = absolute path.
- `--base B` → base branch for the contract and review (default `main`).
- `--spec PATH` → existing spec/plan doc; skips step 0.5 authoring.
- `--budget N` → LOC budget hint for the draft (default: your estimate, ≤ 300).
- `--no-review` → skip step 5.

`PLUGIN_ROOT` = `${CLAUDE_PLUGIN_ROOT}` (if unset, the directory containing this
command file's `commands/` folder). All runner invocations use
`PYTHONPATH="$PLUGIN_ROOT" python3 -m agent.<module>` with `cwd` = `REPO` —
never `python3 agent/run.py` (the package import fails that way).

Preconditions (stop and tell the user if any fails):
- `REPO` is a git repo and `git -C "$REPO" status --porcelain` shows no changes to
  files the task will touch (the LOC baseline is the current file content).
- `claude` is on PATH (`command -v claude`) — the runner's backend shells out to it.

## 0.5. Spec and task breakdown (before any contract)

The runner executes small, pre-decomposed tasks — it does not design. Get a spec
and an ordered task list first:

1. `--spec PATH` given → read it and use its task list.
2. Otherwise, if a spec/task-driven skill is available, invoke it via the Skill tool
   with `TASK` and let it run its own flow (questions, approval) to completion:
   - superpowers: `superpowers:brainstorming` (spec) then `superpowers:writing-plans` (tasks)
   - any other installed spec/plan skill (e.g. spec-kit style `specify` → `plan` → `tasks`)
   Prefer the first found; if several fit, ask the user which to use.
3. No such skill → write a short spec yourself at `$REPO/.driftguard/spec.md`:
   goal, non-goals, acceptance criteria, and a numbered task list where each task
   names its files and tests and fits ≤ 300 LOC. Get user approval (AskUserQuestion)
   before continuing.

Result: `SPEC` (path) and an ordered list of tasks. If the user's `TASK` is already
a single small change naming its files and tests, confirm with the user and treat it
as a one-task list.

Run steps 1–5 **once per task, serially**, in spec order. Stop the sequence at the
first failed run or discarded contract and report which tasks remain.

## 1. Draft the contract

Derive the contract from the current task in `SPEC` (its files, tests, and acceptance
criteria), reading just enough of `REPO` to confirm paths. Draft a contract object with exactly these keys:

```json
{"task": "...", "base": "main", "files_allowed": ["..."], "files_create": ["..."],
 "loc_budget": 40, "tests_required": ["tests/test_x.py::test_y"],
 "test_cmd": "python3 -m pytest {tests} -q", "forbidden": ["new class", "try/except", "logging"],
 "tdd": true, "max_iterations": 40, "max_tokens": 1500000, "max_cost_usd": 2.0,
 "max_deleted_loc": 0}
```

`task` = the spec task text plus `(spec: <SPEC path>, task N)` so the review can
resolve the plan. Rules: list only files the task names or obviously requires; every test file is in
`files_allowed`; `files_create` ⊆ `files_allowed`; paths repo-relative, no `..`;
`test_cmd` contains `{tests}` as its own argument and uses no shell syntax (it is run
without a shell); match the repo's existing test runner (unittest vs pytest), and write
`tests_required` ids in that runner's form (`test_x.py::test_y` for pytest,
`test_x.Class.test_y` for unittest); `max_deleted_loc` stays 0 unless the task
rewrites or removes existing code. Size `max_iterations`, `max_tokens` and
`max_cost_usd` to the task (each `claude -p` call costs roughly 25–35k input tokens,
mostly cached); show them in the approval so the user sees the spend cap.

## 2. Human approval (required)

Show the drafted contract as a JSON block, then AskUserQuestion:
**Approve / Edit / Discard**. On Edit, apply the user's changes and ask again.
On Discard, stop. Never proceed to step 3 without an explicit Approve.

## 3. Validate and write

```bash
cd "$REPO" && PYTHONPATH="$PLUGIN_ROOT" python3 -m agent.contract --json '<approved JSON>'
```

Non-zero exit → show the validation errors, fix with the user, return to step 2.
On success the contract is at `$REPO/.driftguard/contract.json`.

## 4. Run the loop

```bash
cd "$REPO" && PYTHONPATH="$PLUGIN_ROOT" python3 -m agent.run --contract .driftguard/contract.json --repo .
```

Run it in the background (it can exceed the foreground timeout) and wait for it to
finish. Then report:
- exit code and the gate reason (or abort reason: `aborted after N iterations`,
  `token budget spent`, `cost budget spent`)
- the runner's `stats:` line verbatim: iterations/max, calls, input/output tokens,
  cost (vs caps), LOC/budget, denials, gate failures. It is also in `.driftguard/state.json` → `usage`.
- files changed (`git -C "$REPO" diff --stat`)

Exit codes: `0` gate passed · `1` aborted (max_iterations, token/cost cap, or repeated
malformed / CLI-error replies) · `2` circuit breaker released to human (tests/TDD/budget failed
3 times). On `1` or `2`, show the last ~20 lines of `.driftguard/agent_transcript.jsonl`
and stop — do not patch the code by hand or rerun without asking. If the transcript
shows `claude CLI error`, report it (login, model access, or a safeguard refusal;
`DRIFTGUARD_MODEL=<model>` switches the runner's model, default `sonnet`).

## 5. Review

Only on exit `0`, and unless `--no-review`. The runner leaves changes uncommitted,
but review's Tier 0 scripts diff a git range, so snapshot the working tree into a
dangling commit first. This moves no branch and touches neither HEAD nor files:

```bash
cd "$REPO" && IDX=$(mktemp) && cp .git/index "$IDX" \
  && GIT_INDEX_FILE="$IDX" git add -A \
  && SNAP=$(git -c user.name=driftguard -c user.email=driftguard@local \
       commit-tree "$(GIT_INDEX_FILE="$IDX" git write-tree)" -p HEAD -m "driftguard review snapshot") \
  ; rm -f "$IDX"; echo "$SNAP"
```

(`git add -A` respects `.gitignore`, so `.driftguard/` stays out.) Then run
`/driftguard:review --base <B> --head <SNAP> --repo <REPO> --task "<task text>"`.
The review treats `.driftguard/contract.json` as authoritative scope.

## Notes

- Across tasks, keep a running total of tokens and cost from each `stats:` line and
  report it at the end of the sequence.

- One contract per task; multi-task specs loop steps 1–5. Budget > 300 is rejected: split the task.
- Rerunning with an existing `.driftguard/state.json` resumes counters;
  step 3 (`agent.contract`) deletes it so a new contract starts fresh.
- Nothing is committed. Leave committing to the user.
