"""PostToolUse hook emits a Tier 3 reminder when a Tier 3 path is edited."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run_hook(claude_dir: Path, repo_root: Path, payload: dict) -> tuple[int, str, str]:
    script = claude_dir / "hooks" / "post_tier3_edit.py"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["python3", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _payload(tool: str, file_path: str) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
    }


def test_tier3_edit_emits_reminder(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Edit", "bot/execution/ibkr_executor.py"))
    assert rc == 0
    assert "[hygiene]" in err
    assert "Tier 3" in err
    assert "/deepreview" in err


def test_non_tier3_edit_silent(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Edit", "bot/main.py"))
    assert rc == 0
    assert err.strip() == "", f"Non-Tier 3 edit should be silent; got: {err!r}"


def test_write_tool_also_handled(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Write", "bot/risk_manager.py"))
    assert rc == 0
    assert "Tier 3" in err


def test_tool_without_file_path_silent(claude_dir: Path, repo_root: Path) -> None:
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    rc, _, err = _run_hook(claude_dir, repo_root, payload)
    assert rc == 0
    assert err.strip() == ""


def test_malformed_input_does_not_crash(claude_dir: Path, repo_root: Path) -> None:
    script = claude_dir / "hooks" / "post_tier3_edit.py"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["python3", str(script)],
        input="not json at all",
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    assert proc.returncode == 0
