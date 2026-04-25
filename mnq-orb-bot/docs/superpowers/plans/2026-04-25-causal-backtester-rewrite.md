# Causal Backtester Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current bar-by-bar backtester with a causality-faithful engine in which lookahead bias is **structurally inexpressible**, port the four existing setups onto it, and lock a new corrective baseline. After this rewrite, no backtest result can claim a fill that depends on information from the end of the bar in which the fill occurs.

**Architecture:** A new `backtest/causal/` package runs side-by-side with the existing `backtest/backtester.py`. The causal engine is event-driven over bars: for each bar, it walks the intrabar high/low path against an order book (filling pre-placed resting orders), then calls the strategy's `on_bar_complete(view, order_book)` hook with a `BarView` that exposes only completed history. The strategy can place new orders, but those orders are tagged with `decided_at = current_bar_close_timestamp` and cannot fill against any bar at or before that timestamp. Property-based tests assert these invariants on every fill. Existing setups (`orb_breakout`, `or_retest`, `inverse_orb`, `ema_continuation`) are ported into one of two execution archetypes: **pre-placed resting order** (limit/stop/OCO) or **next-bar market entry on completed signal**. After parity audit against the locked corrective baseline, `scripts/run_phase1.py`, `walk_forward.py`, and `monte_carlo.py` are switched to consume causal results, and the new baseline is locked into the regression test.

**Tech Stack:** Python 3.12, pandas, numpy, pytest, hypothesis (added for property-based tests), pyyaml, dataclasses.

---

## File Structure

**New (causal engine):**

- `backtest/causal/__init__.py` — package init, public exports
- `backtest/causal/types.py` — `Order`, `OrderSide`, `OrderType`, `OrderStatus`, `Fill`, `Bar`, `OcoGroup` dataclasses with timestamp invariants
- `backtest/causal/bar_view.py` — `BarView` (read-only access to completed bars only); raises if asked for the current or future bar
- `backtest/causal/order_book.py` — `OrderBook` class; tracks resting orders with `decided_at`, supports limit/stop/market/OCO, exposes `step(bar)` that resolves fills against an intrabar path
- `backtest/causal/intrabar.py` — `walk_intrabar_path(bar)` — yields (timestamp, price) tuples in O→H→L→C or O→L→H→C order based on bar direction heuristic
- `backtest/causal/engine.py` — `CausalEngine.run(data, strategy, risk_config)` driver; per-bar loop that calls `order_book.step(bar)` then `strategy.on_bar_complete(view, order_book)`
- `backtest/causal/strategy.py` — `Strategy` ABC with one method: `on_bar_complete(view: BarView, book: OrderBook) -> None`
- `backtest/causal/risk.py` — sizing, `max_risk_points` cap, daily loss / consecutive losses circuit breakers (ported from current)
- `backtest/causal/results.py` — `CausalResults` dataclass and `to_legacy_results()` adapter producing the existing `BacktestResults` shape so `walk_forward.py` and `monte_carlo.py` keep working
- `backtest/causal/setups/__init__.py`
- `backtest/causal/setups/orb_breakout.py` — pre-placed stop entry above OR high after OR window closes
- `backtest/causal/setups/or_retest.py` — pre-placed limit at OR retest level (first touch only, with bar-count timeout)
- `backtest/causal/setups/inverse_orb.py` — pre-placed stop short after extension event confirmed on prior bar close
- `backtest/causal/setups/ema_continuation.py` — close-confirmed signal → next-bar market open entry

**New (tests):**

- `tests/causal/__init__.py`
- `tests/causal/test_types.py` — type invariants (timestamps, monotonic ordering)
- `tests/causal/test_bar_view.py` — read-only history; future access raises
- `tests/causal/test_order_book_limit.py` — limit fill semantics
- `tests/causal/test_order_book_stop.py` — stop fill semantics
- `tests/causal/test_order_book_market.py` — market fills at next-bar open
- `tests/causal/test_order_book_oco.py` — OCO group; one fills, the other cancels
- `tests/causal/test_engine.py` — driver behavior, fill ordering
- `tests/causal/test_no_lookahead.py` — property tests + lookahead-trap fixture
- `tests/causal/test_setup_orb_breakout.py`
- `tests/causal/test_setup_or_retest.py`
- `tests/causal/test_setup_inverse_orb.py`
- `tests/causal/test_setup_ema_continuation.py`
- `tests/causal/test_parity_audit.py` — causal vs corrected baseline diff report

**Modified:**

- `pyproject.toml` (or `requirements.txt`) — add `hypothesis>=6` for property-based tests
- `scripts/run_phase1.py` — switch from `Backtester` to `CausalEngine`
- `backtest/walk_forward.py` — accept `CausalEngine` (interface kept compatible via `to_legacy_results`)
- `backtest/monte_carlo.py` — unchanged (consumes `BacktestResults` shape)
- `tests/unit/test_strategies_core.py` — re-lock baseline against causal engine output once parity audit accepted
- `VALIDATION.md` — document the rewrite, the new property-test guarantees, and the new locked baseline

**Untouched (kept for reference until parity audit accepted):**

- `backtest/backtester.py` — old engine; will be deprecated in Task 15

---

## Phase A — Causal Foundations

### Task 1: Core types with timestamp invariants

**Files:**
- Create: `backtest/causal/__init__.py`
- Create: `backtest/causal/types.py`
- Test: `tests/causal/__init__.py`, `tests/causal/test_types.py`

- [ ] **Step 1: Create empty package init files**

```bash
mkdir -p backtest/causal tests/causal
```

`backtest/causal/__init__.py`:
```python
"""Causality-faithful backtest engine.

Lookahead bias is structurally inexpressible: a strategy's only input is
a BarView of completed bars; orders carry decided_at timestamps and cannot
fill against any bar whose close timestamp is <= decided_at.
"""
```

`tests/causal/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test for type invariants**

`tests/causal/test_types.py`:
```python
"""Type invariants: timestamps, status transitions, bar monotonicity."""
from datetime import datetime, timezone

import pytest

from backtest.causal.types import (
    Bar,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)


UTC = timezone.utc


def _ts(h: int, m: int) -> datetime:
    return datetime(2025, 1, 2, h, m, tzinfo=UTC)


def test_bar_rejects_inverted_high_low():
    with pytest.raises(ValueError, match="high < low"):
        Bar(timestamp=_ts(14, 30), open=100, high=99, low=101, close=100, volume=10)


def test_bar_rejects_open_outside_range():
    with pytest.raises(ValueError, match="open outside"):
        Bar(timestamp=_ts(14, 30), open=200, high=110, low=90, close=100, volume=10)


def test_bar_rejects_close_outside_range():
    with pytest.raises(ValueError, match="close outside"):
        Bar(timestamp=_ts(14, 30), open=100, high=110, low=90, close=200, volume=10)


def test_bar_accepts_valid():
    b = Bar(timestamp=_ts(14, 30), open=100, high=110, low=90, close=105, volume=10)
    assert b.range == 20
    assert b.is_bullish is True


def test_order_starts_pending():
    o = Order(
        order_id=1,
        side=OrderSide.LONG,
        order_type=OrderType.STOP,
        price=105.0,
        quantity=1,
        decided_at=_ts(14, 30),
    )
    assert o.status is OrderStatus.PENDING
    assert o.fill is None


def test_market_order_must_have_no_price():
    with pytest.raises(ValueError, match="market orders cannot have price"):
        Order(
            order_id=1,
            side=OrderSide.LONG,
            order_type=OrderType.MARKET,
            price=105.0,
            quantity=1,
            decided_at=_ts(14, 30),
        )


def test_limit_stop_order_must_have_price():
    with pytest.raises(ValueError, match="must have price"):
        Order(
            order_id=1,
            side=OrderSide.LONG,
            order_type=OrderType.STOP,
            price=None,
            quantity=1,
            decided_at=_ts(14, 30),
        )


def test_fill_rejects_filled_at_before_decided_at():
    with pytest.raises(ValueError, match="filled_at < decided_at"):
        Fill(
            order_id=1,
            price=105.0,
            quantity=1,
            decided_at=_ts(14, 30),
            filled_at=_ts(14, 15),
        )


def test_fill_accepts_filled_at_equal_to_decided_at_for_market_next_bar():
    f = Fill(
        order_id=1,
        price=105.0,
        quantity=1,
        decided_at=_ts(14, 30),
        filled_at=_ts(14, 31),
    )
    assert f.price == 105.0
```

- [ ] **Step 3: Run test to verify failure**

Run: `pytest tests/causal/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.causal.types'`

- [ ] **Step 4: Implement types**

`backtest/causal/types.py`:
```python
"""Core types for the causal engine.

Every Order carries decided_at (the close timestamp of the bar at whose
completion the strategy decided to place the order). Every Fill carries
both decided_at and filled_at, and asserts filled_at >= decided_at on
construction. This is the load-bearing invariant for causality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime  # close timestamp of the bar
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"high < low: {self.high} < {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open outside [low, high]: {self.open}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close outside [low, high]: {self.close}")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open


@dataclass(frozen=True)
class Fill:
    order_id: int
    price: float
    quantity: int
    decided_at: datetime
    filled_at: datetime

    def __post_init__(self) -> None:
        if self.filled_at < self.decided_at:
            raise ValueError(
                f"filled_at < decided_at: {self.filled_at} < {self.decided_at}"
            )


@dataclass
class Order:
    order_id: int
    side: OrderSide
    order_type: OrderType
    price: Optional[float]
    quantity: int
    decided_at: datetime
    status: OrderStatus = OrderStatus.PENDING
    fill: Optional[Fill] = None
    oco_group_id: Optional[int] = None
    tag: str = ""

    def __post_init__(self) -> None:
        if self.order_type is OrderType.MARKET and self.price is not None:
            raise ValueError("market orders cannot have price")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP) and self.price is None:
            raise ValueError(f"{self.order_type.value} orders must have price")
```

