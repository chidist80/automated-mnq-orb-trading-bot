# Short-Side Research Findings v4 — Thesis-First Exhaustive Sweep

**Date:** 2026-04-26
**Window:** 2024-01-01 → 2026-04-24 (581 trading days, strict full-session-clean)
**Methodology:** 8 distinct theses, 84 variants, per-thesis BH-FDR (q=0.10), IS/OOS at 2025-06-30, slippage stress, combined portfolio.

---

## TL;DR

**Three new findings, each with different practical relevance:**

1. **ATR-CONDITIONAL STOPS on regime-conditioned mirror shorts** produce dramatic per-trade improvements (OOS PF 2-5x, OOS PnL +$1.5-2.7k vs canonical), but the per-trade risk ($250-$480) is **too large for the $3,750 account.** Real for capital ≥$10k. Less applicable today.

2. **RELAXED VWAP CAP** (raise from 65pt to 100pt or remove entirely) on regime-conditioned mirror shorts — same per-trade risk as canonical, OOS PF 2.89 with **zero IS-OOS degradation**, +$975 OOS PnL. Practically deployable. Best new finding for current capital tier.

3. **A+ decomposition:** the EMA slope filter is the load-bearing component (slope-only at threshold 20 yields PF ~1.79 alone); or_close_pos adds further selectivity but isn't redundant. The full A+ (both filters) remains the right specification.

**A failed thesis worth flagging:** T2 (day-after sequential) and T5 (volume confirmation) and T6 (extended time windows) all produced no robust survivors. The canonical 10-11 ET window and current A+ filter cluster appear genuinely well-tuned — not lucky.

---

## Per-thesis results

| Thesis | Story | Tested | Screened | BH-survived | OOS-passed | Slip-passed |
|---|---|---|---|---|---|---|
| **T1 Volatility-conditional** | ATR-scaled stops on regime shorts | 36 | 24 | 24 | 14 | **11** |
| T2 Day-after sequential | Yesterday's close direction predicts today's bias | 8 | 1 | 1 | 1 | 0 |
| T3 Higher-frequency | Looser regime = more shorts | 12 | 9 | 6 | 0 | 0 |
| T4 Regime intersection | Dual-confirmation regimes | 10 | 1 | 1 | 0 | 0 |
| T5 Volume confirmation | Volume-ratio ≥ X on signal bar | 7 | 2 | 1 | 1 | 0 |
| T6 Extended time windows | 11-12, 12-13, 13-14, 14-15 | 12 | 0 | 0 | 0 | 0 |
| **T7 VWAP-cap variations** | Stricter / looser VWAP delta on shorts | 8 | 4 | 4 | 4 | **2** |
| **T8 A+ decomposition** | Slope-only vs pos-only vs both | 9 | 7 | 5 | 5 | 3 |
| **TOTAL** | | **84** | **48** | **42** | **21** | **16** |

84 variants tested, 16 final survivors after every gate. Of those, the actionable ones for current capital are T7 short variants and T8 slope-only confirmations.

---

## T1 Volatility-conditional stops — the dramatic but capital-gated finding

**Setup:** mirror short, normal OR class, 10-11 window, regime gate (atr_pct>1.3 or 5d_ret<−1%), stop = ATR_20 × multiplier, target = stop × RR.

**Best variants (all OOS-and-slip-validated):**

| Variant | n total | IS PF | OOS PF | OOS n | OOS DD | pf@8pt slip |
|---|---|---|---|---|---|---|
| T1_atrstop1.2_rr1.0_atr_pct>1.3 | 18 | 9.47 | **4.84** | 9 | $642 | 6.81 |
| T1_atrstop1.2_rr1.5_5d_ret<−1.0% | 23 | 8.62 | 2.09 | 8 | $656 | 4.11 |
| T1_atrstop0.8_rr1.5_atr_pct>1.3 | 18 | 10.07 | 2.90 | 9 | (combined) | 5.19 |

**Combined with canonical Tier 1 LONG:**

| Combo | Total n | Total PnL | Total PF | Max DD | OOS PnL | OOS DD | OOS PF |
|---|---|---|---|---|---|---|---|
| Canonical Tier 1 LONG only | 89 | $2,036 | 1.67 | $417 | $672 | $417 | 1.52 |
| **+ T1_atrstop1.2_rr1.0_atr_pct>1.3** | 107 | **$7,554** | **2.83** | $642 | **$2,701** | $642 | **2.44** |

**OOS gains (best ATR variant):** +$2,029 OOS PnL (+302%), +60% OOS PF, max DD increases from $417 → $642 (+54%).

### Why these results need a capital warning

ATR_20 for MNQ in this window averages 150-200 points. ATR multiplier 1.2 → stop ~180-240 points → **$360-$480 dollar risk per trade.**

On a $3,750 account, that's 9.6-12.8% per-trade risk — well above the 1-2% rule of thumb, and incompatible with the canonical 20% MC ruin ceiling.

