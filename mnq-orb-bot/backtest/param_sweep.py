"""
Parameter sweep engine.

Runs the backtester across a grid of parameter combinations to
find the optimal configuration. Outputs a ranked table and heatmaps.

IMPORTANT: Parameter optimization without walk-forward validation
is curve-fitting. Always run walk_forward.py on the winning params.

Usage:
    from backtest.param_sweep import run_sweep
    results = run_sweep("data/mnq_15m.csv")
"""

import itertools
import copy
import yaml
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import logging

from backtest.backtester import Backtester

logger = logging.getLogger(__name__)


# Default parameter grid — override by passing custom grid
DEFAULT_GRID = {
    # Opening Range period (minutes)
    "opening_range.period_minutes": [15],  # Keep fixed for now, 15m is consensus

    # OR size filters
    "opening_range.min_size_points": [15, 20, 30],

    # Retest tolerance
    "orb_breakout.retest_tolerance": [3, 5, 8],

    # Retest timeout
    "orb_breakout.retest_timeout_bars": [4, 6, 8],

    # Stop cap
    "orb_breakout.stop_max_points": [80, 100, 120],

    # Target R:R
    "orb_breakout.target.fixed_rr": [0.8, 1.0, 1.5],

    # RSI thresholds
    "confluences.rsi.overbought": [65, 70, 75],

    # EMA period
    "ema_continuation.ema_period": [8, 9, 12],

    # Trading window end
    "schedule.trading_end": ["11:30", "12:00", "13:00", "14:00"],
}

# Reduced grid for quick sweeps
QUICK_GRID = {
    "opening_range.min_size_points": [15, 25],
    "orb_breakout.retest_tolerance": [3, 5],
    "orb_breakout.target.fixed_rr": [1.0, 1.5],
    "confluences.rsi.overbought": [70, 75],
    "schedule.trading_end": ["12:00", "14:00"],
}


@dataclass
class SweepResult:
    """Result of a single parameter combination."""
    params: dict
    total_trades: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    avg_winner: float
    avg_loser: float
    max_consecutive_losses: int


def _set_nested(config: dict, dotted_key: str, value: Any) -> dict:
    """Set a value in a nested dict using dot notation.

    Example: _set_nested(config, "orb_breakout.target.fixed_rr", 1.5)
    """
    keys = dotted_key.split(".")
    d = config
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
    return config


def run_sweep(
    data_path: str | Path,
    strategy_config: str = "config/strategy_params.yaml",
    risk_config: str = "config/risk_params.yaml",
    grid: dict | None = None,
    top_n: int = 20,
    min_trades: int = 30,
) -> pd.DataFrame:
    """Run parameter sweep across all grid combinations.

    Args:
        data_path: Path to historical data
        strategy_config: Base strategy config path
        risk_config: Risk config path
        grid: Parameter grid (dotted key → list of values). Uses DEFAULT_GRID if None.
        top_n: Show top N results
        min_trades: Minimum trades to consider a result valid

    Returns:
        DataFrame of results sorted by profit factor
    """
    if grid is None:
        grid = DEFAULT_GRID

    # Load base config
    with open(strategy_config) as f:
        base_strategy = yaml.safe_load(f)

    # Generate all combinations
    keys = list(grid.keys())
    value_lists = list(grid.values())
    combos = list(itertools.product(*value_lists))

    logger.info(f"Parameter sweep: {len(combos)} combinations across {len(keys)} parameters")

    results = []

    for i, combo in enumerate(combos):
        # Create modified config
        config = copy.deepcopy(base_strategy)
        param_dict = {}
        for key, val in zip(keys, combo):
            _set_nested(config, key, val)
            param_dict[key] = val

        if (i + 1) % 10 == 0 or i == 0:
            logger.info(f"  Running combination {i+1}/{len(combos)}")

        try:
            bt = Backtester(config, risk_config)
            bt_results = bt.run(data_path)

            if bt_results.total_trades < min_trades:
                continue

            results.append(SweepResult(
                params=param_dict,
                total_trades=bt_results.total_trades,
                win_rate=bt_results.win_rate,
                profit_factor=bt_results.profit_factor,
                total_pnl=bt_results.total_pnl,
                max_drawdown=bt_results.max_drawdown,
                avg_winner=bt_results.avg_winner,
                avg_loser=bt_results.avg_loser,
                max_consecutive_losses=bt_results.max_consecutive_losses,
            ))

        except Exception as e:
            logger.warning(f"  Combo {i+1} failed: {e}")
            continue

    if not results:
        logger.error("No valid results from parameter sweep")
        return pd.DataFrame()

    # Convert to DataFrame
    rows = []
    for r in results:
        row = {**r.params}
        row["trades"] = r.total_trades
        row["win_rate"] = r.win_rate
        row["profit_factor"] = r.profit_factor
        row["total_pnl"] = r.total_pnl
        row["max_drawdown"] = r.max_drawdown
        row["avg_winner"] = r.avg_winner
        row["avg_loser"] = r.avg_loser
        row["max_consec_losses"] = r.max_consecutive_losses
        rows.append(row)

    df = pd.DataFrame(rows)

    # Sort by profit factor (primary) and total P&L (secondary)
    df = df.sort_values(["profit_factor", "total_pnl"], ascending=[False, False])

    # Print top results
    print("\n" + "=" * 80)
    print(f"PARAMETER SWEEP RESULTS (Top {top_n} of {len(df)} valid combinations)")
    print("=" * 80)

    display_cols = ["win_rate", "profit_factor", "total_pnl", "max_drawdown", "trades"]
    param_cols = [c for c in df.columns if c not in display_cols + ["avg_winner", "avg_loser", "max_consec_losses"]]

    for i, (_, row) in enumerate(df.head(top_n).iterrows()):
        params_str = " | ".join(f"{k}={row[k]}" for k in param_cols)
        print(f"\n  #{i+1}: WR={row['win_rate']:.0%}  PF={row['profit_factor']:.2f}  "
              f"P&L=${row['total_pnl']:,.0f}  DD=${row['max_drawdown']:,.0f}  "
              f"Trades={row['trades']}")
        print(f"       {params_str}")

    print("\n" + "=" * 80)

    return df


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m backtest.param_sweep <data_path> [--quick]")
        sys.exit(1)

    grid = QUICK_GRID if "--quick" in sys.argv else DEFAULT_GRID
    df = run_sweep(sys.argv[1], grid=grid)

    # Save results
    output = Path("backtest_sweep_results.csv")
    df.to_csv(output, index=False)
    print(f"\nResults saved to {output}")
