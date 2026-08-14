#!/usr/bin/env python3
"""Team context: custom rules, learnings, and code-guideline files.

The lightweight answer to CodeRabbit's knowledge base (learnings + path
instructions + auto-detected guideline files) and Greptile's custom rules.
Everything is plain markdown in/beside the repo — no database, no embeddings:

  .driftguard/rules.md        plain-English review rules; optional path scoping via
                              a `## path: <glob>` heading; negative rules ("never
                              flag …") act as Graphite-style filters
  .driftguard/learnings.md    append-only memory written by /driftguard:learn
  CLAUDE.md, AGENTS.md, .cursorrules, .github/copilot-instructions.md
                              auto-detected coding guidelines

Output: a single JSON object {sources, markdown} — `markdown` is fed verbatim to
every Tier 1 subagent so rules/learnings steer all of them.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import emit  # noqa: E402

MAX_PER_FILE = 3000
GUIDELINE_FILES = ["CLAUDE.md", "AGENTS.md", ".cursorrules",
                   ".github/copilot-instructions.md"]
RULES_FILE = ".driftguard/rules.md"
LEARNINGS_FILE = ".driftguard/learnings.md"
PATH_HEADING = re.compile(r"^##\s+path:\s*(\S+)\s*$", re.M)


def read_capped(path: Path) -> tuple[str, bool]:
    text = path.read_text(errors="replace").strip()
    return text[:MAX_PER_FILE], len(text) > MAX_PER_FILE


def rules_for_paths(text: str, changed: list[str]) -> str:
    """Split rules.md into global rules + path-scoped sections matching `changed`."""
    sections: list[str] = []
    matches = list(PATH_HEADING.finditer(text))
    if not matches:
        return text
    global_part = text[:matches[0].start()].strip()
    if global_part:
        sections.append(global_part)
    for i, m in enumerate(matches):
        glob = m.group(1)
        body = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        if not changed or any(fnmatch.fnmatch(c, glob) for c in changed):
            sections.append(f"Rules for `{glob}`:\n{body.strip()}")
    return "\n\n".join(sections)


def main() -> None:
    ap = argparse.ArgumentParser(description="collect team rules, learnings, guidelines")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--files", default="", help="comma-separated changed files (path rule scoping)")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    changed = [f for f in args.files.split(",") if f.strip()]

    sources: list[dict] = []
    parts: list[str] = []

    rules = repo / RULES_FILE
    if rules.is_file():
        text, truncated = read_capped(rules)
        scoped = rules_for_paths(text, changed)
        if scoped.strip():
            sources.append({"path": RULES_FILE, "kind": "custom-rules", "truncated": truncated})
            parts.append(f"# Custom review rules ({RULES_FILE})\n{scoped}")

    learnings = repo / LEARNINGS_FILE
    if learnings.is_file():
        text, truncated = read_capped(learnings)
        if text:
            entries = [l for l in text.splitlines() if l.strip().startswith("- ")]
            sources.append({"path": LEARNINGS_FILE, "kind": "learnings",
                            "entries": len(entries), "truncated": truncated})
            parts.append(f"# Learnings from past reviews ({LEARNINGS_FILE})\n{text}")

    for rel in GUIDELINE_FILES:
        p = repo / rel
        if p.is_file():
            text, truncated = read_capped(p)
            if text:
                sources.append({"path": rel, "kind": "guidelines", "truncated": truncated})
                parts.append(f"# Coding guidelines ({rel})\n{text}")

    markdown = "\n\n".join(parts)
    if not markdown:
        markdown = ("No team context found (no .driftguard/rules.md, "
                    ".driftguard/learnings.md, or guideline files).")
    emit({"sources": sources, "markdown": markdown})


if __name__ == "__main__":
    main()
