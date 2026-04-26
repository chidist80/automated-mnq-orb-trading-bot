"""External-regime research v6.

Uses VIX, SPX, NDX daily data (Yahoo) to build new regime triggers and tests
whether they unlock additional short-side edge or improve the long-side
risk-adjusted profile.

Pre-registered hypotheses:
  E1  SHORT when VIX > 20
  E2  SHORT when VIX > 25
  E3  SHORT when VIX > 30
  E4  SHORT when SPX 50d-SMA slope negative
  E5  SHORT when SPX 20d return < -2%
  E6  SHORT when NDX 5d return < -1% (more correlated to MNQ than SPX)
  E7  SHORT when NDX gap-down > 0.5% (overnight weakness signal)
  E8  SHORT with VIX>20 AND NDX 5d<-1% (dual external confirmation)
  E9  SHORT ATR-target (1.0xATR) with VIX>20 regime — combine with v5 winner
  E10 SHORT ATR-target (1.0xATR) with VIX>20 AND NDX 5d<-1% intersection
  E11 LONG canonical halt-when VIX > 25
  E12 LONG canonical halt-when SPX 50d slope negative

Each tested via Stage A (n>=15, PF>=1.2, avg>0), per-thesis BH-FDR (q=0.10),
IS/OOS at 2025-06-30, 8pt slippage stress.

Then combined backtest with canonical Tier 1 LONG and the v5 best SHORT.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, replace
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
from thesis_research_v4 import (  # noqa: E402
    summarize, t_pvalue, bh_fdr, split_summary, combined_portfolio,
    short_signals_for, filters_pass,
)
from thesis_research_v5 import RuleV5, evaluate_rule_v5  # noqa: E402

WINDOW_START = "2024-01-01"
IS_END = pd.Timestamp("2025-06-30").date()


def build_external_regime_map(project_root: Path) -> tuple[dict[str, dict[str, bool]], pd.Series]:
    """Build regime triggers from VIX, SPX, NDX daily data.

    All triggers are causal: they use values from day t-1 (yesterday's close)
    to gate trades on day t.
    """
    vix = pd.read_parquet(project_root / "data/vix_1d.parquet")
    spx = pd.read_parquet(project_root / "data/spx_1d.parquet")
    ndx = pd.read_parquet(project_root / "data/ndx_1d.parquet")

    # Use index as date in US/Eastern
    def to_date_keyed(df, col="close"):
        s = df[col].copy()
        s.index = pd.to_datetime(s.index).date
        return s

    vix_close = to_date_keyed(vix)
    spx_close = to_date_keyed(spx)
    ndx_close = to_date_keyed(ndx)
    spx_open = to_date_keyed(spx, "open")
    ndx_open = to_date_keyed(ndx, "open")

    # Compute trailing series; SHIFT BY 1 to ensure causal (use t-1 to gate t)
    def to_dict(s, predicate):
        out = {}
        for d, v in s.items():
            out[str(d)] = bool(predicate(v)) if pd.notna(v) else False
        return out

    # VIX regimes — use yesterday's close
    vix_yesterday = vix_close.shift(1)
    # SPX regimes
    spx_yesterday = spx_close.shift(1)
    spx_50d_sma = spx_close.shift(1).rolling(50).mean()
    spx_50d_slope = spx_50d_sma.diff(5)
    spx_20d_return = spx_close.shift(1).pct_change(20)
    # NDX regimes
    ndx_yesterday = ndx_close.shift(1)
    ndx_5d_return = ndx_close.shift(1).pct_change(5)
    ndx_open_today = ndx_open  # today's open
    ndx_prev_close = ndx_close.shift(1)
    ndx_gap_pct = (ndx_open_today - ndx_prev_close) / ndx_prev_close

    return {
        "vix>20": to_dict(vix_yesterday, lambda v: v > 20),
        "vix>25": to_dict(vix_yesterday, lambda v: v > 25),
        "vix>30": to_dict(vix_yesterday, lambda v: v > 30),
        "spx_50d_slope_neg": to_dict(spx_50d_slope, lambda v: v < 0),
        "spx_20d_ret<-2%": to_dict(spx_20d_return, lambda v: v < -0.02),
        "ndx_5d_ret<-1%": to_dict(ndx_5d_return, lambda v: v < -0.01),
        "ndx_gap<-0.5%": to_dict(ndx_gap_pct, lambda v: v < -0.005),
    }, vix_close


def coverage_check(regime_map):
    """Print fraction of days each regime fires for context."""
    print("Regime trigger coverage (fraction of days TRUE):")
    for k, m in regime_map.items():
        fires = sum(1 for v in m.values() if v)
        print(f"  {k:25s}: {fires:>3d} / {len(m)} days  ({fires/len(m)*100:.1f}%)")


def evaluate_short_with_external(ctx, rule: RuleV5, regime_map_internal,
                                  regime_map_external, atr_series, slippage_rt):
    """Evaluator that takes BOTH internal (MNQ-derived) and external regimes."""
    if not ctx.clean or not ctx.full_session_clean:
        return None
    if ctx.or_class not in rule.or_classes:
        return None
    # Check internal regime
    for k in rule.regime_keys_all:
        # Could be in internal OR external map
        m_int = regime_map_internal.get(k, {})
        m_ext = regime_map_external.get(k, {})
        m = m_int if m_int else m_ext
        if not m.get(ctx.date, False):
            return None

    slip = slippage_rt
    stop_pts = rule.stop_points

    sigs = short_signals_for(ctx, rule) if rule.side == "short" else None
    if sigs is None or sigs.empty:
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

        if rule.atr_target_multiplier is not None and atr_series is not None:
            atr = atr_series.get(pd.to_datetime(ctx.date))
            if atr is None or pd.isna(atr) or atr <= 0:
                return None
            target_pts = float(atr * rule.atr_target_multiplier)
        else:
            target_pts = rule.rr * stop_pts

        stop_price = entry_price + stop_pts
        target_price = entry_price - target_pts

        # Use simulate_exit from v5
        from thesis_research_v4 import simulate_exit
        result = simulate_exit(ctx, rule.side, entry_ts, entry_price, stop_price, target_price, rule.time_exit)
        if result is None:
            return None
        exit_ts, raw_exit, reason = result
        exit_price = apply_slippage(raw_exit, rule.side, "exit", slip)
        return {
            "date": ctx.date, "rule": rule.name, "side": rule.side,
            "signal_ts": signal_ts, "entry_ts": entry_ts,
            "entry_price": entry_price, "stop_price": stop_price, "target_price": target_price,
            "exit_ts": exit_ts, "exit_price": exit_price, "exit_reason": reason,
            "net_pnl": net_pnl(entry_price, exit_price, rule.side),
        }
    return None


def evaluate_long_with_external_halt(ctx, base_rule, regime_map_external, halt_key):
    if halt_key is not None:
        if regime_map_external.get(halt_key, {}).get(ctx.date, False):
            return None
    d = evaluate_rule_on_day(ctx, base_rule, require_full_session_clean=True)
    if not d.eligible:
        return None
    return {
        "date": ctx.date, "rule": base_rule.name + (f"_halt_{halt_key}" if halt_key else ""),
        "side": "long", "entry_ts": d.entry_ts, "exit_ts": d.exit_ts,
        "net_pnl": d.net_pnl,
    }


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]

    # Build internal regime map (atr_series, etc.)
    from thesis_research_v4 import build_regime_map
    internal_map, atr_series = build_regime_map(contexts)
    # External regime map
    external_map, vix_close = build_external_regime_map(PROJECT_ROOT)

    print(f"Days in window: {len(in_window)}")
    coverage_check(external_map)
    print()

    # Define hypotheses
    hypotheses = []
    base_short = {
        "thesis": "external", "side": "short", "setup": "mirror",
        "or_classes": ("normal",), "start": "10:00", "end": "11:00",
        "stop_points": 40, "rr": 1.25, "max_close_vwap_delta": None,
    }
    # E1-E3 VIX
    for vix_level, key in [(20, "vix>20"), (25, "vix>25"), (30, "vix>30")]:
        hypotheses.append(RuleV5(name=f"E_short_{key}", regime_keys_all=(key,), **base_short))
    # E4 SPX 50d slope
    hypotheses.append(RuleV5(name="E_short_spx50slope", regime_keys_all=("spx_50d_slope_neg",), **base_short))
    # E5 SPX 20d return
    hypotheses.append(RuleV5(name="E_short_spx20d_ret", regime_keys_all=("spx_20d_ret<-2%",), **base_short))
    # E6 NDX 5d return
    hypotheses.append(RuleV5(name="E_short_ndx5d_ret", regime_keys_all=("ndx_5d_ret<-1%",), **base_short))
    # E7 NDX gap
    hypotheses.append(RuleV5(name="E_short_ndx_gap", regime_keys_all=("ndx_gap<-0.5%",), **base_short))
    # E8 dual external
    hypotheses.append(RuleV5(name="E_short_vix20_AND_ndx5d", regime_keys_all=("vix>20", "ndx_5d_ret<-1%"), **base_short))
    # E9 ATR-target with VIX
    hypotheses.append(RuleV5(name="E_short_atrtarget_vix20", atr_target_multiplier=1.0,
                              regime_keys_all=("vix>20",), **base_short))
    # E10 ATR-target with VIX + NDX
    hypotheses.append(RuleV5(name="E_short_atrtarget_vix20_AND_ndx5d", atr_target_multiplier=1.0,
                              regime_keys_all=("vix>20", "ndx_5d_ret<-1%"), **base_short))

    # Run shorts
    print("=== Short-side external-regime hypotheses ===")
    raw = []
    for rule in hypotheses:
        rows = []
        for ctx in in_window:
            r = evaluate_short_with_external(ctx, rule, internal_map, external_map, atr_series, DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        s = summarize(rows)
        raw.append({"rule": rule.name, "ext_keys": list(rule.regime_keys_all), "summary": s, "rows": rows})
        print(f"  {rule.name:50s}  n={s['n']:3d}  pf={s['pf']:.3f}  avg=${s['avg']:6.2f}  pnl=${s['pnl']:7.0f}")

    # Pre-screen + BH-FDR
    screened = [r for r in raw if r["summary"]["n"] >= 15 and r["summary"]["pf"] >= 1.2 and r["summary"]["avg"] > 0]
    pvals = [t_pvalue([row["net_pnl"] for row in r["rows"]]) for r in screened]
    rejected = bh_fdr(pvals, q=0.10)
    bh_pass = [r for r, rej in zip(screened, rejected) if rej]
    print(f"\nScreened: {len(screened)}  BH-FDR survivors: {len(bh_pass)}")

    # IS/OOS for screened
    print("\n=== IS/OOS for screened short variants ===")
    oos_passers = []
    for r in screened:
        is_, oos_ = split_summary(r["rows"])
        deg = abs(is_["avg"] - oos_["avg"]) / abs(is_["avg"]) if is_["avg"] != 0 and oos_["n"] > 0 else float("inf")
        ok = is_["pf"] >= 1.3 and oos_["pf"] >= 1.1 and oos_["n"] >= 6 and deg < 0.50
        r["is"] = is_; r["oos"] = oos_; r["deg"] = deg; r["oos_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {r['rule']:50s}  IS pf={is_['pf']:.2f}  OOS pf={oos_['pf']:.2f}  n_oos={oos_['n']:2d}  deg={deg:.2%}")
        if ok:
            oos_passers.append(r)

    # Long-side regime kill-switches
    print("\n=== LONG-side external-regime halt tests ===")
    long_results = {}
    long_baseline = []
    for ctx in in_window:
        r = evaluate_long_with_external_halt(ctx, TIER1_PILOT, external_map, None)
        if r is not None:
            long_baseline.append(r)
    sb = summarize(long_baseline)
    print(f"  Tier 1 LONG baseline: n={sb['n']}  pnl=${sb['pnl']:.0f}  pf={sb['pf']:.3f}  dd=${sb['max_dd']:.0f}")

    for halt_key in ["vix>20", "vix>25", "vix>30", "spx_50d_slope_neg", "ndx_5d_ret<-1%"]:
        rows = []
        for ctx in in_window:
            r = evaluate_long_with_external_halt(ctx, TIER1_PILOT, external_map, halt_key)
            if r is not None:
                rows.append(r)
        s = summarize(rows)
        long_results[halt_key] = {"summary": s, "rows": rows}
        is_, oos_ = split_summary(rows)
        print(f"  Tier 1 LONG halt-when[{halt_key}]: n={s['n']:3d}  pnl=${s['pnl']:.0f}  pf={s['pf']:.3f}  dd=${s['max_dd']:.0f}  | IS pf={is_['pf']:.2f}  OOS pf={oos_['pf']:.2f}  OOS n={oos_['n']}")

    # Combined backtest: canonical Tier 1 + each OOS-passing short
    print("\n=== Combined backtest: canonical Tier 1 LONG + each external-regime SHORT ===")
    canonical_long = []
    for ctx in in_window:
        d = evaluate_rule_on_day(ctx, TIER1_PILOT, require_full_session_clean=True)
        if d.eligible:
            canonical_long.append({
                "date": ctx.date, "rule": "tier1_canonical", "side": "long",
                "entry_ts": d.entry_ts, "exit_ts": d.exit_ts, "net_pnl": d.net_pnl,
            })
    cl_sum = summarize(canonical_long)
    print(f"  Canonical Tier 1 LONG: n={cl_sum['n']}  pnl=${cl_sum['pnl']:.0f}  pf={cl_sum['pf']:.3f}  dd=${cl_sum['max_dd']:.0f}")

    combined_reports = []
    # Test combined for ALL screened (not just OOS-passers, for completeness)
    for r in screened:
        combo = combined_portfolio(canonical_long, r["rows"])
        full = summarize(combo.to_dict("records"))
        is_c, oos_c = split_summary(combo.to_dict("records"))
        collisions = int(combo["collision"].fillna(False).sum()) if "collision" in combo.columns else 0
        combined_reports.append({
            "addition": r["rule"], "summary_full": full, "is": is_c, "oos": oos_c, "collisions": collisions,
        })
        print(f"  +{r['rule']:50s}  n={full['n']:3d}  pnl=${full['pnl']:.0f}  pf={full['pf']:.2f}  dd=${full['max_dd']:.0f}  | OOS pf={oos_c['pf']:.2f}  OOS pnl=${oos_c['pnl']:.0f}  OOS dd=${oos_c['max_dd']:.0f}")

    out = {
        "n_external_keys": len(external_map),
        "external_coverage": {k: sum(1 for v in m.values() if v) / len(m) for k, m in external_map.items()},
        "short_results": [{"rule": r["rule"], "summary": r["summary"], "is": r.get("is"), "oos": r.get("oos"), "oos_pass": r.get("oos_pass")} for r in raw],
        "long_halt_results": {k: {"summary": v["summary"]} for k, v in long_results.items()},
        "combined_with_canonical_t1": combined_reports,
    }
    out_path = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration/external_regime_v6_raw.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
