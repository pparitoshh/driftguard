---
description: Run the harness over the task ledger (per task: coder subagent → test → review → commit)
argument-hint: "[--resume]"
allowed-tools: Bash, Read, Write, Glob, Task, AskUserQuestion, SlashCommand
---

# /driftguard:run — harness orchestrator

You orchestrate; you **never write task code yourself**. Fresh coder subagent
per task, hooks enforce its contract, you run the tests and the review. Keep
only summaries in your context — full subagent output goes to
`.driftguard/traces/`.

`PLUGIN_SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts` (if unset, resolve `scripts/`
from this command file's installed location). All `contract.py` calls run with
`cwd` = the repo root.

## 0. Preconditions

- Repo is git; `git status --porcelain` is clean, except untracked
  `.driftguard/` entries.
- You are on a feature branch — never run on `main`/`master` without explicit
  user consent (AskUserQuestion if `git branch --show-current` says so).
- Record `BRANCH_BASE` = `git merge-base HEAD origin/main` (fall back `main`,
  then `HEAD` if no main exists) for the final review.

`--resume`: continue from the existing ledger — never re-run `done` tasks.
First, repair interruption debris: delete a stale `.driftguard/current`, and
reset any `in_progress` task to `todo` (`contract.py status <id> todo`). A
`blocked` task stays blocked unless the user says otherwise.

## 1. Task list (human gate)

If `.driftguard/tasks.json` does not exist:
1. Require `SPEC.md` at the repo root (missing → tell the user to run
   `/driftguard:spec` first; stop).
2. Spawn the **planner** subagent (Task tool, `agents/planner.md`) with the
   SPEC.md path. It writes `.driftguard/tasks.json` via `contract.py`.
3. `python3 $PLUGIN_SCRIPTS/contract.py validate` — invalid → show the
   problems and stop (do not hand-fix contracts).
4. Show the task list (id, goal, files, max_loc, test) and AskUserQuestion:
   **Approve / Discard**. Never proceed without explicit Approve.

If the ledger exists, `contract.py validate` it and skip the gate
(the human approved it before, or `--resume`).

## 2. Task loop

Repeat until `python3 $PLUGIN_SCRIPTS/contract.py next` prints `null`:

### 2.1 Arm

`TASK` = that JSON, `ID` = its `id`. Then:

```bash
git rev-parse HEAD | xargs python3 $PLUGIN_SCRIPTS/contract.py base "$ID"
echo "$ID" > .driftguard/current
python3 $PLUGIN_SCRIPTS/contract.py status "$ID" in_progress
```

From now until the task commits or blocks, the guard and budget hooks are live.

### 2.2 Run the coder subagent

`mkdir -p .driftguard/traces/$ID`, then spawn the **coder** subagent (Task
tool, `agents/coder.md`) with a prompt containing:
- the task contract JSON verbatim,
- `SPEC` = the ledger's `spec` path,
- a pointer to `.driftguard/rules.md` / `.driftguard/learnings.md` if present,
- on a retry only: the failure findings from the previous attempt verbatim
  (test tail or review findings) with "fix these; stay in the contract",
- one line: "Follow agents/coder.md exactly. Write your final SUMMARY/TEST
  report only."

Write its full final message to `.driftguard/traces/$ID/coder.md`; keep only
its `SUMMARY:` / `TEST:` lines. A `BLOCKED:` report → step 2.5 (do not retry
a contract problem).

### 2.3 Run the test yourself

Do not trust the coder's claim — run the contract's `test` command verbatim
with `cwd` = repo root. Non-zero exit → step 2.5 with the test output tail.

### 2.4 Review the task diff

The changes are uncommitted; snapshot the working tree into a dangling commit
(moves no branch, touches neither HEAD nor files):

```bash
IDX=$(mktemp) && cp .git/index "$IDX" \
  && GIT_INDEX_FILE="$IDX" git add -A \
  && SNAP=$(git -c user.name=driftguard -c user.email=driftguard@local \
       commit-tree "$(GIT_INDEX_FILE="$IDX" git write-tree)" -p HEAD -m "driftguard review snapshot") \
  ; rm -f "$IDX"; echo "$SNAP"
```

Then `/driftguard:review --base <base_sha> --head $SNAP --task "<task goal>"`
(`base_sha` = the task's recorded base). Verdict not `pass` → step 2.5 with
the findings. Verdict `pass` → step 2.6.

### 2.5 Failure: one fix wave, then blocked

- `python3 $PLUGIN_SCRIPTS/contract.py show` → task's `attempts` is 0:
  `contract.py attempt "$ID"`, then back to step 2.2 **with the findings**
  (exactly one retry).
- `attempts` ≥ 1: `contract.py status "$ID" blocked`, `rm -f
  .driftguard/current`, and **stop the whole run**. Report the task, the
  failure (test tail or findings), and the contract change that would unblock
  it (more files, larger `max_loc`, a spec decision). Remaining tasks stay
  `todo`; suggest `/driftguard:run --resume` after the human decides.

### 2.6 Accept

```bash
git add -A && git commit -m "<task goal> (driftguard task $ID)"
python3 $PLUGIN_SCRIPTS/contract.py status "$ID" done
python3 $PLUGIN_SCRIPTS/contract.py summary "$ID" "<coder summary, one line>"
rm -f .driftguard/current
```

(`.driftguard/` is gitignored, so `git add -A` adds only task files.) Report
one line: task id, test result, review verdict, LOC vs budget
(`git diff --numstat <base_sha> HEAD`). Continue the loop.

## 3. Finish (human gate)

When `next` prints `null`:
1. `rm -f .driftguard/current` (safety).
2. Run the repo's full test suite yourself. Failure → report and stop; the
   per-task reviews passed, so this is a cross-task interaction.
3. Final review: `/driftguard:review --base <BRANCH_BASE> --task "<SPEC.md
   goal>"` over the whole branch.
4. Present the summary and AskUserQuestion what to do next — **merge locally /
   open a PR / leave the branch**:

```
Tasks: N done, M blocked (ids)
LOC: X changed vs Y total budget
Final review: <verdict> — <open findings, if any>
Tests: full suite <pass/fail>
```

Never merge or push without the human's explicit choice.
