"""Final combined backtest: cross all top-survivor LONG and SHORT configurations
to find the best risk-adjusted portfolio for current capital ($3,750-$5,000).

LONG variants:
  L0  Canonical Tier 1 (no regime filter)
  L1  Canonical Tier 1, halt when 5d_ret < -1.0%
  L2  Canonical Tier 1, halt when 5d_ret < -1.5%
  L3  Canonical A+ (slope+pos)
  L4  Canonical A+, halt when 5d_ret < -0.5%

SHORT variants (each with 40pt fixed stop = $80 risk):
  S0  none
  S1  T7: mirror, normal, 10-11, atr_pct>1.3, 40pt stop, 50pt target, no VWAP cap
  S2  F1: mirror, normal, 10-11, 5d_ret<-1.0%, 40pt stop, 1.0xATR target, no VWAP cap
  S3  F3: mirror, normal, 10-11, atr_pct>1.3, 40pt stop, 50pt target, slope>=-20, no VWAP cap

Cross product = 5 LONG x 4 SHORT = 20 combinations. Report risk-adjusted metrics
(MAR, OOS PF, OOS DD on $3,750 account).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT, TIER1_PILOT, A_PLUS_SHADOW,
    build_day_contexts, evaluate_rule_on_day, load_1m_parquet,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from thesis_research_v4 import build_regime_map, summarize, split_summary, combined_portfolio  # noqa: E402
from thesis_research_v5 import RuleV5, evaluate_rule_v5  # noqa: E402

WINDOW_START = "2024-01-01"
ACCOUNT = 3750.0


def evaluate_long_canonical_with_halt(ctx, base_rule, regime_map, halt_regime_key):
    if halt_regime_key is not None:
        if regime_map.get(halt_regime_key, {}).get(ctx.date, False):
            return None
    d = evaluate_rule_on_day(ctx, base_rule, require_full_session_clean=True)
    if not d.eligible:
        return None
    return {
        "date": ctx.date, "rule": base_rule.name + (f"_halt_{halt_regime_key}" if halt_regime_key else ""),
        "side": "long", "entry_ts": d.entry_ts, "exit_ts": d.exit_ts,
        "entry_price": d.entry_price, "exit_price": d.exit_price,
        "exit_reason": d.exit_reason, "net_pnl": d.net_pnl,
    }


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp(WINDOW_START).date()]
    regime_map, atr_series = build_regime_map(contexts)

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
            name="S1_T7_atrgate", thesis="combined", side="short", setup="mirror",
            or_classes=("normal",), start="10:00", end="11:00",
            stop_points=40, rr=1.25, regime_keys_all=("atr_pct>1.3",),
            max_close_vwap_delta=None,
        )),
        ("S2_F1_atrtarget_5dgate", RuleV5(
            name="S2_F1_atrtarget", thesis="combined", side="short", setup="mirror",
            or_classes=("normal",), start="10:00", end="11:00",
            stop_points=40, rr=1.25, atr_target_multiplier=1.0,
            regime_keys_all=("5d_ret<-1.0%",), max_close_vwap_delta=None,
        )),
        ("S3_F3_slope_atrgate", RuleV5(
            name="S3_F3_slope", thesis="combined", side="short", setup="mirror",
            or_classes=("normal",), start="10:00", end="11:00",
            stop_points=40, rr=1.25, min_signal_ema_slope=-20.0,
            regime_keys_all=("atr_pct>1.3",), max_close_vwap_delta=None,
        )),
    ]

    # Pre-compute long rows for each long_config
    long_rows_map = {}
    for label, rule, halt in long_configs:
        rows = []
        for ctx in in_window:
            r = evaluate_long_canonical_with_halt(ctx, rule, regime_map, halt)
            if r is not None:
                rows.append(r)
        long_rows_map[label] = rows

    # Pre-compute short rows
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

    # Cross product
    print(f"{'COMBO':40s}  {'n':>4s} {'PnL':>8s} {'PF':>6s} {'DD':>7s} {'MAR':>5s}  {'OOS_n':>5s} {'OOS_PnL':>8s} {'OOS_PF':>6s} {'OOS_DD':>7s} {'OOS_MAR':>7s}")
    print("=" * 130)
    reports = []
    for l_label, _, _ in long_configs:
        for s_label, _ in short_configs:
            long_rows = long_rows_map[l_label]
            short_rows = short_rows_map[s_label]
            combo = combined_portfolio(long_rows, short_rows) if short_rows else pd.DataFrame(long_rows)
            if combo.empty:
                continue
            full_summary = summarize(combo.to_dict("records"))
            is_, oos_ = split_summary(combo.to_dict("records"))
            yrs = 2.31
            full_mar = (full_summary["pnl"] / yrs / full_summary["max_dd"]) if full_summary["max_dd"] > 0 else float("inf")
            oos_yrs = 0.82
            oos_mar = (oos_["pnl"] / oos_yrs / oos_["max_dd"]) if oos_["max_dd"] > 0 else float("inf")
            label = f"{l_label}+{s_label}"
            reports.append({
                "combo": label, "full": full_summary, "is": is_, "oos": oos_,
                "long_n": len(long_rows), "short_n": len(short_rows),
                "full_mar": full_mar, "oos_mar": oos_mar,
            })
            print(f"{label:40s}  {full_summary['n']:>4d} {full_summary['pnl']:>8.0f} {full_summary['pf']:>6.2f} {full_summary['max_dd']:>7.0f} {full_mar:>5.2f}  {oos_['n']:>5d} {oos_['pnl']:>8.0f} {oos_['pf']:>6.2f} {oos_['max_dd']:>7.0f} {oos_mar:>7.2f}")

    # Sort by OOS_MAR descending
    print("\n" + "=" * 130)
    print("TOP 5 by OOS MAR (forward risk-adjusted return):")
    for r in sorted(reports, key=lambda x: -x["oos_mar"])[:5]:
        print(f"  {r['combo']:40s}  OOS_PF={r['oos']['pf']:.2f}  OOS_PnL=${r['oos']['pnl']:.0f}  OOS_DD=${r['oos']['max_dd']:.0f}  OOS_MAR={r['oos_mar']:.2f}")

    out_dir = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration"
    out_dir.joinpath("final_combined_backtest.json").write_text(json.dumps(reports, indent=2, default=str))
    print(f"\nWrote {out_dir}/final_combined_backtest.json")


if __name__ == "__main__":
    main()
