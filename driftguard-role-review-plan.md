# driftguard — Role-Based Review Plan (v0.3)

> Add **role-based review**: before reviewing, driftguard asks what kind of feature the
> PR is — **data scientist**, **data engineer**, or **other** — and attaches a
> role-specific specialist reviewer with a domain checklist. v0.3 ships the
> **data-scientist role only**, with the check catalog weighted toward the
> **ranking / personalization** domain. Data-engineer is specced (§10) but not built;
> "other" is exactly today's generic review.
>
> Sources mined for the catalog: the huggingface/ml-intern repo (`REVIEW.md` review
> rigor rules; `agent/prompts/system_prompt_v3.yaml` ML-engineering failure modes:
> data audit, silent dataset substitution, lost artifacts, unlogged metrics) plus
> standard ranking/recsys review practice (leakage, position bias, sampled softmax,
> per-user vs pooled metrics).

---

## 1. Why roles

The five existing specialists (intent-scope, slop-redundancy, regression-contract,
test-integrity, security) are domain-neutral. A DS feature PR fails in ways none of
them can see: the code is *correct*, the plan *was* followed, the tests pass — and
the NDCG is fake because the same user is on both sides of the split. These are
**methodology bugs**, not code bugs. Catching them needs a checklist a generalist
reviewer never walks.

Role-based review keeps the current architecture intact: a role simply **adds** one
Tier 0 script and one Tier 1 subagent to the existing pipeline, plus a label in the
output. Intent review stays primary and orthogonal — a DS PR is still checked for
intent drift, and a non-DS PR never pays the DS checklist cost.

## 2. UX: role selection flow

Role is resolved once per run, in `commands/review.md`, as a new **step 0.5** between
argument parsing (step 0) and context assembly (step 1):

1. `--role ds|de|other` flag → use it, no question asked. (`ds` aliases:
   `data-scientist`, `ml`.)
2. No flag → **AskUserQuestion** ("What kind of feature is this PR?":
   *Data scientist / Data engineer / Other*), preceded by a one-line auto-hint when
   heuristics fire, so the user can confirm with one keystroke:

   | Heuristic signal (from `git diff --name-only` + manifests) | Hint |
   |---|---|
   | changed paths match `*.ipynb`, `train*.py`, `features/**`, `models/**`, `ranking/**`, `recsys/**`, `notebooks/**` | "looks data-scientist" |
   | manifest adds `torch`, `tensorflow`, `sklearn`, `xgboost`, `lightgbm`, `recbole`, `faiss` | "looks data-scientist" |
   | changed paths match `pipelines/**`, `dag*/**`, `dbt/**`, `sql/**` | "looks data-engineer" |
   | nothing fires | no hint |

3. v0.3 behavior: `de` → print "data-engineer role not built yet (v0.4), continuing
   as *other*" and run the generic review. `other` → today's flow, byte-for-byte.

Plumbing notes:
- Add `AskUserQuestion` to `allowed-tools` in `commands/review.md` frontmatter.
- Record the resolved role as `$RUN_DIR/role.txt`; subagents and the synthesizer read
  it from there (single source of truth, no re-asking).
- Role is per-run. Persistence ("this repo is always DS") is deferred to v0.4 as a
  `role: data-scientist` line in `.driftguard/rules.md` — not built now.

## 3. Architecture: what a role *is* (minimal model)

A role = **{ an optional Tier 0 script, a Tier 1 subagent, an output label }**.
No new directories, no plugin-manifest changes beyond registering one agent file.

| Piece | v0.3 (data-scientist) | Reuses |
|---|---|---|
| Tier 0 script | `scripts/preflight/ml_patterns.py` — AST/regex, stdlib-only, runs only when `role=ds` | pattern of `dead_code.py` |
| Tier 1 subagent | `agents/ds-review.md` — checklist-driven, same JSON output contract as `security.md` | subagent fan-out, dedup, budget |
| Orchestration edits | `commands/review.md`: step 0.5 + conditional Tier 0/Tier 1 lines + output additions | — |
| Output label | `**Role:** data-scientist` + "ML checks walked" coverage list | output contract §5 |

