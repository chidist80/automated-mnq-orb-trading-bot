"""Shared fixtures for hygiene tests."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the path to mnq-orb-bot/ (the dir containing CLAUDE.md and pyproject.toml)."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "CLAUDE.md").exists():
            return candidate
    raise RuntimeError("Could not locate mnq-orb-bot/ root (need CLAUDE.md + pyproject.toml)")


@pytest.fixture(scope="session")
def claude_dir(repo_root: Path) -> Path:
    """Return mnq-orb-bot/.claude/."""
    return repo_root / ".claude"


@pytest.fixture(scope="session")
def global_claude_md() -> Path:
    """Return ~/.claude/CLAUDE.md."""
    return Path.home() / ".claude" / "CLAUDE.md"
