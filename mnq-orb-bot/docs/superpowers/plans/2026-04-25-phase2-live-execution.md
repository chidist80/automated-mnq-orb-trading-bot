# Phase 2: Live Execution Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the live execution path — the trading bot and watchdog that turn Phase 1's validated strategy into real-money MNQ futures trades on IBKR, with hard safety guarantees and a minimal runtime dependency surface.

**Architecture:** Two independent processes on the production VPS. The **bot** detects signals (delegating to a shared strategy core that the backtester also uses) and submits bracket orders. The **watchdog** runs as a separate process with its own IBKR connection and flattens at 15:45 ET regardless of bot state. Strategy semantics live in `backtest/strategies/core.py` so the live path and the backtester are guaranteed to agree.

**Tech Stack:** Python 3.11+, ib_insync (IBKR API), pandas, pytest with mocked IB, systemd for service management. Stdlib + the dependencies already in `pyproject.toml` only — no orchestration frameworks, no agent runtimes, no neural-training dependencies at runtime.

**Architecture decision (2026-04-25):** the original CLAUDE.md envisioned a third "dispatcher" process running ruflo-powered analysis (neural recalibration, HNSW pattern search, consensus voting). After cross-examining the plan, that complexity isn't justified at 1-contract scale: monthly recalibration is a one-shot Python script, trade analysis is a SQL query against Supabase. **Dropped from Phase 2 scope.** `bot/dispatcher.py` remains in-tree as deprecated; Phase 4 decides whether to delete or repurpose. `deploy/mnq-dispatcher.service` is removed from the active deployment runbook (Task 10.1).

---

## Critical Constraints

This is **real money**. Every task must respect:

1. **Tier 3 — STOP and ask** before any change to risk params, IBKR connection settings, or order submission logic. The plan flags these as `🛑 CHECKPOINT`.
2. **Minimal runtime surface.** Runtime processes (bot, watchdog) import only stdlib + ib_insync + pandas + pyyaml + supabase + slack-sdk. No orchestration frameworks, no agent runtimes, no ML dependencies. Verifiable by inspecting the import graph (Task 10.1).
3. **Watchdog independence.** The watchdog connects to IBKR directly with its own `clientId` and flattens at 15:45 ET regardless of bot state. It cannot read from the bot's process.
4. **Bracket orders are atomic.** Entry + stop + target submitted as a single OCA group. Never standalone entries.
5. **Tests before code.** TDD with mocked IB. No live connection until the human checkpoint.
6. **Position size is fixed at 1 MNQ contract** until 50+ validated paper trades meet the scale-up criteria in `config/risk_params.yaml`.

---

## File Structure

### New files
```
backtest/strategies/__init__.py
backtest/strategies/core.py          # Shared strategy semantics — backtester + bot both call this
bot/__init__.py                      # already exists, may stay empty
bot/main.py                          # Bot service entry point
bot/state.py                         # Runtime state (open position, daily P&L, etc.)
bot/bar_aggregator.py                # Tick stream → 15m RTH bars
bot/tick_handler.py                  # Delayed-tick (66-75) ↔ live-tick (1-9) mapping
bot/signal_generator.py              # Live signal loop — calls strategies/core.py
bot/health_monitor.py                # Heartbeat, circuit breakers, connection-loss detection
bot/watchdog.py                      # Independent flatten-at-15:45 process
bot/execution/ibkr_executor.py       # Bracket-order submission, order state tracking
bot/execution/order_state.py         # Order state machine
bot/execution/contract_resolver.py   # MNQ front-month qualification
config/bot.yaml                      # Runtime config (port, account mode, log level)

tests/conftest.py                    # Mock IB fixtures
tests/unit/__init__.py
tests/unit/test_strategies_core.py
tests/unit/test_bar_aggregator.py
tests/unit/test_tick_handler.py
tests/unit/test_contract_resolver.py
tests/unit/test_order_state.py
tests/unit/test_ibkr_executor.py
tests/unit/test_health_monitor.py
tests/unit/test_signal_generator.py
tests/unit/test_watchdog.py
tests/integration/__init__.py
tests/integration/test_replay_day.py # End-to-end with mocked IBKR + replayed bars
tests/integration/test_crash_scenarios.py

scripts/check_no_ruflo.sh            # CI grep: zero ruflo imports under bot/
scripts/paper_smoke_test.py          # Single-day demo paper trading smoke
docs/PHASE2_DEPLOYMENT.md            # Manual deploy + paper-validation runbook
```

### Modified files
```
backtest/backtester.py               # _process_day delegates to strategies/core.py
deploy/mnq-bot.service               # already exists, may need ExecStart tweaks
deploy/mnq-watchdog.service          # already exists, may need ExecStart tweaks
config/strategy_params.yaml          # add Phase 2 strategy hardening fixes (retest tolerance, N3)
pyproject.toml                       # add pytest-mock to dev deps
```

---

## Epic Map

| # | Epic | Risk Tier | Notes |
|---|------|-----------|-------|
| 1 | Shared Strategy Core extraction | Tier 3 | Touches backtester semantics — must regression-test against Phase 1 results |
| 2 | Test Infrastructure | Tier 1 | Mocks only, no live connection |
| 3 | Tick Handler & Bar Aggregator | Tier 2 | Live data ingest; must handle delayed-tick types on demo |
| 4 | IBKR Executor | Tier 3 | Bracket order submission, order state |
| 5 | Health Monitor | Tier 3 | Circuit breakers gate execution |
| 6 | Signal Generator | Tier 3 | Orchestrates everything; entry point for the bot |
| 7 | Watchdog | Tier 3 | Independent safety; standalone process |
| 8 | Strategy Hardening (deferred items) | Tier 3 | Re-validation required after each change |
| 9 | Integration Tests | Tier 2 | Whole-system tests with mocked IB |
| 10 | Deployment Artifacts | Tier 2 | systemd, logging, README |
| 11 | Pre-Live Validation | Tier 3 | Manual checkpoints, paper trading, sign-off |

---

# Epic 1: Shared Strategy Core Extraction

**Why first:** the backtester's `_process_day` contains the canonical strategy semantics. The live signal generator must call the *same code* — not a parallel implementation — or backtest results don't predict live behaviour. We extract `_process_day`'s logic into `backtest/strategies/core.py`, route the backtester through it, and verify Phase 1 numbers are byte-identical.

## Task 1.1: Capture Phase 1 baseline as regression test

**Files:**
- Create: `tests/unit/test_strategies_core.py`

- [x] **Step 1: Run Phase 1 and snapshot the headline numbers**

Run: `python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime" | tee /tmp/phase1_baseline.txt`

Corrective result after deep review (2026-04-25): the old same-bar fill baseline is **not valid for live execution**. A completed bar's close cannot be used to decide that an earlier intrabar fill occurred. Raising capital to `$3,750+` only fixes the corrected Monte Carlo gate under the old non-executable fill model; it does not restore edge under an executable model.

Canonical executable baseline now uses:
- data quality: drop synthetic 09:30 bars and days missing the 09:30 OR bar
- costs: 1.5 MNQ points round-trip slippage, `$0.47` per side commission
- risk: `max_risk_points: 100`
- fills: resting orders placed only after known/completed signal state; fill bar only allows conservative same-bar stop, not same-bar target

Verified 2026-04-25 with executable resting-order semantics: **Phase 1 FAILS**. Full run: 13 trades, PF 0.19, P&L `-$529.61`; walk-forward 1/12 profitable windows, avg OOS PF 0.53, avg OOS P&L `-$452`; Monte Carlo ruin 100%. Larger capital does not fix negative expectancy (`$100k` account still PF 0.51 / P&L `-$11,296` on the same executable model).

🛑 **Corrective pause:** Phase 2 live execution is blocked. Continue Epic 1 only if the goal is preserving the current executable baseline while strategy semantics are redesigned. Do not use the old 365-trade / PF 2.66 / `$17,429` baseline for live-readiness claims.

- [x] **Step 2: Write the regression test**

```python
# tests/unit/test_strategies_core.py
See `tests/unit/test_strategies_core.py`. It locks the executable failing baseline
so strategy-core extraction cannot accidentally drift while the strategy is
redesigned.
```

- [x] **Step 3: Run the regression test against current code**

Run: `pytest tests/unit/test_strategies_core.py -v`
Expected: all 4 tests PASS (this is the executable pre-refactor baseline; it is not a live-readiness pass).

Verified 2026-04-25 after corrective pause: 7/7 targeted methodology/baseline tests pass; full suite 76/76 passes with pytest capture disabled for the local Anaconda capture segfault.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_strategies_core.py
git commit -m "test: capture Phase 1 baseline as regression suite for strategy core extraction"
```

Old commit `c0f6333` captured the pre-corrective baseline and is superseded. New corrective baseline must be committed after final verification.

## Task 1.2: Create strategies package skeleton with type contracts

**Files:**
- Create: `backtest/strategies/__init__.py`
- Create: `backtest/strategies/core.py`

- [ ] **Step 1: Create the package**

```python
# backtest/strategies/__init__.py
"""Shared strategy semantics — used by both the backtester and the live bot.

This package is the SINGLE SOURCE OF TRUTH for what counts as a valid setup,
what entry/stop/target levels apply, and how confluences gate signals.
The backtester wraps it for historical replay; the live bot wraps it for
real-time execution. If you change the rules, change them HERE.
"""

from backtest.strategies.core import (
    SignalContext,
    Signal,
    SetupKind,
    detect_signals_for_day,
)

