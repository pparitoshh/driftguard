# driftguard — Implementation Plan (v3, Claude Code plugin)

> A **Claude Code plugin** that reviews a PR or branch against **what it was asked to
> build** — reconstructed from the plan/spec, the Claude session that produced it, and the
> git development history — and answers: *is this what was requested, and only that?*
>
> v3 scope reset: the zoomcamp-capstone framing, standalone/headless mode, and the
> retrieval benchmark are **dropped**. The plugin is the whole product.

---

## 1. Problem

AI coding agents now write most of the code. The bottleneck moved from *writing* to
*reviewing*. Existing AI reviewers ask **"is this code correct?"** Almost nobody asks
**"is this what was actually requested?"**

Three failure modes a correctness-focused reviewer structurally cannot catch:

1. **Intent drift** — the agent built what you asked, plus three things you didn't. Each
   addition is individually defensible; nothing is *wrong*; the PR is still not what you
   approved.
2. **Slop** — 400 lines where 40 would do. Abstractions with one caller. Config for one
   case. Placeholder logic that looks reasonable and does nothing.
3. **Agent-specific defects** — hallucinated package imports (a supply-chain vector, not a
   typo), tests weakened or skipped so the suite goes green, band-aid fixes that silence
   errors instead of resolving them.

The unique advantage this plugin has over hosted reviewers: **it lives where the request
lives.** The plan doc, the Claude session transcript, and the git history are all on the
same machine. Greptile/CodeRabbit can only see the PR; we can see the *conversation that
produced the PR*.

## 2. What changed from v2 — read this first

| v2 (dropped) | v3 (now) | Why |
|---|---|---|
| Dual mode: host plugin **+** standalone LiteLLM/Ollama | **Claude Code plugin only** | One product, one harness, no `Harness` protocol tax |
| Retrieval benchmark (S vs. V vs. H) as headline deliverable | **Gone** — no benchmark | Was the zoomcapstone contribution; no longer needed |
| Vector RAG: LlamaIndex + fastembed + LanceDB, AST chunking | **No RAG at all.** Direct reads + structural tools + subagent context isolation. Optional BM25 ranker only if context budget breaks (§6) | The review corpus per PR is small and well-scoped; chunking/embedding infrastructure buys nothing here |
| GitHub Action, FastAPI, Streamlit, DuckDB, Langfuse, docker-compose | **None** — interface is a slash command; state is files | Plugin distribution is the deliverable |
| Zoomcamp rubric mapping | Removed | — |

Kept intact: the problem statement, the prior-art reading, Tier 0 deterministic checks,
Tier 1 specialists (now subagents), the output contract, and noise control.

## 3. Prior art — read this before building

| Tool | What it does | Where it stops |
|---|---|---|
| **Greptile** ⚠️ | Indexes whole codebase; reviews each PR against it; markets as the *"independent validation layer for AI-generated code"* | **Closest competitor.** Hosted SaaS, paid, code leaves your machine. Reference point is the codebase, never the request |
| **CodeRabbit** | Indexes codebase + tickets + prior PRs; structural graph per review; 40+ linters/SAST | Hosted SaaS. Reference point still isn't the stated plan |
| **Qodo Merge 2.0** | Parallel agent suite: Critical Issues, Duplicated Logic, Ticket Compliance | Hosted; ticket-scoped, not plan- or session-scoped |
| **Qodo PR-Agent** (OSS) | One LLM call per tool, no retrieval | Doesn't know what was requested |
| **Claude Code Review** | 9 specialized sub-agents in parallel | ~$15–25/PR; reviews correctness, not intent |
| **sigma** | Gherkin spec-as-contract through implement→verify→review; maker≠checker | Must adopt its whole pipeline; spec must be Gherkin |

**Nobody uses the session transcript as a review source.** That is the gap. Put this table
in the README, and state plainly: Greptile and CodeRabbit will catch more *bugs* than this
does. The claim is about *intent and slop*, not bug count.

## 4. Positioning — what's actually ours

| Axis | Hosted reviewers | driftguard |
|---|---|---|
| Reference point | The codebase | **The stated plan/spec + the Claude session + dev history** |
| Core question | Is this correct? | Is this *what was asked*, and is it more code than the task needed? |
| Where it runs | Vendor servers | **Inside your Claude Code session** — zero marginal cost, no keys, code never leaves |
| Detectors | Generic bug detection | **Agent-specific**: hallucinated deps, test subversion, default-fill slop, unrequested scope |

## 5. Interface

One slash command:

```
/driftguard:review 123                 # PR number — context via `gh`
/driftguard:review --base main         # local branch vs. base, no PR needed
/driftguard:review --task "..."        # explicit task statement override
```

Runs entirely inside Claude Code. The host *is* the LLM — prompts, structure, and helper
scripts come from the plugin; inference uses the user's existing subscription. No API
keys, no services, no background processes.

## 6. Context assembly — the three sources (no RAG)

### Source 1 — Plan/spec (resolution chain, first hit wins)

