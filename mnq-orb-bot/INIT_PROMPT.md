# MNQ ORB Bot — Claude Code Initialization

Read this entire file before doing anything. Do not write application code until the validation gate at the end is complete.

---

## Setup

```bash
# 1. Verify ruflo MCP
claude mcp list | grep claude-flow
# If missing: claude mcp add claude-flow -- npx -y ruflo@latest mcp start

# 2. Init ruflo for this project
npx ruflo@latest init
npx ruflo@latest plugins install @claude-flow/plugin-agentic-qe
npx ruflo@latest embeddings init --model all-mpnet-base-v2

# 3. Start session + retrieve prior context
npx ruflo@latest hooks session-start --session-id "mnq-bot-$(date +%Y%m%d)"
npx ruflo@latest memory search --namespace mnq-bot --query "project context" --reasoningbank 2>/dev/null || echo "Fresh start — no prior context"
```

If setup steps fail, note and continue. Ruflo is the factory, not the product.

---

## Core Principle

**Ruflo is the factory. The bot is the product.**

Full ruflo capabilities for building, testing, analysing, recalibrating: swarms, neural learning, HNSW embeddings, consensus validation, workers, memory.

Zero ruflo dependency at runtime. The bot is vanilla Python + ib_insync. If ruflo disappeared, the bot still trades.

---

## What This Is

An automated MNQ (Micro Nasdaq-100 futures) trading bot. Three ORB strategies. IBKR execution. $2,500 account. 1 contract. Fully autonomous on a Hetzner VPS.

Real money. Every line of execution code must be correct.

### The Strategy

Based on u/NeverStoppedout — verified 5-month green streak, 74.91% WR, 2.10 PF, 578 trades, $18,419 profit on TopstepX. Trades MNQ only, 1-2 micros, 15-minute charts, ~2 trades/day.

**Setup 1: ORB Breakout + Retest** — First 15m candle = Opening Range. Breakout on CLOSE beyond OR. Entry on retest of broken level. Stop = opposite side of OR. Target = 1:1 R:R or trail 9EMA.
Confluences: RSI(14) not OB/OS, volume spike on breakout, 9EMA slope confirms.

**Setup 2: 9EMA Continuation** — After ORB establishes direction, enter on pullbacks to 9EMA. Bar wicks through EMA, closes back on trend side. Trail exit on EMA cross.

**Setup 3: Inverse ORB** — Wide OR (top 20th percentile) + failed breakout → fade the failure. Target = OR midpoint. Mutually exclusive with Setup 1.

**Risk:** 1 MNQ ($2/pt). Max $200/trade. Daily -$400 limit. 3 consecutive losses = halt. 20% account drawdown = halt system. Flat by 15:45 ET. Slippage: 1pt. Commission: $0.50 RT.

---

## What Exists (~5,100 lines, 39 files)

### Backtester + Validation (built, audited, bugs fixed):
```
config/strategy_params.yaml      — All tunable strategy parameters
config/risk_params.yaml          — Risk limits, circuit breakers, slippage/commission
backtest/data_loader.py          — CSV/Parquet/IBKR data ingestion
backtest/or_detector.py          — OR detection, breakout, retest, failed breakout
backtest/indicators.py           — RSI, EMA, volume ratio, confluence checker
backtest/backtester.py           — Core engine: bar-by-bar, trade sim, P&L (~850 lines)
backtest/walk_forward.py         — Rolling train/test OOS validation
backtest/monte_carlo.py          — 10K-sim drawdown probability + ruin analysis
backtest/param_sweep.py          — Grid search across parameter combos
backtest/report.py               — HTML report: equity curve, drawdown, breakdowns
tests/test_core.py               — 20 unit tests
db/migrations/001_full_schema.sql — Supabase schema (trades, signals, daily_pnl, system_state, research)
research/crowding_monitor.py     — Weekly ORB popularity tracker (edge decay warning)
STRESS_TEST_AUDIT.md             — Every bug found/fixed, remaining items
```

### Dispatcher (built — auto-triggers analysis workflows):
```
bot/dispatcher.py                — Monitors Supabase, dispatches claude -p workflows
```

### Deployment (built):
```
deploy/setup.sh                  — VPS setup script
deploy/mnq-bot.service           — systemd: trading bot (vanilla Python)
deploy/mnq-watchdog.service      — systemd: flatten watchdog (independent safety)
deploy/mnq-dispatcher.service    — systemd: analysis dispatcher (ruflo-powered)
```

### Ruflo Config (built):
```
.claude/settings.json            — MCP, hooks, agent teams, neural, embeddings
.claude/commands/validate.md     — /validate: backtest pipeline + neural train
.claude/commands/build-phase2.md — /build-phase2: 5-agent swarm build
.claude/commands/recalibrate.md  — /recalibrate: monthly neural-assisted param refresh
.claude/commands/debug.md        — /debug: HNSW trade analysis + neural failure modes
.claude/commands/eval-consensus.md — /eval-consensus: evaluate signal ensemble
```

