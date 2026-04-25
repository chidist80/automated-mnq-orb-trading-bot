"""Tier 3 path lists in prose docs must match the canonical Python list."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_globs(claude_dir: Path) -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "tier3_paths_under_test",
        claude_dir / "hooks" / "tier3_paths.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.TIER3_GLOBS)


def test_project_claude_md_lists_all_tier3_paths(repo_root: Path, claude_dir: Path) -> None:
    canonical = _load_globs(claude_dir)
    body = (repo_root / "CLAUDE.md").read_text()
    for glob in canonical:
        # Allow either the glob form ("bot/execution/**") or the prefix-form
        # ("bot/execution/") in prose.
        prose_form = glob.rstrip("*").rstrip("/")
        assert glob in body or (prose_form + "/") in body or prose_form in body, (
            f"Canonical Tier 3 path {glob!r} not cited in CLAUDE.md"
        )


def test_deepreview_command_references_canonical_list(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    assert "tier3_paths.py" in body, (
        "/deepreview must reference the canonical Tier 3 list at .claude/hooks/tier3_paths.py"
    )
