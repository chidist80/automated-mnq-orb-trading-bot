#!/usr/bin/env python3
"""PostToolUse hook — emit a Tier 3 reminder when an Edit/Write touched a
real-money path. Signaling only (always exits 0). Silent on non-Tier 3 edits.

Contract: JSON on stdin with `tool_name` and `tool_input.file_path`. Emits to
stderr only; never blocks the turn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the sibling tier3_paths module importable.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from tier3_paths import is_tier3_path  # type: ignore  # noqa: E402
except Exception:
    sys.exit(0)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in {"Edit", "Write", "MultiEdit"}:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0

    if is_tier3_path(file_path):
        sys.stderr.write(
            f"[hygiene] Tier 3 path touched: {file_path}\n"
            f"[hygiene] Reminder: invoke /deepreview at the next task boundary "
            f"before claiming this work complete.\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
