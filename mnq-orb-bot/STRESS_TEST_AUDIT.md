# MNQ ORB Bot — Stress Test Audit
## Critical Bugs, Edge Cases, and Missing Components

---

## 🔴 CRITICAL BUGS (will produce wrong results)

### 1. or_detector.py:98 — Percentile calculation is broken
```python
percentile = float(np.percentile(lookback_ors, [25, 75])[0])  # dummy
```
This line calculates a percentile of the lookback but assigns it to an unused variable.
The actual `pct_rank` calculation on line 99 is correct, but this dead line is confusing
and the variable `percentile` shadows the field name. More critically, the percentile
returned in the OpeningRange is `pct_rank` (0-100 scale), which is fine, but the
"dummy" comment suggests this was placeholder code left in.

**Fix**: Remove the dead line.

### 2. backtester.py:301 — Risk check AFTER trade simulation, not before
```python
trade = self._simulate_inverse_orb(...)
if trade and self._check_risk_limits(daily_pnl, daily_trades, daily_losses):
```
The inverse ORB trade is fully simulated THEN risk-checked. This wastes computation
but more importantly, the risk check should happen BEFORE the trade, not after.
The ORB breakout (line 311-313) correctly checks risk before entry. Inconsistent.

**Fix**: Move risk check before `_simulate_inverse_orb()`.

### 3. backtester.py:586-644 — Stop and target on same bar: evaluation order bias
```python
if bar["low"] <= current_stop:    # Stop checked first
    ...
if trade.target_price and bar["high"] >= trade.target_price:  # Target second
```
On a bar where BOTH stop and target are hit (wide range bar), the stop always wins
because it's checked first. In reality, we don't know which was hit first within a
15-minute bar. This creates a systematic pessimistic bias.

**Fix**: Check which level is closer to the open of the bar, or randomize 50/50,
or assume worst case (stop) for conservative estimation with a flag to toggle.

### 4. indicators.py:25-28 — RSI division by zero
```python
rs = avg_gain / avg_loss
return 100 - (100 / (1 + rs))
```
If `avg_loss` is 0 (price only went up during the period), this produces `inf`.
Result: RSI becomes NaN, which will cause confluence checks to silently fail.

**Fix**: Handle zero division: `if avg_loss == 0: return 100.0`

### 5. data_loader.py:106 — Validation rejects legitimate data
```python
assert not df.isnull().any().any(), f"NaN values found"
```
After adding indicators (EMA, RSI), the first N bars will have NaN values due to
the lookback period. This assertion runs on raw data, but if someone loads data
and then calls `_validate` again after adding indicators, it blows up. More
importantly, the `add_all_indicators` function doesn't handle the NaN warmup period.

**Fix**: The backtester should drop the first `max(ema_period, rsi_period)` bars
after adding indicators, or fill NaN with neutral defaults.

### 6. backtester.py:_walk_forward_exit — Doesn't respect trading window end time
The config has `trading_end: "12:00"` but `_walk_forward_exit` will hold positions
all the way to end of day. If you enter at 11:45 and the strategy says stop trading
at 12:00, the position stays open until EOD (16:00). The `flatten_time` at 15:45
is also not enforced.

**Fix**: Add time-based exit logic to `_walk_forward_exit`.

---

## 🟡 LOGIC GAPS (will produce misleading results)

### 7. No slippage modeling
Every entry and exit is at the exact computed price. In reality:
- Breakout entries get filled 1-3 ticks worse due to momentum
- Stop fills get 1-5 ticks of slippage on fast moves
- Retest entries are better (limit order territory)
A backtest without slippage will overstate performance by ~10-20%.

**Fix**: Add configurable slippage per entry/exit type.

### 8. No commission modeling
IBKR charges ~$0.25/contract/side for MNQ. At 1 contract, that's $0.50 round trip.
Small per trade, but over 200+ trades it's $100+ which affects profit factor.

**Fix**: Add commission deduction to P&L calculation.

### 9. Retest detection is too generous
```python
if bar["low"] <= retest_level + tolerance_points:
```
With 5-point tolerance on a 15m bar, this will trigger on almost any pullback.
The retest should require:
- The bar actually REACHES the retest level (low touches it)
- Not just "comes within 5 points"
- A rejection candle pattern (close in upper half for longs)

**Fix**: Tighten retest detection: require `bar["low"] <= retest_level` (actual touch)
AND `bar["close"] > retest_level` AND close in upper 60% of candle range.

### 10. No gap handling
MNQ can gap overnight. If the market opens significantly above/below the previous
close, the first 15m bar may be entirely outside the prior day's range. The OR
detection doesn't account for gap-up/gap-down days, which have different probability
distributions for ORB success.

**Fix**: Calculate gap size (open vs prior close) and add as a filter/context variable.

### 11. No regime awareness
The strategy applies the same parameters in trending markets, choppy markets, and
crash environments. ORB performance varies dramatically by regime:
- Strong trend days: ORB + continuation works well
- Range-bound days: Inverse ORB works well, standard ORB gets chopped
- High-VIX crash days: Everything is noise

