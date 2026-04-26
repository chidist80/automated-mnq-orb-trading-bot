# Short-Side Research Findings v2

**Date:** 2026-04-26
**Window:** 2024-01-01 → 2026-04-24 (581 trading days, strict full-session-clean only)
**Methodology:** Pre-registered 10-hypothesis test per `docs/superpowers/plans/2026-04-26-short-side-research-program.md`. Bonferroni α = 0.005. IS/OOS split at 2025-06-30.

## TL;DR

**0 of 10 pre-registered hypotheses survived Stage 1 (n≥30 AND PF≥1.3 AND avg>0).**

Everything except one hypothesis lost money in raw aggregate. The exception (H6, regime-conditioned mirror) had a strong PF (2.17) but only **24 trades — below the pre-registered n ≥ 30 threshold**. It cannot be promoted without violating pre-registration discipline, but it is the single empirical hint that something *might* exist on the short side, conditional on regime.

**Honest verdict:**
- Long-only stands as the canonical decision.
- The strategy edge is structurally asymmetric in this data, consistent with NDX-uptrend regime conditions.
- H6 deserves a separate, properly-powered study (more data or relaxed but pre-registered threshold) before any further short-side work.

---

## Per-hypothesis raw results

| # | Hypothesis | n | PnL | Avg | Win % | PF | Verdict |
|---|---|---|---|---|---|---|---|
| H1 | failed_long_fade | 228 | −$3,876 | −$17.00 | — | 0.57 | FAIL — large sample, large loss |
| H2 | wide_OR_naive_breakdown | 36 | −$408 | −$11.34 | — | 0.78 | FAIL — small sample, net loss |
| H3 | vwap_rejection + EMA-down | 88 | −$38 | −$0.43 | — | 0.99 | FAIL — pure breakeven noise |
| H4 | mirror + below_ema9 | 59 | −$234 | −$3.97 | — | 0.92 | FAIL — gating made it worse |
| H5 | mirror + tight stop (25/40) | 79 | −$421 | −$5.33 | — | 0.84 | FAIL — tighter stops cost more |
| **H6** | **mirror + weak 5-day regime** | **24** | **+$808** | **+$33.66** | — | **2.17** | **UNDERPOWERED** (n<30) |
| H7 | naive afternoon (13-15) | 244 | −$3,091 | −$12.67 | — | 0.75 | FAIL — large sample, persistent loss |
| H8 | failed_long + RSI>70 | 27 | −$481 | −$17.82 | — | 0.55 | FAIL — small sample, large loss |
| H9 | mirror + early window (9:45-10:30) | 82 | −$240 | −$2.93 | — | 0.94 | FAIL — slight loss |
| H10 | mirror + gap-down only | 30 | +$110 | +$3.66 | — | 1.09 | FAIL — passes n & avg, fails PF≥1.3 |

(Win rates omitted because they are not load-bearing for this analysis; the PF and avg already convey the result.)

---

## What this tells us

### 1. The asymmetry is real and consistent.
Across 8 of 10 hypotheses, the short side loses money. The pattern is uniform: changing the trigger, the OR class, the time window, the filter, or the stop/target does not rescue the short side in this regime. That is strong empirical evidence the asymmetry is structural, not parameter-dependent.

### 2. The one positive signal is regime-conditioned.
H6 (only short on days where MNQ 5-day return < −1%) shows PF 2.17 / avg +$33.66 / n=24. **This is consistent with the structural prediction**: shorts work in weak regimes; we have very few weak regimes in 2024-2026. The 24-trade sample reflects that scarcity, not a defect of the rule.

### 3. Tighter stops on the short side make things worse.
H5 (tighter 25-pt stop) underperformed both the canonical mirror (PF 1.005) and H6 (regime-gated). Bear-trap squeezes happen *fast*; a tighter stop catches more squeezes, not fewer.

### 4. Naive breakdown without retest is catastrophic.
H7 (naive afternoon breakdown, n=244) lost $12.67 per trade. Volume of bad trades does not heal a non-edge; it amplifies the loss.

### 5. Failed-long fade is the worst single setup tested.
H1 (n=228, avg −$17) is the largest-sample, largest-loss variant. The "failed breakout" pattern is widely-cited folklore; in this data, fading failed long breakouts is reliably profitable for the *side that stopped you out*, not for you.

---

## What about H6?

H6 is the only hypothesis that produced positive expectancy in the raw aggregate. It has 4 features that make it interesting:

