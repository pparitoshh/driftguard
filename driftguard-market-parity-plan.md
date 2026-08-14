# driftguard — Market Parity Plan (v0.2)

> What the paid reviewers actually ship (researched 2026-08-14 from vendor pages/docs),
> what is worth adopting, and how to adopt it **without breaking the lightweight
> principle**: no servers, no keys, no embeddings, stdlib-only scripts, subagent
> fan-out instead of infrastructure, evidence-mandatory findings, everything optional
> degrades to "skipped".

---

## 1. What the paid tools ship (research summary)

### Greptile ($30/seat/mo) — greptile.com
- Graph index of the whole codebase; **swarm of parallel agents** review each PR and
  assess impact *beyond the diff*.
- **Learns your codebase over time** by reading the team's PR comments (memory).
- **Custom rules in plain English**, repo-scoped context.
- TREX: writes and runs tests per PR in a sandbox (3 credits/review).
- Fix-in-IDE handoff (Claude Code/Cursor/Codex/Devin), MCP server, `/greploop`
  agent-iteration loop. Self-hosted option, SOC2.

### CodeRabbit — docs.coderabbit.ai
- Review: bug detection, **one-click committable fixes**, AI summaries +
  **walkthrough** (per-change summaries, sequence diagrams, **review-effort estimate**),
  **incremental reviews** on new commits.
- **50+ linters/SAST** run in sandboxes (ruff, eslint, gitleaks/betterleaks…), mostly
  on by default, auto-selected per project.
- Taxonomy: content categories (Security & Privacy, Stability, Data Integrity,
  Functional Correctness, Performance, Maintainability) × severity
  (Critical/Major/Minor/Trivial/Info).
- **Triage**: PR queue ranked by risk/reward/effort/complexity (P0–P3).
- **Change Stack**: large diffs reorganised into layers with blast-radius and
  architecture impact.
- **Security**: deep scans, **dependency vulnerability detection (GHSA)**, leaked
  secrets, continuous posture.
- **Learnings**: natural-language preferences captured from chat, scoped repo/org,
  approval flow, **credential redaction before storage**, vector similarity search.
- **Code guidelines**: auto-detects CLAUDE.md / .cursorrules / copilot-instructions.
- **Path instructions**: glob-scoped plain-English rules. Knowledge base = learnings +
  guidelines + issue trackers + MCP + cross-repo. Jira/Linear requirement validation.
- Finishing touches: autofix, docstring/unit-test generation. Profiles
  (Chill/Assertive) for noise control.

### Qodo Merge — qodo.ai
- Agent suite with high-precision focus; severity-ranked suggestions.
- **Requirement validation**: partial implementation of linked tickets flagged, with
  evidence links to the spec.
- **Agent-ready fix prompts** per finding (paste into your coding agent).
- Auto PR descriptions/tests/docs; cross-repo breaking-change detection; rules
  auto-generated and enforced.

### Graphite Diamond — graphite.dev
- Instant reviews, 1-click fixes, "**high signal**" as the headline (<5% negative
  comment rate).
- **Custom rules in plain language** + templates (OWASP, style guides).
- **Filters**: define comment types *not* to leave (explicit negative rules).
- Categories: logic bug, edge case, security, **accidentally committed code**,
  performance, style, docs.

## 2. Adopt / adapt / reject

