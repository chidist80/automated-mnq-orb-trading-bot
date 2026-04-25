"""Smoke test: hygiene fixtures resolve the right repo root."""
from __future__ import annotations

from pathlib import Path


def test_repo_root_resolves(repo_root: Path) -> None:
    assert (repo_root / "pyproject.toml").exists()
    assert (repo_root / "CLAUDE.md").exists()


def test_claude_dir_exists(claude_dir: Path) -> None:
    assert claude_dir.exists()
    assert claude_dir.is_dir()


def test_global_claude_md_exists(global_claude_md: Path) -> None:
    assert global_claude_md.exists(), f"Expected {global_claude_md} to exist"
