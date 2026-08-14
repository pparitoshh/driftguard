#!/usr/bin/env python3
"""Deterministic triage card for a review range — the lightweight answer to
CodeRabbit Triage (risk/effort/complexity) and review-effort estimates.

Pure git data, no model:
  - diff size + file count
  - churn hotspots (files with heavy history before this range)
  - sensitive-path touches (auth/crypto/migrations/CI/…)
  - test gap (code changed but no tests changed)
  - dependency manifests changed

Output: risk level with a transparent score breakdown, review-effort estimate,
per-file table, and a suggested review order (sensitive + hot + large first).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import base_argparser, changed_files, emit, resolve_range, run, try_run  # noqa: E402

SENSITIVE_RE = re.compile(
    r"(?i)(auth|login|session|token|crypto|password|secret|credential|payment|"
    r"billing|checkout|migration|schema|permission|acl|rbac|admin|security|"
    r"Dockerfile|\.github/workflows|deploy|infra|terraform|helm)")
TEST_RE = re.compile(r"(?i)(^|/)(tests?|testing|__tests__|spec)/|(^|/)(test_[^/]*|"
                     r"[^/]*_test|[^/]*\.test|[^/]*\.spec)\.(py|js|ts|jsx|tsx)$")
MANIFEST_RE = re.compile(r"(?i)(requirements.*\.txt|pyproject\.toml|package\.json|"
                         r"package-lock\.json|poetry\.lock|uv\.lock|Cargo\.toml|go\.mod)$")
HOTSPOT_CHURN = 8          # prior commits before this range that make a file "hot"
MAX_ORDER = 12


def numstat(repo: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    out = run(["git", "diff", "--numstat", f"{base}...{head}"], cwd=repo)
    stats: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                stats[parts[2]] = (int(parts[0]), int(parts[1]))
            except ValueError:
                stats[parts[2]] = (0, 0)  # binary file
    return stats


def prior_commit_count(repo: Path, base: str, path: str) -> int:
    out = try_run(["bash", "-c",
                   f"git log --follow --format=%H {base} -- '{path}' | wc -l"], cwd=repo)
    try:
        return int((out or "0").strip())
    except ValueError:
        return 0


def effort_estimate(total_lines: int) -> str:
    if total_lines < 50:
        return "~5 minutes"
    if total_lines < 200:
        return "~15 minutes"
    if total_lines < 800:
        return "~30–45 minutes"
    return "1 hour+ (consider splitting the review)"


def main() -> None:
    ap = base_argparser("deterministic triage / risk card for a review range")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    files = changed_files(repo, base, head)
    stats = numstat(repo, base, head)

    per_file: list[dict] = []
    churn_by_path: dict[str, int] = {}
    for f in files:
        path = f["path"]
        adds, dels = stats.get(path, (0, 0))
        churn = 0 if f["status"] == "A" else prior_commit_count(repo, base, path)
        churn_by_path[path] = churn
        per_file.append({
            "file": path,
            "status": f["status"],
            "added": adds,
            "removed": dels,
            "prior_commits": churn,
            "sensitive": bool(SENSITIVE_RE.search(path)),
            "kind": ("test" if TEST_RE.search(path)
                     else "manifest" if MANIFEST_RE.search(path)
                     else "docs" if path.lower().endswith((".md", ".rst", ".txt"))
                     else "code"),
        })

    total_lines = sum(a + d for a, d in stats.values())
    code_changed = any(p["kind"] == "code" for p in per_file)
    tests_changed = any(p["kind"] == "test" for p in per_file)
    manifests_changed = any(p["kind"] == "manifest" for p in per_file)
    sensitive_files = [p["file"] for p in per_file if p["sensitive"]]
    hotspots = [p["file"] for p in per_file if p["prior_commits"] >= HOTSPOT_CHURN]

    score, breakdown = 0, []
    def add(points: int, why: str) -> None:
        nonlocal score
        score += points
        breakdown.append(f"+{points}: {why}")

    if sensitive_files:
        add(min(2 * len(sensitive_files), 6),
            f"{len(sensitive_files)} sensitive-path file(s): {', '.join(sensitive_files[:5])}")
    if code_changed and not tests_changed:
        add(3, "code changed but no test files changed")
    if hotspots:
        add(2, f"churn hotspot(s) (≥{HOTSPOT_CHURN} prior commits): {', '.join(hotspots[:3])}")
    size_pts = min(total_lines // 200, 4)
    if size_pts:
        add(size_pts, f"{total_lines} lines changed")
    if manifests_changed:
        add(2, "dependency manifest changed")

    level = "low" if score <= 2 else "medium" if score <= 6 else "high"

    # suggested review order: sensitive first, then hotspots, then largest diffs
    def rank(p: dict) -> tuple:
        return (not p["sensitive"], p["prior_commits"] < HOTSPOT_CHURN,
                -(p["added"] + p["removed"]))
    order = [p["file"] for p in sorted(per_file, key=rank)][:MAX_ORDER]

    emit({
        "check": "risk_score",
        "risk_level": level,
        "risk_score": score,
        "score_breakdown": breakdown or ["+0: small, low-churn, non-sensitive change"],
        "review_effort": effort_estimate(total_lines),
        "totals": {"files": len(per_file), "lines_changed": total_lines,
                   "sensitive_files": len(sensitive_files),
                   "hotspots": len(hotspots),
                   "test_gap": code_changed and not tests_changed},
        "suggested_review_order": order,
        "files": per_file,
    })


if __name__ == "__main__":
    main()
