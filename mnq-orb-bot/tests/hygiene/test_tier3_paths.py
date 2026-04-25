"""Canonical Tier 3 path list lives in .claude/hooks/tier3_paths.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_tier3_module(claude_dir: Path):
    spec = importlib.util.spec_from_file_location(
        "tier3_paths_under_test",
        claude_dir / "hooks" / "tier3_paths.py",
    )
    assert spec and spec.loader, "Could not load tier3_paths module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tier3_globs_present(claude_dir: Path) -> None:
    module = _load_tier3_module(claude_dir)
    globs = module.TIER3_GLOBS
    assert isinstance(globs, tuple)
    assert all(isinstance(g, str) for g in globs)
    expected = {
        "bot/execution/**",
        "bot/watchdog.py",
        "bot/risk_manager.py",
        "bot/health_monitor.py",
        "bot/signal_generator.py",
        "backtest/strategies/core.py",
        "config/risk_params.yaml",
        "config/strategy_params.yaml",
    }
    assert set(globs) == expected, f"Tier 3 globs drifted: {set(globs) ^ expected}"


def test_is_tier3_path_matches_known_files(claude_dir: Path) -> None:
    module = _load_tier3_module(claude_dir)
    is_tier3 = module.is_tier3_path
    assert is_tier3("bot/execution/ibkr_executor.py") is True
    assert is_tier3("bot/execution/order_state.py") is True
    assert is_tier3("bot/watchdog.py") is True
    assert is_tier3("config/risk_params.yaml") is True
    assert is_tier3("backtest/strategies/core.py") is True
    # Negative cases
    assert is_tier3("bot/main.py") is False
    assert is_tier3("tests/unit/test_executor.py") is False
    assert is_tier3("docs/PHASE2_DEPLOYMENT.md") is False


def test_is_tier3_path_handles_absolute_paths(claude_dir: Path, repo_root: Path) -> None:
    module = _load_tier3_module(claude_dir)
    abs_path = str(repo_root / "bot" / "execution" / "ibkr_executor.py")
    assert module.is_tier3_path(abs_path) is True
    abs_outside = "/tmp/scratch.py"
    assert module.is_tier3_path(abs_outside) is False
