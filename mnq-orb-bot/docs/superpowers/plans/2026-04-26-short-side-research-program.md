# Short-Side Research Program — Pre-Registered Hypothesis Catalog

**Goal:** Determine whether any short-side OR-retest variant has executable edge in the canonical 2024+ window — with discipline that prevents finding a noise pattern by accident.

**Honest framing:** With 581 trading days and ~50-80 short signals per variant, we have low statistical power. We will reject false positives aggressively via multiple-comparison correction, accept that most variants will fail, and treat any survivor as a *candidate to be paper-validated*, not a discovery.

---

## Why this needs structure (the p-hacking risk)

We can construct ~24,000 candidate short rules by combining: 7 setup triggers × 4 OR-class filters × 4 time windows × 6 secondary filters × 6 stop/target combos × 4 regime filters × 3 combination modes. If we test 50 variants at α=0.05, we expect 2-3 to look "significant" by chance. **Pre-registration + multiple-comparison correction is non-negotiable.**

---

## Lever inventory (every knob we can turn)

### Setup trigger (S)
| | Description | Already tested |
|---|---|---|
| S1 | Symmetric mirror of long retest (low touched OR_low+5, close < OR_low, close < prev_low) | ✅ broke even |
| S2 | Failed-long fade (close > OR_high earlier, then close back < OR_high in retest window) | ❌ |
| S3 | VWAP-rejection short (price reclaims VWAP, then closes back below within window) | ❌ |
| S4 | Lower-high pattern (15m EMA9 down, current bar high < prior bar high, close < prior low) | ❌ |
| S5 | Naive breakdown (first close < OR_low after 09:45, no retest required) | ❌ |
| S6 | Pre-market gap-down + breakdown — REQUIRES ETH data, NOT TESTABLE TODAY | — |

### OR class (C)
| | Description |
|---|---|
| C1 | Normal only (current Tier 1 long) |
| C2 | Wide only (Phase 1 hinted at short edge here) |
| C3 | Tight only (failed-long setups) |
| C4 | All classes |

### Time window (T)
| | Description |
|---|---|
| T1 | 10:00-11:00 ET (current Tier 1) |
| T2 | 09:45-10:30 ET (early momentum) |
| T3 | 13:00-15:00 ET (afternoon weakness) |
| T4 | 14:30-15:30 ET (late-day liquidation) |

### Filter (F)
| | Description |
|---|---|
| F1 | VWAP delta cap (current: ≤ 65pt below) |
| F2 | + Volume spike (signal-bar volume_ratio20 > 1.5) |
| F3 | + Below 15m EMA9 at signal time (confirmed downtrend) |
| F4 | + EMA9 slope negative |
| F5 | + RSI14 < 30 (oversold extension) |
| F6 | + RSI14 > 70 (overbought rejection — for S2 failed-long) |

### Stop/target (P)
| | Stop / Target / RR | Rationale |
|---|---|---|
| P1 | 40 / 50 / 1.25R | Mirror of long Tier 1 |
| P2 | 25 / 40 / 1.6R | Tight stop, modest target — bear-trap aware |
| P3 | 30 / 45 / 1.5R | Middle ground |
| P4 | 50 / 50 / 1.0R | Wide stop, even RR |
| P5 | 40 / 80 / 2.0R | Mirror of Tier 2 long |

### Regime (R) — testable subset
| | Description |
|---|---|
| R0 | No regime filter |
| R1 | MNQ 5-day return < -1% (proxy for "weak regime") |
| R2 | MNQ 20-day SMA slope negative |
| R3 | Day's open below prior day's close (gap-down day) |

(R-VIX, R-SPX would require external data; deferred.)

### Combination (M)
| | Description |
|---|---|
| M1 | Independent — short fires regardless of long state |
| M2 | Mutually exclusive — short only on days when long not eligible |
| M3 | Daily directional selector — one side per day based on opening structure |

---

## Pre-registered hypotheses (TESTABLE TODAY)

10 candidates ranked by structural plausibility. Numbered for traceability.

