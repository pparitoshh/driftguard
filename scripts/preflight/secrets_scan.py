#!/usr/bin/env python3
"""Secrets / accidentally-committed-credential pre-flight.

Scans ADDED lines of the diff for recognizable credential formats and generic
secret assignments. The paid reviewers all ship this (CodeRabbit Security,
Graphite's "accidentally committed code", gitleaks inside CodeRabbit's sandbox);
ours is regex-only, stdlib-only, and never prints the secret itself — evidence
is redacted (first 4 chars + …), matching CodeRabbit's credential-redaction
practice.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (added_lines_by_file, base_argparser, emit, finding,  # noqa: E402
                    redact, resolve_range)

# High-confidence token formats -> (name, error-severity)
TOKEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI/Anthropic-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

# Generic assignment: key = "value" — warning severity, with placeholder exclusions.
# Leading lookbehind allows db_password / DB-PASSWORD style prefixes while still
# refusing matches glued to letters (e.g. "mypassword").
GENERIC_RE = re.compile(
    r"(?i)(?<![A-Za-z])(api[_-]?key|auth[_-]?token|secret(?:[_-]?key)?|passwd|"
    r"password|access[_-]?token|client[_-]?secret|private[_-]?key)"
    r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']")

PLACEHOLDER_RE = re.compile(
    r"(?i)(example|sample|dummy|placeholder|changeme|change[_-]?me|your[_-]|"
    r"xxx|\.\.\.|<[^>]+>|\*\*\*|redacted|not[_-]?a[_-]?real|fake|test[_-]?only|"
    r"insert|replace[_-]?me|todo)")

# References to env/config lookups are safe, not leaks.
SAFE_REF_RE = re.compile(
    r"(os\.environ|getenv|process\.env|os\.getenv|\$\{|\$\(|config\[|settings\.|"
    r"env\(|%s|%\(|\{0\}|vault|paramstore|ssm)")

SKIP_FILE_RE = re.compile(
    r"(?i)(^|/)(\.gitignore|.*\.lock|package-lock\.json|.*\.min\.js|"
    r".*\.(png|jpg|jpeg|gif|ico|svg|woff2?|ttf|pdf|zip|tar|gz))$")


def scan_line(line: str) -> list[tuple[str, str, str]]:
    """Return [(kind, matched_text, severity)] for one added line."""
    hits: list[tuple[str, str, str]] = []
    for name, pat in TOKEN_PATTERNS:
        m = pat.search(line)
        if m:
            hits.append((name, m.group(0), "error"))
    m = GENERIC_RE.search(line)
    if m and not PLACEHOLDER_RE.search(m.group(2)) and not SAFE_REF_RE.search(line):
        hits.append((f"hardcoded {m.group(1)}", m.group(2), "warning"))
    return hits


def main() -> None:
    ap = base_argparser("secrets / accidentally-committed-credential pre-flight")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    base, head = resolve_range(repo, args.base, args.head)

    findings: list[dict] = []
    scanned = 0
    for path, linenos in added_lines_by_file(repo, base, head).items():
        if SKIP_FILE_RE.search(path):
            continue
        full = repo / path
        if not full.is_file():
            continue
        lines = full.read_text(errors="replace").splitlines()
        for n in linenos:
            if n > len(lines):
                continue
            scanned += 1
            for kind, matched, severity in scan_line(lines[n - 1]):
                findings.append(finding(
                    "tier0:secrets_scan", severity, path, str(n),
                    f"{kind} committed in the diff",
                    [f"regex '{kind}' matched at {path}:{n} -> {redact(matched)}"],
                    "remove the credential from the diff, rotate it if it was ever "
                    "real, and load it from the environment / a secret store instead",
                    category="security"))

    emit({"check": "secrets_scan", "findings": findings,
          "skipped": [], "added_lines_scanned": scanned})


if __name__ == "__main__":
    main()
