"""Stop hook signals when a completion claim is made without fresh test evidence."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _make_transcript(tmp_path: Path, lines: list[dict]) -> Path:
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return p


def _run_hook(claude_dir: Path, repo_root: Path, transcript: Path) -> tuple[int, str, str]:
    script = claude_dir / "hooks" / "stop_completion_check.py"
    payload = {"hook_event_name": "Stop", "transcript_path": str(transcript)}
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


def test_claim_without_test_run_emits_warning(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done. Tests pass."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert "[hygiene]" in err
    assert "verification-before-completion" in err


def test_claim_with_pytest_in_turn_silent(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "pytest tests/unit/ -v"}}]}},
        {"type": "tool_result", "tool_use_id": "x", "content": "5 passed"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "All tests pass — done."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert err.strip() == "", f"Claim with fresh pytest should be silent; got: {err!r}"


def test_no_claim_silent(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the file."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert err.strip() == ""


def test_missing_transcript_path_silent(claude_dir, repo_root, tmp_path) -> None:
    script = claude_dir / "hooks" / "stop_completion_check.py"
    proc = subprocess.run(
        ["python3", str(script)],
        input=json.dumps({"hook_event_name": "Stop"}),
        text=True,
        capture_output=True,
        cwd=str(repo_root),
    )
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""