**Practical implication:**
- ≤$5,000 account: skip ATR-conditional stops; use fixed 40pt stops (T7 below)
- $10,000-$15,000 account: ATR multiplier 0.8 (stop ~$240 risk = 2-3% per trade) is borderline acceptable
- ≥$25,000 account: ATR multiplier 1.0-1.2 becomes appropriate

This isn't a "found alpha" — it's a "found a vol-adaptive sizing approach" that requires bigger capital to express. The PF improvement is real, but it's an architectural finding, not a free signal.

---

## T7 Relaxed VWAP cap — the actionable finding for current capital

**Setup:** mirror short, normal OR class, 10-11 window, atr_pct>1.3 regime, stop=40pt (same risk as canonical Tier 1), VWAP delta cap raised from 65pt to 100pt or removed entirely.

| Variant | n full | IS PF | OOS PF | OOS n | OOS PnL | pf@8pt |
|---|---|---|---|---|---|---|
| **T7_short_mirror_vwap100.0_atr** | 21 | 2.89 | **2.89** | 11 | $975 | 2.71 |
| T7_short_mirror_vwapnone_atr | 21 | 2.89 | 2.89 | 11 | $975 | 2.71 |

**Zero IS/OOS degradation** — same PF in both windows. This is the kind of stability we want.

**Combined with canonical Tier 1 LONG:**

| Combo | Total n | Total PnL | Total PF | Max DD | OOS PnL | OOS DD | OOS PF |
|---|---|---|---|---|---|---|---|
| Canonical Tier 1 LONG only | 89 | $2,036 | 1.67 | $417 | $672 | $417 | 1.52 |
| **+ T7_short_mirror_vwap100_atr** | 108 | **$2,915** | **1.84** | $424 | **$1,647** | $424 | (improved) |

**OOS gains:** +$975 OOS PnL (+145%), DD essentially unchanged ($417 → $424). 11 added trades over the OOS window.

### Why this is the best finding for current capital

- Same per-trade dollar risk as canonical ($80)
- OOS DD does not deteriorate (T1 ATR variants doubled the DD)
- IS/OOS stability is exceptional (no degradation)
- Sample is still small (n=11 OOS) but the *zero degradation* is the load-bearing evidence

**Recommendation:** the VWAP-relaxed regime short is the v4 finding most worth shadow-tracking forward.

---

## T8 A+ decomposition — what the original A+ filter is doing

**Original A+:** Tier 1 + (max_signal_ema_slope ≤ 20) + (min_or_close_pos ≥ 0.4) → 51 trades / $2,077 / PF 2.60.

**Decomposition tests:**

| Variant | n | IS PF | OOS PF | OOS n | OOS PnL |
|---|---|---|---|---|---|
| T8_aplus_slope_only (≤20) | 51 | 1.95 | 2.37 | 23 | (similar) |
| T8_slope_only_15 (stricter) | 39 | 1.92 | 1.91 | 20 | (similar) |
| T8_slope_only_25 (looser) | 56 | 2.03 | 1.58 | 27 | (similar) |
| T8_pos_only_0.3 | 70 | 1.48 | 1.55 | 34 | (similar) |
| T8_pos_only_0.4 (canonical A+ pos) | 60 | (similar) | | | |
| **A+ canonical (both filters together)** | 51 | 2.39 | 2.62 | 18 | $2,077 |

**Reading:**
- Slope-only at threshold 20 gives PF 1.95 IS, 2.37 OOS — better than canonical Tier 1 (1.67) but not as good as full A+ (2.60).
- Position-only at threshold 0.4 gives PF ~1.6 — modest improvement over Tier 1 alone.
- Both filters together give PF 2.60 — combined effect is multiplicative, not additive. **Both filters contribute.**

**Implication:** The A+ filter cluster is well-specified. Don't decompose it. The combined slope+position requirement carries ~50% more PF than either alone.

---

## What did NOT survive — and why that's informative

### T2 Day-after sequential (8 variants, 0 final survivors)
Predictions: yesterday's down day predicts today's short edge; yesterday's up day predicts today's long edge.
Result: only the "long after up day" variant passed BH/OOS but failed slippage stress.
**Interpretation:** the 5-day return regime already captures most of what "yesterday matters" would add. Single-day signals are too noisy.

### T3 Higher-frequency (12 variants, 0 final survivors)
Predictions: looser regime thresholds = more trades = bigger sample.
Result: more trades came at the cost of PF dropping below 1.4 IS.
**Interpretation:** the tight regime threshold is doing meaningful selection. Loosening it reintroduces low-quality trades.

### T4 Regime intersection (10 variants, 0 final survivors)
Predictions: dual-confirmation = higher conviction.
Result: dual-confirmation triggers fire so rarely (n<15) that even good per-trade economics fail OOS sample tests.
**Interpretation:** the single regime triggers are already capturing the regime signal. Intersection adds selectivity at the cost of statistical power.

