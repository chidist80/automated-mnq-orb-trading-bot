# Gate 1 Verdict — PIVOT to Framing C

The 4-agent council was dispatched but all four members exhausted their usage limits and returned no analysis. The Gate 1 decision is therefore made from the Phase 0/1 numerical evidence directly, with the same statistical lens the council was briefed on.

## Pre-set Gate 1 criteria (from `phase0/gate_criteria.md`)

| Criterion | Status |
|---|---|
| G1.1 decomposition exists | ✅ pass |
| G1.2 mechanical replica captures ≥40% of human 1c P&L | ❌ FAIL (−27%) |
| G1.3 no single day >25% of replica P&L | N/A (replicas are losses, not edge) |

## Statistical evidence

### Human tape is statistically real
- n=138 covered, 1-contract simulated mean = $29.99/trade (95% CI $22.18–$37.80)
- Sharpe per trade = 0.64 (95% CI 0.46–0.82)
- Naive annualized Sharpe = 12.08 (CI 8.63–15.53) — even the floor is institutional-grade
- Win rate 89.1% (95% CI 82.7%–93.8%)
- p-value vs zero = 6.23e-12

### Mechanical universe has no defensible edge
- 24 cells tested (4 rules × 2 directions × 3 OR classes) over 109 clean days
- 10 of 24 cells have positive sum P&L
- Of those 10, best uncorrected p-value = 0.51 (orb_break short wide, n=11, +$214)
- Bonferroni alpha for 24 cells = 0.0021
- **Cells surviving Bonferroni: 0. Cells surviving uncorrected p<0.05: 0.**

### Same-day mechanical replica vs human
- Total: human +$4,138, replica rule-exit −$1,131, replica same-time-exit −$12,608
- Even on the days the human picked AND on the same direction, mechanical rules lose money

### Timing offset signature
- Median: human enters 28 minutes after first mechanical signal, gets 63 pts better entry
- 99 of 133 fired-signal ideas: human waited (post-signal entry); 34 anticipated
- Every timing-relation bucket profitable; >30min anticipators 100% (n=8), confirmed 5–30 min 93% (n=30)

## Interpretation

The human's edge is statistically robust but **not located in the setup-label hypothesis space we have tested**. Setup classification, OR-class, RSI regime, EMA position, time-bucket — none of these, alone or in combination at the granularity Phase 1 examined, distinguish profitable mechanical entries from random.

What does signal: the human waits substantially after a setup forms, gets a meaningfully better entry price, and wins at high rates. That is a *pattern* — but the trigger and the level the human is targeting are not in our current feature set.

## Decision: PIVOT to Framing C

**Phase 2 — Discretionary diagnosis.** Hand-inspect the highest-leverage trade clusters (top P&L, conviction-sized, fade-the-extreme bucket), render 1m chart slices around entry, manually identify the visible features the labels missed: prior-day H/L levels, gap fills, wick-rejection candles, volume divergence, prior-week levels, round numbers, news-event proximity, day-of-week regime, etc.

Output of Phase 2: a list of candidate features that ARE encodable from price/volume/calendar data, plus an honest list of features that AREN'T (require Level 2, news flow, or human pattern recognition the bot can't do).

If Phase 2 yields ≥3 encodable features that explain ≥50% of the conviction subset's behavior → proceed to Phase 4 with the augmented feature set. If Phase 2 yields features that are mostly non-encodable → honest conclusion is that this specific trader's edge is not replicable on $2,500 retail; we pivot to a different research thread (e.g., "what mechanical edges DO exist on MNQ that don't require human discretion?").

## What we are NOT doing

- **Not** going to Phase 4 directly. Curve-fitting features on n=138 without first understanding the discretionary signal would produce overfit garbage.
- **Not** killing the research. The timing-offset signature and conviction-trade coherence are too clean to abandon.
- **Not** modifying `bot/` or `config/strategy_params.yaml`. Per CLAUDE.md, that's Tier 3 work — and the verdict here is "no rule is ready to ship."
