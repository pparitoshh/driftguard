## driftguard review — ai-chef d2fa888...286ce95

**Checked against:** plan-doc (`PLAN.md`) + Claude session digest (2 sessions matched, primary score 161)
**Verdict:** pass

### Summary

The requested change is delivered and only that: `pipeline/ingest.py` is now a real
Prefect flow (3 `@task` + 1 `@flow`, values chained, `__main__` wired) in ~26 changed
lines — satisfying the session request *"let do with prefect it less than 50 lines of
code?"* (session c7b33d2d, 08-09 10:46). `prefect>=3.0.0` added to pyproject, uv.lock
churn is mechanical. Tier 0 found nothing (no hallucinated deps, no dead code, no test
weakening; ruff not configured in repo). One documentation-drift warning below.

### Findings

1. **[warning]** `PLAN.md:54,129,206-207,241` — PLAN.md, the self-declared *"single source
   of truth … update it as decisions change"*, was left stale: it still lists the Prefect
   flow as optional/future ("Simple first, automate later", Phase-11 milestone) while this
   diff implements it and the README roadmap was moved to "Done".
   - Evidence: plan.json (PLAN.md header text); PLAN.md rows 54/129/206-207/241; diff.patch
     contains no PLAN.md hunk while README hunk @@ -302,8 +302,8 moves "Prefect ingestion
     flow" into "Done"; session.md: the only doc asks in range were README dataset/video notes.
   - Action: update the four PLAN.md rows to record the decision (made 2026-08-09 per user
     request) and drop "Prefect ingestion flow" from the Phase-11 milestone.

2. **[info]** `pipeline/ingest.py:111-124` — `load_to_postgres` gained `-> int`
   (row count) that its only caller discards.
   - Evidence: grep — `load_to_postgres` occurs exactly twice (def at :90, bare-statement
     call at :124); diff adds `count = cur.fetchone()[0]` / `return count`.
   - Action: drop the return (count is already printed) or return it from `ingest_flow`.

3. **[info]** `pipeline/ingest.py:65-69` — `@task(retries=2, retry_delay_seconds=5)` on
   `load_data` retries a local `pd.read_csv` of a repo data file — config that protects
   against ~nothing (plausibly copied from `load_to_postgres`, where retries make sense).
   - Evidence: `DATA_PATH` is a local path (ingest.py:23); identical decorator on both tasks.
   - Action: plain `@task` on `load_data`; keep retry args only on `load_to_postgres`.

4. **[info]** `pipeline/precompute_embeddings.py:28-30` — `build_embedding_text` is
   copy-paste duplicated between `ingest.py` and `precompute_embeddings.py` (linked only
   by a comment). Not changed by this diff, but flagged at the boundary: a future edit to
   either copy silently drifts the in-memory backend from the pgvector one.
   - Evidence: grep — `build_embedding_text` matches only ingest.py:60-62 and
     precompute_embeddings.py:28-37; precompute_embeddings.py:3 comment admits the duplication.
   - Action: import it from one place (or extract a shared module).

5. **[info]** `pipeline/ingest.py:65-124` — new Prefect symbols (`load_data`,
   `embed_recipes`, `load_to_postgres`, `ingest_flow`) with retry semantics have no test
   referencing them. Not a regression (ingest.py had zero coverage before).
   - Evidence: grep — no test file references any of the new symbols; diff touches no test
     files; t0_test_subversion.json: 0 findings, confirmed by re-reading the diff.
   - Action: optional — a lightweight test calling `load_data.fn()` on a fixture CSV.

### Not checked

- **Out-of-repo callers** of the removed `pipeline.ingest.main()` (cron, CI, external
  notebooks) — nothing in-repo imports `pipeline.ingest`, but external automation can't
  be seen from here.
- **ruff/lint signal** — repo has no ruff config; check skipped.
- **Runtime behaviour of the Prefect flow** (retries, DB load) — static review only; no
  live Postgres/model infra exercised.
- **~11 assistant excerpts truncated** from the session digest at the 8KB budget — minor
  assistant-stated details unchecked.
- **uv.lock** — treated as lockfile noise, not reviewed line-by-line.

---

*Run metadata: 2026-08-14 · host: opencode (subagent fan-out mirrors plugin agents/) ·
artifacts: ai-chef/.driftguard/runs/2026-08-14-test/ · Tier 0: 0 findings · Tier 1 raw:
7 findings → 5 after dedup/merge (README doc-sync info merged into #1; regression
"no stale in-repo caller" confirmation moved to Not checked)*
