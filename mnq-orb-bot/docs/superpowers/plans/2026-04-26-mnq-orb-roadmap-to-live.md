# MNQ ORB Bot — Roadmap to Confident Autonomous Live Execution

> **Type:** Strategic roadmap (not an implementation plan).
> Each phase below has gates that must clear before the next phase starts.
> Phases marked **[needs implementation plan]** require their own detailed
> plan written via `superpowers:writing-plans` (and brainstorming where the
> phase touches Tier 3 code) when that phase becomes active.

**Goal:** Get from "verified historical edge" to "single-contract autonomous live execution that we can defend with evidence" — without skipping any of the steps that protect real money.

**Honest framing:** The research is essentially done. The trading-system work has not started. Anyone who tells you "the backtest is good, we can ship" is selling something. We have an artifact, not a deployable system.

**Tag references:**
- `phase6-causal-or-retest-v1-frozen` — QA-verified frozen baseline
- `phase6-canonical-strict-2024plus` — canonical execution baseline + tooling

---

## What we've actually proven (insights, ranked by confidence)

### High confidence
1. **Causal mechanics are sound.** 0 same-bar-hindsight violations across all 95 default Tier 1 trades; entry always = signal_ts + 1 minute, raw entry price always = next bar open. Stop-first ambiguity correctly modeled.
2. **Methodology bugs from prior worker are fixed and tested.** Peak-relative DD denominator, full-session contamination diagnostics, append-mode safety (28/28 tests passing including conflict, schema mismatch, NaN equality).
3. **IBKR demo HMDS for 1-minute MNQ is deterministic.** Refetch produced bit-identical 2024+ data. Demo cannot be made cleaner; that's the floor.
4. **2023 demo data is unusable** (87.92% zero-volume in old pull, 88.83% in fresh pull). Strict + 2024+ start is the correct historical window.
5. **Strategy improves over time on the data we have.** Tier 1 avg PnL: 2024 $17.66 → 2025 $24.84 → 2026 $33.66. A+ shadow same trend, more pronounced.
6. **Append journal is conflict-safe.** End-to-end audit against the real 686/697-row CSV: identical skips, volatile-only skips, conflicting raises, schema raises, NaN handled correctly, file preserved on every raise.

### Medium confidence
7. **Frozen canonical baseline:** Tier 1 strict 89/$2,035.74/PF 1.6737, A+ strict 51/$2,076.66/PF 2.6035. MC ruin @ $3,750 = 3.13% (T1) / 0.06% (A+). These are real on the data we have.
8. **A+ shadow has higher unit economics but smaller sample.** 51 trades is borderline for statistical inference; effect size is large enough that it's probably real, but the variance is high.
9. **Slippage assumption (5pt RT)** is structurally reasonable for MNQ on liquid time-of-day windows but is **not measured**. Could be too conservative, could be too generous on volatile days.

### Low confidence / unknown
10. **Real fill quality vs simulated fill quality.** We have zero live fills. Every "Tier 1 trade" in the backtest assumes idealized next-bar-open with 2.5pt one-way slippage. Reality may differ on entry, stop, and target.
11. **Live tick stream behavior vs demo bar snapshots.** Demo data has 4.5% zero-volume bars in 2024+. Real-time may have liquidity gaps in different places, especially at session edges.
12. **Strategy robustness to broker error conditions.** Untested: order rejection (max-rate 100, margin 201, cancelled 202), partial fills, latency spikes, disconnects mid-trade.
13. **Strategy's behavior in regimes we haven't seen.** 2024-2026 has been one regime broadly. Tier 1 has 89 trades total — very few that we'd call structurally distinct days (Fed days, payrolls, FOMC, OPEX).

### What we have NOT touched
- Any `bot/execution/` code
- `bot/signal_generator.py` for the new strategy
- `bot/risk_manager.py`
- `bot/watchdog.py`
- `bot/health_monitor.py`
- `backtest/strategies/core.py`
- `config/risk_params.yaml` / `config/strategy_params.yaml`

All of the above are Tier 3 per `mnq-orb-bot/CLAUDE.md` and require `superpowers:brainstorming` + `/deepreview` before edits.

---

## The path forward — five phases with hard gates

Each phase produces evidence; no phase advances until its exit gate clears.

```
Phase 6.5 ─→ Phase 7 ─→ Phase 8 ─→ Phase 9 ─→ Phase 10
Paper      Live-feed   Tier 3      Forward     Live ramp
Forward    integ.      build       gate +      (1 contract)
Ops                                fill recon
```

---

## Phase 6.5 — Paper-Forward Operations (start NOW, low risk)

**Goal:** Stand up the daily paper-forward clock, build observability around it, accumulate forward evidence at zero financial risk.

