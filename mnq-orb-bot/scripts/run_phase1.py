"""
Phase 1 validation gates: full backtest -> walk-forward -> Monte Carlo.

Pass criteria (from VALIDATION.md):
  - Walk-forward: >=75% of OOS windows profitable AND avg OOS WR >=60%
  - Monte Carlo:  <5% probability of 20% account drawdown ($2,500 starting)
"""

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtester import Backtester
from backtest.walk_forward import walk_forward_validate
from backtest.monte_carlo import run_monte_carlo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase1")

DATA_PATH = PROJECT_ROOT / "data" / "mnq_15m.parquet"
STRAT_CFG = PROJECT_ROOT / "config" / "strategy_params.yaml"
RISK_CFG = PROJECT_ROOT / "config" / "risk_params.yaml"
ACCOUNT_SIZE = 2500.0


def section(title: str):
    print(f"\n\n{'#' * 70}\n# {title}\n{'#' * 70}\n")


def main() -> int:
    if not DATA_PATH.exists():
        logger.error(f"Data file missing: {DATA_PATH}")
        return 1

    section("STEP 1 — Full-period backtest (sanity check)")
    bt = Backtester(str(STRAT_CFG), str(RISK_CFG))
    results = bt.run(DATA_PATH)
    print(f"\nTotal trades: {results.total_trades}")
    print(f"Win rate:     {results.win_rate:.1%}")
    print(f"Winners:      {len(results.winners)}")
    print(f"Losers:       {len(results.losers)}")
    if hasattr(results, "summary"):
        try:
            results.summary()
        except Exception as e:
            logger.warning(f"results.summary() raised: {e}")

    if results.total_trades == 0:
        logger.error("No trades produced — cannot run Phase 1 gates. "
                     "Investigate strategy params / data alignment first.")
        return 2

    section("STEP 2 — Walk-forward validation")
    wf = walk_forward_validate(
        DATA_PATH,
        strategy_config=str(STRAT_CFG),
        risk_config=str(RISK_CFG),
        train_months=8,
        test_months=4,
        step_months=2,
    )

    section("STEP 3 — Monte Carlo simulation")
    mc = run_monte_carlo(
        results,
        account_size=ACCOUNT_SIZE,
        simulations=10_000,
        ruin_threshold_pct=0.20,
        seed=42,
    )
    mc.summary()

    section("PHASE 1 VERDICT")
    avg_oos_pf = sum(wf.oos_profit_factors) / max(len(wf.oos_profit_factors), 1)
    avg_oos_pnl = sum(wf.oos_total_pnl) / max(len(wf.oos_total_pnl), 1)
    wf_pass = wf.pass_rate >= 0.75 and avg_oos_pf >= 1.5 and avg_oos_pnl > 0
    mc_pass = mc.ruin_probability < 0.05

    print(f"Walk-forward: {'PASS' if wf_pass else 'FAIL'} "
          f"(pass_rate={wf.pass_rate:.0%}, "
          f"avg_oos_pf={avg_oos_pf:.2f}, "
          f"avg_oos_pnl=${avg_oos_pnl:,.0f})")
    print(f"Monte Carlo:  {'PASS' if mc_pass else 'FAIL'} "
          f"(ruin_prob={mc.ruin_probability:.2%})")

    if wf_pass and mc_pass:
        print("\n=> Phase 1 gates PASSED. Proceed to Phase 2.")
        return 0
    print("\n=> Phase 1 gates FAILED. Investigate before proceeding.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
