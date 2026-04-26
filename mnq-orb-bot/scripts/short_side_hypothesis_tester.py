"""Pre-registered short-side hypothesis tester.

Implements Stages 1-4 of docs/superpowers/plans/2026-04-26-short-side-research-program.md

Each hypothesis is a fully-specified rule. We test ALL 10 in one batch and
apply Bonferroni correction across the family. Outputs both a CSV of raw
results and a structured findings markdown.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT,
    apply_slippage,
    build_day_contexts,
    load_1m_parquet,
    net_pnl,
    time_window_mask,
    timestamp_on_day,
)


IS_END = "2025-06-30"  # IS through end of H1 2025; OOS = H2 2025 + 2026 YTD


@dataclass
class ShortRule:
    name: str
    rationale: str
    setup: str  # one of: "mirror", "failed_long_fade", "vwap_rejection", "naive_breakdown"
    or_classes: tuple[str, ...] = ("normal",)
    start: str = "10:00"
    end: str = "11:00"
    touch_tolerance: float = 5.0
    stop_points: float = 40.0
    rr: float = 1.25
    time_exit: str = "15:55"
    slippage_rt: float = DEFAULT_SLIPPAGE_RT
    max_close_vwap_delta_below: float | None = 65.0
    require_below_ema9: bool = False
    require_neg_ema_slope: bool = False
    min_volume_ratio: float | None = None
    min_rsi: float | None = None
    max_rsi: float | None = None
    require_5d_neg_return: float | None = None  # threshold like -0.01 = require 5d ret < -1%
    require_gap_down: bool = False


HYPOTHESES = [
    ShortRule(
        name="H1_failed_long_fade",
        rationale="Long broke above OR_high earlier; close back below in retest window. Tight stop for squeeze risk.",
        setup="failed_long_fade",
        or_classes=("normal", "tight", "wide"),
        stop_points=25.0, rr=40/25,
    ),
    ShortRule(
        name="H2_wide_OR_naive_breakdown",
        rationale="Phase 1 hint: wide-OR shorts had positive sample. Wide ORs reflect news/conviction; breakdowns continue.",
        setup="naive_breakdown",
        or_classes=("wide",),
    ),
    ShortRule(
        name="H3_vwap_rejection_emadown",
        rationale="Failed bullish reclaim of VWAP with EMA9 confirming downtrend = high-conviction short.",
        setup="vwap_rejection",
        require_neg_ema_slope=True,
    ),
    ShortRule(
        name="H4_mirror_below_ema",
        rationale="Symmetric short trigger gated to require price already below 15m EMA9 (downtrend confirmed).",
        setup="mirror",
        require_below_ema9=True,
    ),
    ShortRule(
        name="H5_mirror_tight_stop",
        rationale="Same trigger as canonical mirror, tighter risk to handle bear-trap squeezes.",
        setup="mirror",
        stop_points=25.0, rr=40/25,
    ),
    ShortRule(
        name="H6_mirror_weak_5d",
        rationale="Regime filter: only short when MNQ 5-day return < -1% (proxy for weak macro).",
        setup="mirror",
        require_5d_neg_return=-0.01,
    ),
    ShortRule(
        name="H7_naive_afternoon",
        rationale="Late-session breakdowns. Lunch-time low broken in afternoon = continuation pattern.",
        setup="naive_breakdown",
        or_classes=("normal", "tight", "wide"),
        start="13:00", end="15:00",
    ),
    ShortRule(
        name="H8_failed_long_rsi_overbought",
        rationale="Failed long break with RSI > 70 = distribution at the top.",
        setup="failed_long_fade",
        max_rsi=None, min_rsi=70.0,  # require RSI >= 70
        stop_points=25.0, rr=40/25,
    ),
    ShortRule(
        name="H9_mirror_early_window",
        rationale="Earlier retest window = more momentum continuation, less mean reversion noise.",
        setup="mirror",
        start="09:45", end="10:30",
    ),
    ShortRule(
        name="H10_mirror_gap_down",
        rationale="Only short on gap-down opens (today's open < yesterday's close).",
        setup="mirror",
        require_gap_down=True,
    ),
]


# -- Setup detectors --------------------------------------------------------

def first_short_breakdown(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] < ctx.or_low]
    return None if hits.empty else hits.index[0]


def first_long_breakout_local(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] > ctx.or_high]
    return None if hits.empty else hits.index[0]


def first_vwap_reclaim(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[(sub["close"] > sub["vwap"]) & (sub["close"].shift(1) <= sub["vwap"].shift(1))]
    return None if hits.empty else hits.index[0]


def candidate_signals_for(ctx, rule: ShortRule) -> pd.DataFrame:
    """Return DataFrame of candidate signal bars for the rule's setup."""
    if rule.setup == "mirror":
        breakdown = first_short_breakdown(ctx)
        if breakdown is None:
            return pd.DataFrame()
        after = ctx.features[
            (ctx.features.index > breakdown)
            & time_window_mask(ctx.features, rule.start, rule.end)
        ]
        mask = (
            (after["high"] >= ctx.or_low - rule.touch_tolerance)
            & (after["close"] < ctx.or_low)
            & (after["close"] < after["prev_low"])
        )
        return after[mask]

    if rule.setup == "naive_breakdown":
        breakdown = first_short_breakdown(ctx)
        if breakdown is None:
            return pd.DataFrame()
        # Take the breakdown bar itself as the signal — entry on next open
        if breakdown in ctx.features.index and breakdown.time() <= pd.Timestamp(rule.end).time():
            return ctx.features.loc[[breakdown]]
        return pd.DataFrame()

    if rule.setup == "failed_long_fade":
        breakout = first_long_breakout_local(ctx)
        if breakout is None:
            return pd.DataFrame()
        after = ctx.features[
            (ctx.features.index > breakout)
            & time_window_mask(ctx.features, rule.start, rule.end)
        ]
        # Failed long: close back below OR_high AND below prev_low (continuation down)
        mask = (after["close"] < ctx.or_high) & (after["close"] < after["prev_low"])
        return after[mask]

    if rule.setup == "vwap_rejection":
        reclaim = first_vwap_reclaim(ctx)
        if reclaim is None:
            return pd.DataFrame()
        after = ctx.features[
            (ctx.features.index > reclaim)
            & time_window_mask(ctx.features, rule.start, rule.end)
        ]
        # Reject: close back below VWAP AND below prior close
        mask = (after["close"] < after["vwap"]) & (after["close"] < after["prev_close"])
        return after[mask]

    raise ValueError(f"Unknown setup: {rule.setup}")


