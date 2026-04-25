"""Evaluate the Phase 6 paper-forward promotion gate.

Reads a paper-forward journal (default: canonical strict 2024+) and applies
the gate:

  - >= 50 eligible Tier 1 trades
  - >= 1 calendar quarter elapsed since first trade
  - PF >= 1.4
  - avg_pnl >= 70% of canonical OOS baseline ($17.87 -> floor ~$12.50)
  - max forward DD <= 1.5x canonical backtest max DD
  - one-sided t-test: forward avg has not degraded >= 30% vs canonical at p <= 0.10

Backtest baseline is loaded from the canonical strict 2024+ replay so the
gate auto-updates if the canonical baseline is rebuilt against fresher data.
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

from backtest.causal_or_retest import _peak_relative_drawdown, summarize_decisions  # noqa: E402

CANONICAL_BASELINE = (
    PROJECT_ROOT
    / "research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv"
)
DEFAULT_FORWARD_JOURNAL = (
    PROJECT_ROOT / "research/human_edge_replay/paper_forward/forward_journal.csv"
)
MIN_TRADES = 50
MIN_QUARTER_DAYS = 90
PF_FLOOR = 1.4
DEGRADATION_TOLERANCE = 0.30
PVALUE_FLOOR = 0.10
DD_MULTIPLIER_CEILING = 1.5
ACCOUNT_SIZE = 3_750.0


def _trade_pnls(log: pd.DataFrame, prefix: str) -> np.ndarray:
    eligible = log[log[f"{prefix}_eligible"] == True]  # noqa: E712 (CSV bool)
    return eligible[f"{prefix}_net_pnl"].to_numpy(dtype=float)


def _max_dd(pnls: np.ndarray, account: float) -> float:
    if pnls.size == 0:
        return 0.0
    max_dd, _, _ = _peak_relative_drawdown(pnls, account)
    return float(max_dd)


def _one_sided_t_pvalue(forward: np.ndarray, baseline_mean: float, tolerance: float) -> float:
    """One-sided t-test: H0 mu_forward >= baseline_mean * (1 - tolerance) vs H1 mu_forward < threshold.

    Returns p-value of observing forward mean as low as observed under H0.
    Uses a sample t statistic with n-1 dof and standard normal approximation.
    """
    if forward.size < 2:
        return 1.0
    threshold = baseline_mean * (1.0 - tolerance)
    sample_mean = float(forward.mean())
    sample_std = float(forward.std(ddof=1))
    if sample_std == 0.0:
        return 0.0 if sample_mean < threshold else 1.0
    t_stat = (sample_mean - threshold) / (sample_std / np.sqrt(forward.size))
    dof = forward.size - 1
    try:
        from scipy.stats import t  # type: ignore

        return float(t.cdf(t_stat, df=dof))
    except ImportError:
        from math import erf, sqrt

        return 0.5 * (1.0 + erf(t_stat / sqrt(2.0)))


def evaluate_gate(
    forward: pd.DataFrame,
    baseline: pd.DataFrame,
    prefix: str,
    account_size: float = ACCOUNT_SIZE,
) -> dict[str, object]:
    forward_pnls = _trade_pnls(forward, prefix)
    baseline_pnls = _trade_pnls(baseline, prefix)
    if baseline_pnls.size == 0:
        raise ValueError(f"No baseline trades found for prefix={prefix} in {CANONICAL_BASELINE}")

    forward_summary = summarize_decisions(forward, prefix)
    baseline_summary = summarize_decisions(baseline, prefix)
    baseline_dd = _max_dd(baseline_pnls, account_size)
    forward_dd = _max_dd(forward_pnls, account_size)
    avg_floor = float(baseline_summary["avg_pnl"]) * (1.0 - DEGRADATION_TOLERANCE)
    p_value = _one_sided_t_pvalue(forward_pnls, float(baseline_summary["avg_pnl"]), DEGRADATION_TOLERANCE)

    eligible = forward[forward[f"{prefix}_eligible"] == True]  # noqa: E712
    if eligible.empty:
        elapsed_days = 0
    else:
        first = pd.to_datetime(eligible["date"]).min()
        last = pd.to_datetime(eligible["date"]).max()
        elapsed_days = int((last - first).days)

    checks = {
        "min_trades": (int(forward_summary["trades"]) >= MIN_TRADES, forward_summary["trades"], MIN_TRADES),
        "min_quarter_elapsed_days": (elapsed_days >= MIN_QUARTER_DAYS, elapsed_days, MIN_QUARTER_DAYS),
        "pf_floor": (
            (np.isfinite(forward_summary["pf"]) and forward_summary["pf"] >= PF_FLOOR),
            round(float(forward_summary["pf"]), 4) if np.isfinite(forward_summary["pf"]) else None,
            PF_FLOOR,
        ),
        "avg_pnl_floor": (
            float(forward_summary["avg_pnl"]) >= avg_floor,
            round(float(forward_summary["avg_pnl"]), 4),
            round(avg_floor, 4),
        ),
        "max_dd_ceiling": (
            forward_dd <= baseline_dd * DD_MULTIPLIER_CEILING,
            round(forward_dd, 2),
            round(baseline_dd * DD_MULTIPLIER_CEILING, 2),
        ),
        "no_significant_degradation": (
            p_value > PVALUE_FLOOR,
            round(p_value, 4),
            PVALUE_FLOOR,
        ),
    }
    overall = all(passed for passed, _, _ in checks.values())
    return {
        "prefix": prefix,
        "overall_pass": overall,
        "forward_summary": {
            "trades": int(forward_summary["trades"]),
            "total_pnl": round(float(forward_summary["total_pnl"]), 2),
            "avg_pnl": round(float(forward_summary["avg_pnl"]), 4),
            "win_rate": round(float(forward_summary["win_rate"]), 4),
            "pf": round(float(forward_summary["pf"]), 4) if np.isfinite(forward_summary["pf"]) else None,
            "max_dd": round(forward_dd, 2),
            "elapsed_days": elapsed_days,
        },
        "baseline_summary": {
            "trades": int(baseline_summary["trades"]),
            "avg_pnl": round(float(baseline_summary["avg_pnl"]), 4),
            "pf": round(float(baseline_summary["pf"]), 4) if np.isfinite(baseline_summary["pf"]) else None,
            "max_dd": round(baseline_dd, 2),
        },
        "checks": {
            name: {"passed": passed, "observed": observed, "threshold": threshold}
            for name, (passed, observed, threshold) in checks.items()
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forward", default=str(DEFAULT_FORWARD_JOURNAL), help="Forward journal CSV path")
    parser.add_argument("--baseline", default=str(CANONICAL_BASELINE), help="Canonical baseline CSV path")
    parser.add_argument("--account-size", type=float, default=ACCOUNT_SIZE)
    parser.add_argument(
        "--prefix",
        action="append",
        choices=["tier1", "a_plus_shadow", "tier2_shadow"],
        help="Which rule prefixes to evaluate; default tier1 + a_plus_shadow",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    forward_path = Path(args.forward)
    baseline_path = Path(args.baseline)
    if not forward_path.exists():
        print(f"ERROR: forward journal not found: {forward_path}", file=sys.stderr)
        return 2
    if not baseline_path.exists():
        print(f"ERROR: canonical baseline not found: {baseline_path}", file=sys.stderr)
        return 2

    forward = pd.read_csv(forward_path)
    baseline = pd.read_csv(baseline_path)
    prefixes = args.prefix or ["tier1", "a_plus_shadow"]

    results = [evaluate_gate(forward, baseline, prefix, args.account_size) for prefix in prefixes]
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            verdict = "PASS" if r["overall_pass"] else "FAIL"
            f = r["forward_summary"]
            b = r["baseline_summary"]
            print(f"\n=== {r['prefix']}: {verdict} ===")
            print(
                f"  forward:  trades={f['trades']}  pnl=${f['total_pnl']}  avg=${f['avg_pnl']}  "
                f"win={f['win_rate']:.2%}  pf={f['pf']}  max_dd=${f['max_dd']}  elapsed_days={f['elapsed_days']}"
            )
            print(
                f"  baseline: trades={b['trades']}  avg=${b['avg_pnl']}  pf={b['pf']}  max_dd=${b['max_dd']}"
            )
            print(f"  Gate checks:")
            for name, payload in r["checks"].items():
                mark = "PASS" if payload["passed"] else "FAIL"
                print(
                    f"    [{mark}] {name}: observed={payload['observed']}  threshold={payload['threshold']}"
                )
    return 0 if all(r["overall_pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
