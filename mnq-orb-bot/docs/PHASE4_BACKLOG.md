# Phase 4 Recalibration Backlog

Strategy enhancements identified during Phase 2 planning (2026-04-25) from u/NeverStoppedout's 5-month verified streak (TopstepX share link, public stats: $18,419 / 74.91% WR / 2.10 PF / 578 trades). These are **deliberately deferred** to Phase 4's recalibration loop because adding them now — after Phase 1's OOS data has been observed — would constitute curve-fitting.

When Phase 4's neural recalibration loop runs against future unseen data, propose these as candidate enhancements one at a time and measure uplift on truly held-out periods.

## Candidate enhancements

### VWAP as support/resistance confluence
**Source claim:** "Vwap. Does it act as support / resistance to support trend."
**How:** add a `vwap` confluence to entries — long entries require price near or above VWAP; short entries near or below. Compute session VWAP (reset at 09:30 ET). 
**Risk:** changes signal frequency. Validate on held-out data only.

### RSI 65/35 as trend-regime identification
**Source claim:** "On trendy day, price stays above 65 RSI (bullish) or below 35 RSI (bearish)."
**How:** new regime filter — gate inverse_orb (mean reversion) when RSI persistently above 65 or below 35. Distinct from our current RSI 70/30 entry filter.
**Note:** this layers on top of, not replaces, our OR-width regime filter (Filter A).

### 5m EMA early-warning exit during a 15m position
**Source claim:** "Monitor 5 min EMA-s to see if the story is changing there to start doubting my trade."
**How:** while a 15m bracket is open, watch the 5-min 9-EMA. If price closes against the 5-min EMA in the direction opposite the trade, exit early at market.
**Risk:** changes trade-management semantics — paper test extensively before any live deploy.

### Volume divergence on pullbacks vs trend
**Source claim:** "Volume on pull backs, is it increasing or decreasing? Trend pullback with low volume → trend trustworthy."
**How:** during a continuation pullback (the 9EMA retest bar), require volume LOWER than the prior trend bar's volume. Reject pullback retests where volume is climbing — that's a reversal warning.

### Time-of-day analysis (data-driven)
**Source claim:** "I have fixed time when I trade. I can see clearly which time of the day is the most beneficial for me."
**How:** after ~100 live paper/live trades, analyze WR/PF by hour-of-day (and day-of-week). Tighten the trading window to the empirically best hours. **Cannot do this on backtest data** without curve-fitting; only meaningful with live trades.

### Automated news/event blackout
**Source claim:** "Clearest signs to not take my set ups must be news."
**How:** integrate FRED (FOMC dates), BLS (CPI/NFP/PPI dates), and a manually maintained calendar of Trump speech / fed-speaker events. Skip signals within ±30 min of any flagged event.
**Status:** the only enhancement on this list defensible to add **immediately** (Phase 2). It's risk reduction, not edge optimization.

## Meta-findings (informational only)

### Streak psychology is a HUMAN failure mode
The benchmark trader explicitly reports: "Green streak has done more harm to me than good. I get protective. I miss A+ setups, undersize, close winners early." This validates the algorithmic approach — our system has no concept of "the streak," so it can't be protective. Circuit breakers are reactive (halt after losses), not protective (skip setups to preserve runs). **No code change required; document so future operators don't accidentally introduce streak-protection logic.**

### "Make trading boring"
Source: "I dont rush into taking trades, I truly sit on my hands for hours before I enter." Algorithmic strategies are naturally boring — confluences gate signal frequency. The translation is that we should NOT add knobs that fire more signals to "stay engaged." If our strategy emits 1-2 trades/day on average, that's a feature, not a bug.

### Wide stops over tight stops
Source: "Stop losses are usually fairly wide, I dont get out where 'too much money lost' but where the trade idea is invalidated." Our `stop_max_points: 120` aligns with this philosophy. **Document so future tightening of stops requires explicit re-validation.**
