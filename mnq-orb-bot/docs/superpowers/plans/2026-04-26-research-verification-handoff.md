# Research Verification Handoff — Independent Agent Audit Pack

> **POST-AUDIT UPDATE 2026-04-26:** This handoff was used by an independent
> verifier whose audit (`research/human_edge_replay/short_side_exploration/INDEPENDENT_VERIFICATION.md`)
> rendered **FAIL_METHODOLOGY** for the v3-v5 regime/ATR findings. A look-ahead
> bug in regime computation (5d_ret / atr_pct / ATR-target used same-day daily
> close/high/low) inflated the OOS metrics. After causal correction, the v5
> headline recommendation does NOT improve over canonical Tier 1 alone.
> The full retraction lives at `research/human_edge_replay/short_side_exploration/findings_v7_RETRACTION.md`.
> The canonical Tier 1 strict and A+ specifications are unaffected. F1-F6 reproduced and stand;
> F7-F11 reproduced but methodology fails. Phase 6.5 should track ONLY canonical Tier 1 + A+.

**Audit target:** All MNQ ORB short-side and combined-strategy research conducted on
`research/human-edge-replay` branch from commits `79533dd` through `4f8fd1e`.
Latest commit at handoff time: `4f8fd1e`.

**Your role:** Independent QA verifier. Do **not** trust prior findings. Treat every
claim in this document as a hypothesis to be empirically verified before
endorsement. The original verifier was the same agent who produced the research, so
adversarial scrutiny by a fresh agent is the entire point of this handoff.

**Honest framing:** The author of this research is Claude Opus 4.7 acting as the
research lead. The author tried to be disciplined (pre-registered hypotheses,
BH-FDR, IS/OOS, slippage stress, decision rules stated before tests run).
Self-disciplined research can still self-deceive. Your job is to catch what was
missed.

---

## 0. Reproducibility prerequisites

### 0.1 Repo state

```bash
cd /Users/tisha/Documents/GitHub/automated-mnq-orb-trading-bot/mnq-orb-bot
git checkout 4f8fd1e   # or whatever the handoff commit is
git status --short     # should be clean (no modifications)
```

### 0.2 Data file

```bash
ls -la data/mnq_1m.parquet
sha256sum data/mnq_1m.parquet   # or shasum -a 256 on macOS
```

**Expected:**
- File: `data/mnq_1m.parquet`
- Size: 5,089,106 bytes
- SHA-256: `ed5db2d90d8371c44cbe212d6d16ad117537c9e185a6592a6ca108ece643d8ce`

If the hash differs, **stop**. Either you're on a different data pull, or someone
modified the file. Do not proceed with verification until the hash matches; all
expected metrics in this document depend on this exact file.

### 0.3 Python environment

```bash
python --version    # expect 3.12.x
python -c "import pandas, numpy; print(pandas.__version__, numpy.__version__)"
```

Required: pandas, numpy, scipy (optional but used for t-tests; falls back to a
math.erfc approximation if missing). All work was done with the system Python at
`/Applications/anaconda3/bin/python` (Python 3.12.7).

### 0.4 Repo file checklist

These files must exist (created by the research):

```
backtest/causal_or_retest.py                                         (verified frozen rule mechanics)
scripts/run_causal_paper_forward.py                                  (canonical replay generator)
scripts/diagnose_causal_replay.py                                    (data quality diagnostics)
scripts/check_paper_forward_gate.py                                  (promotion gate)
scripts/mc_ruin_recheck.py                                           (MC ruin compute)
scripts/explore_short_side.py                                        (v1 symmetric short test)
scripts/short_side_hypothesis_tester.py                              (v2 pre-registered 10 hypotheses)
scripts/exhaustive_research.py                                       (v3 134-variant + LONG regime + combined)
scripts/oos_combined_validation.py                                   (v3 IS/OOS for combined)
scripts/thesis_research_v4.py                                        (v4 8-thesis 84-variant sweep)
scripts/thesis_research_v5.py                                        (v5 74-variant followup)
scripts/final_combined_backtest.py                                   (v5 20-combo cross product)
tests/unit/test_causal_or_retest.py                                  (21 unit tests; total suite 28)
research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv (canonical baseline)
research/human_edge_replay/paper_forward/canonical_mc_ruin.json        (MC ruin output)
research/human_edge_replay/short_side_exploration/findings_v[2-5].md  (per-iteration verdicts)
docs/superpowers/plans/2026-04-26-canonical-mode-adr.md              (canonical decision)
docs/superpowers/plans/2026-04-26-mnq-orb-roadmap-to-live.md         (5-phase roadmap)
docs/superpowers/plans/2026-04-26-short-side-research-program.md     (v2 pre-registration)
```

