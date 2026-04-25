Evaluate whether the consensus signal validation layer (3-agent ensemble) is improving signal quality during paper trading.

Run after 50+ paper trades where consensus was active.

1. Pull paper trading data:
   - All signals generated (taken and skipped)
   - For each signal: consensus scores from all 3 agents (mechanics, regime, cross-market)
   - Actual outcome if taken, or "would-have-been" outcome if skipped

2. Calculate:
   - Unfiltered WR: win rate if ALL signals were taken (no consensus filter)
   - Consensus-filtered WR: win rate of signals that passed consensus (avg score ≥ 7)
   - Consensus-blocked accuracy: what % of blocked signals would have lost?
   - By agent: which agent's scoring is most predictive?

3. Decision matrix:
   - If filtered WR > unfiltered WR by ≥5% AND blocked accuracy > 60%:
     → CONSENSUS VALIDATED. Extract scoring logic into Python function for live.
   - If filtered WR ≈ unfiltered WR (within 3%):
     → NO IMPROVEMENT. Remove consensus layer. It's just latency.
   - If filtered WR < unfiltered WR:
     → CONSENSUS HARMFUL. Remove immediately.

4. If validated, the consensus logic gets extracted into a vanilla Python function
   (no ruflo runtime dependency) and added as a pre-trade filter in the signal generator.
   This becomes a simple function: `def consensus_check(signal_data) -> bool`

5. Store decision:
   ```
   npx ruflo@latest memory store --namespace mnq-bot --key "consensus-evaluation" \
     --value '{"unfiltered_wr": ..., "filtered_wr": ..., "delta": ..., "decision": "validated|removed|harmful"}' \
     --reasoningbank
   npx ruflo@latest hooks post-task --task-id "consensus-eval" --success true --train-neural true
   ```
