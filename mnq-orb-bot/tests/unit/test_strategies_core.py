"""Regression test: backtester output must match Phase 1 baseline before & after refactor.

Run with: pytest tests/unit/test_strategies_core.py -v
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtester import Backtester


PHASE1_DATA = PROJECT_ROOT / "data" / "mnq_15m.parquet"
PHASE1_STRAT = PROJECT_ROOT / "config" / "strategy_params.yaml"
PHASE1_RISK = PROJECT_ROOT / "config" / "risk_params.yaml"


def test_phase1_baseline_total_trades():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert res.total_trades == 365, f"Expected 365 trades, got {res.total_trades}"


def test_phase1_baseline_pnl():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert abs(res.total_pnl - 17429.08) < 1.0, f"P&L drift: got {res.total_pnl:.2f}"


def test_phase1_baseline_profit_factor():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    assert abs(res.profit_factor - 2.66) < 0.05


def test_phase1_baseline_setup_breakdown():
    bt = Backtester(str(PHASE1_STRAT), str(PHASE1_RISK))
    res = bt.run(PHASE1_DATA)
    by_setup = {}
    for t in res.trades:
        by_setup[t.setup] = by_setup.get(t.setup, 0) + 1
    # 240 EMA continuation, 66 ORB breakout, 59 inverse ORB
    assert by_setup["ema_continuation"] == 240
    assert by_setup["orb_breakout"] == 66
    assert by_setup["inverse_orb"] == 59