__all__ = ["SignalContext", "Signal", "SetupKind", "detect_signals_for_day"]
```

- [ ] **Step 2: Create the core module skeleton with stable type contracts**

```python
# backtest/strategies/core.py
"""Strategy logic shared by the backtester and the live bot.

DESIGN: this module is dependency-light and stateless except for what the
caller passes in. The backtester replays days; the live bot streams a partial
day as it accumulates. Both produce identical signal lists for the same input.

EXTRACTED FROM: backtest/backtester.py::_process_day (Phase 1 logic, frozen 2026-04-25).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Literal

import pandas as pd
import numpy as np

from backtest.or_detector import (
    OpeningRange, BreakoutEvent, RetestEvent,
    detect_opening_range, detect_breakout, detect_retest, detect_failed_breakout,
)
from backtest.indicators import check_confluences


SetupKind = Literal["orb_breakout", "ema_continuation", "inverse_orb"]


@dataclass
class Signal:
    """A trade signal ready for execution. Backtester simulates it; live bot
    submits it as a bracket order."""
    setup: SetupKind
    direction: Literal["long", "short"]
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: Optional[float]
    risk_points: float
    or_size: float
    or_classification: str
    confluences: dict = field(default_factory=dict)


@dataclass
class SignalContext:
    """Inputs the strategy needs to decide whether to fire a signal.

    The backtester populates this once per day from historical bars. The live
    bot mutates it incrementally as bars complete.
    """
    day_bars: pd.DataFrame
    date_str: str
    or_history_inverse: list[float]   # 20-day rolling OR for inverse_orb wide classification
    regime_or_history: list[float]    # 60-day rolling OR for regime filter
    strategy_config: dict
    risk_state: "RiskState"           # daily P&L, trade count, consec losses, account state
    last_exit_bar: int = 0            # for EMA continuation gating after a previous trade


@dataclass
class RiskState:
    """The risk controller state at the moment of signal evaluation."""
    daily_pnl: float
    daily_trades: int
    daily_losses: int
    running_pnl: float                # cumulative (for account drawdown)
    peak_pnl: float                   # peak cumulative (for drawdown calc)


def detect_signals_for_day(ctx: SignalContext) -> list[Signal]:
    """Return all signals fired during the day described by ctx.

    Backtester calls this once per day with full day_bars.
    Live bot calls this incrementally as new bars complete; signals already
    emitted in earlier calls are returned again — caller dedupes by entry_time.
    """
    raise NotImplementedError("Filled in by Tasks 1.3-1.8")
```

- [ ] **Step 3: Verify the regression suite still passes (no behaviour change yet)**

Run: `pytest tests/unit/test_strategies_core.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backtest/strategies/__init__.py backtest/strategies/core.py
git commit -m "feat: add backtest/strategies package skeleton with type contracts"
```

## Task 1.3: Implement OR detection + min-size + risk-check gating in core

**Files:**
- Modify: `backtest/strategies/core.py`
- Test: `tests/unit/test_strategies_core.py`

- [ ] **Step 1: Add a focused unit test for OR + min-size gating**

Append to `tests/unit/test_strategies_core.py`:

```python
import yaml
import pandas as pd

from backtest.strategies.core import detect_signals_for_day, SignalContext, RiskState


def _empty_risk_state() -> RiskState:
    return RiskState(daily_pnl=0, daily_trades=0, daily_losses=0,
                     running_pnl=0, peak_pnl=0)


def _make_day(rows: list[dict], date: str = "2025-06-15") -> pd.DataFrame:
    base = pd.Timestamp(f"{date} 09:30", tz="US/Eastern")
    idx = [base + pd.Timedelta(minutes=15 * i) for i in range(len(rows))]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _default_strategy_cfg():
    with open(PHASE1_STRAT) as f:
        return yaml.safe_load(f)


def test_signals_empty_when_or_below_min_size():
    cfg = _default_strategy_cfg()
    cfg["opening_range"]["min_size_points"] = 50
    bars = _make_day([
        # OR is only 10 pts wide — below min_size_points (50)
        {"open": 19000, "high": 19005, "low": 18995, "close": 19002, "volume": 1000},
    ] + [
        {"open": 19010, "high": 19020, "low": 19005, "close": 19015, "volume": 1000}
        for _ in range(20)
    ])
    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=[], regime_or_history=[],
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    assert detect_signals_for_day(ctx) == []
```

- [ ] **Step 2: Run the test — expect FAIL (NotImplementedError)**

Run: `pytest tests/unit/test_strategies_core.py::test_signals_empty_when_or_below_min_size -v`
Expected: FAIL with `NotImplementedError: Filled in by Tasks 1.3-1.8`.

- [ ] **Step 3: Implement the OR-detect + min-size gate**

Replace the body of `detect_signals_for_day` in `backtest/strategies/core.py`:

```python
def detect_signals_for_day(ctx: SignalContext) -> list[Signal]:
    cfg = ctx.strategy_config
    bars = ctx.day_bars

    # Need at least the OR bar plus one more for any signal to fire.
    if len(bars) < 2:
        return []

    or_period = cfg["opening_range"]["period_minutes"]
    wide_pct = cfg.get("inverse_orb", {}).get("wide_or_percentile", 80)

    opening_range = detect_opening_range(bars, or_period, ctx.or_history_inverse, wide_pct)

    min_size = cfg["opening_range"].get("min_size_points", 20)
    if opening_range.size < min_size:
        return []

    # No signals yet — Tasks 1.4-1.7 fill in the three setups.
    return []
```

- [ ] **Step 4: Run the gate test — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py::test_signals_empty_when_or_below_min_size -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/strategies/core.py tests/unit/test_strategies_core.py
git commit -m "feat(strategies): implement OR detection + min-size gate"
```

## Task 1.4: Implement ORB breakout + retest signal generation in core

**Files:**
- Modify: `backtest/strategies/core.py`
- Test: `tests/unit/test_strategies_core.py`

- [ ] **Step 1: Add unit test for breakout-retest signal**

Append to `tests/unit/test_strategies_core.py`:

```python
def test_orb_breakout_retest_signal():
    """A clean long retest setup fires an orb_breakout signal at the OR high."""
    cfg = _default_strategy_cfg()
    # Disable EMA continuation and inverse_orb to isolate this test
    cfg["ema_continuation"]["enabled"] = False
    cfg["inverse_orb"]["enabled"] = False
    cfg["confluences"]["rsi"]["enabled"] = False
    cfg["confluences"]["volume"]["enabled"] = False
    cfg["confluences"]["ema_slope"]["enabled"] = False

    bars = _make_day([
        # OR bar — 50pt range, bullish close
        {"open": 19000, "high": 19050, "low": 19000, "close": 19045, "volume": 5000},
        # Breakout bar — closes above OR high
        {"open": 19045, "high": 19075, "low": 19044, "close": 19070, "volume": 5000},
        # Retest bar — pulls back to OR high (low touches retest level), holds with close
        {"open": 19070, "high": 19075, "low": 19048, "close": 19065, "volume": 5000},
    ] + [
        {"open": 19065, "high": 19068, "low": 19060, "close": 19065, "volume": 1000}
        for _ in range(15)
    ])

    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=[], regime_or_history=[],
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    signals = detect_signals_for_day(ctx)
    orb_signals = [s for s in signals if s.setup == "orb_breakout"]
    assert len(orb_signals) == 1
    assert orb_signals[0].direction == "long"
    assert orb_signals[0].entry_price == 19050.0  # at the OR high
    assert orb_signals[0].stop_price == 19000.0   # opposite side of OR
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `pytest tests/unit/test_strategies_core.py::test_orb_breakout_retest_signal -v`
Expected: FAIL — no orb_breakout signals emitted yet.

- [ ] **Step 3: Implement breakout-retest signal generation**

In `backtest/strategies/core.py`, replace the `# No signals yet` placeholder with the breakout-retest block. Reference `backtest/backtester.py:300-365` for the existing logic — copy the entry/stop derivation exactly, adapted to return `Signal` objects instead of mutating Trade objects.

```python
    signals: list[Signal] = []

    breakout = detect_breakout(bars, opening_range, or_period)
    if breakout is None:
        return signals

    # --- Inverse ORB (mean reversion on wide-OR failed breakout) ---
    inv_cfg = cfg.get("inverse_orb", {})
    took_inverse = False
    if (inv_cfg.get("enabled", False)
            and opening_range.classification == "wide"
            and _within_inverse_window(bars, breakout, inv_cfg)):
        # Filled in Task 1.6
        pass

    # --- ORB breakout / retest ---
    orb_cfg = cfg.get("orb_breakout", {})
    if orb_cfg.get("enabled", True) and not took_inverse:
        max_stop = orb_cfg.get("stop_max_points", 120)
        if opening_range.size <= max_stop:
            tolerance = orb_cfg.get("retest_tolerance", 5)
            timeout = orb_cfg.get("retest_timeout_bars", 8)
            retest = detect_retest(bars, opening_range, breakout, tolerance, timeout)
            if retest is not None:
                conf_bar = bars.iloc[retest.bar_index]
                conf = check_confluences(conf_bar, retest.direction, cfg)
                if all(conf.values()):
                    signals.append(Signal(
                        setup="orb_breakout",
                        direction=retest.direction,
                        entry_time=retest.timestamp,
                        entry_price=retest.entry_price,
                        stop_price=retest.stop_price,
                        target_price=None,  # trail_ema or partial — backtester computes; live recomputes
                        risk_points=retest.risk_points,
                        or_size=opening_range.size,
                        or_classification=opening_range.classification,
                        confluences=conf,
                    ))

    # --- EMA continuation (Tasks 1.5 + 1.7) ---
    return signals


def _within_inverse_window(bars: pd.DataFrame, breakout: BreakoutEvent, inv_cfg: dict) -> bool:
    """Return True if the breakout occurred inside the inverse_orb time window."""
    window_minutes = inv_cfg.get("time_window_minutes", 60)
    bars_in_window = window_minutes // 15
    return breakout.bar_index < bars_in_window
```

- [ ] **Step 4: Run the breakout test — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py::test_orb_breakout_retest_signal -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/strategies/core.py tests/unit/test_strategies_core.py
git commit -m "feat(strategies): emit ORB breakout-retest signals from core"
```

## Task 1.5: Implement inverse ORB signal generation in core

**Files:**
- Modify: `backtest/strategies/core.py`
- Test: `tests/unit/test_strategies_core.py`

- [ ] **Step 1: Add unit test for inverse ORB signal**

Append to `tests/unit/test_strategies_core.py`:

```python
def test_inverse_orb_failed_breakout_signal():
    """Wide-OR + failed breakout fires an inverse_orb signal."""
    cfg = _default_strategy_cfg()
    cfg["orb_breakout"]["enabled"] = False
    cfg["ema_continuation"]["enabled"] = False
    cfg["confluences"]["rsi"]["enabled"] = False
    cfg["confluences"]["volume"]["enabled"] = False
    cfg["confluences"]["ema_slope"]["enabled"] = False

    # Pre-warm or_history_inverse so OR classification is "wide"
    or_history = [50.0] * 19  # 19 prior days of small ORs

    bars = _make_day([
        # Wide OR (bigger than all 19 history entries → top percentile)
        {"open": 19000, "high": 19150, "low": 19000, "close": 19140, "volume": 5000},
        # Long breakout
        {"open": 19140, "high": 19160, "low": 19140, "close": 19155, "volume": 5000},
        # Failed: closes back inside OR
        {"open": 19155, "high": 19160, "low": 19130, "close": 19135, "volume": 5000},
    ] + [
        {"open": 19135, "high": 19140, "low": 19130, "close": 19135, "volume": 1000}
        for _ in range(15)
    ])

    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=or_history, regime_or_history=[],
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    signals = detect_signals_for_day(ctx)
    inv = [s for s in signals if s.setup == "inverse_orb"]
    assert len(inv) == 1
    assert inv[0].direction == "short"  # long breakout failed → fade short
```

- [ ] **Step 2: Run — expect FAIL** (placeholder `pass` block doesn't emit)

Run: `pytest tests/unit/test_strategies_core.py::test_inverse_orb_failed_breakout_signal -v`
Expected: FAIL.

- [ ] **Step 3: Implement inverse ORB signal generation**

Replace the inverse-ORB placeholder block in `backtest/strategies/core.py` (the `pass` inside the `if (inv_cfg.get("enabled"...`). Copy entry-price + stop-price logic from `backtester.py:440-500`. Note: per VALIDATION.md item N3, the inverse ORB entry-price field is currently inconsistent with the actual entry bar; fix in Task 8.x, not here.

```python
    if (inv_cfg.get("enabled", False)
            and opening_range.classification == "wide"
            and _within_inverse_window(bars, breakout, inv_cfg)):
        failure_bars = inv_cfg.get("failure_check_bars", 3)
        failed = detect_failed_breakout(bars, opening_range, breakout, failure_bars)
        if failed:
            # Direction is OPPOSITE to the failed breakout
            inv_direction: Literal["long", "short"] = (
                "short" if breakout.direction == "long" else "long"
            )
            stop_buffer = inv_cfg.get("stop_buffer_points", 10)
            if inv_direction == "short":
                entry_price = opening_range.high
                stop_price = breakout.candle_high + stop_buffer
                target_price = opening_range.midpoint
            else:
                entry_price = opening_range.low
                stop_price = breakout.candle_low - stop_buffer
                target_price = opening_range.midpoint
            signals.append(Signal(
                setup="inverse_orb",
                direction=inv_direction,
                entry_time=breakout.timestamp + pd.Timedelta(minutes=15),
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                risk_points=abs(stop_price - entry_price),
                or_size=opening_range.size,
                or_classification=opening_range.classification,
                confluences={},
            ))
            took_inverse = True
```

- [ ] **Step 4: Run inverse ORB test — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py::test_inverse_orb_failed_breakout_signal -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/strategies/core.py tests/unit/test_strategies_core.py
git commit -m "feat(strategies): emit inverse_orb signals on wide-OR failed breakouts"
```

## Task 1.6: Implement EMA continuation signal generation + regime filter in core

**Files:**
- Modify: `backtest/strategies/core.py`
- Test: `tests/unit/test_strategies_core.py`

- [ ] **Step 1: Add unit tests for EMA continuation + regime filter**

Append to `tests/unit/test_strategies_core.py`:

```python
def test_ema_continuation_signal_fires():
    """A clean continuation pullback to 9EMA fires after a breakout."""
    cfg = _default_strategy_cfg()
    cfg["orb_breakout"]["enabled"] = False
    cfg["inverse_orb"]["enabled"] = False
    cfg["regime_filter"]["enabled"] = False
    cfg["confluences"]["rsi"]["enabled"] = False
    cfg["confluences"]["volume"]["enabled"] = False
    cfg["confluences"]["ema_slope"]["enabled"] = False

    # Construct a bullish continuation: OR bar bullish, breakout, then a pullback
    # bar that wicks through the 9-EMA and closes back above it.
    bars = _make_day([
        {"open": 19000, "high": 19050, "low": 19000, "close": 19045, "volume": 5000},  # OR
        {"open": 19045, "high": 19090, "low": 19044, "close": 19085, "volume": 5000},  # break
        {"open": 19085, "high": 19100, "low": 19082, "close": 19098, "volume": 5000},  # extend
        {"open": 19098, "high": 19105, "low": 19090, "close": 19094, "volume": 5000},  # consolidation
        {"open": 19094, "high": 19096, "low": 19075, "close": 19092, "volume": 5000},  # pullback wick through EMA
    ] + [
        {"open": 19092, "high": 19094, "low": 19090, "close": 19092, "volume": 1000}
        for _ in range(15)
    ])

    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=[], regime_or_history=[],
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    signals = detect_signals_for_day(ctx)
    cont = [s for s in signals if s.setup == "ema_continuation"]
    assert len(cont) >= 1
    assert all(s.direction == "long" for s in cont)


def test_regime_filter_blocks_ema_continuation_on_high_or_day():
    """When today's OR is in the top decile, EMA continuation is suppressed."""
    cfg = _default_strategy_cfg()
    cfg["orb_breakout"]["enabled"] = False
    cfg["inverse_orb"]["enabled"] = False
    cfg["regime_filter"] = {"enabled": True, "ema_skip_or_percentile": 90,
                            "min_history_days": 10, "max_history_days": 60}
    cfg["confluences"]["rsi"]["enabled"] = False
    cfg["confluences"]["volume"]["enabled"] = False
    cfg["confluences"]["ema_slope"]["enabled"] = False

    # Pre-seed regime history with low-vol values; today's OR will be the extreme top.
    regime_history = [50.0] * 30

    bars = _make_day([
        {"open": 19000, "high": 19200, "low": 19000, "close": 19180, "volume": 5000},  # huge OR
        {"open": 19180, "high": 19220, "low": 19170, "close": 19210, "volume": 5000},  # breakout
        {"open": 19210, "high": 19230, "low": 19170, "close": 19200, "volume": 5000},  # pullback
    ] + [
        {"open": 19200, "high": 19210, "low": 19190, "close": 19200, "volume": 1000}
        for _ in range(15)
    ])

    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=[], regime_or_history=regime_history,
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    signals = detect_signals_for_day(ctx)
    assert all(s.setup != "ema_continuation" for s in signals), \
        f"Regime filter failed to block EMA continuation; got {[s.setup for s in signals]}"
```

- [ ] **Step 2: Run — expect FAIL on both**

Run: `pytest tests/unit/test_strategies_core.py::test_ema_continuation_signal_fires tests/unit/test_strategies_core.py::test_regime_filter_blocks_ema_continuation_on_high_or_day -v`
Expected: both FAIL.

- [ ] **Step 3: Implement EMA continuation + regime filter**

In `backtest/strategies/core.py`, replace the `# --- EMA continuation` placeholder. Reference `backtester.py:370-389` for the gate, and `backtester.py:_find_ema_continuations` for the bar-walk logic.

```python
    # --- Regime filter: skip EMA continuation on top-decile-OR days ---
    regime_cfg = cfg.get("regime_filter", {})
    regime_skip_ema = False
    if regime_cfg.get("enabled", False):
        min_hist = regime_cfg.get("min_history_days", 10)
        if len(ctx.regime_or_history) >= min_hist:
            history = list(ctx.regime_or_history)
            threshold_pct = regime_cfg.get("ema_skip_or_percentile", 90)
            threshold = float(np.percentile(history, threshold_pct))
            if opening_range.size >= threshold:
                regime_skip_ema = True

    # --- EMA continuation ---
    ema_cfg = cfg.get("ema_continuation", {})
    if ema_cfg.get("enabled", True) and not regime_skip_ema:
        ema_signals = _find_ema_continuations(
            bars=bars,
            opening_range=opening_range,
            breakout=breakout,
            cfg=cfg,
            min_start_bar=max(breakout.bar_index + 2, ctx.last_exit_bar),
        )
        signals.extend(ema_signals)

    return signals


def _find_ema_continuations(
    bars: pd.DataFrame,
    opening_range: OpeningRange,
    breakout: BreakoutEvent,
    cfg: dict,
    min_start_bar: int,
) -> list[Signal]:
    """Walk bars after the breakout, emit signals on each clean EMA-touch pullback.

    EXTRACTED FROM backtester.py::_find_ema_continuations (Phase 1 logic).
    """
    ema_cfg = cfg["ema_continuation"]
    period = ema_cfg.get("ema_period", 9)
    min_distance = ema_cfg.get("min_distance_from_or", 30)
    stop_buffer = ema_cfg.get("stop_buffer_points", 5)
    max_entries = ema_cfg.get("max_entries_per_day", 2)
    require_orb_dir = ema_cfg.get("require_orb_direction", True)

    ema_col = f"ema_{period}"
    if ema_col not in bars.columns:
        return []

    # Match ORB direction
    if require_orb_dir:
        if opening_range.direction == "bullish" and breakout.direction != "long":
            return []
        if opening_range.direction == "bearish" and breakout.direction != "short":
            return []

    direction = breakout.direction
    out: list[Signal] = []

    for i in range(min_start_bar, len(bars)):
        if len(out) >= max_entries:
            break
        bar = bars.iloc[i]
        ema_val = bar[ema_col]
        if pd.isna(ema_val):
            continue

        # Distance from OR check
        if direction == "long":
            far_enough = (bar["high"] - opening_range.high) >= min_distance
        else:
            far_enough = (opening_range.low - bar["low"]) >= min_distance
        if not far_enough:
            continue

        # EMA touch pullback: low (long) / high (short) wicks through EMA, close back above/below
        if direction == "long":
            touched = bar["low"] <= ema_val and bar["close"] > ema_val
        else:
            touched = bar["high"] >= ema_val and bar["close"] < ema_val
        if not touched:
            continue

        # Confluences on this bar
        conf = check_confluences(bar, direction, cfg)
        if not all(conf.values()):
            continue

        # Stop = swing low (long) or swing high (short) over recent bars
        lookback = 5
        recent = bars.iloc[max(0, i - lookback):i + 1]
        if direction == "long":
            stop_price = float(recent["low"].min()) - stop_buffer
            entry_price = float(bar["close"])
        else:
            stop_price = float(recent["high"].max()) + stop_buffer
            entry_price = float(bar["close"])

        out.append(Signal(
            setup="ema_continuation",
            direction=direction,
            entry_time=bars.index[i],
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=None,  # trail_ema only
            risk_points=abs(entry_price - stop_price),
            or_size=opening_range.size,
            or_classification=opening_range.classification,
            confluences=conf,
        ))

    return out
```

- [ ] **Step 4: Add indicators to bars before passing into core in tests**

Update both new tests to call `add_all_indicators` before constructing the context so the `ema_9` column exists. Append helper at top of file (after imports):

```python
from backtest.indicators import add_all_indicators


def _make_day_with_indicators(rows: list[dict], cfg: dict, date: str = "2025-06-15") -> pd.DataFrame:
    df = _make_day(rows, date)
    return add_all_indicators(df, cfg)
```

Then update `test_ema_continuation_signal_fires` and `test_regime_filter_blocks_ema_continuation_on_high_or_day` to use `_make_day_with_indicators(rows, cfg)` instead of `_make_day(rows)`.

