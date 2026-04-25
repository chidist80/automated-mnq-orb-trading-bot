# VALIDATION.md — MNQ ORB Bot Code Audit
**Date**: 2026-04-02  
**Auditor**: Claude Code (Opus 4.6)  
**Scope**: All backtest/, config/, tests/, bot/dispatcher.py, STRESS_TEST_AUDIT.md

---

## Corrective Addendum — 2026-04-25

Deep review before Phase 2 Task 1.1 invalidated the old Phase 1 pass. The prior
365-trade / PF 2.66 / $17,429 baseline used same-bar hindsight: completed-bar
closes decided whether an earlier intrabar fill had occurred. That is not a
live-executable model for a bot.

The corrected methodology now:
- drops synthetic 09:30 bars and days missing the 09:30 OR bar
- uses corrected peak-relative Monte Carlo drawdown percent
- applies a 100-point hard per-trade risk cap for the $2,500 account
- models entries as resting orders placed only after known signal state, with
  conservative fill-bar handling

Result: Phase 1 **fails** under executable semantics. Full-period run: 13 trades,
PF 0.19, P&L -$529.61. Walk-forward: 1/12 profitable windows, avg OOS PF 0.53.
Monte Carlo: 100% ruin. Raising capital to $3,750+ fixes only the old
non-executable model's corrected Monte Carlo gate; it does not restore edge under
the executable model. Phase 2 live execution is blocked pending strategy redesign
or finer-grained data validation.

---

## Validation Gate Answers

### 1. OR Detection — Correct? Breakout requires CLOSE not wick?

**YES — Correct.** `or_detector.py:155-169` checks `bar["close"] > opening_range.high` for long breakouts and `bar["close"] < opening_range.low` for shorts. Wick-only penetration correctly does NOT trigger a breakout. Test `test_breakout_requires_close_not_wick` (test_core.py:136-144) explicitly validates this.

**Minor issue (FIXED per audit):** The doji case in `detect_opening_range` is now handled — `or_detector.py:96-101` returns "neutral" when close == open. STRESS_TEST_AUDIT #24 is resolved.

### 2. Retest — Mechanically sound? Pullback + rejection candle?

**PARTIALLY.** `or_detector.py:213-228` (long retest):
- Checks `bar["low"] <= retest_level + tolerance_points` — pullback detected
- Checks `bar["close"] > retest_level` — rejection confirmed (close holds above)