| Market feature | Decision | Lightweight shape |
|---|---|---|
| Secrets / accidentally-committed-code scan | **ADOPT (Tier 0)** | `preflight/secrets_scan.py` — regexes over added lines, redacted evidence |
| Dependency vulnerability check (GHSA) | **ADOPT (Tier 0)** | `preflight/osv_check.py` — OSV.dev free API, only for manifests changed in the diff; offline → skipped |
| Polyglot dep hygiene (CodeRabbit covers all languages) | **ADOPT (Tier 0)** | extend `deps_check.py` to JS/TS imports vs package.json + npm registry |
| Repo linters (50+ tools) | **ADAPT** | already run repo's ruff; add **local eslint** (`node_modules/.bin` only — never fetch). The repo's own config is the scope |
| Triage risk/effort scoring | **ADOPT (deterministic)** | `risk_score.py` — churn hotspots, sensitive paths, size, test gap → risk level + review-effort estimate + suggested review order |
| Security review agent | **ADOPT (Tier 1)** | `agents/security.md` — injection, unsafe deserialization, authz gaps, secret handling; fuses with t0_secrets/t0_osv |
| Walkthrough / PR summary + effort | **ADOPT (output contract)** | per-file change table + effort line + suggested review order; numbers from `risk_score.py`, prose by host |
| Learnings / memory | **ADAPT (no vector DB)** | `.driftguard/learnings.md` plain markdown, append via `/driftguard:learn`, loaded into every review; corpus is tiny → no embeddings needed |
| Custom rules / path instructions / filters | **ADOPT** | `.driftguard/rules.md` with optional `## path: <glob>` sections and negative rules ("never flag …") |
| Code-guidelines auto-detection | **ADOPT** | `team_context.py` picks up CLAUDE.md / AGENTS.md / .cursorrules / .github/copilot-instructions.md |
| Agent-ready fix prompts / fix-in-IDE | **ADAPT (free)** | driftguard runs *inside* the coding agent — output contract ends with a fix-loop handoff line ("say `fix findings 1-3`") |
| Finding categories + severity taxonomy | **ADAPT** | keep error/warning/info; add optional `category` field (security/stability/correctness/tests/intent/slop/maintainability) |
| Sequence/architecture diagrams | **REJECT (for now)** | host may emit mermaid ad hoc; no code. Trigger: user demand |
| Vector-search learnings | **REJECT** | plain file suffices at repo scale; trigger: learnings file > ~100 entries |
| Sandbox test execution (TREX) | **REJECT** | heavyweight infra; Tier 0 + test-integrity cover the signal. Trigger: CI/headless mode |
| Multi-PR triage dashboard | **REJECT** | driftguard reviews one range at a time, user-invoked. Trigger: team usage |
| One-click fixes / autofix commits | **REJECT** | redundant inside a coding agent; conversational fix loop instead |
| Cross-repo analysis, hosted dashboards, Slack bots | **REJECT** | out of scope per implementation plan §15 |

## 3. What ships in v0.2

**Tier 0 (scripts, deterministic, stdlib-only):**
1. `scripts/preflight/secrets_scan.py` — token formats (AWS/GitHub/Slack/Stripe/Google/
   private keys) + generic `secret = "…"` assignments with placeholder exclusions;
   evidence redacted (first 4 chars + `…`).
2. `scripts/preflight/osv_check.py` — pinned deps added/changed in requirements*.txt /
   pyproject.toml / package.json queried against OSV.dev (PyPI + npm). Offline → skipped.
3. `scripts/preflight/deps_check.py` — extended: JS/TS imports vs package.json +
   registry.npmjs.org (scoped packages, node builtins, relative imports skipped).
4. `scripts/preflight/run_linters.py` — extended: local eslint when configured and
   `node_modules/.bin/eslint` exists (never npx-fetch).
5. `scripts/risk_score.py` — deterministic triage card: risk level, review-effort
   estimate, churn hotspots, sensitive-path touches, test gap, suggested review order.
6. `scripts/team_context.py` — collects `.driftguard/rules.md`,
   `.driftguard/learnings.md`, CLAUDE.md / AGENTS.md / .cursorrules /
   copilot-instructions.md into one artifact.

**Tier 1:**
7. `agents/security.md` — fifth subagent; fuses with t0_secrets/t0_osv.

**Personalization:**
8. `commands/learn.md` (`/driftguard:learn "…"`) — appends a dated, redacted entry to
   `.driftguard/learnings.md`.

**Orchestration/output:**
9. `commands/review.md` — wires 1–8; output contract gains **Walkthrough** (per-file
   table + effort + review order), finding **categories**, and a **fix-loop** handoff.

**Eval/docs:**
10. New tests for every script above; fixture gains a planted secret + a pinned
    vulnerable dep; README + results.md updated; plugin version → 0.2.0.

## 4. What did NOT change

The moat is unchanged: **intent as the reference point** (plan + session transcript +
dev history), **local and free**, **agent-specific detection**. Nothing in this plan
adds infrastructure, network calls beyond free registry/OSV lookups (all degrading to
"skipped"), or non-stdlib dependencies.
