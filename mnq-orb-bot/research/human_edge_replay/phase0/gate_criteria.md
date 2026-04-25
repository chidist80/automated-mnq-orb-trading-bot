# Phase 0 — Gate Criteria & Sample-Validity Audit

This file locks the quantitative bar an "automatable, sustainable edge" must clear before we touch `strategy_params.yaml` or `signal_generator.py`. Set before looking at feature results to prevent post-hoc moving of the goalposts.

## Sample-Validity Findings

The headline numbers from `summary.md` need correction for a 1-contract, $2,500-account bot:

| Metric | Headline (as-traded) | 1-contract simulated |
|---|---:|---:|
| Sum net P&L (138 covered ideas, ~5mo) | $15,869.56 | $4,138.70 |
| Mean per idea | $115.00 | $29.99 |
| Median per idea | $59.42 | $16.61 |
| Win rate | 91.3% | 89.1% |
| Sharpe per trade | 0.61 | 0.64 |

The trader sized up to **99 contracts** on Topstep prop capital. 11 high-conviction trades (size≥10) contributed $5,564 (35% of total $) at a 100% win rate. **A $2,500 IBKR account can never replicate that sizing.** All edge claims below refer to the 1-contract simulated tape unless stated otherwise.

### Concentration risk (1-contract tape)
- Top 5 ideas = 26% of total $
- Top 10 ideas = 37% of total $
- Top 20 ideas = 55% of total $
- Only 12 losers of 138 (8.7%); avg loss $99 vs avg win $135 — modest skew
- 4 losing days of 98 active trading days

### Survivorship & sample-size caveats
- Topstep shared-stats account; selected on past performance.
- 138 trades over ~98 active days, ~5 calendar months.
- Most context cells in `profitable_context_summary.csv` are n=1–3. Any per-context edge claim from this dataset alone is **not statistically defensible**.
- Distinct strategy labels currently used: 5. Discretionary/unclassified holds 63% of dollar edge.

## Gate Criteria — Edge must clear ALL of these to ship

### Phase 1 → Gate 1 (proceed/pivot/kill)
- **G1.1 Setup-vs-timing decomposition exists** for every covered idea (mechanical replica P&L computed alongside human P&L).
- **G1.2 Mechanical replica captures ≥40%** of the 1-contract human P&L. Below 40% → edge is in discretion, pivot to Framing C.
- **G1.3 No single day** contributes >25% of mechanical replica P&L (concentration sanity).

### Phase 4 → Gate 2 (automation-readiness)
- **G2.1 Trade count ≥80** in the rule set on the available history (raises per-bucket sample density).
- **G2.2 Sample-adjusted Sharpe ≥1.0** with deflation factor `sqrt(N_rules_tried / 1)` applied (multiple-comparison penalty).
- **G2.3 Walk-forward stability**: split history 60/40 chronologically; out-of-sample Sharpe ≥0.6× in-sample Sharpe.
- **G2.4 No regime collapse**: in-sample and out-of-sample win rate within ±10pp of each other.
- **G2.5 Max drawdown ≤ 4× expected weekly P&L** in 1-contract terms.
- **G2.6 Per-trade slippage budget** of 0.50 points (2 ticks) survives — recompute all economics with that haircut applied at entry AND exit.
- **G2.7 Honest sample-size disclosure** in the final report, per regime/bucket.

### Operational floor (independent of edge size)
- **G2.8 IBKR-compatible**: every rule expressible as an entry/exit condition the existing `signal_generator.py` interface can emit, OR an explicit list of new fields with a deepreview-flagged Tier 3 PR.
- **G2.9 RTH-only feasibility**: if the rule requires ETH data, that's documented as a separate epic, not silently assumed.
- **G2.10 No look-ahead**: every feature uses only completed bars at decision time. Already enforced by current `human_edge_replay.py`; will be re-verified in Phase 4.

## What we are explicitly NOT going to do
- Tune feature thresholds to maximize the 1-contract sum on this 138-trade tape (curve-fitting at n=138 is meaningless).
- Promote any rule that depends on the `discretionary_or_unclassified` label without first identifying a concrete, encodable feature behind it.
- Modify `bot/` or `config/strategy_params.yaml` in this branch. Findings hand off to Phase 2 live-execution work via spec.
- Treat the 91.3% win rate as predictive of future performance.

## Honest worst-case framing
If after Phase 4 nothing clears Gate 2: the conclusion is "the human's edge is in discretion, sizing, or selection that the bot cannot replicate on $2,500 with a 1-contract cap." That's a valid output of this research and the right call to make.
