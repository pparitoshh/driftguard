---
description: Review a PR/branch against its plan, Claude session, and dev history
argument-hint: "[pr-number | --base B --head H] [--task \"...\"] [--role ds|de|other]"
allowed-tools: Bash, Read, Grep, Glob, Task, AskUserQuestion
---

# /driftguard:review — orchestration

You are running a driftguard review. Follow these steps in order. Do not skip Tier 0.
Cheapest signal first; subagents only after deterministic context exists.

## 0. Parse arguments

From `$ARGUMENTS`:
- A bare number → PR mode (`PR=<n>`).
- `--base B --head H` → local range mode (defaults: base `main`, head `HEAD`).
- `--task "..."` → explicit task statement (feeds plan resolution chain).
- `--role ds|de|other` → review role, skips the question in step 0.5
  (`ds` aliases: `data-scientist`, `ml`).
- `--repo PATH` → target repo (default: current working directory).

Create the run directory: `RUN_DIR=<repo>/.driftguard/runs/<UTC-timestamp>` (`mkdir -p`).
All context artifacts go there. `PLUGIN_SCRIPTS` = `${CLAUDE_PLUGIN_ROOT}/scripts`
(if `CLAUDE_PLUGIN_ROOT` is unset, resolve the `scripts/` directory from this command
file's installed location).

## 0.5. Resolve review role

Resolve the review role once and write it to `$RUN_DIR/role.txt` (`ds`, `de`, or
`other`) — subagents and the synthesizer read it from there; never re-ask.

1. `--role` given → normalize (`data-scientist`/`ml` → `ds`) and use it, no question.
2. Otherwise **ask** (AskUserQuestion): *"What kind of feature is this PR?"* —
   **Data scientist / Data engineer / Other**. Before asking, look at
   `git diff --name-only B...H` and the repo manifests for a hint to prefix the
   question with:
   - paths matching `*.ipynb`, `train*.py`, `features/**`, `models/**`,
     `ranking/**`, `recsys/**`, `notebooks/**`, or a manifest adding
     torch/tensorflow/sklearn/xgboost/lightgbm/faiss → "looks data-scientist"
   - paths matching `pipelines/**`, `dags/**`, `dbt/**`, `sql/**` → "looks data-engineer"
3. `de` → state "data-engineer role not built yet (v0.4) — continuing as *other*",
   write `other`, and note this in the final "Not checked" section.
   `other` → the rest of this file, unchanged.

## 1. Gather context (scripts, in parallel where possible)

```bash
python3 $PLUGIN_SCRIPTS/plan_resolve.py  --repo <repo> [--pr N] [--base B --head H] [--task "..."] > $RUN_DIR/plan.json
python3 $PLUGIN_SCRIPTS/dev_history.py   --repo <repo> --base B --head H [--pr N]                  > $RUN_DIR/history.json
python3 $PLUGIN_SCRIPTS/risk_score.py    --repo <repo> --base B --head H                           > $RUN_DIR/risk.json
git -C <repo> diff B...H > $RUN_DIR/diff.patch
```

Then read `history.json` and feed its `range_start` / `range_end` / `changed_files`
into the session extractor, and `changed_files` into team context (path-scoped rules):

```bash
python3 $PLUGIN_SCRIPTS/session_extract.py --repo <repo> --since <range_start> --until <range_end> \
    --files <comma-separated changed files> > $RUN_DIR/session.md
python3 $PLUGIN_SCRIPTS/team_context.py --repo <repo> --files <comma-separated changed files> \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['markdown'])" > $RUN_DIR/team_context.md
```

`team_context.md` carries `.driftguard/rules.md` (plain-English custom rules,
path-scoped sections, negative rules = filters), `.driftguard/learnings.md`
(preferences recorded via /driftguard:learn), and auto-detected guideline files
(CLAUDE.md / AGENTS.md / .cursorrules / copilot-instructions.md). Rules and negative
rules are **binding** on every subagent; learnings steer but never suppress Tier 0
`error` findings.

If a script reports `skipped` or errors, note it in `not_checked` and continue — context
sources are enrichment, never blockers. The plan resolution chain always resolves to
*something*; state which link won.

## 2. Tier 0 — deterministic pre-flight (no LLM)

Run all six, in parallel (seven when `role=ds`):

```bash
python3 $PLUGIN_SCRIPTS/preflight/deps_check.py       --repo <repo> --base B --head H > $RUN_DIR/t0_deps.json
python3 $PLUGIN_SCRIPTS/preflight/secrets_scan.py     --repo <repo> --base B --head H > $RUN_DIR/t0_secrets.json
python3 $PLUGIN_SCRIPTS/preflight/osv_check.py        --repo <repo> --base B --head H > $RUN_DIR/t0_osv.json
python3 $PLUGIN_SCRIPTS/preflight/run_linters.py      --repo <repo> --base B --head H > $RUN_DIR/t0_linters.json
python3 $PLUGIN_SCRIPTS/preflight/dead_code.py        --repo <repo> --base B --head H > $RUN_DIR/t0_deadcode.json
python3 $PLUGIN_SCRIPTS/preflight/test_subversion.py  --repo <repo> --base B --head H > $RUN_DIR/t0_tests.json
# role=ds only:
python3 $PLUGIN_SCRIPTS/preflight/ml_patterns.py      --repo <repo> --base B --head H > $RUN_DIR/t0_ml.json
```

`deps_check` and `osv_check` make outbound requests and are **disabled by default**
(they run, but skip their network step unless `DRIFTGUARD_ALLOW_NETWORK=1` is set).
When their JSON reports a `network disabled` skip, copy that verbatim into
"Not checked" — never present it as a clean result.

Findings here are already evidence-backed. `error` severity findings (a dependency
that does not exist on PyPI/npm, a live credential, a known-vulnerable pinned dep)
are blockers by default.

## 3. Tier 1 — fan out review subagents (Task tool, in parallel)

Launch all five subagents in ONE message (six when `role=ds`), passing each: the run
directory path, the repo path, and the diff range. Each subagent reads
`$RUN_DIR/plan.json`, `$RUN_DIR/session.md`, `$RUN_DIR/diff.patch`, `history.json`,
`team_context.md` and drills into the repo itself with Read/Grep/Bash:

- `driftguard:intent-scope` — was the plan built, and only the plan?
- `driftguard:slop-redundancy` — more code than the task needs?
- `driftguard:regression-contract` — broken callers, contract drift?
- `driftguard:test-integrity` — coverage of new paths, weakened tests (fuses with t0_tests.json)?
- `driftguard:security` — holes introduced by the change (fuses with t0_secrets.json / t0_osv.json)?
- `driftguard:ds-review` — **role=ds only**: methodology — split integrity, leakage,
  ranking labels/sampling, evaluation protocol, hygiene (fuses with t0_ml.json)?

## 4. Synthesize + noise control (deterministic rules, apply strictly)

1. Merge Tier 0 + subagent findings.
2. **Dedup**: same file + overlapping line range → one finding (keep highest severity,
   merge evidence lists).
3. **Evidence filter**: DROP any finding whose `evidence` list is empty or asserts a
   file fact you cannot point to. Do not soften — drop.
4. **Vagueness filter**: DROP findings without file + line + concrete `suggested_action`.
5. **Rules filter**: DROP findings that contradict a negative rule in team_context.md.
6. **Budget**: cap at 10 findings, severity-ranked (error > warning > info).
   Summarise the tail in one sentence.

## 5. Output contract (exactly this shape)

```markdown
## driftguard review — <repo> <base>...<head>

**Checked against:** <which plan source won: pr-body | linked-issue | plan-doc | commits | branch-name | task-flag>
**Role:** ds | other
**Verdict:** pass | review_needed | blocked
**Risk:** <low|medium|high from risk.json> · **Review effort:** <estimate> · <N files, M lines>

### Walkthrough
| File | +/- | Kind | What changed |
|---|---|---|---|
| `path` | +a/-d | code/test/docs/manifest | one line (sensitive paths first — use risk.json's suggested_review_order) |

### Summary
<plain-language: what was built, and whether it matches what was asked>

### Findings
1. **[severity][category]** `file:line-range` — claim
   - Evidence: <tool/script output>
   - Action: <concrete fix>

### ML checks walked          ← role=ds only; every group listed, clean or not
- A Split integrity: clean | N findings
- B Data leakage: …
- C Features & skew: …
- D Labels & feedback: …
- E Negative sampling & loss: …
- F Evaluation: …
- G Experiment hygiene: …
- H Data audit & robustness: …

### Not checked
- <every skipped source/check, and why>   ← mandatory, never omit
- when role=ds, also: raw training data not inspected (no warehouse/feature-store
  access); train/serve feature parity asserted from code, not verified against the
  store; notebook cell outputs not trusted; online A/B design out of scope
- if `de` was requested: note it ran as *other* (role not built yet)
```

A `pass` verdict without a populated "Not checked" section is a contract violation —
a green review must not license skipping human scrutiny.

Close with one line:

> Findings are evidence-linked above. Say **"fix findings 1-3"** and this session will
> address them; correct a finding and say **"/driftguard:learn ..."** to make the
> correction permanent for future reviews.
