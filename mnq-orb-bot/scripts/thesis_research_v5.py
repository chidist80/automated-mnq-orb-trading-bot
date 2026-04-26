"""Thesis research v5 — focused follow-ups from v4.

Purpose: separate "real volatility edge" from "position-sizing artifact."

Theses:
  F1 ATR-scaled TARGETS with fixed 40pt stops, regime conditioned
     -> directly tests whether the ATR edge survives without bigger stops
  F2 T7 parameter refinement (ATR threshold, stop, RR sweeps around T7 baseline)
  F3 A+-style filters on the SHORT side (mirror of A+ long: neg-slope + low-or-pos)
  F4 Vol regime taxonomy (low/normal/high/extreme buckets)
  F5 Tier 1 LONG with ATR-scaled targets (does long benefit from vol-adaptive targets?)

Reuses thesis_research_v4.py infrastructure.
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
    DEFAULT_SLIPPAGE_RT, TIER1_PILOT, A_PLUS_SHADOW,
    apply_slippage, build_day_contexts, evaluate_rule_on_day,
    load_1m_parquet, net_pnl, time_window_mask, timestamp_on_day,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from thesis_research_v4 import (  # noqa: E402
    Rule, build_regime_map, summarize, t_pvalue, bh_fdr, split_summary,
    combined_portfolio, first_short_breakdown, first_long_breakout_local,
    short_signals_for, long_signals_for, regime_passes, filters_pass,
    simulate_exit, IS_END,
)

WINDOW_START = "2024-01-01"


@dataclass
class RuleV5(Rule):
    """Extended Rule: ATR-scaled targets + ATR threshold parameterization."""
    atr_target_multiplier: float | None = None  # if set, target = atr * this
    atr_threshold_min: float | None = None  # custom regime threshold (if not using regime_keys)
    atr_threshold_max: float | None = None


def evaluate_rule_v5(ctx, rule: RuleV5, regime_map, atr_series, slippage_rt: float | None = None):
    if not ctx.clean or not ctx.full_session_clean:
        return None
    if ctx.or_class not in rule.or_classes:
        return None
    if not regime_passes(rule, regime_map, ctx.date):
        return None
    # Custom ATR threshold gating (if used instead of regime_keys)
    if rule.atr_threshold_min is not None or rule.atr_threshold_max is not None:
        atr = atr_series.get(pd.to_datetime(ctx.date))
        if atr is None or pd.isna(atr) or atr <= 0:
            return None
        # Note: atr_series stored is raw ATR_20, not pct. Need to compute pct.
        # For simplicity use raw ATR threshold.
        if rule.atr_threshold_min is not None and atr < rule.atr_threshold_min:
            return None
        if rule.atr_threshold_max is not None and atr > rule.atr_threshold_max:
            return None

    slip = slippage_rt if slippage_rt is not None else rule.slippage_rt

    # Compute stop and target points
    stop_pts = rule.stop_points
    if rule.atr_stop_multiplier is not None and atr_series is not None:
        atr = atr_series.get(pd.to_datetime(ctx.date))
        if atr is None or pd.isna(atr) or atr <= 0:
            return None
        stop_pts = float(atr * rule.atr_stop_multiplier)

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

        # Target: ATR-scaled or RR-scaled
        if rule.atr_target_multiplier is not None and atr_series is not None:
            atr = atr_series.get(pd.to_datetime(ctx.date))
            if atr is None or pd.isna(atr) or atr <= 0:
                return None
            target_pts = float(atr * rule.atr_target_multiplier)
        else:
            target_pts = rule.rr * stop_pts

        if rule.side == "long":
            stop_price = entry_price - stop_pts
            target_price = entry_price + target_pts
        else:
            stop_price = entry_price + stop_pts
            target_price = entry_price - target_pts

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
# THESES
# ============================================================================

def thesis_F1_atr_targets_fixed_stops() -> list[RuleV5]:
    """F1: ATR-scaled targets with FIXED 40pt stops, regime conditioned.
    Direct test: does the vol-edge survive without changing per-trade risk?"""
    out = []
    for atr_target_mult in [0.4, 0.5, 0.75, 1.0, 1.25, 1.5]:
        for regime in ["atr_pct>1.3", "5d_ret<-1.0%"]:
            out.append(RuleV5(
                name=f"F1_short_atrtarget{atr_target_mult}_stop40_{regime}",
                thesis="F1_atr_targets",
                side="short", setup="mirror", or_classes=("normal",),
                start="10:00", end="11:00",
                stop_points=40.0, rr=1.25,  # rr ignored when atr_target_multiplier set
                atr_target_multiplier=atr_target_mult,
                regime_keys_all=(regime,),
                max_close_vwap_delta=None,  # T7 finding: no VWAP cap
            ))
    return out


def thesis_F2_T7_refinement() -> list[RuleV5]:
    """F2: Refine T7 (mirror short + atr_pct>1.3 + no VWAP cap). Vary stop, RR,
    ATR threshold, touch tolerance."""
    out = []
    for stop in [25, 30, 40, 50]:
        for rr in [1.0, 1.25, 1.5, 2.0]:
            for regime in ["atr_pct>1.1", "atr_pct>1.3", "atr_pct>1.5"]:
                out.append(RuleV5(
                    name=f"F2_T7ref_s{stop}_rr{rr:g}_{regime}",
                    thesis="F2_T7_refinement",
                    side="short", setup="mirror", or_classes=("normal",),
                    start="10:00", end="11:00",
                    stop_points=stop, rr=rr,
                    regime_keys_all=(regime,),
                    max_close_vwap_delta=None,
                ))
    # Touch tolerance variations
    for tt in [3, 8, 10]:
        out.append(RuleV5(
            name=f"F2_T7tt{tt}",
            thesis="F2_T7_refinement",
            side="short", setup="mirror", or_classes=("normal",),
            start="10:00", end="11:00",
            stop_points=40, rr=1.25, touch_tolerance=tt,
            regime_keys_all=("atr_pct>1.3",),
            max_close_vwap_delta=None,
        ))
    return out


def thesis_F3_aplus_short_mirror() -> list[RuleV5]:
    """F3: A+-style filters applied to SHORT side. Mirror of A+ long (slope<=20, pos>=0.4)
    becomes (slope>=-20, pos<=0.6) for short."""
    out = []
    # Both filters together
    for regime in [None, "atr_pct>1.3", "5d_ret<-1.0%"]:
        regs = (regime,) if regime else ()
        out.append(RuleV5(
            name=f"F3_aplus_short_full_{regime or 'noregime'}",
            thesis="F3_aplus_short",
            side="short", setup="mirror", or_classes=("normal",),
            start="10:00", end="11:00",
            stop_points=40, rr=1.25,
            min_signal_ema_slope=-20.0,
            max_or_close_pos=0.6,
            regime_keys_all=regs,
            max_close_vwap_delta=None,
        ))
    # Slope only
    for regime in [None, "atr_pct>1.3"]:
        regs = (regime,) if regime else ()
        out.append(RuleV5(
            name=f"F3_aplus_short_slope_only_{regime or 'noregime'}",
            thesis="F3_aplus_short",
            side="short", setup="mirror", or_classes=("normal",),
            start="10:00", end="11:00",
            stop_points=40, rr=1.25,
            min_signal_ema_slope=-20.0,
            regime_keys_all=regs,
            max_close_vwap_delta=None,
        ))
    # Pos only
    for regime in [None, "atr_pct>1.3"]:
        regs = (regime,) if regime else ()
        out.append(RuleV5(
            name=f"F3_aplus_short_pos_only_{regime or 'noregime'}",
            thesis="F3_aplus_short",
            side="short", setup="mirror", or_classes=("normal",),
            start="10:00", end="11:00",
            stop_points=40, rr=1.25,
            max_or_close_pos=0.6,
            regime_keys_all=regs,
            max_close_vwap_delta=None,
        ))
    # Tighter variants
    for slope_min in [-15.0, -10.0, 0.0]:
        out.append(RuleV5(
            name=f"F3_short_slope_min{slope_min:g}",
            thesis="F3_aplus_short",
            side="short", setup="mirror", or_classes=("normal",),
            start="10:00", end="11:00",
            stop_points=40, rr=1.25,
            min_signal_ema_slope=slope_min,
            regime_keys_all=("atr_pct>1.3",),
            max_close_vwap_delta=None,
        ))
    return out


def thesis_F4_vol_taxonomy() -> list[RuleV5]:
    """F4: Vol-bucket taxonomy. Test mirror short in different ATR percentile buckets."""
    out = []
    # Discrete buckets via two-sided regime
    bucket_pairs = [
        ("atr_pct<0.8", "low"),  # already exists
        # For others I'd need more regime keys; using existing
    ]
    for regime, label in [("atr_pct>1.5", "extreme"), ("atr_pct>1.3", "high"), ("atr_pct>1.1", "elevated")]:
        for stop, rr in [(40, 1.25), (50, 1.0)]:
            out.append(RuleV5(
                name=f"F4_short_{label}_s{stop}_rr{rr}",
                thesis="F4_vol_taxonomy",
                side="short", setup="mirror", or_classes=("normal",),
                start="10:00", end="11:00",
                stop_points=stop, rr=rr,
                regime_keys_all=(regime,),
                max_close_vwap_delta=None,
            ))
    return out


def thesis_F5_long_atr_targets() -> list[RuleV5]:
    """F5: Tier 1 LONG with ATR-scaled targets. Does long benefit from
    vol-adaptive 'let winners run' in vol regimes?"""
    out = []
    for atr_target_mult in [0.4, 0.5, 0.75, 1.0]:
        for regime in [None, "atr_pct>1.1", "5d_ret>0.5%"]:
            regs = (regime,) if regime else ()
            out.append(RuleV5(
                name=f"F5_long_atrtarget{atr_target_mult}_{regime or 'noregime'}",
                thesis="F5_long_atr_targets",
                side="long", setup="tier1", or_classes=("normal",),
                start="10:00", end="11:00",
                stop_points=40.0, rr=1.25,
                atr_target_multiplier=atr_target_mult,
                regime_keys_all=regs,
                max_close_vwap_delta=65.0,
            ))
    return out


def all_v5_theses() -> list[RuleV5]:
    out = []
    out.extend(thesis_F1_atr_targets_fixed_stops())
    out.extend(thesis_F2_T7_refinement())
    out.extend(thesis_F3_aplus_short_mirror())
    out.extend(thesis_F4_vol_taxonomy())
    out.extend(thesis_F5_long_atr_targets())
    return out


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]
    print(f"Days in window: {len(in_window)}")

    regime_map, atr_series = build_regime_map(contexts)
    rules = all_v5_theses()
    print(f"V5 variants: {len(rules)}")

    raw = {}
    for rule in rules:
        rows = []
        for ctx in in_window:
            r = evaluate_rule_v5(ctx, rule, regime_map, atr_series, slippage_rt=DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        raw[rule.name] = {"rule": rule, "rows": rows, "summary": summarize(rows)}

    # Per-thesis BH-FDR
    by_thesis: dict[str, list[dict]] = {}
    for name, info in raw.items():
        thesis = info["rule"].thesis
        by_thesis.setdefault(thesis, []).append({"name": name, **info})

    print("\n=== Per-thesis screening (n>=15, pf>=1.2, avg>0) + BH-FDR q=0.10 ===")
    survivors = []
    for thesis, items in sorted(by_thesis.items()):
        screened = [it for it in items if it["summary"]["n"] >= 15 and it["summary"]["pf"] >= 1.2 and it["summary"]["avg"] > 0]
        pvals = [t_pvalue([r["net_pnl"] for r in it["rows"]]) for it in screened]
        rejected = bh_fdr(pvals, q=0.10)
        bh_pass = [it for it, rej in zip(screened, rejected) if rej]
        print(f"\n{thesis}: tested={len(items)} screened={len(screened)} BH-survived={len(bh_pass)}")
        for it in sorted(screened, key=lambda x: -x["summary"]["pf"])[:8]:
            s = it["summary"]
            mark = " ***" if it in bh_pass else ""
            print(f"  {it['name']:60s}  n={s['n']:3d}  pf={s['pf']:.3f}  avg=${s['avg']:6.2f}  pnl=${s['pnl']:7.0f}{mark}")
        survivors.extend(bh_pass)

    print(f"\nTotal BH-FDR survivors: {len(survivors)}")

    print("\n=== IS/OOS for survivors ===")
    oos_passers = []
    for s in survivors:
        is_, oos_ = split_summary(s["rows"])
        deg = abs(is_["avg"] - oos_["avg"]) / abs(is_["avg"]) if is_["avg"] != 0 and oos_["n"] > 0 else float("inf")
        ok = is_["pf"] >= 1.3 and oos_["pf"] >= 1.1 and oos_["n"] >= 6 and deg < 0.50
        s["is"] = is_; s["oos"] = oos_; s["deg"] = deg; s["oos_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {s['name']:60s}  IS pf={is_['pf']:.2f}  OOS pf={oos_['pf']:.2f}  n_oos={oos_['n']:2d}  deg={deg:.2%}")
        if ok:
            oos_passers.append(s)

    print(f"\nOOS-validated: {len(oos_passers)}")

    print("\n=== Slippage stress (require pf>=1.2 at 8pt) ===")
    final = []
    for s in oos_passers:
        rule = s["rule"]
        slip_results = {}
        for slip in [3.0, 5.0, 8.0, 12.0]:
            rs = []
            for ctx in in_window:
                r = evaluate_rule_v5(ctx, rule, regime_map, atr_series, slippage_rt=slip)
                if r is not None:
                    rs.append(r)
            slip_results[f"{slip}pt"] = summarize(rs)
        ok = slip_results["8.0pt"]["pf"] >= 1.2
        s["slippage"] = slip_results; s["slip_pass"] = ok
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {s['name']:60s}  pf@5pt={slip_results['5.0pt']['pf']:.2f}  pf@8pt={slip_results['8.0pt']['pf']:.2f}")
        if ok:
            final.append(s)

    print(f"\nFinal v5 survivors: {len(final)}")

    # Combined backtest
    print("\n=== Combined backtest (canonical + v5 final survivors) ===")
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
    print(f"Canonical Tier 1: n={canonical_t1_summary['n']}  pnl=${canonical_t1_summary['pnl']:.0f}  pf={canonical_t1_summary['pf']:.3f}  dd=${canonical_t1_summary['max_dd']:.0f}")

    combined_reports = []
    for s in final:
        combo = combined_portfolio(canonical_t1, s["rows"])
        combo_summary = summarize(combo.to_dict("records"))
        is_c, oos_c = split_summary(combo.to_dict("records"))
        collisions = int(combo["collision"].fillna(False).sum()) if "collision" in combo.columns else 0
        combined_reports.append({
            "addition": s["name"], "summary_full": combo_summary,
            "is": is_c, "oos": oos_c, "collisions": collisions,
        })
        print(f"  +{s['name']:55s}  total n={combo_summary['n']:3d}  pnl=${combo_summary['pnl']:7.0f}  pf={combo_summary['pf']:.3f}  dd=${combo_summary['max_dd']:6.0f}  collisions={collisions}")
        print(f"    IS pf={is_c['pf']:.2f}  OOS pf={oos_c['pf']:.2f}  OOS pnl=${oos_c['pnl']:.0f}  OOS dd=${oos_c['max_dd']:.0f}")

    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    payload = {
        "n_variants": len(rules),
        "survivors": [{"name": s["name"], "thesis": s["rule"].thesis, "summary": s["summary"],
                       "is": s.get("is"), "oos": s.get("oos"),
                       "slippage": s.get("slippage")} for s in survivors],
        "final": [s["name"] for s in final],
        "combined_reports": combined_reports,
    }
    out_dir.joinpath("thesis_v5_raw.json").write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {out_dir}/thesis_v5_raw.json")
    return payload


if __name__ == "__main__":
    main()
