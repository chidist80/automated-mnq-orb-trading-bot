"""Canonical Tier 3 file list for the MNQ ORB bot.

Tier 3 = real-money paths. Edits to these files require /deepreview before
the task can be declared complete. See docs/superpowers/specs/
2026-04-25-execution-hygiene-design.md §3.2.

Anything that imports this module is the canonical reader. CLAUDE.md and
/deepreview prose copies are validated against this list by
tests/hygiene/test_tier3_consistency.py.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

TIER3_GLOBS: tuple[str, ...] = (
    "bot/execution/**",
    "bot/watchdog.py",
    "bot/risk_manager.py",
    "bot/health_monitor.py",
    "bot/signal_generator.py",
    "backtest/strategies/core.py",
    "config/risk_params.yaml",
    "config/strategy_params.yaml",
)


def _repo_relative(path: str) -> str | None:
    """Return path relative to mnq-orb-bot/ if it lives inside, else None."""
    p = Path(path)
    if not p.is_absolute():
        return path.replace("\\", "/")
    here = Path(__file__).resolve().parents[2]  # .claude/hooks/tier3_paths.py -> mnq-orb-bot/
    try:
        return p.resolve().relative_to(here).as_posix()
    except ValueError:
        return None


def is_tier3_path(path: str) -> bool:
    """True if `path` matches any Tier 3 glob.

    Accepts relative paths (matched directly) or absolute paths (relativized
    against mnq-orb-bot/ first). Paths outside the repo return False.
    """
    rel = _repo_relative(path)
    if rel is None:
        return False
    for pattern in TIER3_GLOBS:
        if fnmatch.fnmatch(rel, pattern):
            return True
        # fnmatch doesn't honor `**` recursive globs; emulate by stripping the
        # trailing /** and matching prefix-ish.
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
    return False
