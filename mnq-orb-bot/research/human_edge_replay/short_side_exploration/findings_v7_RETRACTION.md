# Findings v7 — RETRACTION of v3-v6 Regime/ATR Findings

**Date:** 2026-04-26 (post-independent-audit)
**Status:** FORMAL RETRACTION

---

## Summary

The independent verifier (`INDEPENDENT_VERIFICATION.md`) found a methodology defect that invalidates the regime-conditioned and ATR-scaled findings in v3, v4, v5, and v6. After fixing the look-ahead bug, the v5 headline recommendation (Recommendation A: Tier 1 LONG canonical + ATR-target SHORT with 5d_ret regime) **does not improve OOS performance over the canonical Tier 1 baseline alone.**

I have independently re-run the cross-product with strictly causal regime features and confirm the verifier's numbers.

---

## What was wrong

**Look-ahead in regime computation.** In `scripts/exhaustive_research.py`, `scripts/thesis_research_v4.py`, and `scripts/thesis_research_v5.py`, the regime map was built using same-day daily close, high, and low values:

```python
# THE BUG (in build_regime_map):
ret_5d = cs.pct_change(5)                          # uses today's close
atr_20 = daily_range.rolling(20).mean()            # uses today's high-low
atr_pct = daily_range / atr_20                     # uses today's range
sma_20_slope = cs.rolling(20).mean().diff(5)       # uses today's close
```

The trade decision is made between 10:00 and 11:00 ET, but today's close, high, and low are not known until session end. The regime gate was therefore peeking at end-of-day information to decide whether to trade earlier in the day. Same applied to ATR-scaled targets.

`gap_pct` and `prev_day_change` are not affected — they use yesterday's close (already in the past) and today's RTH open (known at 09:30).

---

## Independent reproduction of the corrected numbers

**Causal regime map** (in `scripts/causal_regime_correction.py`):

```python
cs_prior = cs.shift(1)                                   # series through t-1
ret_5d = cs_prior.pct_change(5)                          # (cs[t-1] - cs[t-6]) / cs[t-6]
atr_20_prior = (hs.shift(1) - ls.shift(1)).rolling(20).mean()  # avg ranges through t-1
atr_pct_prior = (hs.shift(1) - ls.shift(1)) / atr_20_prior     # yesterday's range / yesterday's ATR
```

**Critical comparison (canonical Tier 1 LONG + S2 ATR-target SHORT with 5d_ret<−1% regime):**

| | Leaked (v5 reported) | Causal (corrected) | Canonical alone |
|---|---|---|---|
| OOS trades | 45 | 43 | 36 |
| OOS PnL | $1,452 | **$555** | $672 |
| OOS PF | 1.80 | **1.31** | 1.52 |
| OOS DD | $338 | **$661** | $417 |

**Causal-corrected combination is WORSE than canonical Tier 1 LONG alone on every OOS metric.**

This matches the verifier's number to the dollar (verifier reported $555 / PF 1.31 / DD $661).

---

## Full corrected cross-product

| Combo | n | PnL | PF | DD | OOS_n | OOS_PnL | OOS_PF | OOS_DD |
|---|---|---|---|---|---|---|---|---|
| **L0_T1_canonical only** | **89** | **$2,036** | **1.67** | **$417** | **36** | **$672** | **1.52** | **$417** |
| L0 + S1 (atr_pct>1.3 short) | 104 | $1,821 | 1.48 | $424 | 41 | $780 | 1.53 | $424 |
| L0 + S2 (ATR-target+5d short) | 109 | $2,733 | 1.65 | $661 | 43 | $555 | 1.31 | $661 |
| L1 (T1 halt 5d<−1%) only | 65 | $1,768 | 1.85 | $259 | 24 | $448 | 1.52 | $259 |
| L1 + S1 | 82 | $1,380 | 1.46 | $467 | 30 | $470 | 1.42 | $331 |
| L1 + S2 | 89 | $2,692 | 1.76 | $604 | 34 | $643 | 1.41 | $604 |
| L2 (T1 halt 5d<−1.5%) only | 73 | $1,797 | 1.74 | $338 | 29 | $556 | 1.54 | $338 |
| L3_Aplus_canonical only | 51 | $2,077 | 2.60 | $424 | 18 | **$1,146** | **5.42** | $86 |
| L3 + S1 | 69 | $1,783 | 1.79 | $647 | 25 | $1,262 | 3.44 | $173 |
| L3 + S2 | 75 | $3,000 | 2.09 | $683 | 28 | $1,341 | 2.41 | $252 |
| L4 (A+ halt 5d<−0.5%) only | 38 | $1,759 | 3.04 | $173 | 12 | $764 | 5.42 | $86 |
| L4 + S2 | 62 | $2,683 | 2.15 | $424 | 22 | $959 | 2.11 | $252 |

**Honest read after causal correction:**

- **Canonical Tier 1 LONG alone is the best Tier-1-family OOS result.** No combination beats it on PF AND PnL AND DD.
- **A+ canonical alone has the highest OOS PF (5.42)**, smallest sample (n=18 OOS).
- L3 + S2 has higher absolute OOS PnL ($1,341 vs $1,146) but with significantly higher DD ($252 vs $86) — risk-adjusted, A+ alone wins.

---

## What stands (unaffected by the bug)

