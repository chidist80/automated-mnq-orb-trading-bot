"""Walk-forward stability analysis of Recommendation A (v5 best combined).

Recommendation A:
  LONG  = Tier 1 canonical (no regime filter)
  SHORT = mirror, normal OR, 10-11 ET, 40pt fixed stop, 1.0xATR target,
          regime: 5d_ret < -1%, no VWAP cap

Tests:
  - Rolling 3-month windows (PF / PnL / win rate / DD per window)
  - Rolling 6-month windows
  - Concentration analysis: how much of total PnL is in best/worst 3 months?
  - Bootstrap CI for OOS PF
  - Leave-one-out: drop largest winner, recompute PF
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    DEFAULT_SLIPPAGE_RT, TIER1_PILOT,
    build_day_contexts, evaluate_rule_on_day, load_1m_parquet,
)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from thesis_research_v4 import build_regime_map, summarize, combined_portfolio  # noqa: E402
from thesis_research_v5 import RuleV5, evaluate_rule_v5  # noqa: E402


def main():
    df = load_1m_parquet(PROJECT_ROOT / "data/mnq_1m.parquet")
    contexts = build_day_contexts(df)
    in_window = [c for c in contexts if pd.Timestamp(c.date).date() >= pd.Timestamp("2024-01-01").date()]
    regime_map, atr_series = build_regime_map(contexts)

    # Build LONG canonical Tier 1 trades
    long_rows = []
    for ctx in in_window:
        d = evaluate_rule_on_day(ctx, TIER1_PILOT, require_full_session_clean=True)
        if d.eligible:
            long_rows.append({
                "date": ctx.date, "rule": "tier1_canonical", "side": "long",
                "entry_ts": d.entry_ts, "exit_ts": d.exit_ts,
                "net_pnl": d.net_pnl,
            })

    # Build SHORT recommendation
    short_rule = RuleV5(
        name="recA_short", thesis="recA", side="short", setup="mirror",
        or_classes=("normal",), start="10:00", end="11:00",
        stop_points=40, rr=1.25, atr_target_multiplier=1.0,
        regime_keys_all=("5d_ret<-1.0%",), max_close_vwap_delta=None,
    )
    short_rows = []
    for ctx in in_window:
        r = evaluate_rule_v5(ctx, short_rule, regime_map, atr_series, slippage_rt=DEFAULT_SLIPPAGE_RT)
        if r is not None:
            short_rows.append(r)

    combo = combined_portfolio(long_rows, short_rows)
    combo["date"] = pd.to_datetime(combo["date"])
    combo = combo.sort_values("date").reset_index(drop=True)
    print(f"Total trades: {len(combo)}  (long={len(long_rows)}, short={len(short_rows)}, collisions={int(combo['collision'].fillna(False).sum())})")

    # ====== Rolling window analysis ======
    def rolling_windows(combo, window_days):
        start_date = combo["date"].min()
        end_date = combo["date"].max()
        windows = []
        cursor = start_date
        while cursor <= end_date - pd.Timedelta(days=window_days):
            wend = cursor + pd.Timedelta(days=window_days)
            sub = combo[(combo["date"] >= cursor) & (combo["date"] < wend)]
            if not sub.empty:
                s = summarize(sub.to_dict("records"))
                windows.append({
                    "start": cursor.date(), "end": wend.date(),
                    "n": s["n"], "pnl": s["pnl"], "pf": s["pf"],
                    "win_rate": s["win_rate"], "max_dd": s["max_dd"],
                })
            cursor = cursor + pd.Timedelta(days=30)  # step monthly
        return pd.DataFrame(windows)

    print("\n=== Rolling 3-month windows (step 1mo) ===")
    w3 = rolling_windows(combo, 90)
    print(w3.to_string(index=False))
    print(f"\nWindows: {len(w3)}  Profitable: {(w3['pnl']>0).sum()} ({(w3['pnl']>0).mean():.0%})")
    print(f"Window PF stats: min={w3['pf'].min():.2f} median={w3['pf'].median():.2f} max={w3['pf'].max():.2f}")
    print(f"Windows with PF<1: {(w3['pf']<1).sum()} of {len(w3)}")

    print("\n=== Rolling 6-month windows (step 1mo) ===")
    w6 = rolling_windows(combo, 180)
    print(w6.to_string(index=False))
    print(f"\nWindows: {len(w6)}  Profitable: {(w6['pnl']>0).sum()} ({(w6['pnl']>0).mean():.0%})")
    print(f"Window PF stats: min={w6['pf'].min():.2f} median={w6['pf'].median():.2f} max={w6['pf'].max():.2f}")

    # ====== Concentration analysis ======
    print("\n=== PnL concentration analysis ===")
    monthly = combo.copy()
    monthly["month"] = monthly["date"].dt.to_period("M").astype(str)
    by_month = monthly.groupby("month")["net_pnl"].agg(["sum", "count"]).reset_index()
    by_month = by_month.sort_values("sum", ascending=False)
    print("Monthly PnL (sorted):")
    print(by_month.to_string(index=False))
    total = by_month["sum"].sum()
    top3 = by_month.head(3)["sum"].sum()
    bot3 = by_month.tail(3)["sum"].sum()
    print(f"\nTop 3 months: ${top3:.2f} ({top3/total*100:.1f}% of total ${total:.2f})")
    print(f"Bottom 3 months: ${bot3:.2f}")
    print(f"Months with positive PnL: {(by_month['sum']>0).sum()} of {len(by_month)}")

    # ====== Leave-one-out ======
    print("\n=== Leave-one-out sensitivity ===")
    full_summary = summarize(combo.to_dict("records"))
    print(f"Full PF: {full_summary['pf']:.4f}  Full PnL: ${full_summary['pnl']:.2f}")
    largest_winner = combo.loc[combo["net_pnl"].idxmax()]
    largest_loser = combo.loc[combo["net_pnl"].idxmin()]
    print(f"Largest winner: {largest_winner['date'].date()}  ${largest_winner['net_pnl']:.2f}")
    print(f"Largest loser:  {largest_loser['date'].date()}  ${largest_loser['net_pnl']:.2f}")
    no_winner = combo.drop(largest_winner.name)
    no_loser = combo.drop(largest_loser.name)
    s_no_w = summarize(no_winner.to_dict("records"))
    s_no_l = summarize(no_loser.to_dict("records"))
    print(f"PF dropping largest winner: {s_no_w['pf']:.4f}  delta=-{(full_summary['pf']-s_no_w['pf'])/full_summary['pf']*100:.1f}%")
    print(f"PF dropping largest loser:  {s_no_l['pf']:.4f}  delta=+{(s_no_l['pf']-full_summary['pf'])/full_summary['pf']*100:.1f}%")

    # ====== Bootstrap CI for full-window PF ======
    print("\n=== Bootstrap 95% CI for PF (10,000 resamples) ===")
    pnls = combo["net_pnl"].to_numpy()
    rng = np.random.default_rng(42)
    boot_pfs = []
    for _ in range(10000):
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        wins = sample[sample > 0].sum()
        losses = abs(sample[sample <= 0].sum())
        if losses > 0:
            boot_pfs.append(wins / losses)
    boot_pfs = np.array(boot_pfs)
    ci_lo, ci_hi = np.percentile(boot_pfs, [2.5, 97.5])
    print(f"Full PF: {full_summary['pf']:.4f}  Bootstrap 95% CI: [{ci_lo:.3f}, {ci_hi:.3f}]")
    print(f"P(PF>1): {(boot_pfs > 1).mean():.3f}")
    print(f"P(PF>1.5): {(boot_pfs > 1.5).mean():.3f}")

    # ====== Save ======
    out = {
        "n_total": len(combo),
        "n_long": len(long_rows),
        "n_short": len(short_rows),
        "full_summary": full_summary,
        "rolling_3mo": w3.to_dict("records"),
        "rolling_6mo": w6.to_dict("records"),
        "monthly_pnl": by_month.to_dict("records"),
        "leave_one_out": {
            "no_largest_winner_pf": s_no_w["pf"],
            "no_largest_loser_pf": s_no_l["pf"],
        },
        "bootstrap": {
            "ci_lo": float(ci_lo), "ci_hi": float(ci_hi),
            "p_pf_above_1": float((boot_pfs > 1).mean()),
            "p_pf_above_1_5": float((boot_pfs > 1.5).mean()),
        },
    }
    out_path = PROJECT_ROOT / "research/human_edge_replay/short_side_exploration/walk_forward_recA.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
