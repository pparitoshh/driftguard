#!/usr/bin/env python3
"""Extract a compact digest of Claude Code session transcripts relevant to a
review range.

Transcripts: ~/.claude/projects/<path-slug>/*.jsonl (path-slug = repo path with
every non-alphanumeric char replaced by '-').

Matching: a session is relevant when its events overlap the commit time window
(--since/--until, plus margin) and/or touch files changed in the range
(--files). Output is a size-capped markdown digest on stdout.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
MAX_SESSIONS = 3
MAX_USER_MSGS = 15
MAX_ASSISTANT_TEXTS = 10
MAX_FILES = 30
USER_TRUNC = 300
ASSISTANT_TRUNC = 250
MIN_ASSISTANT_LEN = 40


def repo_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_events(path: Path) -> list[dict]:
    events = []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") in {"user", "assistant", "system"} and not e.get("isSidechain"):
                events.append(e)
    return events


def extract_signals(events: list[dict]) -> dict:
    """Pull the high-signal bits out of raw transcript events."""
    user_msgs, assistant_texts, files, compactions, branches = [], [], Counter(), [], Counter()
    cwds = Counter()
    for e in events:
        ts = e.get("timestamp")
        if e.get("cwd"):
            cwds[e["cwd"]] += 1
        if e.get("gitBranch"):
            branches[e["gitBranch"]] += 1
        etype = e.get("type")
        if etype == "system":
            if "compact" in str(e.get("subtype", "")):
                compactions.append(ts)
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        if etype == "user":
            # human-typed prompts: string content; tool results are list/toolUseResult
            if isinstance(content, str) and content.strip():
                user_msgs.append((ts, content.strip()))
        elif etype == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    t = (block.get("text") or "").strip()
                    if len(t) >= MIN_ASSISTANT_LEN:
                        assistant_texts.append((ts, t))
                elif block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                    fp = (block.get("input") or {}).get("file_path")
                    if fp:
                        files[fp] += 1
    return {
        "user_msgs": user_msgs,
        "assistant_texts": assistant_texts,
        "files": files,
        "compactions": compactions,
        "branches": branches,
        "cwds": cwds,
    }


def session_score(events: list[dict], sig: dict, since: datetime | None,
                  until: datetime | None, want_files: set[str],
                  margin: timedelta) -> int:
    score = 0
    if since or until:
        lo = (since - margin) if since else datetime.min.replace(tzinfo=timezone.utc)
        hi = (until + margin) if until else datetime.max.replace(tzinfo=timezone.utc)
        for e in events:
            ts = parse_ts(e.get("timestamp"))
            if ts and lo <= ts <= hi:
                score += 1
    else:
        score += 1  # no window given: every repo session is a candidate
    if want_files:
        norm_want = {w.strip("/") for w in want_files}
        for fp in sig["files"]:
            norm = fp.strip("/")
            if any(norm.endswith(w) or w.endswith(norm) for w in norm_want):
                score += 2
    return score


def fmt_ts(ts: str | None) -> str:
    dt = parse_ts(ts)
    return dt.strftime("%m-%d %H:%M") if dt else "??:??"


def build_digest(sessions: list[tuple[Path, dict, int]], budget: int) -> tuple[str, bool]:
    parts: list[str] = []
    truncated = False
    used = 0
    for path, sig, score in sessions:
        header_bits = [f"## Session {path.stem[:8]}"]
        if sig["branches"]:
            header_bits.append(f"branch={sig['branches'].most_common(1)[0][0]}")
        times = [t for t, _ in sig["user_msgs"] if t]
        if times:
            header_bits.append(f"{fmt_ts(min(times))} → {fmt_ts(max(times))}")
        header_bits.append(f"(match score {score})")
        section = [" ".join(header_bits)]

        if sig["user_msgs"]:
            section.append("### User requests (verbatim)")
            for ts, text in sig["user_msgs"][:MAX_USER_MSGS]:
                text = text.replace("\n", " ")[:USER_TRUNC]
                section.append(f"- [{fmt_ts(ts)}] {text}")
            if len(sig["user_msgs"]) > MAX_USER_MSGS:
                section.append(f"- … +{len(sig['user_msgs']) - MAX_USER_MSGS} more")
        if sig["assistant_texts"]:
            section.append("### Assistant plan/summary excerpts")
            for ts, text in sig["assistant_texts"][:MAX_ASSISTANT_TEXTS]:
                text = re.sub(r"\s+", " ", text)[:ASSISTANT_TRUNC]
                section.append(f"- [{fmt_ts(ts)}] {text}")
            if len(sig["assistant_texts"]) > MAX_ASSISTANT_TEXTS:
                section.append(f"- … +{len(sig['assistant_texts']) - MAX_ASSISTANT_TEXTS} more")
        if sig["files"]:
            section.append("### Files edited in session")
            for fp, n in sig["files"].most_common(MAX_FILES):
                section.append(f"- {fp}" + (f" (x{n})" if n > 1 else ""))
        if sig["compactions"]:
            section.append(f"### Session compacted {len(sig['compactions'])}x (early context summarized away)")

        block = "\n".join(section) + "\n"
        if used + len(block.encode()) > budget:
            truncated = True
            break
        parts.append(block)
        used += len(block.encode())
    return "\n".join(parts), truncated


def main() -> None:
    ap = argparse.ArgumentParser(description="digest Claude sessions relevant to a review range")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--since", default=None, help="ISO timestamp of first commit in range")
    ap.add_argument("--until", default=None, help="ISO timestamp of last commit in range")
    ap.add_argument("--files", default="", help="comma-separated changed file paths")
    ap.add_argument("--budget", type=int, default=8192, help="max digest bytes")
    ap.add_argument("--margin-hours", type=float, default=2.0)
    ap.add_argument("--sessions-dir", default=None, help="override transcript dir (testing)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    sessions_dir = (
        Path(args.sessions_dir)
        if args.sessions_dir
        else Path.home() / ".claude" / "projects" / repo_slug(repo)
    )
    if not sessions_dir.is_dir():
        print(f"# driftguard session digest\n\nNo Claude sessions found for {repo} "
              f"(looked in {sessions_dir}).\n")
        return

    since, until = parse_ts(args.since), parse_ts(args.until)
    want_files = {f for f in args.files.split(",") if f.strip()}
    margin = timedelta(hours=args.margin_hours)

    scored: list[tuple[Path, dict, int]] = []
    for path in sorted(sessions_dir.glob("*.jsonl")):
        events = load_events(path)
        if not events:
            continue
        sig = extract_signals(events)
        score = session_score(events, sig, since, until, want_files, margin)
        if score > 0:
            scored.append((path, sig, score))
    scored.sort(key=lambda t: t[2], reverse=True)
    chosen = scored[:MAX_SESSIONS]

    digest, truncated = build_digest(chosen, args.budget)
    header = "# driftguard session digest\n"
    header += (f"repo={repo} sessions_matched={len(scored)} used={len(chosen)}\n\n")
    if not chosen:
        header += "No session overlapped the review range (time window / files).\n"
    print(header + digest)
    if truncated:
        print("\n[digest truncated to byte budget — narrow the range or raise --budget]")


if __name__ == "__main__":
    main()
