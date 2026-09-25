---
description: Run the next harness task end-to-end (coder subagent → test → review → commit)
argument-hint: ""
allowed-tools: Bash, Read, Write, Glob, Task, AskUserQuestion, SlashCommand
---

# /driftguard:run — harness orchestrator (single task)

You orchestrate; you **never write task code yourself**. The coder subagent
writes code, hooks enforce its contract, you run the tests and the review.
Keep only summaries in your context — full subagent output goes to
`.driftguard/traces/`.

`PLUGIN_SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts` (if unset, resolve `scripts/`
from this command file's installed location). All `contract.py` calls run with
`cwd` = the repo root.

## 0. Preconditions

- Ledger exists and is valid:
  `python3 $PLUGIN_SCRIPTS/contract.py validate`
  If it is missing, stop: the task list must be hand-written into
  `.driftguard/tasks.json` (schema: `{"spec": "SPEC.md", "tasks": [{id, goal,
  files, max_loc, test, depends_on, status, base_sha, attempts, summary}]}`)
  and approved by the human before running.
- You are on a feature branch — never run on `main`/`master` without explicit
  user consent (AskUserQuestion if `git branch --show-current` says so).
- `git status --porcelain` is clean, except untracked `.driftguard/` entries.

## 1. Pick the task

```bash
python3 $PLUGIN_SCRIPTS/contract.py next
```

`null` → nothing runnable: report the ledger state (`contract.py show`) and stop.
Otherwise `TASK` = that JSON, `ID` = its `id`.

## 2. Arm the harness

```bash
BASE=$(git rev-parse HEAD)
echo "$ID" > .driftguard/current
python3 $PLUGIN_SCRIPTS/contract.py status "$ID" in_progress
```

Record `BASE`. From now until step 6 the guard and budget hooks are live.

## 3. Run the coder subagent

`mkdir -p .driftguard/traces/$ID`, then spawn the **coder** subagent (Task
tool) with a prompt containing:
- the task contract JSON verbatim,
- `SPEC` = the ledger's `spec` path,
- a pointer to `.driftguard/rules.md` / `.driftguard/learnings.md` if present,
- one line: "Follow agents/coder.md exactly. Write your final SUMMARY/TEST report only."

When it returns, write its full final message to `.driftguard/traces/$ID/coder.md`
and keep only its `SUMMARY:` / `TEST:` lines in your context. If the report
contains `BLOCKED:` → go to step 7 (blocked), do not retry.

## 4. Run the test yourself

Do not trust the coder's claim — run the contract's `test` command verbatim
with `cwd` = repo root. Non-zero exit → go to step 7 (blocked) with the tail
of the test output.

## 5. Review the task diff

The changes are uncommitted; snapshot the working tree into a dangling commit
(moves no branch, touches neither HEAD nor files):

```bash
IDX=$(mktemp) && cp .git/index "$IDX" \
  && GIT_INDEX_FILE="$IDX" git add -A \
  && SNAP=$(git -c user.name=driftguard -c user.email=driftguard@local \
       commit-tree "$(GIT_INDEX_FILE="$IDX" git write-tree)" -p HEAD -m "driftguard review snapshot") \
  ; rm -f "$IDX"; echo "$SNAP"
```

Then run `/driftguard:review --base $BASE --head $SNAP --task "<task goal>"`.
Overall verdict not `pass` → go to step 7 (blocked) with the findings.

## 6. Accept

```bash
git add -A && git commit -m "<task goal> (driftguard task $ID)"
python3 $PLUGIN_SCRIPTS/contract.py status "$ID" done
rm -f .driftguard/current
```

(`.driftguard/` is gitignored, so `git add -A` adds only task files.) Report:
task id, goal, test result, review verdict, commit sha, coder summary, LOC vs
budget (`git diff --numstat $BASE HEAD`). Stop here — one task per run.

## 7. Blocked

```bash
python3 $PLUGIN_SCRIPTS/contract.py status "$ID" blocked
rm -f .driftguard/current
```

Report why (coder's BLOCKED note, failing test tail, or review findings) and
what contract change would unblock it: more files, larger `max_loc`, a spec
decision. Leave the working tree as the coder left it; the human decides.
