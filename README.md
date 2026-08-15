# driftguard

A Claude Code plugin that reviews a PR or branch against **what it was asked to
build** — reconstructed from the plan/spec, the Claude session that produced it,
and the git development history.

Existing AI reviewers ask *"is this code correct?"* driftguard asks
*"is this what was actually requested — and only that?"*

## What it catches

1. **Intent drift** — the agent built what you asked, plus three things you didn't.
2. **Slop** — one-caller abstractions, one-case config, placeholder logic,
   reimplementations of what already exists.
3. **Agent-specific defects** — hallucinated package imports (a supply-chain vector),
   tests weakened so the suite goes green, dead code added by the diff.
4. **Security holes introduced by the diff** — committed credentials (evidence
   redacted), pinned deps with known CVEs (OSV.dev), injection/deserialization/authz
   gaps traced by a dedicated security subagent.
5. **Methodology bugs in ML work** (data-scientist role) — the class of defect where
   the code is correct, the plan was followed, the tests pass, and the result is
   still wrong: a leaked split, a metric computed on training data, a feature
   window that reaches past the prediction timestamp, an NDCG no code in the diff
   produces.

## Install

Inside **any** Claude Code session, straight from GitHub:

```
/plugin marketplace add pparitoshh/driftguard
/plugin install driftguard@driftguard
```

(The repo root doubles as a marketplace via `.claude-plugin/marketplace.json`.)

Local alternative: clone this repo and either copy it into your project's
`.claude/plugins/` or point `/plugin marketplace add /path/to/driftguard` at it.

Requirements on the target machine: `git`, `python3` (3.11+, stdlib only), and
`gh` (only for PR-number mode — branch mode works without it). The PyPI/npm/OSV
registry checks are the only thing that would use the network, and they are off
unless you opt in (see below); disabled or offline, they degrade to
"check skipped", never to a crash.

## Usage

```
/driftguard:review 123                 # PR number — context via `gh`
/driftguard:review --base main         # local branch vs. base, no PR needed
/driftguard:review --task "..."        # explicit task statement override
/driftguard:review --role ds           # data-scientist review (skips the role question)
/driftguard:learn "prefer early returns in auth code — easier to debug in prod"
```

Everything runs inside your Claude Code session. No API keys, no servers, no
embeddings, no Docker. Code never leaves your machine.

**Offline by default.** Two checks would otherwise make outbound requests —
`deps_check` (package names to PyPI/npm) and `osv_check` (pinned name+version
pairs to OSV.dev). Both reveal part of your dependency graph, so both are off
unless you opt in:

```bash
DRIFTGUARD_ALLOW_NETWORK=1 /driftguard:review 123    # enable registry + CVE lookups
```

Disabled, they report themselves under "Not checked" as
`network disabled (DRIFTGUARD_ALLOW_NETWORK unset)` — never as a silent pass.
Every other check (secrets, linters, dead code, test subversion, ML patterns,
and all subagents) is local-only and unaffected.

A review ends with a fix-loop handoff — say **"fix findings 1-3"** and the same
session addresses them (no separate autofix bot needed; the reviewer *is* your
coding agent).

## Personalize it (plain files, no dashboard)

Like Greptile/CodeRabbit custom rules and learnings — but as markdown in your repo:

- **`.driftguard/rules.md`** — standing review rules in plain English. Binding on
  every subagent. Supports path scoping and negative rules (filters):

  ```markdown
  # Rules
  Every new endpoint must have an auth check.

  ## path: scripts/**
  Never flag shell=True here — these are local dev tools.
  ```

- **`.driftguard/learnings.md`** — append-only memory, one dated line per
  preference. Written by `/driftguard:learn "..."` (credentials are redacted
  before writing). Steers findings; never suppresses Tier 0 errors.