### Empty (your job — the bot runtime):
```
bot/main.py                      — Entry point, lifecycle management
bot/signal_generator.py          — Live signal detection (shared logic with backtester)
bot/risk_manager.py              — Real-time risk enforcement
bot/execution/ibkr_executor.py   — ib_insync bracket orders
bot/trade_logger.py              — Supabase trade/signal logging
bot/slack_notifier.py            — Trade alerts, daily summary, errors
bot/health_monitor.py            — Heartbeat, connection status
bot/watchdog.py                  — Independent flatten process (15:45 ET)
backtest/strategies/core.py      — Shared strategy logic (backtester + bot import from here)
```

---

## VPS Architecture — Three Processes

```
┌──────────────────────────────────────────────────────────┐
│ mnq-bot.service (RUNTIME — vanilla Python)               │
│ Signal → Risk Check → Bracket Order → IBKR               │
│ Writes trades/signals/daily_pnl to Supabase              │
│ ZERO ruflo dependency                                    │
└──────────────────────┬───────────────────────────────────┘
                       │ writes state
                       ▼
                  [Supabase]
                       ▲
                       │ reads state (every 30 min)
┌──────────────────────┴───────────────────────────────────┐
│ mnq-dispatcher.service (FACTORY — ruflo-powered)         │
│                                                          │
│ Auto-triggers based on Supabase state:                   │
│  • 3+ consecutive losses → claude -p "/debug"            │
│  • Weekly PF < 1.3 → claude -p "/debug"                  │
│  • First Saturday of month → claude -p "/recalibrate"    │
│  • 50 paper trades reached → claude -p "/eval-consensus" │
│  • Sunday → crowding monitor                             │
│  • 4 weeks PF < 1.3 → Slack HALT RECOMMENDATION         │
│                                                          │
│ Each dispatch uses: neural predict, HNSW search,         │
│ memory store, pattern training.                          │
│ Non-critical. If it crashes, bot keeps trading.          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ mnq-watchdog.service (SAFETY — independent)              │
│ Restart=always. Flattens all IBKR positions at 15:45 ET. │
│ Survives bot crash. Cannot stay down.                    │
└──────────────────────────────────────────────────────────┘
```

The bot writes. The dispatcher reads and reacts. The watchdog protects.
If the dispatcher dies, the bot keeps trading (you lose auto-analysis, not trading).
If the bot dies, the watchdog still flattens (you lose trading, not safety).

---

## The Build — Prioritised

### Phase 1: Validate the Backtester (THIS WEEK)

Use `/validate` or manually:

```bash
npx ruflo@latest hooks pre-task --description "Phase 1: Backtest validation"
npx ruflo@latest neural predict --task "optimal ORB params" 2>/dev/null  # Use priors if available

# 1a. Acquire 12+ months MNQ 15m data → scripts/fetch_data.py
# 1b. Run backtest → python -m backtest.backtester data/mnq_15m.parquet
# 1c. Param sweep → python -m backtest.param_sweep data/mnq_15m.parquet --quick
# 1d. Walk-forward → python -m backtest.walk_forward data/mnq_15m.parquet
#     GATE: ≥75% OOS windows profitable
# 1e. Monte Carlo → run_monte_carlo(results, account_size=2500)
#     GATE: <5% ruin probability
# 1f. Report → generate_report(results, "reports/v1.html")

# Store + train
npx ruflo@latest memory store --namespace mnq-bot --key "backtest:$(date +%Y%m%d)" --value '<results>' --reasoningbank
npx ruflo@latest neural train --model-type moe --epochs 5
npx ruflo@latest hooks post-task --task-id "phase1" --success true --train-neural true
```

**STOP if gates fail.** No Phase 2 on unvalidated params.

### Phase 2: Live Bot + IBKR Paper (WEEKS 2-4)

Use `/build-phase2` — deploys 5-agent swarm:

```
mcp__claude-flow__swarm_init({ topology: "hierarchical", maxAgents: 6, strategy: "specialized" })

Task("Architect") → Extract shared strategy logic into backtest/strategies/core.py
Task("Coder-Execution") → bot/execution/ibkr_executor.py (bracket orders)
Task("Coder-Signal") → bot/signal_generator.py (imports shared module)
Task("Coder-Safety") → bot/watchdog.py (independent flatten process)
Task("Tester") → TDD on all modules with mock IBKR
```

Post-build: `audit` worker + `testgaps` worker + `security scan` on all execution code.

