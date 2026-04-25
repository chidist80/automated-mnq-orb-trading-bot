"""
Phase 2 — Discretionary diagnosis.

Adds feature columns that the original labeler did not compute, so we can
ask whether the human's entries cluster around any of:

  pdh / pdl / pd_close  - prior-day RTH high, low, close
  gap_to_open           - today open vs prior close (in pts and signed)
  pdh_distance / pdl_distance - signed distance from entry to prior-day H/L
  nearest_round_distance - distance to nearest 50-pt round number
  ema20_60m / ema50_60m  - longer-period EMA on 60m bars completed before entry
  entry_vs_60m_ema20    - above/below
  pre_entry_1m_pattern  - basic 1m candle pattern at the bar entered/prior
  pre_entry_bar_volume_z - z-score of volume relative to RTH-day mean
  volume_3bar_z          - z-score of last-3-bar cumulative volume

No look-ahead: every feature uses bars that completed strictly before entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from human_edge_replay import EASTERN, build_daily_contexts, load_1m_data, ema  # type: ignore[import-not-found]


def prior_day_levels(one_minute: pd.DataFrame, date_obj) -> tuple[float, float, float]:
    rth = one_minute.between_time("09:30", "15:59")
    prior_dates = sorted({d for d in rth.index.date if d < date_obj})
    if not prior_dates:
        return float("nan"), float("nan"), float("nan")
    prior = rth[rth.index.date == prior_dates[-1]]
    if prior.empty:
        return float("nan"), float("nan"), float("nan")
    return float(prior["high"].max()), float(prior["low"].min()), float(prior.iloc[-1]["close"])


def round_number_distance(price: float, increment: float = 50.0) -> tuple[float, float]:
    """Returns (signed pts to nearest round, abs pts)."""
    if not np.isfinite(price):
        return float("nan"), float("nan")
    nearest = round(price / increment) * increment
    return price - nearest, abs(price - nearest)


def bar_pattern(prev: pd.Series, cur: pd.Series, direction: str) -> str:
    """Crude 1m bar-pattern classification at the entered bar (cur) and prior bar (prev)."""
    if cur is None or prev is None:
        return "unknown"
    cur_range = cur["high"] - cur["low"]
    if cur_range <= 0:
        return "doji"
    body = abs(cur["close"] - cur["open"])
    upper_wick = cur["high"] - max(cur["close"], cur["open"])
    lower_wick = min(cur["close"], cur["open"]) - cur["low"]
    body_frac = body / cur_range
    if body_frac < 0.25:
        return "doji"
    if direction == "long":
        if cur["close"] > cur["open"] and lower_wick / cur_range > 0.4:
            return "rejection_low"
        if cur["close"] > prev["high"]:
            return "breakout_up"
        if cur["close"] < cur["open"]:
            return "red_against"
        return "neutral_up"
    if cur["close"] < cur["open"] and upper_wick / cur_range > 0.4:
        return "rejection_high"
    if cur["close"] < prev["low"]:
        return "breakout_down"
    if cur["close"] > cur["open"]:
        return "green_against"
    return "neutral_down"


def add_60m_emas(one_minute: pd.DataFrame) -> pd.DataFrame:
    rth = one_minute.between_time("09:30", "15:59")
    bars_60m = (
        rth.resample("60min", closed="left", label="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    bars_60m["ema20"] = ema(bars_60m["close"], 20)
    bars_60m["ema50"] = ema(bars_60m["close"], 50)
    return bars_60m


def main() -> None:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)
    bars_60m_all = add_60m_emas(one_minute)
    ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")
    covered = ideas[ideas["coverage_status"] == "covered"].copy()
    direction_sign = np.where(covered["direction"] == "long", 1, -1)
    covered["human_1c"] = (
        (covered["weighted_exit_price"] - covered["weighted_entry_price"]) * direction_sign * 2.0 - 1.34
    )

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

        pdh, pdl, pdc = prior_day_levels(one_minute, date_obj)
        signed_round, abs_round = round_number_distance(entry_price)
        # Gap (today open vs prior close)
        today_open = float(day.iloc[0]["open"]) if not day.empty else float("nan")
        gap = today_open - pdc if np.isfinite(pdc) else float("nan")

        # Prior-day level distances (signed: positive = entry is above level)
        pdh_dist = entry_price - pdh
        pdl_dist = entry_price - pdl
        pdc_dist = entry_price - pdc

        # 60m EMA (last completed)
        completed_60m = bars_60m_all[bars_60m_all.index + pd.Timedelta(minutes=60) <= entry_ts]
        if not completed_60m.empty:
            ema20_60 = float(completed_60m.iloc[-1]["ema20"])
            ema50_60 = float(completed_60m.iloc[-1]["ema50"])
            entry_vs_60m_ema20 = (
                "above" if entry_price > ema20_60 + 5
                else "below" if entry_price < ema20_60 - 5
                else "near"
            )
        else:
            ema20_60 = ema50_60 = float("nan")
            entry_vs_60m_ema20 = "unknown"

        # Bar pattern at entry minute (1m bar containing entry_ts) and prior bar
        entry_minute = entry_ts.floor("min")
        prev_minute = entry_minute - pd.Timedelta(minutes=1)
        cur_bar = day.loc[entry_minute] if entry_minute in day.index else None
        prev_bar = day.loc[prev_minute] if prev_minute in day.index else None
        pattern = bar_pattern(prev_bar, cur_bar, direction)

        # Volume z-scores
        rth_day = day.loc[day.index <= entry_minute]
        if len(rth_day) >= 5:
            vol_mean = rth_day["volume"].mean()
            vol_std = rth_day["volume"].std()
            cur_vol = float(cur_bar["volume"]) if cur_bar is not None else float("nan")
            vol_z = (cur_vol - vol_mean) / vol_std if vol_std > 0 else float("nan")
            last3 = rth_day.tail(3)["volume"].sum()
            vol_3z = (last3 / 3 - vol_mean) / vol_std if vol_std > 0 else float("nan")
        else:
            vol_z = vol_3z = float("nan")

        rows.append({
            "idea_id": raw["idea_id"],
            "entry_time_et": entry_ts,
            "direction": direction,
            "setup_label": raw["setup_label"],
            "human_1c": raw["human_1c"],
            "max_size": raw["max_overlapping_contracts"],
            "entry_price": entry_price,
            "pdh": pdh, "pdl": pdl, "pdc": pdc,
            "pdh_dist": pdh_dist, "pdl_dist": pdl_dist, "pdc_dist": pdc_dist,
            "gap_pts": gap,
            "round_signed": signed_round, "round_abs": abs_round,
            "ema20_60m": ema20_60, "ema50_60m": ema50_60,
            "entry_vs_60m_ema20": entry_vs_60m_ema20,
            "pre_entry_pattern": pattern,
            "vol_z": vol_z, "vol_3bar_z": vol_3z,
        })

    df = pd.DataFrame(rows)
    out_dir = PROJECT_ROOT / "research" / "human_edge_replay" / "phase2"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "extended_features.csv", index=False)

    print(f"=== Phase 2 extended features: {len(df)} covered ideas ===\n")

    # Distance distributions
    print("Distance from entry to prior-day levels (points):")
    for col in ("pdh_dist", "pdl_dist", "pdc_dist"):
        print(f"  {col}: mean={df[col].mean():.1f}, median={df[col].median():.1f}, abs_median={df[col].abs().median():.1f}")
    print()

    # How often does the human enter within X pts of a prior-day level?
    within_thresh = 15.0  # 15 points
    print(f"Entries within {within_thresh}pt of any prior-day level (PDH/PDL/PDC):")
    near_pdh = df.pdh_dist.abs() <= within_thresh
    near_pdl = df.pdl_dist.abs() <= within_thresh
    near_pdc = df.pdc_dist.abs() <= within_thresh
    near_any = near_pdh | near_pdl | near_pdc
    print(f"  near PDH: {near_pdh.sum()}, near PDL: {near_pdl.sum()}, near PDC: {near_pdc.sum()}, near ANY: {near_any.sum()}")
    print(f"  near-ANY 1c P&L: ${df[near_any].human_1c.sum():.2f} ({df[near_any].human_1c.sum()/df.human_1c.sum()*100:.1f}% of total)")
    print(f"  near-ANY win rate: {(df[near_any].human_1c>0).mean():.3f}")
    print()

    # Round number proximity
    print(f"Entries within 10pts of a 50-pt round number: {(df.round_abs <= 10).sum()}")
    print(f"  P&L within 10pt: ${df[df.round_abs<=10].human_1c.sum():.2f} ({(df[df.round_abs<=10].human_1c>0).mean():.3f} win)")
    print()

    # 60m EMA position
    print("Entry vs 60m EMA20:")
    print(df.groupby("entry_vs_60m_ema20").agg(
        n=("idea_id","count"), sum_pnl=("human_1c","sum"),
        win=("human_1c", lambda s: float((s>0).mean()))
    ).to_string())
    print()

    # Bar pattern at entry
    print("1m bar pattern at entry minute:")
    print(df.groupby(["direction","pre_entry_pattern"]).agg(
        n=("idea_id","count"), sum_pnl=("human_1c","sum"),
        win=("human_1c", lambda s: float((s>0).mean()))
    ).to_string())
    print()

    # Volume z at entry minute
    print("Pre-entry bar volume z (vs RTH-to-date mean):")
    print(f"  median: {df.vol_z.median():.2f}")
    print(f"  fraction > 1: {(df.vol_z > 1).mean():.3f}")
    print(f"  fraction > 2: {(df.vol_z > 2).mean():.3f}")
    print()

    # Conviction subset only
    conv = df[df.max_size >= 10]
    print(f"=== Conviction subset (n={len(conv)}, all winners by definition) ===")
    print(f"near-ANY pdH/L/C within 15pt: {((conv.pdh_dist.abs()<=15)|(conv.pdl_dist.abs()<=15)|(conv.pdc_dist.abs()<=15)).sum()} of {len(conv)}")
    print(f"within 10pt of round number: {(conv.round_abs <= 10).sum()} of {len(conv)}")
    print(f"60m EMA20 relation: {conv.entry_vs_60m_ema20.value_counts().to_dict()}")
    print(f"pattern distribution: {conv.pre_entry_pattern.value_counts().to_dict()}")
    print(f"vol_z median: {conv.vol_z.median():.2f}, > 1 share: {(conv.vol_z > 1).mean():.2f}")


if __name__ == "__main__":
    main()
