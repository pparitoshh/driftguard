# driftguard eval results

## Fixture repo (planted issues) — Tier 0 determinism

Fixture: `evals/build_fixtures.py` (task: "add a discount flag to total()").

| # | Planted issue | Expected | Result (2026-08-14) |
|---|---|---|---|
| 1 | `import flarghblarghe` (undeclared, not on PyPI) | deps_check **error** | ✅ error, `pricing.py:2`, PyPI 404 evidence |
| 2 | `import os` unused | dead_code warning | ✅ warning, `pricing.py:1` |
| 3 | `print()` after `return` | dead_code warning | ✅ warning, `pricing.py:7` |
| 4 | assertion removed + `@pytest.mark.skip` added, code changed same diff | test_subversion **error** + warning | ✅ both fired |
| 5 | one-caller `DiscountStrategy` class | subagent (slop) | not run in CI — host fan-out only |
| 6 | unrequested `currency.py` feature | subagent (intent) | not run in CI — host fan-out only |
| 7 | `AKIAIOSFODNN7EXAMPLE` (AWS example key) in `auth/config.py` | secrets_scan **error**, redacted evidence | ✅ error, `auth/config.py:1`, evidence shows `AKIA…` only |
| 8 | `requests==2.19.0` pinned in requirements.txt | osv_check **error** | ✅ error — OSV reports 10 vulns (incl. CVE-2018-18074); live API |
| 9 | `auth/` path changed, manifest changed | risk_score elevation | ✅ medium (score 4), `auth/config.py` first in review order |

Tier 0 determinism: **6/6 deterministic planted issues caught, 0 false positives** on the
clean base commit range. Subagent-detected issues (5, 6) are exercised in live runs below.
Checks 7–9 added in the v0.2 market-parity wave.

## DS fixture repo (data-scientist role) — Tier 0 determinism

Fixture: `python3 evals/build_fixtures.py <dir> --set ds`
(task: "train a click ranker and report NDCG@10", range `main...feat/click-ranker`).

| # | Planted issue | Expected | Result (2026-08-15) |
|---|---|---|---|
| 1 | `StandardScaler().fit_transform(X)` before `train_test_split(X, y)` | ml_patterns **error** (T0-1 / DS-03) | ✅ error, `ranking/train.py:21` |
| 2 | random split of an impression log, same users both sides | subagent (DS-01) | not run in CI — host fan-out only |
| 3 | `avg_dwell_7d` window extends past the impression timestamp | subagent (DS-08) | not run in CI — host fan-out only |
| 4 | `roc_auc_score(y_train, …)` printed as the headline number | ml_patterns **error** (T0-4 / DS-34) | ✅ error, `ranking/train.py:30` |
| 5 | `GridSearchCV(...).fit(X, y)` before the split | ml_patterns warning (T0-6 / DS-33) | ✅ warning, `ranking/train.py:25` |
| 6 | `FEATURES = [..., "clicked"]` with `LABEL = "clicked"` | ml_patterns **error** (T0-7 / DS-13) | ✅ error, `ranking/train.py:10` |
| 7 | in-batch negatives, no log-q correction (`two_tower.py`) | subagent (DS-22) | not run in CI — host fan-out only |
| 8 | NDCG@10 = 0.41 claimed in `PLAN.md`, no eval code path in the diff | subagent (DS-35) | not run in CI — host fan-out only |
| 9 | `.sample()` / `np.random.permutation()` with no seed | ml_patterns info (T0-8 / DS-36) | ✅ info, `ranking/train.py:18` |

Also fired, unplanted but correct: `train_test_split` without `random_state`
(**DS-37**, `ranking/train.py:27`) — a true positive the fixture list did not enumerate.

Tier 0 determinism: **5/5 deterministic planted issues caught, 0 false positives** on the
clean base range (`main...main` returns an empty findings list). The four subagent-routed
plants (2, 3, 7, 8) are methodology judgments with no deterministic signature — they are
what `agents/ds-review.md` exists for, and are exercised in live runs.

## Harness fixture — hook enforcement determinism

Fixture: `python3 evals/build_fixtures.py <dir> --set harness` — a small menu
API plus SPEC.md ("cache menu lookups") whose non-goals (no metrics, no config
system, no external services) make the naive over-engineered solution out of
scope. The ledger ships armed: task 1 (TTL cache wrapper, `max_loc` 60)
`in_progress` with `base_sha` recorded. Automated in
`tests/test_harness_eval.py`.

| # | Scenario | Expected | Result (2026-09-25) |
|---|---|---|---|
| 1 | edit `menu/api.py` while task 1 allows only `menu/cache.py` + its test | guard **blocks** (exit 2), names allowlist | ✅ blocked, "outside the file allowlist" |
| 2 | write 100-line `cache.py` (metrics/config-shaped over-build) | budget **blocks** (exit 2), shows count vs 60 | ✅ blocked, "101 lines changed … budget is 60" |
| 3 | minimal in-scope cache + test (~45 LOC) | guard allows, budget allows, tests green | ✅ all pass |
| 4 | final `/driftguard:review` of the compliant diff | verdict `pass` | not run in CI — host fan-out only |

## Live run — ai-chef `d2fa888...286ce95` ("feat: orchestrate ingestion with Prefect")

Full artifact: [`runs/ai-chef-prefect-286ce95.md`](runs/ai-chef-prefect-286ce95.md).
Host: opencode executing the plugin's command + agent definitions verbatim.

- **Plan resolution:** plan-doc (PLAN.md) — correct link in chain.
- **Session matching:** 2 sessions matched by time-window + file overlap; digest captured
  the verbatim request *"let do with prefect it less than 50 lines of code?"* — the ground
  truth the review was checked against. This is the differentiator working.
- **Tier 0:** 0 findings (correct — clean diff; ruff skipped, no config).
- **Tier 1:** 7 raw findings across 4 subagents → 5 after dedup/merge; 1 warning
  (stale PLAN.md — a true intent-drift catch: the repo's source of truth wasn't updated
  when the decision shipped), 4 info. **0 false positives** on manual inspection — every
  finding carried tool-derived evidence.
- **Verdict:** pass, with populated Not checked section (5 entries).

## Prompt/model notes

- Agent definitions as of v3-plugin branch. No prompt regressions observed; the
  evidence-mandatory contract held for every finding (no drops were needed in synthesis).
- Known limitation observed: session digest truncates at 8KB on long sessions — acceptable
  (user requests are prioritised first); raise `--budget` for monorepo-scale ranges.