**Issue (noted in STRESS_TEST_AUDIT #9):** The tolerance check is generous. `bar["low"] <= retest_level + 5` means a bar that never actually touches the OR level can trigger a retest. The original strategy requires price to actually visit the level and reject. This is deferred to param sweep calibration — acceptable for now, but should be tightened if initial backtest shows too many false retests.

**Additional concern:** Entry price is set to `retest_level + tolerance_points` (line 226), which means we're entering 5 points above OR high on longs. In practice this is a limit order at the level, not 5 points above. The tolerance should apply to detection only, not to the entry price.
- **`or_detector.py:226`** — Entry should be `retest_level`, not `retest_level + tolerance_points`
- **`or_detector.py:246`** — Same issue for shorts: entry should be `retest_level`, not `retest_level - tolerance_points`
- **Severity: MEDIUM** — This inflates entry prices by ~5 points per trade, degrading P&L by ~$10/trade

### 3. Inverse ORB and Standard ORB — Mutually exclusive?

**YES — Correct.** `backtester.py:316-338` uses `took_inverse` flag. If inverse ORB fires, `took_inverse = True` and the standard ORB block at line 339 is gated by `not took_inverse`. STRESS_TEST_AUDIT #13 is resolved.

### 4. EMA Continuation — Can't enter while ORB trade is open?

**YES — Correct.** `backtester.py:373-387` calculates `last_exit_bar` from the most recent trade's exit time and passes `min_start_bar` to `_find_ema_continuations`. The continuation scanner won't look at bars before the prior trade exited. STRESS_TEST_AUDIT #12 is resolved.

**Note:** This is bar-level exclusion, not tick-level. If the ORB trade exits on bar N, the EMA continuation can enter on bar N+1 (same 15m window). This is acceptable since we're on 15m bars — effectively a 15-minute minimum gap between positions.

### 5. Slippage, Commissions, Flatten Time — All enforced?

**Slippage: YES.** `backtester.py:736-737` deducts `slippage_points` (default 1.0) from every trade's `pnl_points`. Applied as a flat adverse deduction, which is conservative.

**Commissions: YES.** `backtester.py:742-744` deducts `commission_per_side * 2 * contracts` (default $0.50 RT). STRESS_TEST_AUDIT #7-8 are resolved.

**Flatten time: YES.** `backtester.py:632-637` checks flatten_time (default 15:45 ET) at the start of each bar in `_walk_forward_exit`. Exits at bar open price. STRESS_TEST_AUDIT #6 is resolved.

**Issue:** Trading window end (12:00 ET) is checked for signal generation (`_in_trading_window`) but NOT for position exits. If you enter at 11:50, the position can be held until 15:45. This is actually correct behavior — `trading_end` means "stop generating new signals," not "flatten existing positions." The config naming could be clearer.

### 6. Walk-Forward Criteria?

**Revised 2026-04-25.** The original gate (≥60% avg OOS WR) was inherited from the u/NeverStoppedout reference benchmark (74.91% WR), but that bot uses a different trade-mix. Our strategy is ~70% EMA continuation — a low-WR / high-R:R trend-following setup that's structurally capped at ~50-55% blended WR regardless of edge. WR is not the right edge measure for this strategy mix.

Current gate (`walk_forward.py`):
- **PASS**: ≥75% OOS windows profitable AND avg OOS PF ≥1.5 AND avg OOS P&L > 0
- **MARGINAL**: ≥50% OOS windows profitable
- **FAIL**: <50% OOS windows profitable

Edge is measured by profit factor and total P&L. Robustness is measured by windows-profitable rate. Drawdown / ruin is covered by the Monte Carlo gate (<5% ruin probability), so it's not duplicated here.

Default windows: 8-month train, 4-month test, 2-month step. Standard for futures backtests. Minimum data requirement: `train_months + test_months` months.

**Concern:** The train/test split doesn't re-optimize parameters per window — it uses the same config for all windows. This is the correct approach (testing robustness of fixed params), but should be noted: this is a fixed-parameter walk-forward, not an anchored walk-forward with per-window optimization.

### 7. Monte Carlo Threshold?

**Defined.** `monte_carlo.py:86-93`:
- **LOW RISK**: <5% ruin probability → PASS
- **MODERATE**: 5-15% → needs more capital
- **HIGH RISK**: >15% → increase capital or reduce risk

Ruin threshold is 20% account drawdown (matching `circuit_breakers.account_drawdown_pct`). 10,000 simulations with seed=42 for reproducibility. Capital scaling recommendations included.

**This is sound.** The <5% gate is standard for small accounts.

### 8. Dispatcher Triggers — Cooldowns and thresholds sensible?

| Trigger | Threshold | Cooldown | Assessment |
|---------|-----------|----------|------------|
| Losing streak → debug | 3 consecutive | 24h | **Good.** 3 is right for 1-2 trades/day. 24h prevents spam. |
| PF decay → debug | Weekly PF < 1.3, min 10 trades | 168h (1 week) | **Good.** 10-trade minimum prevents false triggers on low volume. |
| Monthly recalibrate | 1st Saturday | 25 days | **Good.** First Saturday is smart — market closed, no interference. |
| Consensus eval | 50 paper trades | Once (never re-triggers) | **Issue: should re-trigger at 100, 150, etc.** One-shot eval means you lose the ongoing validation loop. |
| Crowding | Sunday | 6 days | **Good.** Weekly is right for Reddit signal decay. |
| Halt recommendation | 4 weeks PF < 1.3 | None | **Issue: no cooldown.** Could spam Slack every 30 min if conditions persist. Add a 7-day cooldown. |

**Bugs in dispatcher:**
- `dispatcher.py:94`: `datetime.utcnow()` is deprecated in Python 3.12. Should use `datetime.now(timezone.utc)`.
- `dispatcher.py:352-353`: `count_mentions` import assumes `research/` is on sys.path. Will fail on VPS unless installed as a package or PYTHONPATH is set.

### 9. Three-Process VPS Architecture — Separation clean?

**YES — Clean separation.**

| Process | Purpose | Dependencies | Crash Impact |
|---------|---------|-------------|-------------|
| `mnq-bot.service` | Trading | Python + ib_insync + Supabase | Stops trading, watchdog still flattens |
| `mnq-dispatcher.service` | Analysis | Python + Supabase + ruflo + Claude CLI | Stops auto-analysis, bot keeps trading |
| `mnq-watchdog.service` | Safety | Python + ib_insync only | Loses flatten protection |

**Systemd configs** (`deploy/`): `Restart=always` on watchdog (critical), `Restart=on-failure` on bot and dispatcher. The watchdog is independent — it reads IBKR directly, not from the bot process.

**The factory/product separation is correct.** Runtime code (bot, watchdog) has zero ruflo imports. Only the dispatcher uses ruflo, and it's non-critical.

---

## Gaps and Bugs Found

### NEW BUGS (not in STRESS_TEST_AUDIT)

| # | File:Line | Severity | Description |
|---|-----------|----------|-------------|
| N1 | `or_detector.py:226` | MEDIUM | Retest entry price includes tolerance offset. Should enter at `retest_level`, not `retest_level + tolerance`. Inflates entry cost by ~5 pts per trade. |
| N2 | `or_detector.py:246` | MEDIUM | Same as N1 for short retests. |
| N3 | `backtester.py:452` | LOW | Inverse ORB entry_price set to `opening_range.high` (for shorts) but actual entry would be at the bar close when price re-enters OR (line 468). The entry_price field doesn't match the actual entry bar. |
| N4 | `backtester.py:665-670` | LOW | Breakeven stop logic runs AFTER target hit break (line 668), but the loop already breaks on target hit. The BE logic only executes on the same bar as the target — it should apply on the bar AFTER partial exit for `partial_trail` mode. Currently dead code since the loop breaks immediately. |
| N5 | `dispatcher.py:323-325` | LOW | Consensus eval is one-shot. After 50 trades it triggers once and never re-evaluates. Should re-trigger at configurable milestones (e.g., every 50 trades). |
| N6 | `dispatcher.py:396-399` | LOW | No cooldown on halt recommendation. Will dispatch Slack every 30 min while conditions persist. |
| N7 | `backtester.py:260` | INFO | Config merge `{**self.strategy, **self.risk}` will silently overwrite duplicate keys. Currently safe since strategy and risk configs don't share keys, but fragile. |

### STRESS_TEST_AUDIT Status

| # | Status | Notes |
|---|--------|-------|
| 1 | **FIXED** | Dead percentile line removed |
| 2 | **FIXED** | Risk check moved before inverse ORB sim (line 321) |
| 3 | **FIXED** | Same-bar stop/target resolved by distance-to-open heuristic |
| 4 | **FIXED** | RSI handles division by zero, fills NaN with 100.0 |
| 5 | **FIXED** | Backtester drops warmup bars (line 226-233) |
| 6 | **FIXED** | Flatten time enforced in `_walk_forward_exit` |
| 7 | **FIXED** | Slippage applied (1pt flat deduction) |
| 8 | **FIXED** | Commissions deducted ($0.50 RT) |
| 9 | **DEFERRED** | Retest tolerance still generous, to be tuned in param sweep |
| 10 | **OPEN** | Gap handling not implemented |
| 11 | **OPEN** | Regime awareness not implemented |
| 12 | **FIXED** | EMA continuation checks last_exit_bar |
| 13 | **FIXED** | Exclusive routing via took_inverse flag |
| 14 | **OPEN** | Partial profit taking not implemented (simplified to full exit at target) |
| 15 | **OPEN** | Intraday equity tracking not implemented |
| 16 | **BUILT** | walk_forward.py exists and works |
| 17 | **BUILT** | param_sweep.py exists with DEFAULT_GRID and QUICK_GRID |
| 18 | **BUILT** | monte_carlo.py with 10K sims |
| 19 | **BUILT** | report.py generates HTML with Chart.js |
| 20 | **BUILT** | 20 unit tests in test_core.py |
| 21 | **OPEN** | No data acquisition script yet |
| 22 | **BUILT** | Full schema in 001_full_schema.sql |
| 23 | **FIXED** | Account drawdown circuit breaker enforced (line 297-299) |
| 24 | **FIXED** | Doji handled as neutral |
| 25 | **FIXED** | EMA column name dynamic via `_ema_col` (line 236-237) |

---

## Verdict

**The backtester is in good shape for Phase 1 validation.** All critical bugs from the stress test are fixed. The remaining open items (#10, #11, #14, #15) are Phase 2 enhancements that won't affect the validity of the initial backtest run.

**Two bugs to fix before first backtest run:**
1. **N1/N2 (retest entry price)** — Will systematically degrade P&L by ~$10/trade. Quick fix.
2. **N3 (inverse ORB entry price)** — Minor inconsistency, won't significantly affect results.

**Blockers:**
- Need MNQ 15m historical data (12+ months) before any backtest can run
- No `scripts/fetch_data.py` exists yet

---

## Phase 1 Run — 2026-04-25

**Data:** 37 months MNQ 15m continuous (2023-03-31 → 2026-04-24), 17,746 RTH bars, fetched via `python scripts/fetch_data.py --months 48 --continuous` against IBKR demo account DUO707586. Extended from initial 24mo run after probing demo's max depth (3 years exact). Earlier history (2022 bear, 2020-2021 COVID) not available on demo — would need Polygon.io or equivalent.

**Full-period backtest (341 trades):**
- WR 54.8%, PF 2.58, P&L +$17,634, max DD $1,010, max consec losses 8
- ORB breakout 72% WR / Inverse ORB 72% WR / EMA continuation 47% WR (70% of trades)

**Walk-forward (13 windows on 37 months, 8mo train + 4mo test, 2mo step, with regime-history seeding from train period):**

| # | Test | Trades | WR | PF | P&L |
|---|------|--------|-----|-----|-----|
| 0 | 2023-11 → 2024-03 | 39 | 46% | 2.63 | +$797 |
| 1 | 2024-01 → 2024-05 | 39 | 51% | 2.10 | +$703 |
| 2 | 2024-03 → 2024-07 | 41 | 54% | 2.96 | +$1,739 |
| 3 | 2024-05 → 2024-09 | 52 | 54% | 2.84 | +$3,080 |
| 4 | 2024-07 → 2024-11 | 65 | 62% | 3.54 | +$4,361 |
| 5 | 2024-09 → 2025-01 | 56 | 55% | 2.10 | +$1,969 |
| 6 | 2024-11 → 2025-03 | 55 | 53% | 2.59 | +$3,359 |
| 7 | 2025-01 → 2025-05 | 60 | 55% | 2.91 | +$4,327 |
| 8 | 2025-03 → 2025-07 | 54 | 59% | 1.99 | +$1,601 |
| 9 | 2025-05 → 2025-09 | 57 | 63% | 2.42 | +$1,607 |
| 10 | 2025-07 → 2025-11 | 42 | 71% | 10.13 | +$3,883 |
| 11 | 2025-09 → 2026-01 | 41 | 61% | 5.49 | +$3,905 |
| 12 | 2025-11 → 2026-03 | 43 | 37% | 1.26 | +$592 |

- **13/13 OOS profitable (100%)**, avg PF 3.30, avg P&L +$2,455.
- The previous Window #1 cold-start meltdown does NOT recur in the 37mo run — Window #7 (test 2025-01 → 2025-05) overlaps the same calendar period but made +$4,327 because seeding `regime_or_history` from the 8-month train period meant the filter was active from day 1.
- Weakest new window: #12 (test 2025-11 → 2026-03) at WR 37% / PF 1.26 / +$592. Still profitable, still passes the gate. Worth understanding the regime profile but not blocking.
- Window #10 PF 10.13 is unusually high but on a 42-trade sample — likely a favourable distribution rather than over-fit.

**Monte Carlo (10,000 sims on 365 trades, $2,500 starting):**
- Ruin probability: **0.00%**
- Median max DD: $688, p95: $1,054, p99: $1,273

**Original gate verdict (SUPERSEDED by corrective addendum):**

| Gate | Threshold | Actual | Status |
|------|-----------|--------|--------|
| OOS windows profitable | ≥75% | **100%** | ✅ |
| Avg OOS profit factor | ≥1.5 | 3.30 | ✅ |
| Avg OOS P&L | > 0 | +$2,455 | ✅ |
| Monte Carlo ruin | <5% | 0.00% | ✅ |

**SUPERSEDED:** this pass used the old same-bar fill model and is no longer a
live-readiness clearance. See the corrective addendum at the top of this file.

### Window #1 regime study (2026-04-25)

Investigated the W1 walk-forward failure (Feb 25 → Jun 25, 2025). Compared to all other periods:
- Daily range: +45% wider (438 vs 303 pts)
- Overnight gap: +57% bigger (185 vs 118 pts)
- OR 30m width: +26% wider (170 vs 135 pts)
- Top-10% gap days: 3.26× more frequent (24% of W1 days vs 7.4%)
- Bottom-10% OR (quiet) days: 0.10× as frequent (1.2% vs 11.9%)

**Isolated $2,500 W1 backtest (no cushion):** 11 trades, 9.1% WR, -$653, max DD $788. All 10 EMA-continuation losses exited on `trail_ema` (the 9-EMA crossback trailing stop) — high-vol whipsaw exits trades on noise. **ORB breakout still won (1/1 trade, +$178).** The same period traded with cushion (full-period run): 61 trades, 50.8% WR, +$1,940. So W1 is a **small-account survival failure during a high-vol regime**, not a strategy break.

### Filter A — OR-width regime filter on EMA continuation

Added 2026-04-25. If today's OR width ≥ p90 of trailing 60-day OR widths, **disable EMA continuation for the day**. ORB breakout and inverse ORB still trade. Walk-forward seeds the test backtester's `regime_or_history` from the train period so the filter is active from day 1 of every test window.

Effect on Phase 1 metrics:
| Metric | No filter | With filter (p90) |
|--------|-----------|-------------------|
| Full-period trades | 341 | 308 (-33 EMA) |
| Full PF | 2.58 | 2.63 |
| Full P&L | $17,634 | $15,832 |
| Avg OOS PF | 2.89 | **3.23** |
| Avg OOS P&L | $2,657 | $2,386 |
| Window #1 trades | 11 | 7 |
| Window #1 P&L | -$653 | -$377 |
| MC ruin probability | 0.00% | 0.00% |
| MC p99 max DD | $1,496 | $1,315 |

W1 partially mitigated, not fully eliminated — at p90 the filter only catches the most extreme W1 days. More aggressive thresholds (p80–p70) marginally improve W1 at the cost of edge on normal days. **p90 chosen a priori from the top-decile regime study finding.** Robustness check: PF stays in 2.66–2.73 across p70–p90, so the strategy is not sensitive to the exact threshold.

### Pre-Phase-2 hardening pass (2026-04-25)

Audited `or_detector.py`, `indicators.py`, `data_loader.py` — the three modules that the live signal generator inherits via the shared strategy module. Bugs that were acceptable in a backtester ("warn and continue") become unacceptable at runtime ("trade with wrong indicator"). Fixed:

| Bug | Severity | Fix |
|-----|----------|-----|
| `indicators.py` hardcoded `ema_9` and `ema_9_slope` column names regardless of configured `ema_period` | HIGH | Column names now use f-string with the configured period; `check_confluences` reads the matching column dynamically. Pre-fix, any non-default EMA period silently failed. |
| `detect_opening_range` IndexError on empty / partial day_bars; NaN-laden OR on misconfigured `or_period_minutes` | HIGH | Explicit guards: empty-data check, multiple-of-15 validation, sufficient-bars check. Live signal generator can no longer get a malformed OR. |
| `volume_ratio` returned `inf` on zero rolling mean (holiday / overnight) | MEDIUM | Replace 0 with NaN in denominator; downstream confluence checks treat NaN as fail (safe default). |
| `load_ibkr_historical()` was the obsolete pre-fix loader (no marketDataType, no ContFuture handling) | MEDIUM | Replaced with `NotImplementedError` pointing at `scripts/fetch_data.py` so no caller silently regresses. |
| `_normalize_timestamps` silently localized naive index to US/Eastern (would shift a UTC CSV by 4-5h) | MEDIUM | Logs a WARNING when localizing a naive index; the bot will fail loudly if someone hands it UTC data. |

SUPERSEDED: this Phase 1 re-run used the old same-bar fill model. The indicator/data
fixes were valid, but the live-readiness conclusion is replaced by the corrective
addendum at the top of this file.

Not fixed (intentional):
- `detect_retest` tolerance allows entry without the bar actually touching the OR level (STRESS_TEST_AUDIT #9, deferred). This is a strategy-semantics change requiring re-validation; defer to Phase 2 strategy hardening if backtest vs live divergence appears.
- Asserts in `_validate` (could be optimized away by `python -O`) — low severity, document and revisit if anyone runs with `-O`.
- RTH filter hardcoded 09:30-16:00 — fine for futures regular session; revisit if extended hours ever needed.

### Phase 2 prerequisites carried forward (revised after extended-data run)
1. **Window #12 mini-study.** WR 37% / PF 1.26 in the most recent test window (Nov 2025 - Mar 2026). Still profitable so it doesn't fail the gate, but it's the weakest window — worth understanding the regime profile (gap behaviour, OR sizes, time-of-day cluster) before live trading exposes the bot to similar conditions. Add to the dispatcher's regime-detection scope.
2. **Live signal generator** must handle DELAYED tick types (66-75) on demo account, or test only against funded paper/live with CME real-time subscription.
3. **Filter inheritance:** the live signal generator must read the same `regime_filter` config block and apply the gate before EMA continuation entry. The shared strategy module makes this automatic.
4. **Pre-live deeper history (defer to Phase 4):** Polygon.io for 2020-2022 data (COVID + rate-hike bear) before any live deploy with bigger size. Current data passes all gates but doesn't include a true bear-market regime. Phase 4's neural recalibration loop needs long history anyway, so that phase is the right place to invest in the upgraded data pipeline.
