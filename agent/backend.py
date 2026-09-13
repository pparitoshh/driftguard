"""LLM backend: headless `claude -p` via subprocess, plus reply parsing."""

import json
import os
import subprocess

_TIMEOUT_S = 300
DEFAULT_MODEL = "sonnet"


def chat(system: str, transcript: list[dict], usage: dict | None = None) -> str:
    """Send system prompt + full transcript; return raw reply text.

    When `usage` is given, token counts and cost from the CLI's JSON output
    are accumulated into it (calls, input_tokens, output_tokens, cost_usd).

    Stateless by design: the framework owns memory, which keeps the loop
    testable with a scripted backend.
    """
    turns = [
        ("AGENT: " if m["role"] == "agent" else "OBSERVATION: ") + m["content"]
        for m in transcript
    ]
    prompt = system + "\n\n" + "\n\n".join(turns)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json",
             "--model", os.environ.get("DRIFTGUARD_MODEL", DEFAULT_MODEL)],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {_TIMEOUT_S}s")
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        reply = {"is_error": True, "result": proc.stdout}
    if usage is not None and isinstance(reply.get("usage"), dict):
        add_usage(usage, reply)
    if proc.returncode != 0 or reply.get("is_error") or not isinstance(reply.get("result"), str):
        detail = (str(reply.get("result") or "").strip() or proc.stderr.strip()).splitlines()
        raise RuntimeError("claude CLI error: " + (detail[0] if detail else f"exit {proc.returncode}"))
    return reply["result"]


def add_usage(usage: dict, reply: dict) -> None:
    tokens = reply["usage"]
    usage["calls"] = usage.get("calls", 0) + 1
    usage["input_tokens"] = usage.get("input_tokens", 0) + sum(
        tokens.get(key, 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    usage["output_tokens"] = usage.get("output_tokens", 0) + tokens.get("output_tokens", 0)
    usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + reply.get("total_cost_usd", 0.0), 6)


def total_tokens(usage: dict) -> int:
    return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)


def loads_json(text: str):
    """Parse JSON from a reply, tolerating ```json fences."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return json.loads("\n".join(lines))


def parse_action(text: str) -> dict:
    """Parse one agent turn; ValueError on any protocol deviation."""
    action = loads_json(text)
    if not isinstance(action, dict) or not isinstance(action.get("action"), str):
        raise ValueError("expected one JSON object with an 'action' key")
    return action
