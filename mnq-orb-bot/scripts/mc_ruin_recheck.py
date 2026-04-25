"""Monte Carlo capital-risk recheck against the canonical strict baseline.

Recomputes peak-relative drawdown distributions for Tier 1 and A+ shadow
PnL vectors and writes the result alongside the canonical baseline so the
official ruin probability is bound to the exact frozen sample.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import bootstrap_drawdown_gate, summarize_decisions  # noqa: E402

CANONICAL_BASELINE = (
    PROJECT_ROOT
    / "research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv"
)
DEFAULT_OUT = (
    PROJECT_ROOT / "research/human_edge_replay/paper_forward/canonical_mc_ruin.json"
)
DEFAULT_ACCOUNTS = [3_750.0, 5_000.0, 7_500.0]
DEFAULT_PREFIXES = ["tier1", "a_plus_shadow"]
RUIN_THRESHOLD_PCT = 0.20
SIMULATIONS = 20_000
SEED = 42


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=str(CANONICAL_BASELINE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--simulations", type=int, default=SIMULATIONS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--account-size",
        type=float,
        action="append",
        help="Account size; repeat flag for multiple capitals",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"ERROR: baseline not found: {baseline_path}", file=sys.stderr)
        return 2
    log = pd.read_csv(baseline_path)
    accounts = args.account_size or DEFAULT_ACCOUNTS

    payload: dict[str, object] = {
        "baseline_csv": str(baseline_path.relative_to(PROJECT_ROOT)),
        "rule_version": str(log["rule_version"].iloc[0]) if not log.empty else None,
        "data_file_hash": str(log["data_file_hash"].iloc[0]) if not log.empty else None,
        "data_last_timestamp": str(log["data_last_timestamp"].iloc[0]) if not log.empty else None,
        "simulations": args.simulations,
        "seed": args.seed,
        "ruin_threshold_pct": RUIN_THRESHOLD_PCT,
        "results": {},
    }

    for prefix in DEFAULT_PREFIXES:
        eligible = log[log[f"{prefix}_eligible"] == True]  # noqa: E712
        pnls = eligible[f"{prefix}_net_pnl"].to_numpy(dtype=float)
        if pnls.size == 0:
            print(f"WARN: no trades for {prefix}; skipping", file=sys.stderr)
            continue
        summary = summarize_decisions(log, prefix)
        per_account = []
        for account in accounts:
            mc = bootstrap_drawdown_gate(
                pnls,
                account_size=account,
                simulations=args.simulations,
                ruin_threshold_pct=RUIN_THRESHOLD_PCT,
                seed=args.seed,
            )
            per_account.append({"account_size": float(account), **mc})
            print(
                f"{prefix}@${account:,.0f}: ruin_prob={mc['ruin_probability']:.4%}  "
                f"median_dd=${mc['median_max_drawdown']:.2f}  "
                f"p95_dd=${mc['p95_max_drawdown']:.2f}  "
                f"p99_dd=${mc['p99_max_drawdown']:.2f}  "
                f"median_dd_pct={mc['median_max_drawdown_pct']:.2%}"
            )
        payload["results"][prefix] = {
            "trades": int(summary["trades"]),
            "total_pnl": round(float(summary["total_pnl"]), 2),
            "avg_pnl": round(float(summary["avg_pnl"]), 4),
            "win_rate": round(float(summary["win_rate"]), 4),
            "pf": round(float(summary["pf"]), 4) if np.isfinite(summary["pf"]) else None,
            "per_account": per_account,
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