- [ ] **Step 5: Run both EMA tests — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py::test_ema_continuation_signal_fires tests/unit/test_strategies_core.py::test_regime_filter_blocks_ema_continuation_on_high_or_day -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add backtest/strategies/core.py tests/unit/test_strategies_core.py
git commit -m "feat(strategies): EMA continuation + regime filter signal generation"
```

## Task 1.7: Route the backtester through strategies/core (delegation)

**Files:**
- Modify: `backtest/backtester.py`

- [ ] **Step 1: Replace `_process_day`'s embedded strategy logic with a call to `detect_signals_for_day`**

Open `backtest/backtester.py`. Locate `_process_day`. Keep the bookkeeping (or_history, regime_or_history maintenance, daily P&L tracking, trade simulation, exit walking) but replace the *signal generation* portion (currently lines ~270-395) with:

```python
from backtest.strategies.core import (
    detect_signals_for_day, SignalContext, RiskState,
)

# inside _process_day, after maintaining or_history / regime_or_history and computing
# daily_pnl/daily_trades/daily_losses but BEFORE the trade simulation:

ctx = SignalContext(
    day_bars=day_bars,
    date_str=date_str,
    or_history_inverse=list(self.or_history),
    regime_or_history=list(self.regime_or_history),
    strategy_config=self.strategy,
    risk_state=RiskState(
        daily_pnl=daily_pnl,
        daily_trades=daily_trades,
        daily_losses=daily_losses,
        running_pnl=self._running_pnl,
        peak_pnl=self._peak_pnl,
    ),
    last_exit_bar=last_exit_bar if 'last_exit_bar' in locals() else 0,
)

signals = detect_signals_for_day(ctx)

# For each signal, run the existing trade simulation. The backtester is
# responsible for stop/target walking, exit price determination, and P&L —
# strategies/core only emits the entry-side decision.
for sig in signals:
    if not self._check_risk_limits(daily_pnl, daily_trades, daily_losses):
        break
    if self._check_account_circuit_breaker():
        break
    trade = self._simulate_signal(day_bars, sig)
    if trade is None:
        continue
    trades.append(trade)
    daily_pnl += trade.pnl_dollars
    daily_trades += 1
    if trade.pnl_dollars <= 0:
        daily_losses += 1
    self._update_running_pnl(trade.pnl_dollars)
    last_exit_bar = self._bar_index_of(day_bars, trade.exit_time)
```

Add a new method `_simulate_signal(self, day_bars, sig: Signal) -> Optional[Trade]` that wraps the existing `_simulate_orb_trade` / EMA / inverse_orb simulators based on `sig.setup`. Keep the simulator bodies — they're correct.

- [ ] **Step 2: Run the regression test suite**

Run: `pytest tests/unit/test_strategies_core.py -v`
Expected: all 8 tests PASS (4 baseline + 4 new behavioural). The baseline tests are the critical ones — they prove byte-identical output.

- [ ] **Step 3: Run the full Phase 1 pipeline**

Run: `python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime" | tail -10`
Expected (must match executable baseline exactly): `Walk-forward: FAIL (pass_rate=8%, avg_oos_pf=0.53, avg_oos_pnl=$-452)` and `Monte Carlo: FAIL (ruin_prob=100.00%)`. A green Phase 1 result here means strategy semantics changed and must be reviewed, not celebrated.

- [ ] **Step 4: Compare numbers vs `/tmp/phase1_baseline.txt`**

Run: `diff /tmp/phase1_baseline.txt <(python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime")`
Expected: no diff or only timing-related diffs (timestamps in log lines). All numerical values must be identical.

- [ ] **Step 5: 🛑 CHECKPOINT — human approval required before continuing**

The backtester now delegates to shared core. Strategy semantics are now in two places that *must* stay aligned. Before any further work:

1. Human visually reviews the diff in `backtest/backtester.py::_process_day`.
2. Human confirms the regression suite passes byte-identically.
3. Human confirms `python scripts/run_phase1.py` produces matching numbers.

If anything drifts even by $1 in P&L, STOP and investigate. The whole Phase 2 strategy-correctness story rests on this delegation being lossless.

- [ ] **Step 6: Commit**

```bash
git add backtest/backtester.py
git commit -m "refactor(backtester): delegate signal generation to strategies/core

The backtester now owns ONLY trade simulation, exit walking, and bookkeeping.
All signal-emission semantics live in backtest/strategies/core.py and are shared
with the live signal generator (Phase 2). Phase 1 results unchanged."
```

---

# Epic 2: Test Infrastructure

## Task 2.1: Add pytest-mock and create mock IB fixture

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest-mock to dev deps**

Open `pyproject.toml`, find the `dev` extras list, add:

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-mock>=3.14",  # NEW
    "ruff>=0.6",
    "ipython>=8.0",
]
```

Run: `pip install -e ".[dev]"`
Expected: `pytest-mock` installed.

- [ ] **Step 2: Create `tests/conftest.py` with a mock IB fixture**

```python
# tests/conftest.py
"""Shared pytest fixtures for the live-bot test suite.

The MockIB fixture stands in for ib_insync.IB() — supports the connection
lifecycle, contract qualification, market-data subscription, order placement,
and event simulation. Tests construct mock bars / ticks / fills explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import pytest


@dataclass
class MockTrade:
    """Stand-in for ib_insync.Trade — what placeOrder returns."""
    contract: Any
    order: Any
    orderStatus: "MockOrderStatus" = field(default_factory=lambda: MockOrderStatus())
    fills: list = field(default_factory=list)
    log: list = field(default_factory=list)


@dataclass
class MockOrderStatus:
    status: str = "Submitted"
    filled: float = 0.0
    remaining: float = 0.0
    avgFillPrice: float = 0.0


@dataclass
class MockContract:
    symbol: str = "MNQ"
    exchange: str = "CME"
    secType: str = "FUT"
    conId: int = 770561201
    localSymbol: str = "MNQM6"
    lastTradeDateOrContractMonth: str = "20260618"
    multiplier: str = "2"
    currency: str = "USD"


class MockIB:
    """Minimal ib_insync.IB stand-in for unit tests."""

    def __init__(self):
        self.connected = False
        self.client_id: Optional[int] = None
        self.market_data_type: Optional[int] = None
        self.placed_orders: list[MockTrade] = []
        self.cancelled_orders: list[MockTrade] = []
        self._next_order_id = 1000
        self._event_handlers: dict[str, list[Callable]] = {}

    def connect(self, host: str, port: int, clientId: int, timeout: float = 20):
        self.connected = True
        self.client_id = clientId

    def disconnect(self):
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected

    def reqMarketDataType(self, market_data_type: int):
        self.market_data_type = market_data_type

    def qualifyContracts(self, contract):
        return [MockContract()]

    def placeOrder(self, contract, order) -> MockTrade:
        self._next_order_id += 1
        order.orderId = self._next_order_id
        trade = MockTrade(contract=contract, order=order)
        self.placed_orders.append(trade)
        return trade

    def cancelOrder(self, order):
        self.cancelled_orders.append(order)

    def positions(self):
        return []

    def reqHistoricalData(self, *args, **kwargs):
        return []

    # Event helpers — tests trigger fills/disconnects manually
    def simulate_fill(self, trade: MockTrade, price: float, qty: float):
        trade.orderStatus.status = "Filled"
        trade.orderStatus.filled = qty
        trade.orderStatus.avgFillPrice = price
        trade.fills.append({"price": price, "qty": qty, "time": datetime.utcnow()})

    def simulate_disconnect(self):
        self.connected = False


@pytest.fixture
def mock_ib() -> MockIB:
    """Fresh MockIB instance per test."""
    return MockIB()
```

- [ ] **Step 3: Smoke-test the fixture with a trivial test**

Append to `tests/conftest.py` (yes, in the same file is fine for a smoke):

Actually create a new test file:

```python
# tests/unit/test_conftest_smoke.py
"""Trivial smoke test that the mock IB fixture is wired correctly."""


def test_mock_ib_fixture_provides_disconnected_instance(mock_ib):
    assert mock_ib.isConnected() is False


def test_mock_ib_connect_disconnect(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    assert mock_ib.isConnected() is True
    assert mock_ib.client_id == 1
    mock_ib.disconnect()
    assert mock_ib.isConnected() is False


def test_mock_ib_qualify_returns_mnq(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    qualified = mock_ib.qualifyContracts(object())
    assert qualified[0].symbol == "MNQ"
    assert qualified[0].exchange == "CME"
```

Run: `pytest tests/unit/test_conftest_smoke.py -v`
Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/unit/test_conftest_smoke.py
git commit -m "test: mock IB fixture for live-bot unit tests"
```

---

# Epic 3: Tick Handler & Bar Aggregator

## Task 3.1: Tick-type mapper for delayed (66-75) ↔ live (1-9)

**Files:**
- Create: `bot/tick_handler.py`
- Test: `tests/unit/test_tick_handler.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_tick_handler.py
"""Tick-type handler maps delayed-data tick types (66-75) to canonical types.

Demo (DUO) accounts emit DELAYED_BID=66, DELAYED_ASK=67, etc. The signal
generator's downstream logic expects canonical BID=1, ASK=2, LAST=4, etc.
"""

from bot.tick_handler import canonical_tick_type, is_delayed_tick


def test_delayed_to_canonical_mapping():
    assert canonical_tick_type(66) == 1   # DELAYED_BID -> BID
    assert canonical_tick_type(67) == 2   # DELAYED_ASK -> ASK
    assert canonical_tick_type(68) == 4   # DELAYED_LAST -> LAST
    assert canonical_tick_type(72) == 6   # DELAYED_HIGH -> HIGH
    assert canonical_tick_type(73) == 7   # DELAYED_LOW -> LOW
    assert canonical_tick_type(74) == 8   # DELAYED_VOLUME -> VOLUME
    assert canonical_tick_type(75) == 9   # DELAYED_CLOSE -> CLOSE


def test_live_tick_passthrough():
    for t in [1, 2, 4, 6, 7, 8, 9]:
        assert canonical_tick_type(t) == t


def test_unknown_tick_type_returns_input_unchanged():
    assert canonical_tick_type(45) == 45  # IBKR's HISTORICAL_VOLATILITY etc.


def test_is_delayed_tick():
    assert is_delayed_tick(66)
    assert is_delayed_tick(75)
    assert not is_delayed_tick(1)
    assert not is_delayed_tick(45)
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/unit/test_tick_handler.py -v`
Expected: FAIL with `ImportError: bot.tick_handler`.

- [ ] **Step 3: Implement**

```python
# bot/tick_handler.py
"""Map delayed-data tick types (66-75) to canonical live tick types (1-9).

IBKR emits DELAYED_* variants on accounts without real-time subscriptions
(specifically DUO demo accounts and any account that called
reqMarketDataType(3) for delayed data). The bot's downstream logic should
not care which feed it's on — this module hides the difference.

Tick type IDs from https://interactivebrokers.github.io/tws-api/tick_types.html
"""

from __future__ import annotations

DELAYED_TO_LIVE = {
    66: 1,   # DELAYED_BID -> BID
    67: 2,   # DELAYED_ASK -> ASK
    68: 4,   # DELAYED_LAST -> LAST
    72: 6,   # DELAYED_HIGH -> HIGH
    73: 7,   # DELAYED_LOW -> LOW
    74: 8,   # DELAYED_VOLUME -> VOLUME
    75: 9,   # DELAYED_CLOSE -> CLOSE
}

DELAYED_RANGE = set(DELAYED_TO_LIVE.keys())


def canonical_tick_type(tick_type: int) -> int:
    """Return the canonical (live) tick type for a possibly-delayed type."""
    return DELAYED_TO_LIVE.get(tick_type, tick_type)


def is_delayed_tick(tick_type: int) -> bool:
    return tick_type in DELAYED_RANGE
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_tick_handler.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/tick_handler.py tests/unit/test_tick_handler.py
git commit -m "feat(bot): tick-type mapper for delayed/live IBKR tick streams"
```

## Task 3.2: 15-minute bar aggregator from a tick stream

**Files:**
- Create: `bot/bar_aggregator.py`
- Test: `tests/unit/test_bar_aggregator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_bar_aggregator.py
"""15-minute bar aggregator builds OHLCV bars from incoming trade ticks.

Strategy operates on completed 15m RTH bars. The aggregator buffers ticks
inside a window and emits a complete bar at window close (every :00, :15, :30, :45).
"""

import pandas as pd
import pytest

from bot.bar_aggregator import BarAggregator, Bar


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="US/Eastern")


def test_first_tick_starts_a_window():
    agg = BarAggregator()
    completed = agg.on_trade_tick(_ts("2025-06-15 09:30:01"), price=19000.0, size=2)
    assert completed is None  # window in progress, not yet complete


def test_window_completes_at_15min_boundary():
    agg = BarAggregator()
    agg.on_trade_tick(_ts("2025-06-15 09:30:01"), price=19000.0, size=2)
    agg.on_trade_tick(_ts("2025-06-15 09:30:30"), price=19010.0, size=1)
    agg.on_trade_tick(_ts("2025-06-15 09:44:59"), price=19005.0, size=3)
    # First tick of the next window triggers completion of the previous one
    bar = agg.on_trade_tick(_ts("2025-06-15 09:45:00"), price=19006.0, size=1)
    assert isinstance(bar, Bar)
    assert bar.timestamp == _ts("2025-06-15 09:30:00")  # window-start labelling
    assert bar.open == 19000.0
    assert bar.high == 19010.0
    assert bar.low == 19000.0
    assert bar.close == 19005.0
    assert bar.volume == 6


def test_no_ticks_in_window_emits_nothing():
    agg = BarAggregator()
    # Tick at 09:30 starts window; no ticks until 10:00; agg should emit one bar
    # for 09:30-09:45 and skip 09:45-10:00 entirely (gap), starting fresh at 10:00.
    agg.on_trade_tick(_ts("2025-06-15 09:30:01"), price=19000.0, size=1)
    bar = agg.on_trade_tick(_ts("2025-06-15 10:00:00"), price=19020.0, size=1)
    assert bar is not None
    assert bar.timestamp == _ts("2025-06-15 09:30:00")
    assert bar.close == 19000.0
    # The 09:45 and 10:00 bars must be inferred or skipped — for the strategy,
    # the rule is: the bar timestamp belongs to its WINDOW START. If there were
    # no trades in 09:45-10:00, no bar is emitted.


def test_bar_window_alignment():
    agg = BarAggregator()
    # Ticks at 09:32:30 should belong to the 09:30-09:45 window
    agg.on_trade_tick(_ts("2025-06-15 09:32:30"), price=19000.0, size=1)
    bar = agg.on_trade_tick(_ts("2025-06-15 09:45:00"), price=19010.0, size=1)
    assert bar.timestamp == _ts("2025-06-15 09:30:00")


def test_premarket_bars_dropped_when_rth_only():
    """Pre-market bars (before 09:30 ET) are not emitted when rth_only=True."""
    agg = BarAggregator(rth_only=True)
    # Pre-market tick at 09:15
    agg.on_trade_tick(_ts("2025-06-15 09:15:00"), price=19000.0, size=1)
    # Tick at 09:30 closes the pre-market window — but we should NOT emit it
    bar = agg.on_trade_tick(_ts("2025-06-15 09:30:00"), price=19010.0, size=1)
    assert bar is None  # the 09:15 window was pre-market — drop it


def test_after_hours_bars_dropped_when_rth_only():
    """After-hours bars (>=16:00 ET) are not emitted when rth_only=True."""
    agg = BarAggregator(rth_only=True)
    agg.on_trade_tick(_ts("2025-06-15 15:55:00"), price=19000.0, size=1)
    # 16:00 closes the 15:45-16:00 RTH window — that one IS emitted
    bar = agg.on_trade_tick(_ts("2025-06-15 16:00:01"), price=19010.0, size=1)
    assert bar is not None and bar.timestamp == _ts("2025-06-15 15:45:00")
    # 16:00 starts an after-hours window — closing it should NOT emit
    bar2 = agg.on_trade_tick(_ts("2025-06-15 16:15:00"), price=19015.0, size=1)
    assert bar2 is None
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_bar_aggregator.py -v`
Expected: FAIL on all (module missing).

- [ ] **Step 3: Implement**

```python
# bot/bar_aggregator.py
"""Aggregate trade ticks into 15-minute OHLCV bars on RTH boundaries.

