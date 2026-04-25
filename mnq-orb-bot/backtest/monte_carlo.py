"""
Monte Carlo simulation for drawdown and ruin analysis.

Takes completed backtest trades, reshuffles the order 10,000 times,
and calculates the probability distribution of max drawdowns.

This answers: "Given this set of trades in random order, what's the
probability my $2,500 account hits a 20% drawdown?"

Usage:
    from backtest.monte_carlo import run_monte_carlo
    mc = run_monte_carlo(backtest_results, account_size=2500, simulations=10000)
    mc.summary()
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloResults:
    """Monte Carlo simulation results."""
    simulations: int
    account_size: float
    trade_pnls: list[float]
    max_drawdowns: np.ndarray         # Max drawdown $ for each sim
    max_drawdown_pcts: np.ndarray     # Max drawdown % for each sim
    final_equities: np.ndarray        # Final equity for each sim
    ruin_count: int                   # Sims that hit ruin threshold
    ruin_threshold_pct: float

    @property
    def median_max_drawdown(self) -> float:
        return float(np.median(self.max_drawdowns))

    @property
    def p95_max_drawdown(self) -> float:
        """95th percentile worst drawdown (the bad case)."""
        return float(np.percentile(self.max_drawdowns, 95))

    @property
    def p99_max_drawdown(self) -> float:
        """99th percentile worst drawdown (the very bad case)."""
        return float(np.percentile(self.max_drawdowns, 99))

    @property
    def ruin_probability(self) -> float:
        return self.ruin_count / self.simulations

    @property
    def median_final_equity(self) -> float:
        return float(np.median(self.final_equities))

    @property
    def p10_final_equity(self) -> float:
        """10th percentile final equity (unlucky scenario)."""
        return float(np.percentile(self.final_equities, 10))

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "MONTE CARLO SIMULATION",
            "=" * 60,
            f"Simulations:          {self.simulations:,}",
            f"Account Size:         ${self.account_size:,.0f}",
            f"Trades Reshuffled:    {len(self.trade_pnls)}",
            "",
            "MAX DRAWDOWN DISTRIBUTION:",
            f"  Median:             ${self.median_max_drawdown:,.0f}",
            f"  95th Percentile:    ${self.p95_max_drawdown:,.0f}",
            f"  99th Percentile:    ${self.p99_max_drawdown:,.0f}",
            "",
            "FINAL EQUITY DISTRIBUTION:",
            f"  Median:             ${self.median_final_equity:,.0f}",
            f"  10th Percentile:    ${self.p10_final_equity:,.0f}",
            f"  90th Percentile:    ${np.percentile(self.final_equities, 90):,.0f}",
            "",
            f"RUIN PROBABILITY ({self.ruin_threshold_pct:.0%} drawdown):",
            f"  {self.ruin_probability:.2%} ({self.ruin_count}/{self.simulations})",
        ]

        # Verdict
        if self.ruin_probability < 0.05:
            lines.append("\n✅ LOW RISK — <5% chance of hitting ruin threshold")
        elif self.ruin_probability < 0.15:
            lines.append("\n⚠️  MODERATE RISK — 5-15% chance of ruin. Consider more capital.")
        else:
            lines.append("\n❌ HIGH RISK — >15% ruin probability. Increase capital or reduce risk.")

        # Capital recommendation: rerun the equity-curve sim at each account size
        # so the comparison uses the same DD-vs-peak definition as the headline gate
        # (not raw DD divided by starting equity, which contradicted the verdict).
        lines.append("")
        lines.append("CAPITAL RECOMMENDATIONS (for <5% ruin probability):")
        rng = np.random.default_rng(42)
        trade_pnls = np.array(self.trade_pnls)
        for multiplier in [1.0, 1.5, 2.0, 3.0]:
            test_size = self.account_size * multiplier
            ruin_hits = 0
            for _ in range(self.simulations):
                equity = test_size + np.cumsum(rng.permutation(trade_pnls))
                equity = np.insert(equity, 0, test_size)
                peak = np.maximum.accumulate(equity)
                dd = peak - equity
                dd_pct = np.max(dd) / np.max(peak) if np.max(peak) > 0 else 0
                if dd_pct >= self.ruin_threshold_pct:
                    ruin_hits += 1
            lines.append(f"  ${test_size:>8,.0f}:  {ruin_hits / self.simulations:.1%} ruin probability")

        lines.append("=" * 60)
        report = "\n".join(lines)
        print(report)
        return report


def run_monte_carlo(
    backtest_results,
    account_size: float = 2500,
    simulations: int = 10000,
    ruin_threshold_pct: float = 0.20,
    seed: int = 42,
) -> MonteCarloResults:
    """Run Monte Carlo simulation on backtest results.

    Args:
        backtest_results: BacktestResults from backtester
        account_size: Starting account size in dollars
        simulations: Number of random reshuffles
        ruin_threshold_pct: Drawdown % that counts as "ruin" (0.20 = 20%)
        seed: Random seed for reproducibility
    """
    rng = np.random.default_rng(seed)

    # Extract trade P&Ls
    trade_pnls = np.array([t.pnl_dollars for t in backtest_results.trades])
    n_trades = len(trade_pnls)

    if n_trades == 0:
        raise ValueError("No trades to simulate")

    logger.info(f"Running {simulations:,} Monte Carlo simulations with {n_trades} trades")

    max_drawdowns = np.zeros(simulations)
    max_drawdown_pcts = np.zeros(simulations)
    final_equities = np.zeros(simulations)
    ruin_count = 0

    for i in range(simulations):
        # Shuffle trade order
        shuffled = rng.permutation(trade_pnls)

        # Build equity curve
        equity = account_size + np.cumsum(shuffled)
        equity = np.insert(equity, 0, account_size)  # Include starting balance

        # Calculate max drawdown
        peak = np.maximum.accumulate(equity)
        drawdowns = peak - equity
        max_dd = np.max(drawdowns)
        max_dd_pct = max_dd / np.max(peak) if np.max(peak) > 0 else 0

        max_drawdowns[i] = max_dd
        max_drawdown_pcts[i] = max_dd_pct
        final_equities[i] = equity[-1]

        if max_dd_pct >= ruin_threshold_pct:
            ruin_count += 1

    logger.info(f"Monte Carlo complete. Ruin probability: {ruin_count/simulations:.2%}")

    return MonteCarloResults(
        simulations=simulations,
        account_size=account_size,
        trade_pnls=trade_pnls.tolist(),
        max_drawdowns=max_drawdowns,
        max_drawdown_pcts=max_drawdown_pcts,
        final_equities=final_equities,
        ruin_count=ruin_count,
        ruin_threshold_pct=ruin_threshold_pct,
    )
