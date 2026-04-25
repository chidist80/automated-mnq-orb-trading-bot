"""Project settings.json hygiene: no ruflo, three signaling hooks present."""
from __future__ import annotations

import json
from pathlib import Path


def _load(claude_dir: Path) -> dict:
    return json.loads((claude_dir / "settings.json").read_text())


def test_no_ruflo_references(claude_dir: Path) -> None:
    raw = (claude_dir / "settings.json").read_text().lower()
    forbidden = ("ruflo", "claude-flow", "claudeflow")
    found = [w for w in forbidden if w in raw]
    assert not found, f"Stale ruflo refs in settings.json: {found}"


def test_required_hooks_present(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    hooks = settings.get("hooks", {})
    assert "SessionStart" in hooks, "SessionStart hook missing (spec §3.5)"
    assert "PostToolUse" in hooks, "PostToolUse hook missing (spec §3.5)"
    assert "Stop" in hooks, "Stop hook missing (spec §3.5)"


def test_hook_commands_reference_project_scripts(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    hooks = settings.get("hooks", {})

    def _all_commands(event: str) -> list[str]:
        out = []
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                if h.get("type") == "command":
                    out.append(h.get("command", ""))
        return out

    session_cmds = " ".join(_all_commands("SessionStart"))
    assert "session_start.sh" in session_cmds

    post_cmds = " ".join(_all_commands("PostToolUse"))
    assert "post_tier3_edit.py" in post_cmds

    stop_cmds = " ".join(_all_commands("Stop"))
    assert "stop_completion_check.py" in stop_cmds


def test_post_tooluse_matches_edit_write(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    post = settings.get("hooks", {}).get("PostToolUse", [])
    matchers = [entry.get("matcher", "") for entry in post]
    matched = " ".join(matchers)
    for tool in ("Edit", "Write", "MultiEdit"):
        assert tool in matched, f"PostToolUse matcher missing tool {tool}: {matchers}"
