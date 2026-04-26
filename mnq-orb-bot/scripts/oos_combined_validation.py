"""IS/OOS validation of the best combined strategies (LONG-filtered + SHORT cluster).

Splits at 2025-06-30. Reports per-side and combined metrics IS vs OOS.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    A_PLUS_SHADOW,
    TIER1_PILOT,
    build_day_contexts,
    load_1m_parquet,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from exhaustive_research import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT,
    ShortRule,
    build_regime_map,
    combined_portfolio,
    evaluate_long_with_regime,
    evaluate_short,
    summarize,
)

IS_END = pd.Timestamp("2025-06-30").date()


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp("2024-01-01").date()]
    regime_map = build_regime_map(contexts)

    print(f"Days in window: {len(in_window)}")
    print(f"IS through {IS_END}, OOS after.\n")

    # Pick best LONG filters from the exhaustive sweep
    long_configs = [
        ("tier1_5dret<-1.0%", TIER1_PILOT, "5d_ret<-1.0%"),
        ("tier1_5dret<-1.5%", TIER1_PILOT, "5d_ret<-1.5%"),
        ("a_plus_5dret<-0.5%", A_PLUS_SHADOW, "5d_ret<-0.5%"),
        ("a_plus_atr>1.3", A_PLUS_SHADOW, "atr_pct>1.3"),
    ]
    short_configs = [
        ShortRule(name="short_atr>1.3", setup="mirror", or_classes=("normal",), start="10:00", end="11:00",
                  stop_points=40, rr=1.25, regime_key="atr_pct>1.3"),
        ShortRule(name="short_5dret<-1.0%", setup="mirror", or_classes=("normal",), start="10:00", end="11:00",
                  stop_points=40, rr=1.25, regime_key="5d_ret<-1.0%"),
        ShortRule(name="short_atr>1.3_s50", setup="mirror", or_classes=("normal",), start="10:00", end="11:00",
                  stop_points=50, rr=1.0, regime_key="atr_pct>1.3"),
    ]

    def split_summary(rows):
        df_ = pd.DataFrame(rows)
        if df_.empty:
            return summarize([]), summarize([])
        df_["d"] = pd.to_datetime(df_["date"]).dt.date
        is_rows = df_[df_["d"] <= IS_END].to_dict("records")
        oos_rows = df_[df_["d"] > IS_END].to_dict("records")
        return summarize(is_rows), summarize(oos_rows)

    print("="*100)
    print(f"{'CONFIG':50s} {'WHICH':18s} {'IS_n':>4s} {'IS_PnL':>8s} {'IS_PF':>6s} {'IS_DD':>7s}  {'OOS_n':>5s} {'OOS_PnL':>8s} {'OOS_PF':>6s} {'OOS_DD':>7s}")
    print("="*100)

    for label, long_base, regime_key in long_configs:
        long_rows = []
        for ctx in in_window:
            r = evaluate_long_with_regime(ctx, long_base, regime_map, regime_key, halt_when_eligible=True)
            if r is not None:
                long_rows.append(r)
        is_long, oos_long = split_summary(long_rows)
        print(f"{label:50s} {'LONG_filtered':18s} {is_long['n']:>4d} {is_long['pnl']:>8.0f} {is_long['pf']:>6.2f} {is_long['max_dd']:>7.0f}  {oos_long['n']:>5d} {oos_long['pnl']:>8.0f} {oos_long['pf']:>6.2f} {oos_long['max_dd']:>7.0f}")

        for short_rule in short_configs:
            short_rows = []
            for ctx in in_window:
                r = evaluate_short(ctx, short_rule, regime_map, slippage_rt=DEFAULT_SLIPPAGE_RT)
                if r is not None:
                    short_rows.append(r)
            is_short, oos_short = split_summary(short_rows)

            combined = combined_portfolio(long_rows, short_rows)
            is_comb, oos_comb = split_summary(combined.to_dict("records"))
            collisions = int(combined["collision"].fillna(False).sum()) if "collision" in combined.columns else 0
            print(f"  + {short_rule.name:46s} {'COMBINED':18s} {is_comb['n']:>4d} {is_comb['pnl']:>8.0f} {is_comb['pf']:>6.2f} {is_comb['max_dd']:>7.0f}  {oos_comb['n']:>5d} {oos_comb['pnl']:>8.0f} {oos_comb['pf']:>6.2f} {oos_comb['max_dd']:>7.0f}  collisions={collisions}")
        print()

    # Baseline canonical for reference
    long_baseline_rows = []
    for ctx in in_window:
        r = evaluate_long_with_regime(ctx, TIER1_PILOT, regime_map, None, halt_when_eligible=True)
        if r is not None:
            long_baseline_rows.append(r)
    is_b, oos_b = split_summary(long_baseline_rows)
    print(f"{'CANONICAL Tier 1 LONG (no filter)':50s} {'BASELINE':18s} {is_b['n']:>4d} {is_b['pnl']:>8.0f} {is_b['pf']:>6.2f} {is_b['max_dd']:>7.0f}  {oos_b['n']:>5d} {oos_b['pnl']:>8.0f} {oos_b['pf']:>6.2f} {oos_b['max_dd']:>7.0f}")
    long_baseline_rows_ap = []
    for ctx in in_window:
        r = evaluate_long_with_regime(ctx, A_PLUS_SHADOW, regime_map, None, halt_when_eligible=True)
        if r is not None:
            long_baseline_rows_ap.append(r)
    is_b, oos_b = split_summary(long_baseline_rows_ap)
    print(f"{'CANONICAL A+ LONG (no filter)':50s} {'BASELINE':18s} {is_b['n']:>4d} {is_b['pnl']:>8.0f} {is_b['pf']:>6.2f} {is_b['max_dd']:>7.0f}  {oos_b['n']:>5d} {oos_b['pnl']:>8.0f} {oos_b['pf']:>6.2f} {oos_b['max_dd']:>7.0f}")


if __name__ == "__main__":
    main()
