"""SessionStart hook emits structured stderr signal with branch + phase context."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run_hook(claude_dir: Path, repo_root: Path) -> tuple[int, str, str]:
    """Run session_start.sh with the project as cwd and return (rc, stdout, stderr)."""
    script = claude_dir / "hooks" / "session_start.sh"
    payload = json.dumps({"hook_event_name": "SessionStart"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["bash", str(script)],
        input=payload,
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_session_start_exits_zero(claude_dir: Path, repo_root: Path) -> None:
    rc, _, _ = _run_hook(claude_dir, repo_root)
    assert rc == 0, "SessionStart hook must not block (signaling only)"


def test_session_start_emits_branch_marker(claude_dir: Path, repo_root: Path) -> None:
    _, _, err = _run_hook(claude_dir, repo_root)
    # Marker prefix MUST be present so Claude treats the lines as guidance.
    assert "[hygiene]" in err, f"Expected [hygiene] marker; got: {err!r}"
    assert "branch" in err.lower(), f"Expected branch info; got: {err!r}"


def test_session_start_emits_phase_pointer(claude_dir: Path, repo_root: Path) -> None:
    _, _, err = _run_hook(claude_dir, repo_root)
    assert "phase" in err.lower(), f"Expected phase pointer; got: {err!r}"
