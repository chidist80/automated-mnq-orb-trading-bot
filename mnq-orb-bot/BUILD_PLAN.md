# BUILD_PLAN.md — MNQ ORB Bot Implementation Plan
**Date**: 2026-04-02  
**Status**: Awaiting review before execution

---

## Phase 1: Validate the Backtester (THIS WEEK)

### Pre-Backtest Fixes (do first)

| # | Task | File | Effort | Priority |
|---|------|------|--------|----------|
| 1.1 | Fix retest entry price (enter at `retest_level`, not `retest_level ± tolerance`) | `or_detector.py:226,246` | 10 min | **CRITICAL** |
| 1.2 | Fix inverse ORB entry_price to use actual entry bar close | `backtester.py:452,459` | 15 min | LOW |
| 1.3 | Add data acquisition script (`scripts/fetch_data.py`) | New file | 1-2 hours | **CRITICAL** |

**1.3 Detail — Data Acquisition Options:**
- **Option A (preferred):** Databento API — clean 15m bars, pay per download, no TWS needed
- **Option B:** IBKR historical via `data_loader.load_ibkr_historical()` — already built, needs TWS running
- **Option C:** TradingView CSV export — manual but free
- Need 12+ months of MNQ 15m RTH data (roughly 5,000-6,000 bars)

### Backtest Validation Sequence

| # | Task | Command | Gate |
|---|------|---------|------|
| 1.4 | Acquire data | `python scripts/fetch_data.py` | File exists, ≥5,000 bars |
| 1.5 | Run baseline backtest | `python -m backtest.backtester data/mnq_15m.parquet` | Completes without error |
| 1.6 | Quick param sweep | `python -m backtest.param_sweep data/mnq_15m.parquet --quick` | ≥30 valid combos |
| 1.7 | Walk-forward validation | `python -m backtest.walk_forward data/mnq_15m.parquet` | **≥75% OOS windows profitable** |
| 1.8 | Monte Carlo simulation | `run_monte_carlo(results, account_size=2500)` | **<5% ruin probability** |
| 1.9 | Generate report | `generate_report(results, "reports/v1.html")` | Report viewable |

**STOP if 1.7 or 1.8 fails.** No Phase 2 on unvalidated parameters.

### Ruflo Integration for Phase 1

```bash
# Before starting
npx ruflo@latest hooks pre-task --description "Phase 1: Backtest validation"
npx ruflo@latest neural predict --task "optimal ORB params" 2>/dev/null

# After successful validation
npx ruflo@latest memory store --namespace mnq-bot --key "backtest:$(date +%Y%m%d)" \
  --value '<results JSON>' --reasoningbank
npx ruflo@latest neural train --model-type moe --epochs 5
npx ruflo@latest hooks post-task --task-id "phase1" --success true --train-neural true
```

---

## Phase 1.5: Pre-Phase-2 Prep

### Shared Strategy Module Design

The single biggest architectural task: extract strategy logic from the backtester so both the backtester and the live bot import from the same source.

**Target file:** `backtest/strategies/core.py`

```
backtest/strategies/core.py
├── detect_and_classify_or()      # From or_detector.py (unchanged)
├── evaluate_orb_signal()         # Extract from backtester._process_day() lines 274-367
│   ├── detect breakout
│   ├── route to strategy (inverse vs standard vs continuation)
│   ├── check confluences
│   └── return Signal dataclass (entry, stop, target, setup, confluences)
├── evaluate_ema_continuation()   # Extract from _find_ema_continuations()
└── Signal dataclass              # Shared between backtest and live

backtest/backtester.py
└── Uses evaluate_orb_signal() → feeds Signal into _walk_forward_exit()

bot/signal_generator.py
└── Uses evaluate_orb_signal() → feeds Signal into risk_manager → ibkr_executor
```

**Key constraint:** The shared module must be pure computation — no I/O, no IBKR calls, no Supabase. It takes a DataFrame of bars + config dict and returns Signal objects. The backtester and bot each handle their own I/O.

**No new dependencies.** The shared module uses only pandas, numpy, and the existing or_detector/indicators modules.

---

## Phase 2: Live Bot + IBKR Paper (WEEKS 2-4)

### Component Breakdown