**Why first:** Costs nothing. Generates the evidence the gate needs. Surfaces operational issues (cron failures, data gaps, feed flakiness) on dummy money. Independent of live feed and account funding.

**Status:** Not started.

**Touches Tier 3:** No. Pure research-layer scripts and infra.

### Concrete deliverables (each gets its own implementation plan task)

1. **Daily-fetch + daily-append cron.** A scheduled job that:
   - Pulls fresh 1m bars after 16:00 ET (bar-fetch script with retry)
   - Runs `scripts/run_causal_paper_forward.py --mode append --require-full-session-clean --start 2024-01-01 --out research/human_edge_replay/paper_forward/forward_journal.csv`
   - Logs success/failure to a known location
   - Sends a Slack/email alert on failure (use existing `slack-sdk`)
2. **Weekly gate-check cron.** Runs `scripts/check_paper_forward_gate.py` every Monday morning, posts the result to Slack.
3. **Data-freshness monitor.** Detects when the parquet's last bar is more than 1 trading day stale (e.g., cron failed silently); alerts.
4. **Forward-vs-backtest divergence dashboard.** Each new forward trade joins to the backtest counterpart (same date, same rule_version) and surfaces any divergence in fill prices, exit reasons, or PnL. Even on backtest data this should be a no-op; the value comes when live data lands.
5. **Documentation:** runbook entries for "what to do if the cron fails," "what to do if the gate FAILs," "how to re-tag the canonical baseline."

### Gate to advance to Phase 7

- Paper-forward cron has run successfully for **7 consecutive trading days**, no missed days, no silent failures.
- Weekly gate-check has run at least once and produced expected output (will be FAIL until 50 trades accumulate, but should run cleanly).
- Alerting verified by a forced-failure drill (intentionally break the cron, confirm alert fires, restore).

### Estimated effort: 2-3 days of focused work, then the cron just runs.

### Open question for brainstorming when this phase starts
- **Where does the cron run?** Local laptop (cheap, but laptop must be on at 16:00 ET) vs cloud box (Hetzner / Fly.io / Railway, ~$5/mo, always on). Recommend cloud — paper-forward continuity matters for the gate clock.

**Implementation plan:** [needs implementation plan] — should be straightforward, ~6-10 tasks.

---

## Phase 7 — Live CME Feed Integration (blocked on account verification)

**Goal:** Replace demo bar snapshots with live CME tick → 1m bar aggregation; confirm the canonical edge survives on tick-quality data.

**Why before Tier 3:** The canonical metrics were computed on demo data. We need to know whether they hold on live data BEFORE we build production wiring around them. If they don't hold, we save weeks of execution-system work.

**Status:** Blocked on you completing IBKR live account verification + CME L1 subscription.

**Touches Tier 3:** No. Still research-layer.

### Concrete deliverables

1. **Live-feed adapter** that subscribes to MNQ ticks from IBKR live (`marketDataType=1`) and persists 1m OHLCV bars to a parquet alongside the existing demo parquet. Run side-by-side with demo for at least one week to compare.
2. **Live-vs-demo bar diff tool.** For overlap days, compare live bars to demo bars: open/high/low/close diffs, volume ratios, zero-volume incidence. Quantify how much demo lies.
3. **Re-run canonical against live data.** Same `run_causal_paper_forward.py --require-full-session-clean --start <live-feed-start>`. Compare metrics. **This is a brutal moment of truth:** if PF drops below 1.4 or avg PnL drops below $14/trade, the strategy doesn't survive contact with reality and we go back to the research drawing board.
4. **Re-tag canonical** if metrics shift (e.g., `phase6-canonical-live-v1`).
5. **Update MC ruin** against the live-data PnL distribution.

### Gate to advance to Phase 8

- ≥ 4 weeks of side-by-side live-vs-demo data collected.
- Live-data canonical metrics meet floor: Tier 1 PF ≥ 1.4, avg ≥ $14/trade, max DD ≤ 1.5× backtest max DD ($625).
- Live-vs-demo bar diff documented; significant divergences (volume, gaps) understood and explained.
- New canonical baseline tagged.

### Risk: This phase can kill the project.
If live data shows the strategy was an artifact of demo-feed quirks, that's a hard stop. **That outcome is acceptable** — better to discover it here on zero risk than after wiring up live execution. Don't let sunk-cost on prior research bias the decision.

**Implementation plan:** [needs implementation plan] when feed lands.

---

## Phase 8 — Tier 3 Execution System Build (multi-week, real engineering)

**Goal:** Build the production-side wiring that turns a research artifact into a deployable trading system. Every component has tests that replay recorded broker behavior including failure modes.

**Why this is hard:** The current `evaluate_rule_on_day()` is a backtest function over a static DataFrame. Live execution needs an event loop, persistent state, idempotent restart, order management, watchdog supervision, risk enforcement, and reconciliation. None of those exist for this strategy.