def passes_filters(signal: pd.Series, ctx, rule: ShortRule) -> bool:
    if rule.max_close_vwap_delta_below is not None:
        below = float(signal["vwap"] - signal["close"])
        if not np.isfinite(below) or below > rule.max_close_vwap_delta_below:
            return False
    if rule.require_below_ema9:
        if not np.isfinite(signal.get("ema9", np.nan)) or signal["close"] >= signal["ema9"]:
            return False
    if rule.require_neg_ema_slope:
        v = float(signal.get("ema9_slope", np.nan))
        if not np.isfinite(v) or v >= 0:
            return False
    if rule.min_volume_ratio is not None:
        v = float(signal.get("volume_ratio20", np.nan))
        if not np.isfinite(v) or v < rule.min_volume_ratio:
            return False
    if rule.min_rsi is not None:
        v = float(signal.get("rsi14", np.nan))
        if not np.isfinite(v) or v < rule.min_rsi:
            return False
    if rule.max_rsi is not None:
        v = float(signal.get("rsi14", np.nan))
        if not np.isfinite(v) or v > rule.max_rsi:
            return False
    return True


def passes_regime(ctx, rule: ShortRule, regime_ctx: dict) -> bool:
    if rule.require_5d_neg_return is not None:
        ret = regime_ctx.get(f"5d_return:{ctx.date}")
        if ret is None or ret >= rule.require_5d_neg_return:
            return False
    if rule.require_gap_down:
        gap = regime_ctx.get(f"gap:{ctx.date}")
        if gap is None or gap >= 0:
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
        if bar["high"] >= stop_price:
            return ts, stop_price, "stop"
        if bar["low"] <= target_price:
            return ts, target_price, "target"
    last = sub.iloc[-1]
    return last.name, float(last["close"]), "time_exit"