| # | Component | File | Depends On | Effort | Risk |
|---|-----------|------|-----------|--------|------|
| 2.1 | Shared strategy module | `backtest/strategies/core.py` | Phase 1 complete | 4-6 hours | Medium |
| 2.2 | Signal generator | `bot/signal_generator.py` | 2.1 | 3-4 hours | Medium |
| 2.3 | Risk manager | `bot/risk_manager.py` | 2.1 | 3-4 hours | **High** |
| 2.4 | IBKR executor | `bot/execution/ibkr_executor.py` | 2.2, 2.3 | 6-8 hours | **Critical** |
| 2.5 | Trade logger | `bot/trade_logger.py` | 2.4 | 2-3 hours | Low |
| 2.6 | Flatten watchdog | `bot/watchdog.py` | — (independent) | 3-4 hours | **Critical** |
| 2.7 | Health monitor | `bot/health_monitor.py` | 2.4 | 2-3 hours | Medium |
| 2.8 | Main entry point | `bot/main.py` | All above | 2-3 hours | Medium |
| 2.9 | Slack notifier | `bot/slack_notifier.py` | — | 2-3 hours | Low |
| 2.10 | Integration tests | `tests/test_integration.py` | All above | 4-6 hours | Medium |

**Total estimated effort: 30-45 hours of development**

### Build Order (dependency-driven)

```
Week 2:  2.1 → 2.2 → 2.3 → 2.6 (parallel with 2.2-2.3)
Week 3:  2.4 → 2.5 → 2.7 → 2.8
Week 4:  2.9 → 2.10 → Paper trading begins
```

### Critical Build Rules (from INIT_PROMPT)

1. **Shared logic** — Signal generator imports from `backtest/strategies/core.py`. NEVER duplicate strategy logic.
2. **Bracket orders** — Every entry submits entry + stop + target as one bracket. No naked entries.
3. **Independent watchdog** — Separate process, `Restart=always`, flattens at 15:45 ET regardless.
4. **Connection loss** — >120s disconnect with position → Slack alert, do NOT auto-flatten on stale data.
5. **Position reconciliation** — Every 5 min, compare bot state with IBKR. Divergence → alert + halt.
6. **Zero ruflo runtime** — No ruflo imports in any bot/ file except dispatcher.py.

### Ruflo Features for Phase 2

| Feature | Where | Why |
|---------|-------|-----|
| **Swarm (5 agents)** | `/build-phase2` | Architect + 2 coders + safety + tester in parallel |
| **AQE plugin TDD** | `ibkr_executor.py`, `risk_manager.py` | TDD on safety-critical execution code |
| **Audit worker** | Post-build | Automated security scan on all execution code |
| **Testgaps worker** | Post-build | Find untested paths in critical modules |
| **Memory** | Throughout | Store architectural decisions, test results |
| **Hooks** | Every file edit | Auto-run tests, pattern learning |

---

## Phase 3: Monitoring (PARALLEL with Phase 2)

| # | Component | Effort |
|---|-----------|--------|
| 3.1 | Slack trade notifications | 2 hours |
| 3.2 | Slack daily summary | 2 hours |
| 3.3 | Slack error/circuit breaker alerts | 1 hour |
| 3.4 | Wire crowding monitor to Supabase + Slack | 2 hours |
| 3.5 | Dashboard (optional — Slack covers 90%) | 8-12 hours if needed |

**Ruflo:** Hooks only. No swarm needed — these are straightforward modules.

---

## Consensus Validation Design (Paper Trading)

### Architecture

During paper trading, each signal passes through a 3-agent consensus layer before execution:

```
Signal Generator → Signal object
    │
    ▼
Consensus Layer (ruflo hive-mind, paper mode only)
    ├── Agent 1: Mechanics  — "Is the retest clean? Breakout candle strong?"
    ├── Agent 2: Regime     — "Trending day or choppy? ATR normal?"
    └── Agent 3: Cross-Mkt  — "ES confirming NQ? VIX stable?"
    │
    ▼
Score ≥ 7/10 avg → Execute    |    Score < 7 → Skip + log reason
```

### Implementation Plan