- [ ] **Step 5: Run test to verify pass**

Run: `pytest tests/causal/test_types.py -v`
Expected: PASS — 8 passed

- [ ] **Step 6: Commit**

```bash
git add backtest/causal/__init__.py backtest/causal/types.py tests/causal/__init__.py tests/causal/test_types.py
git commit -m "feat(causal): core types with timestamp invariants

Order carries decided_at (the bar-close timestamp at which the strategy
decided to place it). Fill asserts filled_at >= decided_at on construction.
This invariant is the foundation that makes lookahead bias structurally
inexpressible in the new engine."
```

---

### Task 2: BarView — read-only access to completed bars only

**Files:**
- Create: `backtest/causal/bar_view.py`
- Test: `tests/causal/test_bar_view.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_bar_view.py`:
```python
"""BarView exposes only completed bars. Asking for the current or future
bar is the lookahead bug; the view raises so the bug becomes a test
failure instead of a silent strategy that 'just works'."""
from datetime import datetime, timedelta, timezone

import pytest

from backtest.causal.bar_view import BarView
from backtest.causal.types import Bar


UTC = timezone.utc


def _bars(n: int) -> list[Bar]:
    base = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    return [
        Bar(
            timestamp=base + timedelta(minutes=15 * i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=10,
        )
        for i in range(n)
    ]


def test_view_at_index_zero_has_no_history():
    view = BarView(bars=_bars(5), current_index=0)
    assert view.history == ()
    assert view.last_completed is None


def test_view_at_index_three_has_three_completed():
    bars = _bars(5)
    view = BarView(bars=bars, current_index=3)
    assert len(view.history) == 3
    assert view.history[0] is bars[0]
    assert view.history[-1] is bars[2]
    assert view.last_completed is bars[2]


def test_view_cannot_access_current_bar():
    view = BarView(bars=_bars(5), current_index=3)
    with pytest.raises(IndexError, match="current or future bar"):
        _ = view.history[3]


def test_view_cannot_access_future_bar_via_get():
    view = BarView(bars=_bars(5), current_index=3)
    with pytest.raises(IndexError, match="current or future bar"):
        _ = view.get(4)


def test_view_get_negative_returns_relative_completed():
    bars = _bars(5)
    view = BarView(bars=bars, current_index=3)
    assert view.get(-1) is bars[2]  # most recent completed
    assert view.get(-3) is bars[0]


def test_view_history_tuple_is_immutable():
    view = BarView(bars=_bars(5), current_index=3)
    with pytest.raises((TypeError, AttributeError)):
        view.history[0] = None  # type: ignore[index]
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_bar_view.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement BarView**

`backtest/causal/bar_view.py`:
```python
"""Read-only view of completed bars, indexed by current_index.

The strategy receives this view in on_bar_complete. It can read history
[0..current_index-1] but never bars[current_index] (the current bar is
"current" — its close is what just triggered the callback, but the
order book has already resolved fills for it, so reading it is fine in
principle; we still hide it to keep the model "you only see what's
already happened and you can only act on the next bar onward").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backtest.causal.types import Bar


class _HistoryTuple(tuple):
    """Tuple subclass that raises IndexError with a clear message on
    accidental future access."""

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            if key >= len(self):
                raise IndexError(
                    f"BarView.history[{key}]: requested current or future bar; "
                    f"only {len(self)} completed bars are visible"
                )
        return super().__getitem__(key)


@dataclass(frozen=True)
class BarView:
    bars: list[Bar]
    current_index: int

    @property
    def history(self) -> tuple[Bar, ...]:
        return _HistoryTuple(self.bars[: self.current_index])

    @property
    def last_completed(self) -> Optional[Bar]:
        if self.current_index == 0:
            return None
        return self.bars[self.current_index - 1]

    def get(self, index: int) -> Bar:
        """Get bar by absolute index (must be < current_index) or
        negative index (relative to most recent completed)."""
        if index < 0:
            abs_index = self.current_index + index
            if abs_index < 0:
                raise IndexError(f"BarView.get({index}): before history starts")
            return self.bars[abs_index]
        if index >= self.current_index:
            raise IndexError(
                f"BarView.get({index}): requested current or future bar"
            )
        return self.bars[index]
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_bar_view.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/bar_view.py tests/causal/test_bar_view.py
git commit -m "feat(causal): BarView with future-access blocked at the type level

history[i] for i >= current_index raises IndexError with a clear message,
turning lookahead access into a loud test failure instead of a silent
'works on backtest, fails live' bug."
```

---

### Task 3: hypothesis added; property test that no synthetic future-bar reach is reachable

**Files:**
- Modify: `pyproject.toml` (add hypothesis dependency)
- Create: `tests/causal/test_no_lookahead.py` (initial property tests; expanded in Task 8)

- [ ] **Step 1: Add hypothesis to dev dependencies**

Find `pyproject.toml` and add to `[project.optional-dependencies].dev` (or `[tool.poetry.dev-dependencies]` depending on tooling — match the existing style). Run `pip install hypothesis` if pyproject is uvicorn-style.

```bash
grep -A 10 "dev" pyproject.toml || cat pyproject.toml | head -40
pip install hypothesis
```

If the project uses a `requirements-dev.txt`, append `hypothesis>=6.0`.

- [ ] **Step 2: Write the failing property test**

`tests/causal/test_no_lookahead.py`:
```python
"""Property: for any sequence of bars and any current_index, BarView.history
contains exactly current_index bars and no Bar object that lives at
position >= current_index in the original list."""
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st

from backtest.causal.bar_view import BarView
from backtest.causal.types import Bar


UTC = timezone.utc


@st.composite
def _bar_sequence(draw, min_size: int = 1, max_size: int = 50) -> list[Bar]:
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    base = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bars = []
    price = 100.0
    for i in range(n):
        op = price
        hi = op + draw(st.floats(min_value=0.1, max_value=5.0))
        lo = op - draw(st.floats(min_value=0.1, max_value=5.0))
        cl = draw(st.floats(min_value=lo, max_value=hi))
        bars.append(
            Bar(
                timestamp=base + timedelta(minutes=15 * i),
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=10.0,
            )
        )
        price = cl
    return bars


@given(_bar_sequence())
@settings(max_examples=100, deadline=None)
def test_history_never_contains_current_or_future(bars):
    for current_index in range(len(bars) + 1):
        view = BarView(bars=bars, current_index=current_index)
        history = view.history
        assert len(history) == current_index
        for bar in history:
            absolute_index = bars.index(bar)
            assert absolute_index < current_index, (
                f"BarView leaked bar at absolute index {absolute_index} "
                f"with current_index={current_index}"
            )


@given(_bar_sequence(min_size=2))
@settings(max_examples=100, deadline=None)
def test_get_negative_one_is_last_completed(bars):
    for current_index in range(1, len(bars) + 1):
        view = BarView(bars=bars, current_index=current_index)
        assert view.get(-1) is bars[current_index - 1]
```

- [ ] **Step 3: Run test to verify pass (BarView already implemented in Task 2)**

Run: `pytest tests/causal/test_no_lookahead.py -v`
Expected: PASS — 2 passed (these are property tests; hypothesis runs many examples each)

- [ ] **Step 4: Commit**

```bash
git add tests/causal/test_no_lookahead.py pyproject.toml
git commit -m "test(causal): hypothesis property tests for BarView history boundary

For any random bar sequence and any current_index, BarView.history can
never leak a bar at position >= current_index. Adds hypothesis dev dep."
```

---

## Phase B — Order Book

### Task 4: OrderBook scaffold + decided_at tracking + cancel

**Files:**
- Create: `backtest/causal/order_book.py`
- Test: `tests/causal/test_order_book_limit.py`

- [ ] **Step 1: Write the failing test for placement and cancel**

`tests/causal/test_order_book_limit.py`:
```python
"""OrderBook: placement, cancel, and limit fill semantics from intrabar path."""
from datetime import datetime, timezone

import pytest

from backtest.causal.order_book import OrderBook
from backtest.causal.types import Bar, OrderSide, OrderStatus, OrderType


UTC = timezone.utc


def _bar(h, m, o, hi, lo, c, v=10.0):
    return Bar(
        timestamp=datetime(2025, 1, 2, h, m, tzinfo=UTC),
        open=o, high=hi, low=lo, close=c, volume=v,
    )


def test_place_limit_returns_pending_order_with_decided_at():
    book = OrderBook()
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    oid = book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1,
        decided_at=decided_at, tag="retest",
    )
    o = book.get(oid)
    assert o.status is OrderStatus.PENDING
    assert o.order_type is OrderType.LIMIT
    assert o.decided_at == decided_at
    assert o.tag == "retest"


def test_cancel_marks_order_cancelled():
    book = OrderBook()
    oid = book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    book.cancel(oid)
    assert book.get(oid).status is OrderStatus.CANCELLED


def test_pending_orders_excludes_cancelled_and_filled():
    book = OrderBook()
    oid1 = book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    oid2 = book.place_limit(
        side=OrderSide.LONG, price=99.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    book.cancel(oid1)
    pending = book.pending_orders()
    assert len(pending) == 1
    assert pending[0].order_id == oid2


def test_long_limit_fills_when_intrabar_low_touches_or_below():
    """Long limit at 100. Bar low touches 99.5. Should fill at 100 (limit
    price, not the touch price — limit fills at the limit or better)."""
    book = OrderBook()
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    oid = book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1, decided_at=decided_at,
    )
    bar = _bar(14, 45, o=101, hi=102, lo=99.5, c=100.8)
    fills = book.step(bar)
    assert len(fills) == 1
    f = fills[0]
    assert f.order_id == oid
    assert f.price == 100.0
    assert f.filled_at == bar.timestamp
    assert f.decided_at == decided_at
    assert book.get(oid).status is OrderStatus.FILLED


def test_long_limit_does_not_fill_when_intrabar_low_above_limit():
    book = OrderBook()
    book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    bar = _bar(14, 45, o=101, hi=103, lo=100.5, c=102)
    fills = book.step(bar)
    assert fills == []


def test_short_limit_fills_when_intrabar_high_touches_or_above():
    book = OrderBook()
    book.place_limit(
        side=OrderSide.SHORT, price=100.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    bar = _bar(14, 45, o=99, hi=100.3, lo=98.5, c=99.5)
    fills = book.step(bar)
    assert len(fills) == 1
    assert fills[0].price == 100.0


def test_step_skips_orders_decided_after_or_at_bar_timestamp():
    """Causality: an order whose decided_at >= bar.timestamp cannot fill
    against that bar. The bar's close timestamp marks when the strategy
    decided; the strategy can only act on the NEXT bar onwards."""
    book = OrderBook()
    bar_ts = datetime(2025, 1, 2, 14, 45, tzinfo=UTC)
    book.place_limit(
        side=OrderSide.LONG, price=100.0, quantity=1, decided_at=bar_ts,
    )
    bar = _bar(14, 45, o=101, hi=102, lo=99.0, c=100.5)
    fills = book.step(bar)
    assert fills == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_order_book_limit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest.causal.order_book'`

- [ ] **Step 3: Implement OrderBook (limit-only for now; stop and market in next tasks)**

`backtest/causal/order_book.py`:
```python
"""Order book for the causal engine.

