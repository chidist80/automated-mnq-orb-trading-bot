Run the full backtest validation pipeline with neural-assisted parameter selection:

1. Check for prior learned patterns:
   `npx ruflo@latest neural predict --task "optimal ORB params for current MNQ regime"`
   `npx ruflo@latest memory search --namespace mnq-bot --query "backtest results" --reasoningbank`

2. If neural predictions exist, use them to narrow the param sweep grid (test predicted configs first, then explore nearby).
   If no priors exist, run the full default grid.

3. Run backtester: `python -m backtest.backtester data/mnq_15m.parquet`

4. Run parameter sweep: `python -m backtest.param_sweep data/mnq_15m.parquet --quick`

5. Walk-forward on top params: `python -m backtest.walk_forward data/mnq_15m.parquet`
   GATE: ≥75% OOS windows profitable. If fail, try next param set.

6. Monte Carlo at $2,500: `run_monte_carlo(results, account_size=2500)`
   GATE: <5% ruin probability. If fail, flag undercapitalized.

7. Generate HTML report: `generate_report(results, "reports/backtest_$(date).html")`

8. Store results and train neural:
   `npx ruflo@latest memory store --namespace mnq-bot --key "backtest:$(date +%Y%m%d)" --value '<full results JSON>' --reasoningbank`
   `npx ruflo@latest neural train --model-type moe --epochs 5`
   `npx ruflo@latest hooks post-task --task-id "validate-$(date +%Y%m%d)" --success true --train-neural true`

9. Print clear summary: WR, PF, P&L, max DD, WF pass rate, MC ruin probability.
   State GO / NO-GO for Phase 2.
