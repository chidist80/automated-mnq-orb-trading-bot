# Independent Verification - 2026-04-26

Verdict: **FAIL_METHODOLOGY for v3-v5 regime/ATR findings.**

The deterministic reproduction work mostly passes. The research code and data produce the handoff's headline tables. However, the regime and ATR logic used by the v3-v5 short-side work is not executable as written because it uses same-day information that is not known during the 10:00-11:00 ET entry window. After replacing those features with causal, as-of-prior-day equivalents, the v5 ATR-target recommendation no longer improves the canonical Tier 1 OOS baseline.

This does **not** invalidate the frozen canonical Tier 1 baseline, causal entry mechanics, append safety, or the A+ shadow result. It does invalidate the recommendation to wire the v5 ATR-target short rule as a promoted shadow rule without a full causal rerun.

## Environment And Reproduction

- Current HEAD: `2b9c83a1cd7a5bd7302cf14ba54689bf9147361a`, not the handoff's `4f8fd1e`. `2b9c83a` is the handoff-doc commit on top of `4f8fd1e`; I verified against the current tree.
- Working tree: not clean because of unrelated untracked `.claude` files. No tracked research files were dirty after reproduction.
- Data file: `data/mnq_1m.parquet`, size `5,089,106`, SHA-256 `ed5db2d90d8371c44cbe212d6d16ad117537c9e185a6592a6ca108ece643d8ce`.
- Python: `3.12.7`; pandas `2.3.3`; numpy `1.26.4`.
- Unit tests: `python -m pytest -p no:capture tests/unit -q` -> `28 passed in 16.06s`.

## F1-F11 Reproduction

| Finding | Result | Notes |
|---|---:|---|
| F1 frozen canonical baseline | PASS | `tier1: trades=89 pnl=2035.74 pf=1.673662`; `a_plus_shadow: trades=51 pnl=2076.66 pf=2.603475`. |
| F2 zero same-bar hindsight | PASS | Default replay: `Total trades: 95`; `Causal-invariant violations: 0`. |
| F3 append-mode safety | PASS | 3 unit tests passed; empirical duplicate/conflict audit preserved file hash. |
| F4 demo data floor | PASS_WITH_CONCERN | Zero-volume by year reproduced: 2023 `88.83%`, 2024 `4.50%`, 2025 `1.61%`, 2026 `1.27%`. 2024+ is usable for research, but still demo data. |
| F5 MC ruin | PASS | `tier1@$3,750 ruin_prob=3.1300%`; `a_plus_shadow@$3,750 ruin_prob=0.0600%`. |
| F6 v2 negative result | PASS | All 10 pre-registered short hypotheses fail Stage 1; `SURVIVORS: 0`. |
| F7 v3 LONG filter OOS worse | PASS_REPRODUCTION_ONLY | Reported numbers reproduce, but the `5d_ret` filter is non-causal. |
| F8 v3 combined improves OOS | PASS_REPRODUCTION_ONLY | Reported numbers reproduce, but the short regime filters are non-causal. |
| F9 v4 T7 robustness | PASS_REPRODUCTION_WITH_NOTE | Expected grep lines reproduce. The prose claim says `n=21`; actual standalone T7 is `n=22`, PnL `$980.52`, IS/OOS PF `2.8927` each. |
| F10 v5 ATR-target headline | PASS_REPRODUCTION_ONLY | Exact line reproduces: `113 / $5,472 / PF 2.32 / OOS $1,452 / OOS PF 1.80`. Methodology fails. |
| F11 v5 top-5 by OOS MAR | PASS_REPRODUCTION_ONLY | Exact ordering and numbers reproduce. Methodology fails for short/regime components. |

## Critical Methodology Failure

The regime maps in both v3 and v4/v5 use current-day close/high/low:

- `scripts/exhaustive_research.py:241-248`: `ret_5d`, `ret_10d`, `sma20_slope`, `atr_20`, and `atr_pct` are computed from unshifted same-day daily series.
- `scripts/thesis_research_v4.py:112-118`: same issue in the v4/v5 shared regime builder.
- `scripts/thesis_research_v5.py:99-103`: ATR targets use `atr_series[ctx.date]`; because `atr_series` came from same-day full daily range, the target is also contaminated.

For a trade signaled between 10:00 and 11:00 ET, the strategy cannot know:

- the current day's RTH close,
- the current day's full high-low range,
- whether the current day will be `atr_pct>1.3`,
- an ATR value that includes the current day's full range.

`gap_pct` and `prev_day_change` are causal enough for this setup because the current RTH open and previous closes are known before the trade window. The problem is the return/slope/ATR family.

## Causal Rerun

I reran the v5 final cross-product with a causal map:

- `5d_ret`/`10d_ret`/`sma20_slope`: computed from closes through day `t-1`.
- ATR target: prior 20-day average range through day `t-1`.
- `atr_pct`: previous day's range divided by ATR known as of day `t-1`.
- `gap_pct`: current RTH open versus prior close.

Key result:

| Combo | Original leaked OOS | Causal OOS |
|---|---:|---:|
| Canonical Tier 1 only | `$672`, PF `1.52`, DD `$417` | unchanged |
| `L0_T1_canonical + S2_F1_atrtarget_5dgate` | `$1,452`, PF `1.80`, DD `$338` | `$555`, PF `1.31`, DD `$661` |
| S2 short standalone | `n=28`, `$3,690`, PF `3.67` | `n=24`, `$924`, PF `1.63` |