1. **Data collection (paper trades 1-50):** Run signals through consensus, log both consensus-approved and consensus-rejected signals alongside actual outcomes
2. **Evaluation at 50 trades:** Compare WR of consensus-filtered vs unfiltered signals
3. **Decision gate:**
   - Consensus WR > unfiltered WR by ≥5% → extract scoring logic into vanilla Python function
   - No improvement → remove consensus layer (it's just adding latency)
4. **If extracted:** The scoring function becomes a pre-trade filter in `signal_generator.py` — still no ruflo runtime dependency

### Key Design Decisions

- Consensus is **paper-only** until validated. Live path stays clean: signal → risk → bracket.
- Each agent gets the same signal data and evaluates independently (no cross-talk).
- Results stored in ruflo memory for pattern analysis.
- The dispatcher auto-triggers `/eval-consensus` at 50 trades (TRIGGERS config).

---

## Dispatcher Adjustments

Based on VALIDATION.md findings:

| Change | Current | Proposed | Reason |
|--------|---------|----------|--------|
| Consensus re-eval | One-shot at 50 | Every 50 trades (50, 100, 150...) | Ongoing validation, not just initial |
| Halt cooldown | None | 7 days | Prevents Slack spam while conditions persist |
| `datetime.utcnow()` | Used throughout | `datetime.now(timezone.utc)` | Deprecated in Python 3.12 |
| Crowding import | Relative import | Add project root to sys.path or use package install | Will fail on VPS otherwise |

---

## Risk Assessment

### What could go wrong

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Backtest looks great, live fails | Medium | High | Walk-forward + Monte Carlo gates; 4-week paper minimum |
| IBKR connection drops mid-trade | Medium | High | Independent watchdog; 120s timeout before alert |
| Strategy edge decays over time | High | Medium | Crowding monitor; monthly recalibration; neural priors |
| Parameter overfitting | Medium | High | Fixed-param walk-forward; resist re-optimizing |
| VPS goes down | Low | High | Systemd Restart=always; Slack alerts on health check miss |
| Shared module diverges from backtester | Low | High | Single source of truth in strategies/core.py; integration tests |

---

## Decisions Made

1. **Data source:** IBKR historical API via `data_loader.load_ibkr_historical()`
2. **N1/N2:** Fixed 2026-04-02 — retest entry now at `retest_level` (not ± tolerance)
3. **Phase 2 build:** Swarm (`/build-phase2`) if feasible
4. **Paper trading targets:** Research-based criteria below

---

## Paper Trading Graduation Criteria

Based on quantitative trading literature (Davey, Unger, Chan) and prop firm standards:

| Criterion | Minimum | Target | Rationale |
|-----------|---------|--------|-----------|
| **Trade count** | 100 | 150+ | <100 is statistically meaningless at 55-65% WR (Davey) |
| **Duration** | 12 calendar weeks | 16 weeks | Must capture FOMC cycle, OpEx, and mixed regimes |
| **Win rate** | ≥50% | ≥55% | 1:1 R:R needs 50%+ to be net profitable after costs |
| **Profit factor** | ≥1.5 | ≥1.8 | Expect 10-20% PF decay live; 1.3 paper ≈ 1.1 live (breakeven) |
| **Max drawdown** | ≤10% ($250) | ≤8% ($200) | Prop firm standard 8-12% trailing |
| **Consecutive losses** | ≤5 | ≤4 | 5 consecutive at 55% WR has ~1.8% probability |
| **Sharpe ratio** | ≥1.5 (annualized) | ≥2.0 | Below 1.0 is not tradeable |
| **Recovery factor** | ≥3.0 | ≥5.0 | Net profit / max drawdown over test period |
| **No single trade** | <30% of total profit | <20% | Ensures results aren't driven by one outlier |
| **Regime coverage** | Profitable in ≥2 of 3 regimes | All 3 | Regimes: strong trend, mild trend, range-bound |
| **Monte Carlo** | 95th %ile DD < $250 | < $200 | 1,000+ reshuffles of trade sequence |

### Execution Quality (Paper-Specific)
- Add 1 tick ($0.50/contract) per side simulated slippage on top of modeled slippage
- Log entry-to-signal latency — if >500ms consistently, ORB edge degrades
- Reject paper fills where volume at price/time < 50 contracts

### Graduation Gate
**ALL criteria must pass simultaneously.** If any single criterion fails at the 100-trade mark, continue paper trading to 150 trades. If still failing at 150, stop and recalibrate.

---

**Next step:** Acquire IBKR data, run Phase 1 validation.