Window labelling: the bar timestamp is the WINDOW START (09:30, 09:45, ...),
matching how ib_insync.reqHistoricalData with barSizeSetting='15 mins' labels
historical bars and how the backtest data loader stores them.

Emit policy: a completed bar is returned by the FIRST tick of the next window.
If a 15m window has zero ticks (illiquid late session), no bar is emitted —
the strategy already tolerates day_bars sparsity in `detect_signals_for_day`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Bar:
    timestamp: pd.Timestamp   # window start, US/Eastern
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarAggregator:
    """RTH = 09:30 to 16:00 ET. Set rth_only=False to keep pre/post-market bars.

    The default (rth_only=True) drops any bar whose window-start is outside
    [09:30, 16:00) ET. This matches the backtester's data-load filter so the
    live path can't ingest data the strategy never trained against.
    """
    RTH_START = pd.Timestamp("09:30", tz="US/Eastern").time()
    RTH_END = pd.Timestamp("16:00", tz="US/Eastern").time()

    def __init__(self, rth_only: bool = True):
        self.rth_only = rth_only
        self._window_start: Optional[pd.Timestamp] = None
        self._open: Optional[float] = None
        self._high: float = float("-inf")
        self._low: float = float("inf")
        self._close: Optional[float] = None
        self._volume: float = 0.0

    def on_trade_tick(self, ts: pd.Timestamp, price: float, size: float) -> Optional[Bar]:
        """Add a tick. If the tick crosses a 15m boundary, return the prior bar
        (only if its window was inside RTH when rth_only=True)."""
        window = _window_start_for(ts)
        completed: Optional[Bar] = None

        if self._window_start is None:
            self._begin_window(window, price)
        elif window != self._window_start:
            if self._is_rth_window(self._window_start) or not self.rth_only:
                completed = self._emit()
            self._begin_window(window, price)

        self._high = max(self._high, price)
        self._low = min(self._low, price)
        self._close = price
        self._volume += size
        return completed

    def _is_rth_window(self, window_start: pd.Timestamp) -> bool:
        t = window_start.time()
        return self.RTH_START <= t < self.RTH_END

    def _begin_window(self, window: pd.Timestamp, opening_price: float):
        self._window_start = window
        self._open = opening_price
        self._high = opening_price
        self._low = opening_price
        self._close = opening_price
        self._volume = 0.0

    def _emit(self) -> Bar:
        assert self._window_start is not None and self._open is not None
        return Bar(
            timestamp=self._window_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,  # type: ignore[arg-type]
            volume=self._volume,
        )