- **Auto-detected guidelines** — `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and
  `.github/copilot-instructions.md` are picked up automatically.

## Role-based review

Before reviewing, driftguard asks what kind of feature the PR is — **data
scientist**, **data engineer**, or **other** — hinting at an answer from the
changed paths and manifests, so confirming takes one keystroke. Pass
`--role ds|de|other` to skip the question.

`ds` attaches a domain specialist a generalist reviewer can't replace: the code
can be correct, the plan followed, the tests green, and the NDCG still fake
because the same user is on both sides of the split. It adds one deterministic
pre-flight (`ml_patterns.py` — fit-before-split, metrics on train data, label in
the feature list, tuning before the split, missing seeds) and the **ds-review**
subagent, which walks a 48-check catalog: split integrity, data leakage,
training/serving skew, ranking labels and feedback loops, negative sampling and
loss, evaluation protocol, experiment hygiene, and data audit. Findings are
tagged `ml-*` and carry stable IDs (`DS-08`), so a rule can suppress one check
("never flag DS-18 here — position is a deliberate feature"). Every check group
is listed in the output, clean or not.

`de` is specced but not built yet (v0.4) — it runs as *other* and says so.
`other` is exactly the generic review, unchanged.

## How it works

1. **Context assembly (scripts, no LLM)** — plan/spec resolution chain
   (PR body → linked issue → PLAN.md → commit messages → branch name → --task),
   git development history (churn per changed file, branch commits), a
   **digest of the Claude session that produced the branch** (matched from
   `~/.claude/projects/<slug>/*.jsonl` by commit time-window + files touched),
   a **deterministic triage card** (risk level, review-effort estimate, churn
   hotspots, sensitive-path touches, suggested review order), and **team context**
   (`.driftguard/rules.md` custom/path-scoped rules, `.driftguard/learnings.md`
   memory, auto-detected CLAUDE.md/AGENTS.md/.cursorrules/copilot-instructions).
2. **Tier 0 deterministic pre-flight** — hallucinated-dependency check against
   PyPI **and npm**, **secrets scan** (redacted evidence), **known-vulnerability
   check** for pinned deps (OSV.dev), repo's own linters (ruff, local eslint),
   AST dead-code scan, test-subversion diff analysis — plus the **ML-methodology
   scan** (`ml_patterns.py`) when the role is data-scientist.
3. **Tier 1 subagent fan-out** — intent-scope, slop-redundancy,
   regression-contract, test-integrity, **security** run in parallel, each with
   its own context window, drilling into the repo with Read/Grep/Bash — plus
   **ds-review** when the role is data-scientist.
4. **Noise control** — cross-agent dedup, evidence-mandatory filter (findings
   without tool-derived evidence are *dropped*), team negative-rules filter, hard
   finding budget, and a mandatory "Not checked" section so a green verdict can't
   be over-trusted.
5. **Learning loop** — correct a finding with `/driftguard:learn "..."` and the
   preference is appended to `.driftguard/learnings.md` (plain markdown, no
   database), steering every future review. Fix findings conversationally —
   driftguard already runs inside your coding agent.

No RAG: the per-review corpus is small and well-scoped by construction
(deterministic pre-filtering + subagent context isolation). An optional BM25
ranker is specced as a fallback only and is not shipped.

Every review returns the same shape: **verdict** (pass / review_needed / blocked),
**risk + review-effort** from the triage card, a **walkthrough** table of what
changed per file (sensitive paths first), severity- and category-tagged
**findings** with tool evidence and a concrete action each, and a mandatory
**"Not checked"** section listing everything the review did not verify.

## Features

- **Intent as the reference point** — reviews against the stated plan and the
  session transcript, not just the codebase. driftguard is the only reviewer
  that reads the *transcript* of how the code was produced.
- **Agent-specific detection** — catches the failure modes of AI-written code:
  overreach beyond the plan, silent scope creep, undoing of earlier decisions.
- **Local and free** — runs entirely on your machine; your code is never
  hosted on someone else's servers.
- **Secrets scan** — detects accidentally committed credentials, with values
  redacted in output.
- **Known-CVE dependency check** — queries the free OSV.dev API (no key
  required); off unless `DRIFTGUARD_ALLOW_NETWORK=1`.
- **npm dependency hygiene** — validates imports against `package.json` and
  the npm registry.
- **Security subagent** — a dedicated review pass focused on vulnerabilities.
- **Risk/effort triage card** — every review is scored for risk, complexity,
  and estimated review effort.
- **Custom rules and learnings** — plain markdown in your repo, no database.
- **Role-based review** — a data-scientist role that checks methodology
  (splits, leakage, ranking evaluation) the way a generalist reviewer can't.

## Development

```bash
python3 -m unittest discover -s tests                    # 71 tests, stdlib only
python3 evals/build_fixtures.py /tmp/fixture             # planted-issue fixture repo
python3 evals/build_fixtures.py /tmp/ds --set ds         # data-scientist fixture
```

Layout: `commands/` (slash commands), `agents/` (Tier 1 subagents),
`scripts/` (context + Tier 0, stdlib-only Python), `evals/` (fixtures + results),
`driftguard-implementation-plan_1.md` (the design doc),
`driftguard-role-review-plan.md` (the v0.3 role-based review design),
`driftguard-market-parity-plan.md` (v0.2 upgrade analysis).

## Out of scope

Standalone/headless mode (CI), non-Claude host agents, vector retrieval,
multi-repo, IDE plugins, web UI. See the plan doc §15 for upgrade triggers.
