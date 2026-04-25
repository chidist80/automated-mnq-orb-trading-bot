"""
Validate executable OR stop-entry candidates on 1-minute MNQ data.

This is research/validation infrastructure, not the production backtester path.
It computes the 15-minute OR from 1-minute bars, places a long-only buy-stop at
the OR high, and simulates stop/target sequencing on 1-minute bars.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtester import BacktestResults, Trade
from backtest.monte_carlo import run_monte_carlo

logger = logging.getLogger("or_stop_1m")


@dataclass(frozen=True)
class Candidate:
    name: str
    account_size: float
    normal_only: bool


CANDIDATES = [
    Candidate("$3,750 long-only normal OR", account_size=3750.0, normal_only=True),
    Candidate("$7,500 long-only all OR", account_size=7500.0, normal_only=False),
]


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_1m(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")

    rth = df.between_time("09:30", "15:59").sort_index()
    return rth[["open", "high", "low", "close", "volume"]].copy()


def classify_or(or_size: float, history: list[float], wide_percentile: float) -> tuple[str, float]:
    if len(history) < 5:
        return "normal", 50.0

    pct_rank = sum(1 for x in history if x <= or_size) / len(history) * 100
    if pct_rank <= 25:
        return "tight", pct_rank
    if pct_rank >= wide_percentile:
        return "wide", pct_rank
    return "normal", pct_rank


def is_clean_day(day: pd.DataFrame) -> bool:
    """Require clean data for OR formation and trade/exit sequencing."""
    if len(day) < 376:
        return False

    or_window = day.between_time("09:30", "09:44")
    execution_window = day.between_time("09:30", "15:45")

    if len(or_window) != 15 or len(execution_window) != 376:
        return False
    if (or_window["volume"] == 0).any() or (execution_window["volume"] == 0).any():
        return False
    if or_window["high"].max() <= or_window["low"].min():
        return False

    return True


def simulate_candidate(
    df: pd.DataFrame,
    strategy: dict,
    risk: dict,
    candidate: Candidate,
    slippage_points: float,
    train_start: pd.Timestamp | None = None,
    train_end: pd.Timestamp | None = None,
    test_start: pd.Timestamp | None = None,
    test_end: pd.Timestamp | None = None,
) -> BacktestResults:
    """Run the stop-entry candidate over either full data or a test window.

    When train/test bounds are provided, OR classification history is seeded
    from the training window and trades are only taken in the test window.
    """
    point_value = strategy["instrument"].get("point_value", 2.0)
    commission_side = risk["execution"].get("commission_per_side", 0.47)
    min_or = strategy["opening_range"].get("min_size_points", 20)
    max_risk = risk["position_sizing"].get("max_risk_points", 100)
    wide_pct = strategy.get("inverse_orb", {}).get("wide_or_percentile", 80)

    trades: list[Trade] = []
    history: list[float] = []
    clean_days = [(date, day) for date, day in df.groupby(df.index.date) if is_clean_day(day)]

    for date, day in clean_days:
        day_start = day.index[0]
        if train_end is not None and day_start >= train_end and test_start is None:
            break

        or_window = day.between_time("09:30", "09:44")
        or_high = float(or_window["high"].max())
        or_low = float(or_window["low"].min())
        or_size = or_high - or_low
        classification, _ = classify_or(or_size, history, wide_pct)
        history.append(or_size)
        lookback = strategy.get("inverse_orb", {}).get("lookback_days", 20)
        if len(history) > lookback:
            history = history[-lookback:]

        in_test = True
        if test_start is not None and test_end is not None:
            in_test = test_start <= day_start < test_end
        if not in_test:
            continue

        if or_size < min_or or or_size > max_risk:
            continue
        if candidate.normal_only and classification != "normal":
            continue

        trade = simulate_day(
            day=day,
            date_str=str(date),
            entry_price=or_high,
            stop_price=or_low,
            target_price=or_high + or_size,
            risk_points=or_size,
            classification=classification,
            point_value=point_value,
            commission_side=commission_side,
            slippage_points=slippage_points,
        )
        if trade is not None:
            trades.append(trade)

    return BacktestResults(trades=trades, equity_curve=build_equity_curve(trades), config={})


def simulate_day(
    day: pd.DataFrame,
    date_str: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    risk_points: float,
    classification: str,
    point_value: float,
    commission_side: float,
    slippage_points: float,
) -> Trade | None:
    entry_idx = None
    actual_entry = entry_price
    entry_window = day.between_time("09:45", "12:00")

    for ts, bar in entry_window.iterrows():
        # If the stop level is already through at the minute open, a live stop
        # order would elect at/near the open, not at the stale OR level.
        if bar["open"] >= entry_price:
            entry_idx = ts
            actual_entry = float(bar["open"])
            break
        if bar["high"] >= entry_price:
            entry_idx = ts
            actual_entry = entry_price
            break

    if entry_idx is None:
        return None

    actual_risk = actual_entry - stop_price
    if actual_risk <= 0:
        return None
    actual_target = actual_entry + actual_risk

    trade = Trade(
        date=date_str,
        setup="orb_stop_entry_1m",
        direction="long",
        entry_time=entry_idx,
        entry_price=actual_entry,
        stop_price=stop_price,
        target_price=actual_target,
        risk_points=actual_risk,
        contracts=1,
        confluences={"resting_buy_stop": True},
        or_size=risk_points,
        or_classification=classification,
    )

    after_entry = day.loc[entry_idx:]
    for i, (ts, bar) in enumerate(after_entry.iterrows()):
        if ts.time() >= pd.Timestamp("15:45").time():
            trade.exit_price = float(bar["open"])
            trade.exit_time = ts
            trade.exit_reason = "flatten_time"
            break

        stop_hit = bar["low"] <= stop_price
        target_hit = bar["high"] >= actual_target

        # Conservative sequencing: on the entry minute, only stop-outs are
        # allowed. On later same-minute stop+target ambiguity, stop wins.
        if i == 0:
            if stop_hit:
                trade.exit_price = stop_price
                trade.exit_time = ts
                trade.exit_reason = "stop"
                break
            continue

        if stop_hit:
            trade.exit_price = stop_price
            trade.exit_time = ts
            trade.exit_reason = "stop"
            break
        if target_hit:
            trade.exit_price = actual_target
            trade.exit_time = ts
            trade.exit_reason = "target"
            break
    else:
        last = day.iloc[-1]
        trade.exit_price = float(last["close"])
        trade.exit_time = day.index[-1]
        trade.exit_reason = "eod_flatten"

    trade.pnl_points = trade.exit_price - trade.entry_price - slippage_points
    trade.pnl_dollars = trade.pnl_points * point_value * trade.contracts
    trade.pnl_dollars -= commission_side * 2 * trade.contracts
    return trade


def walk_forward(
    df: pd.DataFrame,
    strategy: dict,
    risk: dict,
    candidate: Candidate,
    slippage_points: float,
    train_months: int = 8,
    test_months: int = 4,
    step_months: int = 2,
) -> list[BacktestResults]:
    start_date = df.index[0].date()
    end_date = df.index[-1].date()
    current = pd.Timestamp(start_date, tz=df.index.tz)
    windows: list[BacktestResults] = []

    while True:
        train_start = current
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)
        if test_end.date() > end_date:
            break

        result = simulate_candidate(
            df,
            strategy,
            risk,
            candidate,
            slippage_points,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        )
        windows.append(result)
        current += pd.DateOffset(months=step_months)

    return windows


def summarize_results(result: BacktestResults) -> dict:
    return {
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor if result.profit_factor != float("inf") else np.inf,
        "pnl": result.total_pnl,
        "max_drawdown": result.max_drawdown,
    }


def build_equity_curve(trades: list[Trade]) -> pd.Series:
    if not trades:
        return pd.Series(dtype=float)
    ordered = sorted(trades, key=lambda t: t.exit_time or t.entry_time)
    index = [t.exit_time or t.entry_time for t in ordered]
    pnl = np.cumsum([t.pnl_dollars for t in ordered])
    return pd.Series(pnl, index=index)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/mnq_1m.parquet")
    parser.add_argument("--strategy-config", default="config/strategy_params.yaml")
    parser.add_argument("--risk-config", default="config/risk_params.yaml")
    parser.add_argument("--slippage", nargs="+", type=float, default=[1.5, 3.0, 5.0])
    parser.add_argument("--mc-sims", type=int, default=10000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = load_1m(PROJECT_ROOT / args.data)
    strategy = load_yaml(PROJECT_ROOT / args.strategy_config)
    risk = load_yaml(PROJECT_ROOT / args.risk_config)

    clean_days = sum(1 for _, day in df.groupby(df.index.date) if is_clean_day(day))
    total_days = len({d for d in df.index.date})
    print(f"1m data: {len(df):,} bars, {total_days} RTH days, {clean_days} clean execution days")

    all_pass = True
    for candidate in CANDIDATES:
        print("\n" + "=" * 80)
        print(candidate.name)
        print("=" * 80)
        for slip in args.slippage:
            full = simulate_candidate(df, strategy, risk, candidate, slip)
            wf = walk_forward(df, strategy, risk, candidate, slip)
            pfs = [w.profit_factor for w in wf if w.profit_factor != float("inf")]
            avg_pf = float(np.mean(pfs)) if pfs else np.inf
            avg_pnl = float(np.mean([w.total_pnl for w in wf])) if wf else 0.0
            pass_rate = sum(1 for w in wf if w.total_pnl > 0) / len(wf) if wf else 0.0
            mc = run_monte_carlo(
                full,
                account_size=candidate.account_size,
                simulations=args.mc_sims,
                ruin_threshold_pct=0.20,
                seed=42,
            )

            full_s = summarize_results(full)
            wf_pass = pass_rate >= 0.75 and avg_pf >= 1.5 and avg_pnl > 0
            mc_pass = mc.ruin_probability < 0.05
            all_pass = all_pass and wf_pass and mc_pass

            print(
                f"slip={slip:>4.1f} | full trades={full_s['trades']:>3} "
                f"WR={full_s['win_rate']:.1%} PF={full_s['profit_factor']:.2f} "
                f"P&L=${full_s['pnl']:,.0f} DD=${full_s['max_drawdown']:,.0f} | "
                f"WF {sum(1 for w in wf if w.total_pnl > 0)}/{len(wf)} "
                f"avgPF={avg_pf:.2f} avgP&L=${avg_pnl:,.0f} | "
                f"MC ruin={mc.ruin_probability:.2%} | "
                f"{'PASS' if wf_pass and mc_pass else 'FAIL'}"
            )

    print("\nOverall:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 3


if __name__ == "__main__":
    raise SystemExit(main())
