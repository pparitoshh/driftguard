#!/usr/bin/env python3
"""Run the repo's own linters on changed files. Free deterministic signal —
never hard-fail: if no linter is configured/available, report skipped.

Python: ruff (repo-configured). JS/TS: eslint — only the repo's local install
(node_modules/.bin/eslint); never npx-fetches anything.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import base_argparser, changed_files, emit, finding, resolve_range, try_run  # noqa: E402

MAX_FINDINGS = 20
JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")


# ---------------------------------------------------------------- ruff

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


def run_ruff(repo: Path, py_files: list[str], findings: list[dict],
             skipped: list[dict]) -> None:
    if not py_files:
        return
    if not ruff_configured(repo):
        skipped.append({"check": "linters(ruff)", "reason": "no ruff config found in repo"})
        return
    cmd = ruff_command()
    if not cmd:
        skipped.append({"check": "linters(ruff)", "reason": "ruff not installed and no uvx fallback"})
        return
    out = try_run([*cmd, "check", "--output-format=json", *py_files],
                  cwd=repo, timeout=120)
    if out is None:
        skipped.append({"check": "linters(ruff)", "reason": "ruff invocation failed"})
        return
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
            f"fix or silence {d.get('code')} at {path}:{row}",
            category="maintainability"))
    if len(diagnostics) > MAX_FINDINGS:
        findings.append(finding(
            "tier0:linters", "info", "-", "-",
            f"+{len(diagnostics) - MAX_FINDINGS} further ruff diagnostics not listed",
            ["ruff check output truncated"], "run ruff locally for the full list"))


# ---------------------------------------------------------------- eslint

def eslint_configured(repo: Path) -> bool:
    for name in (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
                 ".eslintrc.yml", ".eslintrc.yaml", "eslint.config.js",
                 "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts"):
        if (repo / name).is_file():
            return True
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            return "eslintConfig" in json.loads(pkg.read_text(errors="replace"))
        except json.JSONDecodeError:
            return False
    return False


def run_eslint(repo: Path, js_files: list[str], findings: list[dict],
               skipped: list[dict]) -> None:
    if not js_files:
        return
    if not eslint_configured(repo):
        skipped.append({"check": "linters(eslint)", "reason": "no eslint config found in repo"})
        return
    binary = repo / "node_modules" / ".bin" / "eslint"
    if not binary.is_file():
        skipped.append({"check": "linters(eslint)",
                        "reason": "eslint configured but no local install "
                                  "(node_modules/.bin/eslint missing) — never auto-fetched"})
        return
    # eslint exits 1 when findings exist, so call it directly and read stdout
    import subprocess
    try:
        proc = subprocess.run([str(binary), "--format", "json", "--no-warn-ignored",
                               *js_files], cwd=repo, capture_output=True, text=True,
                              timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        skipped.append({"check": "linters(eslint)", "reason": "eslint invocation failed"})
        return
    out = proc.stdout.strip() or None
    if out is None:
        skipped.append({"check": "linters(eslint)", "reason": "eslint produced no output"})
        return
    try:
        results = json.loads(out)
    except json.JSONDecodeError:
        skipped.append({"check": "linters(eslint)", "reason": "eslint output not JSON"})
        return
    count = 0
    for res in results:
        path = (res.get("filePath") or "").removeprefix(str(repo) + "/")
        for msg in res.get("messages", []):
            if count >= MAX_FINDINGS:
                break
            count += 1
            findings.append(finding(
                "tier0:linters",
                "warning" if msg.get("severity", 1) >= 2 else "info",
                path, str(msg.get("line", "?")),
                f"eslint {msg.get('ruleId') or 'fatal'}: {msg.get('message')}",
                [f"eslint {path}:{msg.get('line', '?')}"],
                f"fix or silence {msg.get('ruleId') or 'the error'} at "
                f"{path}:{msg.get('line', '?')}",
                category="maintainability"))


def main() -> None:
    ap = base_argparser("run repo linters on changed files")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    findings: list[dict] = []
    skipped: list[dict] = []

    changed = [f["path"] for f in changed_files(repo, base, head)
               if (repo / f["path"]).is_file()]
    py_files = [p for p in changed if p.endswith(".py")]
    js_files = [p for p in changed if p.endswith(JS_EXTS)]

    if not py_files and not js_files:
        skipped.append({"check": "linters", "reason": "no changed .py/.js/.ts files"})

    run_ruff(repo, py_files, findings, skipped)
    run_eslint(repo, js_files, findings, skipped)

    emit({"check": "run_linters", "findings": findings, "skipped": skipped})


if __name__ == "__main__":
    main()
