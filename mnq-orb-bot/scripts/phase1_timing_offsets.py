"""
Phase 1 — timing-offset analysis.

For every covered idea where a mechanical signal fired on the same
day/direction, compute:
  - delta_minutes = human_entry_ts - mech_signal_ts
  - delta_price = human_entry_price - mech_entry_price (signed by direction
    so positive = human got a worse price)
  - was the human within +-5 of the mechanical level at entry, or far away?

Output dispersion characterizes how the human relates to mechanical signals.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from human_edge_replay import EASTERN, build_daily_contexts, load_1m_data  # type: ignore[import-not-found]
from phase1_setup_vs_timing import (  # type: ignore[import-not-found]
    MNQ_MULTIPLIER,
    COMMISSION_RT,
    apply_slippage,
    mechanical_entry_for_ema_continuation,
    mechanical_entry_for_inverse_orb,
    mechanical_entry_for_orb_break,
    mechanical_entry_for_orb_retest,
)


def first_mechanical_signal(
    day: pd.DataFrame,
    bars_15m: pd.DataFrame,
    or_high: float,
    or_low: float,
    direction: str,
) -> tuple[pd.Timestamp | None, str]:
    candidates = []
    for rule, fn in (
        ("orb_break", lambda: mechanical_entry_for_orb_break(day, or_high, or_low, direction)),
        ("orb_retest", lambda: mechanical_entry_for_orb_retest(day, or_high, or_low, direction)),
        ("inverse_orb", lambda: mechanical_entry_for_inverse_orb(day, or_high, or_low, direction)),
        ("ema_continuation", lambda: mechanical_entry_for_ema_continuation(day, bars_15m, direction)),
    ):
        ts = fn()
        if ts is not None:
            candidates.append((ts, rule))
    if not candidates:
        return None, "no_signal"
    earliest = min(candidates, key=lambda c: c[0])
    return earliest


def main() -> None:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)
    ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")
    covered = ideas[ideas["coverage_status"] == "covered"].copy()
    direction_sign = np.where(covered["direction"] == "long", 1, -1)
    covered["human_1c_net_pnl"] = (
        (covered["weighted_exit_price"] - covered["weighted_entry_price"]) * direction_sign * MNQ_MULTIPLIER
        - COMMISSION_RT
    )

    rows = []
    for raw in covered.to_dict("records"):
        date_key = str(pd.Timestamp(raw["entry_time_et"]).tz_convert(EASTERN).date())
        ctx = contexts.get(date_key)
        if ctx is None or not ctx.get("clean", False):
            continue
        day = ctx["day"]
        sig_ts, rule = first_mechanical_signal(
            day, ctx.get("bars_15m"), ctx["or_high"], ctx["or_low"], raw["direction"]
        )
        human_ts = pd.Timestamp(raw["entry_time_et"]).tz_convert(EASTERN)
        human_price = float(raw["weighted_entry_price"])
        if sig_ts is None:
            rows.append({
                "idea_id": raw["idea_id"],
                "direction": raw["direction"],
                "setup_label": raw["setup_label"],
                "human_entry_ts": human_ts,
                "human_entry_price": human_price,
                "human_1c_net_pnl": raw["human_1c_net_pnl"],
                "mech_rule": "no_signal",
                "mech_signal_ts": None,
                "mech_entry_price": np.nan,
                "delta_minutes": np.nan,
                "delta_price_signed": np.nan,
                "or_high": ctx["or_high"],
                "or_low": ctx["or_low"],
                "or_size": ctx["or_size"],
                "or_class": ctx["or_classification"],
            })
            continue
        # Get the mechanical entry price (close at sig_ts)
        eligible = day[day.index >= sig_ts]
        mech_raw_price = float(eligible.iloc[0]["close"]) if not eligible.empty else np.nan
        mech_entry_price = (
            apply_slippage(mech_raw_price, raw["direction"], "entry")
            if np.isfinite(mech_raw_price)
            else np.nan
        )
        delta_min = (human_ts - sig_ts).total_seconds() / 60.0
        # Signed price delta: positive = human got a worse price than mechanical
        if raw["direction"] == "long":
            delta_price = human_price - mech_entry_price
        else:
            delta_price = mech_entry_price - human_price
        rows.append({
            "idea_id": raw["idea_id"],
            "direction": raw["direction"],
            "setup_label": raw["setup_label"],
            "human_entry_ts": human_ts,
            "human_entry_price": human_price,
            "human_1c_net_pnl": raw["human_1c_net_pnl"],
            "mech_rule": rule,
            "mech_signal_ts": sig_ts,
            "mech_entry_price": mech_entry_price,
            "delta_minutes": delta_min,
            "delta_price_signed": delta_price,
            "or_high": ctx["or_high"],
            "or_low": ctx["or_low"],
            "or_size": ctx["or_size"],
            "or_class": ctx["or_classification"],
        })

    df = pd.DataFrame(rows)
    out_dir = PROJECT_ROOT / "research" / "human_edge_replay" / "phase1"
    df.to_csv(out_dir / "timing_offsets.csv", index=False)

    print("=== Timing offsets: human entry vs first mechanical signal (same day, same direction) ===")
    print()
    fired = df[df.mech_signal_ts.notna()]
    print(f"Mechanical signal fired on {len(fired)} of {len(df)} covered ideas")
    print()
    print("Distribution of delta_minutes (human - mech_signal, in minutes):")
    print(f"  human-AFTER-mech (positive, waited for confirmation): {(fired.delta_minutes > 0).sum()}")
    print(f"  human-BEFORE-mech (negative, anticipated): {(fired.delta_minutes < 0).sum()}")
    print(f"  human=mech (within same minute): {(fired.delta_minutes == 0).sum()}")
    print()
    print("delta_minutes percentiles:")
    print(fired.delta_minutes.describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_string())
    print()
    print("delta_price_signed (positive = human got worse price than mech, points):")
    print(fired.delta_price_signed.describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_string())
    print()
    print("Per-bucket: how often human entered before vs after mech signal")
    grouped = fired.groupby("setup_label").agg(
        n=("delta_minutes", "count"),
        anticipated=("delta_minutes", lambda s: int((s < 0).sum())),
        same_minute=("delta_minutes", lambda s: int((s == 0).sum())),
        confirmed=("delta_minutes", lambda s: int((s > 0).sum())),
        median_delta_min=("delta_minutes", "median"),
        median_delta_price=("delta_price_signed", "median"),
        sum_human_pnl=("human_1c_net_pnl", "sum"),
    ).reset_index()
    print(grouped.to_string(index=False))
    print()
    print("Profitability of human entries by their timing relationship to mech signal:")
    fired_copy = fired.copy()
    fired_copy["timing_relation"] = pd.cut(
        fired_copy["delta_minutes"],
        bins=[-1e9, -30, -5, -0.001, 0.001, 5, 30, 1e9],
        labels=["anticipated_>30min", "anticipated_5to30", "anticipated_<5min",
                "same_minute", "confirmed_<5min", "confirmed_5to30", "confirmed_>30min"],
    )
    by_rel = fired_copy.groupby("timing_relation", observed=True).agg(
        n=("idea_id", "count"),
        sum_pnl=("human_1c_net_pnl", "sum"),
        mean_pnl=("human_1c_net_pnl", "mean"),
        win_rate=("human_1c_net_pnl", lambda s: float((s > 0).mean())),
    ).reset_index()
    print(by_rel.to_string(index=False))


if __name__ == "__main__":
    main()
