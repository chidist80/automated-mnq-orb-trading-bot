"""Test symmetric short OR-retest against the same canonical 1m harness.

Mirrors the Tier 1 / A+ long rules to the short side and reports metrics.
This is exploratory analysis, not a strategy change. The frozen long rules
in backtest/causal_or_retest.py are NOT modified.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT,
    EASTERN,
    MNQ_MULTIPLIER,
    COMMISSION_RT,
    TIER1_PILOT,
    apply_slippage,
    build_day_contexts,
    load_1m_parquet,
    net_pnl,
    time_window_mask,
    timestamp_on_day,
)


@dataclass(frozen=True)
class ShortRuleSpec:
    name: str
    or_classes: tuple[str, ...] = ("normal",)
    start: str = "10:00"
    end: str = "11:00"
    touch_tolerance: float = 5.0
    stop_points: float = 40.0
    rr: float = 1.25
    time_exit: str = "15:55"
    slippage_rt: float = DEFAULT_SLIPPAGE_RT
    max_close_vwap_delta_below: float | None = 65.0  # how far BELOW vwap is too extended
    min_signal_ema_slope: float | None = None
    max_or_close_pos: float | None = None


TIER1_SHORT = ShortRuleSpec(name="tier1_short")
A_PLUS_SHORT = ShortRuleSpec(
    name="a_plus_short",
    min_signal_ema_slope=-20.0,
    max_or_close_pos=0.6,
)


def first_short_breakdown(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] < ctx.or_low]
    return None if hits.empty else hits.index[0]


def candidate_short_signals(ctx, rule: ShortRuleSpec, breakdown_ts: pd.Timestamp) -> pd.DataFrame:
    after = ctx.features[
        (ctx.features.index > breakdown_ts) & time_window_mask(ctx.features, rule.start, rule.end)
    ]
    mask = after["high"] >= ctx.or_low - rule.touch_tolerance
    mask &= after["close"] < ctx.or_low
    mask &= after["close"] < after["prev_low"]
    return after[mask]


def short_passes_optional(signal: pd.Series, ctx, rule: ShortRuleSpec) -> bool:
    if rule.max_close_vwap_delta_below is not None:
        below = float(signal["vwap"] - signal["close"])
        if not np.isfinite(below) or below > rule.max_close_vwap_delta_below:
            return False
    if rule.min_signal_ema_slope is not None:
        v = float(signal.get("ema9_slope", np.nan))
        if not np.isfinite(v) or v < rule.min_signal_ema_slope:
            return False
    if rule.max_or_close_pos is not None:
        if not np.isfinite(ctx.or_close_position) or ctx.or_close_position > rule.max_or_close_pos:
            return False
    return True


def simulate_short_exit(ctx, entry_ts, entry_price, stop_price, target_price, time_exit):
    if not target_price < entry_price < stop_price:
        return None
    end_ts = timestamp_on_day(ctx, time_exit)
    sub = ctx.features[(ctx.features.index >= entry_ts) & (ctx.features.index <= end_ts)]
    if sub.empty:
        return None
    for ts, bar in sub.iterrows():
        stop_hit = bar["high"] >= stop_price
        target_hit = bar["low"] <= target_price
        if stop_hit:
            return ts, stop_price, "stop"
        if target_hit:
            return ts, target_price, "target"
    last = sub.iloc[-1]
    return last.name, float(last["close"]), "time_exit"


def evaluate_short(ctx, rule: ShortRuleSpec, require_full_session_clean: bool = False):
    if not ctx.clean:
        return None, ctx.quality
    if require_full_session_clean and not ctx.full_session_clean:
        return None, ctx.full_session_quality
    if ctx.or_class not in rule.or_classes:
        return None, f"or_class_{ctx.or_class}"

    breakdown_ts = first_short_breakdown(ctx)
    if breakdown_ts is None:
        return None, "no_downside_breakdown"

    signals = candidate_short_signals(ctx, rule, breakdown_ts)
    if signals.empty:
        return None, "no_retest_signal"

    for signal_ts, signal in signals.iterrows():
        if not short_passes_optional(signal, ctx, rule):
            continue
        eligible = ctx.features[ctx.features.index > signal_ts]
        if eligible.empty:
            return None, "no_next_open"
        entry_ts = eligible.index[0]
        raw_entry = float(eligible.iloc[0]["open"])
        entry_price = apply_slippage(raw_entry, "short", "entry", rule.slippage_rt)
        stop_price = entry_price + rule.stop_points
        target_price = entry_price - rule.rr * rule.stop_points
        result = simulate_short_exit(ctx, entry_ts, entry_price, stop_price, target_price, rule.time_exit)
        if result is None:
            return None, "no_exit_path"
        exit_ts, raw_exit, reason = result
        exit_price = apply_slippage(raw_exit, "short", "exit", rule.slippage_rt)
        return {
            "date": ctx.date,
            "rule": rule.name,
            "signal_ts": signal_ts,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_ts": exit_ts,
            "exit_price": exit_price,
            "exit_reason": reason,
            "net_pnl": net_pnl(entry_price, exit_price, "short"),
        }, "eligible"
    return None, "signal_filtered"


def run(start: str = "2024-01-01"):
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    ctxs = build_day_contexts(df)
    in_window = [c for c in ctxs if pd.Timestamp(c.date).date() >= pd.Timestamp(start).date()]
    print(f"Days in window {start}+: {len(in_window)}")

    short_t1, short_ap = [], []
    for ctx in in_window:
        for rule, bucket in [(TIER1_SHORT, short_t1), (A_PLUS_SHORT, short_ap)]:
            decision, _ = evaluate_short(ctx, rule, require_full_session_clean=True)
            if decision is not None:
                bucket.append(decision)

    long_log = pd.read_csv(PROJECT_ROOT / "research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv")
    long_t1 = long_log[long_log["tier1_eligible"] == True].assign(
        date=pd.to_datetime(long_log[long_log["tier1_eligible"] == True]["date"]),
        net_pnl=long_log[long_log["tier1_eligible"] == True]["tier1_net_pnl"],
        rule="tier1_long",
    )[["date", "rule", "net_pnl"]]
    long_ap = long_log[long_log["a_plus_shadow_eligible"] == True].assign(
        date=pd.to_datetime(long_log[long_log["a_plus_shadow_eligible"] == True]["date"]),
        net_pnl=long_log[long_log["a_plus_shadow_eligible"] == True]["a_plus_shadow_net_pnl"],
        rule="a_plus_long",
    )[["date", "rule", "net_pnl"]]

    def summarize(name, rows):
        df_ = pd.DataFrame(rows) if isinstance(rows, list) else rows
        if df_.empty:
            print(f"  {name}: 0 trades")
            return
        pnl = df_["net_pnl"]
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        pf = wins.sum() / abs(losses.sum()) if len(losses) else float("inf")
        eq = pnl.cumsum()
        eq0 = pd.concat([pd.Series([0.0]), eq], ignore_index=True)
        peak = eq0.cummax()
        max_dd = (peak - eq0).max()
        print(
            f"  {name:>22}: n={len(df_):3d}  pnl=${pnl.sum():>9.2f}  "
            f"avg=${pnl.mean():>6.2f}  win={(pnl > 0).mean():.2%}  "
            f"pf={pf:.3f}  max_dd=${max_dd:.2f}"
        )

    print("\n=== Long-side (canonical, for reference) ===")
    summarize("Tier 1 LONG", long_t1)
    summarize("A+ LONG",     long_ap)

    print("\n=== Symmetric SHORT (this analysis) ===")
    summarize("Tier 1 SHORT", short_t1)
    summarize("A+ SHORT",     short_ap)

    if short_t1:
        st1_df = pd.DataFrame(short_t1)
        st1_df["date"] = pd.to_datetime(st1_df["date"])
        combined_t1 = pd.concat([
            long_t1[["date", "net_pnl"]].assign(side="long"),
            st1_df[["date", "net_pnl"]].assign(side="short"),
        ]).sort_values("date").reset_index(drop=True)
        print("\n=== Tier 1 LONG + SHORT combined ===")
        summarize("T1 LONG+SHORT", combined_t1)

        same_day = set(long_t1["date"].dt.date) & set(st1_df["date"].dt.date)
        print(f"  Days with BOTH long and short Tier 1 signals: {len(same_day)}")
        if same_day:
            print(f"  Same-day examples: {sorted(same_day)[:5]}")

    if short_ap:
        sap_df = pd.DataFrame(short_ap)
        sap_df["date"] = pd.to_datetime(sap_df["date"])
        combined_ap = pd.concat([
            long_ap[["date", "net_pnl"]].assign(side="long"),
            sap_df[["date", "net_pnl"]].assign(side="short"),
        ]).sort_values("date").reset_index(drop=True)
        print("\n=== A+ LONG + SHORT combined ===")
        summarize("A+ LONG+SHORT", combined_ap)

    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    out_dir.mkdir(parents=True, exist_ok=True)
    if short_t1:
        pd.DataFrame(short_t1).to_csv(out_dir / "tier1_short_trades.csv", index=False)
    if short_ap:
        pd.DataFrame(short_ap).to_csv(out_dir / "a_plus_short_trades.csv", index=False)
    print(f"\nWrote per-trade short-side CSVs to {out_dir}")


if __name__ == "__main__":
    run()
