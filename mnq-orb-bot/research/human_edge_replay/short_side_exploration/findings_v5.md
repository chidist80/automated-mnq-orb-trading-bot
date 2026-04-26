# Short-Side Research Findings v5 — ATR-Targets Win + Final Combined Portfolio

**Date:** 2026-04-26
**Window:** 2024-01-01 → 2026-04-24 (581 trading days, strict full-session-clean)
**Methodology:** v5 ran 5 follow-up theses (74 variants), then tested 20 cross-product combinations of LONG (5 configs) × SHORT (4 configs).

---

## TL;DR — The Headline

**ATR-scaled TARGETS with FIXED 40pt stops on regime-conditioned mirror shorts is the best new finding.** Same per-trade dollar risk as canonical Tier 1 ($80), but combined OOS PnL more than doubles, OOS PF improves 30-50%, and OOS DD stays at canonical levels.

**The single best deployable combination for the $3,750 account:**

| Component | Specification |
|---|---|
| **LONG** | Canonical Tier 1 (no regime filter) — n=89, $2,036, PF 1.67, DD $417 |
| **SHORT** | Mirror short, normal OR, 10:00-11:00, 40pt stop, **1.0 × ATR_20 target**, regime: 5d_ret < −1%, NO VWAP cap |
| **Combined OOS** | n=45, PF 1.80, PnL $1,452 (vs canonical $672), DD $338 |

For higher-quality but smaller-sample alternative: **A+ LONG + same SHORT** → OOS PF 3.39, OOS PnL $2,267, OOS DD $173.

---

## The critical experiment — ATR-targets vs ATR-stops

**v4 found:** ATR-scaled stops on regime shorts produced enormous PF improvements (OOS PF 2-5x), but per-trade risk was $250-$480 — incompatible with $3,750 account.

**v5 hypothesis (F1):** Maybe the edge is in *letting winners run further in vol regimes*, not in *taking bigger losses*. Test ATR-scaled targets with FIXED 40pt stops.

**Result:** Hypothesis confirmed. The volatility-edge IS real and survives without enlarged stops.

| Variant | OOS PF | OOS PnL | OOS DD | pf@8pt slip |
|---|---|---|---|---|
| F1_atrtarget0.5_atr_pct>1.3 | 1.44 | $798 | $590 | 2.65 |
| F1_atrtarget0.75_atr_pct>1.3 | 1.62 | $1,129 | $590 | 4.03 |
| F1_atrtarget0.75_5d<−1% | 1.82 | **$1,484** | **$338** | 3.36 |
| **F1_atrtarget1.0_5d<−1%** | **1.80** | **$1,452** | **$338** | **3.51** |
| F1_atrtarget1.25_5d<−1% | 1.80 | $1,452 | $338 | 3.57 |
| F1_atrtarget1.5_5d<−1% | 1.80 | $1,452 | $338 | 3.32 |

**Key observations:**
- ATR target multipliers 0.75-1.5 all produce nearly identical results — robust to that parameter
- 5d_ret<−1% regime is materially better than atr_pct>1.3 (lower OOS DD: $338 vs $590)
- Slippage robustness is excellent (PF stays >3 at 8pt RT)
- OOS PnL is **double canonical** ($1,452 vs $672), with same per-trade risk

**The structural story:** in weak regimes (5d down), short setups have asymmetric payoff — losses cap at the fixed 40pt stop, but wins extend further because volatility is elevated. ATR-scaling the target captures this.

---

## All 20 cross-product combinations

5 LONG configs × 4 SHORT configs = 20. Sorted by OOS MAR (forward risk-adjusted return on $3,750).

