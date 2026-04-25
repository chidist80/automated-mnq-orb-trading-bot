# Phase 2 — Discretionary Diagnosis Findings

## Question
The Phase 1 verdict said "the edge is not in the setup labels we tested — it's in something the human sees on the discretionary trades that we haven't encoded." Phase 2 asked: **what is that something?**

## Method
Tested specific encodable hypotheses on all 138 covered ideas (and the 11-trade conviction subset):
- Prior-day levels (PDH, PDL, PD-close), gap structure
- Round-number proximity (50-pt grid)
- Intraday levels: OR_high/low/mid, today's VWAP, today's session H/L, completed-bar swing pivots
- Longer-period EMAs (60m EMA20/EMA50)
- 1m bar patterns at the entry minute (rejection, doji, breakout)
- Pre-entry bar volume z-score

All features computed using only completed bars at decision time (no look-ahead).

## Result: every encodable hypothesis falsified

| Hypothesis | Coverage | Win-rate signal? |
|---|---:|---|
| Within 15pt of any prior-day level | 21/138 (15%) | No (85.7% vs 89.1% baseline) |
| Within 10pt of 50-pt round | 45/138 (33%) | No (88.9% vs 89.1%) |
| 60m EMA20 above vs below | 79 / 57 | No (88.6% vs 91.2%) |
| 1m bar pattern (any) at entry | All bins n=2-28 | No (80-96% across all) |
| Within 5pt of any intraday level | 16/138 (12%) | Modest (100% but n=16) |
| Conviction subset within 10pt of any level | **0/11** | n/a — they are FAR from levels |

The conviction-trade subset is the most striking: median absolute distance from entry to the nearest of {OR_high, OR_low, OR_mid, VWAP, EMA9_15m, EMA9_5m, swing_high, swing_low, session_H, session_L} = **178+ points**. Conviction entries are not at any standard technical level.

## The one real finding: volume quietness signature

| Cohort | Median pre-entry bar vol z-score | Fraction > +1σ |
|---|---:|---:|
| All 138 covered | -0.61 | 9.4% |
| Conviction (size≥10) | -0.89 | 0% |

The human consistently enters on **below-average-volume** 1m bars, after the move's high-volume action has passed. Mechanical breakout rules fire on the high-volume bar; the human waits for the bar AFTER. This matches the Phase 1 timing offset (median 28 min wait, 63 pts better entry).

But "low-volume bar" is a *property*, not a *trigger*. It doesn't tell us when to enter — only that when the human enters, the bar is quiet.

## Interpretation

The human's edge appears to require pattern recognition that simple feature thresholds do not capture. Plausible explanations:

1. **Order-flow / Level-2 reading.** The human watches DOM tape and feels supply/demand exhaustion. Not in our 1m OHLCV.
2. **Multi-bar shape recognition.** The human reads a 5–15 bar consolidation/reversal *shape*, not a single-bar feature. Encodable in principle but requires non-trivial pattern-matching infrastructure.
3. **Cross-asset / context cues.** SPX, NQ, sector rotation, news ticker. Not in our local data.
4. **Survivorship + intuition.** A skilled discretionary trader who happens to share stats publicly. The 89.1% win rate at n=138 has CI [82.7, 93.8] — real, but recall this trader is selected on past performance.

What we are confident of:
- The edge is real on this tape (Sharpe 0.64 per trade, p=6e-12 vs zero).
- It is not in any of: setup type, OR class, time bucket alone, RSI, EMA position, prior-day levels, intraday levels, round numbers, single-bar patterns, or volume confirmation.
- It correlates with: patience (median 28 min wait after first mechanical signal), better entry by ~63 pts, low-volume entry bars, mostly 09:30–11:00 ET, mostly counter-trend (above VWAP+EMA for shorts; below for longs).

## Implications for the project

The original goal was "find an edge we can automate and execute sustainably on a $2,500 1-contract MNQ bot." Phase 1 + Phase 2 together demonstrate that **the specific human's edge studied here is not extractable into a simple-feature mechanical strategy**. Pursuing Phase 4 (feature shortlist + walk-forward backtest) on the residual encodable signals would be:

- Fitting on n=138 with patterns that explain ≤30% of dollar P&L
- Per Gate 2 G2.1, the rule needs n≥80 trades; the encodable subset is 30–45 trades
- Per Gate 2 G2.2, sample-adjusted Sharpe ≥1.0 with 24+ rules tried — no cell survives Bonferroni in Phase 1
- This is curve-fitting, not edge discovery

## Recommendation: Gate-1.5 decision required

Phase 2 has effectively closed the original research question. The next gate decides among three options:

1. **HONEST NULL with pivot.** Write up the research as a definitive "this trader's edge is not automatable on $2,500 retail with simple features" report. Pivot the bot's research focus to a different question (e.g., "what mechanical edges DO exist on MNQ that don't require human discretion?" — likely candidates: end-of-day momentum, volatility-clustering setups, calendar effects).
2. **CONSTRAINED PROCEED.** Run Phase 4 on the narrow encodable subset (the fade-the-extreme + low-volume-entry combination) with full disclosure that we expect ≤30% of edge captured and high overfitting risk. Treat any positive walk-forward result as a "small additional signal," not the primary strategy.
3. **PIVOT TO HUMAN-ASSIST.** Rather than replicate the human, build tooling that *helps* a human trader: alert when conviction-pattern conditions cluster (time-of-day, direction, distance-from-level proxies), but require human approval. Out of scope for the current bot architecture but technically feasible.

I recommend **option 1 with optional option 2 as a secondary research line**. Reason: option 2 violates the "no curve-fitting at n=138" stance set in Phase 0, and the user's stated goal was a *sustainable* automated edge, which a 30%-explanatory feature set cannot provide on its own. A clean pivot is better than a fuzzy "we kind of automated something" outcome.
