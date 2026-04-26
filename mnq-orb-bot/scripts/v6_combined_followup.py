"""V6 combined follow-up: cross-product of LONG configs (incl. NDX-halt) x v5 best SHORTs.

LONG variants:
  L0  Canonical Tier 1
  L1  Tier 1 halt-when MNQ 5d_ret<-1.0%
  L2  Tier 1 halt-when NDX 5d_ret<-1.0%   (NEW from v6)
  L3  A+ canonical
  L4  A+ halt-when NDX 5d_ret<-1.0%       (NEW)

SHORT variants (with ATR target / 5d regime):
  S0  none
  S1  Mirror short, 40pt stop, 1.0xATR target, MNQ 5d_ret<-1.0% regime  (v5 winner)
  S2  Mirror short, 40pt stop, 1.0xATR target, NDX 5d_ret<-1.0% regime  (NEW)
  S3  Mirror short, 40pt stop, 1.0xATR target, vix>20 AND NDX 5d<-1%   (dual)
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
from thesis_research_v5 import RuleV5  # noqa: E402
from external_regime_research import (  # noqa: E402
    build_external_regime_map, evaluate_short_with_external,
    evaluate_long_with_external_halt,
)


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp("2024-01-01").date()]
    internal_map, atr_series = build_regime_map(contexts)
    external_map, _ = build_external_regime_map(PROJECT_ROOT)

    long_configs = [
        ("L0_T1_canonical", TIER1_PILOT, None),
        ("L1_T1_halt_mnq5d", TIER1_PILOT, "5d_ret<-1.0%"),
        ("L2_T1_halt_ndx5d", TIER1_PILOT, "ndx_5d_ret<-1%"),
        ("L3_Aplus_canonical", A_PLUS_SHADOW, None),
        ("L4_Aplus_halt_ndx5d", A_PLUS_SHADOW, "ndx_5d_ret<-1%"),
    ]
    base_short = {
        "thesis": "v6", "side": "short", "setup": "mirror",
        "or_classes": ("normal",), "start": "10:00", "end": "11:00",
        "stop_points": 40, "rr": 1.25, "max_close_vwap_delta": None,
        "atr_target_multiplier": 1.0,
    }
    short_configs = [
        ("S0_none", None),
        ("S1_atrtarget_mnq5d", RuleV5(name="S1", regime_keys_all=("5d_ret<-1.0%",), **base_short)),
        ("S2_atrtarget_ndx5d", RuleV5(name="S2", regime_keys_all=("ndx_5d_ret<-1%",), **base_short)),
        ("S3_atrtarget_vix20_ndx5d", RuleV5(name="S3", regime_keys_all=("vix>20", "ndx_5d_ret<-1%"), **base_short)),
    ]

    # Pre-compute LONG and SHORT trades
    long_rows_map = {}
    for label, rule, halt in long_configs:
        rows = []
        for ctx in in_window:
            # Long can halt by either internal or external regime
            if halt is not None:
                if halt in internal_map:
                    if internal_map[halt].get(ctx.date, False):
                        continue
                else:
                    if external_map.get(halt, {}).get(ctx.date, False):
                        continue
            d = evaluate_rule_on_day(ctx, rule, require_full_session_clean=True)
            if d.eligible:
                rows.append({
                    "date": ctx.date, "rule": label, "side": "long",
                    "entry_ts": d.entry_ts, "exit_ts": d.exit_ts, "net_pnl": d.net_pnl,
                })
        long_rows_map[label] = rows

    short_rows_map = {}
    for label, rule in short_configs:
        if rule is None:
            short_rows_map[label] = []
            continue
        rows = []
        for ctx in in_window:
            r = evaluate_short_with_external(ctx, rule, internal_map, external_map, atr_series, DEFAULT_SLIPPAGE_RT)
            if r is not None:
                rows.append(r)
        short_rows_map[label] = rows

    print(f"{'COMBO':45s}  {'n':>3s} {'PnL':>7s} {'PF':>5s} {'DD':>6s}  {'OOS_n':>5s} {'OOS_PnL':>7s} {'OOS_PF':>6s} {'OOS_DD':>6s} {'OOS_MAR':>7s}")
    print("=" * 130)
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
            oos_yrs = 0.82
            oos_mar = (oos_["pnl"] / oos_yrs / oos_["max_dd"]) if oos_["max_dd"] > 0 else float("inf")
            label = f"{l_label}+{s_label}"
            reports.append({
                "combo": label, "full": full, "is": is_, "oos": oos_, "oos_mar": oos_mar,
                "long_n": len(long_rows), "short_n": len(short_rows),
            })
            print(f"{label:45s}  {full['n']:>3d} {full['pnl']:>7.0f} {full['pf']:>5.2f} {full['max_dd']:>6.0f}  {oos_['n']:>5d} {oos_['pnl']:>7.0f} {oos_['pf']:>6.2f} {oos_['max_dd']:>6.0f} {oos_mar:>7.2f}")

    print("\n" + "=" * 130)
    print("TOP 5 by OOS MAR:")
    for r in sorted(reports, key=lambda x: -x["oos_mar"])[:5]:
        print(f"  {r['combo']:50s}  OOS_PF={r['oos']['pf']:.2f}  OOS_PnL=${r['oos']['pnl']:.0f}  OOS_DD=${r['oos']['max_dd']:.0f}  OOS_MAR={r['oos_mar']:.2f}")
    print("\nTOP 5 by OOS PnL:")
    for r in sorted(reports, key=lambda x: -x["oos"]["pnl"])[:5]:
        print(f"  {r['combo']:50s}  OOS_PF={r['oos']['pf']:.2f}  OOS_PnL=${r['oos']['pnl']:.0f}  OOS_DD=${r['oos']['max_dd']:.0f}  OOS_MAR={r['oos_mar']:.2f}")

    out = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration/v6_combined_results.json"
    out.write_text(json.dumps(reports, indent=2, default=str))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
