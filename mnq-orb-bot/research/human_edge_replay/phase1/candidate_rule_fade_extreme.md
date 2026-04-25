# Candidate rule: fade-the-extreme at open

Extracted from the conviction-trade signature (n=11, all winners, $5,564 as-traded).
The 11 conviction trades have a coherent shape: counter-trend short above VWAP+EMA at extension, counter-trend long below VWAP+EMA at extension, mostly 09:30–11:00 ET, average MFE 218 pts vs MAE 68 pts (~3:1 R/R), held ~166 minutes.

## Rule definition (human-entry filter form)

```
Match an entry if:
  time_bucket in {09:30-10:00, 10:00-11:00}
  AND ( (direction == short AND entry_vs_vwap == above AND entry_vs_15m_ema9 == above AND rsi_regime != bear_trend_zone)
        OR
        (direction == long  AND entry_vs_vwap == below AND entry_vs_15m_ema9 == below AND rsi_regime != bull_trend_zone) )
```

## Performance on the human tape (1-contract simulated)

| Metric | Value |
|---|---:|
| Matching ideas | 45 of 138 (32.6%) |
| Direction split | 33 short / 12 long |
| Sum 1c P&L | $1,481.45 |
| Mean per idea | $32.92 |
| Median per idea | $11.59 |
| Std | $49.02 |
| Sharpe per trade | 0.67 |
| Win rate | 88.9% |

By time bucket:
- 09:30-10:00: n=10, sum $348, win 90%
- 10:00-11:00: n=35, sum $1,134, win 89%

## What this rule does NOT solve

The rule is a **filter on pre-entry context** — it does not specify the trigger or the exit. It tells us which human entries cluster together; it does not tell us how to time the entry mechanically. Phase 1 already demonstrated that mechanical timing on the human's labeled setups loses money on the same days, so this filter alone cannot be deployed.

A mechanical version of this rule would need to add:
1. A trigger condition (e.g., "wait for an N-bar candle pattern in the fade direction" — pattern TBD)
2. An exit definition (the human held mean 166 min — far longer than typical scalp targets)
3. A no-trade veto for trend-day regimes (the rule will likely fail catastrophically on strong-trend days, which retail traders survive by skipping)

## Sample-size honesty

Matching trades = 45 over 5 months (~9/month). Out of those, only 5 are conviction-sized; the rest are 1–9 contracts. The 89% win rate at n=45 has a 95% CI of roughly [76%, 96%] (Wilson interval), so even the "high win rate" claim is loose. **Below Gate 2's G2.1 threshold of n≥80 for the rule alone**, before adding any out-of-sample split.

## Status

Held for Gate 1 council verdict. If verdict is PROCEED to Phase 4 → this is the leading candidate to formalize as a mechanical rule and walk-forward test. If verdict is PIVOT to Framing C → this is the first cluster to inspect chart-by-chart for the missing trigger pattern. If verdict is KILL → this becomes the headline pattern in the null-result writeup.
