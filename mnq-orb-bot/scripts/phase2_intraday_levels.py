"""
Phase 2 — Intraday level proximity.

Test whether the human enters near specific intraday levels:
  or_high_dist, or_low_dist, or_mid_dist
  vwap_dist
  swing_high_dist, swing_low_dist  - intraday swing pivots from completed bars
  ema9_15m_dist, ema9_5m_dist
  intraday_session_high_dist, intraday_session_low_dist

A "swing pivot" = a 1m bar high/low that hasn't been violated in the most
recent N bars (uses N=5 lookback).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from human_edge_replay import EASTERN, build_daily_contexts, load_1m_data  # type: ignore[import-not-found]


def compute_completed_swing_pivots(day_so_far: pd.DataFrame, lookback: int = 5) -> tuple[float | None, float | None]:
    """Return the most recent confirmed swing high and low using completed bars."""
    if len(day_so_far) < 2 * lookback + 1:
        return None, None
    highs = day_so_far["high"].values
    lows = day_so_far["low"].values
    # Find pivots that have at least `lookback` bars on each side that are lower-high or higher-low
    n = len(day_so_far)
    swing_high = None
    swing_low = None
    # Walk backwards, take most recent confirmed pivot
    for i in range(n - lookback - 1, lookback - 1, -1):
        if swing_high is None:
            left = highs[max(0, i - lookback):i]
            right = highs[i + 1:i + 1 + lookback]
            if len(left) >= 1 and len(right) >= lookback:
                if highs[i] > left.max() and highs[i] > right.max():
                    swing_high = float(highs[i])
        if swing_low is None:
            left = lows[max(0, i - lookback):i]
            right = lows[i + 1:i + 1 + lookback]
            if len(left) >= 1 and len(right) >= lookback:
                if lows[i] < left.min() and lows[i] < right.min():
                    swing_low = float(lows[i])
        if swing_high is not None and swing_low is not None:
            break
    return swing_high, swing_low


def main() -> None:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)
    ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")
    covered = ideas[ideas["coverage_status"] == "covered"].copy()
    direction_sign = np.where(covered["direction"] == "long", 1, -1)
    covered["human_1c"] = (covered["weighted_exit_price"] - covered["weighted_entry_price"]) * direction_sign * 2.0 - 1.34

    rows = []
    for raw in covered.to_dict("records"):
        entry_ts = pd.Timestamp(raw["entry_time_et"]).tz_convert(EASTERN)
        date_obj = entry_ts.date()
        ctx = contexts.get(str(date_obj))
        if ctx is None or not ctx.get("clean", False):
            continue
        day = ctx["day"]
        entry_price = float(raw["weighted_entry_price"])
        direction = raw["direction"]

        # Day-so-far before the entry minute
        cutoff = entry_ts.floor("min") - pd.Timedelta(minutes=1)
        day_so_far = day.loc[day.index <= cutoff]

        # Intraday session H/L so far
        sess_high = float(day_so_far["high"].max()) if not day_so_far.empty else float("nan")
        sess_low = float(day_so_far["low"].min()) if not day_so_far.empty else float("nan")

        # VWAP so far
        if not day_so_far.empty:
            typ = (day_so_far["high"] + day_so_far["low"] + day_so_far["close"]) / 3.0
            volsum = day_so_far["volume"].sum()
            vwap = float((typ * day_so_far["volume"]).sum() / volsum) if volsum > 0 else float("nan")
        else:
            vwap = float("nan")

        # OR levels
        or_high = ctx["or_high"]
        or_low = ctx["or_low"]
        or_mid = ctx["or_midpoint"]

        # 15m / 5m EMAs
        bars_15m = ctx.get("bars_15m")
        bars_5m = ctx.get("bars_5m")
        ema9_15m = float("nan")
        if bars_15m is not None and not bars_15m.empty:
            done = bars_15m[bars_15m.index + pd.Timedelta(minutes=15) <= entry_ts]
            if not done.empty:
                ema9_15m = float(done.iloc[-1]["ema9"])
        ema9_5m = float("nan")
        if bars_5m is not None and not bars_5m.empty:
            done = bars_5m[bars_5m.index + pd.Timedelta(minutes=5) <= entry_ts]
            if not done.empty:
                ema9_5m = float(done.iloc[-1]["ema9"])

        # Swing pivots from completed 1m bars before entry
        swing_high, swing_low = compute_completed_swing_pivots(day_so_far, lookback=5)

        # Distances (signed: positive = entry above level)
        rows.append({
            "idea_id": raw["idea_id"],
            "direction": direction,
            "human_1c": raw["human_1c"],
            "max_size": raw["max_overlapping_contracts"],
            "setup_label": raw["setup_label"],
            "or_high_dist": entry_price - or_high,
            "or_low_dist": entry_price - or_low,
            "or_mid_dist": entry_price - or_mid,
            "vwap_dist": entry_price - vwap if np.isfinite(vwap) else float("nan"),
            "ema9_15m_dist": entry_price - ema9_15m if np.isfinite(ema9_15m) else float("nan"),
            "ema9_5m_dist": entry_price - ema9_5m if np.isfinite(ema9_5m) else float("nan"),
            "swing_high_dist": entry_price - swing_high if swing_high is not None else float("nan"),
            "swing_low_dist": entry_price - swing_low if swing_low is not None else float("nan"),
            "sess_high_dist": entry_price - sess_high,
            "sess_low_dist": entry_price - sess_low,
        })

    df = pd.DataFrame(rows)
    out_dir = PROJECT_ROOT / "research" / "human_edge_replay" / "phase2"
    df.to_csv(out_dir / "intraday_levels.csv", index=False)

    # Distance summaries — focus on absolute distance to find the "anchor"
    print(f"=== Intraday level proximity, n={len(df)} covered ideas ===\n")
    print("Median |distance| from entry to each intraday level (points):")
    levels = ["or_high_dist","or_low_dist","or_mid_dist","vwap_dist",
              "ema9_15m_dist","ema9_5m_dist","swing_high_dist","swing_low_dist",
              "sess_high_dist","sess_low_dist"]
    rows_summary = []
    for lvl in levels:
        col = df[lvl].dropna()
        if col.empty:
            continue
        rows_summary.append({
            "level": lvl,
            "median_abs": col.abs().median(),
            "p25_abs": col.abs().quantile(0.25),
            "p75_abs": col.abs().quantile(0.75),
            "within_5pt": int((col.abs() <= 5).sum()),
            "within_10pt": int((col.abs() <= 10).sum()),
            "within_15pt": int((col.abs() <= 15).sum()),
        })
    summary = pd.DataFrame(rows_summary).sort_values("median_abs")
    print(summary.to_string(index=False))
    print()

    # Direction-aware: longs look for support, shorts look for resistance
    print("Conviction-subset (size>=10) direction-aware level proximity:")
    conv = df[df.max_size >= 10]
    print(f"n_conviction = {len(conv)}")
    for lvl in levels:
        col = conv[lvl].dropna()
        if col.empty: continue
        within = (col.abs() <= 10).sum()
        print(f"  {lvl}: within 10pt = {within}/{len(conv)}, abs_median={col.abs().median():.1f}")

    print()
    print("Best near-level rule on full covered set: entries within 10pt of nearest of (vwap, ema9_5m, ema9_15m, or_mid):")
    candidates = df[["vwap_dist","ema9_5m_dist","ema9_15m_dist","or_mid_dist"]].abs()
    df["min_dyn_dist"] = candidates.min(axis=1)
    print(f"  within 5pt: {(df.min_dyn_dist <= 5).sum()}, sum_pnl ${df[df.min_dyn_dist<=5].human_1c.sum():.2f}, win {(df[df.min_dyn_dist<=5].human_1c>0).mean():.3f}")
    print(f"  within 10pt: {(df.min_dyn_dist <= 10).sum()}, sum_pnl ${df[df.min_dyn_dist<=10].human_1c.sum():.2f}, win {(df[df.min_dyn_dist<=10].human_1c>0).mean():.3f}")
    print(f"  within 15pt: {(df.min_dyn_dist <= 15).sum()}, sum_pnl ${df[df.min_dyn_dist<=15].human_1c.sum():.2f}, win {(df[df.min_dyn_dist<=15].human_1c>0).mean():.3f}")
    print(f"  within 20pt: {(df.min_dyn_dist <= 20).sum()}, sum_pnl ${df[df.min_dyn_dist<=20].human_1c.sum():.2f}, win {(df[df.min_dyn_dist<=20].human_1c>0).mean():.3f}")
    print(f"  > 20pt: {(df.min_dyn_dist > 20).sum()}, sum_pnl ${df[df.min_dyn_dist>20].human_1c.sum():.2f}, win {(df[df.min_dyn_dist>20].human_1c>0).mean():.3f}")


if __name__ == "__main__":
    main()