**Critical build rules:**
1. Signal generator imports from same module as backtester. NEVER rewrite strategy logic.
2. Every entry = bracket order (entry + stop + target). Never naked entries.
3. Flatten watchdog = independent process. Survives bot crash.
4. Connection loss >120s with position → Slack alert, do NOT flatten on stale connection.
5. Position reconciliation every 5 min. Divergence → alert + halt.
6. Runtime code has ZERO ruflo dependency.

**Paper trading with consensus validation:**
Enable 3-agent consensus (mechanics / regime / cross-market) to evaluate each signal.
After 50 trades, the dispatcher auto-triggers `/eval-consensus`.
If ensemble improves WR by ≥5%, extract scoring logic into vanilla Python function.

**Paper minimum: 4 weeks / 50+ trades.**

### Phase 3: Monitoring (PARALLEL)

**Slack alerts** (bot/slack_notifier.py): trade notifications, daily summary, errors.
**Crowding monitor**: auto-runs Sundays via dispatcher.
**Dashboard**: optional Next.js — Slack covers 90%.

### Phase 4: Live ($2,500)

All gates must pass. Change IBKR port 7497→7496 in .env. Start all three services.

### Ongoing: Automatic Maintenance

Once live, the dispatcher handles ongoing maintenance autonomously:

| Trigger | Condition | Action |
|---------|-----------|--------|
| Auto-debug | 3 consecutive losses | `claude -p "/debug"` + Slack alert |
| Auto-debug | Weekly PF < 1.3 | `claude -p "/debug"` + Slack alert |
| Auto-recalibrate | First Saturday of month | `claude -p "/recalibrate"` (neural-assisted) |
| Auto-eval | 50 paper trades reached | `claude -p "/eval-consensus"` |
| Crowding check | Every Sunday | `crowding_monitor.py` → Slack if spike |
| Halt recommendation | 4 weeks PF < 1.3 | Slack "🛑 HALT" (human decides) |

All triggers have cooldowns and minimum thresholds. Config is in `bot/dispatcher.py` TRIGGERS dict.

---

## Ruflo Features — Where Each Is Used

| Feature | Where | Purpose |
|---------|-------|---------|
| **Swarm** | `/build-phase2` | 5 parallel agents build the bot |
| **Neural (SONA)** | `/validate`, `/recalibrate` | Learn which params work, start from priors |
| **HNSW embeddings** | `/debug`, daily trade indexing | Semantic search over trade history |
| **Consensus** | Paper trading signal eval | 3-agent ensemble reduces false breakouts |
| **Memory** | Everywhere | Persist decisions, results, patterns across sessions |
| **Hooks** | Every file edit | Auto-test, pattern learning, quality gates |
| **Workers** | Execution code edits | Auto audit + testgaps on safety-critical code |
| **AQE plugin** | Phase 2 build | TDD on IBKR executor and risk manager |
| **MoE routing** | All builds | Haiku for simple edits, Sonnet for architecture |
| **Slash commands** | Manual + auto-dispatched | 5 commands covering full lifecycle |

**None of these touch runtime.** The bot is vanilla Python. Ruflo is the factory.

---

## Decision Framework

**Tier 1 / Level 0-1**: Routine coding, tests, logging, docs. Just do it.
**Tier 2 / Level 1-2**: New deps, schema changes, pattern deviations. Do it, flag.
**Tier 3 / Level 2-3**: Trade execution logic, risk params, IBKR handling, git push. Stop, ask.

Unsure? → Tier 3. Money is at stake.

---

## Validation Gate

Before writing application code:

1. **Read** all files in `backtest/`, both config YAMLs, `tests/test_core.py`, `STRESS_TEST_AUDIT.md`, `bot/dispatcher.py`.

2. **Write `VALIDATION.md`** answering:
   - OR detection correct? Breakout requires CLOSE not wick?
   - Retest mechanically sound? Pullback + rejection candle?
   - Inverse ORB and standard ORB mutually exclusive?
   - EMA continuation can't enter while ORB trade open?
   - Slippage, commissions, flatten time all enforced?
   - Walk-forward criteria? Monte Carlo threshold?
   - Dispatcher triggers: do the cooldowns and thresholds make sense?
   - Three-process VPS architecture: is the separation clean?
   - Gaps or bugs — file names, line numbers.

3. **Write `BUILD_PLAN.md`** with:
   - Prioritised tasks for Phase 1
   - Changes to existing code before first backtest
   - Shared strategy module design (backtest + bot import from same source)
   - Phase 2 component effort estimates
   - Which ruflo features per phase and why
   - Consensus validation design for paper trading
   - Dispatcher trigger thresholds: any adjustments?

4. **Store validation in memory:**
   ```bash
   npx ruflo@latest memory store --namespace mnq-bot --key "init-validation" --value "[summary]" --reasoningbank
   ```

5. **Stop.** Wait for review before writing application code.

```bash
npx ruflo@latest hooks post-task --task-id "init-validation" --success true --train-neural true
```

Begin.
