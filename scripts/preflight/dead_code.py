#!/usr/bin/env python3
"""Dead-code pre-flight, scoped to what the diff ADDED:
  - unused imports introduced by the diff
  - statements made unreachable by an added return/raise
AST-based, stdlib only.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (added_lines_by_file, base_argparser, changed_files, emit,  # noqa: E402
                    finding, resolve_range)


def bound_names(tree: ast.AST) -> list[tuple[str, int]]:
    """(name, lineno) bound by import statements."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append(((a.asname or a.name).split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.append((a.asname or a.name, node.lineno))
    return out


def used_names(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def unreachable_after(tree: ast.AST) -> list[int]:
    """linenos of statements following return/raise in the same block."""
    bad: list[int] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for prev, nxt in zip(body, body[1:]):
                if isinstance(prev, (ast.Return, ast.Raise)) and hasattr(nxt, "lineno"):
                    bad.append(nxt.lineno)
    return bad


def main() -> None:
    ap = base_argparser("dead-code pre-flight")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    added = added_lines_by_file(repo, base, head)
    findings: list[dict] = []
    skipped: list[dict] = []

    for f in changed_files(repo, base, head):
        path = f["path"]
        if not path.endswith(".py") or path not in added:
            continue
        full = repo / path
        if not full.is_file():
            continue
        try:
            tree = ast.parse(full.read_text(errors="replace"))
        except SyntaxError as e:
            skipped.append({"check": "dead_code", "reason": f"{path}: syntax error at line {e.lineno}"})
            continue
        added_set = set(added[path])
        used = used_names(tree)

        for name, lineno in bound_names(tree):
            if lineno in added_set and name not in used:
                findings.append(finding(
                    "tier0:dead_code", "warning", path, str(lineno),
                    f"import '{name}' added by this diff but never used in the file",
                    [f"ast: name '{name}' bound at line {lineno}, 0 references in {path}"],
                    f"remove the unused import at {path}:{lineno}"))

        for lineno in unreachable_after(tree):
            if lineno in added_set:
                findings.append(finding(
                    "tier0:dead_code", "warning", path, str(lineno),
                    "statement is unreachable (follows return/raise in the same block)",
                    [f"ast: {path}:{lineno} follows a return/raise"],
                    f"delete or reorder the dead statement at {path}:{lineno}"))

    emit({"check": "dead_code", "findings": findings, "skipped": skipped})


if __name__ == "__main__":
    main()