### 0.5 First-touch sanity

Run all unit tests; expect 28 passing:

```bash
python -m pytest -p no:capture tests/unit -q
```

**Expected:** `28 passed in <5s`. If any test fails, stop and investigate. The unit
test suite is the foundation; everything else depends on its correctness.

---

## 1. Findings to verify (ordered by importance)

Each finding has:
- The claim
- The exact reproduction command
- The exact expected output
- A decision rule

If the reproduction matches expected output exactly, the *claim* is reproducible.
Reproducibility ≠ correctness — the claim could still be wrong if the methodology
is flawed. Section 2-4 cover methodological scrutiny.

---

### F1 — Frozen canonical strict 2024+ baseline

**Claim:** Tier 1 LONG strict mode + 2024-01-01 start = 89 trades / $2,035.74 / PF
1.6737. A+ shadow same window = 51 trades / $2,076.66 / PF 2.6035.

**Reproduce:**

```bash
python scripts/run_causal_paper_forward.py --require-full-session-clean \
  --start 2024-01-01 \
  --out /tmp/verify_canonical.csv
```

**Expected stdout (last 5 lines):**

```
Wrote 581 daily rows to /tmp/verify_canonical.csv (regenerate)
Strict full-session clean mode enabled
WARNING: 32 OR-clean rows have full-session quality issues; ...
tier1: trades=89 pnl=2035.74 avg=22.87 win_rate=60.67% pf=1.67
tier2_shadow: trades=140 pnl=3307.40 avg=23.62 win_rate=47.14% pf=1.52
a_plus_shadow: trades=51 pnl=2076.66 avg=40.72 win_rate=70.59% pf=2.60
```

**Verify full-precision PF:**

```bash
python -c "
import sys; sys.path.insert(0, '.')
import pandas as pd
from backtest.causal_or_retest import summarize_decisions
log = pd.read_csv('/tmp/verify_canonical.csv')
for p in ['tier1', 'tier2_shadow', 'a_plus_shadow']:
    s = summarize_decisions(log, p)
    print(f'{p}: pf={s[\"pf\"]:.6f}')
"
```

**Expected:**
```
tier1: pf=1.673662
tier2_shadow: pf=1.517658
a_plus_shadow: pf=2.603475
```

**Decision rule:** Match to the exact integer trade count and to the cent on PnL.
PF should match to 4 decimal places. Any deviation is a critical issue — either
the data has changed (data hash mismatch from §0.2) or the code has changed.

---

### F2 — Causal mechanics: zero same-bar hindsight

**Claim:** Across all 95 default Tier 1 trades, entry timestamp is exactly
signal_ts + 1 minute, raw entry price equals next bar open, signal bar low ≤
OR_high + 5 (touch tolerance), signal bar close > OR_high (breakout
confirmation). All 95 trades satisfy these invariants.

**Reproduce:**

```bash
python scripts/run_causal_paper_forward.py
python -c "
import pandas as pd
log = pd.read_csv('research/human_edge_replay/paper_forward/paper_forward_log.csv')
elig = log[log['tier1_eligible']==True]
violations = 0
for _, r in elig.iterrows():
    sig = pd.Timestamp(r['tier1_signal_ts'])
    ent = pd.Timestamp(r['tier1_entry_ts'])
    if (ent - sig).total_seconds() != 60: violations += 1
    if r['tier1_signal_bar_low'] > r['or_high'] + 5.0: violations += 1
    if r['tier1_signal_bar_close'] <= r['or_high']: violations += 1
    if abs(r['tier1_raw_entry_price'] - r['tier1_entry_bar_open']) > 1e-6: violations += 1
print(f'Total trades: {len(elig)}')
print(f'Causal-invariant violations: {violations}')
"
```