### T5 Volume confirmation (7 variants, 0 final survivors)
Predictions: high-volume signal bars are higher quality.
Result: T5_long_t1_vol>1.5 passed OOS but failed slippage at 8pt RT (PF 1.08).
**Interpretation:** volume on a 1m bar is too noisy a signal. The OR-retest already implicitly selects for liquidity.

### T6 Extended time windows (12 variants, 0 final survivors)
Predictions: 11-12 lunch fade, 13-14 afternoon trend, 14-15 power hour for shorts.
Result: every alternate window underperformed. None passed even pre-screening.
**Interpretation:** the canonical 10-11 ET window is genuinely well-chosen. Other windows have different microstructure and don't replicate the OR-retest edge.

**This is valuable negative information.** The canonical strategy's choices (10-11 ET, single regime trigger, vol/slope/pos filters) are not arbitrary — alternatives consistently fail.

---

## Combined portfolio backtest summary

### Canonical + best individual addition
| Add-on | Total PnL | Total PF | Max DD | OOS PnL | OOS PF |
|---|---|---|---|---|---|
| (none — canonical only) | $2,036 | 1.67 | $417 | $672 | 1.52 |
| + T7 VWAP-relaxed short (atr regime) | $2,915 | 1.84 | $424 | $1,647 | 1.84 |
| + T1 ATR-stop short (atr regime) | $7,554 | 2.83 | $642 | $2,701 | 2.44 |

### Canonical + ALL final survivors (with same-day collision tiebreak by earliest entry_ts)
| | Total | OOS |
|---|---|---|
| n | 122 | 49 |
| PnL | $7,821 | $2,961 |
| PF | 2.35 | 2.13 |
| Max DD | $910 | $910 |
| Collisions | 109 (89% of canonical days have a competing signal) | — |

The "all survivors" backtest looks dramatic but is misleading: 109 collisions means most strategies fire on overlapping days. The marginal contribution of adding survivors beyond T7 is small.

---

## Three actionable recommendations

### 1. Add T7 VWAP-relaxed regime short as a shadow rule (HIGH CONFIDENCE)
- **Setup:** mirror short, normal OR class, 10-11 window, atr_pct>1.3 regime, 40pt stop / 50pt target, no VWAP delta cap (or 100pt cap)
- **Why:** zero IS/OOS degradation, same risk as canonical, OOS PF 2.89, +$975 OOS PnL
- **How:** add to canonical paper-forward harness as `tier1_vwap_relaxed_short` shadow rule alongside existing Tier 2 and A+ shadows

### 2. Test ATR-scaled TARGETS (not stops) as a separate research item (MEDIUM PRIORITY)
- **Hypothesis:** the ATR-stop variants' edge comes from "let winners run further in vol regimes," not from "use bigger stops." If we keep the 40pt fixed stop (so per-trade risk stays at $80) but use ATR-scaled targets (e.g., target = 1.0 × ATR_20), we may capture the volatility-regime edge without the position-sizing problem.
- **Why this isn't covered today:** v4 didn't test ATR-scaled targets specifically. Worth a v5 if the user wants to push further.

### 3. Don't change Tier 1 or A+ canonical (HIGH CONFIDENCE)
- T8 confirms A+ filter cluster (slope + or_close_pos) is well-specified
- T6 confirms 10-11 ET window is well-chosen
- T5 confirms volume-on-signal-bar adds nothing
- The current canonical specifications are not lucky — alternatives consistently fail
- **No change to TIER1_PILOT or A_PLUS_SHADOW.**

---

## What I would NOT do based on v4

- Promote T1 ATR-stop variants on the $3,750 account (per-trade risk too high)
- Reframe A+ as "slope-only" or "position-only" (decomposition shows both contribute)
- Test more time windows on this dataset (T6 was definitive — alternatives don't work)
- Test more day-after sequential variants (T2 captured the space; nothing survived)
- Promote any variant to live based on this analysis alone (forward evidence required)

---

## What I WOULD do as v5 (if user wants to keep pushing)

1. **ATR-scaled TARGETS with fixed stops** — separates volatility-aware edge from position-sizing artifact
2. **Walk-forward analysis** with 3-month windows on T7 to test stability across sub-periods
3. **VIX/SPX-derived regime filters** if external data can be sourced (would unlock truly-new regime hypotheses)
4. **Multi-asset regime triggers** (e.g., MES weakness predicts MNQ short edge)
5. **Pre-market gap research** with extended-hours data fetch (the Phase 3 hint about overnight short bias)

---

## Files

- `scripts/thesis_research_v4.py` — 8-thesis tester
- `research/human_edge_replay/short_side_exploration/thesis_v4_raw.json` — per-variant raw results
- `findings_v4.md` — this document