Everything downstream — dedup, evidence filter, vagueness filter, rules filter,
10-finding budget, "Not checked" — applies to DS findings unchanged. DS findings
carry `category: ml-*` so they stay distinguishable and individually addressable
("fix ml-leakage findings").

## 4. Data-scientist check catalog (the core of this plan)

Stable IDs (DS-xx) so `.driftguard/rules.md` negative rules can suppress a specific
check ("never flag DS-18 here — we use position as a feature deliberately").
**Route**: T0 = deterministic (`ml_patterns.py`), T1 = subagent judgment.
**Severity**: `error` = result is scientifically invalid, blocks merge · `warning` =
methodological risk, needs written justification · `info` = hygiene.

### 4.1 Split integrity (train / val / test)

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-01 | **Group leakage** — interaction/impression data split randomly instead of by user/session; same entity on both sides | T1 | error |
| DS-02 | **Non-temporal split** of time-ordered events — future behavior leaks into features/labels; expect time-based or `TimeSeriesSplit` | T1 (+T0 hint) | error |
| DS-03 | **Preprocessing fitted before split** — scaler, vocab, ID→index maps, popularity stats, embedding tables fitted on full data | T0 + T1 | error |
| DS-04 | **Duplicate / near-duplicate samples across splits** — same impression id, same user-item within overlapping windows | T1 | warning |
| DS-05 | **Test set reused for selection** — early stopping / model picking on test, or val reported *as* test after repeated peeking | T1 | error |
| DS-06 | Split ratios and split logic not documented anywhere in the diff/README | T1 | info |

### 4.2 Data leakage

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-07 | **Target leakage** — feature derived from the label or from post-event signals (dwell, scroll depth, post-click actions when predicting the click) | T1 | error |
| DS-08 | **Temporal leakage in feature joins** — aggregations over windows that include events ≥ prediction time; missing point-in-time / as-of semantics | T1 | error |
| DS-09 | Target/mean encoding computed on the full dataset without an out-of-fold scheme | T1 | warning |
| DS-10 | **Training-only features** — columns available in the warehouse that will not exist at serving time | T1 | warning |
| DS-11 | Raw high-cardinality IDs passed as features with no hashing/embedding/cap (memorization masquerading as signal) | T1 | info |
| DS-12 | `.fit()`/`.fit_transform()` called on a variable named `*test*`/`*val*` (name-based heuristic) | T0 | warning |
| DS-13 | Label column literally present in the feature-column list | T0 | error |

### 4.3 Features & training/serving skew

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-14 | Same feature computed by **two different code paths** (train pipeline vs inference path) with no shared implementation | T1 | warning |
| DS-15 | No train-vs-recent **distribution comparison** for key features (shift unexamined) | T1 | info |
| DS-16 | Null/default/imputation handling differs between train pipeline and inference path | T1 | warning |

### 4.4 Labels & feedback loops — ranking/personalization

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-17 | **Label window undefined or violated** — delayed conversions (install/purchase) censored or misattributed; window must close before training-data end | T1 | error |
| DS-18 | **Position bias unaddressed** — click model trained on logged impressions with no position feature, IPS/propensity weighting, or randomized bucket | T1 | warning |
| DS-19 | **Selection/exposure bias ignored** — MNAR implicit feedback treated as a random sample; items never shown contribute no signal and this is unacknowledged | T1 | warning |
| DS-20 | **Feedback-loop blindness** — training data generated by the previous model version with no exploration traffic (self-reinforcing popularity) | T1 | info |
| DS-21 | **Label definition mismatch** between train and eval (e.g. train: any click; eval: click + dwell > 30s) | T1 | error |