PR body → linked issue body → `PLAN.md` / `docs/specs/` / `.driftguard/plan.md` →
commit messages on the branch → branch name → `--task "..."` flag. Always resolves to
*something*; the chain and which link won are stated in the review output.

### Source 2 — Claude session transcript (the differentiator)

Transcripts live at `~/.claude/projects/<path-slug>/*.jsonl`. `scripts/session_extract.py`:

1. Identify candidate sessions for the repo (cwd match on the project slug).
2. Match the session(s) that produced this branch by **timestamp window of branch
   commits** + **overlap of files touched** (Write/Edit tool inputs carry `file_path`).
3. Extract only high-signal events: user requests, plan-mode plans, compaction summaries,
   permission-granted decisions, files edited. Skip tool-result bodies (huge, low signal).
4. Emit a compact, chronologically-ordered digest — target ≤ a few KB.

This digest is the ground truth for *what was asked*. It answers "the user approved X at
14:32 and explicitly said no to Y" — which no diff or PR body ever contains.

### Source 3 — Development history

`git log --follow` on diff-touched files (prior churn), `git blame -L` on modified ranges
(provenance), branch commit messages, PR review comments via `gh`. Also answers "was this
file already touched 5 times this month" — a slop signal.

### Context budget without RAG

Per review the corpus is deliberately small: one diff, one plan doc, one session digest,
file-scoped history. Three mechanisms keep it that way, in order:

1. **Deterministic pre-filtering** — everything above is already scoped to the diff's
   files and the branch's time window before any model sees it.
2. **Subagent context isolation** — each review subagent (§8) gets only the slices it
   needs and drills down itself via Read/Grep/Bash. *Subagent fan-out replaces retrieval
   infrastructure*: each specialist has its own context window, so total review capacity
   scales by agents, not by stuffing one window.
3. **Optional BM25 ranker — only if 1–2 demonstrably break.** If a monorepo-scale diff or
   a month-long session log overflows, rank pre-filtered candidate chunks (session events,
   spec sections) with **Okapi BM25** (`rank_bm25`, pure Python, ~zero deps). No
   embeddings, no vector store, no chunking pipeline — BM25 scores whole documents/sections
   against the plan text and takes top-k. Ship it only behind evidence it's needed; the
   default path must not import it.

## 7. Tier 0 — deterministic pre-flight (bundled scripts, no LLM)

Runs first, on every review, as `scripts/preflight/*.py` (invoked by the command via Bash).
Each emits JSON findings to stdout; failures degrade to "check skipped", never crash.

| Check | Why |
|---|---|
| **Hallucinated dependency** | Every new import in the diff verified against PyPI/npm registry. Slopsquatting is a supply-chain vector. **Treated as an error, not a warning** |
| **Repo's own linters + type checker** | Free, already configured, high precision — run the free thing first |
| **Dead code** | Unreachable branches, unused imports/exports added by the diff |
| **Test subversion** | Tests changed in the same diff as covered code, in a *weakening* direction — assertions removed, `skip`/`xfail` added, expected values loosened |

## 8. Tier 1 — review subagents (plugin `agents/`)

Four subagents fanned out in parallel via the Task tool, each with the shared output
contract. The review command orchestrates: pre-flight → fan-out → synthesis.

| Subagent | Question | Inputs |
|---|---|---|
| **intent-scope** | Built what the plan said? What was added unasked? | plan + diff + session digest |
| **slop-redundancy** | More code than the task needs? One-caller abstractions? One-case config? Default-fill placeholders? Reimplements something already in the repo? | diff + Grep/Read drill-down + churn history |
| **regression-contract** | Signature changes with un-updated callers? Behaviour drift at boundaries? | diff + callers via grep + tests covering changed files |
| **test-integrity** | New paths covered? Existing tests weakened? | diff + Tier 0 test-subversion output |

## 9. Output contract and noise control

Structured markdown with a JSON findings block, identical shape every run:

```
summary: str                    # what was built, and which plan-link it was checked against
findings: list[Finding]
  - source (tier0|subagent), severity, file, line_range
  - claim: str
  - evidence: list[str]         # tool/script outputs — never model assertion alone
  - suggested_action: str       # concrete; never "consider refactoring"
verdict: pass | review_needed | blocked
not_checked: list[str]          # mandatory — what this review did NOT verify
```

- **Cross-agent dedup** — one line flagged by three agents becomes one finding.
- **Finding budget** — hard cap, severity-ranked; the tail is summarised, not listed.
- **Evidence mandatory** — a finding with no tool-derived evidence is *dropped*, not
  softened.
- **No vague findings** — file, line, concrete action required.
- **Guard false confidence** — `not_checked` is mandatory so a green verdict doesn't
  license skipping human review.

## 10. Evaluation (lightweight, proportionate)

No benchmark harness, no LLM-judge infrastructure. Just:

- **Fixture repos** under `evals/`: small repos with planted issues — one hallucinated
  import, one weakened test, one unrequested feature, one one-caller abstraction.
