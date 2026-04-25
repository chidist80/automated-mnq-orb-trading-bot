"""
Window #1 regime study (Feb 25 - Jun 25, 2025).

Hypothesis: this 4-month period had a market regime our strategy couldn't handle,
which tripped the 20% account drawdown circuit breaker early in the walk-forward
test window and locked out the rest of the period.

Goal: identify what was structurally different about Feb-Jun 2025 vs the other
test windows, and decide whether a regime filter would help.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtester import Backtester
from backtest.data_loader import load_parquet

logging.basicConfig(level=logging.WARNING)  # quiet the breaker spam
logger = logging.getLogger("regime")
logger.setLevel(logging.INFO)

DATA_PATH = PROJECT_ROOT / "data" / "mnq_15m.parquet"
STRAT_CFG = PROJECT_ROOT / "config" / "strategy_params.yaml"
RISK_CFG = PROJECT_ROOT / "config" / "risk_params.yaml"

WINDOW1_START = pd.Timestamp("2025-02-25", tz="US/Eastern")
WINDOW1_END = pd.Timestamp("2025-06-25", tz="US/Eastern")


def section(title: str):
    print(f"\n\n{'=' * 70}\n {title}\n{'=' * 70}")


def daily_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-day metrics: range, gap, opening-range size, volume."""
    df = df.copy()
    df["date"] = df.index.date

    # Per-day OHLCV from the 15m bars
    daily = df.groupby("date").agg(
        day_open=("open", "first"),
        day_high=("high", "max"),
        day_low=("low", "min"),
        day_close=("close", "last"),
        day_volume=("volume", "sum"),
    )
    daily["day_range"] = daily["day_high"] - daily["day_low"]
    daily["day_pct_range"] = daily["day_range"] / daily["day_close"] * 100

    # Overnight gap: today's first 09:30 bar open vs yesterday's last 15:45 close
    daily["prev_close"] = daily["day_close"].shift(1)
    daily["gap_points"] = daily["day_open"] - daily["prev_close"]
    daily["gap_abs"] = daily["gap_points"].abs()

    # Opening range size: high-low of first 30 min (09:30-09:45 = 1 bar; 09:30-10:00 = 2 bars).
    # Strategy uses 09:30-09:45 by default = 1 bar. Use 2 bars (30 min) to capture early action.
    or_width = []
    for date in daily.index:
        day_bars = df[df["date"] == date]
        early = day_bars.iloc[:2]  # first 30 min (two 15m bars)
        if len(early) >= 2:
            or_width.append(early["high"].max() - early["low"].min())
        else:
            or_width.append(np.nan)
    daily["or_30m"] = or_width
    daily["or_pct"] = daily["or_30m"] / daily["day_close"] * 100

    return daily


def compare(name: str, in_win: pd.Series, out_win: pd.Series, fmt: str = "{:.2f}") -> str:
    """Format a comparison row: window-1 vs rest, with delta and t-stat-ish ratio."""
    in_mean = in_win.mean()
    out_mean = out_win.mean()
    in_med = in_win.median()
    out_med = out_win.median()
    pct_diff = ((in_mean - out_mean) / out_mean * 100) if out_mean else 0
    return (
        f"  {name:<25} | "
        f"W1: mean={fmt.format(in_mean)} med={fmt.format(in_med)} | "
        f"Rest: mean={fmt.format(out_mean)} med={fmt.format(out_med)} | "
        f"Δmean={pct_diff:+.1f}%"
    )