def evaluate(ctx, rule: ShortRule, regime_ctx: dict, slippage_rt: float):
    if not ctx.clean or not ctx.full_session_clean:
        return None
    if ctx.or_class not in rule.or_classes:
        return None
    if not passes_regime(ctx, rule, regime_ctx):
        return None
    signals = candidate_signals_for(ctx, rule)
    if signals.empty:
        return None
    for signal_ts, signal in signals.iterrows():
        if not passes_filters(signal, ctx, rule):
            continue
        eligible = ctx.features[ctx.features.index > signal_ts]
        if eligible.empty:
            return None
        entry_ts = eligible.index[0]
        raw_entry = float(eligible.iloc[0]["open"])
        entry_price = apply_slippage(raw_entry, "short", "entry", slippage_rt)
        stop_price = entry_price + rule.stop_points
        target_price = entry_price - rule.rr * rule.stop_points
        result = simulate_short_exit(ctx, entry_ts, entry_price, stop_price, target_price, rule.time_exit)
        if result is None:
            return None
        exit_ts, raw_exit, reason = result
        exit_price = apply_slippage(raw_exit, "short", "exit", slippage_rt)
        return {
            "date": ctx.date,
            "rule": rule.name,
            "signal_ts": str(signal_ts),
            "entry_ts": str(entry_ts),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_ts": str(exit_ts),
            "exit_price": exit_price,
            "exit_reason": reason,
            "net_pnl": net_pnl(entry_price, exit_price, "short"),
        }
    return None