The causal S2 addition is worse than canonical Tier 1 alone on OOS PnL, PF, and drawdown.

## Methodology Challenge Results

Sample size / bootstrap:

- Leaked `L0+S2` OOS PF bootstrap 95% CI: `[0.868, 3.498]`.
- Causal `L0+S2` OOS PF bootstrap 95% CI: `[0.643, 2.500]`.
- Both lower bounds include sub-1 PF. The headline is not statistically stable.

ATR-target leverage critique:

- Leaked S2 mean ATR target: `334.20` points. Constant target at that mean produced nearly identical results: short `n=28`, `$3,631`, PF `3.63`; `L0+const` OOS `$1,500`, PF `1.83`.
- Causal S2 mean ATR target: `352.63` points. Constant target again matched: short `n=24`, `$915`, PF `1.62`; `L0+const` OOS `$555`, PF `1.31`.
- Conclusion: ATR adaptation itself is not adding edge. The effect is mostly the large target distance.

Regime attribution:

- Leaked 5d regime split for no-regime ATR-target short: true-regime `n=28`, PF `3.67`; false-regime `n=59`, PF `0.60`; random-subset p `0.001`.
- Causal 5d regime split: true-regime `n=24`, PF `1.63`; false-regime `n=63`, PF `1.25`; random-subset p `0.336`.
- Conclusion: the strong regime effect is largely an artifact of day-t leakage.

Walk-forward stability:

- Leaked `L0+S2` has weak/negative rolling 3-month windows despite the strong full headline: examples include `2024-08..2024-10` PF `1.05`, `2025-04..2025-06` PF `0.81`, `2025-09..2025-11` PF `0.90`.
- Causal `L0+S2` is weaker: `2024-07..2024-09` PF `0.81`, `2024-08..2024-10` PF `0.60`, `2025-04..2025-06` PF `0.65`, `2025-10..2025-12` PF `0.54`, `2025-11..2026-01` PF `0.54`.

Collision sensitivity:

- For causal `L0+S2`, same-day collision policy changes OOS materially:
  - earliest/long-wins: `$555`, PF `1.31`, DD `$661`
  - short-wins: `$1,126`, PF `1.65`, DD `$511`
  - skip-both: `$814`, PF `1.52`, DD `$410`
- This is only 4 collisions, but the tiebreaker is load-bearing enough that any combined rule must pre-register it.

Data quality:

- 2024+ edge-time zero-volume rates are much better than 2023 but not perfect. At `10:00`, zero-volume rates were 2024 `1.59%`, 2025 `1.60%`, 2026 `1.27%`.
- This supports using 2024+ for research, not production confidence. Live data validation remains required.

## Additional Causal Edge Probe

I ran a smaller, executable-input-only short search:

- 336 variants.
- Inputs restricted to prior closes, prior day range/ATR, current RTH gap, opening-range state, and signal-bar filters.
- IS screen: full `n>=20`, IS `n>=12`, IS PF `>=1.25`, IS avg `>0`.
- BH-FDR on IS-screened variants: `0` passers.

Best non-endorsed leads:

- No-regime prior-ATR target, fixed 40 stop, 1.0x prior ATR target: full `n=87`, `$1,886`, PF `1.35`; OOS `n=34`, `$999`, PF `1.47`, DD `$604`; OOS PF at 8pt slip `1.40`.
- Causal 5d weak + 30 stop / 80 target: OOS `n=10`, `$217`, PF `1.54`; too small.

These are not execution candidates. At most, the no-regime prior-ATR target can be a v6 shadow hypothesis after pre-registration, because it failed BH-FDR and has high drawdown.

## What Still Looks Real

The A+ long filter remains the cleanest edge candidate because it is based on signal-time/opening-range features, not the leaked daily regime map:

- A+ full: `n=51`, `$2,076.66`, PF `2.6035`, DD `$424`.
- A+ OOS: `n=18`, `$1,145.88`, PF `5.4239`, DD `$86`.
- A+ OOS PF bootstrap lower bound was about `2.17`, but `n=18` is still too small for promotion.

The correct near-term plan is therefore:

1. Keep canonical Tier 1 strict unchanged as the only execution candidate.
2. Keep A+ as the primary shadow/promotion candidate.
3. Do not wire leaked v5 S2 as a recommended shadow rule without relabeling it as invalid research.
4. Build v6 around causal daily features only, with tests that fail if day `t` close/high/low enters pre-entry regimes.
5. Shadow any short-side work only after pre-registration; do not execute until forward data clears the promotion gate.

## Final Sign-Off Checklist

- [x] Data hash matches exactly.
- [x] Unit tests pass.
- [x] F1-F6 reproduce.
- [x] F7-F8 reproduce, but methodology fails.
- [x] F9 reproduces expected lines; prose has a small `n` mismatch.
- [x] F10-F11 reproduce, but methodology fails.
- [x] Data-quality concern checked and remains a live-data risk.
- [x] Bootstrap CIs computed.
- [x] Rolling 3-month stability checked.
- [x] Regime causality checked and failed.
- [x] ATR-target control run and failed to show adaptive edge.
- [x] Regime-effect attribution run and failed under causal features.
- [x] Final verdict: **FAIL_METHODOLOGY**.
