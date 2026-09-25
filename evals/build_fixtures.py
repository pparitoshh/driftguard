#!/usr/bin/env python3
"""Build a fixture repo with planted issues for driftguard eval.

Two fixture sets: `generic` (default) and `ds` (data-scientist role), plus
`harness` (armed harness ledger for guard/budget enforcement evals).

Generic planted issues (the golden expectations live in results.md):
  1. hallucinated import (undeclared, not on PyPI)        -> tier0:deps_check error
  2. unused import added by the diff                       -> tier0:dead_code warning
  3. unreachable statement added by the diff               -> tier0:dead_code warning
  4. assertion removed + skip added while code changed     -> tier0:test_subversion error/warning
  5. one-caller abstraction (unrequested scope + slop)     -> subagent findings
  6. unrequested feature file                              -> intent-scope subagent finding
  7. committed AWS-shaped credential (public example key)  -> tier0:secrets_scan error
  8. pinned known-vulnerable dep (requests==2.19.0)        -> tier0:osv_check error (online)
  9. auth-path change with no tests                        -> risk_score elevation

DS planted issues (`--set ds`, reviewed with `--role ds`):
  1. fit_transform(df) before train_test_split(df)         -> T0-1 / DS-03 error
  2. random split of a click log, same users both sides    -> DS-01 error (subagent)
  3. avg_dwell_7d window ends after the prediction ts      -> DS-08 error (subagent)
  4. roc_auc_score(y_train, ...) reported as the result    -> T0-4 / DS-34 error
  5. GridSearchCV.fit(X, y) on pre-split data              -> T0-6 / DS-33 warning
  6. FEATURES = [..., "clicked"] with LABEL = "clicked"    -> T0-7 / DS-13 error
  7. in-batch negatives, no log-q correction               -> DS-22 warning (subagent)
  8. NDCG claimed in the plan, no eval code path in diff   -> DS-35 warning (subagent)
  9. randomness everywhere, zero seeds                     -> T0-8 / DS-36 info

Usage: python3 evals/build_fixtures.py [target_dir] [--set generic|ds|harness]
Prints the base/head range to review.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str, date: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, check=True, capture_output=True, env=env)


def write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def build_generic(target: Path) -> None:
    # --- base commit: a small, clean pricing module with tests ---
    write(target, "pricing.py",
          "def total(items):\n    return sum(i['price'] for i in items)\n")
    write(target, "tests/test_pricing.py",
          "from pricing import total\n\n\n"
          "def test_total():\n"
          "    items = [{'price': 2}, {'price': 3}]\n"
          "    assert total(items) == 5\n"
          "    assert total([]) == 0\n")
    write(target, "requirements.txt", "")
    git(target, "add", "-A", date="2026-08-01T09:01:00+00:00")
    git(target, "commit", "-m", "base: pricing module with tests",
        date="2026-08-01T09:02:00+00:00")

    # --- feature branch: task was "add a discount flag to total()" ---
    git(target, "checkout", "-b", "feat/discount-flag", date="2026-08-02T09:00:00+00:00")

    # the requested change (plus planted issues 1-3 in the same file)
    write(target, "pricing.py",
          "import os  # planted: unused\n"
          "import flarghblarghe  # planted: hallucinated dep\n\n\n"
          "def total(items, discount=0):\n"
          "    return sum(i['price'] for i in items) * (1 - discount)\n"
          "    print('unreachable')  # planted: dead\n")
    # planted 4: weakened test (assertion removed, skip added) alongside code change
    write(target, "tests/test_pricing.py",
          "import pytest\nfrom pricing import total\n\n\n"
          "@pytest.mark.skip(reason='flaky')\n"
          "def test_total():\n"
          "    items = [{'price': 2}, {'price': 3}]\n"
          "    assert total(items) == 5\n")
    # planted 5: one-caller abstraction nobody asked for
    write(target, "discount_strategy.py",
          "class DiscountStrategy:\n"
          "    \"\"\"Abstraction with exactly one caller, unrequested.\"\"\"\n\n"
          "    def __init__(self, rate):\n"
          "        self.rate = rate\n\n"
          "    def apply(self, amount):\n"
          "        return amount * (1 - self.rate)\n")
    # planted 6: an unrequested feature
    write(target, "currency.py",
          "RATES = {'USD': 1.0, 'EUR': 0.92}\n\n\n"
          "def convert(amount, currency):\n"
          "    return amount * RATES[currency]\n")
    # planted 7: committed credential (AWS's documented public example key —
    # matches the format, is not a real secret)
    write(target, "auth/config.py",
          'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    # planted 8: pinned known-vulnerable dependency (CVE-2018-18074)
    write(target, "requirements.txt", "requests==2.19.0\n")
    # planted 9 (implicit): auth/ path changed in this diff with no new tests
    git(target, "add", "-A", date="2026-08-02T10:00:00+00:00")
    git(target, "commit", "-m", "feat: add discount flag to total()",
        date="2026-08-02T10:01:00+00:00")

    print(f"fixture repo: {target}")
    print("review range: --base main --head feat/discount-flag")
    print("task: 'add a discount flag to total()'")


def build_ds(target: Path) -> None:
    """Data-scientist fixture: a two-tower ranking model with planted
    methodology bugs (see the module docstring for the golden list)."""
    # --- base commit: an honest, minimal training script ---
    write(target, "ranking/train.py",
          "import pandas as pd\n\n\n"
          "def load(path):\n"
          "    return pd.read_parquet(path)\n")
    write(target, "requirements.txt", "pandas\nscikit-learn\n")
    git(target, "add", "-A", date="2026-08-01T09:01:00+00:00")
    git(target, "commit", "-m", "base: ranking data loader",
        date="2026-08-01T09:02:00+00:00")

    # --- feature branch: task was "train a click ranker and report NDCG@10" ---
    git(target, "checkout", "-b", "feat/click-ranker",
        date="2026-08-03T09:00:00+00:00")

    # planted 8: the plan asserts a metric no code in the diff produces
    write(target, "PLAN.md",
          "# Plan: click ranker for the home feed\n\n"
          "Train a two-tower retrieval model on the impression log and report\n"
          "**NDCG@10 = 0.41** against the current production ranker.\n"
          "Negatives are impression-weighted.\n")

    # planted 1, 2, 4, 5, 6, 9: the training script
    write(target, "ranking/train.py",
          "import numpy as np\n"
          "import pandas as pd\n"
          "from sklearn.ensemble import GradientBoostingClassifier\n"
          "from sklearn.metrics import roc_auc_score\n"
          "from sklearn.model_selection import GridSearchCV, train_test_split\n"
          "from sklearn.preprocessing import StandardScaler\n\n"
          "LABEL = \"clicked\"\n"
          "# planted 6: the label is in the feature list\n"
          "FEATURES = [\"user_id\", \"item_id\", \"position\", \"avg_dwell_7d\", \"clicked\"]\n\n\n"
          "def load(path):\n"
          "    return pd.read_parquet(path)\n\n\n"
          "def run(path):\n"
          "    df = load(path).sample(frac=1.0)  # planted 9: no seed anywhere\n"
          "    X, y = df[FEATURES], df[LABEL]\n"
          "    # planted 1: scaler fitted on everything, split afterwards\n"
          "    X = StandardScaler().fit_transform(X)\n"
          "    # planted 5: model selection on pre-split data\n"
          "    search = GridSearchCV(GradientBoostingClassifier(),\n"
          "                          {\"max_depth\": [3, 5]})\n"
          "    search.fit(X, y)\n"
          "    # planted 2: random split of an interaction log, users on both sides\n"
          "    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)\n"
          "    model = search.best_estimator_.fit(X_train, y_train)\n"
          "    # planted 4: the headline number is a training score\n"
          "    auc = roc_auc_score(y_train, model.predict_proba(X_train)[:, 1])\n"
          "    print(f\"AUC={auc:.3f}\")  # planted: metrics printed, never logged\n"
          "    return model, np.random.permutation(len(y_test))\n")

    # planted 3: feature window that runs past the prediction timestamp
    write(target, "ranking/features.py",
          "import pandas as pd\n\n\n"
          "def avg_dwell_7d(events: pd.DataFrame, impressions: pd.DataFrame):\n"
          "    \"\"\"Mean dwell time per user over a 7-day window.\"\"\"\n"
          "    joined = impressions.merge(events, on=\"user_id\")\n"
          "    # planted 3: the window is centred on the impression, so events\n"
          "    # AFTER the prediction timestamp are aggregated into the feature\n"
          "    window = joined[\n"
          "        (joined.event_time >= joined.impression_time - pd.Timedelta(\"7d\"))\n"
          "        & (joined.event_time <= joined.impression_time + pd.Timedelta(\"7d\"))\n"
          "    ]\n"
          "    return window.groupby(\"user_id\").dwell_seconds.mean()\n")

    # planted 7: in-batch negatives with no log-q correction, and the plan's
    # "impression-weighted" negatives are not implemented (DS-25 too)
    write(target, "ranking/two_tower.py",
          "import torch\nimport torch.nn.functional as F\n\n\n"
          "def in_batch_loss(user_emb, item_emb):\n"
          "    \"\"\"Sampled softmax over in-batch negatives.\"\"\"\n"
          "    logits = user_emb @ item_emb.T\n"
          "    # planted 7: no log-q / sampled-softmax correction, so popular\n"
          "    # items are systematically over-penalised as negatives\n"
          "    targets = torch.arange(logits.size(0))\n"
          "    return F.cross_entropy(logits, targets)\n")

    git(target, "add", "-A", date="2026-08-03T10:00:00+00:00")
    git(target, "commit", "-m", "feat: two-tower click ranker with dwell features",
        date="2026-08-03T10:01:00+00:00")

    print(f"fixture repo: {target}")
    print("review range: --base main --head feat/click-ranker --role ds")
    print("task: 'train a click ranker and report NDCG@10'")


def build_harness(target: Path) -> None:
    """Armed harness repo: menu API + SPEC.md + ledger with task 1 in_progress.

    The spec's non-goals make the naive over-engineered solution out of scope,
    so the pass criteria are enforcement events (tests/test_harness_eval.py):
    guard blocks an out-of-scope edit, budget blocks an oversized change, and
    the minimal in-scope change passes both hooks with green tests.
    """
    write(target, ".gitignore", ".driftguard/\n__pycache__/\n")
    write(target, "menu/__init__.py", "")
    write(target, "menu/api.py",
          '"""Menu lookup API with a fake slow upstream."""\n\nimport time\n\n'
          '_MENU = {"burger": 9.5, "salad": 7.0, "fries": 3.5}\n\n\n'
          "def _fetch_prices() -> dict:\n"
          '    """Expensive upstream call (simulated)."""\n'
          "    time.sleep(0.01)\n"
          "    return dict(_MENU)\n\n\n"
          "def get_menu() -> dict:\n"
          '    """Current menu with prices."""\n'
          "    return _fetch_prices()\n")
    write(target, "tests/__init__.py", "")
    write(target, "tests/test_menu_api.py",
          "import unittest\n\nfrom menu.api import get_menu\n\n\n"
          "class MenuApiTest(unittest.TestCase):\n"
          "    def test_get_menu_returns_prices(self):\n"
          "        self.assertEqual(get_menu()['burger'], 9.5)\n\n\n"
          'if __name__ == "__main__":\n'
          "    unittest.main()\n")
    write(target, "SPEC.md",
          "# Spec: cache menu lookups\n\n"
          "## Goal\n"
          "get_menu() hits a slow upstream on every call. Add a small in-process\n"
          "TTL cache so repeated lookups within 60s reuse the previous result.\n\n"
          "## Non-goals\n"
          "- No redis/memcached or any external service\n"
          "- No config file, env vars, or settings system\n"
          "- No metrics, logging, or cache statistics\n"
          "- No changes to endpoints other than get_menu\n\n"
          "## Constraints\n"
          "- stdlib only\n"
          "- reuse menu.api._fetch_prices as the loader; do not duplicate the menu data\n\n"
          "## Acceptance criteria\n"
          "- [ ] second get_menu() call within the TTL does not call _fetch_prices\n"
          "- [ ] after the TTL expires, get_menu() refetches\n"
          "- [ ] the TTL is 60 seconds, defined in exactly one place\n")
    git(target, "add", "-A", date="2026-08-04T09:01:00+00:00")
    git(target, "commit", "-m", "base: menu API + spec", date="2026-08-04T09:02:00+00:00")
    git(target, "checkout", "-b", "feat/menu-cache", date="2026-08-04T09:03:00+00:00")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, check=True,
                         capture_output=True, text=True).stdout.strip()

    import json
    ledger = {
        "spec": "SPEC.md",
        "tasks": [
            {"id": 1, "goal": "Add TTL cache wrapper for menu lookups",
             "files": ["menu/cache.py", "tests/test_cache.py"], "max_loc": 60,
             "test": "python3 -m unittest tests.test_cache",
             "depends_on": [], "status": "in_progress", "base_sha": sha,
             "attempts": 0, "summary": None},
            {"id": 2, "goal": "Wire the TTL cache into get_menu",
             "files": ["menu/api.py", "tests/test_menu_api.py"], "max_loc": 30,
             "test": "python3 -m unittest tests.test_menu_api",
             "depends_on": [1], "status": "todo", "base_sha": None,
             "attempts": 0, "summary": None},
        ],
    }
    dg = target / ".driftguard"  # untracked local state, as in real runs
    dg.mkdir()
    (dg / "tasks.json").write_text(json.dumps(ledger, indent=2) + "\n")
    (dg / "current").write_text("1\n")

    print(f"fixture repo: {target}")
    print("harness state: task 1 in_progress (TTL cache wrapper), task 2 todo")
    print("expected: guard blocks menu/api.py; budget blocks >60 LOC; "
          "minimal cache.py passes both")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a driftguard eval fixture repo")
    ap.add_argument("target", nargs="?")
    ap.add_argument("--set", dest="which", choices=("generic", "ds", "harness"),
                    default="generic")
    args = ap.parse_args()
    which = args.which

    default = {"generic": "fixture_repo", "ds": "fixture_repo_ds",
               "harness": "fixture_repo_harness"}[which]
    target = Path(args.target or Path(__file__).parent / default)
    if target.exists():
        print(f"fixture already exists at {target}", file=sys.stderr)
        sys.exit(1)
    target.mkdir(parents=True)
    git(target, "init", "-b", "main", date="2026-08-01T09:00:00+00:00")
    {"generic": build_generic, "ds": build_ds, "harness": build_harness}[which](target)


if __name__ == "__main__":
    main()