**Expected:** `Total trades: 95` and `Causal-invariant violations: 0`.

**Decision rule:** Any violation is a causal-mechanics defect. Reject all
downstream findings until fixed.

---

### F3 — Append-mode safety

**Claim:** `append_journal()` (in `scripts/run_causal_paper_forward.py`) is safe:
identical duplicates skip, conflicting duplicates raise without writing, schema
mismatch raises, NaN equality is handled correctly.

**Reproduce:** Run the unit test cases that cover this directly:

```bash
python -m pytest -p no:capture \
  tests/unit/test_causal_or_retest.py::test_append_journal_skips_duplicate_date_rule_version \
  tests/unit/test_causal_or_retest.py::test_append_journal_conflicting_duplicate_raises_and_leaves_file_unchanged \
  tests/unit/test_causal_or_retest.py::test_append_journal_schema_mismatch_still_raises \
  -v
```

**Expected:** 3 tests passing.

**Additional empirical audit** (mirrors the original audit):

```bash
python -c "
import sys, hashlib, shutil
sys.path.insert(0, '.')
from pathlib import Path
import pandas as pd
from scripts.run_causal_paper_forward import append_journal

src = Path('research/human_edge_replay/paper_forward/paper_forward_log.csv')
journal = Path('/tmp/verify_journal.csv')
shutil.copy(src, journal)
before_hash = hashlib.sha256(journal.read_bytes()).hexdigest()

# Identical re-append
incoming = pd.read_csv(src)
combined, skipped, added = append_journal(incoming, journal)
assert skipped == len(incoming) and added == 0, 'identical re-append should skip all'
print(f'OK: identical re-append skipped {skipped} rows, file unchanged: {hashlib.sha256(journal.read_bytes()).hexdigest() == before_hash}')

# Conflict re-append
inc2 = pd.read_csv(src)
target = inc2[inc2['tier1_eligible']==True].index[0]
inc2.loc[target, 'tier1_net_pnl'] = inc2.loc[target, 'tier1_net_pnl'] + 999.99
try:
    append_journal(inc2, journal)
    print('FAIL: conflict should have raised')
except ValueError as e:
    after = hashlib.sha256(journal.read_bytes()).hexdigest()
    print(f'OK: conflict raised, file unchanged: {after == before_hash}')
"
```

**Expected stdout:** Both `OK:` lines, both `True` for file-unchanged.

**Decision rule:** If the file changes after a raise, that's a critical bug — fix
before any forward operations.

---

### F4 — IBKR demo data refetch is bit-identical

**Claim:** Refetching the same window from IBKR demo HMDS produces bit-identical
2024+ data; demo retention is the floor and cannot be improved.

**Verification:** This is harder to reproduce because it requires TWS/Gateway
running. Skip the live refetch; instead audit the prior commit's claims by reading
`mnq-orb-bot/docs/superpowers/plans/2026-04-26-canonical-mode-adr.md` (the
"Empirical confirmation (2026-04-26)" section). The artifacts from the refetch are
in commit `325498b`.

**What you can verify independently:**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/mnq_1m.parquet')
df = df.copy()
df['year'] = df.index.year
zv = df.groupby('year').agg(rows=('volume','size'), zv=('volume', lambda s: int((s==0).sum())))
zv['zv_pct'] = (zv.zv/zv.rows*100).round(2)
print(zv)
"
```

**Expected output:**
```
       rows     zv  zv_pct
