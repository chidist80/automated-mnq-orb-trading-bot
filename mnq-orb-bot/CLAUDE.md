# CLAUDE.md — MNQ ORB Trading Bot

## Project Facts

- **Account:** $2,500 IBKR margin (`DUO707586` for paper data; live account separate). Real money.
- **Instrument:** MNQ futures. $2/point, $0.50/tick (4 ticks/point), multiplier 2. RTH only.
- **Position cap:** 1 contract until ≥50 paper trades pass `BUILD_PLAN.md` graduation criteria.
- **Data type:** market data type 3 (delayed) on demo; type 1 (live) on funded account.
- **Runtime surface:** stdlib + ib_insync + pandas + pyyaml + supabase + slack-sdk. Nothing else.

## Tier 3 (real-money paths)

Editing any of these requires `/deepreview` before declaring the task complete:

- `bot/execution/**`
- `bot/watchdog.py`
- `bot/risk_manager.py`
- `bot/health_monitor.py`
- `bot/signal_generator.py`
- `backtest/strategies/core.py`
- `config/risk_params.yaml`
- `config/strategy_params.yaml`

Canonical list: `.claude/hooks/tier3_paths.py`.

## Phase pointer

- **Current phase:** Phase 2 — live execution.
- **Plan:** `mnq-orb-bot/docs/superpowers/plans/2026-04-25-phase2-live-execution.md`.
- **Branch:** `phase2/live-execution`.

## Mock-fidelity rule (execution-path tests)

Mocks of ib_insync MUST replay recorded broker behavior — partial fills, error
codes (201 margin, 202 cancelled, 100 max-rate), out-of-order execution
reports, ≥100ms simulated fill latency. **Why:** idealized mocks (`fill =
limit_price` instantly) hide the bugs that lose accounts. **Scope:** any
test under `tests/` that imports from `bot/execution/` or simulates a fill.

## No-shortcut covenant

When blocked, raise the blocker explicitly. Don't fabricate a workaround that
"looks right." Don't suppress an exception to make a test pass. Don't disable
the watchdog to make a check green. **Why:** real money. **Scope:** all work
in this repo. Invoke `superpowers:verification-before-completion` before any
completion claim.

## Brainstorming brief for Tier 3 epics

Any Tier 3 brainstorm must produce explicit answers to: max position, max
daily loss, connection-loss behavior, watchdog independence proof,
reconciliation behavior on reconnect. **Why:** these are the categories where
retail futures bots actually fail. **Scope:** every Tier 3 epic in the Phase 2
plan.