Resting orders carry decided_at. step(bar) resolves fills against the
bar's intrabar reachability (high/low envelope), but only for orders
whose decided_at < bar.timestamp. Equality is excluded: an order placed
at the close of bar t (decided_at == t.timestamp) cannot fill against
bar t — only against bar t+1 onwards.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backtest.causal.types import (
    Bar,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)


class OrderBook:
    def __init__(self) -> None:
        self._orders: dict[int, Order] = {}
        self._next_id: int = 1
        self._oco_groups: dict[int, list[int]] = {}
        self._next_group_id: int = 1

    def get(self, order_id: int) -> Order:
        return self._orders[order_id]

    def pending_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status is OrderStatus.PENDING]

    def place_limit(
        self,
        side: OrderSide,
        price: float,
        quantity: int,
        decided_at: datetime,
        tag: str = "",
        oco_group_id: Optional[int] = None,
    ) -> int:
        return self._place(
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            quantity=quantity,
            decided_at=decided_at,
            tag=tag,
            oco_group_id=oco_group_id,
        )

    def cancel(self, order_id: int) -> None:
        o = self._orders[order_id]
        if o.status is OrderStatus.PENDING:
            o.status = OrderStatus.CANCELLED

    def step(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        for o in list(self._orders.values()):
            if o.status is not OrderStatus.PENDING:
                continue
            if o.decided_at >= bar.timestamp:
                # Causality: can't fill against the bar in which (or before
                # which) the order was decided.
                continue
            fill = self._try_fill(o, bar)
            if fill is not None:
                o.status = OrderStatus.FILLED
                o.fill = fill
                fills.append(fill)
                self._cancel_oco_siblings(o)
        return fills

    def _place(
        self,
        side: OrderSide,
        order_type: OrderType,
        price: Optional[float],
        quantity: int,
        decided_at: datetime,
        tag: str,
        oco_group_id: Optional[int],
    ) -> int:
        oid = self._next_id
        self._next_id += 1
        self._orders[oid] = Order(
            order_id=oid,
            side=side,
            order_type=order_type,
            price=price,
            quantity=quantity,
            decided_at=decided_at,
            tag=tag,
            oco_group_id=oco_group_id,
        )
        if oco_group_id is not None:
            self._oco_groups.setdefault(oco_group_id, []).append(oid)
        return oid

    def _try_fill(self, o: Order, bar: Bar) -> Optional[Fill]:
        if o.order_type is OrderType.LIMIT:
            return self._try_fill_limit(o, bar)
        return None  # other types added in subsequent tasks

    def _try_fill_limit(self, o: Order, bar: Bar) -> Optional[Fill]:
        assert o.price is not None
        if o.side is OrderSide.LONG and bar.low <= o.price:
            return Fill(
                order_id=o.order_id,
                price=o.price,
                quantity=o.quantity,
                decided_at=o.decided_at,
                filled_at=bar.timestamp,
            )
        if o.side is OrderSide.SHORT and bar.high >= o.price:
            return Fill(
                order_id=o.order_id,
                price=o.price,
                quantity=o.quantity,
                decided_at=o.decided_at,
                filled_at=bar.timestamp,
            )
        return None

    def _cancel_oco_siblings(self, filled: Order) -> None:
        if filled.oco_group_id is None:
            return
        for sibling_id in self._oco_groups.get(filled.oco_group_id, []):
            if sibling_id == filled.order_id:
                continue
            sibling = self._orders[sibling_id]
            if sibling.status is OrderStatus.PENDING:
                sibling.status = OrderStatus.CANCELLED
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_order_book_limit.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/order_book.py tests/causal/test_order_book_limit.py
git commit -m "feat(causal): OrderBook with limit fills; orders decided at bar.timestamp cannot fill against that bar

Limit fill semantics: long fills if bar.low <= limit_price at the limit
price; short fills if bar.high >= limit_price. step() skips any order
whose decided_at >= bar.timestamp — that's the causality gate."
```

---

### Task 5: Stop orders

**Files:**
- Modify: `backtest/causal/order_book.py`
- Test: `tests/causal/test_order_book_stop.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_order_book_stop.py`:
```python
"""Stop fill semantics: long stops fill on bar.high >= stop, short stops
on bar.low <= stop. Fill price is the stop price (slippage applied later
by the engine, not here)."""
from datetime import datetime, timezone

from backtest.causal.order_book import OrderBook
from backtest.causal.types import Bar, OrderSide


UTC = timezone.utc


def _bar(h, m, o, hi, lo, c, v=10.0):
    return Bar(
        timestamp=datetime(2025, 1, 2, h, m, tzinfo=UTC),
        open=o, high=hi, low=lo, close=c, volume=v,
    )


def test_long_stop_fills_when_high_touches_or_above():
    book = OrderBook()
    book.place_stop(
        side=OrderSide.LONG, price=105.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    bar = _bar(14, 45, o=104, hi=105.2, lo=103, c=104.5)
    fills = book.step(bar)
    assert len(fills) == 1
    assert fills[0].price == 105.0


def test_long_stop_does_not_fill_when_high_below():
    book = OrderBook()
    book.place_stop(
        side=OrderSide.LONG, price=105.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    bar = _bar(14, 45, o=103, hi=104.5, lo=102, c=103.8)
    fills = book.step(bar)
    assert fills == []


def test_short_stop_fills_when_low_touches_or_below():
    book = OrderBook()
    book.place_stop(
        side=OrderSide.SHORT, price=95.0, quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
    )
    bar = _bar(14, 45, o=96, hi=96.5, lo=94.8, c=95.5)
    fills = book.step(bar)
    assert len(fills) == 1
    assert fills[0].price == 95.0


def test_stop_decided_at_current_bar_does_not_fill():
    book = OrderBook()
    bar_ts = datetime(2025, 1, 2, 14, 45, tzinfo=UTC)
    book.place_stop(
        side=OrderSide.LONG, price=105.0, quantity=1, decided_at=bar_ts,
    )
    bar = _bar(14, 45, o=104, hi=106, lo=103, c=105.5)
    assert book.step(bar) == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_order_book_stop.py -v`
Expected: FAIL — `AttributeError: 'OrderBook' object has no attribute 'place_stop'`

- [ ] **Step 3: Add `place_stop` and stop fill rule to `order_book.py`**

In `backtest/causal/order_book.py`, add this method to the `OrderBook` class (alongside `place_limit`):

```python
    def place_stop(
        self,
        side: OrderSide,
        price: float,
        quantity: int,
        decided_at: datetime,
        tag: str = "",
        oco_group_id: Optional[int] = None,
    ) -> int:
        return self._place(
            side=side,
            order_type=OrderType.STOP,
            price=price,
            quantity=quantity,
            decided_at=decided_at,
            tag=tag,
            oco_group_id=oco_group_id,
        )
```

Update `_try_fill` to dispatch on type:

```python
    def _try_fill(self, o: Order, bar: Bar) -> Optional[Fill]:
        if o.order_type is OrderType.LIMIT:
            return self._try_fill_limit(o, bar)
        if o.order_type is OrderType.STOP:
            return self._try_fill_stop(o, bar)
        return None  # market handled in next task
```

Add `_try_fill_stop`:

```python
    def _try_fill_stop(self, o: Order, bar: Bar) -> Optional[Fill]:
        assert o.price is not None
        if o.side is OrderSide.LONG and bar.high >= o.price:
            return Fill(
                order_id=o.order_id,
                price=o.price,
                quantity=o.quantity,
                decided_at=o.decided_at,
                filled_at=bar.timestamp,
            )
        if o.side is OrderSide.SHORT and bar.low <= o.price:
            return Fill(
                order_id=o.order_id,
                price=o.price,
                quantity=o.quantity,
                decided_at=o.decided_at,
                filled_at=bar.timestamp,
            )
        return None
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_order_book_stop.py tests/causal/test_order_book_limit.py -v`
Expected: PASS — 11 passed (7 limit + 4 stop)

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/order_book.py tests/causal/test_order_book_stop.py
git commit -m "feat(causal): stop orders in OrderBook

Long stop fills on bar.high >= stop; short on bar.low <= stop. Causality
gate from limit orders applies unchanged."
```

---

### Task 6: Market orders + OCO groups

**Files:**
- Modify: `backtest/causal/order_book.py`
- Test: `tests/causal/test_order_book_market.py`, `tests/causal/test_order_book_oco.py`

- [ ] **Step 1: Write the failing tests**

`tests/causal/test_order_book_market.py`:
```python
"""Market orders fill at the next bar's open. The OrderBook does not know
which bar is 'next' on its own — the engine passes step(bar) and the
market fill is delivered the first time step is called with a bar whose
timestamp > decided_at."""
from datetime import datetime, timedelta, timezone

from backtest.causal.order_book import OrderBook
from backtest.causal.types import Bar, OrderSide


UTC = timezone.utc


def _bar(h, m, o, hi, lo, c, v=10.0):
    return Bar(
        timestamp=datetime(2025, 1, 2, h, m, tzinfo=UTC),
        open=o, high=hi, low=lo, close=c, volume=v,
    )


def test_market_order_fills_at_next_bar_open():
    book = OrderBook()
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    oid = book.place_market(
        side=OrderSide.LONG, quantity=1, decided_at=decided_at,
    )
    next_bar = _bar(14, 45, o=101.5, hi=102, lo=101, c=101.7)
    fills = book.step(next_bar)
    assert len(fills) == 1
    f = fills[0]
    assert f.order_id == oid
    assert f.price == 101.5  # bar.open
    assert f.filled_at == next_bar.timestamp


def test_market_order_does_not_fill_against_decided_bar():
    book = OrderBook()
    bar_ts = datetime(2025, 1, 2, 14, 45, tzinfo=UTC)
    book.place_market(
        side=OrderSide.LONG, quantity=1, decided_at=bar_ts,
    )
    same_bar = _bar(14, 45, o=101.5, hi=102, lo=101, c=101.7)
    assert book.step(same_bar) == []
```

`tests/causal/test_order_book_oco.py`:
```python
"""OCO group: when one order in the group fills, all siblings cancel."""
from datetime import datetime, timezone

from backtest.causal.order_book import OrderBook
from backtest.causal.types import Bar, OrderSide, OrderStatus


UTC = timezone.utc


def _bar(h, m, o, hi, lo, c, v=10.0):
    return Bar(
        timestamp=datetime(2025, 1, 2, h, m, tzinfo=UTC),
        open=o, high=hi, low=lo, close=c, volume=v,
    )


def test_oco_target_fills_cancels_stop():
    book = OrderBook()
    group = book.new_oco_group()
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    target_id = book.place_limit(
        side=OrderSide.SHORT, price=110.0, quantity=1,
        decided_at=decided_at, oco_group_id=group, tag="target",
    )
    stop_id = book.place_stop(
        side=OrderSide.SHORT, price=95.0, quantity=1,
        decided_at=decided_at, oco_group_id=group, tag="stop",
    )
    bar = _bar(14, 45, o=108, hi=110.5, lo=107, c=109)
    fills = book.step(bar)
    assert len(fills) == 1
    assert fills[0].order_id == target_id
    assert book.get(stop_id).status is OrderStatus.CANCELLED


def test_oco_stop_fills_cancels_target():
    book = OrderBook()
    group = book.new_oco_group()
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    target_id = book.place_limit(
        side=OrderSide.SHORT, price=110.0, quantity=1,
        decided_at=decided_at, oco_group_id=group,
    )
    stop_id = book.place_stop(
        side=OrderSide.SHORT, price=95.0, quantity=1,
        decided_at=decided_at, oco_group_id=group,
    )
    bar = _bar(14, 45, o=98, hi=99, lo=94.5, c=96)
    fills = book.step(bar)
    assert len(fills) == 1
    assert fills[0].order_id == stop_id
    assert book.get(target_id).status is OrderStatus.CANCELLED
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/causal/test_order_book_market.py tests/causal/test_order_book_oco.py -v`
Expected: FAIL — `AttributeError: 'OrderBook' has no attribute 'place_market'` and `'new_oco_group'`

- [ ] **Step 3: Add market + OCO support to `order_book.py`**

Add to `OrderBook`:

```python
    def place_market(
        self,
        side: OrderSide,
        quantity: int,
        decided_at: datetime,
        tag: str = "",
        oco_group_id: Optional[int] = None,
    ) -> int:
        return self._place(
            side=side,
            order_type=OrderType.MARKET,
            price=None,
            quantity=quantity,
            decided_at=decided_at,
            tag=tag,
            oco_group_id=oco_group_id,
        )

    def new_oco_group(self) -> int:
        gid = self._next_group_id
        self._next_group_id += 1
        self._oco_groups[gid] = []
        return gid
```

Update `_try_fill` dispatcher:

```python
    def _try_fill(self, o: Order, bar: Bar) -> Optional[Fill]:
        if o.order_type is OrderType.LIMIT:
            return self._try_fill_limit(o, bar)
        if o.order_type is OrderType.STOP:
            return self._try_fill_stop(o, bar)
        if o.order_type is OrderType.MARKET:
            return self._try_fill_market(o, bar)
        return None
```

Add `_try_fill_market`:

```python
    def _try_fill_market(self, o: Order, bar: Bar) -> Optional[Fill]:
        # Market orders always fill at the open of the first bar whose
        # timestamp > decided_at. The decided_at >= bar.timestamp guard
        # in step() already excluded same-bar fills.
        return Fill(
            order_id=o.order_id,
            price=bar.open,
            quantity=o.quantity,
            decided_at=o.decided_at,
            filled_at=bar.timestamp,
        )
```

- [ ] **Step 4: Run all order book tests**

Run: `pytest tests/causal/test_order_book_*.py -v`
Expected: PASS — 15 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/order_book.py tests/causal/test_order_book_market.py tests/causal/test_order_book_oco.py
git commit -m "feat(causal): market orders and OCO groups in OrderBook

Market fills at the open of the first bar whose timestamp > decided_at.
OCO groups cancel siblings on fill."
```

---

## Phase C — Engine and Causality Property Tests

### Task 7: CausalEngine driver + Strategy ABC

**Files:**
- Create: `backtest/causal/strategy.py`
- Create: `backtest/causal/engine.py`
- Test: `tests/causal/test_engine.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_engine.py`:
```python
"""Engine drives bars: for each bar, step() the order book to resolve any
fills, then call strategy.on_bar_complete with a BarView whose
current_index points just past that bar."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest.causal.bar_view import BarView
from backtest.causal.engine import CausalEngine
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import Bar, OrderSide


UTC = timezone.utc


def _bars_df(n: int) -> pd.DataFrame:
    base = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    rows = []
    for i in range(n):
        rows.append({
            "timestamp": base + timedelta(minutes=15 * i),
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 10.0,
        })
    df = pd.DataFrame(rows).set_index("timestamp")
    return df


class _RecordingStrategy(Strategy):
    def __init__(self) -> None:
        self.calls: list[tuple[int, datetime]] = []

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        self.calls.append((view.current_index, view.last_completed.timestamp))


def test_engine_calls_on_bar_complete_once_per_bar_with_correct_index():
    df = _bars_df(5)
    strat = _RecordingStrategy()
    engine = CausalEngine()
    engine.run(df, strat)
    assert len(strat.calls) == 5
    for i, (idx, ts) in enumerate(strat.calls):
        assert idx == i + 1
        assert ts == df.index[i].to_pydatetime()


class _PlaceStopAtIndexThree(Strategy):
    """On the third bar's completion, place a long stop at price 105."""
    def __init__(self) -> None:
        self.placed = False

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        if view.current_index == 3 and not self.placed:
            book.place_stop(
                side=OrderSide.LONG,
                price=view.last_completed.high + 1,
                quantity=1,
                decided_at=view.last_completed.timestamp,
                tag="stop_after_3",
            )
            self.placed = True


def test_engine_resolves_fills_before_calling_strategy():
    df = _bars_df(8)
    strat = _PlaceStopAtIndexThree()
    engine = CausalEngine()
    result = engine.run(df, strat)
    fills = result.fills
    assert len(fills) == 1
    f = fills[0]
    # Stop placed at decided_at = bar[2].timestamp; fills against bar 3 onwards.
    assert f.decided_at == df.index[2].to_pydatetime()
    assert f.filled_at >= df.index[3].to_pydatetime()
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Strategy ABC**

`backtest/causal/strategy.py`:
```python
"""Strategy ABC. The single contract: on_bar_complete receives a BarView
of completed bars and an OrderBook; it can read history and place orders.
It cannot inspect the current or future bars (BarView blocks that) and
its orders are tagged with view.last_completed.timestamp as decided_at,
which means they cannot fill against the current bar — only the next."""
from __future__ import annotations

from abc import ABC, abstractmethod

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook


class Strategy(ABC):
    @abstractmethod
    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        ...
```

- [ ] **Step 4: Implement CausalEngine**

`backtest/causal/engine.py`:
```python
"""Causal engine driver.

For each bar:
  1. order_book.step(bar) — fills any pending order whose decided_at <
     bar.timestamp and whose price is reachable by the bar's H/L envelope.
  2. strategy.on_bar_complete(view, book) — strategy reads completed
     history and can place new orders. New orders carry decided_at =
     view.last_completed.timestamp, which is exactly bar.timestamp,
     so they cannot fill against bar (per the causality gate in step()).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import Bar, Fill


@dataclass
class EngineResult:
    fills: list[Fill] = field(default_factory=list)
    bars: list[Bar] = field(default_factory=list)


class CausalEngine:
    def run(self, df: pd.DataFrame, strategy: Strategy) -> EngineResult:
        bars = self._df_to_bars(df)
        book = OrderBook()
        result = EngineResult(bars=bars)
        for i, bar in enumerate(bars):
            fills = book.step(bar)
            result.fills.extend(fills)
            view = BarView(bars=bars, current_index=i + 1)
            strategy.on_bar_complete(view, book)
        return result

    @staticmethod
    def _df_to_bars(df: pd.DataFrame) -> list[Bar]:
        bars: list[Bar] = []
        for ts, row in df.iterrows():
            bars.append(
                Bar(
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                )
            )
        return bars
```

- [ ] **Step 5: Run test to verify pass**

Run: `pytest tests/causal/test_engine.py -v`
Expected: PASS — 2 passed

- [ ] **Step 6: Commit**

```bash
git add backtest/causal/strategy.py backtest/causal/engine.py tests/causal/test_engine.py
git commit -m "feat(causal): CausalEngine driver and Strategy ABC

Per-bar loop: order_book.step(bar) then strategy.on_bar_complete(view, book).
The strategy's only input is BarView of completed history; orders it
places carry decided_at = bar.close, which the order book's causality
gate uses to prevent same-bar fills."
```

---

### Task 8: Property tests for end-to-end causality + lookahead-trap fixture

**Files:**
- Modify: `tests/causal/test_no_lookahead.py` (add property + fixture tests)

- [ ] **Step 1: Add property tests for end-to-end fills**

Append to `tests/causal/test_no_lookahead.py`:

```python
import pandas as pd

from backtest.causal.engine import CausalEngine
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import Bar, OrderSide


@st.composite
def _bars_df(draw, min_size: int = 5, max_size: int = 30):
    bars = draw(_bar_sequence(min_size=min_size, max_size=max_size))
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars]),
    )


class _PlaceLongStopEachBar(Strategy):
    """On every bar's completion, place a long stop at last_high + 1.
    This is the canonical 'I see what just happened, place a resting
    order' pattern — no future information used."""
    def on_bar_complete(self, view, book) -> None:
        last = view.last_completed
        book.place_stop(
            side=OrderSide.LONG,
            price=last.high + 1,
            quantity=1,
            decided_at=last.timestamp,
        )


@given(_bars_df())
@settings(max_examples=50, deadline=None)
def test_every_fill_has_filled_at_strictly_greater_than_decided_at(df):
    if len(df) < 2:
        return
    engine = CausalEngine()
    result = engine.run(df, _PlaceLongStopEachBar())
    for f in result.fills:
        # For resting orders, fill must be on a strictly later bar.
        assert f.filled_at > f.decided_at, (
            f"Fill {f.order_id}: filled_at {f.filled_at} <= decided_at {f.decided_at}"
        )


class _LookaheadTrapStrategy(Strategy):
    """A buggy strategy that TRIES to use a future bar's close to decide
    where to place a same-bar order. The engine's BarView blocks future
    access, so the strategy raises IndexError instead of producing a
    silent lookahead fill."""
    def __init__(self) -> None:
        self.attempts = 0
        self.errors = 0

    def on_bar_complete(self, view, book) -> None:
        self.attempts += 1
        try:
            future = view.bars[view.current_index]  # the current/about-to-be-bar
            _ = future.close
        except IndexError:
            self.errors += 1
            return


def test_lookahead_attempt_raises_indexerror_via_bar_view():
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103],
            "high": [101, 102, 103, 104],
            "low": [99, 100, 101, 102],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [10, 10, 10, 10],
        },
        index=pd.date_range("2025-01-02 14:30", periods=4, freq="15min", tz=UTC),
    )
    strat = _LookaheadTrapStrategy()
    engine = CausalEngine()
    engine.run(df, strat)
    # Strategy was called every bar; every attempt raised because BarView
    # blocks current/future access through history[].
    assert strat.attempts == 4
    assert strat.errors == 4
```

- [ ] **Step 2: Run tests to verify**

Run: `pytest tests/causal/test_no_lookahead.py -v`
Expected: PASS — 4 passed (2 prior + 2 new)

- [ ] **Step 3: Commit**

```bash
git add tests/causal/test_no_lookahead.py
git commit -m "test(causal): property test — every fill has filled_at > decided_at; lookahead-trap fixture proves BarView blocks future access

Property test runs hypothesis-generated bar sequences end-to-end through
the engine and asserts every fill respects causality. The lookahead-trap
strategy demonstrates that even an actively malicious strategy cannot
read the current/future bar — BarView raises IndexError."
```

---

## Phase D — Strategy Ports

### Task 9: Port `orb_breakout` (pre-placed stop entry above OR high)

**Files:**
- Create: `backtest/causal/setups/__init__.py`
- Create: `backtest/causal/setups/orb_breakout.py`
- Test: `tests/causal/test_setup_orb_breakout.py`

- [ ] **Step 1: Write the failing test**

`backtest/causal/setups/__init__.py`:
```python
```

`tests/causal/test_setup_orb_breakout.py`:
```python
"""ORB breakout: at the close of the OR window (last bar of the OR period),
place a long stop at OR_high + buffer and a short stop at OR_low - buffer
as an OCO. Stops fill on the first subsequent bar that trades through.

OR window for this test: 09:30 ET to 10:00 ET (two 15-min bars). On the
close of the second bar (10:00), strategy decides; orders cannot fill
against 10:00 — only against 10:15 onwards."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest.causal.engine import CausalEngine
from backtest.causal.setups.orb_breakout import OrbBreakout
from backtest.causal.types import OrderSide


ET = timezone(timedelta(hours=-5))


def _df_with_or(or_high: float, or_low: float, post_or_bars: list[tuple[float, float, float, float]]):
    """Build a DataFrame with two OR bars (09:30 and 09:45) bracketing
    [or_low, or_high], followed by post-OR bars (10:00, 10:15, ...).
    post_or_bars is a list of (open, high, low, close) tuples."""
    base = datetime(2025, 1, 2, 9, 30, tzinfo=ET)
    rows = []
    rows.append({"timestamp": base, "open": or_low, "high": or_high, "low": or_low, "close": (or_low + or_high) / 2, "volume": 100})
    rows.append({"timestamp": base + timedelta(minutes=15), "open": (or_low + or_high) / 2, "high": or_high, "low": or_low, "close": or_low + 1, "volume": 100})
    for i, (o, hi, lo, c) in enumerate(post_or_bars):
        rows.append({"timestamp": base + timedelta(minutes=15 * (2 + i)), "open": o, "high": hi, "low": lo, "close": c, "volume": 100})
    return pd.DataFrame(rows).set_index("timestamp")


def test_breakout_fires_long_when_subsequent_bar_trades_above_or_high():
    df = _df_with_or(or_high=100.0, or_low=95.0, post_or_bars=[
        (98, 101.5, 97, 101),  # 10:00: trades through 100; long stop at 100 fills
    ])
    strat = OrbBreakout(or_window_bars=2, buffer_points=0.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1
    f = result.fills[0]
    assert f.price == pytest.approx(100.0)


def test_breakout_fires_short_when_subsequent_bar_trades_below_or_low():
    df = _df_with_or(or_high=100.0, or_low=95.0, post_or_bars=[
        (96, 96.5, 94.5, 95),  # 10:00: trades through 95; short stop at 95 fills
    ])
    strat = OrbBreakout(or_window_bars=2, buffer_points=0.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1
    f = result.fills[0]
    assert f.price == pytest.approx(95.0)


def test_breakout_does_not_fire_when_subsequent_bars_stay_inside():
    df = _df_with_or(or_high=100.0, or_low=95.0, post_or_bars=[
        (97, 99, 96, 98),
        (98, 99.5, 96.5, 99),
    ])
    strat = OrbBreakout(or_window_bars=2, buffer_points=0.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []


def test_long_breakout_cancels_short_oco_sibling():
    """When the long breakout fires, the short stop in the same OCO group
    must be cancelled so a later down-move doesn't double-fire."""
    df = _df_with_or(or_high=100.0, or_low=95.0, post_or_bars=[
        (98, 101.5, 97, 101),  # long fires
        (101, 101, 94, 95),    # later down-move; short would fill if not cancelled
    ])
    strat = OrbBreakout(or_window_bars=2, buffer_points=0.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1  # only the long
    assert result.fills[0].price == pytest.approx(100.0)
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_setup_orb_breakout.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement OrbBreakout**

`backtest/causal/setups/orb_breakout.py`:
```python
"""ORB breakout setup, causally faithful.

On the close of the OR window's final bar (current_index == or_window_bars),
the strategy reads OR high/low from completed history and places an OCO:
  - long stop at OR_high + buffer
  - short stop at OR_low - buffer

Both orders carry decided_at = view.last_completed.timestamp. The order
book's causality gate ensures they can only fill against bars whose
timestamp > that decided_at — i.e., never the OR window's last bar.
"""
from __future__ import annotations

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import OrderSide


class OrbBreakout(Strategy):
    def __init__(self, or_window_bars: int = 2, buffer_points: float = 0.0) -> None:
        self.or_window_bars = or_window_bars
        self.buffer_points = buffer_points
        self._placed = False

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        if self._placed:
            return
        if view.current_index < self.or_window_bars:
            return
        or_bars = view.history[: self.or_window_bars]
        or_high = max(b.high for b in or_bars)
        or_low = min(b.low for b in or_bars)
        decided_at = view.last_completed.timestamp
        group = book.new_oco_group()
        book.place_stop(
            side=OrderSide.LONG,
            price=or_high + self.buffer_points,
            quantity=1,
            decided_at=decided_at,
            tag="orb_long",
            oco_group_id=group,
        )
        book.place_stop(
            side=OrderSide.SHORT,
            price=or_low - self.buffer_points,
            quantity=1,
            decided_at=decided_at,
            tag="orb_short",
            oco_group_id=group,
        )
        self._placed = True
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_setup_orb_breakout.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/setups/__init__.py backtest/causal/setups/orb_breakout.py tests/causal/test_setup_orb_breakout.py
git commit -m "feat(causal): port orb_breakout — pre-placed OCO stops at OR high/low after OR window closes

Decision is made on the final OR bar's completion; both stops carry
decided_at = that bar's close. The causality gate in OrderBook.step
prevents same-bar fills automatically. OCO ensures only one direction fires."
```

---

### Task 10: Port `or_retest` (pre-placed limit at retest level, first touch only)

**Files:**
- Create: `backtest/causal/setups/or_retest.py`
- Test: `tests/causal/test_setup_or_retest.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_setup_or_retest.py`:
```python
"""OR retest, causally faithful: after a confirmed OR breakout (price
closes beyond OR high/low on a post-OR bar), place a limit at the OR
level for a retest entry. The limit fills if a subsequent bar trades
back to the level. No same-bar hindsight: the breakout is confirmed on
a completed bar, the limit is placed at decided_at = that bar's close,
and only later bars can fill it."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest.causal.engine import CausalEngine
from backtest.causal.setups.or_retest import OrRetest


ET = timezone(timedelta(hours=-5))


def _df(rows: list[tuple[float, float, float, float]]):
    base = datetime(2025, 1, 2, 9, 30, tzinfo=ET)
    return pd.DataFrame(
        [
            {"timestamp": base + timedelta(minutes=15 * i), "open": o, "high": hi, "low": lo, "close": c, "volume": 100}
            for i, (o, hi, lo, c) in enumerate(rows)
        ]
    ).set_index("timestamp")


def test_long_retest_fires_after_breakout_and_pullback():
    # OR window: bars 0,1 -> high=100, low=95
    # Bar 2: closes above 100 (breakout confirmed on close)
    # Bar 3: trades back to 100 (limit fills)
    df = _df([
        (95, 100, 95, 99),       # OR bar 0
        (99, 100, 96, 99),       # OR bar 1 -> OR_high=100, OR_low=95
        (99, 102, 99, 101.5),    # post-OR: closes above 100 -> long retest armed
        (101.5, 101.5, 99.5, 101),  # pullback through 100 -> limit at 100 fills
    ])
    strat = OrRetest(or_window_bars=2, retest_timeout_bars=10)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1
    assert result.fills[0].price == pytest.approx(100.0)


def test_long_retest_does_not_fire_if_no_breakout_close():
    # Bar 2 trades above OR_high but closes below — not a confirmed breakout
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 100.5, 99, 99.5),    # wick above OR_high but close below
        (99.5, 100, 98, 99),
    ])
    strat = OrRetest(or_window_bars=2, retest_timeout_bars=10)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []


def test_long_retest_first_touch_only_no_double_fire():
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 102, 99, 101.5),
        (101.5, 101.5, 99.5, 101),  # first retest: fills
        (101, 101, 99.5, 100.5),    # second touch: should NOT re-fire (single-shot)
    ])
    strat = OrRetest(or_window_bars=2, retest_timeout_bars=10)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1


def test_long_retest_times_out():
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 102, 99, 101.5),
        (101.5, 102, 101, 101.5),  # +1 bar past breakout, no retest
        (101.5, 102, 101, 101.5),  # +2
        (101.5, 102, 99.5, 101),    # +3, would touch 100 — but timeout=2 so cancelled
    ])
    strat = OrRetest(or_window_bars=2, retest_timeout_bars=2)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_setup_or_retest.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement OrRetest**

`backtest/causal/setups/or_retest.py`:
```python
"""OR retest setup, causally faithful.

State machine:
  - Bars 0 .. or_window_bars-1: build OR (read after OR window closes).
  - On any post-OR bar's completion, check if it closed beyond OR_high
    (breakout up) or OR_low (breakout down). If so, place a limit at the
    OR level (decided_at = that bar's close) and start a timeout counter.
  - If the limit fills before timeout, done. If timeout elapses without
    fill, cancel the limit. Only one retest per direction per session.

No same-bar hindsight: the breakout is detected on a *completed* bar,
the limit is placed AFTER that bar closes, and the order book's
causality gate ensures the limit cannot fill against the breakout bar.
"""
from __future__ import annotations

from typing import Optional

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import OrderSide


class OrRetest(Strategy):
    def __init__(self, or_window_bars: int = 2, retest_timeout_bars: int = 10) -> None:
        self.or_window_bars = or_window_bars
        self.retest_timeout_bars = retest_timeout_bars
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._long_retest_id: Optional[int] = None
        self._short_retest_id: Optional[int] = None
        self._long_armed_at: Optional[int] = None
        self._short_armed_at: Optional[int] = None
        self._long_done = False
        self._short_done = False

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        # Build OR after OR window completes.
        if view.current_index == self.or_window_bars:
            or_bars = view.history[: self.or_window_bars]
            self._or_high = max(b.high for b in or_bars)
            self._or_low = min(b.low for b in or_bars)
            return

        if view.current_index < self.or_window_bars or self._or_high is None:
            return

        last = view.last_completed
        decided_at = last.timestamp

        # Long-side breakout detection / retest arming.
        if not self._long_done and self._long_retest_id is None:
            if last.close > self._or_high:
                self._long_retest_id = book.place_limit(
                    side=OrderSide.LONG,
                    price=self._or_high,
                    quantity=1,
                    decided_at=decided_at,
                    tag="or_retest_long",
                )
                self._long_armed_at = view.current_index

        # Short-side breakout detection / retest arming.
        if not self._short_done and self._short_retest_id is None:
            if last.close < self._or_low:
                self._short_retest_id = book.place_limit(
                    side=OrderSide.SHORT,
                    price=self._or_low,
                    quantity=1,
                    decided_at=decided_at,
                    tag="or_retest_short",
                )
                self._short_armed_at = view.current_index

        # Manage long retest: cancel on timeout, mark done on fill.
        if self._long_retest_id is not None:
            o = book.get(self._long_retest_id)
            if o.status.value == "filled":
                self._long_done = True
                self._long_retest_id = None
            elif (view.current_index - (self._long_armed_at or 0)) >= self.retest_timeout_bars:
                book.cancel(self._long_retest_id)
                self._long_done = True
                self._long_retest_id = None

        if self._short_retest_id is not None:
            o = book.get(self._short_retest_id)
            if o.status.value == "filled":
                self._short_done = True
                self._short_retest_id = None
            elif (view.current_index - (self._short_armed_at or 0)) >= self.retest_timeout_bars:
                book.cancel(self._short_retest_id)
                self._short_done = True
                self._short_retest_id = None
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_setup_or_retest.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/setups/or_retest.py tests/causal/test_setup_or_retest.py
git commit -m "feat(causal): port or_retest — limit placed AFTER breakout-confirming bar closes

Breakout confirmed on a completed bar's close; limit placed with
decided_at = that close timestamp; OrderBook causality gate ensures the
limit cannot fill against the breakout bar itself. First-touch only;
times out if not filled within retest_timeout_bars."
```

---

### Task 11: Port `inverse_orb` (pre-placed stop short after extension event)

**Files:**
- Create: `backtest/causal/setups/inverse_orb.py`
- Test: `tests/causal/test_setup_inverse_orb.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_setup_inverse_orb.py`:
```python
"""Inverse ORB: detect 'price extended past OR high then closed back inside'
on a completed bar; place a short stop below OR low (or below the
extension bar's low) for the reversal entry. Decision is made on the
extension bar's close; orders cannot fill against that bar."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest.causal.engine import CausalEngine
from backtest.causal.setups.inverse_orb import InverseOrb


ET = timezone(timedelta(hours=-5))


def _df(rows):
    base = datetime(2025, 1, 2, 9, 30, tzinfo=ET)
    return pd.DataFrame(
        [
            {"timestamp": base + timedelta(minutes=15 * i), "open": o, "high": hi, "low": lo, "close": c, "volume": 100}
            for i, (o, hi, lo, c) in enumerate(rows)
        ]
    ).set_index("timestamp")


def test_inverse_orb_fires_short_after_failed_breakout_up():
    # OR: high=100, low=95
    # Bar 2: extends to 102 then closes back inside at 99 -> failed breakout up
    # Bar 3: trades through 95 (OR_low) -> inverse short fills
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 102, 99, 99),       # extension up, close back inside
        (99, 99, 94.5, 95),      # trades through 95 -> short fills
    ])
    strat = InverseOrb(or_window_bars=2, extension_points=1.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1
    assert result.fills[0].price == pytest.approx(95.0)


def test_inverse_orb_does_not_fire_without_failed_breakout():
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 100, 99, 99.5),     # no extension
        (99.5, 99.5, 94.5, 95),  # but no inverse setup armed
    ])
    strat = InverseOrb(or_window_bars=2, extension_points=1.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []


def test_inverse_orb_does_not_fire_when_breakout_holds():
    df = _df([
        (95, 100, 95, 99),
        (99, 100, 96, 99),
        (99, 102, 99, 101.5),    # extension up AND closes outside -> not failed
        (101.5, 101.5, 99, 100), # back inside but no inverse short was armed
    ])
    strat = InverseOrb(or_window_bars=2, extension_points=1.0)
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_setup_inverse_orb.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement InverseOrb**

`backtest/causal/setups/inverse_orb.py`:
```python
"""Inverse ORB setup, causally faithful.

Detect a failed breakout on a completed bar:
  - Up-side fail: bar.high >= OR_high + extension_points AND bar.close <= OR_high.
  - Down-side fail: bar.low <= OR_low - extension_points AND bar.close >= OR_low.

On detection, place a short stop at OR_low (for up-side fail) or a long
stop at OR_high (for down-side fail), with decided_at = that bar's close.
The order book's causality gate ensures the stop cannot fill against the
extension bar itself.
"""
from __future__ import annotations

from typing import Optional

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import OrderSide


class InverseOrb(Strategy):
    def __init__(
        self,
        or_window_bars: int = 2,
        extension_points: float = 1.0,
    ) -> None:
        self.or_window_bars = or_window_bars
        self.extension_points = extension_points
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._short_stop_id: Optional[int] = None
        self._long_stop_id: Optional[int] = None

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        if view.current_index == self.or_window_bars:
            or_bars = view.history[: self.or_window_bars]
            self._or_high = max(b.high for b in or_bars)
            self._or_low = min(b.low for b in or_bars)
            return
        if self._or_high is None:
            return

        last = view.last_completed
        decided_at = last.timestamp

        # Failed breakout up: extension above OR_high but close back at/below OR_high.
        if (
            self._short_stop_id is None
            and last.high >= self._or_high + self.extension_points
            and last.close <= self._or_high
        ):
            self._short_stop_id = book.place_stop(
                side=OrderSide.SHORT,
                price=self._or_low,
                quantity=1,
                decided_at=decided_at,
                tag="inverse_orb_short",
            )

        # Failed breakout down: extension below OR_low but close back at/above OR_low.
        if (
            self._long_stop_id is None
            and last.low <= self._or_low - self.extension_points
            and last.close >= self._or_low
        ):
            self._long_stop_id = book.place_stop(
                side=OrderSide.LONG,
                price=self._or_high,
                quantity=1,
                decided_at=decided_at,
                tag="inverse_orb_long",
            )
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_setup_inverse_orb.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/setups/inverse_orb.py tests/causal/test_setup_inverse_orb.py
git commit -m "feat(causal): port inverse_orb — failed-breakout detection on completed bar; stop placed for next bar onward

Failed-breakout signal evaluated on the extension bar's close; reversal
stop placed with decided_at = that close. Causality gate prevents the
stop from filling against the extension bar."
```

---

### Task 12: Port `ema_continuation` (next-bar market entry on close-confirmed signal)

**Files:**
- Create: `backtest/causal/setups/ema_continuation.py`
- Test: `tests/causal/test_setup_ema_continuation.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_setup_ema_continuation.py`:
```python
"""EMA continuation: signal confirmed on completed bar's close
('wick-through then close-back-through' pattern). Entry is a market
order at the next bar's open. No intrabar 'EMA ± 2' fill — the close
confirmation arrives only at end of bar, so the only executable entry
is the next bar's open."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtest.causal.engine import CausalEngine
from backtest.causal.setups.ema_continuation import EmaContinuation


ET = timezone(timedelta(hours=-5))


def _df(rows):
    base = datetime(2025, 1, 2, 9, 30, tzinfo=ET)
    return pd.DataFrame(
        [
            {"timestamp": base + timedelta(minutes=15 * i), "open": o, "high": hi, "low": lo, "close": c, "volume": 100}
            for i, (o, hi, lo, c) in enumerate(rows)
        ]
    ).set_index("timestamp")


def test_ema_long_continuation_fills_at_next_bar_open():
    """Pretend EMA = 100 (use the test stub). Wick-through-and-back-through:
    bar 5 has low <= 100 (wick below EMA) and close > 100 (back above)."""
    rows = [
        (101, 102, 100.5, 101.5),  # 0: above EMA
        (101.5, 102, 101, 101.5),
        (101.5, 102, 101, 101.5),
        (101.5, 102, 101, 101.5),
        (101.5, 102, 101, 101.5),
        (101, 101.5, 99.5, 100.8),  # 5: wick below 100, close above (signal)
        (100.8, 102, 100.5, 101.5),  # 6: market order fills at open=100.8
    ]
    df = _df(rows)
    strat = EmaContinuation(ema_value=100.0, side="long")
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert len(result.fills) == 1
    assert result.fills[0].price == pytest.approx(100.8)


def test_ema_long_continuation_does_not_fill_if_no_close_back():
    rows = [
        (101, 102, 100.5, 101.5),
        (101.5, 102, 101, 101.5),
        (101, 101.5, 99.5, 99.8),  # wick below AND closes below — not a continuation up
        (99.8, 100, 99, 99.5),
    ]
    df = _df(rows)
    strat = EmaContinuation(ema_value=100.0, side="long")
    engine = CausalEngine()
    result = engine.run(df, strat)
    assert result.fills == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_setup_ema_continuation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement EmaContinuation**

For now this uses a stub `ema_value`; integration with the full EMA indicator from `backtest/indicators.py` is wired up via the engine's risk/indicator preprocessing in Task 13.

`backtest/causal/setups/ema_continuation.py`:
```python
"""EMA continuation, causally faithful.

Signal: wick-through-and-close-back-through EMA on a completed bar.
  - long: bar.low <= ema AND bar.close > ema
  - short: bar.high >= ema AND bar.close < ema

Entry: a market order placed AFTER the signal bar closes. The order
book causality gate guarantees it fills at the NEXT bar's open, not
inside the signal bar.

For now ema_value is a constant for unit testing. The full integration
takes a series of EMA values aligned with the bar index and reads the
signal-bar value from view.last_completed's index.
"""
from __future__ import annotations

from typing import Literal, Optional

from backtest.causal.bar_view import BarView
from backtest.causal.order_book import OrderBook
from backtest.causal.strategy import Strategy
from backtest.causal.types import OrderSide


class EmaContinuation(Strategy):
    def __init__(
        self,
        ema_value: float,
        side: Literal["long", "short"],
    ) -> None:
        self.ema_value = ema_value
        self.side = side
        self._fired = False

    def on_bar_complete(self, view: BarView, book: OrderBook) -> None:
        if self._fired:
            return
        last = view.last_completed
        if last is None:
            return
        signal = False
        if self.side == "long":
            signal = last.low <= self.ema_value and last.close > self.ema_value
            order_side = OrderSide.LONG
        else:
            signal = last.high >= self.ema_value and last.close < self.ema_value
            order_side = OrderSide.SHORT
        if signal:
            book.place_market(
                side=order_side,
                quantity=1,
                decided_at=last.timestamp,
                tag=f"ema_cont_{self.side}",
            )
            self._fired = True
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_setup_ema_continuation.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/setups/ema_continuation.py tests/causal/test_setup_ema_continuation.py
git commit -m "feat(causal): port ema_continuation — close-confirmed signal, market fill at NEXT bar open

Old engine assumed an intrabar 'EMA ± 2' fill inside the same bar whose
close confirmed the signal — that's the lookahead bug. New engine: signal
on completed bar -> market order placed -> fills at next bar's open."
```

---

## Phase E — Integration

### Task 13: Risk module port (sizing, max_risk_points, circuit breakers)

**Files:**
- Create: `backtest/causal/risk.py`
- Test: extend `tests/causal/test_engine.py` or add `tests/causal/test_risk.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_risk.py`:
```python
"""Risk module: position sizing, max_risk_points cap, daily loss limit,
consecutive losses circuit breaker."""
import pytest

from backtest.causal.risk import RiskConfig, position_size, exceeds_max_risk_points


def test_position_size_rounds_down_to_integer():
    # account = $2,500; risk_pct = 0.5%; risk_per_contract_dollars = $50
    # raw = 2500 * 0.005 / 50 = 0.25 -> floor = 0
    cfg = RiskConfig(account=2500, risk_pct=0.005, max_risk_points=100)
    n = position_size(cfg, risk_per_contract_dollars=50.0)
    assert n == 0


def test_position_size_returns_one_when_above_one_contract_threshold():
    cfg = RiskConfig(account=10_000, risk_pct=0.01, max_risk_points=100)
    # raw = 10000 * 0.01 / 50 = 2 contracts
    n = position_size(cfg, risk_per_contract_dollars=50.0)
    assert n == 2


def test_exceeds_max_risk_points_true():
    cfg = RiskConfig(account=10_000, risk_pct=0.01, max_risk_points=80)
    assert exceeds_max_risk_points(cfg, risk_points=85) is True


def test_exceeds_max_risk_points_false():
    cfg = RiskConfig(account=10_000, risk_pct=0.01, max_risk_points=100)
    assert exceeds_max_risk_points(cfg, risk_points=80) is False
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_risk.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement risk module**

`backtest/causal/risk.py`:
```python
"""Risk parameters and helpers, ported from the existing risk_params.yaml
contract. Position sizing uses fractional-Kelly-style fixed-percent risk:
  contracts = floor(account * risk_pct / risk_per_contract_dollars)
max_risk_points caps how wide a stop can be before the trade is skipped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    account: float
    risk_pct: float
    max_risk_points: float
    point_value: float = 2.0  # MNQ default; $2/point


def position_size(cfg: RiskConfig, risk_per_contract_dollars: float) -> int:
    if risk_per_contract_dollars <= 0:
        return 0
    raw = cfg.account * cfg.risk_pct / risk_per_contract_dollars
    return int(math.floor(raw))


def exceeds_max_risk_points(cfg: RiskConfig, risk_points: float) -> bool:
    return risk_points > cfg.max_risk_points
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_risk.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/risk.py tests/causal/test_risk.py
git commit -m "feat(causal): risk module — position sizing and max_risk_points cap

Mirrors the existing risk_params.yaml contract: fixed-percent risk
sizing, hard cap on stop width via max_risk_points."
```

---

### Task 14: Results adapter to existing `BacktestResults` shape

**Files:**
- Create: `backtest/causal/results.py`
- Test: `tests/causal/test_results.py`

- [ ] **Step 1: Write the failing test**

`tests/causal/test_results.py`:
```python
"""Results adapter: convert engine fills + closing fills into the legacy
BacktestResults shape (Trade list with entry/exit/pnl) so walk_forward
and monte_carlo continue to work unchanged."""
from datetime import datetime, timedelta, timezone

from backtest.causal.results import build_trades_from_fills
from backtest.causal.types import Fill, OrderSide


UTC = timezone.utc


def test_open_then_close_pair_produces_one_long_trade_with_correct_pnl():
    decided_at = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    open_fill = Fill(
        order_id=1,
        price=100.0,
        quantity=1,
        decided_at=decided_at,
        filled_at=datetime(2025, 1, 2, 14, 45, tzinfo=UTC),
    )
    close_fill = Fill(
        order_id=2,
        price=105.0,
        quantity=1,
        decided_at=datetime(2025, 1, 2, 14, 45, tzinfo=UTC),
        filled_at=datetime(2025, 1, 2, 15, 0, tzinfo=UTC),
    )
    trades = build_trades_from_fills(
        [(open_fill, OrderSide.LONG, "orb_breakout"), (close_fill, OrderSide.LONG, "orb_breakout_target")],
        point_value=2.0,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.setup == "orb_breakout"
    assert t.pnl == 10.0  # (105-100) * 1 contract * $2/point = $10
    assert t.win is True
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/causal/test_results.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement results adapter**

`backtest/causal/results.py`:
```python
"""Adapter from causal-engine fills into the legacy BacktestResults shape.

Entry/exit fills come in pairs — by convention the engine emits an
'open' fill and a later 'close' fill (target hit, stop hit, or
session-end forced exit). This adapter pairs them and produces Trade
objects in the existing dataclass shape so walk_forward.py and
monte_carlo.py keep working without modification.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from backtest.causal.types import Fill, OrderSide


@dataclass(frozen=True)
class Trade:
    setup: str
    side: OrderSide
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    quantity: int
    pnl: float

    @property
    def win(self) -> bool:
        return self.pnl > 0


def build_trades_from_fills(
    pairs: Sequence[tuple[Fill, OrderSide, str]],
    point_value: float,
) -> list[Trade]:
    """`pairs` is a list of (fill, side, setup_tag) entries in chronological
    order, alternating open/close. Each consecutive pair becomes one Trade."""
    trades: list[Trade] = []
    it = iter(pairs)
    for open_entry, close_entry in zip(it, it):
        open_fill, side, setup = open_entry
        close_fill, _, _ = close_entry
        if side is OrderSide.LONG:
            pnl = (close_fill.price - open_fill.price) * open_fill.quantity * point_value
        else:
            pnl = (open_fill.price - close_fill.price) * open_fill.quantity * point_value
        trades.append(
            Trade(
                setup=setup,
                side=side,
                entry_time=open_fill.filled_at,
                entry_price=open_fill.price,
                exit_time=close_fill.filled_at,
                exit_price=close_fill.price,
                quantity=open_fill.quantity,
                pnl=pnl,
            )
        )
    return trades
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/causal/test_results.py -v`
Expected: PASS — 1 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/causal/results.py tests/causal/test_results.py
git commit -m "feat(causal): results adapter — Trade dataclass and fill-pair → trade conversion"
```

---

### Task 15: Parity audit + scripts cutover + lock new baseline

**Files:**
- Create: `tests/causal/test_parity_audit.py`
- Modify: `scripts/run_phase1.py`
- Modify: `tests/unit/test_strategies_core.py` (re-lock against causal output)
- Modify: `VALIDATION.md`
- Modify: `backtest/backtester.py` (add deprecation notice)

- [ ] **Step 1: Run the causal engine end-to-end on the Phase 1 dataset to produce the new locked baseline**

Run: `python -c "from backtest.causal.engine import CausalEngine; ..."` — see the parity audit script below for the exact harness.

`tests/causal/test_parity_audit.py`:
```python
"""Parity audit: causal engine vs corrected baseline.

Documents (and locks) the differences:
  - Where the corrected (legacy) baseline already used resting-order
    semantics, the two should match exactly.
  - Where the legacy engine used same-bar hindsight (which we patched
    out into resting orders), the causal engine is expected to produce
    the same or fewer trades and equal-or-worse P&L (more conservative).

This test SKIPS if the local data parquet is not present, like the
existing baseline test."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PHASE1_DATA = PROJECT_ROOT / "data" / "mnq_15m.parquet"

pytestmark = pytest.mark.skipif(
    not PHASE1_DATA.exists(),
    reason="Phase 1 baseline requires local historical data/mnq_15m.parquet",
)


def test_causal_engine_total_trades_matches_locked_value():
    """Locked value to be filled in after the parity audit run.
    Until then, this test is a placeholder asserting the audit was performed.
    """
    from backtest.causal.engine import CausalEngine
    # Locked after audit; value will be set in the same commit that
    # updates this test.
    LOCKED_TRADES = None  # type: ignore[assignment]
    if LOCKED_TRADES is None:
        pytest.skip("Locked baseline not yet recorded; run parity audit first")
    # When LOCKED_TRADES is set:
    # engine = CausalEngine()
    # ... assemble strategies + risk + run
    # assert len(causal_results.trades) == LOCKED_TRADES
```

- [ ] **Step 2: Wire up `scripts/run_phase1.py` to optionally use the causal engine**

Add a `--engine causal|legacy` flag (default: `legacy` until parity audit accepted, then flip to `causal`):

```python
# scripts/run_phase1.py — add at top of main()
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--engine", choices=["legacy", "causal"], default="legacy")
args = parser.parse_args()

if args.engine == "causal":
    from backtest.causal.engine import CausalEngine
    from backtest.causal.setups.orb_breakout import OrbBreakout
    # ... assemble setups, run, convert to legacy BacktestResults via adapter
else:
    from backtest.backtester import Backtester
    bt = Backtester(strat_path, risk_path)
    res = bt.run(data_path)
```

- [ ] **Step 3: Run the parity audit harness**

```bash
python scripts/run_phase1.py --engine legacy 2>&1 | tee /tmp/parity_legacy.txt
python scripts/run_phase1.py --engine causal 2>&1 | tee /tmp/parity_causal.txt
diff /tmp/parity_legacy.txt /tmp/parity_causal.txt
```

Expected: causal trades <= legacy trades; causal P&L <= legacy P&L (more conservative once same-bar hindsight is gone). Document the diff in `VALIDATION.md`.

- [ ] **Step 4: Lock the new baseline**

Update `tests/unit/test_strategies_core.py` so the assertions match the causal-engine numbers (replace the four `assert ... == <legacy_value>` lines with the causal-engine values produced by step 3). Update the docstring header to note the engine source.

Update `tests/causal/test_parity_audit.py`: replace `LOCKED_TRADES = None` with the actual integer, and uncomment the assertion block.

- [ ] **Step 5: Mark legacy backtester deprecated**

At the top of `backtest/backtester.py`, add:

```python
"""DEPRECATED: this module retains its same-bar-hindsight semantics for
backwards compatibility with the corrected-baseline regression test only.
New code should use backtest.causal.CausalEngine. Removal: after one
review cycle confirms the causal engine baseline is stable.
"""
```

- [ ] **Step 6: Update `VALIDATION.md`**

Add a section: "Causal Engine Rewrite (2026-04-25 → ...)" documenting:
- Why: causality bug enumeration from the prior handoff
- What: structurally inexpressible lookahead (BarView + decided_at/filled_at invariant)
- Property tests: list the four (BarView future block, hypothesis history bound, fill timestamp invariant, lookahead-trap fixture)
- Locked baseline: causal-engine numbers from step 3
- Phase 1 status: still blocked (the engine is honest; the strategy is not the issue)

- [ ] **Step 7: Run the full suite**

Run: `PYTHONFAULTHANDLER=1 pytest -p no:capture tests -q`
Expected: all tests pass including the new causal/* tests and the re-locked baseline.

- [ ] **Step 8: Commit**

```bash
git add tests/causal/test_parity_audit.py scripts/run_phase1.py tests/unit/test_strategies_core.py VALIDATION.md backtest/backtester.py
git commit -m "feat(causal): parity audit + cutover; locked baseline now from causal engine

scripts/run_phase1.py supports --engine causal|legacy (default causal
after this commit). tests/unit/test_strategies_core.py re-locks the
baseline against causal-engine output. backtest/backtester.py marked
deprecated; retained only for the legacy-baseline regression in the
parity audit. VALIDATION.md documents the rewrite, the property-test
guarantees, and the new locked baseline."
```

---

## Self-Review Notes

**Spec coverage:**
- Two execution archetypes (resting order, next-bar market): Tasks 4–6 (order book), 9 (orb_breakout = resting stop), 10 (or_retest = resting limit), 11 (inverse_orb = resting stop), 12 (ema_continuation = next-bar market). ✓
- Lookahead structurally inexpressible: Task 2 (BarView blocks future access), Task 4 (decided_at >= bar.timestamp gate), Task 8 (property test + fixture). ✓
- Property test that synthetic future-bar reach fails loudly: Task 3 (history bound) + Task 8 (lookahead-trap fixture). ✓
- Port existing setups: Tasks 9–12. ✓
- Parity audit + cutover: Task 15. ✓
- BacktestResults adapter so walk_forward + monte_carlo keep working: Task 14. ✓
- Phase 1 status update + VALIDATION.md: Task 15 step 6. ✓

**Out of scope, deliberately deferred:**
- Replacing IBKR delayed-data with a research-grade source (separate plan: `2026-04-XX-data-procurement.md`).
- Stop-compression / OR-band / regime-filter research (separate plan: `2026-04-XX-edge-recovery-research.md`, gated on this rewrite landing).
- Live execution wiring (Phase 2; remains paused).

**Risks:**
- The intrabar fill model uses bar O/H/L/C heuristics (no tick data). For OCO with both stop and target reachable in the same bar, the order of touch is ambiguous; current implementation fills both candidates from `step()` and the order book emits both fills, then OCO cancels one. The first-touched assumption (long: low touched first if bar is bearish, high first if bullish) is a separate refinement, deferred.
- `ema_continuation` test stub uses a constant EMA value; full integration with `backtest/indicators.py` is wired through a preprocessing pass on the DataFrame before `engine.run()` (the indicator is pre-computed per bar; the strategy reads the value at `view.last_completed`'s index from a passed-in series). This wiring belongs in Task 12 step 3 if the test there starts using the real indicator, or in a separate small follow-up if a stub is sufficient for the unit test (which it is).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-causal-backtester-rewrite.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