- **Run log** `evals/results.md`: per fixture, which findings fired (TP/FP/FN), tracked
  across prompt edits. Precision and false-positive rate computed by hand — the set is
  small on purpose.
- **Gate:** no prompt change ships unless it doesn't regress the fixture results.
- **Dogfooding:** run on this repo's own real branches before calling it done.

## 11. Stack

| Concern | Choice |
|---|---|
| Distribution | Claude Code plugin (`.claude-plugin/plugin.json` + `commands/` + `agents/` + `scripts/`) |
| Helper scripts | Python 3.11, `uv run` with inline deps (`# /// script`) — no install step |
| Git | subprocess to `git`; `gh` CLI for PR data |
| AST | stdlib `ast` (tree-sitter only if multi-language proves necessary) |
| Search | host's Grep/Glob; ripgrep via host Bash |
| Optional ranker | `rank_bm25` — imported only in `scripts/bm25_rank.py`, only if §6.3 triggers |
| Eval | pytest for scripts + fixture repos |

Explicitly **not** in the stack: embeddings, vector stores, chunking, LiteLLM, LangGraph,
DuckDB, Streamlit/FastAPI, Docker, Langfuse/Grafana, CI actions.

## 12. Schedule (~5 days)

**Day 1 — Skeleton + context sources.** Plugin manifest, `/driftguard:review` command,
plan/spec resolution chain, `gh` + git context scripts, session extractor v1
(slug match + time window + file overlap → digest).

**Day 2 — Tier 0.** All four pre-flight scripts emitting the JSON finding shape. Fixture
repos with planted hallucinated import + weakened test must fire deterministically.

**Day 3 — Tier 1 + synthesis.** Four subagent definitions, shared prompt contract, dedup +
finding budget + evidence filter, `not_checked` enforcement.

**Day 4 — Eval + hardening.** Full fixture matrix, results log, prompt iteration against
the gate. Decide yes/no on the BM25 ranker based on evidence, not speculation.

**Day 5 — Polish.** Dogfood on real branches, README (problem, prior-art table with the
honest caveat, install instructions), marketplace-ready packaging.

**Cut order if behind:** regression-contract subagent → BM25 (already optional) → eval
fixtures beyond the two Tier-0 ones. **Never cut the session extractor** — it's the whole
point.

## 13. Repository layout

```
driftguard/
├── .claude-plugin/plugin.json    # manifest
├── README.md                     # incl. prior-art table + honest caveat
├── commands/
│   └── review.md                 # /driftguard:review orchestration
├── agents/                       # Tier 1 subagents
│   ├── intent-scope.md
│   ├── slop-redundancy.md
│   ├── regression-contract.md
│   └── test-integrity.md
├── scripts/
│   ├── plan_resolve.py           # source 1: resolution chain
│   ├── session_extract.py        # source 2: transcript digest
│   ├── dev_history.py            # source 3: git/gh history
│   ├── bm25_rank.py              # optional, only if needed
│   └── preflight/
│       ├── deps_check.py
│       ├── run_linters.py
│       ├── dead_code.py
│       └── test_subversion.py
├── tests/                        # pytest for scripts
└── evals/                        # fixture repos + results.md
```

## 14. Definition of done

1. `/driftguard:review <pr>` inside Claude Code produces an evidence-backed review checked
   against plan/spec + session digest + dev history.
2. `--base main` works on a local branch with no PR and no `gh`.
3. Tier 0 catches the planted hallucinated import and planted weakened test
   deterministically.
4. The session extractor correctly matches transcript → branch on at least this repo's own
   history, and the digest stays within its size budget.
5. Fixture results logged with precision / FP rate; the evidence filter demonstrably drops
   an evidence-free finding.
6. Zero infrastructure: no server, no embeddings, no Docker, no keys. Installs as a plugin
   from the repo.

## 15. Out of scope (documented with upgrade triggers)

- **Standalone/headless mode (CI, GitHub Action)** → requires own LLM key + a harness
  abstraction; revisit only if team/CI demand appears.
- **Other host agents** (Codex, OpenCode, Aider) → session extractor is Claude-transcript
  specific; new hosts need new extractors.
- **Vector retrieval / benchmark** → revive only with numbers showing pre-filtering +
  subagent isolation + BM25 insufficient on real reviews.
- **Multi-repo, IDE plugins, monitoring stack, web UI, feedback learning loop.**

Writing these as reasoned decisions with explicit triggers is deliberate — it reads as
judgment, not omission.

## 16. Notes for the implementer

- The **session extractor is the moat** — get branch matching right before anything else.
- Cheapest filter first: Tier 0 runs before any subagent, on every review.
- Findings without tool-derived evidence get **dropped**, not softened.
- Every optional piece (BM25, tree-sitter) degrades or is absent; nothing optional becomes
  a hard dependency.
- Prefer boring deterministic scripts wherever a script can answer the question. The
  model's job is synthesis, not fact retrieval.
