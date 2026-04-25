"""Global CLAUDE.md hygiene: ≤110 lines, three new triple-format sections."""
from __future__ import annotations

import re
from pathlib import Path


def _read(global_claude_md: Path) -> str:
    return global_claude_md.read_text()


def test_under_line_cap(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    line_count = len(body.splitlines())
    assert line_count <= 110, f"Global CLAUDE.md is {line_count} lines; cap is 110 (spec §3.1)."


def test_verification_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Verification" in body or "verification-before-completion" in body
    assert "verification-before-completion" in body


def test_research_first_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Research" in body or "research" in body
    assert "context7" in body


def test_multi_aspect_review_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Multi-aspect" in body or "multi-aspect" in body or "multi-aspect" in body.lower()
    # Universal floor must reference /audit-project
    assert "audit-project" in body


def test_rule_reason_scope_format_used(global_claude_md: Path) -> None:
    """At least one new behavioral section uses rule + reason + scope wording."""
    body = _read(global_claude_md)
    assert re.search(r"\bRule\b", body), "rule+reason+scope format missing 'Rule'"
    assert re.search(r"\b(Reason|Why)\b", body), "format missing 'Reason' or 'Why'"
    assert re.search(r"\bScope\b", body), "format missing 'Scope'"


def test_existing_routing_table_preserved(global_claude_md: Path) -> None:
    """Don't accidentally clobber the plugin auto-routing table."""
    body = _read(global_claude_md)
    assert "Plugin & Skill Auto-Routing" in body or "Plugin" in body
    # Spot-check a few existing plugin entries
    for plugin in ("/ralph-loop", "/review-pr", "/audit-project", "/ship", "/next-task"):
        assert plugin in body, f"Existing plugin reference {plugin} clobbered"
