"""
Phase 1 extension — run the mechanical rules on every clean RTH day in the
local tape, regardless of whether the human traded. This isolates the rule's
unconditional expectancy from the human's selection.

Outputs:
  mechanical_universe.csv  - one row per (date, rule, direction) where the rule fired
  mechanical_summary.csv   - aggregate P&L per rule and direction
  mechanical_vs_human.csv  - per-day join with human idea counts and P&L
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
    COMMISSION_RT,
    MNQ_MULTIPLIER,
    apply_slippage,
    exit_via_rule,
    mechanical_entry_for_ema_continuation,
    mechanical_entry_for_inverse_orb,
    mechanical_entry_for_orb_break,
    mechanical_entry_for_orb_retest,
    net_pnl_from_prices,
)


def _close_at(day: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    eligible = day[day.index >= ts]
    return None if eligible.empty else float(eligible.iloc[0]["close"])


def evaluate_rule(
    day: pd.DataFrame,
    bars_15m: pd.DataFrame,
    or_high: float,
    or_low: float,
    or_size: float,
    rule: str,
    direction: str,
) -> dict | None:
    if rule == "orb_break":
        entry_ts = mechanical_entry_for_orb_break(day, or_high, or_low, direction)
    elif rule == "orb_retest":
        entry_ts = mechanical_entry_for_orb_retest(day, or_high, or_low, direction)
    elif rule == "inverse_orb":
        entry_ts = mechanical_entry_for_inverse_orb(day, or_high, or_low, direction)
    elif rule == "ema_continuation":
        entry_ts = mechanical_entry_for_ema_continuation(day, bars_15m, direction)
    else:
        raise ValueError(rule)
    if entry_ts is None:
        return None
    raw_entry = _close_at(day, entry_ts)
    if raw_entry is None:
        return None
    entry_price = apply_slippage(raw_entry, direction, "entry")
    rule_exit_ts, raw_exit, reason = exit_via_rule(day, entry_ts, entry_price, direction, or_size)
    exit_price = apply_slippage(raw_exit, direction, "exit")
    return {
        "rule": rule,
        "direction": direction,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "exit_ts": rule_exit_ts,
        "exit_price": exit_price,
        "exit_reason": reason,
        "net_pnl": net_pnl_from_prices(entry_price, exit_price, direction),
    }


def main() -> None:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)
    ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")

    # Restrict universe to dates within the human's trading window
    human_dates = pd.to_datetime(ideas["trade_date_et"]).dt.date
    start, end = human_dates.min(), human_dates.max()

    rules = ["orb_break", "orb_retest", "inverse_orb", "ema_continuation"]
    directions = ["long", "short"]

    all_signals = []
    for date_key, ctx in contexts.items():
        date_obj = pd.to_datetime(date_key).date()
        if date_obj < start or date_obj > end:
            continue
        if not ctx.get("clean", False):
            continue
        day = ctx["day"]
        bars_15m = ctx.get("bars_15m")
        for rule in rules:
            for direction in directions:
                result = evaluate_rule(
                    day, bars_15m, ctx["or_high"], ctx["or_low"],
                    ctx["or_size"], rule, direction,
                )
                if result is not None:
                    result["date"] = date_key
                    result["or_class"] = ctx["or_classification"]
                    all_signals.append(result)

    universe = pd.DataFrame(all_signals)
    out_dir = PROJECT_ROOT / "research" / "human_edge_replay" / "phase1"
    universe.to_csv(out_dir / "mechanical_universe.csv", index=False)

    print(f"Days in window {start} to {end}: {len(universe.date.unique())} fired at least once")

    summary = universe.groupby(["rule", "direction"]).agg(
        signals=("net_pnl", "count"),
        sum_pnl=("net_pnl", "sum"),
        mean_pnl=("net_pnl", "mean"),
        median_pnl=("net_pnl", "median"),
        win_rate=("net_pnl", lambda s: float((s > 0).mean())),
        sum_pnl_wide_or=("net_pnl", lambda s: 0.0),  # filled below
    ).reset_index()
    # Also breakdown by OR-class
    by_class = universe.groupby(["rule", "direction", "or_class"]).agg(
        signals=("net_pnl", "count"),
        sum_pnl=("net_pnl", "sum"),
        mean_pnl=("net_pnl", "mean"),
        win_rate=("net_pnl", lambda s: float((s > 0).mean())),
    ).reset_index()
    summary.to_csv(out_dir / "mechanical_summary.csv", index=False)
    by_class.to_csv(out_dir / "mechanical_summary_by_or_class.csv", index=False)

    # Total mechanical universe P&L vs human P&L
    print()
    print("=== Mechanical universe expectancy (all clean days, no human filter) ===")
    print(summary[["rule", "direction", "signals", "sum_pnl", "mean_pnl", "win_rate"]].to_string(index=False))
    print()
    print("=== With OR-class filter ===")
    print(by_class.to_string(index=False))

    # Days the human traded a given setup vs days mechanical rule fired
    print()
    print("=== Human vs mechanical-rule fire days ===")
    human_setups = ideas[ideas["coverage_status"] == "covered"][
        ["trade_date_et", "direction", "setup_label", "human_1c_net_pnl"] if "human_1c_net_pnl" in ideas.columns else
        ["trade_date_et", "direction", "setup_label"]
    ].copy() if "setup_label" in ideas.columns else None

    # Compute on the fly
    covered = ideas[ideas["coverage_status"] == "covered"].copy()
    direction_sign = np.where(covered["direction"] == "long", 1, -1)
    covered["human_1c_net_pnl"] = (
        (covered["weighted_exit_price"] - covered["weighted_entry_price"]) * direction_sign * MNQ_MULTIPLIER - COMMISSION_RT
    )

    human_per_day = covered.groupby(["trade_date_et", "direction"]).agg(
        human_ideas=("idea_id", "count"),
        human_pnl=("human_1c_net_pnl", "sum"),
    ).reset_index()

    universe["date"] = pd.to_datetime(universe["date"]).dt.date.astype(str)
    human_per_day["trade_date_et"] = human_per_day["trade_date_et"].astype(str)

    # For each rule, ask: when this rule fires, did the human also trade that direction?
    for rule in rules:
        rule_df = universe[universe["rule"] == rule]
        merged = rule_df.merge(
            human_per_day,
            left_on=["date", "direction"],
            right_on=["trade_date_et", "direction"],
            how="left",
        )
        merged["human_traded_same_dir"] = merged["human_ideas"].notna()
        agg = merged.groupby(["direction", "human_traded_same_dir"]).agg(
            signals=("net_pnl", "count"),
            sum_pnl=("net_pnl", "sum"),
            mean_pnl=("net_pnl", "mean"),
            win_rate=("net_pnl", lambda s: float((s > 0).mean())),
        ).reset_index()
        agg.insert(0, "rule", rule)
        print()
        print(f"--- {rule} ---")
        print(agg.to_string(index=False))


if __name__ == "__main__":
    main()
