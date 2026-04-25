"""
Walk-forward validation.

Splits historical data into rolling train/test windows to detect
overfitting and validate that backtested edge holds out-of-sample.

A strategy that only works in-sample is curve-fitted garbage.
Walk-forward is the single most important validation step.

Usage:
    from backtest.walk_forward import walk_forward_validate
    results = walk_forward_validate("data/mnq_15m.csv", train_months=8, test_months=4)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import logging

from backtest.data_loader import load_csv, load_parquet
from backtest.backtester import Backtester, BacktestResults

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    """Results for a single train/test window."""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_results: BacktestResults
    test_results: BacktestResults

    @property
    def train_win_rate(self) -> float:
        return self.train_results.win_rate

    @property
    def test_win_rate(self) -> float:
        return self.test_results.win_rate

    @property
    def degradation(self) -> float:
        """How much worse is test vs train performance (profit factor)."""
        train_pf = self.train_results.profit_factor
        test_pf = self.test_results.profit_factor
        if train_pf == 0 or train_pf == float("inf"):
            return 0.0
        return (train_pf - test_pf) / train_pf


@dataclass
class WalkForwardResults:
    """Aggregated walk-forward validation results."""
    windows: list[WalkForwardWindow]
    config: dict

    @property
    def num_windows(self) -> int:
        return len(self.windows)

    @property
    def oos_win_rates(self) -> list[float]:
        """Out-of-sample win rates across all windows."""
        return [w.test_win_rate for w in self.windows]

    @property
    def oos_profit_factors(self) -> list[float]:
        """Out-of-sample profit factors across all windows."""
        return [w.test_results.profit_factor for w in self.windows
                if w.test_results.profit_factor != float("inf")]

    @property
    def oos_total_pnl(self) -> list[float]:
        return [w.test_results.total_pnl for w in self.windows]

    @property
    def avg_degradation(self) -> float:
        return np.mean([w.degradation for w in self.windows])

    @property
    def windows_profitable(self) -> int:
        return sum(1 for w in self.windows if w.test_results.total_pnl > 0)

    @property
    def pass_rate(self) -> float:
        """% of OOS windows that were profitable."""
        return self.windows_profitable / self.num_windows if self.num_windows else 0.0

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "WALK-FORWARD VALIDATION RESULTS",
            "=" * 60,
            f"Windows:              {self.num_windows}",
            f"OOS Profitable:       {self.windows_profitable}/{self.num_windows} ({self.pass_rate:.0%})",
            f"Avg OOS Win Rate:     {np.mean(self.oos_win_rates):.1%}",
            f"Avg OOS Profit Factor:{np.mean(self.oos_profit_factors):.2f}" if self.oos_profit_factors else "",
            f"Avg OOS P&L:          ${np.mean(self.oos_total_pnl):,.2f}",
            f"Avg Degradation:      {self.avg_degradation:.1%}",
            "",
            "PER WINDOW:",
        ]

        for w in self.windows:
            lines.append(
                f"  #{w.window_id}: Train {w.train_start}→{w.train_end} | "
                f"Test {w.test_start}→{w.test_end} | "
                f"WR {w.test_win_rate:.0%} | "
                f"PF {w.test_results.profit_factor:.2f} | "
                f"P&L ${w.test_results.total_pnl:,.0f} | "
                f"Trades {w.test_results.total_trades}"
            )

        lines.append("=" * 60)

        # Verdict (revised 2026-04-25 — see VALIDATION.md for rationale).
        # The strategy's structure (~70% of trades are EMA continuation, a low-WR /
        # high-R:R trend-following setup) makes the inherited 60% blended-WR gate
        # mathematically incompatible with the design. Profit factor + windows-
        # profitable + positive avg P&L are the right edge & robustness measures.
        avg_oos_pf = np.mean(self.oos_profit_factors) if self.oos_profit_factors else 0.0
        avg_oos_pnl = np.mean(self.oos_total_pnl)
        if self.pass_rate >= 0.75 and avg_oos_pf >= 1.5 and avg_oos_pnl > 0:
            lines.append("✅ PASS — Strategy shows robust out-of-sample edge")
        elif self.pass_rate >= 0.50:
            lines.append("⚠️  MARGINAL — Some OOS windows profitable, needs investigation")
        else:
            lines.append("❌ FAIL — Strategy does not hold out-of-sample. Likely curve-fitted.")

        report = "\n".join(lines)
        print(report)
        return report


def walk_forward_validate(
    data_path: str | Path,
    strategy_config: str = "config/strategy_params.yaml",
    risk_config: str = "config/risk_params.yaml",
    train_months: int = 8,
    test_months: int = 4,
    step_months: int = 2,
) -> WalkForwardResults:
    """Run walk-forward validation.

    Rolls through the data with overlapping train/test windows:
      Window 1: Train months 1-8,  Test months 9-12
      Window 2: Train months 3-10, Test months 11-14
      ...

    Args:
        data_path: Path to historical data
        strategy_config: Strategy YAML path
        risk_config: Risk YAML path
        train_months: Training period length in months
        test_months: Test period length in months
        step_months: How far to advance the window each iteration
    """
    path = Path(data_path)
    if path.suffix == ".parquet":
        df = load_parquet(path)
    else:
        df = load_csv(path)

    # Get date range
    start_date = df.index[0].date()
    end_date = df.index[-1].date()
    total_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)

    if total_months < train_months + test_months:
        raise ValueError(
            f"Data covers {total_months} months but need {train_months + test_months} "
            f"for train+test. Get more data."
        )

    # Match the index's tz so comparisons work whether the data is naive or tz-aware.
    index_tz = df.index.tz
    windows = []
    window_id = 0
    current_start = pd.Timestamp(start_date, tz=index_tz)

    while True:
        train_start = current_start
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)

        # Check if we have enough data for this window
        if test_end.date() > end_date:
            break

        # Split data
        train_mask = (df.index >= train_start) & (df.index < train_end)
        test_mask = (df.index >= test_start) & (df.index < test_end)

        train_df = df[train_mask]
        test_df = df[test_mask]

        if len(train_df) < 50 or len(test_df) < 20:
            current_start += pd.DateOffset(months=step_months)
            continue

        # Save splits to temp files and run backtests
        train_path = Path(f"/tmp/wf_train_{window_id}.parquet")
        test_path = Path(f"/tmp/wf_test_{window_id}.parquet")
        train_df.to_parquet(train_path)
        test_df.to_parquet(test_path)

        logger.info(
            f"Window {window_id}: Train {train_start.date()}→{train_end.date()}, "
            f"Test {test_start.date()}→{test_end.date()}"
        )

        # Run backtests. Seed the test backtester's regime history from the train
        # period so the regime filter is active from day 1 of the test window.
        bt_train = Backtester(strategy_config, risk_config)
        train_results = bt_train.run(train_path)

        bt_test = Backtester(strategy_config, risk_config)
        bt_test.seed_regime_history(train_df)
        test_results = bt_test.run(test_path)

        # Clean up temp files
        train_path.unlink(missing_ok=True)
        test_path.unlink(missing_ok=True)

        windows.append(WalkForwardWindow(
            window_id=window_id,
            train_start=str(train_start.date()),
            train_end=str(train_end.date()),
            test_start=str(test_start.date()),
            test_end=str(test_end.date()),
            train_results=train_results,
            test_results=test_results,
        ))

        window_id += 1
        current_start += pd.DateOffset(months=step_months)

    results = WalkForwardResults(windows=windows, config={})
    results.summary()
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m backtest.walk_forward <data_path>")
        sys.exit(1)

    walk_forward_validate(sys.argv[1])