def main():
    df = load_parquet(DATA_PATH)
    print(f"Loaded {len(df):,} bars: {df.index[0]} -> {df.index[-1]}")

    daily = daily_stats(df)
    daily.index = pd.to_datetime(daily.index).tz_localize("US/Eastern")

    in_win = daily[(daily.index >= WINDOW1_START) & (daily.index < WINDOW1_END)]
    out_win = daily[(daily.index < WINDOW1_START) | (daily.index >= WINDOW1_END)]

    section("MARKET REGIME COMPARISON — Window #1 vs all other periods")
    print(f"  Window #1:     {in_win.index.min().date()} -> {in_win.index.max().date()}  ({len(in_win)} days)")
    print(f"  Other periods: rest of the dataset                   ({len(out_win)} days)")
    print()
    print(compare("Daily range (pts)", in_win["day_range"], out_win["day_range"], "{:.1f}"))
    print(compare("Daily range (%)", in_win["day_pct_range"], out_win["day_pct_range"], "{:.2f}"))
    print(compare("Overnight gap (pts)", in_win["gap_abs"], out_win["gap_abs"], "{:.1f}"))
    print(compare("OR 30m width (pts)", in_win["or_30m"], out_win["or_30m"], "{:.1f}"))
    print(compare("OR 30m width (%)", in_win["or_pct"], out_win["or_pct"], "{:.3f}"))
    print(compare("Volume (sum)", in_win["day_volume"], out_win["day_volume"], "{:,.0f}"))

    # Distribution: extreme days (top 10% gap and top 10% range) — over-represented in W1?
    section("EXTREME-DAY FREQUENCY")
    p90_gap = daily["gap_abs"].quantile(0.90)
    p90_range = daily["day_range"].quantile(0.90)
    p90_or = daily["or_30m"].quantile(0.90)
    p10_or = daily["or_30m"].quantile(0.10)
    for label, mask_full in [
        ("Top-10% gap days", daily["gap_abs"] >= p90_gap),
        ("Top-10% range days", daily["day_range"] >= p90_range),
        ("Top-10% OR width", daily["or_30m"] >= p90_or),
        ("Bottom-10% OR width", daily["or_30m"] <= p10_or),
    ]:
        in_count = mask_full[in_win.index].sum()
        out_count = mask_full[out_win.index].sum()
        in_rate = in_count / len(in_win) * 100
        out_rate = out_count / len(out_win) * 100
        print(f"  {label:<22}  W1: {in_count:3d} ({in_rate:4.1f}%)  | "
              f"Rest: {out_count:3d} ({out_rate:4.1f}%)  | "
              f"ratio={in_rate/out_rate if out_rate else float('inf'):.2f}x")

    # Run the backtester to inspect trades
    section("TRADE-LEVEL ANALYSIS — Window #1 only")
    bt = Backtester(str(STRAT_CFG), str(RISK_CFG))
    results = bt.run(DATA_PATH)

    trades_df = pd.DataFrame([
        {
            "date": t.date,
            "entry_time": t.entry_time,
            "setup": t.setup,
            "direction": t.direction,
            "exit_reason": t.exit_reason,
            "pnl_dollars": t.pnl_dollars,
            "or_size": t.or_size,
            "or_classification": t.or_classification,
        }
        for t in results.trades
    ])
    trades_df["entry_dt"] = pd.to_datetime(trades_df["entry_time"], utc=True).dt.tz_convert("US/Eastern")

    w1_trades = trades_df[
        (trades_df["entry_dt"] >= WINDOW1_START) & (trades_df["entry_dt"] < WINDOW1_END)
    ].sort_values("entry_dt").reset_index(drop=True)
    rest_trades = trades_df[
        (trades_df["entry_dt"] < WINDOW1_START) | (trades_df["entry_dt"] >= WINDOW1_END)
    ]

    print(f"  Trades in W1:  {len(w1_trades)}")
    print(f"  Trades rest:   {len(rest_trades)}")

    if len(w1_trades) == 0:
        print("  (no trades — circuit breaker tripped at start; "
              "the 11 trades in the walk-forward Window #1 test came from the WF run, "
              "not the full-period run. The full-period breaker tripped earlier.)")
    else:
        wins = (w1_trades["pnl_dollars"] > 0).sum()
        print(f"  W1 WR:         {wins/len(w1_trades):.1%}")
        print(f"  W1 P&L:        ${w1_trades['pnl_dollars'].sum():,.2f}")

    # Run a fresh backtester ONLY on the W1 window to see what would happen with a
    # clean account — this isolates regime effects from the cumulative account state.
    section("ISOLATED W1 BACKTEST — clean $2,500 account, only Feb-Jun 2025")
    win_path = Path("/tmp/window1_only.parquet")
    df_w1 = df[(df.index >= WINDOW1_START) & (df.index < WINDOW1_END)]
    df_w1.to_parquet(win_path)
    bt_w1 = Backtester(str(STRAT_CFG), str(RISK_CFG))
    res_w1 = bt_w1.run(win_path)
    win_path.unlink(missing_ok=True)

    if res_w1.total_trades == 0:
        print("  Zero trades. Breaker tripped before any trade fired (impossible — investigate).")
    else:
        print(f"  Trades:        {res_w1.total_trades}")
        print(f"  Win rate:      {res_w1.win_rate:.1%}")
        print(f"  Profit factor: {res_w1.profit_factor:.2f}")
        print(f"  Total P&L:     ${res_w1.total_pnl:,.2f}")
        print(f"  Max DD:        ${res_w1.max_drawdown:,.2f}")
        print(f"  Max consec L:  {res_w1.max_consecutive_losses}")

        # Trade-by-trade for the first 20 to see the failure pattern
        print("\n  First 20 trades in isolated W1 run (where it goes wrong):")
        cum = 0.0
        for i, t in enumerate(res_w1.trades[:20], 1):
            cum += t.pnl_dollars
            tag = "WIN " if t.pnl_dollars > 0 else "LOSS"
            print(f"    {i:2d}. {t.date} {t.entry_time.strftime('%H:%M'):5s} "
                  f"{t.setup:<18} {t.direction:5s} {tag} "
                  f"${t.pnl_dollars:+7.2f}  cum=${cum:+8.2f}  ({t.exit_reason})")

        # Setup breakdown for W1 isolated
        by_setup = {}
        for t in res_w1.trades:
            by_setup.setdefault(t.setup, []).append(t.pnl_dollars)
        print("\n  W1 by setup:")
        for setup, pnls in by_setup.items():
            wins = sum(1 for p in pnls if p > 0)
            print(f"    {setup:<22} {len(pnls):3d} trades  "
                  f"WR {wins/len(pnls):4.0%}  P&L ${sum(pnls):+,.2f}")

    section("CONCLUSION — read the numbers above and decide")


if __name__ == "__main__":
    main()
