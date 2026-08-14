"""Shared helpers for driftguard scripts. Stdlib only."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 60) -> str:
    """Run a command, return stdout. Raises RuntimeError on failure."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise RuntimeError(f"binary not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout ({timeout}s): {' '.join(cmd)}")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def try_run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 60) -> str | None:
    """Like run() but returns None instead of raising."""
    try:
        return run(cmd, cwd, timeout)
    except RuntimeError:
        return None


def emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def finding(source: str, severity: str, file: str, line_range: str,
            claim: str, evidence: list[str], action: str,
            category: str = "") -> dict:
    return {
        "source": source,
        "severity": severity,
        "category": category,
        "file": file,
        "line_range": line_range,
        "claim": claim,
        "evidence": evidence,
        "suggested_action": action,
    }


def http_json(url: str, timeout: int = 4) -> tuple[int | None, dict | None]:
    """GET a JSON URL. Returns (status, parsed) or (None, None) when unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None, None


def http_post_json(url: str, payload: dict, timeout: int = 4) -> dict | None:
    """POST JSON, return parsed response; None on any failure."""
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def redact(text: str, keep: int = 4) -> str:
    """Redact a secret for evidence output: first `keep` chars + ellipsis."""
    text = text.strip()
    return text[:keep] + "…" if len(text) > keep else "…"


def resolve_range(repo: Path, base: str | None, head: str | None) -> tuple[str, str]:
    """Resolve base/head to refs that exist. Defaults: merge-base with main/master .. HEAD."""
    head = head or "HEAD"
    if base:
        return base, head
    for candidate in ("origin/main", "main", "origin/master", "master"):
        if try_run(["git", "rev-parse", "--verify", candidate], cwd=repo) is not None:
            mb = try_run(["git", "merge-base", candidate, head], cwd=repo)
            if mb:
                return mb.strip(), head
    # single-commit repo fallback: diff against the empty tree
    return "4b825dc642cb6eb9a060e54bf8d69288fbee4904", head


def diff_args(base: str, head: str) -> list[str]:
    return [f"{base}...{head}"]


def changed_files(repo: Path, base: str, head: str) -> list[dict]:
    """[{status, path}] for the range."""
    out = run(["git", "diff", "--name-status", *diff_args(base, head)], cwd=repo)
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "path": parts[-1]})
    return files


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines_by_file(repo: Path, base: str, head: str) -> dict[str, list[int]]:
    """Map file -> sorted list of added line numbers (in head)."""
    out = run(["git", "diff", "--unified=0", *diff_args(base, head)], cwd=repo)
    result: dict[str, list[int]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, [])
        elif line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m and current:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                result[current].extend(range(start, start + count))
    return {k: sorted(set(v)) for k, v in result.items()}


def diff_added_removed(repo: Path, base: str, head: str,
                       path: str) -> tuple[list[str], list[str]]:
    """(added_lines, removed_lines) for one file, without +/- markers."""
    out = run(["git", "diff", "--unified=0", *diff_args(base, head), "--", path], cwd=repo)
    added, removed = [], []
    for line in out.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def stdlib_modules() -> frozenset[str]:
    return frozenset(getattr(sys, "stdlib_module_names", ()) or dir(sys))


def base_argparser(description: str) -> "argparse.ArgumentParser":
    import argparse
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--repo", default=".", help="target repo path")
    p.add_argument("--base", default=None)
    p.add_argument("--head", default=None)
    return p
