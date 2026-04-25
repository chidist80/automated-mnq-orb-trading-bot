"""Stale-doc hygiene: files known to be obsolete must stay deleted."""
from __future__ import annotations

from pathlib import Path


def test_global_claude_md_additions_is_deleted(repo_root: Path) -> None:
    """commit b7da8e5 dropped ruflo from Phase 2.

    GLOBAL_CLAUDE_MD_ADDITIONS.md described ruflo as the build factory and is
    now actively misleading. It must not return.
    """
    stale = repo_root / "GLOBAL_CLAUDE_MD_ADDITIONS.md"
    assert not stale.exists(), (
        f"{stale} is stale ruflo content — see "
        f"docs/superpowers/specs/2026-04-25-execution-hygiene-design.md §3.6"
    )