These findings used ONLY causal features (signal-bar VWAP, EMA9 slope at signal time, OR close position from 09:30-09:44 bars, prior-bar high/low) and are NOT impacted by the look-ahead bug:

1. **Canonical Tier 1 strict**: 89 trades / $2,035.74 / PF 1.6737 / max DD $417 / MC ruin @ $3,750 = 3.13%
2. **Canonical A+ shadow**: 51 trades / $2,076.66 / PF 2.6035 / max DD $424 / MC ruin @ $3,750 = 0.06%
3. **Causal mechanics**: 0 same-bar hindsight violations across all 95 default trades
4. **Append-mode safety**: all 3 unit tests + empirical audit pass
5. **Frozen rule constants**: TIER1_PILOT and A_PLUS_SHADOW specifications
6. **MC ruin computations**: based on canonical PnL vector, unaffected
7. **F1-F6 from the verifier handoff**: all reproduce exactly

---

## What is RETRACTED

The following claims and recommendations are formally withdrawn:

### From v3 (`findings_v3.md`)
- ❌ "Combined LONG-filtered + SHORT-regime improves OOS" (PF 1.78 vs 1.52)
- ❌ "LONG regime filter alone is OOS-worse than canonical" — the comparison itself used non-causal features

### From v4 (`findings_v4.md`)
- ❌ T7 (mirror short with no VWAP cap, atr_pct>1.3 regime) — atr_pct was non-causal
- ❌ T1 ATR-stop variants — used non-causal atr_pct gate
- ❌ All combined-portfolio claims that included regime-conditioned shorts

### From v5 (`findings_v5.md`)
- ❌ **Recommendation A** (Tier 1 + S2 ATR-target short): does not improve OOS over canonical
- ❌ **Recommendation B** (Tier 1 halt-5d<−1.5% + S2): same defect
- ❌ **Recommendation C** (A+ + S2): A+ alone is better
- ❌ All 20 cross-product results that combined regime-conditioned shorts with longs

### From v6 (`findings_v6.md`)
- ❌ Walk-forward stability of Recommendation A — was based on bug-driven trades
- ❌ NDX 5d_ret<−1% LONG halt as "improvement" over MNQ-internal — internal comparison was leaked; need to re-verify with causal MNQ regime
- ❌ Dual-external SHORT (VIX>20 AND NDX 5d<−1%) — uses external regime correctly but the COMBINED with internal-regime LONG was leaked

### Summary
**All v3-v6 "shadow rule" recommendations are withdrawn.** The 4 shadow rules I had recommended for paper-forward (`tier1_combo_v5`, `tier1_ndxhalt_combo_v6`, `aplus_combo_v5`, `aplus_dual_external_v6`) **must NOT be wired into the canonical paper-forward harness as combined rules.**

---

## What we should ACTUALLY do

1. **Phase 6.5 paper-forward should track ONLY canonical Tier 1 + canonical A+.** Drop all combined-shadow rules from the planned configuration.
2. **The frozen canonical baseline (`phase6-canonical-strict-2024plus` tag) remains valid** for execution after the gate clears. It uses only causal mechanics.
3. **A clean causal-only short probe is needed** if we want to revisit short-side research. The verifier's causal probe found no BH-FDR passers, suggesting the regime-conditioned short edge is not real once the look-ahead is removed.

---

## What I learned (process improvements)

### What went wrong
- Wrote regime computation using `pct_change` and `rolling().mean()` without thinking through which date the value was indexed at
- Did not include "regime causality" as a unit test or as part of the BH-FDR pre-registration
- The §2.5 caveat in the verifier handoff document explicitly named this concern, but I had not actually fixed it before claiming the findings

### What process change would have caught this earlier
- **Unit test:** for any regime gate, verify `regime[t]` depends only on data with timestamps `< t`. Could be a property test that randomizes data on day t and confirms regime values for day t do not change.
- **Pre-registration template:** every new regime / feature must declare its causality boundary at definition time, not after the fact.
- **No leaked feature shall be deployed even with adjustment.** Once a leaked feature is found, do not "patch" the existing finding — invalidate it and start over with the causal version pre-registered.

### What I would have done differently
- **Run the verifier's §2.5 challenge test BEFORE writing v5 findings.** The handoff doc named it; I should have run it.
- **Computed ATR series with explicit `shift(1)`** in the original v4 code.
- **Avoided "best of N" claims for combined portfolios** without a separate Bonferroni budget across cross-product cells.

---

## Updated ADR position

The canonical-mode ADR (`docs/superpowers/plans/2026-04-26-canonical-mode-adr.md`)
remains valid for:
- Frozen Tier 1 strict canonical
- Frozen A+ shadow (unchanged spec)
- Capital sizing notes
- MC ruin numbers
- Promotion gate criteria

It must be updated to:
- Remove any references to combined LONG+SHORT shadow rules
- Note that the v3-v6 short-side findings have been retracted
- Note that no executable short-side edge has been validated on this data

---

## Final note

The verifier did exactly what they were asked to do, found a real defect, and articulated it clearly. This is the entire point of the audit. **The right response to "your headline finding is wrong" is "you're right, here's the retraction" — not defensive justification.**

The frozen canonical Tier 1 + A+ remain the best evidence of edge in this data. They are also the only execution candidates that survive causal scrutiny. Phase 6.5 paper-forward should proceed against these alone.
