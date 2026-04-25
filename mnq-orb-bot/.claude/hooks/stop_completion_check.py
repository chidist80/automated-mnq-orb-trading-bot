#!/usr/bin/env python3
"""Stop hook — if the assistant's final turn includes a completion-claim phrase
but no fresh pytest invocation was issued in that same turn, emit a reminder
to invoke superpowers:verification-before-completion.

Signaling only (always exits 0).

Contract: JSON on stdin with `transcript_path` pointing to the session's
JSONL transcript. Each line is one entry with `type` ∈ {assistant, user,
tool_result, ...}. We only inspect the LAST assistant turn (everything
between the last user message and EOF).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CLAIM_PATTERNS = [
    r"\btests?\s+pass(es|ed|ing)?\b",
    r"\ball\s+tests?\s+(pass|green)\b",
    r"\b(it\s+)?works\s+now\b",
    r"\ball\s+green\b",
    r"\bworking\s+as\s+expected\b",
    r"\b(implementation|task|feature)\s+(is\s+)?(complete|done|finished)\b",
    r"\bbug\s+is\s+fixed\b",
]
CLAIM_RE = re.compile("|".join(CLAIM_PATTERNS), re.IGNORECASE)

PYTEST_RE = re.compile(r"\bpytest\b|\bpython\s+-m\s+pytest\b", re.IGNORECASE)


def _last_assistant_turn(lines: list[dict]) -> list[dict]:
    """Return the entries from the LAST user message onward."""
    last_user = -1
    for i, entry in enumerate(lines):
        if entry.get("type") == "user":
            last_user = i
    return lines[last_user + 1:] if last_user >= 0 else lines


def _entry_text(entry: dict) -> str:
    """Flatten any text-bearing fields of an entry into one searchable string."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            if block.get("type") == "tool_use":
                tool_input = block.get("input") or {}
                # Capture the full bash command text so PYTEST_RE can match.
                cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
                if cmd:
                    parts.append(str(cmd))
    elif isinstance(content, str):
        parts.append(content)
    if entry.get("type") == "tool_result":
        c = entry.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0
    p = Path(transcript_path)
    if not p.exists():
        return 0

    lines: list[dict] = []
    try:
        for raw_line in p.read_text().splitlines():
            if not raw_line.strip():
                continue
            try:
                lines.append(json.loads(raw_line))
            except Exception:
                continue
    except Exception:
        return 0

    turn = _last_assistant_turn(lines)
    text = "\n".join(_entry_text(e) for e in turn)
    if not text.strip():
        return 0

    if not CLAIM_RE.search(text):
        return 0
    if PYTEST_RE.search(text):
        return 0

    sys.stderr.write(
        "[hygiene] Completion claim detected without a fresh pytest invocation in this turn.\n"
        "[hygiene] Re-check superpowers:verification-before-completion before closing the turn.\n"
        "[hygiene] If this is a docs-only or non-code change, ignore.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
