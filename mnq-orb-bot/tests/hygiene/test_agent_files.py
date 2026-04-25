"""Custom agent files have required structure."""
from __future__ import annotations

from pathlib import Path

REQUIRED_AREAS = (
    "Contract arithmetic",
    "OCA",
    "Session boundaries",
    "Look-ahead",
    "reconciliation",
    "Mock fidelity",
    "Operator UX",
)


def test_risk_mechanics_reviewer_exists(claude_dir: Path) -> None:
    p = claude_dir / "agents" / "risk-mechanics-reviewer.md"
    assert p.exists(), "risk-mechanics-reviewer.md missing"


def test_risk_mechanics_reviewer_has_frontmatter(claude_dir: Path) -> None:
    p = claude_dir / "agents" / "risk-mechanics-reviewer.md"
    body = p.read_text()
    assert body.startswith("---\n"), "Agent file must start with YAML frontmatter"
    assert "name:" in body[:200]
    assert "description:" in body[:500]


def test_risk_mechanics_reviewer_covers_all_areas(claude_dir: Path) -> None:
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    missing = [a for a in REQUIRED_AREAS if a.lower() not in body.lower()]
    assert not missing, f"Missing charter areas: {missing}"


def test_risk_mechanics_reviewer_specifies_output_schema(claude_dir: Path) -> None:
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    for token in ("severity", "file", "line", "JSON"):
        assert token in body, f"Output schema missing {token!r}"


def test_risk_mechanics_reviewer_cites_mnq_specs(claude_dir: Path) -> None:
    """The agent must know MNQ contract math to flag tick-value drift."""
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    for fact in ("$2/point", "$0.50/tick"):
        assert fact in body, f"Missing MNQ spec fact: {fact}"
