"""V7 causal correction: rerun v5 cross-product with strictly causal regime features.

Root cause of v3-v5 invalidation:
  - 5d_ret, 10d_ret, 20d_ret, sma20_slope, atr_20, atr_pct were computed from
    same-day daily series (close, high, low). The regime gate was applied
    during the 10:00-11:00 ET trade window, BEFORE today's close/high/low
    are known.
  - ATR-scaled targets used same-day ATR_20 — same leak.

Causal fix in this script:
  - All daily-derived series shifted by 1 day (use through t-1, gate trade on t).
  - gap_pct uses today's RTH open vs prior close (causal — open is known at 09:30).
  - prev_day_change uses (t-1 close - t-2 close) / t-2 close (already causal).
  - ATR_20 used for targets is the average of daily ranges through day t-1.

Goal: independently confirm verifier's finding that v5 ATR-target short is
worse than canonical Tier 1 alone after causal correction.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT, TIER1_PILOT, A_PLUS_SHADOW,
    apply_slippage, build_day_contexts, evaluate_rule_on_day,
    load_1m_parquet, net_pnl, time_window_mask, timestamp_on_day,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from thesis_research_v4 import summarize, t_pvalue, bh_fdr, split_summary, combined_portfolio  # noqa: E402
from thesis_research_v5 import RuleV5, evaluate_rule_v5  # noqa: E402

WINDOW_START = "2024-01-01"
IS_END = pd.Timestamp("2025-06-30").date()


def build_causal_regime_map(contexts):
    """Strictly causal version of build_regime_map.

    Every daily-derived value uses ONLY data through day t-1.
    """
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

    # CAUSAL: shift by 1 so each series value at date t reflects only data through t-1
    cs_prior = cs.shift(1)
    hs_prior = hs.shift(1)
    ls_prior = ls.shift(1)
    daily_range_prior = hs_prior - ls_prior

    ret_5d = cs_prior.pct_change(5)  # (cs[t-1] - cs[t-6]) / cs[t-6]
    ret_10d = cs_prior.pct_change(10)
    ret_20d = cs_prior.pct_change(20)
    sma_20_slope = cs_prior.rolling(20).mean().diff(5)

    atr_20_prior = daily_range_prior.rolling(20).mean()  # avg of ranges through t-1
    # atr_pct: yesterday's range / yesterday's ATR_20
    atr_pct_prior = daily_range_prior / atr_20_prior

    # gap_pct uses today's open (causal, known at 09:30) vs prior close
    prev_close = cs.shift(1)
    gap_pct = (os_ - prev_close) / prev_close

    # prev_day_change: t-1 to t-2
    prev_day_change = (cs.shift(1) - cs.shift(2)) / cs.shift(2)

    def to_map(series, predicate):
        out = {}
        for d in sorted_dates:
            v = series.loc[pd.to_datetime(d)]
            out[d] = bool(predicate(v)) if pd.notna(v) else False
        return out

    regime_map = {
        "5d_ret<-0.5%": to_map(ret_5d, lambda v: v < -0.005),
        "5d_ret<-1.0%": to_map(ret_5d, lambda v: v < -0.01),
        "5d_ret<-1.5%": to_map(ret_5d, lambda v: v < -0.015),
        "5d_ret<-2.0%": to_map(ret_5d, lambda v: v < -0.02),
        "10d_ret<-1.0%": to_map(ret_10d, lambda v: v < -0.01),
        "10d_ret<-2.0%": to_map(ret_10d, lambda v: v < -0.02),
        "20d_ret<-2.0%": to_map(ret_20d, lambda v: v < -0.02),
        "sma20_slope_neg": to_map(sma_20_slope, lambda v: v < 0),
        "gap_pct<-0.10%": to_map(gap_pct, lambda v: v < -0.001),
        "gap_pct<-0.30%": to_map(gap_pct, lambda v: v < -0.003),
        "gap_pct<-0.50%": to_map(gap_pct, lambda v: v < -0.005),
        "atr_pct>1.1": to_map(atr_pct_prior, lambda v: v > 1.1),
        "atr_pct>1.3": to_map(atr_pct_prior, lambda v: v > 1.3),
        "atr_pct>1.5": to_map(atr_pct_prior, lambda v: v > 1.5),
        "prev_day<-1%": to_map(prev_day_change, lambda v: v < -0.01),
        "prev_day<-0.5%": to_map(prev_day_change, lambda v: v < -0.005),
    }

    # ATR series for targets — series of yesterday's ATR_20, indexed by date
    atr_for_targets = {}
    for d in sorted_dates:
        v = atr_20_prior.loc[pd.to_datetime(d)]
        atr_for_targets[pd.to_datetime(d)] = float(v) if pd.notna(v) else None

    # Coverage report
    print("CAUSAL regime trigger coverage (fraction TRUE):")
    for k, m in regime_map.items():
        fires = sum(1 for v in m.values() if v)
        print(f"  {k:25s}: {fires:>3d}/{len(m)} ({fires/len(m)*100:.1f}%)")

    return regime_map, atr_for_targets


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]

    regime_map, atr_series = build_causal_regime_map(contexts)

    # Define LONG configs (canonical T1 / T1+halt-mnq-5d / A+ canonical / A+ halt)
    long_configs = [
        ("L0_T1_canonical", TIER1_PILOT, None),
        ("L1_T1_halt_5d-1.0", TIER1_PILOT, "5d_ret<-1.0%"),
        ("L2_T1_halt_5d-1.5", TIER1_PILOT, "5d_ret<-1.5%"),
        ("L3_Aplus_canonical", A_PLUS_SHADOW, None),
        ("L4_Aplus_halt_5d-0.5", A_PLUS_SHADOW, "5d_ret<-0.5%"),
    ]
    short_configs = [
        ("S0_none", None),
        ("S1_T7_atrgate", RuleV5(
            name="S1", thesis="causal", side="short", setup="mirror",
            or_classes=("normal",), start="10:00", end="11:00",
            stop_points=40, rr=1.25, regime_keys_all=("atr_pct>1.3",),
            max_close_vwap_delta=None,
        )),
        ("S2_F1_atrtarget_5dgate", RuleV5(
            name="S2", thesis="causal", side="short", setup="mirror",
            or_classes=("normal",), start="10:00", end="11:00",
            stop_points=40, rr=1.25, atr_target_multiplier=1.0,
            regime_keys_all=("5d_ret<-1.0%",), max_close_vwap_delta=None,
        )),
    ]

    def evaluate_long_with_halt(ctx, base_rule, halt_key):
        if halt_key is not None:
            if regime_map.get(halt_key, {}).get(ctx.date, False):
                return None
        d = evaluate_rule_on_day(ctx, base_rule, require_full_session_clean=True)
        if not d.eligible:
            return None
        return {"date": ctx.date, "rule": base_rule.name, "side": "long",
                "entry_ts": d.entry_ts, "exit_ts": d.exit_ts, "net_pnl": d.net_pnl}

    long_rows_map = {}
    for label, rule, halt in long_configs:
        rows = []
        for ctx in in_window:
            r = evaluate_long_with_halt(ctx, rule, halt)
            if r is not None:
                rows.append(r)
        long_rows_map[label] = rows

    short_rows_map = {}
    for label, rule in short_configs:
        if rule is None:
            short_rows_map[label] = []
            continue
        rows = []
        for ctx in in_window:
            r = evaluate_rule_v5(ctx, rule, regime_map, atr_series, slippage_rt=DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        short_rows_map[label] = rows

    print(f"\n{'COMBO':40s}  {'n':>3s} {'PnL':>7s} {'PF':>5s} {'DD':>6s}  {'OOS_n':>5s} {'OOS_PnL':>7s} {'OOS_PF':>6s} {'OOS_DD':>6s}")
    print("=" * 120)
    reports = []
    for l_label, _, _ in long_configs:
        for s_label, _ in short_configs:
            long_rows = long_rows_map[l_label]
            short_rows = short_rows_map[s_label]
            combo = combined_portfolio(long_rows, short_rows) if short_rows else pd.DataFrame(long_rows)
            if combo.empty:
                continue
            full = summarize(combo.to_dict("records"))
            is_, oos_ = split_summary(combo.to_dict("records"))
            label = f"{l_label}+{s_label}"
            reports.append({"combo": label, "full": full, "is": is_, "oos": oos_})
            print(f"{label:40s}  {full['n']:>3d} {full['pnl']:>7.0f} {full['pf']:>5.2f} {full['max_dd']:>6.0f}  {oos_['n']:>5d} {oos_['pnl']:>7.0f} {oos_['pf']:>6.2f} {oos_['max_dd']:>6.0f}")

    out = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration/causal_correction_v7.json"
    out.write_text(json.dumps(reports, indent=2, default=str))
    print(f"\nWrote {out}")
    print("\n=== CRITICAL COMPARISON: L0+S2 leaked vs causal ===")
    print("  Leaked  (v5 reported):  OOS n=45  pnl=$1452  pf=1.80  dd=$338")
    causal_l0s2 = next((r for r in reports if r["combo"] == "L0_T1_canonical+S2_F1_atrtarget_5dgate"), None)
    if causal_l0s2:
        o = causal_l0s2["oos"]
        print(f"  Causal  (corrected):    OOS n={o['n']}  pnl=${o['pnl']:.0f}  pf={o['pf']:.2f}  dd=${o['max_dd']:.0f}")
        print(f"  Canonical alone:        OOS n=36  pnl=$672  pf=1.52  dd=$417")


if __name__ == "__main__":
    main()
