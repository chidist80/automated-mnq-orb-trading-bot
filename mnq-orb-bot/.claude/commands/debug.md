Debug a losing streak or specific losing trade using HNSW-indexed trade history and neural pattern analysis.

Usage: /debug [description of what went wrong]
Example: /debug "5 consecutive losses on ORB retest this week, all during first 30 minutes"

1. Search trade history for similar patterns:
   ```
   npx ruflo@latest memory search --namespace trades --query "[user's description]" --reasoningbank
   npx ruflo@latest memory search --namespace mnq-bot --query "losing streaks similar conditions" --reasoningbank
   ```

2. Query neural patterns for known failure modes:
   ```
   npx ruflo@latest neural predict --task "why ORB retests fail: [conditions described]"
   npx ruflo@latest neural patterns --recent 30
   ```

3. Pull raw trade data from Supabase for quantitative analysis:
   - Filter trades matching the described conditions
   - Calculate: WR by setup, WR by hour, WR by OR size, WR by day of week
   - Compare current period stats vs historical baseline
   - Identify what changed (regime shift? parameter drift? crowding?)

4. Check external context:
   - `python -m research.crowding_monitor` — has ORB popularity spiked?
   - Check if VIX/ATR regime has shifted vs the backtest period

5. Recommend action:
   - If regime shift detected: suggest param recalibration (run /recalibrate)
   - If crowding detected: suggest tightening confluence filters
   - If single outlier: suggest no change (variance is normal)
   - If systematic failure in one setup: suggest disabling that setup temporarily

6. Store findings:
   ```
   npx ruflo@latest memory store --namespace mnq-bot --key "debug:$(date +%Y%m%d)" --value '<findings and recommendation>' --reasoningbank
   npx ruflo@latest hooks post-task --task-id "debug" --success true --train-neural true
   ```