**Status:** Not started. **Hard prerequisites: Phase 6.5 + Phase 7 gates cleared.**

**Touches Tier 3:** YES. Per `mnq-orb-bot/CLAUDE.md`:
- Edits to `bot/execution/**`, `bot/watchdog.py`, `bot/risk_manager.py`, `bot/health_monitor.py`, `bot/signal_generator.py`, `backtest/strategies/core.py`, `config/risk_params.yaml`, `config/strategy_params.yaml` ALL require `/deepreview` before declaring complete.
- `superpowers:brainstorming` REQUIRED before this phase starts. Brief must produce explicit answers to: max position, max daily loss, connection-loss behavior, watchdog independence proof, reconciliation behavior on reconnect.

### Subsystems to build (each gets its own brainstorm + implementation plan)

#### 8.1 Signal generator [needs brainstorm + plan]
- Live event loop running every minute during RTH
- Persistent OR-window state (high/low/class/breakout flag)
- Retest-window detection on completed 1m bars
- VWAP/EMA9/RSI14/volume_ratio20 incremental computation
- Idempotent restart: process can crash mid-day and resume from last persisted state
- Emits a structured "signal" event (date, signal_ts, entry_ts, stop_price, target_price, rule)
- Tested against a tick-replay harness using recorded 1m bars

#### 8.2 Order execution path [needs brainstorm + plan]
- Receives signal event → places entry order → places OCO stop + target → manages exits
- Idempotency keys to prevent duplicate orders on restart
- Handle IBKR errors: 201 (margin reject), 202 (cancelled), 100 (max rate), partial fills
- Pre-trade risk check (talks to risk manager)
- Slippage tracking: every fill recorded with simulated counterpart for reconciliation
- Mock-fidelity tests per CLAUDE.md: replay recorded broker behavior including ≥100ms latency, partial fills, error codes, out-of-order execution reports

#### 8.3 Risk manager [needs brainstorm + plan]
- Hard limits enforced as a separate process, not just config:
  - Max 1 contract open
  - Max daily loss: $X (TBD in brainstorm)
  - Max consecutive losses: N (TBD)
  - Kill switch: file-based or admin command, halts all new entries immediately
- Kill switch state survives process restart

#### 8.4 Watchdog [needs brainstorm + plan]
- Independent process supervising the bot
- Monitors: process alive, IBKR connected, last-tick-received age, last-bar-completed age, CPU/memory
- Auto-restart on hang, but NOT auto-restart if risk manager has tripped kill switch
- Independence proof: can the watchdog itself fail silently? Heartbeat to external service.

#### 8.5 Health monitor + reconciliation [needs brainstorm + plan]
- On every reconnect: query broker for open positions and orders, reconcile against internal state, flag discrepancies LOUDLY
- Daily end-of-day reconciliation: account balance, open positions, fills count, P&L

#### 8.6 Observability
- Slack alerts: signal generated, order placed, order filled, order rejected, risk limit triggered, watchdog action, daily summary
- Structured logs (JSON) for every state transition
- Dashboard (Grafana or similar) for: equity curve, daily P&L, fill quality vs sim, watchdog health

### Gate to advance to Phase 9

- Every subsystem has tests that pass with realistic broker mocks.
- `/deepreview` (and `/audit-project --recent`) clean on all Tier 3 files.
- Bot can run end-to-end against IBKR paper for 5 consecutive trading days with no human intervention, no silent failures, no reconciliation discrepancies.
- Risk manager kill switch tested by deliberate trigger: bot stops, alerts fire, no further entries until manual reset.
- Connection-loss drill: kill TWS mid-trade, restart, confirm bot reconciles correctly.

### Estimated effort: 3-6 weeks of careful work.

---

## Phase 9 — Forward Gate + Fill Reconciliation (≥ 1 quarter wall clock)

**Goal:** Run paper-forward through the canonical promotion gate AND reconcile every paper fill against its simulated counterpart.

**Status:** Blocked on Phase 8.

**Touches Tier 3:** Yes (same code, just running paper).

### Concrete deliverables

1. **Bot runs paper-trading on IBKR paper account for ≥ 1 calendar quarter.**
2. **Every fill reconciled** against the backtest's simulated fill for the same signal: entry price diff, stop price diff (if hit), target price diff (if hit), time-exit price diff. Aggregate slippage statistics.
3. **`scripts/check_paper_forward_gate.py` PASSES** on the accumulated forward trades:
   - ≥ 50 Tier 1 trades
   - ≥ 90 days elapsed
   - PF ≥ 1.4
   - avg ≥ $16.01
   - max DD ≤ $625.59
   - No ≥ 30% degradation at p ≤ 0.10