year                      
2023  40508  35982   88.83
2024  97785   4397    4.50
2025  97005   1560    1.61
2026  30630    390    1.27
```

**Decision rule:** Confirm 2023 has ≥87% zero-volume bars. This is the structural
basis for the strict + 2024+ canonical decision.

---

### F5 — MC ruin on canonical baseline

**Claim:** Tier 1 strict canonical, $3,750 account, 20% ruin threshold:
ruin probability ≈ 3.13% (20k bootstrap, seed 42). A+ shadow same conditions:
0.06%.

**Reproduce:**

```bash
python scripts/mc_ruin_recheck.py
```

**Expected:**
```
tier1@$3,750: ruin_prob=3.1300%  median_dd=$431.70  ...
tier1@$5,000: ruin_prob=0.6800%  ...
a_plus_shadow@$3,750: ruin_prob=0.0600%  median_dd=$259.02  ...
```

**Decision rule:** Match to within bootstrap noise (≤0.05% absolute deviation).
Larger deviations indicate either a different PnL vector (data drift) or a seed/algorithm change.

---

### F6 — Pre-registered v2 negative result (10 hypotheses, 0 BH-FDR survivors)

**Claim:** Of 10 pre-registered short-side hypotheses tested with the
methodology in `docs/superpowers/plans/2026-04-26-short-side-research-program.md`,
zero cleared Stage 1 (n≥30 AND PF≥1.3 AND avg>0). H6 (regime-conditioned mirror,
5d<−1%) was the only hypothesis with positive expectancy but failed the n
threshold (n=24).

**Reproduce:**

```bash
python scripts/short_side_hypothesis_tester.py 2>&1 | tail -20
```

**Expected output should include:**
```
[STAGE 1 FAIL] H1_failed_long_fade: n=228  pf=0.567  avg=$-17.00
[STAGE 1 FAIL] H2_wide_OR_naive_breakdown: n=36  pf=0.775  avg=$-11.34
[STAGE 1 FAIL] H3_vwap_rejection_emadown: n=88  pf=0.990  avg=$-0.43
[STAGE 1 FAIL] H4_mirror_below_ema: n=59  pf=0.915  avg=$-3.97
[STAGE 1 FAIL] H5_mirror_tight_stop: n=79  pf=0.844  avg=$-5.33
[STAGE 1 FAIL] H6_mirror_weak_5d: n=24  pf=2.170  avg=$33.66
[STAGE 1 FAIL] H7_naive_afternoon: n=244  pf=0.750  avg=$-12.67
[STAGE 1 FAIL] H8_failed_long_rsi_overbought: n=27  pf=0.550  avg=$-17.82
[STAGE 1 FAIL] H9_mirror_early_window: n=82  pf=0.937  avg=$-2.93
[STAGE 1 FAIL] H10_mirror_gap_down: n=30  pf=1.085  avg=$3.66
SURVIVORS: 0
```

**Decision rule:** All 10 should fail Stage 1. H6 should specifically be PF 2.17,
n=24, avg +$33.66 — this is the underpowered positive that motivated v3-v5
research.

---

### F7 — v3 LONG regime filter alone is OOS-worse than canonical

**Claim:** Adding regime filter "halt LONG when 5d_ret<−1%" looks great IS (PF
1.99 vs 1.67) but degrades OOS (PF 1.36 vs 1.52, PnL $369 vs $672). Standalone
LONG regime filter is REJECTED.

**Reproduce:**

```bash
python scripts/oos_combined_validation.py 2>&1 | tail -30
```

**Expected output should include lines matching:**
```
tier1_5dret<-1.0% LONG_filtered: IS_n=41 IS_PnL=1680 IS_PF=2.62 IS_DD=173 OOS_n=27 OOS_PnL=369 OOS_PF=1.36 OOS_DD=259
CANONICAL Tier 1 LONG (no filter) BASELINE: IS_n=53 IS_PnL=1364 IS_PF=1.79 IS_DD=417 OOS_n=36 OOS_PnL=672 OOS_PF=1.52 OOS_DD=417
```

**Decision rule:** OOS PnL of LONG-filtered should be $369 — clearly LESS than
canonical $672. Confirms LONG-filter-alone is rejected.

---

### F8 — v3 Combined LONG-filtered + SHORT regime improves OOS

**Claim:** The combined strategy (LONG halt-5d<−1% + SHORT mirror with atr_pct>1.3
regime) improves OOS PnL from $672 to $758, OOS PF from 1.52 to 1.63, and reduces
DD from $417 to $259. The improvement is modest but real.

**Reproduce:** Same `oos_combined_validation.py` output. Look for:

```
+ short_atr>1.3 COMBINED IS_n=52 IS_PnL=2170 IS_PF=2.68 IS_DD=173 OOS_n=35 OOS_PnL=758 OOS_PF=1.63 OOS_DD=259 collisions=1
```

**Decision rule:** The combined strategy must improve OOS metrics versus
LONG-filtered-alone (otherwise the SHORT side adds nothing). Confirm OOS PnL ≥
$700.

---

### F9 — v4 thesis sweep — T7 (relaxed VWAP cap on regime short) is robust

**Claim:** The variant "mirror short, normal OR, 10-11 ET, atr_pct>1.3 regime,
40pt stop, 50pt target, NO VWAP cap" produces n=21, IS PF 2.89, OOS PF 2.89 (zero
degradation), OOS PnL $975, pf@8pt slip = 2.71.

**Reproduce:**

```bash
python scripts/thesis_research_v4.py 2>&1 | grep "T7_short_mirror_vwapnone_atr"
```

**Expected output should include lines:**
```
[PASS] T7_short_mirror_vwapnone_atr  IS pf=2.89  OOS pf=2.89  ...
[PASS] T7_short_mirror_vwapnone_atr  pf@5pt=2.89  pf@8pt=2.71
```

**Decision rule:** Confirm zero IS/OOS degradation (both PFs equal 2.89).

---

### F10 — v5 ATR-target finding (the headline)

**Claim:** Mirror short with 40pt FIXED stop and 1.0×ATR_20 target, on 5d_ret<−1%
regime, combined with canonical Tier 1 LONG, produces:
- Total: n=113, $5,472, PF 2.32, DD $417
- OOS: n=45, $1,452, PF 1.80, DD $338, MAR 5.24
- pf@8pt slippage ≈ 3.5

This is the recommended new shadow rule.

**Reproduce:**

```bash
python scripts/final_combined_backtest.py 2>&1 | grep "L0_T1_canonical+S2"
```

**Expected output line:**
```
L0_T1_canonical+S2_F1_atrtarget_5dgate     113     5472   2.32     417  5.68     45     1452   1.80     338    5.24
```

**Decision rule:** Match all numbers exactly. This combination is the v5
recommendation; if it doesn't reproduce, the recommendation is null.

---

### F11 — v5 cross-product top-5 by OOS MAR

**Claim:** Top 5 combinations by OOS MAR are:

| Rank | Combo | OOS PF | OOS PnL | OOS DD | OOS MAR |
|---|---|---|---|---|---|
| 1 | L3_Aplus + S3_F3_slope | 4.99 | $1,722 | $86 | 24.33 |
| 2 | L3_Aplus + S1_T7_atrgate | 4.16 | $1,636 | $86 | 23.11 |
| 3 | L4_Aplus_halt + S3_F3_slope | 3.91 | $1,254 | $86 | 17.71 |
| 4 | L4_Aplus_halt + S1_T7_atrgate | 3.25 | $1,168 | $86 | 16.50 |
| 5 | L3_Aplus + S0 (baseline) | 5.42 | $1,146 | $86 | 16.19 |

**Reproduce:**

```bash
python scripts/final_combined_backtest.py 2>&1 | grep -A 6 "TOP 5 by OOS MAR"
```

**Decision rule:** All 5 combos should appear in this exact order with these
exact numbers.

---

## 2. Methodological scrutiny — your hardest job

The numbers reproducing means the *code is consistent*. It does NOT mean the
*methodology is sound*. Here is what to challenge specifically.

### 2.1 Data-quality ceiling

- 2023 is 88% zero-volume on demo. Is the strategy's 2024+ "edge" actually a
  function of demo-feed quirks that wouldn't appear on live tick data?
- Demo retention only goes back ~Sept 2023 (MNQM4 first qualified contract). If
  IBKR fixed something about its demo HMDS feed in late 2023, the entire 2024+
  sample could be on subtly different data than will be available live.
- **Test:** Examine the bar volumes at session edges (09:30 vs 10:00 vs 15:55) on
  random days. If volumes show suspicious patterns (e.g., always-zero at certain
  times), demo data is structurally unreliable.

### 2.2 Sample sizes

OOS samples for the v5 best findings are n=8-45 trades. That's underpowered for
strong claims. Specific concerns:
- The "best v5 short" has n_oos=15-20 trades depending on configuration. A
  bootstrap CI on PF 1.80 with that sample is roughly [1.0, 3.0] — wide enough
  that the OOS improvement could be entirely sampling noise.
- BH-FDR was applied to the 134-variant sweep (v3) and the per-thesis groupings
  (v4, v5). But the *combined portfolio backtests* (v3 Phase E, v5 cross-product)
  were NOT correction-controlled. Picking the best of 20 combinations is itself a
  multiple-comparison problem.
- **Test:** Compute bootstrap 95% CIs for OOS PF on each "winning" combination.
  If the lower bound includes 1.0, the finding is not statistically distinguishable
  from random.

### 2.3 Walk-forward stability

Single IS/OOS split at 2025-06-30 is the simplest robustness test. It can hide
edge concentration in 1-2 months. Specifically:
- The v5 "best" might have the entire OOS PnL come from one or two trades during
  a single market event (e.g., Aug 2025 vol spike).
- **Test:** Run rolling 3-month windows on the v5 recommended combo. Compute PF
  per window. If most windows are PF<1.3 with 1-2 outliers, the edge is fragile.

### 2.4 Same-day overlap and collision logic

The combined portfolio uses "earliest entry_ts wins" for same-day collisions.
This is a defensible choice but it's a choice. Alternatives:
- LONG always wins (long-bias)
- Higher-conviction wins (whichever rule has the larger expected value)
- Skip both
- **Test:** Re-run the v5 recommended combo with each tiebreaker. If PF varies
  by more than 10% across tiebreakers, the collision logic is load-bearing and
  the 1-collision result is not robust.

### 2.5 Regime definition leak

The regime filters use 5-day or 20-day return computed from the SAME data the
strategy trades on. This is non-causal at the edges (e.g., a 5d return computed
at end-of-day t-1 uses bars from days t-6 through t-1, which is fine — but make
sure the implementation isn't accidentally using day t).
- **Test:** Read `build_regime_map` in `scripts/exhaustive_research.py`. Verify
  that the regime value for day t uses ONLY data through day t-1 close.
- **Specifically check:** lines computing `ret_5d`, `ret_10d`, `gap_pct`,
  `atr_pct`. Each should use shifted series.

### 2.6 ATR computation timing

The ATR series in `thesis_research_v5.py` is computed once per day from daily
range. The ATR-target multiplier uses the ATR value for the *current* day. Verify
this is causal:
- For a trade signaled at 10:38 ET on day t, the ATR_20 used should be the
  trailing 20-day average of daily ranges *as of day t-1*, NOT including day t's
  range.
- **Test:** Read `build_regime_map`'s ATR computation. Confirm the series is
  shifted appropriately or otherwise causal.

### 2.7 The "ATR-stop = leverage scaling" critique

v4 found ATR-stop variants with massive PF improvements; v5 properly diagnosed
this as a position-sizing artifact and tested ATR-targets with fixed stops
instead. The v5 ATR-target finding is supposed to be the "real" effect.
- **Test:** Construct a control variant where the target is a *constant* equal
  to the average value of `1.0 × ATR_20` on the days the rule fires, but does
  not adapt per-trade. If the constant-target version has similar PF to the
  ATR-target version, then the per-trade ATR adaptation is not actually adding
  edge — what works is just "use a bigger target than 50pt."

### 2.8 The "regime-conditioned shorts work" thesis

The strongest empirical claim is that mirror shorts work specifically when
regime is weak (5d<−1%, atr_pct>1.3). Verify this is REGIME effect, not just
"more days = more chances":
- **Test:** Compute the per-day expectancy of the SAME short rule conditional
  on the regime being TRUE vs FALSE. If the FALSE-regime expectancy is also
  positive (or much closer to TRUE-regime than expected), the regime filter is
  decoration not edge.
- **Test:** Bootstrap p-value for "regime-conditioned PF > unconditional PF" —
  is the gap statistically distinguishable from a random subset of equal size?

---

## 3. Adversarial scrutiny — questions to ask of every finding

For each numbered finding above, answer:

1. **Reproduction:** Did you reproduce it exactly? If not, what differed?
2. **Data dependence:** Does the finding rely on the specific PnL of any single
   high-impact trade? (Compute "leave-one-out" PF — drop the single largest
   winner and recompute. If PF drops by >20%, the finding is fragile.)
3. **Slippage realism:** The simulation uses fixed RT slippage (default 5pt).
   Real slippage on demo bars is unmeasured. If real slippage is 8-12pt during
   high-vol regimes (likely!), does the finding survive? (See `pf@8pt slip`
   reports for v4-v5 survivors.)
4. **Survivor bias:** This research tested ~300 variants total across all
   iterations (v2: 10, v3: 134, v4: 84, v5: 74, plus 20 cross-product). Even
   with within-iteration BH-FDR, the cross-iteration multiple comparison
   problem is large. What is your prior that any individual "winning" combination
   is real edge vs noise?
5. **Forward applicability:** Demo data is the basis for everything. Live tick
   fills will differ. Is the recommended shadow-tracking plan sufficient to
   catch a forward-time edge collapse?

---

## 4. Specific reproduction command sequence

Run this from repo root in this exact order. Each section should produce the
expected output. If any deviates, stop and investigate.

```bash
# §1 — repo state
git rev-parse HEAD
# Expected: 4f8fd1ea... (or whatever the handoff commit is)

