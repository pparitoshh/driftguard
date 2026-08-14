#!/usr/bin/env python3
"""Development-history context for a review range.

Output JSON:
  base/head, range_start/range_end (ISO, for session matching),
  branch_commits, changed_files (with per-file prior churn), pr_conversation (if --pr).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import base_argparser, changed_files, emit, resolve_range, run, try_run  # noqa: E402

MAX_FILES = 25


def git_log_lines(repo: Path, fmt: str, *extra: str) -> list[str]:
    out = try_run(["git", "log", f"--format={fmt}", *extra], cwd=repo)
    return [l for l in (out or "").splitlines() if l.strip()]


def prior_churn(repo: Path, base: str, path: str, limit: int = 5) -> dict:
    """History of `path` as of `base` (i.e. before this branch's work)."""
    recent = git_log_lines(repo, "%h %ad %s", "--date=short", "--follow", f"-n{limit}", base, "--", path)
    count_out = try_run(
        ["bash", "-c", f"git log --follow --format=%H {base} -- '{path}' | wc -l"], cwd=repo
    )
    try:
        total = int((count_out or "0").strip())
    except ValueError:
        total = None
    return {"recent": recent, "total_commits_before_range": total}


def range_times(repo: Path, base: str, head: str) -> tuple[str | None, str | None]:
    out = try_run(["git", "log", "--format=%aI", f"{base}..{head}"], cwd=repo)
    dates = sorted(l for l in (out or "").splitlines() if l.strip())
    if not dates:
        return None, None
    return dates[0], dates[-1]


def pr_conversation(repo: Path, pr: int) -> dict | None:
    out = try_run(
        ["gh", "pr", "view", str(pr), "--json", "comments,reviews,title,url"], cwd=repo
    )
    if not out:
        return None
    data = json.loads(out)
    def trim(items):
        return [
            {"author": (i.get("author") or {}).get("login"), "body": (i.get("body") or "")[:500]}
            for i in (items or [])[:15]
        ]
    return {
        "title": data.get("title"),
        "url": data.get("url"),
        "comments": trim(data.get("comments")),
        "reviews": trim(data.get("reviews")),
    }


def main() -> None:
    ap = base_argparser("development history for a review range")
    ap.add_argument("--pr", type=int, default=None)
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    files = changed_files(repo, base, head)
    start, end = range_times(repo, base, head)

    for f in files[:MAX_FILES]:
        if f["status"] != "A":  # churn only meaningful for pre-existing files
            f["churn"] = prior_churn(repo, base, f["path"])

    emit({
        "base": base,
        "head": head,
        "range_start": start,
        "range_end": end,
        "branch_commits": git_log_lines(repo, "%h %aI %an %s", f"{base}..{head}"),
        "changed_files": files,
        "files_capped": len(files) > MAX_FILES,
        "pr_conversation": pr_conversation(repo, args.pr) if args.pr else None,
    })


if __name__ == "__main__":
    main()