4. **Slippage assumption validated or revised.** If real RT slippage is materially different from 5pt assumption, retune canonical and re-tag.
5. **Zero unexplained execution divergences.** Every divergence between paper fill and sim has a documented cause.

### Gate to advance to Phase 10 (live ramp)

- All gate criteria PASS.
- Slippage assumption either confirmed or canonical re-tagged with measured value.
- No outstanding alerts, no unresolved reconciliation discrepancies in the most recent 30 days.
- Account funded to ≥ $3,750 (hard floor; ideally $5,000 for ruin headroom).

---

## Phase 10 — Single-Contract Live Ramp (real money)

**Goal:** Move from paper to live with the smallest possible bet, maximum supervision, daily review.

**Status:** Blocked on Phase 9.

### Concrete deliverables

1. **Single-contract live trading** for 30+ trading days. Same code, just live account credentials.
2. **Daily review** for first 2 weeks: every fill reviewed manually, slippage logged, any anomaly investigated same-day.
3. **Weekly review** for weeks 3-6: aggregate metrics, drawdown vs forward expectation, equity curve.
4. **Daily P&L hard limit:** if loss > $X (TBD in Phase 8 risk brainstorm), bot halts, no further entries until manual review.
5. **Equity-curve gate:** if cumulative live PnL after 30 trades is < 0, halt and re-evaluate. (Backtest had Tier 1 win rate 60.67% — a sub-zero equity curve at 30 trades is a 5-sigma event.)

### Gate to consider scaling (out of scope for this roadmap)

- 30+ live trades.
- Live PF within 20% of forward PF.
- Live avg PnL within 25% of forward avg PnL.
- Account drawdown not exceeded $625 (the 1.5× backtest DD ceiling).

---

## Crosscutting concerns (apply to every phase)

### Always
- **Append-only journal** for every paper/live decision. Immutable `(date, rule_version)` key.
- **Verification-before-completion** before any "this works" claim — run the test, check the output.
- **No same-bar hindsight, ever.** Every change to signal/exit logic re-runs the 95-trade causal-invariant scan.
- **Tag every canonical re-baseline** with an immutable git tag and a dated message.
- **Keep `data/` gitignored** (already true). The `data_file_hash` field in journals captures data state for verification.

### Tier 3 changes only
- `superpowers:brainstorming` first — produce answers to max position / max daily loss / connection-loss / watchdog independence / reconciliation.
- TDD with mock-fidelity tests (recorded broker behavior, not idealized fills).
- `/deepreview` before declaring complete.
- `/audit-project --recent` before merging.

### Hard prohibitions
- No tuning the strategy on the historical sample (Tier 1 / A+ are frozen at v1).
- No skipping `--no-verify` on commits.
- No bypassing the watchdog or risk manager to "test something quick."
- No live trading at < $3,750 account balance.
- No promoting a strategy to live without forward-gate PASS.

---

## Decision log (anchor for future work)

| Date | Decision | Rationale | Tag |
|---|---|---|---|
| 2026-04-26 | Canonical mode = strict + 2024-01-01 start | 2023 demo data 0% full-clean; 2024+ stable | `phase6-canonical-strict-2024plus` |
| 2026-04-26 | Tier 1 = primary, A+ = secondary, Tier 2 = informational only | User preference + capital floor mismatch | (in ADR) |
| 2026-04-26 | IBKR demo refetch confirms canonical bit-identical | HMDS deterministic; live feed is the only robustness path | commit `325498b` |
| 2026-04-26 | Roadmap to live = 5 phases with hard gates | Real money requires earned confidence | this doc |

---

## Where each phase's detailed implementation plan will live

When a phase starts, write its plan to:

- Phase 6.5: `docs/superpowers/plans/2026-XX-XX-paper-forward-operations.md`
- Phase 7:  `docs/superpowers/plans/2026-XX-XX-live-feed-integration.md`
- Phase 8.1-8.6: separate plan per subsystem under `docs/superpowers/plans/2026-XX-XX-tier3-<subsystem>.md`
- Phase 9:  `docs/superpowers/plans/2026-XX-XX-forward-gate.md`
- Phase 10: `docs/superpowers/plans/2026-XX-XX-live-ramp.md`

Each plan file gets its own brainstorm doc (for Tier 3 phases) saved next to it.

---

## What I'd do today if I were you

1. **Approve or push back on this roadmap.** If you disagree with phase ordering or gates, now's the cheap time to argue.
2. **Pick a host for the paper-forward cron** (cloud recommended).
3. **Tell me to write the Phase 6.5 implementation plan.** That's the only phase that's ready to start, and the work is bounded enough to plan in detail today.
4. **Don't touch Tier 3 until Phase 7 gate clears.** Even if you're tempted. Especially if you're tempted.
