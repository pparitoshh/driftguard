"""LLM backend: headless `claude -p` via subprocess, plus reply parsing."""

import json
import subprocess

_TIMEOUT_S = 300


def chat(system: str, transcript: list[dict]) -> str:
    """Send system prompt + full transcript; return raw reply text.

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
            ["claude", "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {_TIMEOUT_S}s")
    return proc.stdout


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
