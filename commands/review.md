---
description: Review a PR/branch against its plan, Claude session, and dev history
argument-hint: "[pr-number | --base B --head H] [--task \"...\"]"
allowed-tools: Bash, Read, Grep, Glob, Task
---

# /driftguard:review — orchestration

You are running a driftguard review. Follow these steps in order. Do not skip Tier 0.
Cheapest signal first; subagents only after deterministic context exists.

## 0. Parse arguments

From `$ARGUMENTS`:
- A bare number → PR mode (`PR=<n>`).
- `--base B --head H` → local range mode (defaults: base `main`, head `HEAD`).
- `--task "..."` → explicit task statement (feeds plan resolution chain).
- `--repo PATH` → target repo (default: current working directory).

Create the run directory: `RUN_DIR=<repo>/.driftguard/runs/<UTC-timestamp>` (`mkdir -p`).
All context artifacts go there. `PLUGIN_SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts`
(if `CLAUDE_PLUGIN_ROOT` is unset, resolve the `scripts/` directory from this command
file's installed location).

## 1. Gather context (scripts, in parallel where possible)

```bash
python3 $PLUGIN_SCRIPTS/plan_resolve.py  --repo <repo> [--pr N] [--base B --head H] [--task "..."] > $RUN_DIR/plan.json
python3 $PLUGIN_SCRIPTS/dev_history.py   --repo <repo> --base B --head H [--pr N]                  > $RUN_DIR/history.json
git -C <repo> diff B...H > $RUN_DIR/diff.patch
```

Then read `history.json` and feed its `range_start` / `range_end` / `changed_files` into
the session extractor:

```bash
python3 $PLUGIN_SCRIPTS/session_extract.py --repo <repo> --since <range_start> --until <range_end> \
    --files <comma-separated changed files> > $RUN_DIR/session.md
```

If a script reports `skipped` or errors, note it in `not_checked` and continue — context
sources are enrichment, never blockers. The plan resolution chain always resolves to
*something*; state which link won.

## 2. Tier 0 — deterministic pre-flight (no LLM)

Run all four, in parallel:

```bash
python3 $PLUGIN_SCRIPTS/preflight/deps_check.py       --repo <repo> --base B --head H > $RUN_DIR/t0_deps.json
python3 $PLUGIN_SCRIPTS/preflight/run_linters.py      --repo <repo> --base B --head H > $RUN_DIR/t0_linters.json
python3 $PLUGIN_SCRIPTS/preflight/dead_code.py        --repo <repo> --base B --head H > $RUN_DIR/t0_deadcode.json
python3 $PLUGIN_SCRIPTS/preflight/test_subversion.py  --repo <repo> --base B --head H > $RUN_DIR/t0_tests.json
```

Findings here are already evidence-backed. `error` severity findings (e.g. a dependency
that does not exist on PyPI) are blockers by default.

## 3. Tier 1 — fan out review subagents (Task tool, in parallel)

Launch all four subagents in ONE message, passing each: the run directory path, the repo
path, and the diff range. Each subagent reads `$RUN_DIR/plan.json`, `$RUN_DIR/session.md`,
`$RUN_DIR/diff.patch`, `history.json` and drills into the repo itself with Read/Grep/Bash:

- `driftguard:intent-scope` — was the plan built, and only the plan?
- `driftguard:slop-redundancy` — more code than the task needs?
- `driftguard:regression-contract` — broken callers, contract drift?
- `driftguard:test-integrity` — coverage of new paths, weakened tests (fuses with t0_tests.json)?

## 4. Synthesize + noise control (deterministic rules, apply strictly)

1. Merge Tier 0 + subagent findings.
2. **Dedup**: same file + overlapping line range → one finding (keep highest severity,
   merge evidence lists).
3. **Evidence filter**: DROP any finding whose `evidence` list is empty or asserts a file
   fact you cannot point to. Do not soften — drop.
4. **Vagueness filter**: DROP findings without file + line + concrete `suggested_action`.
5. **Budget**: cap at 10 findings, severity-ranked. Summarise the tail in one sentence.

## 5. Output contract (exactly this shape)

```markdown
## driftguard review — <repo> <base>...<head>

**Checked against:** <which plan source won: pr-body | linked-issue | plan-doc | commits | branch-name | task-flag>
**Verdict:** pass | review_needed | blocked

### Summary
<plain-language: what was built, and whether it matches what was asked>

### Findings
1. **[severity]** `file:line-range` — claim
   - Evidence: <tool/script output>
   - Action: <concrete fix>

### Not checked
- <every skipped source/check, and why>   ← mandatory, never omit
```

A `pass` verdict without a populated "Not checked" section is a contract violation —
a green review must not license skipping human scrutiny.