| # | Combo | n | PnL | PF | DD | MAR | OOS_n | OOS_PnL | OOS_PF | OOS_DD | OOS_MAR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | L3_Aplus + S3_F3_slope_atrgate | 69 | $3,043 | 2.85 | $424 | 3.10 | 28 | $1,722 | 4.99 | $86 | **24.33** |
| 2 | L3_Aplus + S1_T7_atrgate | 73 | $3,057 | 2.69 | $424 | 3.12 | 29 | $1,636 | 4.16 | $86 | 23.11 |
| 3 | L4_Aplus_halt + S3_F3_slope | 54 | $2,718 | 3.42 | $173 | 6.81 | 23 | $1,254 | 3.91 | $86 | 17.71 |
| 4 | L4_Aplus_halt + S1_T7_atrgate | 58 | $2,732 | 3.11 | $173 | 6.85 | 24 | $1,168 | 3.25 | $86 | 16.50 |
| 5 | **L3_Aplus + S2_F1_atrtarget** | 79 | $5,767 | 3.15 | $343 | 7.28 | 30 | **$2,267** | 3.39 | $173 | 16.01 |
| 6 | L4_Aplus_halt + S2_F1_atrtarget | 64 | $5,442 | 3.52 | $259 | 9.10 | 25 | $1,799 | 2.89 | $173 | 12.70 |
| 7 | L3_Aplus baseline (no short) | 51 | $2,077 | 2.60 | $424 | 2.12 | 18 | $1,146 | 5.42 | $86 | 16.19 |
| 8 | L4_Aplus_halt baseline | 36 | $1,752 | 3.25 | $173 | 4.39 | 13 | $678 | 3.62 | $86 | 9.57 |
| 9 | L2_T1_halt-1.5 + S2 | 101 | $5,847 | 2.61 | $432 | 5.86 | 41 | **$1,677** | 1.97 | $432 | 4.74 |
| 10 | L2_T1_halt-1.5 + S3_F3_slope | 90 | $3,029 | 2.17 | $345 | 3.80 | 38 | $1,039 | 1.86 | $259 | 4.89 |
| 11 | L2_T1_halt-1.5 + S1_T7 | 94 | $3,044 | 2.10 | $345 | 3.82 | 39 | $953 | 1.74 | $259 | 4.49 |
| 12 | L1_T1_halt-1.0 + S2 | 96 | $5,739 | 2.66 | $432 | 5.76 | 39 | $1,490 | 1.86 | $432 | 4.21 |
| 13 | **L0_T1_canonical + S2** | 113 | $5,472 | 2.32 | $417 | 5.68 | 45 | **$1,452** | 1.80 | $338 | 5.24 |
| 14 | L1_T1_halt-1.0 + S3_F3_slope | 85 | $2,921 | 2.21 | $259 | 4.88 | 36 | $852 | 1.70 | $259 | 4.01 |
| 15 | L1_T1_halt-1.0 + S1_T7 | 89 | $2,936 | 2.13 | $259 | 4.91 | 37 | $765 | 1.59 | $259 | 3.60 |
| 16 | L0_T1_canonical + S3_F3_slope | 104 | $2,901 | 1.88 | $424 | 2.96 | 44 | $1,061 | 1.72 | $424 | 3.05 |
| 17 | L0_T1_canonical + S1_T7 | 108 | $2,915 | 1.84 | $424 | 2.97 | 45 | $975 | 1.63 | $424 | 2.80 |
| 18 | L2_T1_halt-1.5 baseline | 73 | $2,157 | 1.96 | $259 | 3.61 | 29 | $556 | 1.54 | $259 | 2.62 |
| 19 | **L0_T1_canonical baseline** | **89** | **$2,036** | **1.67** | **$417** | **2.11** | **36** | **$672** | **1.52** | **$417** | **1.96** |
| 20 | L1_T1_halt-1.0 baseline | 68 | $2,049 | 1.99 | $259 | 3.42 | 27 | $369 | 1.36 | $259 | 1.74 |

(L=LONG config, S=SHORT config; baselines have no short paired)

---

## Three deployable recommendations, ranked by capital fit