**Fix**: Add VIX/ATR-based regime filter. At minimum, track 20-day ATR of MNQ
and flag high-volatility regimes where parameters should shift.

### 12. EMA continuation can fire on the same bar as a retest
If a retest and an EMA touch happen on the same bar, both trades could trigger.
The EMA continuation finder doesn't check if an ORB trade is already active.

**Fix**: Track active position state; don't signal continuation while in an ORB trade.

### 13. Inverse ORB and standard ORB can conflict
On a wide OR day where the breakout fails, the code tries an inverse ORB (lines
292-308), THEN also tries a standard ORB retest (lines 310-338). Both could fire
on the same day, potentially in opposite directions.

**Fix**: If inverse ORB triggers, skip standard ORB for the day (exclusive routing).

### 14. No partial profit taking modeled
Config mentions `partial_trail` but the implementation (line 607) just exits at
target with a comment "partial_trail simplified." This means the backtest doesn't
reflect the actual execution plan (take 50% at 1:1, trail remainder).

**Fix**: Implement proper partial exit logic — simulate two "sub-trades" of 0.5
contracts each with different exit logic.

### 15. Equity curve doesn't track intraday drawdown
`max_drawdown` is calculated from daily closing equity only. The actual max drawdown
could be much worse intraday (e.g., down $800 mid-day before recovering to -$200).
This matters because IBKR margin calls happen intraday.

**Fix**: Build bar-by-bar equity curve tracking unrealized P&L of open positions.

---

## 🔵 MISSING COMPONENTS (needed before live trading)

### 16. No walk-forward validation
The backtest runs on the entire dataset. Without out-of-sample testing, any
parameter optimization will be curve-fitted. This is the #1 reason backtests
don't translate to live performance.

**Need**: `walk_forward.py` — train on rolling 8-month window, test on next 4 months.

### 17. No parameter sweep engine
Can't optimize parameters without testing combinations systematically.

**Need**: `param_sweep.py` — grid search across key dimensions, output heatmap.

### 18. No Monte Carlo simulation
Can't assess drawdown probability without randomized trade reordering.

**Need**: `monte_carlo.py` — reshuffle trade sequence 10,000x, calculate drawdown
distribution and probability of ruin at various account sizes.

### 19. No report/visualization output
`results.summary()` prints text only. No equity curve chart, no drawdown chart,
no trade distribution by time/day, no parameter heatmaps.

**Need**: `report.py` — generate matplotlib/HTML report.

### 20. No test suite
Zero unit tests. The OR detection, retest logic, indicator calculations, and
risk manager all need tests with known inputs/outputs.

**Need**: Tests for every core function with edge cases.

### 21. No data acquisition script
The backtester expects a CSV but there's no code to actually get historical
MNQ data. Need a script that downloads from IBKR or Databento.

### 22. No Supabase schema
`db/migrations/` is empty. Need tables for: trades, signals, daily_pnl,
equity_curve, system_state, research_posts, extracted_strategies.

### 23. No account-level drawdown tracking in backtest
The risk config has `circuit_breakers.account_drawdown_pct: 0.20` but the
backtester doesn't enforce it. A 20% drawdown on $2,500 is $500 — if the
backtest hits this, it should halt, but currently it keeps trading.

---

## ⚪ MINOR ISSUES

### 24. or_detector.py:94 — Doji candle not handled
```python
direction = "bullish" if close_price > open_price else "bearish"
```
A doji (close == open) is classified as "bearish." Should be "neutral."

### 25. Hardcoded `ema_9` column name
Throughout the backtester, `bar.get("ema_9")` is hardcoded. If the config
changes the EMA period to 12, the column name won't match.

### 26. Reddit scraper rate limiting
PRAW has rate limits but `scrape_all()` doesn't implement any backoff.
Could get throttled on large scrapes.

### 27. Claude extractor doesn't handle API rate limits
No retry logic, no backoff. Will fail silently on rate limit errors.

### 28. No .gitignore
Missing standard Python gitignore. `__pycache__`, `.env`, `data/` should
all be excluded.

---

## Priority Fix Order

1. ~~**Critical bugs** (#1-6) — Fix before any backtest run~~ ✅ FIXED
2. ~~**Slippage + commissions** (#7-8) — Fix before trusting any results~~ ✅ FIXED
3. ~~**Retest logic** (#9) — Directly impacts signal quality~~ ⚠️ Noted, tightening deferred to param sweep
4. ~~**Walk-forward + Monte Carlo** (#16-18) — Required for validation~~ ✅ BUILT
5. ~~**Conflict resolution** (#12-13) — Prevents contradictory signals~~ ✅ FIXED
6. ~~**Tests** (#20) — Catch regressions~~ ✅ BUILT
7. ~~**Everything else** — Before Phase 2 (live)~~ ✅ Schema, report, param sweep built

## Remaining Items (Phase 2 blockers)
- #10: Gap handling — add during backtest calibration
- #11: Regime awareness — add after initial results (VIX/ATR filter)
- #14: Partial profit taking — implement if backtest shows trail > fixed target
- #21: Data acquisition script — need to acquire MNQ data before first run
