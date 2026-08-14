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
`gh` (only for PR-number mode — branch mode works without it). Network access is
used only for the PyPI/npm/OSV registry checks; offline they degrade to
"check skipped", never to a crash.

## Usage

```
/driftguard:review 123                 # PR number — context via `gh`
/driftguard:review --base main         # local branch vs. base, no PR needed
/driftguard:review --task "..."        # explicit task statement override
/driftguard:learn "prefer early returns in auth code — easier to debug in prod"
```

Everything runs inside your Claude Code session. No API keys, no servers, no
embeddings, no Docker. Code never leaves your machine.

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
   AST dead-code scan, test-subversion diff analysis.
3. **Tier 1 subagent fan-out** — intent-scope, slop-redundancy,
   regression-contract, test-integrity, **security** run in parallel, each with
   its own context window, drilling into the repo with Read/Grep/Bash.
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

## Prior art, honestly

| Tool | What it does better than driftguard |
|---|---|
| **Greptile** | Catches more *bugs* — whole-codebase graph index, cross-service seams, sandboxed test execution (TREX) |
| **CodeRabbit** | 50+ sandboxed linters/SAST, multi-PR triage dashboards, change-stack visualization, more polish |
| **Qodo Merge** | Cross-repo breaking-change detection, hosted dashboards |
| **sigma** | Full spec-as-contract pipeline (if you adopt its whole workflow) |

driftguard's claims: **intent as the reference point** (plan + session, not just
the codebase), **local and free**, **agent-specific detection** — and it is the
only reviewer that reads the *session transcript* of how the code was produced.
Since v0.2 it also covers the market's table stakes in lightweight form: secrets
scan, known-CVE dep check (OSV), npm dep hygiene, security subagent, risk/effort
triage card, custom rules, and learnings — see `driftguard-market-parity-plan.md`
for the full adopt/reject analysis. What it still won't do: host your code on
someone else's servers.

## Development

```bash
python3 -m unittest discover -s tests          # 36 tests, stdlib only
python3 evals/build_fixtures.py /tmp/fixture   # fixture repo with planted issues
```

Layout: `commands/` (slash commands), `agents/` (Tier 1 subagents),
`scripts/` (context + Tier 0, stdlib-only Python), `evals/` (fixtures + results),
`driftguard-implementation-plan_1.md` (the design doc),
`driftguard-market-parity-plan.md` (v0.2 upgrade analysis).

## Out of scope

Standalone/headless mode (CI), non-Claude host agents, vector retrieval,
multi-repo, IDE plugins, web UI. See the plan doc §15 for upgrade triggers.