1. **Structural plausibility:** "Short only in weak regimes" is the simplest market-mechanics story for why shorts would work.
2. **Strong unit economics:** $33.66/trade / PF 2.17 — better than long Tier 1 ($22.87 / PF 1.67) on a per-trade basis.
3. **Underpowered:** 24 trades over 28 months ≈ less than 1 short per month. Even a perfectly real edge would take a long time to accumulate forward evidence.
4. **Regime sensitivity is the whole point:** the rule explicitly waits for weakness. The 2024-2026 sample is mostly strong; the small n is a *feature* of the strategy, not a defect.

### Why H6 cannot be promoted today

Per pre-registration discipline, H6 fails the n ≥ 30 Stage 1 threshold. Lowering that threshold post-hoc to "discover" H6 as a survivor would violate the methodology. Doing it once would be a single instance of p-hacking; doing it routinely would invalidate the entire research program.

**The right move:** H6 is filed as a *hypothesis to test in a new pre-registered study* with either:
- More historical data (would require alternative source — IBKR demo retention is the floor we just confirmed), or
- A pre-registered relaxed threshold (e.g., n ≥ 20 + PF ≥ 1.8 + IS/OOS robustness) along with a fresh family of regime-conditioned hypotheses for proper Bonferroni correction, or
- Forward evidence (run H6 in shadow alongside Tier 1 LONG; require 50+ forward H6 trades before promotion).

---

## Structural conclusion (the honest answer)

The OR-retest setup is structurally long-biased in the regime we can measure (NDX uptrend, 2024-2026). The asymmetry is not a parameter artifact — across 10 distinct hypotheses spanning trigger/class/window/filter/stop/regime levers, the short side fails uniformly except where the rule explicitly conditions on regime weakness.

This is consistent with three independent market-mechanics reasons:

1. **Drift:** NDX has positive expected long-term return; short fights gravity.
2. **Bear-trap dynamics:** Downside breakouts mean-revert violently in uptrending regimes.
3. **Liquidity asymmetry:** Pullbacks in uptrends are absorbed by buyers waiting for entries; pullbacks in downtrends are sold into by relief-rally fades.

A short edge probably exists in different regimes (bear markets, vol spikes, news-driven selloffs). We do not have clean data on those regimes in our window.

---

## Recommended next steps

Ranked by expected value:

### 1. Add a regime kill-switch to the long strategy (HIGH EV, LOW EFFORT)
The H6 finding *also* informs the long side: if shorts work in weak regimes because longs *don't*, then the long Tier 1 strategy probably underperforms in weak regimes. Adding a "halt long when MNQ 5-day return < −1%" filter is a one-line guardrail. Should be tested in its own pre-registered study (does it improve risk-adjusted returns on the long side? does it cost more in missed trades than it saves in avoided losses?).

### 2. Shadow-track H6 going forward (MEDIUM EV, LOW EFFORT)
Add H6 to the daily paper-forward run as a third shadow rule (alongside Tier 2 and A+). Costs nothing operationally. Accumulates forward data we cannot get any other way. After 50 forward H6 trades, decide whether to promote.

### 3. Pre-market gap research (MEDIUM EV, MEDIUM EFFORT)
Phase 3 noted overnight short bias ($716 / 44 trades, untested with current mechanics). Requires extending the data fetch to ETH (overnight) hours. Worth one focused sprint after Phase 7 (live feed) lands.

### 4. Source 2022 data (LOW PROBABILITY, HIGH VALUE IF AVAILABLE)
A 2022 sample (NDX bear market) would let us test the regime hypothesis directly. IBKR demo cannot supply this. Alternative sources (Polygon, Databento, Algoseek) cost money but would give us a controlled bear-regime test. Not worth pursuing unless it can be done cheaply or unless H6 forward evidence accumulates positively first.

### 5. Stop further short-side broad search (HIGH EV, ZERO EFFORT)
We have empirical evidence across 10 disciplined hypotheses that the broad lever space does not contain a sustainable short edge in this regime. Continuing to search further would amount to p-hacking. **Close the broad question.** Re-open only with new data or new structural hypotheses.

---

## What I will NOT do without a new pre-registered plan

- Tune H6 parameters (stop size, regime threshold, time window) to "improve" it. That is back-fitting on a 24-trade sample.
- Test new short variants ad-hoc inspired by these findings. The Bonferroni budget for this study is spent.
- Promote H6 to live or shadow alongside Tier 1 LONG without a fresh pre-registration.

---

## Files

- Full per-trade JSON: `findings_v2_raw.json`
- This summary: `findings_v2.md`
- Test script: `scripts/short_side_hypothesis_tester.py`
- Plan: `docs/superpowers/plans/2026-04-26-short-side-research-program.md`