| # | Setup | OR | Time | Filter | P | Regime | Rationale |
|---|---|---|---|---|---|---|---|
| H1 | S2 failed-long fade | C4 all | T1 10-11 | F1 VWAP | P2 25/40 | R0 | Failed breakout = bear trap on shorts; tight stop for squeeze risk |
| H2 | S5 naive breakdown | C2 wide | T1 10-11 | F1 | P1 40/50 | R0 | Phase 1 hint: wide-OR shorts had positive sample |
| H3 | S3 VWAP rejection | C1 normal | T1 10-11 | F4 EMA-down | P1 40/50 | R0 | Failed bullish reclaim with EMA confirming = high-conviction short |
| H4 | S1 mirror | C1 normal | T1 10-11 | F1+F3 below-EMA | P1 40/50 | R0 | Add downtrend gating to symmetric short |
| H5 | S1 mirror | C1 normal | T1 10-11 | F1 | P2 25/40 | R0 | Same trigger as canonical, tighter risk |
| H6 | S1 mirror | C1 normal | T1 10-11 | F1 | P1 40/50 | R1 5-day weak | Regime-conditioned short |
| H7 | S5 naive breakdown | C4 all | T3 13-15 | F1 | P1 40/50 | R0 | Afternoon breakdown / late-day weakness |
| H8 | S2 failed-long fade | C1 normal | T1 10-11 | F6 RSI>70 | P2 25/40 | R0 | Distribution-at-the-top short |
| H9 | S1 mirror | C1 normal | T2 9:45-10:30 | F1 | P1 40/50 | R0 | Earlier window = more momentum continuation |
| H10 | S1 mirror | C1 normal | T1 10-11 | F1 | P1 40/50 | R3 gap-down | Short only on gap-down opens |

### Why these 10 (and not 50)
Each has a market-mechanics rationale (not "let's try this combination"). Each varies one or two levers from a baseline we already know (long Tier 1 mechanics). This bounds the search and makes the multiple-comparison correction tractable.

---

## Methodology

### Stage 1 — Sample-size + raw aggregate filter
For each hypothesis, run on the full canonical 2024-01-01 → 2026-04-24 window with `--require-full-session-clean`.
- **Pass criteria:** n ≥ 30 trades AND raw PF ≥ 1.3 AND avg PnL > 0
- Variants failing Stage 1 are rejected without further testing.

### Stage 2 — IS/OOS robustness
Survivors split:
- **IS:** 2024-01-01 → 2025-06-30 (~377 days)
- **OOS:** 2025-07-01 → 2026-04-24 (~204 days)
- **Pass criteria:**
  - IS PF ≥ 1.4 AND OOS PF ≥ 1.2
  - |IS_avg − OOS_avg| / IS_avg < 0.40 (≤ 40% degradation)
  - n_OOS ≥ 15 (otherwise underpowered, defer)

### Stage 3 — Multiple-comparison correction
- Compute one-sided t-stat for OOS avg PnL vs zero
- Bonferroni α: 0.05 / num_variants_tested = 0.05 / 10 = **0.005**
- Survivors must clear p < 0.005 OOS

### Stage 4 — Slippage sensitivity
- Re-test survivors at 1.5 / 3.0 / 5.0 / 8.0 / 12.0 pt RT slippage
- **Pass criteria:** PF ≥ 1.3 at 8pt RT (real demo data may show worse fills than 5pt assumption)

### Stage 5 — Decision
- **Survivors:** propose for shadow-only forward tracking (mirror of A+ shadow status); minimum 50 forward trades before promotion to live alongside Tier 1 LONG
- **Failures:** document in this file; long-only stands as the only executable side; close the question for ≥ 6 months unless new data arrives

---

## Reporting requirements

Findings written to `research/human_edge_replay/short_side_exploration/findings_v2.md` with:
- Per-hypothesis raw + IS + OOS metrics
- Per-hypothesis Bonferroni-corrected p-values
- Slippage-sensitivity table for any survivor
- Honest verdict per hypothesis (PASS / FAIL_STAGE_N / UNDERPOWERED)
- Overall conclusion: is there ANY executable short edge in this data?

---

## Important caveats baked into the design

1. **Sample window is one regime.** All testing is within a persistent NDX uptrend (2024-2026). A short variant that "works" here would still be regime-conditioned. We cannot test bear-regime performance with the data we have.
2. **Bonferroni is conservative for non-independent variants.** Many of these hypotheses share structure (e.g., several use C1+T1+F1). The true number of independent tests is fewer than 10. We accept the conservative bias as the price of discipline.
3. **OOS sample is small.** ~20-30 trades typical. Underpowered for strong claims. A pass at this stage is a *paper-forward candidate*, not a verified edge.
4. **No tuning during testing.** Each hypothesis has fixed parameters declared above. We do not "adjust" mid-test if results look promising.
5. **No back-fitting.** If H1 fails and we notice "but with stop=30 it would have worked," that observation cannot be promoted to a new hypothesis without restarting the multiple-comparison budget on a new candidate set.

---

## Backlog (deferred, not tested today)

- S6 pre-market gap-down — requires ETH (overnight) data
- R-VIX / R-SPX regime filters — requires external time series
- Combination modes M2, M3 (mutually exclusive, daily selector) — only relevant if a base short variant survives Stage 4
- Walk-forward analysis with monthly retraining — appropriate for a survivor going to forward
