#!/usr/bin/env python3
"""ML-methodology pre-flight (data-scientist role), scoped to what the diff ADDED:
  T0-1 fit/fit_transform before train_test_split on the same variable  (DS-03, error)
  T0-2 train_test_split without random_state                           (DS-37, warning)
  T0-3 fit on a *test*/*val* variable                                  (DS-12, warning)
  T0-4 metric computed on *train* variables                            (DS-34, error)
  T0-5 shuffled split/CV in a file with timestamp-like columns         (DS-02, warning)
  T0-6 search/CV fitted on data that is only split afterwards          (DS-33, warning)
  T0-7 label column literally present in the feature list              (DS-13, error)
  T0-8 randomness used with no seed anywhere in the file               (DS-36, info)
AST-based, stdlib only. Precision over recall: ambiguous cases are left to the
ds-review subagent, never reported here. Notebooks are skipped (listed in skipped).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (added_lines_by_file, base_argparser, changed_files, emit,  # noqa: E402
                    finding, resolve_range)

FIT_ATTRS = {"fit", "fit_transform", "partial_fit"}
METRIC_NAMES = {
    "accuracy_score", "precision_score", "recall_score", "f1_score",
    "roc_auc_score", "average_precision_score", "log_loss",
    "mean_squared_error", "mean_absolute_error", "r2_score", "ndcg_score",
    "mean_average_precision", "auc",
}
SEARCH_NAMES = {"GridSearchCV", "RandomizedSearchCV", "HalvingGridSearchCV",
                "HalvingRandomSearchCV", "BayesSearchCV", "OptunaSearchCV"}
STUDY_FACTORIES = {"create_study", "load_study"}  # optuna
CROSS_VAL_NAMES = {"cross_val_score", "cross_validate", "cross_val_predict"}
SHUFFLE_SPLIT_NAMES = {
    "KFold", "StratifiedKFold", "ShuffleSplit", "StratifiedShuffleSplit",
    "GroupKFold",
}
SEED_NAMES = {"seed", "manual_seed", "set_seed"}
RAND_ATTRS = {"sample", "shuffle", "choice", "randint", "randn", "rand",
              "uniform", "normal", "permutation"}

TEST_VAR_RE = re.compile(r"^(?:[A-Za-z]+_)?(?:test|val|valid|eval)(?:_.*)?$")
TRAIN_VAR_RE = re.compile(r"^(?:[A-Za-z]+_)?train(?:_.*)?$")
TIMESTAMP_RE = re.compile(
    r"\b(?:timestamp|event_time|impression_time|event_ts|created_at|date)\b|\bts\b")
LABEL_NAME_RE = re.compile(r"(?i)^(?:labels?|targets?|y)(?:_col(?:umn)?)?s?$")
FEATURES_NAME_RE = re.compile(
    r"(?i)^(?:features?|feature_cols?|feature_columns?|x_cols?|input_cols?|feature_names?)$")


def root_name(node: ast.AST) -> str | None:
    """Root variable of an expression: df[cols].dropna() -> 'df'."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def call_name(call: ast.Call) -> str | None:
    """Function name of a call: np.random.seed -> 'seed', fit -> 'fit'."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def scope_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    """All nodes in a scope, not descending into nested functions/classes."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        out.append(node)
        # a nested function/class is its own scope: record it, never walk into it
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _kw_true(call: ast.Call, name: str) -> bool:
    return any(k.arg == name and isinstance(k.value, ast.Constant)
               and k.value.value is True for k in call.keywords)


