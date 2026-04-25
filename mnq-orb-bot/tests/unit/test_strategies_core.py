"""Regression test: executable backtester output must stay stable before refactor.

Run with: pytest tests/unit/test_strategies_core.py -v

Corrective baseline locked 2026-04-25 after a confidence audit surfaced and fixed:
  - Synthetic 09:30 OR bars in 2023 were polluting the inverse_orb percentile
    and regime_filter rolling windows. data_loader._drop_invalid_or_days now filters
    synthetic and late-start sessions at load time.
  - risk_params.execution: slippage_points 1.0 → 1.5 (round-trip), commission_per_side
    0.25 → 0.47 (IBKR all-in MNQ rate). Both tightenings make the backtest more
    pessimistic than live so we don't over-promise.
  - backtest entries now use executable resting-order semantics instead of using a
    completed bar's close to decide that an earlier intrabar fill occurred.

This baseline intentionally does NOT clear Phase 1 gates. It is locked so the
shared-core extraction cannot accidentally drift while the strategy is redesigned.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtester import Backtester


PHASE1_DATA = PROJECT_ROOT / "data" / "mnq_15m.parquet"
PHASE1_STRAT = PROJECT_ROOT / "config" / "strategy_params.yaml"
PHASE1_RISK = PROJECT_ROOT / "config" / "risk_params.yaml"

pytestmark = pytest.mark.skipif(
    not PHASE1_DATA.exists(),
    reason="Phase 1 baseline requires local historical data/mnq_15m.parquet",
)


def test_phase1_baseline_total_trades():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert res.total_trades == 13, f"Expected 13 trades, got {res.total_trades}"


def test_phase1_baseline_pnl():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert abs(res.total_pnl - (-529.61)) < 1.0, f"P&L drift: got {res.total_pnl:.2f}"


def test_phase1_baseline_profit_factor():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert abs(res.profit_factor - 0.19) < 0.05


def test_phase1_baseline_setup_breakdown():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    by_setup = {}
    for t in res.trades:
        by_setup[t.setup] = by_setup.get(t.setup, 0) + 1
    assert by_setup.get("ema_continuation", 0) == 4
    assert by_setup.get("orb_breakout", 0) == 0
    assert by_setup.get("inverse_orb", 0) == 9
