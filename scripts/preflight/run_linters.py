#!/usr/bin/env python3
"""Run the repo's own linters on changed files. Free deterministic signal —
never hard-fail: if no linter is configured/available, report skipped."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import base_argparser, changed_files, emit, finding, resolve_range, try_run  # noqa: E402

MAX_FINDINGS = 20


def ruff_configured(repo: Path) -> bool:
    if (repo / "ruff.toml").is_file() or (repo / ".ruff.toml").is_file():
        return True
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(errors="replace")
        return "[tool.ruff" in text or '"ruff' in text or "'ruff" in text
    return False


def ruff_command() -> list[str] | None:
    if shutil.which("ruff"):
        return ["ruff"]
    if shutil.which("uvx"):
        return ["uvx", "ruff"]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "ruff"]
    return None


def main() -> None:
    ap = base_argparser("run repo linters on changed files")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    findings: list[dict] = []
    skipped: list[dict] = []

    py_files = [f["path"] for f in changed_files(repo, base, head)
                if f["path"].endswith(".py") and (repo / f["path"]).is_file()]

    if not py_files:
        skipped.append({"check": "linters", "reason": "no changed .py files"})
    elif not ruff_configured(repo):
        skipped.append({"check": "linters", "reason": "no ruff config found in repo"})
    else:
        cmd = ruff_command()
        if not cmd:
            skipped.append({"check": "linters", "reason": "ruff not installed and no uvx fallback"})
        else:
            out = try_run([*cmd, "check", "--output-format=json", *py_files],
                          cwd=repo, timeout=120)
            if out is None:
                skipped.append({"check": "linters", "reason": "ruff invocation failed"})
            else:
                try:
                    diagnostics = json.loads(out or "[]")
                except json.JSONDecodeError:
                    diagnostics = []
                for d in diagnostics[:MAX_FINDINGS]:
                    loc = d.get("location") or {}
                    row = loc.get("row", "?")
                    path = (d.get("filename") or "").removeprefix(str(repo) + "/")
                    findings.append(finding(
                        "tier0:linters", "warning", path, str(row),
                        f"ruff {d.get('code')}: {d.get('message')}",
                        [f"ruff check {path}:{row}"],
                        d.get("fix") and "apply ruff's suggested fix" or
                        f"fix or silence {d.get('code')} at {path}:{row}"))
                if len(diagnostics) > MAX_FINDINGS:
                    findings.append(finding(
                        "tier0:linters", "info", "-", "-",
                        f"+{len(diagnostics) - MAX_FINDINGS} further ruff diagnostics not listed",
                        ["ruff check output truncated"], "run ruff locally for the full list"))

    emit({"check": "run_linters", "findings": findings, "skipped": skipped})


if __name__ == "__main__":
    main()
