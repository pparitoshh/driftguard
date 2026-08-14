#!/usr/bin/env python3
"""Resolve the plan/spec for a review range.

Chain (first hit wins): PR body -> linked issue -> PLAN.md / docs/specs /
.driftguard/plan.md -> commit messages -> branch name -> --task flag.

Output: {"source": ..., "text": ..., "tried": [...]}
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import base_argparser, changed_files, diff_args, emit, resolve_range, run, try_run  # noqa: E402

MAX_TEXT = 6000

ISSUE_REF = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)


def from_pr(repo: Path, pr: int) -> tuple[str | None, dict | None]:
    out = try_run(["gh", "pr", "view", str(pr), "--json", "title,body,url"], cwd=repo)
    if not out:
        return None, {"source": "pr-body", "found": False, "note": "gh failed or no such PR"}
    import json
    data = json.loads(out)
    text = f"{data.get('title', '')}\n\n{data.get('body', '')}".strip()
    if not text:
        return None, {"source": "pr-body", "found": False, "note": "empty body"}
    return text, {"source": "pr-body", "found": True, "url": data.get("url")}


def from_linked_issue(repo: Path, pr_body: str) -> tuple[str | None, dict | None]:
    m = ISSUE_REF.search(pr_body or "")
    if not m:
        return None, {"source": "linked-issue", "found": False, "note": "no issue reference in PR body"}
    num = m.group(1)
    out = try_run(["gh", "issue", "view", num, "--json", "title,body"], cwd=repo)
    if not out:
        return None, {"source": "linked-issue", "found": False, "note": f"gh failed for #{num}"}
    import json
    data = json.loads(out)
    text = f"Issue #{num}: {data.get('title', '')}\n\n{data.get('body', '')}".strip()
    return text, {"source": "linked-issue", "found": True, "issue": int(num)}


def from_repo_docs(repo: Path) -> tuple[str | None, dict | None]:
    candidates = ["PLAN.md", "plan.md", ".driftguard/plan.md", "SPEC.md", "docs/spec.md"]
    for rel in candidates:
        p = repo / rel
        if p.is_file() and p.read_text(errors="replace").strip():
            return p.read_text(errors="replace"), {"source": "plan-doc", "found": True, "path": rel}
    specs = sorted((repo / "docs" / "specs").glob("*.md")) if (repo / "docs" / "specs").is_dir() else []
    if specs:
        text = "\n\n".join(f"--- {s.name} ---\n{s.read_text(errors='replace')}" for s in specs[:5])
        return text, {"source": "plan-doc", "found": True, "path": "docs/specs/"}
    return None, {"source": "plan-doc", "found": False, "note": "no PLAN.md/SPEC/docs/specs"}


def from_commits(repo: Path, base: str, head: str) -> tuple[str | None, dict | None]:
    out = try_run(["git", "log", "--format=- %s%n%b", *diff_args(base, head)], cwd=repo)
    if out and out.strip():
        return "Commit messages:\n" + out.strip(), {"source": "commits", "found": True}
    return None, {"source": "commits", "found": False}


def from_branch(repo: Path, head: str) -> tuple[str | None, dict | None]:
    name = head if head != "HEAD" else try_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if name and name.strip() and name.strip() != "HEAD":
        return f"Branch name: {name.strip()}", {"source": "branch-name", "found": True}
    return None, {"source": "branch-name", "found": False}


def main() -> None:
    ap = base_argparser("resolve plan/spec for a review range")
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--task", default=None)
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    tried: list[dict] = []
    text, meta = (None, None)

    if args.pr:
        text, meta = from_pr(repo, args.pr)
        tried.append(meta or {})
        if text:
            issue_text, issue_meta = from_linked_issue(repo, text)
            if issue_text:
                text = text + "\n\n" + issue_text
                tried.append(issue_meta or {})

    if not text:
        text, meta = from_repo_docs(repo)
        tried.append(meta or {})
    if not text:
        text, meta = from_commits(repo, base, head)
        tried.append(meta or {})
    if not text:
        text, meta = from_branch(repo, head)
        tried.append(meta or {})
    if not text and args.task:
        text, meta = f"Task (user-supplied): {args.task}", {"source": "task-flag", "found": True}
        tried.append(meta)
    if not text:
        text, meta = "No plan found; review against the diff alone.", {"source": "none", "found": False}

    emit({
        "source": (meta or {}).get("source", "none"),
        "text": text[:MAX_TEXT],
        "truncated": len(text) > MAX_TEXT,
        "base": base,
        "head": head,
        "tried": tried,
    })


if __name__ == "__main__":
    main()