git status --short
# Expected: clean working tree (no modifications)

# §2 — data integrity
sha256sum data/mnq_1m.parquet
# Expected: ed5db2d90d8371c44cbe212d6d16ad117537c9e185a6592a6ca108ece643d8ce

# §3 — unit tests
python -m pytest -p no:capture tests/unit -q
# Expected: 28 passed

# §4 — canonical baseline (F1)
python scripts/run_causal_paper_forward.py --require-full-session-clean \
  --start 2024-01-01 --out /tmp/verify_canonical.csv
# Expected: tier1: trades=89 pnl=2035.74 ... pf=1.67
#          a_plus_shadow: trades=51 pnl=2076.66 ... pf=2.60

# §5 — default replay (for F2 causal mechanics)
python scripts/run_causal_paper_forward.py
# Expected: tier1: trades=95 pnl=2186.70 pf=1.68

# §6 — diagnostics (F4 data quality)
python scripts/diagnose_causal_replay.py

# §7 — MC ruin (F5)
python scripts/mc_ruin_recheck.py
# Expected: tier1@$3,750: ruin_prob=3.1300%

# §8 — pre-registered hypothesis test (F6)
python scripts/short_side_hypothesis_tester.py
# Expected: SURVIVORS: 0 (all 10 fail Stage 1)