### 4.5 Negative sampling & loss — ranking/personalization

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-22 | **In-batch / sampled negatives without log-q (sampled-softmax) correction** — popular items over-penalized | T1 | warning |
| DS-23 | **Loss family mismatched to headline metric** — pointwise BCE optimized while reporting NDCG@10, with no justification | T1 | warning |
| DS-24 | Negative-sampling strategy undocumented (random vs hard mix, number of negatives, per-epoch resampling) | T1 | info |
| DS-25 | Sampling weighting claimed in the plan (impression-weighted, position-weighted) not implemented as described | T1 | warning |

### 4.6 Evaluation protocol & metrics — ranking/personalization

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-26 | **Inappropriate headline metric** — plain accuracy/AUC for heavily imbalanced ranking; no @K metric (NDCG/MAP/MRR/Recall@K) | T1 | warning |
| DS-27 | **Pooled vs per-user metrics conflated** — pooled numbers dominated by heavy users; per-user average absent when claims are per-user | T1 | warning |
| DS-28 | **Candidate-generation eval conflated with re-ranking eval** — recall-over-corpus and NDCG-over-shown-items reported interchangeably | T1 | error |
| DS-29 | **No baselines** — popularity baseline and current-production-model comparison on the *same split* both missing | T1 | warning |
| DS-30 | **Cold-start slice not reported** — no separate numbers for new users / new items | T1 | info |
| DS-31 | **Calibration unexamined** while scores feed downstream arithmetic (ads pCTR × bid, blending weights) | T1 | info |
| DS-32 | **Recommendation distribution unmeasured** — catalog coverage / popularity-bias (Gini, entropy) of what the model actually surfaces | T1 | info |
| DS-33 | **Hyperparameter/model tuning on the test set** — test metric consulted repeatedly for selection decisions | T1 | error |
| DS-34 | Evaluation computed on training data (metric call over `*train*` variables) | T0 | error |
| DS-35 | Reported metric not reproducible from the diff — number asserted in PR body with no eval code path producing it | T1 | warning |