def _kw_present(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def check_scope(path: str, nodes: list[ast.AST], added: set[int],
                ts_file: bool, findings: list[dict]) -> None:
    """Per-scope rules: T0-1..T0-6 (T0-5 additionally gated on file timestamp tokens)."""
    calls = sorted((n for n in nodes if isinstance(n, ast.Call)),
                   key=lambda c: c.lineno)
    assigns = [n for n in nodes if isinstance(n, ast.Assign)]

    fits = [(c.lineno, root_name(c.args[0]) if c.args else None, c)
            for c in calls
            if isinstance(c.func, ast.Attribute) and c.func.attr in FIT_ATTRS]
    splits = [(c.lineno, {root_name(a) for a in c.args}, c)
              for c in calls if call_name(c) == "train_test_split"]

    # T0-1: fit/fit_transform on a variable that is only split later (DS-03)
    for fl, fvar, fc in fits:
        if not fvar:
            continue
        for sl, svars, _ in splits:
            if sl > fl and fvar in svars and ({fl, sl} & added):
                findings.append(finding(
                    "tier0:ml_patterns", "error", path, str(fl),
                    f"DS-03: '{fc.func.attr}' fitted before train_test_split on "
                    f"'{fvar}' — preprocessing statistics leak into the test set",
                    [f"ast: {path}:{fl} fits '{fvar}'; {path}:{sl} splits it afterwards"],
                    f"split first, then fit the transformer on the train side only "
                    f"({path}:{fl})", "ml-leakage"))
                break

    for c in calls:
        name = call_name(c)
        first_arg = root_name(c.args[0]) if c.args else None

        # T0-2: train_test_split without random_state (DS-37)
        if name == "train_test_split" and not _kw_present(c, "random_state") \
                and c.lineno in added:
            findings.append(finding(
                "tier0:ml_patterns", "warning", path, str(c.lineno),
                "DS-37: train_test_split without random_state — split is not reproducible",
                [f"ast: {path}:{c.lineno} has no random_state= keyword"],
                f"pass a fixed random_state at {path}:{c.lineno}", "ml-split"))

        # T0-3: fit on a test/val variable (DS-12)
        if isinstance(c.func, ast.Attribute) and c.func.attr in FIT_ATTRS \
                and first_arg and TEST_VAR_RE.match(first_arg) and c.lineno in added:
            findings.append(finding(
                "tier0:ml_patterns", "warning", path, str(c.lineno),
                f"DS-12: '{c.func.attr}' called on test/val variable '{first_arg}'",
                [f"ast: {path}:{c.lineno} fits '{first_arg}' (name matches test/val/eval)"],
                f"fit on the train split; use '{first_arg}' for evaluation only "
                f"({path}:{c.lineno})", "ml-leakage"))

        # T0-4: metric computed on train variables (DS-34)
        is_metric = (isinstance(c.func, ast.Name) and name in METRIC_NAMES) or \
                    (isinstance(c.func, ast.Attribute) and name == "score")
        if is_metric and first_arg and TRAIN_VAR_RE.match(first_arg) \
                and c.lineno in added:
            findings.append(finding(
                "tier0:ml_patterns", "error", path, str(c.lineno),
                f"DS-34: '{name}' computed on training variable '{first_arg}' — "
                "the reported number is a training score, not an evaluation",
                [f"ast: {path}:{c.lineno} passes '{first_arg}' to {name}()"],
                f"evaluate on the held-out test split ({path}:{c.lineno})", "ml-eval"))

        # T0-5: shuffled split/CV in a timestamp-carrying file (DS-02 hint)
        if ts_file and c.lineno in added:
            if name == "train_test_split" and not any(
                    k.arg == "shuffle" and isinstance(k.value, ast.Constant)
                    and k.value.value is False for k in c.keywords):
                findings.append(finding(
                    "tier0:ml_patterns", "warning", path, str(c.lineno),
                    "DS-02: train_test_split shuffles by default and this file has "
                    "timestamp-like columns — random split risks temporal leakage",
                    [f"ast: {path}:{c.lineno} no shuffle=False; "
                     "timestamp-like token present in file"],
                    f"use a time-based split (cutoff on the timestamp column) "
                    f"at {path}:{c.lineno}", "ml-split"))
            elif name in SHUFFLE_SPLIT_NAMES and _kw_true(c, "shuffle"):
                findings.append(finding(
                    "tier0:ml_patterns", "warning", path, str(c.lineno),
                    f"DS-02: {name}(shuffle=True) in a file with timestamp-like "
                    "columns — temporal leakage risk",
                    [f"ast: {path}:{c.lineno} shuffle=True; "
                     "timestamp-like token present in file"],
                    f"use TimeSeriesSplit or a time-ordered split at {path}:{c.lineno}",
                    "ml-split"))

    # T0-6: search/CV fitted on data that is only split afterwards (DS-33)
    search_objs, study_objs = set(), set()
    for a in assigns:
        if not (len(a.targets) == 1 and isinstance(a.targets[0], ast.Name)
                and isinstance(a.value, ast.Call)):
            continue
        name = call_name(a.value)
        if name in SEARCH_NAMES:
            search_objs.add(a.targets[0].id)
        elif name in STUDY_FACTORIES:
            study_objs.add(a.targets[0].id)

    # optuna `study.optimize(...)` / hyperopt `fmin(...)`: the data is captured by
    # the objective closure, so there is no variable to match — the signal is that
    # a split happens later in the same scope.
    for c in calls:
        is_optuna = (isinstance(c.func, ast.Attribute) and c.func.attr == "optimize"
                     and root_name(c.func.value) in study_objs)
        is_hyperopt = call_name(c) == "fmin" and (len(c.args) >= 2
                                                  or _kw_present(c, "space"))
        is_tuner = is_optuna or is_hyperopt
        if is_tuner and c.lineno in added and any(sl > c.lineno for sl, _, _ in splits):
            lib = "optuna" if is_optuna else "hyperopt"
            findings.append(finding(
                "tier0:ml_patterns", "warning", path, str(c.lineno),
                f"DS-33: {lib} hyperparameter search runs before the train/test "
                "split — the objective sees data that later becomes the test set",
                [f"ast: {path}:{c.lineno} runs the {lib} search; "
                 f"train_test_split at {path}:{min(sl for sl, _, _ in splits if sl > c.lineno)}"],
                f"split first, then tune on the train split only ({path}:{c.lineno})",
                "ml-eval"))
    for c in calls:
        name = call_name(c)
        target_var = None
        if isinstance(c.func, ast.Name) and name in CROSS_VAL_NAMES and len(c.args) >= 2:
            target_var = root_name(c.args[1])
        elif isinstance(c.func, ast.Attribute) and c.func.attr in FIT_ATTRS \
                and root_name(c.func.value) in search_objs and c.args:
            target_var = root_name(c.args[0])
        if target_var and c.lineno in added and any(
                sl > c.lineno and target_var in svars for sl, svars, _ in splits):
            findings.append(finding(
                "tier0:ml_patterns", "warning", path, str(c.lineno),
                f"DS-33: model selection ('{name}') fitted on '{target_var}' before "
                "the split — test data contaminates hyperparameter/CV selection",
                [f"ast: {path}:{c.lineno} fits '{target_var}'; split happens later"],
                f"run {name} on the train split only ({path}:{c.lineno})", "ml-eval"))


def check_file_level(path: str, tree: ast.AST, added: set[int],
                     findings: list[dict]) -> None:
    """File-level rules: T0-7 (label in features), T0-8 (randomness, no seed)."""
    all_nodes = list(ast.walk(tree))
    calls = [n for n in all_nodes if isinstance(n, ast.Call)]
    assigns = [n for n in all_nodes if isinstance(n, ast.Assign)]

    # T0-7: label column literally present in the feature list (DS-13)
    labels: dict[str, int] = {}
    feat_lists: list[tuple[int, list[str]]] = []
    for a in assigns:
        if len(a.targets) != 1 or not isinstance(a.targets[0], ast.Name):
            continue
        tname = a.targets[0].id
        if LABEL_NAME_RE.match(tname) and isinstance(a.value, ast.Constant) \
                and isinstance(a.value.value, str):
            labels[a.value.value] = a.lineno
        if FEATURES_NAME_RE.match(tname) and isinstance(a.value, (ast.List, ast.Tuple)):
            strs = [el.value for el in a.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)]
            feat_lists.append((a.lineno, strs))
    for fl, strs in feat_lists:
        for hit in set(strs) & set(labels):
            if {fl, labels[hit]} & added:
                findings.append(finding(
                    "tier0:ml_patterns", "error", path, str(fl),
                    f"DS-13: label column '{hit}' is present in the feature list — "
                    "the model reads the answer",
                    [f"ast: label assigned at {path}:{labels[hit]}; "
                     f"'{hit}' appears in feature list at {path}:{fl}"],
                    f"drop '{hit}' from the feature list at {path}:{fl}", "ml-leakage"))

    # T0-8: randomness used, no seed anywhere in the file (DS-36)
    seeded = any(call_name(c) in SEED_NAMES or _kw_present(c, "random_state")
                 for c in calls)
    rand_lines = [c.lineno for c in calls
                  if (isinstance(c.func, ast.Attribute) and call_name(c) in RAND_ATTRS)
                  or call_name(c) == "train_test_split"
                  or _kw_true(c, "shuffle")]
    if rand_lines and not seeded:
        added_rand = [l for l in rand_lines if l in added]
        if added_rand:
            line = min(added_rand)
            findings.append(finding(
                "tier0:ml_patterns", "info", path, str(line),
                "DS-36: randomness used (split/shuffle/sample) but no seed is set "
                "anywhere in this file — runs are not reproducible",
                [f"ast: {len(rand_lines)} randomness call(s) in {path}; "
                 "no seed()/manual_seed()/random_state= found"],
                f"set a seed (e.g. np.random.seed / random_state) near {path}:{line}",
                "ml-hygiene"))


def main() -> None:
    ap = base_argparser("ML-methodology pre-flight (data-scientist role)")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    added = added_lines_by_file(repo, base, head)
    findings: list[dict] = []
    skipped: list[dict] = []

    for f in changed_files(repo, base, head):
        path = f["path"]
        if path.endswith(".ipynb"):
            skipped.append({"check": "ml_patterns",
                            "reason": f"{path}: notebooks not analyzed in v0.3"})
            continue
        if not path.endswith(".py") or path not in added:
            continue
        full = repo / path
        if not full.is_file():
            continue
        source = full.read_text(errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            skipped.append({"check": "ml_patterns",
                            "reason": f"{path}: syntax error at line {e.lineno}"})
            continue
        added_set = set(added[path])
        ts_file = bool(TIMESTAMP_RE.search(source))

        scopes = [tree.body] + [n.body for n in ast.walk(tree)
                                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for body in scopes:
            check_scope(path, scope_nodes(body), added_set, ts_file, findings)
        check_file_level(path, tree, added_set, findings)

    emit({"check": "ml_patterns", "findings": findings, "skipped": skipped})


if __name__ == "__main__":
    main()
