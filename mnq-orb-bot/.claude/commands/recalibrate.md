Monthly parameter recalibration for the MNQ ORB bot.

Run on the first weekend of each month. Uses neural learning to improve param selection over time.

1. Retrieve accumulated intelligence:
   ```
   npx ruflo@latest neural predict --task "optimal ORB params for current MNQ regime given last 12 months"
   npx ruflo@latest memory search --namespace mnq-bot --query "backtest recalibration" --reasoningbank
   npx ruflo@latest neural patterns --recent 10
   ```

2. Check if historical data needs updating. Ensure data/mnq_15m.parquet covers latest 12 months.
   If stale, run scripts/fetch_data.py to update.

3. Run focused param sweep using neural predictions as starting configs:
   - If neural suggests specific params, test those first plus +/- 1 step on each dimension
   - If no predictions, fall back to full QUICK_GRID sweep
   `python -m backtest.param_sweep data/mnq_15m.parquet --quick`

4. Walk-forward on top 3 configs: `python -m backtest.walk_forward data/mnq_15m.parquet`

5. Monte Carlo on winning config: `run_monte_carlo(results, account_size=current_equity)`

6. Compare new optimal params vs currently deployed params.
   If shift is minor (<10% change on any param): log but don't change.
   If shift is significant: flag for review, do NOT auto-deploy.

7. Store results and train:
   ```
   npx ruflo@latest memory store --namespace mnq-bot --key "recalib:$(date +%Y%m%d)" --value '<results + comparison>' --reasoningbank
   npx ruflo@latest neural train --model-type moe --epochs 10
   ```

8. Check crowding monitor: `python -m research.crowding_monitor`
   If ORB mentions trending up, note in recalibration report.

9. Generate recalibration report with before/after comparison.

10. Print summary: old params vs new optimal, WR/PF delta, recommendation (keep/change/investigate).
