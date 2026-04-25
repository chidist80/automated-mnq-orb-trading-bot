"""Project CLAUDE.md hygiene: ≤80 lines, no ruflo, required sections present."""
from __future__ import annotations

from pathlib import Path


def _read(repo_root: Path) -> str:
    return (repo_root / "CLAUDE.md").read_text()


def test_line_count_under_cap(repo_root: Path) -> None:
    body = _read(repo_root)
    line_count = len(body.splitlines())
    assert line_count <= 80, (
        f"CLAUDE.md is {line_count} lines; cap is 80 (spec §3.2). "
        f"Opus 4.7 tokenizer is 1.0-1.35x heavier; trim it."
    )


def test_no_ruflo_references(repo_root: Path) -> None:
    body = _read(repo_root).lower()
    forbidden = ("ruflo", "claude-flow", "agentic-qe", "hive-mind")
    found = [w for w in forbidden if w in body]
    assert not found, f"Stale ruflo terms in CLAUDE.md: {found} (commit b7da8e5 dropped ruflo)"


def test_required_sections_present(repo_root: Path) -> None:
    body = _read(repo_root)
    required_headings = (
        "Project Facts",
        "Tier 3",
        "Phase",
        "Mock",
        "shortcut",
        "Brainstorming",
    )
    missing = [h for h in required_headings if h.lower() not in body.lower()]
    assert not missing, f"Missing required sections: {missing}"


def test_tier3_files_listed(repo_root: Path) -> None:
    body = _read(repo_root)
    tier3 = (
        "bot/execution/",
        "bot/watchdog.py",
        "bot/risk_manager.py",
        "bot/health_monitor.py",
        "bot/signal_generator.py",
        "backtest/strategies/core.py",
        "config/risk_params.yaml",
        "config/strategy_params.yaml",
    )
    missing = [p for p in tier3 if p not in body]
    assert not missing, f"Tier 3 paths missing from CLAUDE.md: {missing}"


def test_phase_pointer_present(repo_root: Path) -> None:
    body = _read(repo_root)
    assert "phase2/live-execution" in body, "Phase pointer (branch) missing"
    assert "2026-04-25-phase2-live-execution.md" in body, "Phase plan path missing"