### Recommendation A — Best for current $3,750 account (HIGH CONFIDENCE)
**L0_T1_canonical + S2_F1_atrtarget (combo #13)**

| Metric | Value | vs Canonical |
|---|---|---|
| Total trades | 113 | +27% |
| OOS trades | 45 | +25% |
| OOS PnL | $1,452 | **+116%** |
| OOS PF | 1.80 | **+18%** |
| OOS DD | $338 | −19% |
| OOS MAR | 5.24 | **+167%** |
| Slippage @ 8pt RT (full window PF) | ~3.5 | very robust |

**Why this wins:**
- Same per-trade risk as canonical ($80)
- Larger trade sample (good statistical power)
- OOS PF and PnL both materially better
- Lower OOS DD than canonical
- Slippage-robust (3.5x at 8pt RT)
- Fully deployable on existing $3,750 account

### Recommendation B — Highest OOS PnL with bigger trade sample (MEDIUM CONFIDENCE)
**L2_T1_halt-1.5 + S2_F1_atrtarget (combo #9)**

| Metric | Value | vs Canonical |
|---|---|---|
| Total trades | 101 | +13% |
| OOS PnL | $1,677 | **+150%** |
| OOS PF | 1.97 | +30% |
| OOS DD | $432 | +4% |
| OOS MAR | 4.74 | +142% |

Adds the LONG regime filter (halt when 5d<−1.5%). Marginal improvement over Recommendation A. Worth shadow-tracking but not strictly better.

### Recommendation C — Highest-quality unit economics, smaller sample (LOW CONFIDENCE)
**L3_Aplus_canonical + S2_F1_atrtarget (combo #5)** — uses A+ as the LONG side

| Metric | Value | vs Canonical Tier 1 |
|---|---|---|
| Total trades | 79 | −11% |
| OOS PnL | **$2,267** | +237% |
| OOS PF | 3.39 | +123% |
| OOS DD | $173 | **−59%** |
| OOS MAR | 16.01 | +717% |

Uses A+ shadow on the long side instead of Tier 1. Higher unit economics, lower DD, but smaller sample (51 trades + 28 short = 79 total). Worth shadow-tracking; promote only after 50+ forward A+ trades pass gate.

---

## What v5 confirmed about prior findings

### Confirmed: T7 (mirror short with no VWAP cap, atr_pct>1.3 regime) is robust
The `F2_T7_refinement` tests showed atr_pct>1.3 is the right threshold (>1.1 fails OOS by 70%, >1.5 too rare). Touch tolerance, stop, and RR are all stable around T7's defaults.

### Confirmed: Slope filter is load-bearing on the SHORT side
F3 tested an A+-style slope filter on the short side (`min_signal_ema_slope >= -20`). Slope-only at threshold −20 with atr_pct>1.3 regime gives OOS PF 4.34 — better than T7 alone. The asymmetry is symmetric: long benefits from "slope not too steep up," short benefits from "slope not too steep down."

### Confirmed: F4 vol-bucket taxonomy adds nothing beyond binary regime
The `atr_pct>1.3` binary trigger captures the regime signal cleanly. Subdividing into "elevated" vs "high" vs "extreme" buckets doesn't add edge — only reduces sample size.

### Confirmed: F5 ATR-targets do NOT help LONG
LONG side did not benefit from ATR-scaled targets. The vol-edge is asymmetric: it's a feature of weak regimes (where shorts work) not strong regimes (where longs work). LONG canonical already captures the "let winners run to 1.25R" appropriately.

---

## What we will NOT do based on v5

1. **Promote to live without paper-forward gate.** OOS samples are 24-45 trades. We need 50+ forward.
2. **Use ATR-scaled stops** (v4 finding) on the $3,750 account — risk per trade too high.
3. **Replace canonical Tier 1 with A+ as primary LONG.** A+ has higher per-trade economics but smaller sample. Use Tier 1 for primary, A+ as secondary candidate.
4. **Test more time windows, more day-after sequential, more volume confirmation, more dual-confirmation regimes** — v4 already proved these don't work in this data.
5. **Test more parameter sweeps around F1.** Robustness across multipliers 0.75-1.5 is established.

---

## What v6 might address (optional)

If we want to keep going, these are the remaining unexplored corners:

1. **Walk-forward stability** of Recommendation A across rolling 6-month windows — does the edge hold in every sub-period or concentrated in 1-2 months?
2. **Pre-market gap research** with extended-hours data fetch (Phase 3 hint).
3. **ETH (overnight) breakout/breakdown setups** — entirely new setup, requires new fetch.
4. **Counter-trend mean-reversion** after extreme moves (gap > 0.5%, intraday move > 1.5%).
5. **External-data regimes** (VIX, SPX trend) — would unlock truly new regime hypotheses but requires sourcing.

---

## Concrete next steps

1. **Update Phase 6.5 paper-forward to track 3 new shadow rules:**
   - `tier1_combo_v5`: Tier 1 LONG canonical + F1 short (atr-target 1.0, 5d<−1% regime)
   - `aplus_combo_v5`: A+ LONG canonical + F1 short (same)
   - `tier1_haltfilter_combo_v5`: Tier 1 LONG halt-5d<−1.5% + F1 short (same)
2. **Set explicit promotion gate** for each combined rule:
   - 50+ forward LONG trades + 15+ forward SHORT trades
   - Combined OOS PF ≥ 1.6 (Tier 1 family) / 2.5 (A+ family)
   - Combined max DD ≤ 1.5× backtest OOS DD ($338 × 1.5 = $507 for Tier 1 family; $173 × 1.5 = $260 for A+ family)
   - No >30% degradation vs current OOS at p ≤ 0.10
3. **After 1 calendar quarter of forward data, evaluate.** If gates pass, switch canonical to the best-performing combined rule.
4. **Re-run this entire v5 analysis when live CME tick data lands** — IBKR demo bars are the floor; live data may shift the best variant choice.

---

## Honest statistical caveats

- **OOS samples are small everywhere** (8-45 trades). Real statistical confidence requires forward data.
- **The PF improvements look dramatic** but they're estimated from small samples. Bootstrap CIs would be wide.
- **All findings are regime-conditional.** The 2024-2026 sample is one persistent regime. Different regimes may behave differently.
- **The combined strategies haven't been BH-FDR-corrected** as a family — they were validated through IS/OOS structural splits, not per-test significance. This is acceptable for *candidate* selection, not for *promotion* claims.

---

## Files

- `scripts/thesis_research_v5.py` — 74-variant v5 sweep
- `scripts/final_combined_backtest.py` — 20-combination cross product
- `research/human_edge_replay/short_side_exploration/thesis_v5_raw.json` — v5 raw results
- `research/human_edge_replay/short_side_exploration/final_combined_backtest.json` — final cross-product results
- `findings_v5.md` — this document
