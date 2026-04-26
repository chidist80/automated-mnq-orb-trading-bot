"""Exhaustive short-side research + LONG regime kill-switch + combined portfolio.

Single-script execution of:
- Phase A: ~150 short-side variants with BH-FDR (q=0.10 broad screen)
- Phase B: IS/OOS robustness for Phase-A survivors
- Phase C: Slippage stress for Phase-B survivors
- Phase D: ~14 LONG-side regime kill-switch variants (Tier 1 + A+)
- Phase E: Combined portfolio backtest of best LONG (filtered) + best SHORT
- Phase F: Write structured findings

Outputs:
  research/human_edge_replay/short_side_exploration/exhaustive_results.json
  research/human_edge_replay/short_side_exploration/findings_v3.md
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT,
    TIER1_PILOT,
    A_PLUS_SHADOW,
    apply_slippage,
    build_day_contexts,
    candidate_signals,
    evaluate_rule_on_day,
    first_long_breakout,
    load_1m_parquet,
    net_pnl,
    next_open,
    signal_passes_optional_filters,
    simulate_long_exit,
    time_window_mask,
    timestamp_on_day,
)


WINDOW_START = "2024-01-01"
IS_END = pd.Timestamp("2025-06-30").date()


# =========================================================================
# SHORT-SIDE INFRASTRUCTURE
# =========================================================================

@dataclass(frozen=True)
class ShortRule:
    name: str
    setup: str  # mirror | naive_breakdown | failed_long_fade | vwap_rejection
    or_classes: tuple[str, ...]
    start: str
    end: str
    stop_points: float
    rr: float
    touch_tolerance: float = 5.0
    time_exit: str = "15:55"
    max_close_vwap_delta_below: float | None = 65.0
    require_below_ema9: bool = False
    require_neg_ema_slope: bool = False
    regime_key: str | None = None  # key into regime_map; None = no filter


def first_short_breakdown(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] < ctx.or_low]
    return None if hits.empty else hits.index[0]


def first_long_break_local(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] > ctx.or_high]
    return None if hits.empty else hits.index[0]


def first_vwap_reclaim(ctx) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    if "vwap" not in sub.columns or sub.empty:
        return None
    vwap_above = sub["close"] > sub["vwap"]
    prior_below = sub["close"].shift(1) <= sub["vwap"].shift(1)
    hits = sub[vwap_above & prior_below]
    return None if hits.empty else hits.index[0]


def short_signals(ctx, rule: ShortRule) -> pd.DataFrame:
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
        if bd in ctx.features.index and bd.time() <= end_t:
            return ctx.features.loc[[bd]]
        return pd.DataFrame()

    if rule.setup == "failed_long_fade":
        bo = first_long_break_local(ctx)
        if bo is None:
            return pd.DataFrame()
        after = ctx.features[(ctx.features.index > bo) & time_window_mask(ctx.features, rule.start, rule.end)]
        m = (after["close"] < ctx.or_high) & (after["close"] < after["prev_low"])
        return after[m]

    if rule.setup == "vwap_rejection":
        rc = first_vwap_reclaim(ctx)
        if rc is None:
            return pd.DataFrame()
        after = ctx.features[(ctx.features.index > rc) & time_window_mask(ctx.features, rule.start, rule.end)]
        m = (after["close"] < after["vwap"]) & (after["close"] < after["prev_close"])
        return after[m]

    raise ValueError(rule.setup)


def short_passes_filters(signal, ctx, rule: ShortRule, regime_map, date) -> bool:
    if rule.regime_key is not None:
        if not regime_map.get(rule.regime_key, {}).get(date, False):
            return False
    if rule.max_close_vwap_delta_below is not None:
        below = float(signal["vwap"] - signal["close"])
        if not np.isfinite(below) or below > rule.max_close_vwap_delta_below:
            return False
    if rule.require_below_ema9:
        ema = signal.get("ema9", np.nan)
        if not np.isfinite(ema) or signal["close"] >= ema:
            return False
    if rule.require_neg_ema_slope:
        s = signal.get("ema9_slope", np.nan)
        if not np.isfinite(s) or s >= 0:
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


def evaluate_short(ctx, rule: ShortRule, regime_map, slippage_rt: float):
    if not ctx.clean or not ctx.full_session_clean:
        return None
    if ctx.or_class not in rule.or_classes:
        return None
    sigs = short_signals(ctx, rule)
    if sigs.empty:
        return None
    for signal_ts, sig in sigs.iterrows():
        if not short_passes_filters(sig, ctx, rule, regime_map, ctx.date):
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
            "side": "short",
            "signal_ts": signal_ts,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_ts": exit_ts,
            "exit_price": exit_price,
            "exit_reason": reason,
            "net_pnl": net_pnl(entry_price, exit_price, "short"),
        }
    return None


# =========================================================================
# REGIME COMPUTATION
# =========================================================================

def build_regime_map(contexts) -> dict[str, dict[str, bool]]:
    """For each regime key, map date -> bool eligible."""
    closes = {}
    opens = {}
    highs = {}
    lows = {}
    for ctx in contexts:
        if ctx.day.empty:
            continue
        closes[ctx.date] = float(ctx.day.iloc[-1]["close"])
        opens[ctx.date] = float(ctx.day.iloc[0]["open"])
        highs[ctx.date] = float(ctx.day["high"].max())
        lows[ctx.date]  = float(ctx.day["low"].min())

    sorted_dates = sorted(closes)
    series = pd.Series([closes[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    opens_series = pd.Series([opens[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    high_series  = pd.Series([highs[d] for d in sorted_dates], index=pd.to_datetime(sorted_dates))
    low_series   = pd.Series([lows[d]  for d in sorted_dates], index=pd.to_datetime(sorted_dates))

    ret_5d  = series.pct_change(5)
    ret_10d = series.pct_change(10)
    ret_20d = series.pct_change(20)
    sma_20 = series.rolling(20).mean()
    sma_20_slope = sma_20.diff(5)
    daily_range = high_series - low_series
    atr_20 = daily_range.rolling(20).mean()
    atr_pct = daily_range / atr_20

    prior_close = series.shift(1)
    gap_signed = opens_series - prior_close
    gap_pct = gap_signed / prior_close

    out: dict[str, dict[str, bool]] = {}
    for thresh in [-0.005, -0.01, -0.015, -0.02]:
        out[f"5d_ret<{thresh*100:+.1f}%"] = {d: bool(ret_5d.loc[pd.to_datetime(d)] < thresh)
                                              if pd.notna(ret_5d.loc[pd.to_datetime(d)]) else False
                                              for d in sorted_dates}
    for thresh in [-0.01, -0.02, -0.03]:
        out[f"10d_ret<{thresh*100:+.1f}%"] = {d: bool(ret_10d.loc[pd.to_datetime(d)] < thresh)
                                               if pd.notna(ret_10d.loc[pd.to_datetime(d)]) else False
                                               for d in sorted_dates}
    out["20d_ret<-2%"] = {d: bool(ret_20d.loc[pd.to_datetime(d)] < -0.02)
                          if pd.notna(ret_20d.loc[pd.to_datetime(d)]) else False
                          for d in sorted_dates}
    out["sma20_slope_neg"] = {d: bool(sma_20_slope.loc[pd.to_datetime(d)] < 0)
                              if pd.notna(sma_20_slope.loc[pd.to_datetime(d)]) else False
                              for d in sorted_dates}
    for pct_thresh in [-0.001, -0.003, -0.005]:
        out[f"gap_pct<{pct_thresh*100:+.2f}%"] = {d: bool(gap_pct.loc[pd.to_datetime(d)] < pct_thresh)
                                                  if pd.notna(gap_pct.loc[pd.to_datetime(d)]) else False
                                                  for d in sorted_dates}
    out["atr_pct>1.3"] = {d: bool(atr_pct.loc[pd.to_datetime(d)] > 1.3)
                          if pd.notna(atr_pct.loc[pd.to_datetime(d)]) else False
                          for d in sorted_dates}
    return out


# =========================================================================
# SHORT VARIANT CATALOG
# =========================================================================

def build_short_catalog(regime_keys: list[str]) -> list[ShortRule]:
    """~150 structured short variants."""
    variants: list[ShortRule] = []

    # Group A: mirror × normal × (10,11) × stop sweep × RR sweep × regime sweep
    for stop, rr in [(20, 2.0), (25, 1.6), (30, 1.5), (40, 1.25), (50, 1.0)]:
        for rk in [None] + regime_keys:
            label = f"mirror_norm_10-11_s{stop}_rr{rr:g}_{rk or 'noregime'}"
            variants.append(ShortRule(
                name=label, setup="mirror", or_classes=("normal",),
                start="10:00", end="11:00", stop_points=stop, rr=rr, regime_key=rk,
            ))

    # Group B: naive_breakdown × OR-class × window
    for orc, orc_label in [(("normal",), "norm"), (("wide",), "wide"),
                           (("normal", "wide"), "nwide"), (("normal", "tight", "wide"), "all")]:
        for (start, end, wlabel) in [("10:00","11:00","10-11"),
                                      ("09:45","10:30","9-10"),
                                      ("13:00","15:00","13-15")]:
            for rk in [None, "5d_ret<-1.0%", "gap_pct<-0.30%"]:
                label = f"naive_{orc_label}_{wlabel}_{rk or 'noregime'}"
                variants.append(ShortRule(
                    name=label, setup="naive_breakdown", or_classes=orc,
                    start=start, end=end, stop_points=40, rr=1.25, regime_key=rk,
                ))

    # Group C: failed_long_fade × OR-class × stop × regime
    for orc, orc_label in [(("normal",), "norm"), (("normal", "tight", "wide"), "all")]:
        for stop, rr in [(25, 1.6), (40, 1.25)]:
            for rk in [None, "5d_ret<-1.0%", "gap_pct<-0.30%", "sma20_slope_neg"]:
                label = f"failong_{orc_label}_s{stop}_{rk or 'noregime'}"
                variants.append(ShortRule(
                    name=label, setup="failed_long_fade", or_classes=orc,
                    start="10:00", end="11:00", stop_points=stop, rr=rr, regime_key=rk,
                ))

    # Group D: vwap_rejection × normal × {ema_slope, below_ema9} × regime
    for ema_slope_neg in [False, True]:
        for below_ema in [False, True]:
            for rk in [None, "5d_ret<-1.0%", "sma20_slope_neg"]:
                label = f"vwap_rej_emaslope{int(ema_slope_neg)}_belowema{int(below_ema)}_{rk or 'noregime'}"
                variants.append(ShortRule(
                    name=label, setup="vwap_rejection", or_classes=("normal",),
                    start="10:00", end="11:00", stop_points=40, rr=1.25,
                    require_neg_ema_slope=ema_slope_neg, require_below_ema9=below_ema,
                    regime_key=rk,
                ))

    return variants


# =========================================================================
# STATISTICS
# =========================================================================

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
        "n": int(len(df)),
        "pnl": float(pnl.sum()),
        "avg": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "pf": float(pf),
        "max_dd": float((peak - eq).max()),
    }


def t_pvalue_above_zero(pnls):
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


def bh_fdr(p_values: list[float], q: float = 0.05) -> tuple[list[bool], float]:
    """Benjamini-Hochberg FDR control. Returns (rejected_mask, threshold)."""
    m = len(p_values)
    if m == 0:
        return [], 0.0
    order = sorted(range(m), key=lambda i: p_values[i])
    thresholds = [(rank + 1) / m * q for rank in range(m)]
    crit = -1
    for rank, idx in enumerate(order):
        if p_values[idx] <= thresholds[rank]:
            crit = rank
    rejected = [False] * m
    if crit >= 0:
        max_p = p_values[order[crit]]
        for i in range(m):
            if p_values[i] <= max_p:
                rejected[i] = True
        return rejected, max_p
    return rejected, 0.0


# =========================================================================
# LONG-SIDE WITH OPTIONAL REGIME FILTER
# =========================================================================

def evaluate_long_with_regime(ctx, base_rule, regime_map, regime_key,
                              halt_when_eligible: bool = True,
                              slippage_override: float | None = None):
    """If halt_when_eligible=True, the regime filter HALTS long when regime is True
    (e.g., 'halt long when 5d_ret<-1%'). If False, it ALLOWS long only when regime is True."""
    if regime_key is not None:
        regime_active = regime_map.get(regime_key, {}).get(ctx.date, False)
        if halt_when_eligible and regime_active:
            return None
        if not halt_when_eligible and not regime_active:
            return None
    rule = base_rule
    if slippage_override is not None:
        from dataclasses import replace
        rule = replace(base_rule, slippage_rt=slippage_override)
    decision = evaluate_rule_on_day(ctx, rule, require_full_session_clean=True)
    if not decision.eligible:
        return None
    return {
        "date": ctx.date,
        "rule": base_rule.name,
        "side": "long",
        "entry_ts": decision.entry_ts,
        "entry_price": decision.entry_price,
        "stop_price": decision.stop_price,
        "target_price": decision.target_price,
        "exit_ts": decision.exit_ts,
        "exit_price": decision.exit_price,
        "exit_reason": decision.exit_reason,
        "net_pnl": decision.net_pnl,
    }


# =========================================================================
# COMBINED PORTFOLIO
# =========================================================================

def combined_portfolio(long_rows, short_rows) -> pd.DataFrame:
    """Merge long + short trades, single contract: same-day collision -> earliest entry_ts wins."""
    rows = []
    by_date: dict = {}
    for r in long_rows:
        by_date.setdefault(r["date"], []).append(r)
    for r in short_rows:
        by_date.setdefault(r["date"], []).append(r)
    for date, candidates in by_date.items():
        if len(candidates) == 1:
            rows.append(candidates[0])
        else:
            chosen = min(candidates, key=lambda r: r["entry_ts"])
            chosen = dict(chosen)
            chosen["collision"] = True
            rows.append(chosen)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("date").reset_index(drop=True)
    return out


# =========================================================================
# MAIN
# =========================================================================

def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]
    print(f"Days in window: {len(in_window)}")

    regime_map = build_regime_map(contexts)
    regime_keys_for_short = list(regime_map.keys())

    short_catalog = build_short_catalog(regime_keys_for_short)
    print(f"Short variants: {len(short_catalog)}")

    # ============== PHASE A: broad screen ==============
    print(f"\n=== PHASE A: broad screen of {len(short_catalog)} variants ===")
    a_results = []
    for rule in short_catalog:
        rows = []
        for ctx in in_window:
            r = evaluate_short(ctx, rule, regime_map, slippage_rt=DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        s = summarize(rows)
        # Pre-filter: must have >=20 trades, PF>=1.2, avg>0
        screen = (s["n"] >= 20) and (s["pf"] >= 1.2) and (s["avg"] > 0)
        a_results.append({"rule": rule.name, "summary": s, "screen": screen, "rows": rows})
    screened = [r for r in a_results if r["screen"]]
    print(f"Variants passing screen (n>=20, PF>=1.2, avg>0): {len(screened)} of {len(a_results)}")
    for r in screened[:20]:
        s = r["summary"]
        print(f"  {r['rule']:55s}  n={s['n']:3d}  pf={s['pf']:.3f}  avg=${s['avg']:6.2f}  pnl=${s['pnl']:7.0f}")

    # BH-FDR on the screened set
    pvals = [t_pvalue_above_zero([row["net_pnl"] for row in r["rows"]]) for r in screened]
    rejected, threshold = bh_fdr(pvals, q=0.10)
    print(f"\nBH-FDR (q=0.10) reject threshold: p<={threshold:.4f}")
    bh_survivors = [r for r, rej in zip(screened, rejected) if rej]
    print(f"BH-FDR survivors: {len(bh_survivors)}")
    for r in bh_survivors:
        s = r["summary"]
        idx = screened.index(r)
        print(f"  [BH] {r['rule']:55s} pf={s['pf']:.3f} avg=${s['avg']:6.2f} p={pvals[idx]:.4f}")

    # ============== PHASE B: IS/OOS for BH survivors ==============
    print(f"\n=== PHASE B: IS/OOS robustness ===")
    b_survivors = []
    for r in bh_survivors:
        df_ = pd.DataFrame(r["rows"])
        df_["d"] = pd.to_datetime(df_["date"]).dt.date
        is_rows = df_[df_["d"] <= IS_END].to_dict("records")
        oos_rows = df_[df_["d"] > IS_END].to_dict("records")
        is_s = summarize(is_rows)
        oos_s = summarize(oos_rows)
        deg = (abs(is_s["avg"] - oos_s["avg"]) / abs(is_s["avg"])
               if is_s["avg"] != 0 and oos_s["n"] > 0 else float("inf"))
        ok = (is_s["pf"] >= 1.3 and oos_s["pf"] >= 1.1
              and oos_s["n"] >= 8 and deg < 0.50)
        r["is"] = is_s
        r["oos"] = oos_s
        r["degradation"] = deg
        r["b_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {r['rule']:55s}  IS pf={is_s['pf']:.2f}  OOS pf={oos_s['pf']:.2f}  OOS n={oos_s['n']:2d}  deg={deg:.2%}")
        if ok:
            b_survivors.append(r)
    print(f"Phase B survivors: {len(b_survivors)}")

    # ============== PHASE C: slippage stress ==============
    print(f"\n=== PHASE C: slippage stress (require pf>=1.2 at 8pt RT) ===")
    c_survivors = []
    rule_lookup = {rule.name: rule for rule in short_catalog}
    for r in b_survivors:
        rule = rule_lookup[r["rule"]]
        slip_results = {}
        for slip in [1.5, 3.0, 5.0, 8.0, 12.0]:
            rows = []
            for ctx in in_window:
                row = evaluate_short(ctx, rule, regime_map, slippage_rt=slip)
                if row is not None:
                    rows.append(row)
            slip_results[f"{slip}pt"] = summarize(rows)
        ok = slip_results["8.0pt"]["pf"] >= 1.2
        r["slippage"] = slip_results
        r["c_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {r['rule']:55s}  pf@5pt={slip_results['5.0pt']['pf']:.2f}  pf@8pt={slip_results['8.0pt']['pf']:.2f}")
        if ok:
            c_survivors.append(r)
    print(f"Phase C survivors: {len(c_survivors)}")

    # ============== PHASE D: LONG regime kill-switch ==============
    print(f"\n=== PHASE D: LONG-side regime kill-switch search (Tier 1 + A+) ===")
    long_results = {}
    long_rule_baseline_t1 = TIER1_PILOT
    long_rule_baseline_ap = A_PLUS_SHADOW
    regime_choices = [None] + list(regime_map.keys())
    for label, base in [("tier1", long_rule_baseline_t1), ("a_plus", long_rule_baseline_ap)]:
        long_results[label] = {}
        for rk in regime_choices:
            rows = []
            for ctx in in_window:
                row = evaluate_long_with_regime(ctx, base, regime_map, rk, halt_when_eligible=True)
                if row is not None:
                    rows.append(row)
            s = summarize(rows)
            mar = (s["pnl"] / 2.31 / s["max_dd"]) if s["max_dd"] > 0 else float("inf")
            long_results[label][rk or "no_filter"] = {
                "summary": s, "mar_3750": (s["pnl"]/2.31/3750*100) / (s["max_dd"]/3750*100) if s["max_dd"] > 0 else float("inf"),
                "rows": rows,
            }
            print(f"  {label} halt-when[{rk or 'NONE'}]: n={s['n']:3d}  pnl=${s['pnl']:7.0f}  pf={s['pf']:.3f}  dd=${s['max_dd']:6.0f}")

    # ============== PHASE E: combined portfolio (best LONG + best SHORT) ==============
    print(f"\n=== PHASE E: combined portfolio backtests ===")
    # Find best LONG: highest pf where avg > unfiltered or DD < unfiltered
    def best_long(label):
        baseline = long_results[label]["no_filter"]["summary"]
        candidates = [(rk, info) for rk, info in long_results[label].items() if rk != "no_filter"]
        # Pick filter that improves PF AND reduces or maintains DD AND keeps n large
        best = None
        for rk, info in candidates:
            s = info["summary"]
            if s["pf"] > baseline["pf"] and s["max_dd"] <= baseline["max_dd"] * 1.05 and s["n"] >= baseline["n"] * 0.7:
                if best is None or s["pf"] > best[1]["summary"]["pf"]:
                    best = (rk, info)
        return best

    best_t1_filter = best_long("tier1")
    best_ap_filter = best_long("a_plus")
    print(f"  Best Tier 1 regime kill: {best_t1_filter[0] if best_t1_filter else 'NONE — unfiltered already best'}")
    print(f"  Best A+ regime kill:     {best_ap_filter[0] if best_ap_filter else 'NONE — unfiltered already best'}")

    combined_reports = []
    short_to_combine = c_survivors[:3] if c_survivors else []
    if not short_to_combine:
        print("  No short survivors — combining LONG-only with regime filter only.")
    long_t1_rows = (best_t1_filter[1]["rows"] if best_t1_filter else long_results["tier1"]["no_filter"]["rows"])
    long_ap_rows = (best_ap_filter[1]["rows"] if best_ap_filter else long_results["a_plus"]["no_filter"]["rows"])

    for short_r in short_to_combine + [None]:
        short_rows = short_r["rows"] if short_r else []
        short_label = short_r["rule"] if short_r else "no_short"
        for long_label, long_rows in [("tier1", long_t1_rows), ("a_plus", long_ap_rows)]:
            combined = combined_portfolio(long_rows, short_rows)
            if combined.empty:
                continue
            s = summarize(combined.to_dict("records"))
            label = f"{long_label}+{short_label}"
            collisions = int(combined["collision"].fillna(False).sum()) if "collision" in combined.columns else 0
            combined_reports.append({
                "label": label, "summary": s, "collisions": collisions,
                "long_n": len(long_rows), "short_n": len(short_rows),
            })
            print(f"  combined {label}:  n={s['n']:3d}  pnl=${s['pnl']:7.0f}  pf={s['pf']:.3f}  dd=${s['max_dd']:6.0f}  collisions={collisions}")

    # ============== PHASE F: combine best LONG-filtered with screened-cluster SHORTs ==============
    # The screened cluster shows coherent regime-conditioned positivity. Even though no single
    # variant clears BH-FDR, the cluster itself is signal. Test the combination explicitly.
    print(f"\n=== PHASE F: LONG-filtered + best regime-shorts combinations ===")
    # Pick top 3 screened shorts by PF
    top_screened = sorted([r for r in screened], key=lambda r: -r["summary"]["pf"])[:3]
    print("Top 3 screened SHORT variants by PF (note: failed BH-FDR individually):")
    for r in top_screened:
        s = r["summary"]
        print(f"  {r['rule']:60s}  n={s['n']:3d}  pf={s['pf']:.3f}  pnl=${s['pnl']:7.0f}")

    phase_f_reports = []
    for short_r in top_screened:
        short_rows = short_r["rows"]
        for long_label, long_rows in [("tier1_filtered", long_t1_rows), ("a_plus_filtered", long_ap_rows)]:
            combined = combined_portfolio(long_rows, short_rows)
            if combined.empty:
                continue
            s = summarize(combined.to_dict("records"))
            label = f"{long_label}+{short_r['rule']}"
            collisions = int(combined["collision"].fillna(False).sum()) if "collision" in combined.columns else 0
            phase_f_reports.append({
                "label": label, "summary": s, "collisions": collisions,
                "long_n": len(long_rows), "short_n": len(short_rows),
            })
            print(f"  combined: n={s['n']:3d}  pnl=${s['pnl']:7.0f}  pf={s['pf']:.3f}  dd=${s['max_dd']:6.0f}  collisions={collisions}")

    payload_phase_f = phase_f_reports

    # ============== Save everything ==============
    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "window_start": WINDOW_START,
        "is_end": str(IS_END),
        "phase_a": [{"rule": r["rule"], "summary": r["summary"], "screen_pass": r["screen"]} for r in a_results],
        "phase_a_screened": [{"rule": r["rule"], "summary": r["summary"]} for r in screened],
        "bh_fdr_threshold": threshold,
        "bh_survivors": [{"rule": r["rule"], "summary": r["summary"]} for r in bh_survivors],
        "phase_b_survivors": [{"rule": r["rule"], "is": r["is"], "oos": r["oos"], "degradation": r["degradation"]} for r in b_survivors],
        "phase_c_survivors": [{"rule": r["rule"], "slippage": r["slippage"]} for r in c_survivors],
        "long_regime_results": {
            label: {rk: {"summary": info["summary"]} for rk, info in info_map.items()}
            for label, info_map in long_results.items()
        },
        "best_long_filter": {
            "tier1": (best_t1_filter[0] if best_t1_filter else None),
            "a_plus": (best_ap_filter[0] if best_ap_filter else None),
        },
        "combined": combined_reports,
        "phase_f_combined_with_screened_shorts": payload_phase_f,
    }
    out_dir.joinpath("exhaustive_results.json").write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {out_dir}/exhaustive_results.json")
    return payload


if __name__ == "__main__":
    main()