### 4.7 Experiment hygiene & reproducibility

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-36 | **No seed anywhere** — randomness used (`train_test_split`, `random`, `np.random`, `torch` sampling) with no `seed`/`random_state`/`manual_seed` | T0 | info |
| DS-37 | `train_test_split` without `random_state` (unreproducible split specifically) | T0 | warning |
| DS-38 | **Data snapshot not pinned** — training query/table/date-range unrecorded; run not reproducible to the same rows | T1 | warning |
| DS-39 | **Config + metrics not logged to a tracker** (MLflow/W&B/Trackio/params file) — printed only, lost after the run *(ml-intern)* | T1 | warning |
| DS-40 | **Silent dataset substitution** — session transcript shows the requested dataset/version was swapped without telling the user *(ml-intern; uses session digest — driftguard's unique source)* | T1 | error |
| DS-41 | **Model artifact not persisted/versioned** — training ends without saving/registering the artifact *(ml-intern "LOST MODELS")* | T1 | warning |
| DS-42 | **Hand-tuned hyperparameter thrash** — session shows repeated ad-hoc single edits instead of a scripted sweep *(ml-intern; session transcript)* | T1 | info |

### 4.8 Data audit & training-code robustness *(mostly ml-intern-derived)*

| ID | Check | Route | Severity |
|---|---|---|---|
| DS-43 | **No data audit before training** — schema, nulls, duplicates, class imbalance, label distribution never inspected *(ml-intern "Data audit")* | T1 | warning |
| DS-44 | Missing **fail-fast asserts** — required columns exist, splits non-empty, label in expected range *(ml-intern)* | T1 | info |
| DS-45 | Core ML dep versions neither pinned nor printed at startup *(ml-intern)* | T1 | info |
| DS-46 | No **smoke-path sanity** (overfit-one-batch / single-step run) before the full training entrypoint *(ml-intern)* | T1 | info |
| DS-47 | No NaN/Inf guard in loss or feature transforms; deep model without gradient clipping | T1 | info |
| DS-48 | Long training run with no checkpoint/resume path | T1 | info |

**Totals:** 48 checks — 7 fully deterministic (T0), the rest T1. Ranking/personalization
weight: §4.4–§4.6 (18 checks) are ranking-specific; §4.1–§4.2 apply to all supervised
DS features with ranking-flavored defaults.

## 5. Tier 0 — `scripts/preflight/ml_patterns.py`

Stdlib-only AST walker over changed `.py` files (notebooks skipped in v0.3 — listed in
"Not checked"). Precision over recall: every rule must cite the exact line; ambiguous
cases go to T1, never reported by T0.

| Rule | Detects | Feeds |
|---|---|---|
| T0-1 | `fit_transform(X…)` / `.fit(X…)` where `train_test_split(X…)` occurs **later in the same scope** | DS-03 |
| T0-2 | `train_test_split(...)` without `random_state=` | DS-37 |
| T0-3 | `.fit(` / `.fit_transform(` / `.partial_fit(` on variable matching `(x_|X_)?(test|val|valid|eval)\b` | DS-12 |
| T0-4 | metric call (`accuracy_score`, `roc_auc_score`, `average_precision_score`, `ndcg_score`, `log_loss`, `f1_score`, …) whose first arg matches `*train*` | DS-34 |
| T0-5 | `shuffle=True` in a split/CV call **and** timestamp-like tokens (`timestamp`, `event_time`, `impression_time`, `\bts\b`, `date`) in the same file | DS-02 hint |
| T0-6 | `GridSearchCV`/`RandomizedSearchCV`/`Halving*`/`BayesSearchCV`/`cross_val_score` fitted on the same variable that is later split (tuning on full data); plus optuna `study.optimize(...)` / hyperopt `fmin(...)` running before a later split in the same scope (the data is captured by the objective closure, so there is no variable to match) | DS-33 |
| T0-7 | simple assignment analysis: `LABEL = "clicked"` and `"clicked"` inside the `FEATURES`/`feature_cols` literal | DS-13 |
| T0-8 | file uses randomness (`random.`, `np.random`, `torch`, `.sample(`, `train_test_split`) but never sets a seed | DS-36 |

Output: same JSON shape as the other preflight scripts
(`{"check": "ml_patterns", "findings": [...], "skipped": [...]}`), wired into
`commands/review.md` step 2 as `t0_ml.json`, **run only when `role=ds`**.

## 6. Tier 1 — `agents/ds-review.md`

Modeled on `security.md` (same frontmatter, same JSON output contract). Distinct rules:

- **Inputs**: the standard run-dir artifacts **plus** `t0_ml.json` (confirm/refute each
  T0 hit, as the security agent does with secrets).
- **Trace the data flow, not just the diff**: for every leakage/split finding, open the
  feature-pipeline code and cite the computation chain `file:line` per hop
  (ml-intern's verification bar — "this breaks X" without a line reference is dropped).
- **Session-transcript checks** (DS-40, DS-42, and peeking-at-test evidence for
  DS-05/DS-33) read `$RUN_DIR/session.md` — the source no hosted reviewer has.
- Categories: `ml-split`, `ml-leakage`, `ml-skew`, `ml-label`, `ml-sampling`,
  `ml-eval`, `ml-hygiene`, `ml-data`.
- Budget: max **8** findings (ranking checklists are long; cap forces triage).
- Explicit non-goals in the agent file: no style, no security, no intent — other
  specialists own those; pre-existing methodology debt outside the diff goes to
  `notes`, not findings.

## 7. Orchestration & output-contract changes (`commands/review.md`)

1. **Frontmatter**: add `AskUserQuestion` to `allowed-tools`; argument hint gains
   `[--role ds|de|other]`.
2. **Step 0.5** (new): role resolution per §2; write `$RUN_DIR/role.txt`.
3. **Step 2**: add conditional line —
   `role=ds → python3 $PLUGIN_SCRIPTS/preflight/ml_patterns.py … > $RUN_DIR/t0_ml.json`.
4. **Step 3**: fan-out list gains, when `role=ds` —
   `driftguard:ds-review — methodology: splits, leakage, ranking eval, hygiene (fuses with t0_ml.json)`.
5. **Step 5 output contract** gains two lines and one section:
   ```
   **Role:** data-scientist
   ```
   and, between Findings and Not checked, a coverage list (ml-intern's
   "What I checked", adapted):
   ```
   ### ML checks walked
   - Split integrity (DS-01–06): clean
   - Data leakage (DS-07–13): 1 finding
   - …
   ```
   Every group is listed, clean or not — a green DS verdict must show the checklist
   was actually walked, same philosophy as "Not checked".

## 8. "Not checked" additions (mandatory when role=ds)

- Raw training data not inspected — no warehouse/feature-store access from the diff.
- Training/serving feature parity asserted from code, not verified against the store.
- Notebook cell *outputs* not trusted (stale outputs can show any metric).
- Offline metrics only — online A/B design and guardrail metrics are out of scope.
- `de` role requested → note that it ran as *other*.

## 9. Fixtures & tests

Extend `evals/build_fixtures.py` with a `ds/` scenario set (planted, labeled issues):

| # | Fixture plants | Expected catch |
|---|---|---|
| 1 | `fit_transform(df)` before `train_test_split(df)` | T0-1 / DS-03 |
| 2 | random split of synthetic click log, same users both sides | DS-01 |
| 3 | feature `avg_dwell_7d` window ends *after* prediction timestamp | DS-08 |
| 4 | `roc_auc_score(y_train, model.predict(X_train))` reported as result | T0-4 / DS-34 |
| 5 | `GridSearchCV.fit(X, y)` on pre-split data | T0-6 / DS-33 |
| 6 | `FEATURES = [..., "clicked"]` with `LABEL = "clicked"` | T0-7 / DS-13 |
| 7 | two-tower retrieval training, in-batch negatives, no log-q correction | DS-22 |
| 8 | NDCG claimed in PR body; no eval code path in diff | DS-35 |
| 9 | randomness everywhere, zero seeds | T0-8 / DS-36 |

Unit tests: `tests/test_ml_patterns.py` — one test per T0 rule (positive + negative
case), mirroring the existing `unittest` style; keeps the "36 tests" counter honest
(→ ~44).

## 10. Phasing & the data-engineer role (specced, not built)

- **v0.3 (this plan)**: role selection + data-scientist role end-to-end, fixtures, tests.
- **v0.4**: `data-engineer` role pack — `agents/de-review.md` + optionally
  `preflight/de_patterns.py`. Check themes to detail when built: pipeline
  **idempotency/rerunnability**, backfill semantics, **schema evolution** &
  compatibility, partition & **late-data** handling, **data-quality gates**
  (null/uniqueness/volume asserts), exactly-once vs duplicate semantics, **skew/hot
  keys**, full-scan cost, **PII in new columns**, contract with downstream consumers.
  Also in v0.4: `role:` persistence line in `.driftguard/rules.md`.
- **v0.5+**: `.ipynb`-aware diffing (cell-level, output-stripping), role-aware learnings
  filters, team-shared role packs.

## 11. Out of scope for v0.3

Executing training or data profiling (no data access, ever — review is static);
automatic role detection *without* user confirmation; the data-engineer role itself;
online A/B design review; framework-specific deep checks (TF feature columns, PyTorch
`DataLoader` worker seeding beyond DS-36); image/NLP/LLM-fine-tuning-specific eval
suites (ranking/personalization is the v0.3 domain weight; generic supervised checks
still apply to the rest).
