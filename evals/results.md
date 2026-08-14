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

Tier 0 determinism: **4/4 deterministic planted issues caught, 0 false positives** on the
clean base commit range. Subagent-detected issues (5, 6) are exercised in live runs below.

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
