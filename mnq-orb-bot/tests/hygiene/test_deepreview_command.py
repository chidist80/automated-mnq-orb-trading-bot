"""/deepreview slash command file has required structure."""
from __future__ import annotations

from pathlib import Path

REQUIRED_AGENTS = (
    "silent-failure-hunter",
    "pr-test-analyzer",
    "type-design-analyzer",
    "risk-mechanics-reviewer",
)


def test_deepreview_exists(claude_dir: Path) -> None:
    assert (claude_dir / "commands" / "deepreview.md").exists()


def test_deepreview_frontmatter(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    assert body.startswith("---\n")
    assert "description:" in body[:500]


def test_deepreview_lists_all_panel_agents(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    missing = [a for a in REQUIRED_AGENTS if a not in body]
    assert not missing, f"Panel agents missing from /deepreview: {missing}"


def test_deepreview_has_iteration_loop(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    body_lower = body.lower()
    assert "iterate" in body_lower or "loop" in body_lower
    for token in ("critical", "high", "severity"):
        assert token in body_lower, f"Iteration spec missing {token!r}"


def test_deepreview_describes_output_contract(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    for token in ("JSON", "file", "line", "severity"):
        assert token in body