# §9 — exhaustive 134-variant + LONG regime + combined (F7, F8)
python scripts/exhaustive_research.py

# §10 — IS/OOS for v3 combined (F7, F8)
python scripts/oos_combined_validation.py

# §11 — v4 thesis sweep (F9)
python scripts/thesis_research_v4.py

# §12 — v5 thesis sweep
python scripts/thesis_research_v5.py

# §13 — v5 final cross-product (F10, F11)
python scripts/final_combined_backtest.py
# Expected: L0_T1_canonical+S2_F1_atrtarget_5dgate ... 113 5472 2.32 ... 1452 1.80
```

---

## 5. What to do when you find something wrong

If reproduction fails:
1. **Stop.** Do not proceed with downstream verification.
2. Capture exact deltas (your output vs expected). Include hashes, exit codes.
3. Investigate root cause: data file changed? Code changed? Environment changed?
   Random seed leaked into a "deterministic" computation?
4. Document in a new file `research/human_edge_replay/short_side_exploration/VERIFIER_FINDINGS.md`
   with timestamp, divergence details, root cause.

If methodology is sound but you find a different conclusion:
1. State the conclusion plainly with evidence.
2. Frame as "the original analysis claimed X; my analysis indicates Y because
   Z." Do not soften with "perhaps" or "might."
3. Specify what the prior research should have done differently.

If methodology is unsound but conclusions happen to be right:
1. State the methodological flaw explicitly. The claim being right by accident
   is still claim-by-accident.
2. Recommend the correct analysis. Re-run if feasible.

---

## 6. Sign-off checklist for the verifier

Before declaring "verified," confirm in writing:

- [ ] Repo state matches §0.1 (commit, clean tree)
- [ ] Data hash matches §0.2 exactly
- [ ] Unit tests all pass (§0.5)
- [ ] F1-F11 reproductions all match expected output to specified precision
- [ ] §2.1 data-quality concern: examined and either confirmed acceptable or
       flagged as concerning
- [ ] §2.2 sample-size concerns: bootstrap CIs computed for v5 recommended combo
- [ ] §2.3 walk-forward: rolling 3-month PF for v5 best combo computed and
       reviewed
- [ ] §2.5 regime causality: confirmed ATR / 5d_ret / gap series are causal
       (no day-t leak)
- [ ] §2.7 ATR-target leverage critique: control test run
- [ ] §2.8 regime-effect critique: per-regime expectancy comparison run
- [ ] §3 adversarial questions answered for at least F1, F8, F10, F11
- [ ] Final verdict written: PASS / PASS_WITH_CONCERNS / FAIL_REPRODUCTION /
       FAIL_METHODOLOGY

The verdict and supporting evidence should be written to
`research/human_edge_replay/short_side_exploration/INDEPENDENT_VERIFICATION.md`
and committed.

---

## 7. Boundary of what is verified vs not verified

**This handoff covers:**
- All Phase 6 research (causal harness, frozen baselines, short-side exploration v1-v5)
- All canonical decisions (mode, capital, regime treatment)
- All recommended shadow rules

**This handoff does NOT cover:**
- Production code (`bot/execution/**`, watchdog, risk_manager, signal_generator) — these are Tier 3 and have not been touched by this research
- Live data feed (still pending IBKR live verification)
- Forward execution behavior (no forward data yet)
- Phase 6.5 paper-forward operations infrastructure (not yet built)

If verification passes, the recommended action is:
- Update Phase 6.5 implementation plan to wire the 3 v5 shadow rules into the
  daily paper-forward harness
- Begin Phase 6.5 cron immediately (zero financial risk, generates required
  forward evidence)
- Do NOT change canonical execution rules until 50+ forward trades clear the
  promotion gate per ADR

If verification fails:
- Halt all forward planning
- Investigate the root cause
- Do not spend further engineering time on Phase 6.5+ until the research
  foundation is solid

---

## 8. Files to read for full context (in priority order)

1. `docs/superpowers/plans/2026-04-26-mnq-orb-roadmap-to-live.md` — strategic context, 5-phase roadmap
2. `docs/superpowers/plans/2026-04-26-canonical-mode-adr.md` — canonical decision and rationale
3. `research/human_edge_replay/short_side_exploration/findings_v5.md` — most recent findings
4. `research/human_edge_replay/short_side_exploration/findings_v4.md` — penultimate findings, includes negative results
5. `research/human_edge_replay/short_side_exploration/findings_v3.md` — combined-strategy origins
6. `research/human_edge_replay/short_side_exploration/findings_v2.md` — pre-registered negative result
7. `docs/superpowers/plans/2026-04-26-short-side-research-program.md` — v2 pre-registration document
8. `mnq-orb-bot/CLAUDE.md` — project rules, especially Tier 3 paths and verification requirements
9. `backtest/causal_or_retest.py` — the verified backtest module (28 unit tests)

Read in order. Each subsequent file assumes context from the prior.

---

## 9. Final note from the original research lead

I am not unbiased. I produced this research and I want it to be right. The
purpose of this handoff is to put the work in front of someone whose job is to
find what I missed. Please be hostile. Specifically:

- I am most worried about §2.2 (sample size) and §2.7 (ATR control test). If
  either reveals a flaw, the v5 finding may not be robust.
- I am modestly worried about §2.1 (demo data quality). The 2023 garbage and
  4.5% zero-vol in 2024 may be hiding live-data behavior we can't see.
- I am least worried about reproducibility (the code is deterministic and the
  data hash is captured). But verify it anyway.

If you find a flaw I missed, please write it as plainly as possible. Trust is
non-recoverable; the right answer is always better than a comfortable one.