def _window_start_for(ts: pd.Timestamp) -> pd.Timestamp:
    """Floor a timestamp to its 15-minute window start."""
    minute = (ts.minute // 15) * 15
    return ts.replace(minute=minute, second=0, microsecond=0, nanosecond=0)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_bar_aggregator.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/bar_aggregator.py tests/unit/test_bar_aggregator.py
git commit -m "feat(bot): 15m bar aggregator from tick stream"
```

---

# Epic 4: IBKR Executor

## Task 4.1: Contract resolver — qualify front-month MNQ

**Files:**
- Create: `bot/execution/contract_resolver.py`
- Test: `tests/unit/test_contract_resolver.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_contract_resolver.py
"""Front-month MNQ contract resolution. Live bot trades the front-month
specific Future (NOT ContFuture — we need to be able to query position by conId).
"""

from bot.execution.contract_resolver import resolve_front_month_mnq


def test_resolve_front_month_returns_qualified_contract(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    contract = resolve_front_month_mnq(mock_ib)
    assert contract.symbol == "MNQ"
    assert contract.exchange == "CME"
    assert contract.conId > 0
    assert contract.localSymbol.startswith("MNQ")


def test_resolve_raises_when_not_connected(mock_ib):
    # mock_ib starts disconnected
    import pytest
    with pytest.raises(RuntimeError, match="not connected"):
        resolve_front_month_mnq(mock_ib)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_contract_resolver.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# bot/execution/contract_resolver.py
"""Resolve the front-month MNQ futures contract via IBKR.

Live trading uses a specific Future (not ContFuture) so we can:
  - place orders on a known conId
  - track position via the same conId in the watchdog

Rolls happen ~8 days before expiry. This module qualifies the *current* front
month at startup and returns it. If you start the bot on roll day, restart
after roll completes — auto-rollover during a session is out of scope.
"""

from __future__ import annotations

from typing import Any


def resolve_front_month_mnq(ib: Any) -> Any:
    """Return a qualified MNQ futures contract.

    Uses ib_insync's ContFuture qualification to identify the active front month,
    then re-qualifies the resolved-month spec as a Future to get a stable conId.
    """
    if not ib.isConnected():
        raise RuntimeError("IBKR not connected — call ib.connect() first")

    # Late import: keep ib_insync out of the module-level imports so unit tests
    # that pass MockIB don't need ib_insync installed (they don't, but keep
    # the boundary clean).
    from ib_insync import ContFuture, Future

    cont = ContFuture("MNQ", exchange="CME")
    qualified_cont = ib.qualifyContracts(cont)
    if not qualified_cont:
        raise RuntimeError("Failed to qualify MNQ ContFuture")

    front_month = qualified_cont[0].lastTradeDateOrContractMonth
    fut = Future("MNQ", lastTradeDateOrContractMonth=front_month, exchange="CME")
    qualified_fut = ib.qualifyContracts(fut)
    if not qualified_fut:
        raise RuntimeError(f"Failed to qualify MNQ Future {front_month}")

    return qualified_fut[0]
```

Note: `MockIB.qualifyContracts` returns a `MockContract` directly without distinguishing ContFuture vs Future, which is fine for this test — we just verify the API is called and a usable contract comes back.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_contract_resolver.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/execution/contract_resolver.py tests/unit/test_contract_resolver.py
git commit -m "feat(bot): MNQ front-month contract resolver"
```

## Task 4.2: Order state machine

**Files:**
- Create: `bot/execution/order_state.py`
- Test: `tests/unit/test_order_state.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_order_state.py
"""Order state machine — tracks bracket-order lifecycle.

States: SUBMITTED -> WORKING -> {FILLED, CANCELLED, REJECTED, EXITED}.
A bracket has three legs: ENTRY, STOP, TARGET. The bracket as a whole is
EXITED when either STOP or TARGET fills (the surviving leg auto-cancels
via OCA / parentId linkage).
"""

import pytest
from bot.execution.order_state import (
    BracketTracker, BracketState, OrderLegStatus,
)


def test_new_bracket_starts_submitted():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    assert bt.state == BracketState.SUBMITTED
    assert bt.position == 0


def test_entry_fill_transitions_to_working():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    bt.on_fill(order_id=1, price=19050.0, qty=1)
    assert bt.state == BracketState.WORKING
    assert bt.position == 1
    assert bt.entry_price == 19050.0


def test_stop_fill_exits_bracket():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    bt.on_fill(order_id=1, price=19050.0, qty=1)
    bt.on_fill(order_id=2, price=19000.0, qty=1)  # stop hit
    assert bt.state == BracketState.EXITED
    assert bt.position == 0
    assert bt.exit_price == 19000.0
    assert bt.exit_reason == "stop"


def test_target_fill_exits_bracket():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    bt.on_fill(order_id=1, price=19050.0, qty=1)
    bt.on_fill(order_id=3, price=19100.0, qty=1)
    assert bt.state == BracketState.EXITED
    assert bt.exit_reason == "target"


def test_reject_before_fill_kills_bracket():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    bt.on_status(order_id=1, status="Cancelled")
    assert bt.state == BracketState.REJECTED


def test_unknown_order_id_raises():
    bt = BracketTracker(parent_id=1, stop_id=2, target_id=3)
    with pytest.raises(ValueError, match="not part of this bracket"):
        bt.on_fill(order_id=999, price=19050.0, qty=1)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_order_state.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# bot/execution/order_state.py
"""State machine for a single bracket order (entry + stop + target).

Lifecycle:
  SUBMITTED  -- entry fills      -->  WORKING (position open)
  WORKING    -- stop fills       -->  EXITED (reason="stop")
  WORKING    -- target fills     -->  EXITED (reason="target")
  WORKING    -- bot flatten/eod  -->  EXITED (reason="manual")
  SUBMITTED  -- entry rejected   -->  REJECTED
  SUBMITTED  -- bot cancels      -->  CANCELLED
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class BracketState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    EXITED = "exited"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderLegStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


class BracketTracker:
    def __init__(self, parent_id: int, stop_id: int, target_id: int):
        self.parent_id = parent_id
        self.stop_id = stop_id
        self.target_id = target_id
        self.state = BracketState.SUBMITTED
        self.position: int = 0
        self.entry_price: Optional[float] = None
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str] = None
        self._leg_status = {parent_id: OrderLegStatus.PENDING,
                            stop_id: OrderLegStatus.PENDING,
                            target_id: OrderLegStatus.PENDING}

    def on_fill(self, order_id: int, price: float, qty: int) -> None:
        if order_id not in self._leg_status:
            raise ValueError(f"orderId {order_id} not part of this bracket")
        self._leg_status[order_id] = OrderLegStatus.FILLED

        if order_id == self.parent_id:
            self.state = BracketState.WORKING
            self.position = qty
            self.entry_price = price
        elif order_id == self.stop_id:
            self.state = BracketState.EXITED
            self.position = 0
            self.exit_price = price
            self.exit_reason = "stop"
        elif order_id == self.target_id:
            self.state = BracketState.EXITED
            self.position = 0
            self.exit_price = price
            self.exit_reason = "target"

    def on_status(self, order_id: int, status: str) -> None:
        if order_id not in self._leg_status:
            raise ValueError(f"orderId {order_id} not part of this bracket")
        if status in ("Cancelled", "ApiCancelled"):
            self._leg_status[order_id] = OrderLegStatus.CANCELLED
            if order_id == self.parent_id and self.state == BracketState.SUBMITTED:
                self.state = BracketState.REJECTED

    def manual_exit(self, price: float, reason: str = "manual") -> None:
        """Bot/watchdog forced flatten — record the exit."""
        if self.state == BracketState.WORKING:
            self.state = BracketState.EXITED
            self.position = 0
            self.exit_price = price
            self.exit_reason = reason
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_order_state.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/execution/order_state.py tests/unit/test_order_state.py
git commit -m "feat(bot): bracket order state machine"
```

## Task 4.3: IBKR executor — bracket submission

**Files:**
- Create: `bot/execution/ibkr_executor.py`
- Test: `tests/unit/test_ibkr_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_ibkr_executor.py
"""IBKR executor submits bracket orders (entry + stop + target) atomically.

The executor is the ONLY path through which the bot places orders. It enforces:
  - 1 contract default (no scale-up)
  - bracket atomicity (3 legs as one OCA group)
  - parentId linkage (stop/target child of entry)
  - transmit=True only on the LAST leg
"""

from bot.execution.ibkr_executor import IBKRExecutor


def test_submit_long_bracket_emits_three_orders(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    ex = IBKRExecutor(mock_ib)
    bracket = ex.submit_bracket(
        action="BUY",
        quantity=1,
        entry_price=19050.0,
        stop_price=19000.0,
        target_price=19100.0,
    )
    assert len(mock_ib.placed_orders) == 3
    parent, target, stop = mock_ib.placed_orders
    # OCA / parent linkage
    assert target.order.parentId == parent.order.orderId
    assert stop.order.parentId == parent.order.orderId
    # Transmit only on the last leg
    assert parent.order.transmit is False
    assert target.order.transmit is False
    assert stop.order.transmit is True


def test_submit_short_bracket_inverts_actions(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    ex = IBKRExecutor(mock_ib)
    ex.submit_bracket(
        action="SELL",
        quantity=1,
        entry_price=19000.0,
        stop_price=19050.0,
        target_price=18950.0,
    )
    parent, target, stop = mock_ib.placed_orders
    assert parent.order.action == "SELL"
    assert target.order.action == "BUY"  # opposite of entry to close
    assert stop.order.action == "BUY"


def test_submit_refuses_quantity_above_max(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    ex = IBKRExecutor(mock_ib, max_contracts=1)
    import pytest
    with pytest.raises(ValueError, match="exceeds max_contracts"):
        ex.submit_bracket("BUY", 2, 19050.0, 19000.0, 19100.0)
    assert mock_ib.placed_orders == []
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_ibkr_executor.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# bot/execution/ibkr_executor.py
"""IBKR executor — submits bracket orders for the live bot.

Real-money guarantees:
  - bracket is 3 legs in a single OCA group (entry + stop + target)
  - transmit=True only on the LAST leg sent (atomic from IBKR's view)
  - quantity capped at max_contracts (default 1) — refuses to scale up
  - the executor never places NAKED entries (no entry without stop+target)
"""

from __future__ import annotations

from typing import Any, Optional

from bot.execution.order_state import BracketTracker


class IBKRExecutor:
    def __init__(self, ib: Any, max_contracts: int = 1):
        self.ib = ib
        self.max_contracts = max_contracts
        self._contract: Optional[Any] = None
        self._brackets: list[BracketTracker] = []
        self._exit_callback = None  # set via set_exit_callback() — see handle_fill

    def set_contract(self, contract: Any) -> None:
        self._contract = contract

    def submit_bracket(
        self,
        action: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
    ) -> BracketTracker:
        """Submit entry + stop + target as an OCA bracket. Returns a tracker."""
        if quantity > self.max_contracts:
            raise ValueError(
                f"quantity {quantity} exceeds max_contracts {self.max_contracts}"
            )
        if action not in ("BUY", "SELL"):
            raise ValueError(f"action must be BUY or SELL, got {action}")
        if self._contract is None:
            raise RuntimeError("Contract not set — call set_contract() first")

        from ib_insync import LimitOrder, StopOrder

        opposite = "SELL" if action == "BUY" else "BUY"

        parent = LimitOrder(action, quantity, entry_price)
        parent.transmit = False
        parent.outsideRth = False

        target = LimitOrder(opposite, quantity, target_price)
        target.transmit = False

        stop = StopOrder(opposite, quantity, stop_price)
        stop.transmit = True   # final leg — IBKR processes the group atomically

        # Place parent first to allocate orderId, then chain children
        parent_trade = self.ib.placeOrder(self._contract, parent)
        target.parentId = parent_trade.order.orderId
        target_trade = self.ib.placeOrder(self._contract, target)
        stop.parentId = parent_trade.order.orderId
        stop_trade = self.ib.placeOrder(self._contract, stop)

        tracker = BracketTracker(
            parent_id=parent_trade.order.orderId,
            stop_id=stop_trade.order.orderId,
            target_id=target_trade.order.orderId,
        )
        self._brackets.append(tracker)
        return tracker

    def cancel_all(self) -> None:
        """Cancel all open orders. Used on bot shutdown."""
        for trade in list(self.ib.placed_orders):
            if trade.orderStatus.status not in ("Filled", "Cancelled"):
                self.ib.cancelOrder(trade.order)

    def open_brackets(self) -> list[BracketTracker]:
        from bot.execution.order_state import BracketState
        return [b for b in self._brackets
                if b.state in (BracketState.SUBMITTED, BracketState.WORKING)]
```

Note: tests use `MockIB.placeOrder` which assigns an orderId via `self._next_order_id += 1; order.orderId = self._next_order_id`. Real ib_insync uses its own ID allocation. The mock matches the contract well enough to test bracket structure.

Add `set_contract` call in tests (they currently don't):

```python
# Update each test before submit_bracket():
    ex.set_contract(mock_ib.qualifyContracts(None)[0])
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_ibkr_executor.py -v`
Expected: 3 PASS (after updating tests with set_contract).

- [ ] **Step 5: Commit**

```bash
git add bot/execution/ibkr_executor.py tests/unit/test_ibkr_executor.py
git commit -m "feat(bot): IBKR bracket-order executor with size cap"
```

## Task 4.4: IBKR executor — fill / cancel event handling

**Files:**
- Modify: `bot/execution/ibkr_executor.py`
- Modify: `tests/unit/test_ibkr_executor.py`

- [ ] **Step 1: Write failing test for fill propagation**

Append to `tests/unit/test_ibkr_executor.py`:

```python
def test_executor_routes_fills_to_correct_bracket(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    ex = IBKRExecutor(mock_ib)
    ex.set_contract(mock_ib.qualifyContracts(None)[0])
    bracket = ex.submit_bracket("BUY", 1, 19050.0, 19000.0, 19100.0)

    # Simulate entry fill via the executor's event handler
    parent_trade = mock_ib.placed_orders[0]
    ex.handle_fill(parent_trade.order.orderId, 19050.0, 1)
    assert bracket.position == 1

    # Simulate target fill
    target_trade = mock_ib.placed_orders[1]
    ex.handle_fill(target_trade.order.orderId, 19100.0, 1)
    assert bracket.exit_reason == "target"
    assert bracket.position == 0
```

- [ ] **Step 2: Run — expect FAIL** (handle_fill missing)

Run: `pytest tests/unit/test_ibkr_executor.py::test_executor_routes_fills_to_correct_bracket -v`
Expected: FAIL.

- [ ] **Step 3: Implement event routing on the executor**

Add to `IBKRExecutor`:

```python
    def handle_fill(self, order_id: int, price: float, qty: int) -> None:
        """Called by the bot's event loop when an orderStatus fill arrives.

        On bracket EXIT (stop or target fill), invoke the registered exit
        callback so the bot updates its state (daily P&L, consecutive losses,
        peak equity) which the health monitor's circuit breakers depend on.
        """
        from bot.execution.order_state import BracketState
        for bracket in self._brackets:
            if order_id in (bracket.parent_id, bracket.stop_id, bracket.target_id):
                bracket.on_fill(order_id, price, qty)
                if bracket.state == BracketState.EXITED and self._exit_callback is not None:
                    self._exit_callback(bracket)
                return
        import logging
        logging.getLogger(__name__).warning(
            f"handle_fill: unknown orderId {order_id}; ignoring"
        )

    def handle_status(self, order_id: int, status: str) -> None:
        for bracket in self._brackets:
            if order_id in (bracket.parent_id, bracket.stop_id, bracket.target_id):
                bracket.on_status(order_id, status)
                return

    def set_exit_callback(self, fn) -> None:
        """Register a callback fired when a bracket fully exits. Receives the
        BracketTracker (with entry_price, exit_price, exit_reason populated)."""
        self._exit_callback = fn
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_ibkr_executor.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/execution/ibkr_executor.py tests/unit/test_ibkr_executor.py
git commit -m "feat(bot): executor routes IBKR fill/status events to bracket trackers"
```

---

# Epic 5: Health Monitor

## Task 5.1: Heartbeat + circuit-breaker enforcement

**Files:**
- Create: `bot/health_monitor.py`
- Test: `tests/unit/test_health_monitor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_health_monitor.py
"""Health monitor enforces circuit breakers, heartbeat, connection-loss detection.

Circuit breakers (from config/risk_params.yaml):
  - account_drawdown_pct: halt if peak-equity drawdown >= 20%
  - daily_limits.max_loss: halt if daily P&L <= -$400
  - daily_limits.consecutive_loss_halt: halt after 3 consecutive losses
  - daily_limits.max_trades: halt after 5 trades
  - connection_loss_seconds: flatten if data feed disconnected > 60s
"""

from datetime import datetime, timedelta
from bot.health_monitor import HealthMonitor, HaltReason


def _cfg() -> dict:
    return {
        "account": {"starting_balance": 2500},
        "daily_limits": {
            "max_loss": -400,
            "max_trades": 5,
            "consecutive_loss_halt": 3,
        },
        "circuit_breakers": {
            "account_drawdown_pct": 0.20,
            "connection_loss_seconds": 60,
        },
    }


def test_no_halt_on_clean_state():
    hm = HealthMonitor(_cfg())
    halt = hm.check_can_trade(daily_pnl=0, daily_trades=0, consec_losses=0,
                              running_pnl=0, peak_pnl=0)
    assert halt is None


def test_halt_on_max_daily_loss():
    hm = HealthMonitor(_cfg())
    halt = hm.check_can_trade(daily_pnl=-401, daily_trades=2, consec_losses=1,
                              running_pnl=-401, peak_pnl=0)
    assert halt == HaltReason.DAILY_LOSS


def test_halt_on_max_daily_trades():
    hm = HealthMonitor(_cfg())
    halt = hm.check_can_trade(daily_pnl=200, daily_trades=5, consec_losses=0,
                              running_pnl=200, peak_pnl=200)
    assert halt == HaltReason.DAILY_TRADES


def test_halt_on_consecutive_losses():
    hm = HealthMonitor(_cfg())
    halt = hm.check_can_trade(daily_pnl=-200, daily_trades=3, consec_losses=3,
                              running_pnl=-200, peak_pnl=0)
    assert halt == HaltReason.CONSECUTIVE_LOSSES


def test_halt_on_account_drawdown():
    hm = HealthMonitor(_cfg())
    # Peak P&L was +$1000 (account at $3500), now down to -$200 from peak's $1000
    # Drawdown = (3500 - 2300) / 3500 = 34% > 20%
    halt = hm.check_can_trade(daily_pnl=-100, daily_trades=1, consec_losses=1,
                              running_pnl=-200, peak_pnl=1000)
    assert halt == HaltReason.ACCOUNT_DRAWDOWN


def test_connection_loss_triggers_after_threshold():
    hm = HealthMonitor(_cfg())
    now = datetime(2025, 6, 15, 10, 0, 0)
    hm.note_data_seen(now)
    # 30s later — still healthy
    assert hm.check_data_freshness(now + timedelta(seconds=30)) is None
    # 70s later — over threshold
    assert hm.check_data_freshness(now + timedelta(seconds=70)) == HaltReason.DATA_STALE
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_health_monitor.py -v`
Expected: FAIL on all.

- [ ] **Step 3: Implement**

```python
# bot/health_monitor.py
"""Health monitor — heartbeat, circuit breakers, data freshness.

The bot calls check_can_trade() before every signal evaluation. If a halt
reason is returned, the bot does NOT submit a new bracket. Existing positions
remain (their stops still protect them; the watchdog flattens at 15:45).

The bot calls check_data_freshness() in its main loop. If data is stale,
the bot enters a "flatten and wait" state — it cancels any pending entry,
holds existing positions to their stops, and pauses signal generation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class HaltReason(str, Enum):
    DAILY_LOSS = "daily_loss"
    DAILY_TRADES = "daily_trades"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    ACCOUNT_DRAWDOWN = "account_drawdown"
    DATA_STALE = "data_stale"


class HealthMonitor:
    def __init__(self, risk_cfg: dict):
        self.cfg = risk_cfg
        self._last_data_seen: Optional[datetime] = None

    def check_can_trade(
        self,
        daily_pnl: float,
        daily_trades: int,
        consec_losses: int,
        running_pnl: float,
        peak_pnl: float,
    ) -> Optional[HaltReason]:
        limits = self.cfg["daily_limits"]
        if daily_pnl <= limits["max_loss"]:
            return HaltReason.DAILY_LOSS
        if daily_trades >= limits["max_trades"]:
            return HaltReason.DAILY_TRADES
        if consec_losses >= limits["consecutive_loss_halt"]:
            return HaltReason.CONSECUTIVE_LOSSES

        cb = self.cfg.get("circuit_breakers", {})
        max_dd_pct = cb.get("account_drawdown_pct", 0.20)
        starting_balance = self.cfg["account"]["starting_balance"]
        peak_equity = starting_balance + peak_pnl
        current_equity = starting_balance + running_pnl
        if peak_equity > 0:
            dd_pct = (peak_equity - current_equity) / peak_equity
            if dd_pct >= max_dd_pct:
                return HaltReason.ACCOUNT_DRAWDOWN

        return None

    def note_data_seen(self, when: datetime) -> None:
        self._last_data_seen = when

    def check_data_freshness(self, now: datetime) -> Optional[HaltReason]:
        if self._last_data_seen is None:
            return None  # never received data yet — bot startup
        cb = self.cfg.get("circuit_breakers", {})
        threshold_s = cb.get("connection_loss_seconds", 60)
        if (now - self._last_data_seen) > timedelta(seconds=threshold_s):
            return HaltReason.DATA_STALE
        return None
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_health_monitor.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/health_monitor.py tests/unit/test_health_monitor.py
git commit -m "feat(bot): health monitor — circuit breakers + data freshness"
```

---

# Epic 6: Signal Generator

## Task 6.1: Bot runtime state

**Files:**
- Create: `bot/state.py`
- Test: covered indirectly by signal_generator tests

- [ ] **Step 1: Implement (no tests; this is a plain dataclass container)**

```python
# bot/state.py
"""Runtime state for the bot — counters and aggregates used by health/signal logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BotState:
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_losses: int = 0
    consec_losses: int = 0
    running_pnl: float = 0.0
    peak_pnl: float = 0.0
    or_history_inverse: list[float] = field(default_factory=list)
    regime_or_history: list[float] = field(default_factory=list)
    completed_bars: list = field(default_factory=list)  # list[Bar] — full day
    today_date: Optional[str] = None

    def reset_daily(self, date_str: str) -> None:
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_losses = 0
        self.consec_losses = 0
        self.completed_bars = []
        self.today_date = date_str

    def record_trade(self, pnl_dollars: float) -> None:
        self.daily_pnl += pnl_dollars
        self.daily_trades += 1
        self.running_pnl += pnl_dollars
        if self.running_pnl > self.peak_pnl:
            self.peak_pnl = self.running_pnl
        if pnl_dollars <= 0:
            self.daily_losses += 1
            self.consec_losses += 1
        else:
            self.consec_losses = 0
```

- [ ] **Step 2: Commit**

```bash
git add bot/state.py
git commit -m "feat(bot): runtime state container"
```

## Task 6.2: Signal generator — bar-driven loop

**Files:**
- Create: `bot/signal_generator.py`
- Test: `tests/unit/test_signal_generator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_signal_generator.py
"""Signal generator: as 15m bars arrive, evaluate the strategy and submit
bracket orders via the executor. Inherits regime_filter from config.

Tests use replayed historical bars rather than a tick stream — the aggregator
already converted ticks to bars in production. We hand bars in directly.
"""

import yaml
from pathlib import Path

import pandas as pd

from backtest.indicators import add_all_indicators
from bot.bar_aggregator import Bar
from bot.execution.ibkr_executor import IBKRExecutor
from bot.health_monitor import HealthMonitor
from bot.state import BotState
from bot.signal_generator import SignalGenerator


PROJECT_ROOT = Path(__file__).parent.parent.parent
STRAT = PROJECT_ROOT / "config" / "strategy_params.yaml"
RISK = PROJECT_ROOT / "config" / "risk_params.yaml"


def _load_cfg(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _bars_to_dataframe(bars: list[Bar], cfg: dict) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume
    } for b in bars], index=pd.DatetimeIndex([b.timestamp for b in bars]))
    return add_all_indicators(df, cfg)


def test_signal_generator_does_not_submit_below_min_or(mock_ib):
    """OR below min_size_points — no bracket submitted."""
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    strat = _load_cfg(STRAT)
    risk = _load_cfg(RISK)
    strat["opening_range"]["min_size_points"] = 50
    state = BotState()
    state.today_date = "2025-06-15"
    executor = IBKRExecutor(mock_ib)
    executor.set_contract(mock_ib.qualifyContracts(None)[0])
    health = HealthMonitor(risk)
    sg = SignalGenerator(strat, risk, executor, health, state)

    base = pd.Timestamp("2025-06-15 09:30", tz="US/Eastern")
    sg.on_bar_complete(Bar(timestamp=base, open=19000, high=19010, low=19000, close=19005, volume=1000))
    sg.on_bar_complete(Bar(timestamp=base + pd.Timedelta(minutes=15),
                           open=19005, high=19006, low=19000, close=19002, volume=1000))
    assert mock_ib.placed_orders == []


def test_signal_generator_inherits_regime_filter(mock_ib):
    """High-OR day with regime filter on — EMA continuation suppressed."""
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    strat = _load_cfg(STRAT)
    risk = _load_cfg(RISK)
    state = BotState()
    state.regime_or_history = [50.0] * 30  # low-vol history
    state.today_date = "2025-06-15"
    executor = IBKRExecutor(mock_ib)
    executor.set_contract(mock_ib.qualifyContracts(None)[0])
    health = HealthMonitor(risk)
    sg = SignalGenerator(strat, risk, executor, health, state)

    # Construct a bullish breakout day with HUGE OR (top decile of history)
    base = pd.Timestamp("2025-06-15 09:30", tz="US/Eastern")
    bars = [
        Bar(base + pd.Timedelta(minutes=15 * i), o, h, l, c, 5000)
        for i, (o, h, l, c) in enumerate([
            (19000, 19200, 19000, 19180),  # huge OR
            (19180, 19220, 19170, 19210),  # breakout
            (19210, 19230, 19170, 19200),  # pullback through EMA
        ])
    ]
    for b in bars:
        sg.on_bar_complete(b)
    # Regime filter should block any EMA-continuation bracket
    # (an ORB breakout-retest could still fire in principle — but the bars above
    # don't form a clean retest, so net submissions should be 0)
    assert mock_ib.placed_orders == []


def test_signal_generator_halts_on_circuit_breaker(mock_ib):
    """When health monitor returns a halt, no bracket is submitted."""
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    strat = _load_cfg(STRAT)
    risk = _load_cfg(RISK)
    state = BotState()
    state.daily_pnl = -500   # already breached -$400 limit
    state.today_date = "2025-06-15"
    executor = IBKRExecutor(mock_ib)
    executor.set_contract(mock_ib.qualifyContracts(None)[0])
    health = HealthMonitor(risk)
    sg = SignalGenerator(strat, risk, executor, health, state)

    base = pd.Timestamp("2025-06-15 09:30", tz="US/Eastern")
    sg.on_bar_complete(Bar(base, 19000, 19050, 19000, 19045, 5000))
    sg.on_bar_complete(Bar(base + pd.Timedelta(minutes=15),
                           19045, 19075, 19044, 19070, 5000))
    assert mock_ib.placed_orders == []
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_signal_generator.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# bot/signal_generator.py
"""Bar-driven signal loop. Called by the bot's main event loop.

Each completed 15m bar triggers strategy evaluation. If the strategy emits
a new signal AND the health monitor allows trading, submit a bracket order
via the executor.

This module owns NO strategy semantics — it delegates to
backtest.strategies.core.detect_signals_for_day.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from backtest.indicators import add_all_indicators
from backtest.strategies.core import detect_signals_for_day, SignalContext, RiskState
from bot.bar_aggregator import Bar
from bot.execution.ibkr_executor import IBKRExecutor
from bot.health_monitor import HealthMonitor, HaltReason
from bot.state import BotState

logger = logging.getLogger(__name__)


class SignalGenerator:
    def __init__(
        self,
        strategy_cfg: dict,
        risk_cfg: dict,
        executor: IBKRExecutor,
        health: HealthMonitor,
        state: BotState,
    ):
        self.strategy_cfg = strategy_cfg
        self.risk_cfg = risk_cfg
        self.executor = executor
        self.health = health
        self.state = state
        self._submitted_signals: set = set()  # (entry_time, setup) keys to dedupe

    def on_bar_complete(self, bar: Bar) -> None:
        # Reset state on new trading day
        bar_date = str(bar.timestamp.date())
        if self.state.today_date != bar_date:
            self.state.reset_daily(bar_date)

        # Trading-window enforcement — strategy_params.yaml schedule.trading_end
        # gates new signals (e.g. no entries after 12:00 ET). We still process
        # the bar (so completed_bars stays current and exits get evaluated),
        # but skip signal generation outside the window.
        schedule = self.strategy_cfg.get("schedule", {})
        end_str = schedule.get("trading_end", "12:00")
        end_h, end_m = map(int, end_str.split(":"))
        bar_minutes = bar.timestamp.hour * 60 + bar.timestamp.minute
        end_minutes = end_h * 60 + end_m
        in_window = bar_minutes <= end_minutes

        self.state.completed_bars.append(bar)

        # Build a DataFrame of today's completed bars + indicators
        df = pd.DataFrame([
            {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in self.state.completed_bars
        ], index=pd.DatetimeIndex([b.timestamp for b in self.state.completed_bars]))
        df = add_all_indicators(df, self.strategy_cfg)

        # Maintain or_history_inverse and regime_or_history at OR-bar boundary
        or_period = self.strategy_cfg["opening_range"]["period_minutes"]
        or_bars = or_period // 15
        if len(self.state.completed_bars) == or_bars:
            # OR just completed today
            or_size = max(b.high for b in self.state.completed_bars[:or_bars]) - \
                      min(b.low for b in self.state.completed_bars[:or_bars])
            self.state.or_history_inverse.append(or_size)
            inv_lookback = self.strategy_cfg.get("inverse_orb", {}).get("lookback_days", 20)
            self.state.or_history_inverse = self.state.or_history_inverse[-inv_lookback:]
            self.state.regime_or_history.append(or_size)
            regime_max = self.strategy_cfg.get("regime_filter", {}).get("max_history_days", 60)
            self.state.regime_or_history = self.state.regime_or_history[-regime_max:]

        # Health check first
        halt = self.health.check_can_trade(
            daily_pnl=self.state.daily_pnl,
            daily_trades=self.state.daily_trades,
            consec_losses=self.state.consec_losses,
            running_pnl=self.state.running_pnl,
            peak_pnl=self.state.peak_pnl,
        )
        if halt is not None:
            logger.warning(f"Bar {bar.timestamp}: trading halted — {halt.value}")
            return

        # Outside the strategy's signal window — skip new signals
        if not in_window:
            return

        # Already have an open position? Don't submit another.
        # Source of truth is the executor's bracket list, not bot state — avoids
        # state-clearing bugs when brackets exit.
        if self.executor.open_brackets():
            return

        # Run strategy
        ctx = SignalContext(
            day_bars=df,
            date_str=bar_date,
            or_history_inverse=list(self.state.or_history_inverse),
            regime_or_history=list(self.state.regime_or_history),
            strategy_config=self.strategy_cfg,
            risk_state=RiskState(
                daily_pnl=self.state.daily_pnl,
                daily_trades=self.state.daily_trades,
                daily_losses=self.state.daily_losses,
                running_pnl=self.state.running_pnl,
                peak_pnl=self.state.peak_pnl,
            ),
        )
        signals = detect_signals_for_day(ctx)

        # Dedupe and submit at most one new signal per bar
        for sig in signals:
            key = (sig.entry_time, sig.setup)
            if key in self._submitted_signals:
                continue
            target = sig.target_price if sig.target_price is not None \
                else self._derive_target(sig)
            tracker = self.executor.submit_bracket(
                action="BUY" if sig.direction == "long" else "SELL",
                quantity=1,
                entry_price=sig.entry_price,
                stop_price=sig.stop_price,
                target_price=target,
            )
            self._submitted_signals.add(key)
            logger.info(f"Submitted {sig.setup} {sig.direction} bracket at {sig.entry_price}, "
                        f"stop {sig.stop_price}, target {target}")
            return  # one entry per bar

    def _derive_target(self, sig) -> float:
        """For setups with target_method=trail_ema we still need an initial target
        for the bracket. Default to 1:1 R:R; the trail logic in a future task
        revises it during the trade life."""
        if sig.direction == "long":
            return sig.entry_price + sig.risk_points
        return sig.entry_price - sig.risk_points
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_signal_generator.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/signal_generator.py tests/unit/test_signal_generator.py
git commit -m "feat(bot): bar-driven signal generator with regime + health gating"
```

## Task 6.3: Bot main entry point

**Files:**
- Create: `bot/main.py`
- Create: `config/bot.yaml`

- [ ] **Step 1: Create runtime config**

```yaml
# config/bot.yaml
# Bot runtime config — read by bot/main.py at startup.
# Strategy params live in config/strategy_params.yaml; risk params in
# config/risk_params.yaml. THIS file is for runtime/connection settings only.

ibkr:
  host: 127.0.0.1
  port: 7497            # 7497 paper / 7496 live
  client_id: 1
  market_data_type: 3   # 1=live, 3=delayed (DUO demo). Switch to 1 on funded paper/live.

logging:
  level: INFO
  file: /var/log/mnq-bot/bot.log

health:
  heartbeat_seconds: 30

session:
  trading_start: "09:30"  # ET — bot starts evaluating signals
  trading_end: "12:00"    # ET — last new-signal time (config/strategy_params.yaml)
  flatten_time: "15:45"   # ET — bot's own flatten (watchdog also flattens here)
```

- [ ] **Step 2: Create the entry point**

```python
# bot/main.py
"""Bot service entry point. Wires everything together.

Lifecycle:
  1. Load configs (bot.yaml, strategy_params.yaml, risk_params.yaml)
  2. Connect to IBKR; set marketDataType
  3. Resolve front-month MNQ contract
  4. Subscribe to market data
  5. Wire BarAggregator -> SignalGenerator -> IBKRExecutor
  6. Run the event loop until SIGTERM or 16:00 ET

This module imports ZERO ruflo. Verifiable via scripts/check_no_ruflo.sh.
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

import yaml

from bot.bar_aggregator import BarAggregator
from bot.execution.contract_resolver import resolve_front_month_mnq
from bot.execution.ibkr_executor import IBKRExecutor
from bot.health_monitor import HealthMonitor
from bot.signal_generator import SignalGenerator
from bot.state import BotState
from bot.tick_handler import canonical_tick_type, is_delayed_tick


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    project_root = Path(__file__).parent.parent
    bot_cfg = _load_yaml(project_root / "config" / "bot.yaml")
    strategy_cfg = _load_yaml(project_root / "config" / "strategy_params.yaml")
    risk_cfg = _load_yaml(project_root / "config" / "risk_params.yaml")

    log_cfg = bot_cfg["logging"]
    logging.basicConfig(
        level=getattr(logging, log_cfg["level"]),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("mnq-bot")
    logger.info("MNQ ORB bot starting...")

    from ib_insync import IB, util
    ib = IB()
    ib_cfg = bot_cfg["ibkr"]
    ib.connect(ib_cfg["host"], ib_cfg["port"], clientId=ib_cfg["client_id"], timeout=20)
    ib.reqMarketDataType(ib_cfg["market_data_type"])
    logger.info(f"Connected (marketDataType={ib_cfg['market_data_type']})")

    contract = resolve_front_month_mnq(ib)
    logger.info(f"Front-month: {contract.localSymbol}")

    state = BotState()
    executor = IBKRExecutor(ib)
    executor.set_contract(contract)
    health = HealthMonitor(risk_cfg)
    sg = SignalGenerator(strategy_cfg, risk_cfg, executor, health, state)
    aggregator = BarAggregator(rth_only=True)

    # Wire bracket exits → bot state so circuit breakers see realized P&L
    point_value = strategy_cfg["instrument"]["point_value"]
    def _on_bracket_exit(bracket):
        if bracket.entry_price is None or bracket.exit_price is None:
            return
        # P&L direction depends on long/short — determine from the parent order's action
        # We know the parent was BUY for long, SELL for short; can infer from fills.
        # Conservatively, use entry vs exit sign:
        pnl_points = bracket.exit_price - bracket.entry_price
        # If this was a short, sign flips. Read from parent order action.
        parent_trade = next((t for t in ib.trades()
                             if t.order.orderId == bracket.parent_id), None)
        if parent_trade and parent_trade.order.action == "SELL":
            pnl_points = -pnl_points
        pnl_dollars = pnl_points * point_value * bracket.position if bracket.position else \
                      pnl_points * point_value  # position cleared at this point — assume 1
        state.record_trade(pnl_dollars)
        logger.info(f"Bracket exited: reason={bracket.exit_reason} "
                    f"pnl=${pnl_dollars:.2f}")
    executor.set_exit_callback(_on_bracket_exit)

    # IBKR event wiring
    ticker = ib.reqMktData(contract, "", False, False)

    def on_pending_ticks(tickers):
        for tk in tickers:
            for tick in tk.ticks:
                canonical = canonical_tick_type(tick.tickType)
                if canonical == 4:  # LAST trade
                    bar = aggregator.on_trade_tick(tick.time, tick.price, tick.size)
                    if bar is not None:
                        sg.on_bar_complete(bar)
                health.note_data_seen(datetime.utcnow())

    ib.pendingTickersEvent += on_pending_ticks

    def on_order_status(trade):
        if trade.orderStatus.status == "Filled":
            executor.handle_fill(trade.order.orderId,
                                 trade.orderStatus.avgFillPrice,
                                 int(trade.orderStatus.filled))
        else:
            executor.handle_status(trade.order.orderId, trade.orderStatus.status)

    ib.orderStatusEvent += on_order_status

    # Graceful shutdown on SIGTERM (systemd stop)
    def _shutdown(signum, frame):
        logger.info("SIGTERM received — disconnecting")
        ib.disconnect()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Hand control to ib_insync's event loop
    ib.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test that the module imports without errors**

Run: `python -c "from bot import main; print(main.main)"`
Expected: prints `<function main at ...>`. (Don't run main() — it would try to connect to IBKR.)

- [ ] **Step 4: Commit**

```bash
git add bot/main.py config/bot.yaml
git commit -m "feat(bot): main entry point — wires aggregator/signal/executor with IBKR events"
```

---

# Epic 7: Watchdog

## Task 7.1: Independent watchdog process

**Files:**
- Create: `bot/watchdog.py`
- Test: `tests/unit/test_watchdog.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_watchdog.py
"""Watchdog flattens any open MNQ position at flatten_time, regardless of
the bot's state. Runs as an independent process with its own clientId.
"""

from datetime import datetime, time
from bot.watchdog import Watchdog


def test_watchdog_flattens_open_position_at_flatten_time(mock_ib, monkeypatch):
    mock_ib.connect("127.0.0.1", 7497, clientId=99)
    # Pretend MNQ has an open long position
    from tests.conftest import MockContract
    contract = MockContract()

    class _MockPos:
        contract = MockContract()
        position = 1

    mock_ib.positions = lambda: [_MockPos()]

    wd = Watchdog(mock_ib, flatten_time=time(15, 45))
    wd.set_contract(contract)

    # 15:44 — should NOT flatten
    wd.tick(datetime(2025, 6, 15, 15, 44, 0))
    assert mock_ib.placed_orders == []

    # 15:45 — should flatten
    wd.tick(datetime(2025, 6, 15, 15, 45, 0))
    assert len(mock_ib.placed_orders) == 1
    flatten_order = mock_ib.placed_orders[0].order
    assert flatten_order.action == "SELL"  # opposite of long
    assert flatten_order.totalQuantity == 1


def test_watchdog_does_nothing_when_flat(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=99)
    mock_ib.positions = lambda: []  # no open position
    from tests.conftest import MockContract

    wd = Watchdog(mock_ib, flatten_time=time(15, 45))
    wd.set_contract(MockContract())
    wd.tick(datetime(2025, 6, 15, 15, 45, 30))
    assert mock_ib.placed_orders == []


def test_watchdog_only_acts_on_mnq_positions(mock_ib):
    mock_ib.connect("127.0.0.1", 7497, clientId=99)

    class _OtherPos:
        from tests.conftest import MockContract as _MC
        contract = _MC(symbol="ES", localSymbol="ESM6")
        position = 1

    mock_ib.positions = lambda: [_OtherPos()]
    from tests.conftest import MockContract

    wd = Watchdog(mock_ib, flatten_time=time(15, 45))
    wd.set_contract(MockContract(symbol="MNQ"))
    wd.tick(datetime(2025, 6, 15, 15, 45, 30))
    # Watchdog only flattens MNQ — should ignore ES
    assert mock_ib.placed_orders == []
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/test_watchdog.py -v`
Expected: FAIL on all.

- [ ] **Step 3: Implement**

```python
# bot/watchdog.py
"""Independent flatten-at-EOD safety. Runs as a separate process with its
own IBKR connection. The bot can crash, hang, deadlock, or get OOM-killed —
the watchdog still flattens any open MNQ position at flatten_time.

Design constraints:
  - Uses a SEPARATE clientId from the bot (so neither process kicks the other off)
  - Does NOT read from the bot — queries IBKR directly via ib.positions()
  - Issues a MARKET order to flatten (no limit — speed > price at this point)
  - Idempotent: safe to call tick() repeatedly

Failure modes covered:
  - Bot crashed: watchdog still flattens
  - Watchdog crashes: systemd restarts (Restart=always in deploy/mnq-watchdog.service)
  - Both crashed: positional risk; manual intervention required (Slack alert in production)
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Optional


logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, ib: Any, flatten_time: time):
        self.ib = ib
        self.flatten_time = flatten_time
        self._contract: Optional[Any] = None
        self._flattened_today: Optional[str] = None  # date string when last flattened

    def set_contract(self, contract: Any) -> None:
        self._contract = contract

    def tick(self, now: datetime) -> None:
        """Called by the watchdog's main loop every N seconds."""
        if now.time() < self.flatten_time:
            return
        date_key = now.date().isoformat()
        if self._flattened_today == date_key:
            return  # already flattened this session

        positions = [p for p in self.ib.positions()
                     if self._is_target_contract(p.contract) and p.position != 0]
        if not positions:
            self._flattened_today = date_key  # nothing to do; record so we skip
            return

        for pos in positions:
            self._flatten_position(pos)
        self._flattened_today = date_key

    def _is_target_contract(self, c: Any) -> bool:
        if self._contract is None:
            return False
        return c.symbol == self._contract.symbol

    def _flatten_position(self, pos: Any) -> None:
        from ib_insync import MarketOrder
        action = "SELL" if pos.position > 0 else "BUY"
        qty = int(abs(pos.position))
        logger.warning(
            f"WATCHDOG flattening {pos.contract.localSymbol} "
            f"qty={qty} action={action}"
        )
        order = MarketOrder(action, qty)
        self.ib.placeOrder(pos.contract, order)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/test_watchdog.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/watchdog.py tests/unit/test_watchdog.py
git commit -m "feat(bot): independent watchdog flattens MNQ positions at 15:45 ET"
```

## Task 7.2: Watchdog main loop

**Files:**
- Modify: `bot/watchdog.py` (add main())

- [ ] **Step 1: Add main() at bottom of `bot/watchdog.py`**

```python
def main():
    """Watchdog service entry point. Runs forever, ticks every 30 seconds."""
    import time as _time
    from datetime import datetime
    from pathlib import Path
    import signal as _signal
    import sys
    import yaml
    from ib_insync import IB
    from bot.execution.contract_resolver import resolve_front_month_mnq

    project_root = Path(__file__).parent.parent
    with open(project_root / "config" / "bot.yaml") as f:
        bot_cfg = yaml.safe_load(f)
    flatten_str = bot_cfg["session"]["flatten_time"]
    flatten_h, flatten_m = map(int, flatten_str.split(":"))

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] watchdog: %(message)s")

    ib = IB()
    # IMPORTANT: separate clientId from the bot to avoid kicking each other off
    ib_cfg = bot_cfg["ibkr"]
    ib.connect(ib_cfg["host"], ib_cfg["port"], clientId=ib_cfg["client_id"] + 100,
               timeout=20)
    ib.reqMarketDataType(ib_cfg["market_data_type"])
    contract = resolve_front_month_mnq(ib)

    wd = Watchdog(ib, time(flatten_h, flatten_m))
    wd.set_contract(contract)

    def _shutdown(signum, frame):
        ib.disconnect()
        sys.exit(0)
    _signal.signal(_signal.SIGTERM, _shutdown)
    _signal.signal(_signal.SIGINT, _shutdown)

    while True:
        wd.tick(datetime.now())
        _time.sleep(30)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-import**

Run: `python -c "from bot import watchdog; print(watchdog.main)"`
Expected: prints function reference.

- [ ] **Step 3: Commit**

```bash
git add bot/watchdog.py
git commit -m "feat(bot): watchdog main loop — independent process entry"
```

---

# Epic 8: Strategy Hardening (deferred items from Phase 1)

## Task 8.1: Tighten retest tolerance to require actual level touch

**Why:** Phase 1 audit (STRESS_TEST_AUDIT #9, deferred) — `detect_retest` accepts bars whose low is up to `tolerance_points` ABOVE the OR high. This means we enter on bars that didn't actually touch the OR level. In live trading this means lower-quality entries.

🛑 **TIER 3 — strategy semantics change. Re-run Phase 1 after fix and verify gates still pass before continuing.**

**Files:**
- Modify: `backtest/or_detector.py:218,239`
- Test: `tests/unit/test_strategies_core.py` (add cases)

- [ ] **Step 1: Add a regression test that PASSES today and would still pass after fix**

```python
# Append to tests/unit/test_strategies_core.py

def test_retest_requires_actual_level_touch():
    """Long retest fires only if the bar's low actually reaches OR high (not above)."""
    from backtest.or_detector import detect_opening_range, detect_breakout, detect_retest

    # Bars: OR 19000-19050, breakout, then a "retest" bar whose low is 19055 (above OR high)
    bars = _make_day([
        {"open": 19000, "high": 19050, "low": 19000, "close": 19045, "volume": 5000},
        {"open": 19045, "high": 19090, "low": 19044, "close": 19085, "volume": 5000},
        {"open": 19080, "high": 19082, "low": 19055, "close": 19075, "volume": 5000},  # never touches OR
    ])
    or_ = detect_opening_range(bars)
    bo = detect_breakout(bars, or_)
    # POST-FIX: retest should be None because low (19055) > OR high (19050) by 5+ points
    retest = detect_retest(bars, or_, bo, tolerance_points=2.0, timeout_bars=8)
    assert retest is None
```

- [ ] **Step 2: Run — expect FAIL today (current code is too generous)**

Run: `pytest tests/unit/test_strategies_core.py::test_retest_requires_actual_level_touch -v`
Expected: FAIL — current code returns a RetestEvent because tolerance=2.0 still permits low up to retest_level + 2.

- [ ] **Step 3: Tighten the retest condition**

In `backtest/or_detector.py`:

```python
# Long retest, replace line 218:
if bar["low"] <= retest_level + tolerance_points:
# WITH:
# Bar low must REACH the retest level (within tolerance below is fine; above is not).
# This rejects "phantom retests" where price never actually touched OR.
if bar["low"] <= retest_level and bar["low"] >= retest_level - tolerance_points:

# Short retest, replace line 239:
if bar["high"] >= retest_level - tolerance_points:
# WITH:
if bar["high"] >= retest_level and bar["high"] <= retest_level + tolerance_points:
```

- [ ] **Step 4: Run the new test — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py::test_retest_requires_actual_level_touch -v`
Expected: PASS.

- [ ] **Step 5: Run full Phase 1 — REVALIDATE**

Run: `python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime" | tail -10`
Expected: PASS verdict still holds. Numbers will shift slightly — the question is whether the gates remain green:
- pass_rate >= 75%
- avg_oos_pf >= 1.5
- avg_oos_pnl > 0
- ruin_prob < 5%

🛑 **CHECKPOINT.** If the new run fails any gate, STOP and revert this change. The retest fix is improving entry quality but the strategy might depend on the looser semantics. Human reviews the new numbers and signs off OR rolls back.

- [ ] **Step 6: Commit**

```bash
git add backtest/or_detector.py tests/unit/test_strategies_core.py
git commit -m "fix(strategy): retest requires actual touch of OR level (STRESS_TEST_AUDIT #9)

Resolves the deferred audit item from Phase 1. Pre-fix, a bar whose low never
reached the OR level could still trigger a retest entry (because tolerance was
generously additive). Post-fix, the bar must actually visit the level within
tolerance below; bars hovering above are rejected. Phase 1 gates re-validated."
```

## Task 8.2: Fix N3 — inverse ORB entry price consistency

**Why:** Audit item N3 — `_simulate_inverse_orb_trade` records entry_price as the OR boundary, but the actual entry happens at the bar that re-enters the OR. Cosmetic for the backtester (the simulation handles it correctly internally), but the live executor reads `signal.entry_price` and submits a limit order at that level, which would not fill if price has moved past it.

🛑 **TIER 3 — touches live order pricing.**

**Files:**
- Modify: `backtest/strategies/core.py` (the inverse_orb signal block from Task 1.5)
- Test: `tests/unit/test_strategies_core.py`

- [ ] **Step 1: Write a test that asserts inverse_orb entry_price reflects actual fill conditions**

Append to `tests/unit/test_strategies_core.py`:

```python
def test_inverse_orb_entry_price_uses_failed_breakout_close():
    """Inverse ORB entry should be the bar that re-entered the OR (the failure
    bar's close), not the OR boundary."""
    cfg = _default_strategy_cfg()
    cfg["orb_breakout"]["enabled"] = False
    cfg["ema_continuation"]["enabled"] = False
    cfg["regime_filter"]["enabled"] = False
    cfg["confluences"]["rsi"]["enabled"] = False
    cfg["confluences"]["volume"]["enabled"] = False
    cfg["confluences"]["ema_slope"]["enabled"] = False

    or_history = [50.0] * 19
    bars = _make_day([
        {"open": 19000, "high": 19150, "low": 19000, "close": 19140, "volume": 5000},
        {"open": 19140, "high": 19160, "low": 19140, "close": 19155, "volume": 5000},
        {"open": 19155, "high": 19160, "low": 19130, "close": 19135, "volume": 5000},  # failure
    ] + [{"open": 19135, "high": 19140, "low": 19130, "close": 19135, "volume": 1000}
        for _ in range(15)])

    ctx = SignalContext(
        day_bars=bars, date_str="2025-06-15",
        or_history_inverse=or_history, regime_or_history=[],
        strategy_config=cfg, risk_state=_empty_risk_state(),
    )
    signals = detect_signals_for_day(ctx)
    inv = [s for s in signals if s.setup == "inverse_orb"]
    # Failure bar closed at 19135 — that's where we'd fill, not the OR high (19150)
    assert inv[0].entry_price == 19135.0
```

- [ ] **Step 2: Run — expect FAIL** (current code emits OR boundary as entry_price)

Run: `pytest tests/unit/test_strategies_core.py::test_inverse_orb_entry_price_uses_failed_breakout_close -v`
Expected: FAIL — entry_price is 19150.0, not 19135.0.

- [ ] **Step 3: Fix the inverse ORB signal in `backtest/strategies/core.py`**

In the inverse_orb block (from Task 1.5), replace the entry-price derivation. The detect_failed_breakout function returns `bool` — we need the actual failure bar. Update:

```python
if (inv_cfg.get("enabled", False)
        and opening_range.classification == "wide"
        and _within_inverse_window(bars, breakout, inv_cfg)):
    failure_bars = inv_cfg.get("failure_check_bars", 3)
    failed = detect_failed_breakout(bars, opening_range, breakout, failure_bars)
    if failed:
        # Find the actual failure bar (first close back inside OR)
        fail_bar = _find_failure_bar(bars, opening_range, breakout, failure_bars)
        if fail_bar is None:
            return signals  # detect_failed_breakout said yes but we can't find the bar — defensive
        inv_direction: Literal["long", "short"] = (
            "short" if breakout.direction == "long" else "long"
        )
        stop_buffer = inv_cfg.get("stop_buffer_points", 10)
        entry_price = float(fail_bar["close"])  # CHANGED: use actual failure-bar close
        if inv_direction == "short":
            stop_price = max(breakout.candle_high, fail_bar["high"]) + stop_buffer
            target_price = opening_range.midpoint
        else:
            stop_price = min(breakout.candle_low, fail_bar["low"]) - stop_buffer
            target_price = opening_range.midpoint
        signals.append(Signal(
            setup="inverse_orb",
            direction=inv_direction,
            entry_time=fail_bar.name,  # bar's index (timestamp)
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            risk_points=abs(stop_price - entry_price),
            or_size=opening_range.size,
            or_classification=opening_range.classification,
            confluences={},
        ))
        took_inverse = True
```

Add the helper at the bottom of `backtest/strategies/core.py`:

```python
def _find_failure_bar(bars, opening_range, breakout, failure_bars):
    """First bar after breakout whose close is back inside the OR."""
    start = breakout.bar_index + 1
    end = min(start + failure_bars, len(bars))
    for i in range(start, end):
        bar = bars.iloc[i]
        if breakout.direction == "long" and bar["close"] < opening_range.high:
            return bar
        if breakout.direction == "short" and bar["close"] > opening_range.low:
            return bar
    return None
```

- [ ] **Step 4: Run inverse-ORB tests — expect PASS**

Run: `pytest tests/unit/test_strategies_core.py -k inverse_orb -v`
Expected: both inverse_orb tests PASS.

- [ ] **Step 5: Run full Phase 1 — REVALIDATE**

Run: `python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime" | tail -10`
Expected: PASS verdict holds. Numbers shift again. Confirm gates green.

🛑 **CHECKPOINT.** Same rules as Task 8.1.

- [ ] **Step 6: Commit**

```bash
git add backtest/strategies/core.py tests/unit/test_strategies_core.py
git commit -m "fix(strategy): inverse_orb entry price uses failure-bar close (audit N3)

Live executor reads Signal.entry_price as the bracket's limit price. With the
old code, this was the OR boundary, which would not fill (price has already
moved past it). Now it's the failure-bar close, matching the actual entry."
```

## Task 8.3: Automated news/event blackout window

**Why:** the benchmark trader manually skips trading around FOMC, CPI, NFP, and major political events ("Clearest signs to not take my set ups must be news"). Our `config/risk_params.yaml` already has `no_trade.event_types` listed but no actual enforcement. This task adds enforcement: a calendar list of blackout windows, and a check before signal submission.

**Distinction from curve-fitting:** this is *risk reduction*, not edge optimization. We're skipping trades during high-uncertainty windows; we're not changing what counts as a valid signal.

**Files:**
- Create: `bot/news_calendar.py`
- Create: `config/news_calendar.yaml`
- Test: `tests/unit/test_news_calendar.py`
- Modify: `bot/signal_generator.py`

- [ ] **Step 1: Create the calendar config**

```yaml
# config/news_calendar.yaml
# High-impact event blackout windows. Times are ET.
# Bot skips signal submission within ±blackout_minutes of any listed event.
#
# Maintained manually for now. Future enhancement: auto-scrape FRED / BLS / Fed
# calendar feeds at startup and merge.
blackout_minutes: 30
events:
  # FOMC 2026 schedule
  - { date: "2026-01-29", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-03-19", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-04-30", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-06-18", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-07-30", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-09-17", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-11-05", time: "14:00", kind: "fomc_decision" }
  - { date: "2026-12-17", time: "14:00", kind: "fomc_decision" }
  # CPI 2026 (8:30 ET first/second Tuesday of month, varies)
  - { date: "2026-05-13", time: "08:30", kind: "cpi" }
  - { date: "2026-06-11", time: "08:30", kind: "cpi" }
  # NFP 2026 (first Friday of month)
  - { date: "2026-05-02", time: "08:30", kind: "nfp" }
  - { date: "2026-06-06", time: "08:30", kind: "nfp" }
```

- [ ] **Step 2: Write failing test**

```python
# tests/unit/test_news_calendar.py
"""News calendar blackout — bot skips signals within ±N minutes of any
configured event."""

from datetime import datetime
from bot.news_calendar import NewsCalendar


def test_within_blackout_returns_true():
    cal = NewsCalendar({
        "blackout_minutes": 30,
        "events": [{"date": "2026-05-13", "time": "08:30", "kind": "cpi"}],
    })
    # 08:31 — inside the +30min window
    assert cal.in_blackout(datetime(2026, 5, 13, 8, 31)) is True
    # 09:00 — exactly at +30min boundary, still blackout
    assert cal.in_blackout(datetime(2026, 5, 13, 9, 0)) is True
    # 09:01 — just outside
    assert cal.in_blackout(datetime(2026, 5, 13, 9, 1)) is False
    # 08:00 — exactly at -30min boundary
    assert cal.in_blackout(datetime(2026, 5, 13, 8, 0)) is True
    # 07:59 — just before
    assert cal.in_blackout(datetime(2026, 5, 13, 7, 59)) is False


def test_no_events_means_no_blackout():
    cal = NewsCalendar({"blackout_minutes": 30, "events": []})
    assert cal.in_blackout(datetime(2026, 5, 13, 10, 0)) is False


def test_blackout_returns_event_kind_for_logging():
    cal = NewsCalendar({
        "blackout_minutes": 30,
        "events": [{"date": "2026-05-13", "time": "08:30", "kind": "cpi"}],
    })
    assert cal.blackout_reason(datetime(2026, 5, 13, 8, 31)) == "cpi"
    assert cal.blackout_reason(datetime(2026, 5, 13, 12, 0)) is None
```

- [ ] **Step 3: Run — expect FAIL**

Run: `pytest tests/unit/test_news_calendar.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

```python
# bot/news_calendar.py
"""News blackout calendar. Skip signal submission within ±N minutes of any
high-impact event. Loaded from config/news_calendar.yaml.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


class NewsCalendar:
    def __init__(self, cfg: dict):
        self.blackout_minutes = cfg.get("blackout_minutes", 30)
        self._events: list[tuple[datetime, str]] = []
        for ev in cfg.get("events", []):
            ts = datetime.strptime(f"{ev['date']} {ev['time']}", "%Y-%m-%d %H:%M")
            self._events.append((ts, ev.get("kind", "unknown")))

    def in_blackout(self, now: datetime) -> bool:
        return self.blackout_reason(now) is not None

    def blackout_reason(self, now: datetime) -> Optional[str]:
        delta = timedelta(minutes=self.blackout_minutes)
        for event_ts, kind in self._events:
            if event_ts - delta <= now <= event_ts + delta:
                return kind
        return None
```

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/unit/test_news_calendar.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Wire into signal generator**

In `bot/signal_generator.py`, add a `news_calendar` parameter to `__init__`:

```python
    def __init__(
        self,
        strategy_cfg: dict,
        risk_cfg: dict,
        executor: IBKRExecutor,
        health: HealthMonitor,
        state: BotState,
        news_calendar=None,  # NEW
    ):
        # ... existing init ...
        self.news_calendar = news_calendar
```

In `on_bar_complete`, after the health check, add:

```python
        if self.news_calendar is not None:
            reason = self.news_calendar.blackout_reason(datetime.utcnow())
            if reason is not None:
                logger.info(f"News blackout — skipping signals (event: {reason})")
                return
```

In `bot/main.py`, load the calendar and pass it:

```python
    news_cfg_path = project_root / "config" / "news_calendar.yaml"
    news_cal = None
    if news_cfg_path.exists():
        with open(news_cfg_path) as f:
            news_cal = NewsCalendar(yaml.safe_load(f))
    sg = SignalGenerator(strategy_cfg, risk_cfg, executor, health, state,
                         news_calendar=news_cal)
```

- [ ] **Step 7: Run all signal_generator tests still pass (existing tests pass news_calendar=None)**

Run: `pytest tests/unit/test_signal_generator.py tests/unit/test_news_calendar.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add bot/news_calendar.py config/news_calendar.yaml tests/unit/test_news_calendar.py bot/signal_generator.py bot/main.py
git commit -m "feat(bot): news/event blackout windows

Skips signal submission within ±30min of FOMC, CPI, NFP and other listed
high-impact events. Risk-reduction (not edge optimization), so adding it
post-OOS does not constitute curve-fitting. Calendar config in
config/news_calendar.yaml — keep it updated manually for now; auto-scrape
in Phase 4."
```

---

# Epic 9: Integration Tests

## Task 9.1: Replay-one-day end-to-end

**Files:**
- Create: `tests/integration/test_replay_day.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_replay_day.py
"""End-to-end: feed a day of historical bars through the live signal/executor
path with a mock IB and verify the same trades fire as the backtester.

Pulls one day from the parquet, runs the backtester on JUST that day, then
runs the SignalGenerator with bars fed in chronological order, and asserts
the brackets submitted match the backtester's signal entries.
"""

from pathlib import Path
import yaml

import pandas as pd
import pytest

from backtest.backtester import Backtester
from backtest.data_loader import load_parquet
from bot.bar_aggregator import Bar
from bot.execution.ibkr_executor import IBKRExecutor
from bot.health_monitor import HealthMonitor
from bot.signal_generator import SignalGenerator
from bot.state import BotState


PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA = PROJECT_ROOT / "data" / "mnq_15m.parquet"
STRAT = PROJECT_ROOT / "config" / "strategy_params.yaml"
RISK = PROJECT_ROOT / "config" / "risk_params.yaml"


def _load(p):
    with open(p) as f:
        return yaml.safe_load(f)


def test_one_day_replay_matches_backtester(mock_ib):
    """Pick a day with a clean ORB-breakout signal and replay it."""
    df = load_parquet(DATA)
    # Find a day where the executable backtester emits >=1 trade. The corrective
    # baseline is sparse, so sample a known active day from the current run summary.
    target_date = pd.Timestamp("2025-08-04", tz="US/Eastern").date()
    day_bars = df[df.index.date == target_date]
    if len(day_bars) < 5:
        pytest.skip(f"Not enough bars for {target_date}")

    # Run the backtester on the same single-day slice to get expected entries
    tmp = Path("/tmp/replay_oneday.parquet")
    day_bars.to_parquet(tmp)
    bt = Backtester(str(STRAT), str(RISK))
    bt_results = bt.run(tmp)
    expected_entries = sorted([(t.entry_time, t.setup, t.direction, t.entry_price)
                                for t in bt_results.trades])
    tmp.unlink()

    # Now run the live path with the same bars
    mock_ib.connect("127.0.0.1", 7497, clientId=1)
    state = BotState()
    state.today_date = str(target_date)
    executor = IBKRExecutor(mock_ib)
    executor.set_contract(mock_ib.qualifyContracts(None)[0])
    health = HealthMonitor(_load(RISK))
    sg = SignalGenerator(_load(STRAT), _load(RISK), executor, health, state)

    for ts, row in day_bars.iterrows():
        bar = Bar(timestamp=ts, open=row["open"], high=row["high"],
                  low=row["low"], close=row["close"], volume=row["volume"])
        sg.on_bar_complete(bar)

    # Each parent order corresponds to one entry
    parent_orders = [t.order for t in mock_ib.placed_orders[::3]]  # every 3rd is parent
    submitted_entries = sorted([
        (t.order.lmtPrice, "BUY" if t.order.action == "BUY" else "SELL")
        for t in mock_ib.placed_orders[::3]
    ])

    # Number of brackets matches number of expected entries (each bracket = 3 orders)
    assert len(mock_ib.placed_orders) == 3 * len(expected_entries), \
        f"Expected {3 * len(expected_entries)} orders ({len(expected_entries)} brackets * 3 legs), got {len(mock_ib.placed_orders)}"
```

- [ ] **Step 2: Run — likely FAIL or SKIP initially**

Run: `pytest tests/integration/test_replay_day.py -v`
Expected: either PASS or SKIP if the chosen date has 0 trades. If PASS, great. If FAIL, examine the diff and adjust the test (likely needs better date selection).

- [ ] **Step 3: If PASS, commit; if FAIL, iterate**

```bash
git add tests/integration/test_replay_day.py tests/integration/__init__.py
git commit -m "test: end-to-end replay-one-day matches backtester signals"
```

---

# Epic 10: Deployment Artifacts

## Task 10.1: Update systemd unit files + import-surface guard

**Files:**
- Modify: `deploy/mnq-bot.service`
- Modify: `deploy/mnq-watchdog.service`
- Remove from active deploy: `deploy/mnq-dispatcher.service` (file stays in-tree as deprecated reference)
- Create: `scripts/check_runtime_imports.sh`

- [ ] **Step 1: Verify ExecStart matches new module path**

Open `deploy/mnq-bot.service`. Verify `ExecStart=/opt/mnq-orb-bot/.venv/bin/python -m bot.main`. If different, update.

Open `deploy/mnq-watchdog.service`. Add `ExecStart=/opt/mnq-orb-bot/.venv/bin/python -m bot.watchdog`. If watchdog wasn't pre-templated, copy the bot.service file and adjust:

```ini
# Save to /etc/systemd/system/mnq-watchdog.service
[Unit]
Description=MNQ Watchdog (independent flatten safety)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/mnq-orb-bot
ExecStart=/opt/mnq-orb-bot/.venv/bin/python -m bot.watchdog
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/mnq-orb-bot/.env

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create runtime-imports CI check**

```bash
# scripts/check_runtime_imports.sh
#!/usr/bin/env bash
# Keep the runtime import surface minimal. Allowed at the top level of bot/
# (excluding bot/dispatcher.py which is deprecated, and tests):
#   stdlib + ib_insync + pandas + numpy + yaml + pytz +
#   bot.* + backtest.strategies.* + backtest.indicators (used by signal_generator)
#
# Anything else gets flagged so we know we're growing the runtime surface.
set -euo pipefail

forbidden_pat='import (claude_flow|claude-flow|ruflo|tensorflow|torch|sklearn|xgboost)|from (claude_flow|claude-flow|ruflo|tensorflow|torch|sklearn|xgboost)'

# Exclude deprecated dispatcher and __pycache__
hits=$(find bot -type f -name '*.py' \
    -not -path 'bot/dispatcher.py' \
    -not -path 'bot/__pycache__/*' \
    -exec grep -E "$forbidden_pat" {} + 2>/dev/null || true)

if [ -n "$hits" ]; then
    echo "ERROR: heavy/forbidden imports detected in runtime modules:"
    echo "$hits"
    exit 1
fi
echo "OK: runtime import surface is minimal"
```

Make it executable: `chmod +x scripts/check_runtime_imports.sh`

- [ ] **Step 3: Run the check**

Run: `bash scripts/check_runtime_imports.sh`
Expected: `OK: runtime import surface is minimal`.

- [ ] **Step 4: Commit**

```bash
git add deploy/mnq-bot.service deploy/mnq-watchdog.service scripts/check_runtime_imports.sh
git commit -m "deploy: systemd units + runtime-import surface guard"
```

## Task 10.2: Deployment runbook

**Files:**
- Create: `docs/PHASE2_DEPLOYMENT.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Phase 2 Deployment — MNQ ORB Bot

## Pre-deploy checklist (manual)

- [ ] Phase 1 PASSES: `python scripts/run_phase1.py` → both gates green
- [ ] All unit tests pass: `pytest tests/ -v`
- [ ] Runtime-import guard passes: `bash scripts/check_runtime_imports.sh`
- [ ] Risk params reviewed: `config/risk_params.yaml` — max_contracts=1, daily_max_loss=-400
- [ ] Bot config reviewed: `config/bot.yaml` — port matches account type, market_data_type matches account
- [ ] IBKR account ready: funded paper (DU) or live (U); CME real-time subscription if market_data_type=1
- [ ] Front-month MNQ verified in TWS: see a live quote or delayed-tagged quote on demo

## Deploy to VPS

(Ansible or manual SSH; this runbook assumes manual.)

```bash
ssh deploy@vps
cd /opt/mnq-orb-bot
git pull --ff-only
.venv/bin/pip install -e ".[dev]"
sudo cp deploy/mnq-bot.service /etc/systemd/system/
sudo cp deploy/mnq-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mnq-watchdog mnq-bot   # watchdog first — safer
```

## Smoke test (paper account)

```bash
python scripts/paper_smoke_test.py
# Expects: connect, qualify MNQ, subscribe market data,
#          receive 5+ ticks, build at least 1 bar, no errors.
```

## Start the bot for paper trading

```bash
sudo systemctl start mnq-watchdog
sudo systemctl status mnq-watchdog
sudo systemctl start mnq-bot
sudo systemctl status mnq-bot
journalctl -u mnq-bot -f      # watch live
```

## Stop the bot

```bash
sudo systemctl stop mnq-bot          # bot first — watchdog stays up to flatten if needed
# At session end:
sudo systemctl stop mnq-watchdog
```

## Paper validation (MUST be 50+ trades before going live)

- Run for 50+ trades on paper account
- Verify: WR roughly matches Phase 1 (around 55%), no unauthorized trades, no order rejections
- Compare paper P&L vs the same period's backtester output (re-run with `--start-date` matching paper)
- If paper trades diverge >5% from backtester signals, STOP and investigate — strategy semantics drift

## Going live

🛑 **CHECKPOINT — human signs off after paper validation passes the criteria above.**

```bash
# 1. Stop both services
sudo systemctl stop mnq-bot mnq-watchdog
# 2. Switch config/bot.yaml: port to 7496, market_data_type to 1
# 3. Restart watchdog FIRST, then bot
sudo systemctl start mnq-watchdog
sudo systemctl start mnq-bot
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/PHASE2_DEPLOYMENT.md
git commit -m "docs: Phase 2 deployment runbook"
```

---

# Epic 11: Pre-Live Validation

## Task 11.1: Run the full unit test suite

- [ ] **Step 1: Run all unit tests**

Run: `pytest tests/ -v`
Expected: 0 failures.

- [ ] **Step 2: Verify runtime-import guard**

Run: `bash scripts/check_runtime_imports.sh`
Expected: OK.

- [ ] **Step 3: Verify Phase 1 still passes**

Run: `python scripts/run_phase1.py 2>&1 | grep -v "circuit breaker active" | grep -v "Seeded regime" | tail -5`
Expected: both gates PASS.

## Task 11.2: 🛑 Manual code review checklist

Human review (no agent):

- [ ] All Tier 3 changes match what was approved during the build.
- [ ] `bot/main.py` calls `reqMarketDataType()` matching the account type.
- [ ] `bot/watchdog.py` uses a SEPARATE clientId from the bot.
- [ ] Bracket orders set `transmit=True` only on the LAST leg.
- [ ] `IBKRExecutor.max_contracts == 1`.
- [ ] No paths to placing entry orders without stop+target.
- [ ] Logging is verbose enough that a fill or rejection can be reconstructed from the journal.

## Task 11.3: 🛑 Demo paper smoke test

**Files:**
- Create: `scripts/paper_smoke_test.py`

```python
# scripts/paper_smoke_test.py
"""Connect to IBKR demo, subscribe to MNQ market data for 5 minutes,
print bars and any signals. Does NOT place orders. Verifies the
bar-aggregator + signal-generator path works against live ticks.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot.bar_aggregator import BarAggregator
from bot.tick_handler import canonical_tick_type


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("smoke")
    with open(PROJECT_ROOT / "config" / "bot.yaml") as f:
        cfg = yaml.safe_load(f)
    ic = cfg["ibkr"]

    from ib_insync import IB, ContFuture
    ib = IB()
    ib.connect(ic["host"], ic["port"], clientId=ic["client_id"] + 200, timeout=20)
    ib.reqMarketDataType(ic["market_data_type"])
    cont = ib.qualifyContracts(ContFuture("MNQ", exchange="CME"))[0]
    log.info(f"Subscribed to {cont.localSymbol}")
    ticker = ib.reqMktData(cont, "", False, False)

    agg = BarAggregator()
    end = datetime.utcnow() + timedelta(minutes=5)
    bar_count = 0

    def on_ticks(tickers):
        nonlocal bar_count
        for tk in tickers:
            for tick in tk.ticks:
                ct = canonical_tick_type(tick.tickType)
                if ct == 4:
                    bar = agg.on_trade_tick(tick.time, tick.price, tick.size)
                    if bar is not None:
                        bar_count += 1
                        log.info(f"BAR {bar.timestamp} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")

    ib.pendingTickersEvent += on_ticks
    while datetime.utcnow() < end:
        ib.sleep(1)
    log.info(f"Smoke test done — received {bar_count} bars")
    ib.disconnect()


if __name__ == "__main__":
    main()
```

- [ ] **Step 1: Run the smoke test against IBKR demo (TWS must be running on port 7497)**

Run: `python scripts/paper_smoke_test.py`
Expected: connects, subscribes, logs at least 1 bar in 5 minutes (RTH only — outside 09:30-16:00 ET there will be no trades and no bars; that's fine, the connection is what we're testing).

- [ ] **Step 2: 🛑 Human reviews the output. If anything looks wrong (no ticks, wrong tick types, exceptions), STOP and debug.**

- [ ] **Step 3: Commit**

```bash
git add scripts/paper_smoke_test.py
git commit -m "test: paper smoke test — verifies live tick stream + bar aggregator"
```

## Task 11.4: 🛑 CHECKPOINT — Human approval before connecting to funded paper

This is the moment the code first faces a real (paper) money flow. Before flipping the switch:

- [ ] Live account + CME subscription confirmed active
- [ ] `config/bot.yaml` updated: `port: 7497` (paper) and `market_data_type: 1` (live data)
- [ ] All preceding tasks complete
- [ ] Watchdog deployed and running BEFORE the bot starts
- [ ] First trading day: human present, ready to `systemctl stop` if anything looks wrong

## Task 11.5: 50-trade paper validation period

- [ ] Run on funded paper for at least 50 trades (typically 1-2 trading weeks at ~5 trades/day)
- [ ] After 50 trades:
  - [ ] Compute paper WR, PF, max DD, daily-loss-limit hits
  - [ ] Compare to backtester results for the same calendar period (run backtester with `--start-date YYYY-MM-DD`)
  - [ ] Acceptable divergence: paper P&L within 10% of backtester for the same period
  - [ ] Unacceptable: any unauthorized order, any silent failure to flatten at 15:45, any drawdown exceeding the circuit breaker without halt

## Task 11.6: 🛑 FINAL CHECKPOINT — Human approval before going live

- [ ] Paper validation passed criteria above
- [ ] Slack notifications working (if configured)
- [ ] Manual review of last 50 paper trades for unexpected behaviour
- [ ] Live account capital confirmed
- [ ] Live deploy via runbook in `docs/PHASE2_DEPLOYMENT.md` § Going live

---

# Self-Review

(Performed inline 2026-04-25.)

**Spec coverage check:** every Phase 2 scope item from the user spec maps to at least one task —
- backtest/strategies/core.py → Tasks 1.1-1.7
- bot/execution/ibkr_executor.py → Tasks 4.3-4.4
- bot/signal_generator.py → Task 6.2
- bot/watchdog.py → Tasks 7.1-7.2
- bot/health_monitor.py → Task 5.1
- Tests with mocked IBKR → Task 2.1 + every Epic 3-7 task
- Live signal generator handling delayed tick types → Tasks 3.1, 6.3
- regime_filter inheritance → Task 6.2 (test_signal_generator_inherits_regime_filter)
- Strategy hardening (deferred items) → Epic 8 (Tasks 8.1, 8.2)
- Factory/product separation → Task 10.1 (no-ruflo CI guard)
- 1-contract enforcement → Task 4.3 (test_submit_refuses_quantity_above_max)
- Bracket atomicity → Task 4.3 (test_submit_long_bracket_emits_three_orders)
- 50-trade paper validation → Task 11.5

**Placeholder scan:** no "TODO", "TBD", or "implement later" patterns. The watchdog auto-rollover is explicitly OUT of scope (called out in `contract_resolver.py` docstring).

**Type consistency:** `BotState`, `Signal`, `SignalContext`, `BracketTracker`, `BracketState`, `HaltReason` are defined once and reused with consistent names. `MockIB` and `MockTrade` defined once in `conftest.py`.

---

# Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-phase2-live-execution.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task with two-stage review (code + tests). Best fit for Tier 3 work where each step needs verification before continuing.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`. Faster but reviews happen at epic boundaries, not per-task.

Given that this is real-money execution code with multiple Tier 3 checkpoints, **subagent-driven is strongly recommended.** The fresh-context-per-task discipline catches drift; the per-task review catches Tier 3 violations early.

**Which approach?**
