"""Tests for the ML-methodology pre-flight (T0-1..T0-8), one positive + one
negative case per rule."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "preflight"))

import ml_patterns  # noqa: E402
from test_scripts import RepoTestCase  # noqa: E402


def run_main(module, argv: list[str]) -> dict:
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", [module.__name__, *argv]):
        with contextlib.redirect_stdout(buf):
            module.main()
    return json.loads(buf.getvalue())


class MLPatternsTestCase(RepoTestCase):
    """Commits `train.py` empty, then the code under test as the diff."""

    def scan(self, code: str) -> dict:
        self.commit_file("train.py", "", "base")
        self.commit_file("train.py", code, "add training code",
                         "2026-08-02T10:00:00+00:00")
        return run_main(ml_patterns, ["--repo", str(self.repo),
                                      "--base", "HEAD~1", "--head", "HEAD"])

    def claims(self, code: str) -> str:
        return " | ".join(f["claim"] for f in self.scan(code)["findings"])


class TestFitBeforeSplit(MLPatternsTestCase):
    def test_fit_before_split_flagged(self):
        self.assertIn("DS-03", self.claims(
            "from sklearn.preprocessing import StandardScaler\n"
            "from sklearn.model_selection import train_test_split\n"
            "def go(df, y):\n"
            "    scaler = StandardScaler()\n"
            "    X = scaler.fit_transform(df)\n"
            "    return train_test_split(df, y, random_state=0)\n"))

    def test_fit_after_split_clean(self):
        self.assertNotIn("DS-03", self.claims(
            "from sklearn.preprocessing import StandardScaler\n"
            "from sklearn.model_selection import train_test_split\n"
            "def go(df, y):\n"
            "    X_train, X_test, y_train, y_test = train_test_split(\n"
            "        df, y, random_state=0)\n"
            "    return StandardScaler().fit_transform(X_train)\n"))


class TestSplitRandomState(MLPatternsTestCase):
    def test_missing_random_state_flagged(self):
        self.assertIn("DS-37", self.claims(
            "from sklearn.model_selection import train_test_split\n"
            "def go(X, y):\n"
            "    return train_test_split(X, y, test_size=0.2)\n"))

    def test_random_state_present_clean(self):
        self.assertNotIn("DS-37", self.claims(
            "from sklearn.model_selection import train_test_split\n"
            "def go(X, y):\n"
            "    return train_test_split(X, y, test_size=0.2, random_state=7)\n"))


class TestFitOnTestVar(MLPatternsTestCase):
    def test_fit_on_test_var_flagged(self):
        self.assertIn("DS-12", self.claims(
            "def go(X_test, scaler):\n"
            "    return scaler.fit_transform(X_test)\n"))

    def test_transform_on_test_var_clean(self):
        self.assertNotIn("DS-12", self.claims(
            "def go(X_test, scaler):\n"
            "    return scaler.transform(X_test)\n"))


class TestMetricOnTrain(MLPatternsTestCase):
    def test_metric_on_train_flagged(self):
        self.assertIn("DS-34", self.claims(
            "from sklearn.metrics import roc_auc_score\n"
            "def go(model, X_train, y_train):\n"
            "    return roc_auc_score(y_train, model.predict(X_train))\n"))

    def test_metric_on_test_clean(self):
        self.assertNotIn("DS-34", self.claims(
            "from sklearn.metrics import roc_auc_score\n"
            "def go(model, X_test, y_test):\n"
            "    return roc_auc_score(y_test, model.predict(X_test))\n"))


class TestTemporalShuffle(MLPatternsTestCase):
    def test_shuffled_split_with_timestamps_flagged(self):
        self.assertIn("DS-02", self.claims(
            "from sklearn.model_selection import train_test_split\n"
            "def go(df, y):\n"
            "    df = df.sort_values('event_time')\n"
            "    return train_test_split(df, y, random_state=0)\n"))

    def test_no_timestamp_column_clean(self):
        self.assertNotIn("DS-02", self.claims(
            "from sklearn.model_selection import train_test_split\n"
            "def go(df, y):\n"
            "    return train_test_split(df, y, random_state=0)\n"))

    def test_kfold_shuffle_with_timestamps_flagged(self):
        self.assertIn("DS-02", self.claims(
            "from sklearn.model_selection import KFold\n"
            "def go(df):\n"
            "    df = df.sort_values('timestamp')\n"
            "    return KFold(n_splits=5, shuffle=True, random_state=0)\n"))


class TestTuningBeforeSplit(MLPatternsTestCase):
    def test_gridsearch_before_split_flagged(self):
        self.assertIn("DS-33", self.claims(
            "from sklearn.model_selection import GridSearchCV, train_test_split\n"
            "def go(est, grid, X, y):\n"
            "    search = GridSearchCV(est, grid)\n"
            "    search.fit(X, y)\n"
            "    return train_test_split(X, y, random_state=0)\n"))

    def test_cross_val_score_before_split_flagged(self):
        self.assertIn("DS-33", self.claims(
            "from sklearn.model_selection import cross_val_score, train_test_split\n"
            "def go(est, X, y):\n"
            "    cross_val_score(est, X, y)\n"
            "    return train_test_split(X, y, random_state=0)\n"))

    def test_optuna_before_split_flagged(self):
        self.assertIn("DS-33", self.claims(
            "import optuna\n"
            "from sklearn.model_selection import train_test_split\n"
            "def go(objective, X, y):\n"
            "    study = optuna.create_study()\n"
            "    study.optimize(objective, n_trials=50)\n"
            "    return train_test_split(X, y, random_state=0)\n"))

    def test_hyperopt_before_split_flagged(self):
        self.assertIn("DS-33", self.claims(
            "from hyperopt import fmin, tpe\n"
            "from sklearn.model_selection import train_test_split\n"
            "def go(objective, space, X, y):\n"
            "    fmin(objective, space, algo=tpe.suggest, max_evals=50)\n"
            "    return train_test_split(X, y, random_state=0)\n"))

    def test_optuna_after_split_clean(self):
        self.assertNotIn("DS-33", self.claims(
            "import optuna\n"
            "from sklearn.model_selection import train_test_split\n"
            "def go(objective, X, y):\n"
            "    X_train, X_test, y_train, y_test = train_test_split(\n"
            "        X, y, random_state=0)\n"
            "    study = optuna.create_study()\n"
            "    study.optimize(objective, n_trials=50)\n"
            "    return study\n"))

    def test_gridsearch_after_split_clean(self):
        self.assertNotIn("DS-33", self.claims(
            "from sklearn.model_selection import GridSearchCV, train_test_split\n"
            "def go(est, grid, X, y):\n"
            "    X_train, X_test, y_train, y_test = train_test_split(\n"
            "        X, y, random_state=0)\n"
            "    search = GridSearchCV(est, grid)\n"
            "    search.fit(X_train, y_train)\n"
            "    return search\n"))


class TestLabelInFeatures(MLPatternsTestCase):
    def test_label_in_feature_list_flagged(self):
        self.assertIn("DS-13", self.claims(
            'LABEL = "clicked"\n'
            'FEATURES = ["position", "dwell", "clicked"]\n'))

    def test_label_absent_from_features_clean(self):
        self.assertNotIn("DS-13", self.claims(
            'LABEL = "clicked"\n'
            'FEATURES = ["position", "dwell"]\n'))


class TestMissingSeed(MLPatternsTestCase):
    def test_randomness_without_seed_flagged(self):
        self.assertIn("DS-36", self.claims(
            "import numpy as np\n"
            "def go(df):\n"
            "    return df.sample(100), np.random.permutation(10)\n"))

    def test_seed_set_clean(self):
        self.assertNotIn("DS-36", self.claims(
            "import numpy as np\n"
            "np.random.seed(0)\n"
            "def go(df):\n"
            "    return df.sample(100), np.random.permutation(10)\n"))


class TestScopeAndSkips(MLPatternsTestCase):
    def test_notebook_skipped(self):
        self.commit_file("explore.ipynb", "{}\n", "base")
        self.commit_file("explore.ipynb", '{"cells": []}\n', "edit notebook",
                         "2026-08-02T10:00:00+00:00")
        out = run_main(ml_patterns, ["--repo", str(self.repo),
                                     "--base", "HEAD~1", "--head", "HEAD"])
        self.assertTrue(any("notebook" in s["reason"] for s in out["skipped"]))

    def test_unchanged_lines_not_flagged(self):
        code = ("from sklearn.model_selection import train_test_split\n"
                "def go(X, y):\n"
                "    return train_test_split(X, y)\n")
        self.commit_file("train.py", code, "base")
        self.commit_file("train.py", code + "OTHER = 1\n", "unrelated edit",
                         "2026-08-02T10:00:00+00:00")
        out = run_main(ml_patterns, ["--repo", str(self.repo),
                                     "--base", "HEAD~1", "--head", "HEAD"])
        self.assertEqual(out["findings"], [])

    def test_syntax_error_skipped(self):
        out = self.scan("def go(:\n    pass\n")
        self.assertTrue(any("syntax error" in s["reason"] for s in out["skipped"]))


if __name__ == "__main__":
    unittest.main()
