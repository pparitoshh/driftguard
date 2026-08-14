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
`gh` (only for PR-number mode — branch mode works without it).

## Usage

```
/driftguard:review 123                 # PR number — context via `gh`
/driftguard:review --base main         # local branch vs. base, no PR needed
/driftguard:review --task "..."        # explicit task statement override
```

Everything runs inside your Claude Code session. No API keys, no servers, no
embeddings, no Docker. Code never leaves your machine.

## How it works

1. **Context assembly (scripts, no LLM)** — plan/spec resolution chain
   (PR body → linked issue → PLAN.md → commit messages → branch name → --task),
   git development history (churn per changed file, branch commits), and a
   **digest of the Claude session that produced the branch** (matched from
   `~/.claude/projects/<slug>/*.jsonl` by commit time-window + files touched).
2. **Tier 0 deterministic pre-flight** — hallucinated-dependency check against
   PyPI, repo's own linters, AST dead-code scan, test-subversion diff analysis.
3. **Tier 1 subagent fan-out** — intent-scope, slop-redundancy,
   regression-contract, test-integrity run in parallel, each with its own
   context window, drilling into the repo with Read/Grep/Bash.
4. **Noise control** — cross-agent dedup, evidence-mandatory filter (findings
   without tool-derived evidence are *dropped*), hard finding budget, and a
   mandatory "Not checked" section so a green verdict can't be over-trusted.

No RAG: the per-review corpus is small and well-scoped by construction
(deterministic pre-filtering + subagent context isolation). An optional BM25
ranker is specced as a fallback only and is not shipped.

## Prior art, honestly

| Tool | What it does better than driftguard |
|---|---|
| **Greptile** | Catches more *bugs* — whole-codebase index, cross-service seams |
| **CodeRabbit** | More linters/SAST, more polish, per-comment rule tracing |
| **Qodo Merge** | Ticket-compliance workflows, hosted dashboards |
| **sigma** | Full spec-as-contract pipeline (if you adopt its whole workflow) |

driftguard's only claims: **intent as the reference point** (plan + session, not
just the codebase), **local and free**, and **agent-specific detection** — and
it is the only reviewer that reads the *session transcript* of how the code was
produced.

## Development

```bash
python3 -m unittest discover -s tests          # 18 tests, stdlib only
python3 evals/build_fixtures.py /tmp/fixture   # fixture repo with planted issues
```

Layout: `commands/` (slash command), `agents/` (Tier 1 subagents),
`scripts/` (context + Tier 0, stdlib-only Python), `evals/` (fixtures + results),
`driftguard-implementation-plan_1.md` (the design doc).

## Out of scope

Standalone/headless mode (CI), non-Claude host agents, vector retrieval,
multi-repo, IDE plugins, web UI. See the plan doc §15 for upgrade triggers.
