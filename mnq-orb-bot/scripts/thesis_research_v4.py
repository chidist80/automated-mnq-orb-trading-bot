"""Thesis-first exhaustive short+long research v4.

Each thesis articulates a market-mechanics story, then defines variants
that test it. Per-thesis BH-FDR (q=0.10) prevents multiple-comparison
across the family. IS/OOS validates survivors. Combined portfolio merges
the best from each thesis with the existing canonical Tier 1 LONG +
filtered combinations.

Theses (8 total):
  T1 Volatility-conditional parameters (ATR-scaled stops on canonical mirror short)
  T2 Day-after sequential (yesterday close direction)
  T3 Higher-frequency mirror short (looser regime thresholds)
  T4 Regime intersection (dual-confirmation)
  T5 Volume confirmation (vol-ratio >= X on signal bar)
  T6 Extended time windows (11-12, 12-13, 13-14, 14-15, 14:30-15:30)
  T7 VWAP delta variations (30/50/100/none)
  T8 A+ component decomposition (slope-alone vs or-pos-alone)

Outputs:
  research/human_edge_replay/short_side_exploration/findings_v4.md
  research/human_edge_replay/short_side_exploration/thesis_v4_raw.json
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    A_PLUS_SHADOW,
    DEFAULT_SLIPPAGE_RT,
    TIER1_PILOT,
    apply_slippage,
    build_day_contexts,
    evaluate_rule_on_day,
    load_1m_parquet,
    net_pnl,
    time_window_mask,
    timestamp_on_day,
)

WINDOW_START = "2024-01-01"
IS_END = pd.Timestamp("2025-06-30").date()


# ============================================================================
# UNIFIED RULE
# ============================================================================

@dataclass
class Rule:
    name: str
    thesis: str
    side: str  # "long" or "short"
    setup: str  # "mirror" | "naive_breakdown" | "failed_long_fade" | "vwap_rejection" | "tier1" | "a_plus"
    or_classes: tuple[str, ...] = ("normal",)
    start: str = "10:00"
    end: str = "11:00"
    touch_tolerance: float = 5.0
    stop_points: float = 40.0
    rr: float = 1.25
    time_exit: str = "15:55"
    slippage_rt: float = DEFAULT_SLIPPAGE_RT
    max_close_vwap_delta: float | None = 65.0  # for longs: above; for shorts: below
    min_volume_ratio: float | None = None
    require_below_ema9: bool = False
    require_above_ema9: bool = False
    require_neg_ema_slope: bool = False
    require_pos_ema_slope: bool = False
    max_signal_ema_slope: float | None = None
    min_signal_ema_slope: float | None = None
    min_or_close_pos: float | None = None
    max_or_close_pos: float | None = None
    regime_keys_all: tuple[str, ...] = ()  # ALL must be true (intersection)
    regime_keys_any: tuple[str, ...] = ()  # at least one true
    atr_stop_multiplier: float | None = None  # if set, stop = atr * multiplier
    atr_lookback: int = 20


# ============================================================================
# REGIME MAP (extended)
# ============================================================================

def build_regime_map(contexts) -> dict[str, dict[str, bool]]:
    closes = {}
    opens = {}
    highs = {}
    lows = {}
    for c in contexts:
        if c.day.empty:
            continue
        closes[c.date] = float(c.day.iloc[-1]["close"])
        opens[c.date] = float(c.day.iloc[0]["open"])
        highs[c.date] = float(c.day["high"].max())
        lows[c.date] = float(c.day["low"].min())

    sorted_dates = sorted(closes)
    cs = pd.Series([closes[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    os_ = pd.Series([opens[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    hs = pd.Series([highs[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    ls = pd.Series([lows[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))

    ret_5d = cs.pct_change(5)
    ret_10d = cs.pct_change(10)
    ret_20d = cs.pct_change(20)
    sma_20_slope = cs.rolling(20).mean().diff(5)
    daily_range = hs - ls
    atr_20 = daily_range.rolling(20).mean()
    atr_pct = daily_range / atr_20
    prev_close = cs.shift(1)
    gap_pct = (os_ - prev_close) / prev_close
    prev_day_change = (prev_close.shift(0) - cs.shift(2)) / cs.shift(2)  # yesterday's daily change

    def to_map(series, predicate) -> dict[str, bool]:
        out = {}
        for d in sorted_dates:
            v = series.loc[pd.to_datetime(d)]
            out[d] = bool(predicate(v)) if pd.notna(v) else False
        return out

    return {
        "5d_ret<-0.5%": to_map(ret_5d, lambda v: v < -0.005),
        "5d_ret<-1.0%": to_map(ret_5d, lambda v: v < -0.01),
        "5d_ret<-1.5%": to_map(ret_5d, lambda v: v < -0.015),
        "5d_ret<-2.0%": to_map(ret_5d, lambda v: v < -0.02),
        "5d_ret>0.5%":  to_map(ret_5d, lambda v: v > 0.005),
        "5d_ret>1.5%":  to_map(ret_5d, lambda v: v > 0.015),
        "10d_ret<-1.0%": to_map(ret_10d, lambda v: v < -0.01),
        "10d_ret<-2.0%": to_map(ret_10d, lambda v: v < -0.02),
        "20d_ret<-2.0%": to_map(ret_20d, lambda v: v < -0.02),
        "sma20_slope_neg": to_map(sma_20_slope, lambda v: v < 0),
        "sma20_slope_pos": to_map(sma_20_slope, lambda v: v > 0),
        "gap_pct<-0.10%": to_map(gap_pct, lambda v: v < -0.001),
        "gap_pct<-0.30%": to_map(gap_pct, lambda v: v < -0.003),
        "gap_pct<-0.50%": to_map(gap_pct, lambda v: v < -0.005),
        "gap_pct>0.30%":  to_map(gap_pct, lambda v: v >  0.003),
        "atr_pct>1.1":    to_map(atr_pct, lambda v: v > 1.1),
        "atr_pct>1.3":    to_map(atr_pct, lambda v: v > 1.3),
        "atr_pct>1.5":    to_map(atr_pct, lambda v: v > 1.5),
        "atr_pct<0.8":    to_map(atr_pct, lambda v: v < 0.8),
        "prev_day<-1%":   to_map(prev_day_change, lambda v: v < -0.01),
        "prev_day<-0.5%": to_map(prev_day_change, lambda v: v < -0.005),
        "prev_day>0.5%":  to_map(prev_day_change, lambda v: v >  0.005),
    }, atr_20  # also return ATR series for ATR-scaled stops


# ============================================================================
# UNIFIED EVALUATOR
# ============================================================================

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


def short_signals_for(ctx, rule: Rule) -> pd.DataFrame:
    if rule.setup == "mirror":
        bd = first_short_breakdown(ctx)
        if bd is None:
            return pd.DataFrame()
        after = ctx.features[(ctx.features.index > bd) & time_window_mask(ctx.features, rule.start, rule.end)]
        m = (
            (after["high"] >= ctx.or_low - rule.touch_tolerance)
            & (after["close"] < ctx.or_low)
            & (after["close"] < after["prev_low"])
        )
        return after[m]
    if rule.setup == "naive_breakdown":
        bd = first_short_breakdown(ctx)
        if bd is None:
            return pd.DataFrame()
        end_t = pd.Timestamp(rule.end).time()
        if bd.time() <= end_t and bd in ctx.features.index:
            return ctx.features.loc[[bd]]
        return pd.DataFrame()
    return pd.DataFrame()


def long_signals_for(ctx, rule: Rule) -> pd.DataFrame:
    bo = first_long_breakout_local(ctx)
    if bo is None:
        return pd.DataFrame()
    after = ctx.features[(ctx.features.index > bo) & time_window_mask(ctx.features, rule.start, rule.end)]
    m = (
        (after["low"] <= ctx.or_high + rule.touch_tolerance)
        & (after["close"] > ctx.or_high)
        & (after["close"] > after["prev_high"])
    )
    return after[m]


def regime_passes(rule: Rule, regime_map, date) -> bool:
    for k in rule.regime_keys_all:
        if not regime_map.get(k, {}).get(date, False):
            return False
    if rule.regime_keys_any:
        if not any(regime_map.get(k, {}).get(date, False) for k in rule.regime_keys_any):
            return False
    return True


def filters_pass(signal: pd.Series, ctx, rule: Rule) -> bool:
    if rule.min_volume_ratio is not None:
        v = float(signal.get("volume_ratio20", np.nan))
        if not np.isfinite(v) or v < rule.min_volume_ratio:
            return False
    if rule.require_below_ema9:
        ema = signal.get("ema9", np.nan)
        if not np.isfinite(ema) or signal["close"] >= ema:
            return False
    if rule.require_above_ema9:
        ema = signal.get("ema9", np.nan)
        if not np.isfinite(ema) or signal["close"] <= ema:
            return False
    if rule.require_neg_ema_slope:
        s = signal.get("ema9_slope", np.nan)
        if not np.isfinite(s) or s >= 0:
            return False
    if rule.require_pos_ema_slope:
        s = signal.get("ema9_slope", np.nan)
        if not np.isfinite(s) or s <= 0:
            return False
    if rule.max_signal_ema_slope is not None:
        s = signal.get("ema9_slope", np.nan)
        if not np.isfinite(s) or s > rule.max_signal_ema_slope:
            return False
    if rule.min_signal_ema_slope is not None:
        s = signal.get("ema9_slope", np.nan)
        if not np.isfinite(s) or s < rule.min_signal_ema_slope:
            return False
    if rule.min_or_close_pos is not None:
        if not np.isfinite(ctx.or_close_position) or ctx.or_close_position < rule.min_or_close_pos:
            return False
    if rule.max_or_close_pos is not None:
        if not np.isfinite(ctx.or_close_position) or ctx.or_close_position > rule.max_or_close_pos:
            return False
    if rule.max_close_vwap_delta is not None:
        if rule.side == "long":
            delta = float(signal["close"] - signal["vwap"])
            if not np.isfinite(delta) or delta > rule.max_close_vwap_delta:
                return False
        else:
            delta = float(signal["vwap"] - signal["close"])
            if not np.isfinite(delta) or delta > rule.max_close_vwap_delta:
                return False
    return True


def simulate_exit(ctx, side, entry_ts, entry_price, stop_price, target_price, time_exit):
    end_ts = timestamp_on_day(ctx, time_exit)
    sub = ctx.features[(ctx.features.index >= entry_ts) & (ctx.features.index <= end_ts)]
    if sub.empty:
        return None
    if side == "long":
        if not stop_price < entry_price < target_price:
            return None
        for ts, bar in sub.iterrows():
            if bar["low"] <= stop_price:
                return ts, stop_price, "stop"
            if bar["high"] >= target_price:
                return ts, target_price, "target"
    else:
        if not target_price < entry_price < stop_price:
            return None
        for ts, bar in sub.iterrows():
            if bar["high"] >= stop_price:
                return ts, stop_price, "stop"
            if bar["low"] <= target_price:
                return ts, target_price, "target"
    last = sub.iloc[-1]
    return last.name, float(last["close"]), "time_exit"


def evaluate_rule(ctx, rule: Rule, regime_map, atr_series, slippage_rt: float | None = None):
    if not ctx.clean or not ctx.full_session_clean:
        return None
    if ctx.or_class not in rule.or_classes:
        return None
    if not regime_passes(rule, regime_map, ctx.date):
        return None
    slip = slippage_rt if slippage_rt is not None else rule.slippage_rt

    # Compute ATR-scaled stop if requested
    stop_pts = rule.stop_points
    if rule.atr_stop_multiplier is not None and atr_series is not None:
        atr = atr_series.get(pd.to_datetime(ctx.date))
        if atr is not None and pd.notna(atr) and atr > 0:
            stop_pts = float(atr * rule.atr_stop_multiplier)
        else:
            return None

    sigs = short_signals_for(ctx, rule) if rule.side == "short" else long_signals_for(ctx, rule)
    if sigs.empty:
        return None

    for signal_ts, signal in sigs.iterrows():
        if not filters_pass(signal, ctx, rule):
            continue
        eligible = ctx.features[ctx.features.index > signal_ts]
        if eligible.empty:
            return None
        entry_ts = eligible.index[0]
        raw_entry = float(eligible.iloc[0]["open"])
        entry_price = apply_slippage(raw_entry, rule.side, "entry", slip)
        if rule.side == "long":
            stop_price = entry_price - stop_pts
            target_price = entry_price + rule.rr * stop_pts
        else:
            stop_price = entry_price + stop_pts
            target_price = entry_price - rule.rr * stop_pts
        result = simulate_exit(ctx, rule.side, entry_ts, entry_price, stop_price, target_price, rule.time_exit)
        if result is None:
            return None
        exit_ts, raw_exit, reason = result
        exit_price = apply_slippage(raw_exit, rule.side, "exit", slip)
        return {
            "date": ctx.date, "rule": rule.name, "thesis": rule.thesis, "side": rule.side,
            "signal_ts": signal_ts, "entry_ts": entry_ts,
            "entry_price": entry_price, "stop_price": stop_price, "target_price": target_price,
            "exit_ts": exit_ts, "exit_price": exit_price, "exit_reason": reason,
            "net_pnl": net_pnl(entry_price, exit_price, rule.side),
        }
    return None


# ============================================================================
# THESIS GENERATORS
# ============================================================================

def thesis_T1_volatility_conditional() -> list[Rule]:
    """T1: ATR-scaled stops on canonical mirror short. Story: high-vol days need
    bigger stops to avoid noise stop-outs, but with proportionally bigger targets."""
    out = []
    for atr_mult in [0.8, 1.0, 1.2, 1.5]:
        for rr in [1.0, 1.25, 1.5]:
            for regime in [None, "atr_pct>1.3", "5d_ret<-1.0%"]:
                regs = (regime,) if regime else ()
                out.append(Rule(
                    name=f"T1_atrstop{atr_mult}_rr{rr}_{regime or 'noregime'}",
                    thesis="T1_vol_conditional",
                    side="short", setup="mirror", or_classes=("normal",),
                    start="10:00", end="11:00",
                    stop_points=40.0,  # placeholder; ATR override
                    rr=rr,
                    atr_stop_multiplier=atr_mult,
                    regime_keys_all=regs,
                ))
    return out


def thesis_T2_day_after_sequential() -> list[Rule]:
    """T2: Yesterday close direction predicts bias. After down day, shorts work;
    after up day, longs work."""
    out = []
    # Short on day after big down
    for thresh_key in ["prev_day<-0.5%", "prev_day<-1%"]:
        for stop, rr in [(40, 1.25), (50, 1.0), (25, 1.6)]:
            out.append(Rule(
                name=f"T2_short_after_{thresh_key}_s{stop}_rr{rr}",
                thesis="T2_day_after",
                side="short", setup="mirror", or_classes=("normal",),
                stop_points=stop, rr=rr,
                regime_keys_all=(thresh_key,),
            ))
    # Long on day after up
    for stop, rr in [(40, 1.25), (50, 1.0)]:
        out.append(Rule(
            name=f"T2_long_after_prev_day_up_s{stop}_rr{rr}",
            thesis="T2_day_after",
            side="long", setup="tier1",  # use existing tier1 mechanics via long_signals_for
            or_classes=("normal",), stop_points=stop, rr=rr,
            regime_keys_all=("prev_day>0.5%",),
        ))
    return out


def thesis_T3_higher_freq_short() -> list[Rule]:
    """T3: Looser regime threshold = more shorts = bigger sample. Trade off PF
    for n. Worth it if PF stays > 1.4."""
    out = []
    for regime, label in [("5d_ret<-0.5%", "5d05"), ("atr_pct>1.1", "atr11"),
                           ("gap_pct<-0.10%", "gap01"), ("sma20_slope_neg", "smaneg")]:
        for stop, rr in [(40, 1.25), (50, 1.0), (30, 1.5)]:
            out.append(Rule(
                name=f"T3_loose_{label}_s{stop}_rr{rr}",
                thesis="T3_higher_freq",
                side="short", setup="mirror", or_classes=("normal",),
                stop_points=stop, rr=rr,
                regime_keys_all=(regime,),
            ))
    return out


def thesis_T4_regime_intersection() -> list[Rule]:
    """T4: Dual-confirmation regimes. Two filters = higher conviction = better expectancy."""
    out = []
    pairs = [
        ("atr_pct>1.3", "5d_ret<-1.0%"),
        ("atr_pct>1.3", "gap_pct<-0.30%"),
        ("5d_ret<-1.0%", "sma20_slope_neg"),
        ("atr_pct>1.3", "sma20_slope_neg"),
        ("gap_pct<-0.30%", "sma20_slope_neg"),
    ]
    for r1, r2 in pairs:
        for stop, rr in [(40, 1.25), (50, 1.0)]:
            out.append(Rule(
                name=f"T4_intersect_{r1.split('<')[0]}_AND_{r2.split('<')[0]}_s{stop}",
                thesis="T4_intersection",
                side="short", setup="mirror", or_classes=("normal",),
                stop_points=stop, rr=rr,
                regime_keys_all=(r1, r2),
            ))
    return out


def thesis_T5_volume_confirmation() -> list[Rule]:
    """T5: High-volume signal bars are more reliable. Test for both LONG and SHORT."""
    out = []
    for vol_min in [1.5, 2.0, 2.5]:
        # Long Tier 1 with volume
        out.append(Rule(
            name=f"T5_long_t1_vol>{vol_min}",
            thesis="T5_volume_conf",
            side="long", setup="tier1", or_classes=("normal",),
            stop_points=40, rr=1.25, max_close_vwap_delta=65.0,
            min_volume_ratio=vol_min,
        ))
        # Short mirror with volume
        for regime in [None, "atr_pct>1.3"]:
            regs = (regime,) if regime else ()
            out.append(Rule(
                name=f"T5_short_mirror_vol>{vol_min}_{regime or 'noregime'}",
                thesis="T5_volume_conf",
                side="short", setup="mirror", or_classes=("normal",),
                stop_points=40, rr=1.25, min_volume_ratio=vol_min,
                regime_keys_all=regs,
            ))
    return out


def thesis_T6_extended_windows() -> list[Rule]:
    """T6: Test time windows beyond the canonical 10-11. Lunch fade, afternoon trend,
    power hour."""
    out = []
    windows = [("11:00","12:00","11-12"), ("12:00","13:00","12-13"),
               ("13:00","14:00","13-14"), ("14:00","15:00","14-15"),
               ("14:30","15:30","1430-1530"), ("15:00","15:50","15-1550")]
    for s, e, lab in windows:
        for side, setup in [("short", "mirror"), ("long", "tier1")]:
            out.append(Rule(
                name=f"T6_{side}_{setup}_{lab}",
                thesis="T6_extended_windows",
                side=side, setup=setup, or_classes=("normal",),
                start=s, end=e, stop_points=40, rr=1.25,
            ))
    return out


def thesis_T7_vwap_variations() -> list[Rule]:
    """T7: VWAP delta cap variations. Maybe the 65pt cap is wrong. Test stricter
    and looser."""
    out = []
    for vwap_cap in [30.0, 50.0, 100.0, None]:
        # Long
        out.append(Rule(
            name=f"T7_long_t1_vwap{vwap_cap if vwap_cap else 'none'}",
            thesis="T7_vwap_var",
            side="long", setup="tier1", or_classes=("normal",),
            stop_points=40, rr=1.25, max_close_vwap_delta=vwap_cap,
        ))
        # Short
        out.append(Rule(
            name=f"T7_short_mirror_vwap{vwap_cap if vwap_cap else 'none'}_atr",
            thesis="T7_vwap_var",
            side="short", setup="mirror", or_classes=("normal",),
            stop_points=40, rr=1.25, max_close_vwap_delta=vwap_cap,
            regime_keys_all=("atr_pct>1.3",),
        ))
    return out


def thesis_T8_aplus_decomposition() -> list[Rule]:
    """T8: A+ uses BOTH ema_slope<=20 AND or_close_pos>=0.4. Test each alone to
    see which carries the edge."""
    out = []
    # Slope alone
    out.append(Rule(
        name="T8_aplus_slope_only",
        thesis="T8_aplus_decomp",
        side="long", setup="tier1", or_classes=("normal",),
        stop_points=40, rr=1.25, max_close_vwap_delta=65.0,
        max_signal_ema_slope=20.0,
    ))
    # Position alone
    out.append(Rule(
        name="T8_aplus_pos_only",
        thesis="T8_aplus_decomp",
        side="long", setup="tier1", or_classes=("normal",),
        stop_points=40, rr=1.25, max_close_vwap_delta=65.0,
        min_or_close_pos=0.4,
    ))
    # Looser slope variations
    for slope_max in [15.0, 25.0, 30.0]:
        out.append(Rule(
            name=f"T8_slope_only_{slope_max:g}",
            thesis="T8_aplus_decomp",
            side="long", setup="tier1", or_classes=("normal",),
            stop_points=40, rr=1.25, max_close_vwap_delta=65.0,
            max_signal_ema_slope=slope_max,
        ))
    # Looser pos variations
    for pos_min in [0.3, 0.5, 0.6]:
        out.append(Rule(
            name=f"T8_pos_only_{pos_min:g}",
            thesis="T8_aplus_decomp",
            side="long", setup="tier1", or_classes=("normal",),
            stop_points=40, rr=1.25, max_close_vwap_delta=65.0,
            min_or_close_pos=pos_min,
        ))
    return out


def all_theses() -> list[Rule]:
    out = []
    out.extend(thesis_T1_volatility_conditional())
    out.extend(thesis_T2_day_after_sequential())
    out.extend(thesis_T3_higher_freq_short())
    out.extend(thesis_T4_regime_intersection())
    out.extend(thesis_T5_volume_confirmation())
    out.extend(thesis_T6_extended_windows())
    out.extend(thesis_T7_vwap_variations())
    out.extend(thesis_T8_aplus_decomposition())
    return out


# ============================================================================
# STATS + PIPELINE
# ============================================================================

def summarize(rows) -> dict:
    df = pd.DataFrame(rows) if isinstance(rows, list) else rows
    if df.empty:
        return {"n": 0, "pnl": 0.0, "avg": float("nan"), "win_rate": float("nan"),
                "pf": float("nan"), "max_dd": 0.0}
    pnl = df["net_pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else float("inf")
    eq = pd.concat([pd.Series([0.0]), pnl.cumsum()], ignore_index=True)
    peak = eq.cummax()
    return {
        "n": int(len(df)), "pnl": float(pnl.sum()), "avg": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "pf": float(pf), "max_dd": float((peak - eq).max()),
    }


def t_pvalue(pnls):
    arr = np.asarray(pnls, dtype=float)
    if arr.size < 2:
        return 1.0
    mean = arr.mean()
    sd = arr.std(ddof=1)
    if sd == 0:
        return 0.0 if mean > 0 else 1.0
    t = mean / (sd / math.sqrt(arr.size))
    try:
        from scipy.stats import t as tdist
        return float(1.0 - tdist.cdf(t, df=arr.size - 1))
    except ImportError:
        return float(0.5 * math.erfc(t / math.sqrt(2.0)))


def bh_fdr(p_values: list[float], q: float) -> list[bool]:
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    crit = -1
    for rank, idx in enumerate(order):
        if p_values[idx] <= (rank + 1) / m * q:
            crit = rank
    rejected = [False] * m
    if crit >= 0:
        max_p = p_values[order[crit]]
        for i in range(m):
            if p_values[i] <= max_p:
                rejected[i] = True
    return rejected


def split_summary(rows):
    df = pd.DataFrame(rows) if isinstance(rows, list) else rows
    if df.empty:
        return summarize([]), summarize([])
    df = df.copy()
    df["d"] = pd.to_datetime(df["date"]).dt.date
    is_rows = df[df["d"] <= IS_END].to_dict("records")
    oos_rows = df[df["d"] > IS_END].to_dict("records")
    return summarize(is_rows), summarize(oos_rows)


def combined_portfolio(*lists_of_rows) -> pd.DataFrame:
    by_date: dict = {}
    for rows in lists_of_rows:
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
    out_rows = []
    for date, candidates in by_date.items():
        if len(candidates) == 1:
            out_rows.append(candidates[0])
        else:
            chosen = min(candidates, key=lambda r: r["entry_ts"])
            chosen = dict(chosen)
            chosen["collision"] = True
            out_rows.append(chosen)
    out = pd.DataFrame(out_rows)
    if not out.empty:
        out = out.sort_values("date").reset_index(drop=True)
    return out


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]
    print(f"Days in window: {len(in_window)}")

    regime_map, atr_series = build_regime_map(contexts)
    rules = all_theses()
    print(f"Variants across all theses: {len(rules)}")

    # ================== run all variants ==================
    raw = {}
    for rule in rules:
        rows = []
        for ctx in in_window:
            r = evaluate_rule(ctx, rule, regime_map, atr_series, slippage_rt=DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        raw[rule.name] = {"rule": rule, "rows": rows, "summary": summarize(rows)}

    # ================== per-thesis screening + BH-FDR ==================
    print("\n=== Per-thesis screening + BH-FDR (q=0.10) ===")
    by_thesis: dict[str, list[dict]] = {}
    for name, info in raw.items():
        thesis = info["rule"].thesis
        by_thesis.setdefault(thesis, []).append({"name": name, **info})

    survivors = []
    thesis_summaries = {}
    for thesis, items in sorted(by_thesis.items()):
        # Pre-screen: n>=20, pf>=1.2, avg>0
        screened = [it for it in items if it["summary"]["n"] >= 20 and it["summary"]["pf"] >= 1.2 and it["summary"]["avg"] > 0]
        pvals = [t_pvalue([r["net_pnl"] for r in it["rows"]]) for it in screened]
        rejected = bh_fdr(pvals, q=0.10)
        bh_pass = [it for it, rej in zip(screened, rejected) if rej]
        # Stats
        thesis_summaries[thesis] = {
            "tested": len(items),
            "screened": len(screened),
            "bh_survived": len(bh_pass),
        }
        print(f"\nThesis {thesis}: tested={len(items)}  screened={len(screened)}  BH-survived={len(bh_pass)}")
        # Show top 5 screened
        for it in sorted(screened, key=lambda x: -x["summary"]["pf"])[:5]:
            s = it["summary"]
            mark = " ***" if it in bh_pass else ""
            print(f"  {it['name']:60s}  n={s['n']:3d}  pf={s['pf']:.3f}  avg=${s['avg']:6.2f}  pnl=${s['pnl']:7.0f}{mark}")
        survivors.extend(bh_pass)

    print(f"\nTotal BH-FDR survivors across all theses: {len(survivors)}")

    # ================== IS/OOS for survivors ==================
    print("\n=== IS/OOS for survivors ===")
    oos_passers = []
    for s in survivors:
        is_, oos_ = split_summary(s["rows"])
        deg = abs(is_["avg"] - oos_["avg"]) / abs(is_["avg"]) if is_["avg"] != 0 and oos_["n"] > 0 else float("inf")
        ok = is_["pf"] >= 1.3 and oos_["pf"] >= 1.1 and oos_["n"] >= 8 and deg < 0.50
        s["is"] = is_
        s["oos"] = oos_
        s["deg"] = deg
        s["oos_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {s['name']:60s}  IS pf={is_['pf']:.2f}  OOS pf={oos_['pf']:.2f}  n_oos={oos_['n']:2d}  deg={deg:.2%}")
        if ok:
            oos_passers.append(s)
    print(f"\nOOS-validated survivors: {len(oos_passers)}")

    # ================== Slippage stress for OOS-passers ==================
    print("\n=== Slippage stress for OOS-validated (require pf>=1.2 at 8pt) ===")
    final = []
    for s in oos_passers:
        rule = s["rule"]
        slip_results = {}
        for slip in [3.0, 5.0, 8.0, 12.0]:
            rs = []
            for ctx in in_window:
                r = evaluate_rule(ctx, rule, regime_map, atr_series, slippage_rt=slip)
                if r is not None:
                    rs.append(r)
            slip_results[f"{slip}pt"] = summarize(rs)
        ok = slip_results["8.0pt"]["pf"] >= 1.2
        s["slippage"] = slip_results
        s["slip_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {s['name']:60s}  pf@5pt={slip_results['5.0pt']['pf']:.2f}  pf@8pt={slip_results['8.0pt']['pf']:.2f}")
        if ok:
            final.append(s)

    # ================== Combined portfolio with all final survivors ==================
    print(f"\n=== Combined portfolio backtest (final survivors + canonical Tier 1) ===")
    # Build canonical Tier 1 LONG rows (no regime filter)
    canonical_t1 = []
    for ctx in in_window:
        d = evaluate_rule_on_day(ctx, TIER1_PILOT, require_full_session_clean=True)
        if d.eligible:
            canonical_t1.append({
                "date": ctx.date, "rule": "canonical_t1", "thesis": "BASELINE", "side": "long",
                "entry_ts": d.entry_ts, "entry_price": d.entry_price, "exit_ts": d.exit_ts,
                "exit_price": d.exit_price, "exit_reason": d.exit_reason, "net_pnl": d.net_pnl,
            })
    canonical_t1_summary = summarize(canonical_t1)
    print(f"  Canonical Tier 1 LONG: n={canonical_t1_summary['n']}  pnl=${canonical_t1_summary['pnl']:.0f}  pf={canonical_t1_summary['pf']:.3f}  dd=${canonical_t1_summary['max_dd']:.0f}")

    final_combined_reports = []
    if final:
        # Show each final survivor's contribution alongside canonical
        for s in final:
            combo = combined_portfolio(canonical_t1, s["rows"])
            combo_summary = summarize(combo.to_dict("records"))
            collisions = int(combo["collision"].fillna(False).sum()) if "collision" in combo.columns else 0
            is_c, oos_c = split_summary(combo.to_dict("records"))
            final_combined_reports.append({
                "addition": s["name"],
                "summary_full": combo_summary, "is": is_c, "oos": oos_c, "collisions": collisions,
            })
            print(f"  +{s['name']:55s}  total n={combo_summary['n']:3d}  pnl=${combo_summary['pnl']:7.0f}  pf={combo_summary['pf']:.3f}  dd=${combo_summary['max_dd']:6.0f}  collisions={collisions}")
            print(f"    IS pf={is_c['pf']:.2f}  OOS pf={oos_c['pf']:.2f}  OOS pnl=${oos_c['pnl']:.0f}  OOS dd=${oos_c['max_dd']:.0f}")

        # Combine ALL final survivors with canonical
        all_rows = [canonical_t1]
        for s in final:
            all_rows.append(s["rows"])
        full_combo = combined_portfolio(*all_rows)
        full_summary = summarize(full_combo.to_dict("records"))
        full_is, full_oos = split_summary(full_combo.to_dict("records"))
        full_collisions = int(full_combo["collision"].fillna(False).sum()) if "collision" in full_combo.columns else 0
        print(f"\n  ALL SURVIVORS + canonical: n={full_summary['n']}  pnl=${full_summary['pnl']:.0f}  pf={full_summary['pf']:.3f}  dd=${full_summary['max_dd']:.0f}  collisions={full_collisions}")
        print(f"    IS pf={full_is['pf']:.2f}  OOS pf={full_oos['pf']:.2f}  OOS pnl=${full_oos['pnl']:.0f}  OOS dd=${full_oos['max_dd']:.0f}")

    # ================== Save ==================
    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "n_variants": len(rules),
        "thesis_summary": thesis_summaries,
        "survivors": [
            {"name": s["name"], "thesis": s["rule"].thesis, "summary": s["summary"],
             "is": s.get("is"), "oos": s.get("oos"),
             "oos_pass": s.get("oos_pass"), "slippage": s.get("slippage"),
             "slip_pass": s.get("slip_pass")}
            for s in survivors
        ],
        "final_survivors": [s["name"] for s in final],
        "canonical_t1_summary": canonical_t1_summary,
        "combined_reports": final_combined_reports,
    }
    out_dir.joinpath("thesis_v4_raw.json").write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {out_dir}/thesis_v4_raw.json")
    return payload


if __name__ == "__main__":
    main()
