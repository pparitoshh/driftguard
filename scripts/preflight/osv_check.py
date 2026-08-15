#!/usr/bin/env python3
"""Dependency-vulnerability pre-flight (the lightweight GHSA scanner).

When the diff changes a dependency manifest, newly added/changed *pinned* deps are
queried against the free OSV.dev API (no key). Covers what CodeRabbit markets as
"dependency vulnerability detection" with one POST per package.

Manifests: requirements*.txt, pyproject.toml (PyPI); package.json (npm).
Only exact pins are checked (`pkg==1.2.3`, `"pkg": "1.2.3"`); ranges are noted as
unverifiable. Offline, or network disabled by default
(DRIFTGUARD_ALLOW_NETWORK unset) -> check skipped.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (NETWORK_DISABLED_REASON, base_argparser,  # noqa: E402
                    changed_files, diff_added_removed, emit, finding,
                    http_post_json, network_allowed, resolve_range)

OSV_URL = "https://api.osv.dev/v1/query"
MAX_QUERIES = 20
MAX_VULNS_PER_DEP = 3

REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9.\-+]+)")
PYPROJECT_DEP = re.compile(r"""["']([A-Za-z0-9_.\-]+)==\s*([A-Za-z0-9.\-+]+)["']""")
PKG_JSON_DEP = re.compile(r"""["']([^"@/\s][^"\s]*|@[^"\s/]+/[^"\s]+)["']\s*:\s*["']~?\^?([0-9][A-Za-z0-9.\-+]*)["']""")


def is_manifest(path: str) -> str | None:
    """Return ecosystem when `path` is a dependency manifest."""
    name = Path(path).name
    if re.fullmatch(r"requirements.*\.txt", name) or name == "pyproject.toml":
        return "PyPI"
    if name == "package.json":
        return "npm"
    return None


def parse_added_deps(ecosystem: str, added: list[str]) -> dict[str, str]:
    """dep -> pinned version, from added diff lines of a manifest."""
    deps: dict[str, str] = {}
    for line in added:
        m = None
        if ecosystem == "PyPI":
            m = REQ_LINE.match(line) or PYPROJECT_DEP.search(line)
        else:
            m = PKG_JSON_DEP.search(line)
            if m and m.group(1) in ("name", "version", "private", "type", "main"):
                continue
        if m:
            deps[m.group(1)] = m.group(2)
    return deps


def main() -> None:
    ap = base_argparser("dependency-vulnerability pre-flight (OSV.dev)")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    findings: list[dict] = []
    skipped: list[dict] = []
    queries: list[tuple[str, str, str, str]] = []  # (file, dep, version, ecosystem)

    for f in changed_files(repo, base, head):
        eco = is_manifest(f["path"])
        if not eco:
            continue
        added, _ = diff_added_removed(repo, base, head, f["path"])
        for dep, ver in parse_added_deps(eco, added).items():
            queries.append((f["path"], dep, ver, eco))

    if not queries:
        emit({"check": "osv_check", "findings": [],
              "skipped": [{"check": "osv_check",
                           "reason": "no pinned dependencies added/changed in manifests"}],
              "deps_checked": 0})
        return

    offline = False
    checked = 0
    for path, dep, ver, eco in queries[:MAX_QUERIES]:
        resp = http_post_json(OSV_URL, {
            "package": {"name": dep, "ecosystem": eco}, "version": ver})
        if resp is None:
            offline = True
            continue
        checked += 1
        vulns = resp.get("vulns") or []
        if vulns:
            ids = [v.get("id", "?") for v in vulns[:MAX_VULNS_PER_DEP]]
            summaries = "; ".join(
                f"{v.get('id', '?')}: {(v.get('summary') or 'no summary')[:80]}"
                for v in vulns[:MAX_VULNS_PER_DEP])
            findings.append(finding(
                "tier0:osv_check", "error", path, "manifest",
                f"{dep}=={ver} has {len(vulns)} known vulnerability(ies) in OSV ({eco})",
                [f"POST api.osv.dev {eco}/{dep}@{ver} -> {summaries}"
                 + (f" … +{len(vulns) - MAX_VULNS_PER_DEP} more"
                    if len(vulns) > MAX_VULNS_PER_DEP else "")],
                f"bump {dep} to a fixed release (see {', '.join(ids)}); if pinned "
                f"deliberately, document why in the PR description",
                category="security"))

    if offline:
        skipped.append({"check": "osv_check(api)",
                        "reason": (NETWORK_DISABLED_REASON if not network_allowed()
                                   else "OSV unreachable") +
                        " — some pinned deps not verified"})
    if len(queries) > MAX_QUERIES:
        skipped.append({"check": "osv_check(budget)",
                        "reason": f"+{len(queries) - MAX_QUERIES} deps over query budget"})
    emit({"check": "osv_check", "findings": findings, "skipped": skipped,
          "deps_checked": checked})


if __name__ == "__main__":
    main()