def build_regime_context(contexts):
    """Compute per-date 5d trailing return and gap-vs-prev-close."""
    daily_close = {}
    for ctx in contexts:
        if not ctx.day.empty:
            daily_close[ctx.date] = float(ctx.day.iloc[-1]["close"])
    sorted_dates = sorted(daily_close)
    closes = pd.Series([daily_close[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    ret_5d = closes.pct_change(5)
    out = {}
    for ctx in contexts:
        d = ctx.date
        if d in daily_close:
            r = ret_5d.loc[pd.to_datetime(d)]
            out[f"5d_return:{d}"] = float(r) if pd.notna(r) else None
        if not ctx.day.empty:
            today_open = float(ctx.day.iloc[0]["open"])
            ts_today = pd.to_datetime(d)
            prior_dates = [pd.to_datetime(s) for s in sorted_dates if pd.to_datetime(s) < ts_today]
            if prior_dates:
                prior_close = daily_close[prior_dates[-1].strftime("%Y-%m-%d")]
                out[f"gap:{d}"] = today_open - prior_close
    return out


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "pnl": 0.0, "avg": float("nan"), "win_rate": float("nan"), "pf": float("nan"), "max_dd": 0.0}
    df = pd.DataFrame(rows)
    pnl = df["net_pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else float("inf")
    eq = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    peak = eq.cummax()
    return {
        "n": int(len(df)),
        "pnl": float(pnl.sum()),
        "avg": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "pf": float(pf),
        "max_dd": float((peak - eq).max()),
    }


def one_sided_p_value_above_zero(pnls):
    """One-sided t-test: H0 mean<=0 vs H1 mean>0."""
    arr = np.asarray(pnls, dtype=float)
    if arr.size < 2:
        return 1.0
    mean = arr.mean()
    sd = arr.std(ddof=1)
    if sd == 0:
        return 0.0 if mean > 0 else 1.0
    t = mean / (sd / math.sqrt(arr.size))
    try:
        from scipy.stats import t as t_dist
        return float(1.0 - t_dist.cdf(t, df=arr.size - 1))
    except ImportError:
        return float(0.5 * math.erfc(t / math.sqrt(2.0)))


def run():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp("2024-01-01").date()]
    regime = build_regime_context(in_window)
    is_end = pd.Timestamp(IS_END).date()

    print(f"Total in-window days: {len(in_window)}")
    print(f"IS through {is_end}, OOS after\n")

    stage1, stage2_passed, stage3_passed = [], [], []

    for rule in HYPOTHESES:
        rows = []
        for ctx in in_window:
            r = evaluate(ctx, rule, regime, slippage_rt=DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)

        full = summarize(rows)
        # Stage 1
        s1_pass = (full["n"] >= 30) and (full["pf"] >= 1.3) and (full["avg"] > 0)
        result = {
            "rule": rule.name,
            "rationale": rule.rationale,
            "stage": "1",
            "stage_pass": s1_pass,
            "full": full,
        }

        if not s1_pass:
            print(f"[STAGE 1 FAIL] {rule.name}: n={full['n']}  pf={full['pf']:.3f}  avg=${full['avg']:.2f}")
            stage1.append(result)
            continue

        # Stage 2: IS/OOS
        df_ = pd.DataFrame(rows)
        df_["d"] = pd.to_datetime(df_["date"]).dt.date
        is_rows = df_[df_["d"] <= is_end].to_dict("records")
        oos_rows = df_[df_["d"] > is_end].to_dict("records")
        is_summary = summarize(is_rows)
        oos_summary = summarize(oos_rows)
        deg = (
            abs(is_summary["avg"] - oos_summary["avg"]) / abs(is_summary["avg"])
            if is_summary["avg"] != 0 and oos_summary["n"] > 0 else float("inf")
        )
        s2_pass = (
            is_summary["pf"] >= 1.4
            and oos_summary["pf"] >= 1.2
            and oos_summary["n"] >= 15
            and deg < 0.40
        )
        result.update({
            "stage": "2",
            "stage_pass": s2_pass,
            "is": is_summary,
            "oos": oos_summary,
            "degradation": deg,
        })

        # Stage 3: Bonferroni p-value
        oos_pnls = [r["net_pnl"] for r in oos_rows]
        p_raw = one_sided_p_value_above_zero(oos_pnls)
        bonferroni_alpha = 0.05 / len(HYPOTHESES)
        s3_pass = p_raw < bonferroni_alpha
        result.update({
            "stage": "3",
            "stage_pass": s2_pass and s3_pass,
            "oos_p_raw": p_raw,
            "bonferroni_alpha": bonferroni_alpha,
        })

        if not s2_pass:
            print(f"[STAGE 2 FAIL] {rule.name}: full n={full['n']} pf={full['pf']:.3f}  IS pf={is_summary['pf']:.3f}  OOS pf={oos_summary['pf']:.3f}  OOS n={oos_summary['n']}  deg={deg:.2%}")
            stage1.append(result)
            continue

        if not s3_pass:
            print(f"[STAGE 3 FAIL] {rule.name}: OOS p={p_raw:.4f} (need <{bonferroni_alpha:.4f})")
            stage2_passed.append(result)
            continue

        print(f"[STAGE 1-3 PASS] {rule.name}: full n={full['n']} pf={full['pf']:.3f}  OOS pf={oos_summary['pf']:.3f}  p={p_raw:.4f}")

        # Stage 4: slippage sensitivity
        slip_results = {}
        for slip in [1.5, 3.0, 5.0, 8.0, 12.0]:
            slip_rows = []
            for ctx in in_window:
                r = evaluate(ctx, rule, regime, slippage_rt=slip)
                if r is not None:
                    slip_rows.append(r)
            slip_results[f"{slip}pt"] = summarize(slip_rows)
        s4_pass = slip_results.get("8.0pt", {}).get("pf", 0) >= 1.3
        result.update({
            "stage": "4",
            "stage_pass": s4_pass,
            "slippage": slip_results,
        })
        stage3_passed.append(result)
        if s4_pass:
            print(f"[STAGE 4 PASS] {rule.name}: 8pt slippage pf={slip_results['8.0pt']['pf']:.3f}")
        else:
            print(f"[STAGE 4 FAIL] {rule.name}: 8pt slippage pf={slip_results['8.0pt']['pf']:.3f}")

    # Save raw findings
    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_results = stage1 + stage2_passed + stage3_passed
    out_dir.joinpath("findings_v2_raw.json").write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nWrote raw results to {out_dir}/findings_v2_raw.json")

    # Final verdict
    survivors = [r for r in stage3_passed if r.get("stage_pass")]
    print(f"\n=== FINAL ===")
    print(f"Hypotheses tested: {len(HYPOTHESES)}")
    print(f"Stage 1 fails: {len([r for r in stage1 if r['stage'] == '1'])}")
    print(f"Stage 2 fails: {len([r for r in stage1 if r['stage'] == '2'])}")
    print(f"Stage 3 fails (p>{0.05/len(HYPOTHESES):.4f}): {len(stage2_passed)}")
    print(f"Stage 4 fails (slippage): {len([r for r in stage3_passed if not r.get('stage_pass')])}")
    print(f"SURVIVORS: {len(survivors)}")
    for s in survivors:
        print(f"  -> {s['rule']}")
    return all_results, survivors


if __name__ == "__main__":
    run()
