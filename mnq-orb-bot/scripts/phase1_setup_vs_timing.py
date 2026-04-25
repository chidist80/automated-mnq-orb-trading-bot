"""
Phase 1 — setup vs timing decomposition.

For every covered human trade idea, replay the corresponding mechanical entry
on the same trading day using only completed bars at decision time.

The mechanical replica answers: "if a rule-based system had entered on the
human's classified setup, on the same day and direction, what would its
1-contract P&L have been?" Comparing against the human's 1-contract simulated
P&L isolates how much edge is in the setup choice vs in the human's timing
or discretion.

No look-ahead: every entry uses the close of a completed 1m bar. Slippage of
2 ticks (0.50 pt) is applied at entry; commissions of $1.34 round trip.

Two exit rules are evaluated for each replica:
  same_time_exit   - exit at the human's exit timestamp (cleanest apples-
                     to-apples on entry timing alone)
  rule_exit        - mechanical exit using the OR midpoint as a stop and a
                     2x-OR-size target, or end-of-RTH at 15:55 ET
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from human_edge_replay import (  # type: ignore[import-not-found]
    EASTERN,
    build_daily_contexts,
    load_1m_data,
)

MNQ_MULTIPLIER = 2.0  # $/point
TICK = 0.25
SLIPPAGE_TICKS = 2  # 0.50pt entry + 0.50pt exit baked in
COMMISSION_RT = 1.34  # IBKR + exchange round trip per contract
RTH_END = pd.Timestamp("15:55").time()


def points_to_dollars(points: float) -> float:
    return points * MNQ_MULTIPLIER


def apply_slippage(price: float, direction: str, side: str) -> float:
    """side='entry' fills worse than mid; side='exit' fills worse than mid."""
    delta = SLIPPAGE_TICKS * TICK
    if side == "entry":
        return price + delta if direction == "long" else price - delta
    return price - delta if direction == "long" else price + delta


def gross_points(entry: float, exit_: float, direction: str) -> float:
    return exit_ - entry if direction == "long" else entry - exit_


def net_pnl_from_prices(entry: float, exit_: float, direction: str) -> float:
    return points_to_dollars(gross_points(entry, exit_, direction)) - COMMISSION_RT


# ---------- Mechanical entry rules per setup_label ----------


def _next_close_at_or_after(day: pd.DataFrame, ts: pd.Timestamp) -> pd.Series | None:
    eligible = day[day.index >= ts]
    return None if eligible.empty else eligible.iloc[0]


def _first_close_breaching(
    day: pd.DataFrame,
    after_ts: pd.Timestamp,
    level: float,
    direction: str,
) -> pd.Series | None:
    sub = day[day.index >= after_ts]
    for _ts, bar in sub.iterrows():
        if direction == "long" and bar["close"] > level:
            return bar
        if direction == "short" and bar["close"] < level:
            return bar
    return None


def _first_close_returning(
    day: pd.DataFrame,
    after_ts: pd.Timestamp,
    or_high: float,
    or_low: float,
) -> pd.Series | None:
    """For inverse ORB: first close back inside the OR after a breakout."""
    sub = day[day.index >= after_ts]
    for _ts, bar in sub.iterrows():
        if or_low <= bar["close"] <= or_high:
            return bar
    return None


def mechanical_entry_for_orb_break(
    day: pd.DataFrame,
    or_high: float,
    or_low: float,
    direction: str,
) -> pd.Timestamp | None:
    or_locked_ts = day.index[0].normalize() + pd.Timedelta(hours=9, minutes=45)
    or_locked_ts = or_locked_ts.tz_localize(EASTERN) if or_locked_ts.tz is None else or_locked_ts
    level = or_high if direction == "long" else or_low
    bar = _first_close_breaching(day, or_locked_ts, level, direction)
    return None if bar is None else bar.name + pd.Timedelta(minutes=1)


def mechanical_entry_for_orb_retest(
    day: pd.DataFrame,
    or_high: float,
    or_low: float,
    direction: str,
) -> pd.Timestamp | None:
    """Enter on the first close that re-touches the broken OR level after a confirmed break."""
    or_locked_ts = day.index[0].normalize() + pd.Timedelta(hours=9, minutes=45)
    or_locked_ts = or_locked_ts.tz_localize(EASTERN) if or_locked_ts.tz is None else or_locked_ts
    level = or_high if direction == "long" else or_low
    breakout = _first_close_breaching(day, or_locked_ts, level, direction)
    if breakout is None:
        return None
    after = breakout.name + pd.Timedelta(minutes=1)
    sub = day[day.index >= after]
    for _ts, bar in sub.iterrows():
        if direction == "long" and bar["low"] <= level:
            return bar.name + pd.Timedelta(minutes=1)
        if direction == "short" and bar["high"] >= level:
            return bar.name + pd.Timedelta(minutes=1)
    return None


def mechanical_entry_for_inverse_orb(
    day: pd.DataFrame,
    or_high: float,
    or_low: float,
    direction: str,
) -> pd.Timestamp | None:
    """Wide OR breakout fails, price returns inside OR. Enter in the fade direction."""
    or_locked_ts = day.index[0].normalize() + pd.Timedelta(hours=9, minutes=45)
    or_locked_ts = or_locked_ts.tz_localize(EASTERN) if or_locked_ts.tz is None else or_locked_ts
    fade_dir = "short" if direction == "short" else "long"
    breakout_dir = "long" if fade_dir == "short" else "short"
    level = or_high if breakout_dir == "long" else or_low
    breakout = _first_close_breaching(day, or_locked_ts, level, breakout_dir)
    if breakout is None:
        return None
    after = breakout.name + pd.Timedelta(minutes=1)
    fade = _first_close_returning(day, after, or_high, or_low)
    return None if fade is None else fade.name + pd.Timedelta(minutes=1)


def mechanical_entry_for_ema_continuation(
    day: pd.DataFrame,
    bars_15m: pd.DataFrame,
    direction: str,
) -> pd.Timestamp | None:
    """
    Enter on the first 1m bar whose close is within 5pts of the latest
    completed 15m EMA9, in the trend direction.
    """
    if bars_15m is None or bars_15m.empty:
        return None
    completed = bars_15m[bars_15m.index + pd.Timedelta(minutes=15) <= day.index[-1]]
    if completed.empty:
        return None
    or_locked_ts = day.index[0].normalize() + pd.Timedelta(hours=9, minutes=45)
    or_locked_ts = or_locked_ts.tz_localize(EASTERN) if or_locked_ts.tz is None else or_locked_ts
    sub = day[day.index >= or_locked_ts]
    for ts, bar in sub.iterrows():
        prior = completed[completed.index + pd.Timedelta(minutes=15) <= ts]
        if prior.empty:
            continue
        ema9 = float(prior.iloc[-1]["ema9"])
        slope = float(prior.iloc[-1]["ema9_slope"])
        rsi = float(prior.iloc[-1]["rsi14"])
        if not np.isfinite(ema9) or abs(bar["close"] - ema9) > 5.0:
            continue
        if direction == "long" and slope > 0 and rsi >= 50:
            return ts + pd.Timedelta(minutes=1)
        if direction == "short" and slope < 0 and rsi <= 50:
            return ts + pd.Timedelta(minutes=1)
    return None


# ---------- Exit rules ----------


def exit_at_time(day: pd.DataFrame, exit_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    eligible = day[day.index <= exit_ts]
    if eligible.empty:
        return None
    last = eligible.iloc[-1]
    return last.name, float(last["close"])


def exit_via_rule(
    day: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    direction: str,
    or_size: float,
) -> tuple[pd.Timestamp, float, str]:
    """
    Stop = entry_price -/+ 1 OR-size (capped at 30 points).
    Target = entry_price +/- 2 OR-size.
    Else exit at 15:55 ET.
    """
    risk = min(or_size, 30.0) if np.isfinite(or_size) and or_size > 0 else 15.0
    target = entry_price + 2 * risk if direction == "long" else entry_price - 2 * risk
    stop = entry_price - risk if direction == "long" else entry_price + risk
    sub = day[day.index >= entry_ts]
    for ts, bar in sub.iterrows():
        if direction == "long":
            if bar["low"] <= stop:
                return ts, stop, "stop"
            if bar["high"] >= target:
                return ts, target, "target"
        else:
            if bar["high"] >= stop:
                return ts, stop, "stop"
            if bar["low"] <= target:
                return ts, target, "target"
    rth_end = day.index[0].normalize() + pd.Timedelta(hours=15, minutes=55)
    rth_end = rth_end.tz_localize(EASTERN) if rth_end.tz is None else rth_end
    eligible = day[day.index <= rth_end]
    last = eligible.iloc[-1] if not eligible.empty else day.iloc[-1]
    return last.name, float(last["close"]), "rth_close"


# ---------- Replica computation ----------


def replica_for_idea(
    idea: dict,
    day: pd.DataFrame,
    bars_15m: pd.DataFrame,
    or_high: float,
    or_low: float,
    or_size: float,
) -> dict:
    direction = idea["direction"]
    entry_ts = pd.Timestamp(idea["entry_time_et"]).tz_convert(EASTERN)
    exit_ts = pd.Timestamp(idea["exit_time_et"]).tz_convert(EASTERN)
    setup = idea["setup_label"]

    if setup == "orb_breakout_or_retest_candidate":
        mech_entry_ts = mechanical_entry_for_orb_break(day, or_high, or_low, direction)
    elif setup == "inverse_orb_candidate":
        mech_entry_ts = mechanical_entry_for_inverse_orb(day, or_high, or_low, direction)
    elif setup == "ema_continuation_candidate":
        mech_entry_ts = mechanical_entry_for_ema_continuation(day, bars_15m, direction)
    elif setup == "pre_or_locked_trade":
        return {
            "replica_setup": "not_mechanizable_pre_or_locked",
            "replica_entry_ts": None,
            "replica_entry_price": np.nan,
            "replica_same_time_pnl": np.nan,
            "replica_rule_pnl": np.nan,
            "replica_rule_exit_reason": "n/a",
            "replica_rule_exit_ts": None,
        }
    else:
        # discretionary_or_unclassified — try both ORB break and EMA continuation
        # in the same direction, take whichever fires first.
        candidates = [
            mechanical_entry_for_orb_break(day, or_high, or_low, direction),
            mechanical_entry_for_ema_continuation(day, bars_15m, direction),
        ]
        candidates = [c for c in candidates if c is not None]
        mech_entry_ts = min(candidates) if candidates else None

    if mech_entry_ts is None:
        return {
            "replica_setup": setup,
            "replica_entry_ts": None,
            "replica_entry_price": np.nan,
            "replica_same_time_pnl": np.nan,
            "replica_rule_pnl": np.nan,
            "replica_rule_exit_reason": "no_signal",
            "replica_rule_exit_ts": None,
        }

    bar = _next_close_at_or_after(day, mech_entry_ts)
    if bar is None:
        return {
            "replica_setup": setup,
            "replica_entry_ts": mech_entry_ts,
            "replica_entry_price": np.nan,
            "replica_same_time_pnl": np.nan,
            "replica_rule_pnl": np.nan,
            "replica_rule_exit_reason": "no_bar_after_signal",
            "replica_rule_exit_ts": None,
        }
    raw_entry_price = float(bar["close"])
    entry_price = apply_slippage(raw_entry_price, direction, "entry")

    same_time = exit_at_time(day, exit_ts)
    if same_time is None:
        same_time_pnl = np.nan
    else:
        _, raw_exit = same_time
        exit_price = apply_slippage(raw_exit, direction, "exit")
        same_time_pnl = net_pnl_from_prices(entry_price, exit_price, direction)

    rule_exit_ts, raw_rule_exit, reason = exit_via_rule(
        day, bar.name, entry_price, direction, or_size
    )
    rule_exit_price = apply_slippage(raw_rule_exit, direction, "exit")
    rule_pnl = net_pnl_from_prices(entry_price, rule_exit_price, direction)

    return {
        "replica_setup": setup,
        "replica_entry_ts": bar.name,
        "replica_entry_price": entry_price,
        "replica_same_time_pnl": same_time_pnl,
        "replica_rule_pnl": rule_pnl,
        "replica_rule_exit_reason": reason,
        "replica_rule_exit_ts": rule_exit_ts,
    }


def main() -> None:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)
    ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")
    covered = ideas[ideas["coverage_status"] == "covered"].copy()

    # 1-contract human simulation (matches Phase 0 baseline)
    direction_sign = np.where(covered["direction"] == "long", 1, -1)
    covered["human_1c_gross_points"] = (
        covered["weighted_exit_price"] - covered["weighted_entry_price"]
    ) * direction_sign
    covered["human_1c_gross_dollars"] = covered["human_1c_gross_points"] * MNQ_MULTIPLIER
    covered["human_1c_net_pnl"] = covered["human_1c_gross_dollars"] - COMMISSION_RT

    rows = []
    for raw in covered.to_dict("records"):
        date_key = str(pd.Timestamp(raw["entry_time_et"]).tz_convert(EASTERN).date())
        ctx = contexts.get(date_key)
        if ctx is None or not ctx.get("clean", False):
            rows.append({**raw, "replica_setup": "no_context", "replica_entry_ts": None,
                         "replica_entry_price": np.nan, "replica_same_time_pnl": np.nan,
                         "replica_rule_pnl": np.nan, "replica_rule_exit_reason": "no_context",
                         "replica_rule_exit_ts": None})
            continue
        replica = replica_for_idea(
            raw,
            ctx["day"],
            ctx.get("bars_15m"),
            ctx["or_high"],
            ctx["or_low"],
            ctx["or_size"],
        )
        rows.append({**raw, **replica})

    out = pd.DataFrame(rows)
    out_dir = PROJECT_ROOT / "research" / "human_edge_replay" / "phase1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / "replica_comparison.csv", index=False)

    # Aggregations
    summary = out.groupby("setup_label").agg(
        ideas=("idea_id", "count"),
        replica_fired=("replica_entry_price", lambda s: s.notna().sum()),
        human_1c_sum=("human_1c_net_pnl", "sum"),
        replica_same_time_sum=("replica_same_time_pnl", "sum"),
        replica_rule_sum=("replica_rule_pnl", "sum"),
        human_1c_mean=("human_1c_net_pnl", "mean"),
        replica_same_time_mean=("replica_same_time_pnl", "mean"),
        replica_rule_mean=("replica_rule_pnl", "mean"),
        human_1c_winrate=("human_1c_net_pnl", lambda s: float((s > 0).mean())),
        replica_same_time_winrate=(
            "replica_same_time_pnl",
            lambda s: float((s.dropna() > 0).mean()) if s.notna().any() else np.nan,
        ),
        replica_rule_winrate=(
            "replica_rule_pnl",
            lambda s: float((s.dropna() > 0).mean()) if s.notna().any() else np.nan,
        ),
    ).reset_index()
    summary.to_csv(out_dir / "summary_by_setup.csv", index=False)

    print("=== Phase 1: Setup vs Timing Decomposition ===")
    print()
    print(f"Covered ideas processed: {len(out)}")
    print()
    total_human = out["human_1c_net_pnl"].sum()
    total_same = out["replica_same_time_pnl"].sum(skipna=True)
    total_rule = out["replica_rule_pnl"].sum(skipna=True)
    fired = out["replica_entry_price"].notna().sum()
    print(f"Human 1c sum P&L:               ${total_human:.2f}")
    print(f"Replica same-time-exit sum P&L: ${total_same:.2f}  (replica fired on {fired}/{len(out)} ideas)")
    print(f"Replica rule-exit sum P&L:      ${total_rule:.2f}")
    print()
    print("Per-setup breakdown:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
