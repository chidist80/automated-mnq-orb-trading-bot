# CLAUDE.md — MNQ ORB Trading Bot

## FIRST RUN
Read `INIT_PROMPT.md` first. Write VALIDATION.md + BUILD_PLAN.md. Wait for approval.

## Core Principle
**Ruflo is the factory. The bot is the product.**
Full ruflo capabilities for building, testing, analysing, and recalibrating.
Zero ruflo dependency at runtime. The bot is vanilla Python + ib_insync.
If ruflo disappeared tomorrow, the bot still trades.

## Project Context
Automated MNQ futures trading bot. Three ORB strategies. IBKR execution. $2,500 account. Real money.

---

## Ruflo Configuration

### MCP + Plugins (project setup)
```bash
claude mcp add claude-flow -- npx -y ruflo@latest mcp start
npx ruflo@latest init
npx ruflo@latest plugins install @claude-flow/plugin-agentic-qe
npx ruflo@latest embeddings init --model all-mpnet-base-v2
```

### Task Classification for This Project

| Task | Level | Ruflo Features |
|------|-------|----------------|
| Edit config YAML | 0 | None |
| Add backtest metric | 1 | Hooks (post-edit, pattern learn) |
| Build signal generator | 2 | Swarm (architect+coder+tester), memory |
| Build IBKR executor | 3 | Swarm + audit worker + AQE TDD + security scan |
| Build flatten watchdog | 3 | Swarm + audit + consensus review |
| Run param sweep | 1 | Hooks + neural predict (use priors from last run) |
| Monthly recalibration | 2 | Neural train + HNSW pattern search + memory store |
| Debug a losing trade | 1 | HNSW search over trade history |
| Paper trading signal validation | 2 | Consensus (3 agents evaluate signal) |
| Slack notifier | 1 | Hooks only |

---

## Neural Learning — Recalibration Loop

The bot's parameters degrade over time as market conditions shift. Instead of blind monthly grid search, use ruflo's neural layer to start from learned priors:

### After Every Backtest Validation
```bash
# Store results with full context
npx ruflo@latest memory store --namespace mnq-bot --key "backtest:$(date +%Y%m%d)" \
  --value '{"params": {...}, "wr": 0.72, "pf": 1.95, "dd": -480, "wf_pass": 0.83, "mc_ruin": 0.03}' \
  --reasoningbank

# Train neural patterns on what worked
npx ruflo@latest neural train --model-type moe --epochs 5
npx ruflo@latest hooks post-task --task-id "backtest-$(date +%Y%m%d)" --success true --train-neural true
```

### Monthly Recalibration (1st weekend of each month)
```bash
# 1. Retrieve learned patterns from previous months
npx ruflo@latest neural predict --task "optimal ORB params for current MNQ regime"
npx ruflo@latest memory search --namespace mnq-bot --query "backtest results" --reasoningbank

# 2. Use predictions as starting point for param sweep (narrow the grid)
# Instead of sweeping 10 dimensions blindly, neural layer suggests top 3 configs to test first

# 3. Run focused sweep → walk-forward → monte carlo

# 4. Store new results and train
npx ruflo@latest memory store --namespace mnq-bot --key "recalib:$(date +%Y%m%d)" --value '...' --reasoningbank
npx ruflo@latest neural train --model-type moe --epochs 10

# 5. If params shifted, deploy new config to bot (restart required)
```

### Pattern Accumulation Over Time
```
Month 1: Blind grid search → find optimal params → train neural
Month 2: Neural suggests starting configs → faster convergence → train again
Month 3: Neural has 2 months of priors → suggests regime-specific params
Month 6: Neural recognises "high VIX + wide OR" regime → recommends inverse ORB bias
Month 12: System has learned seasonal patterns, regime transitions, decay signals
```

---

## HNSW Embeddings — Trade Pattern Analysis

Every trade logged to Supabase gets indexed for semantic search. This is the debugging and analysis layer.

### Setup
```bash
npx ruflo@latest embeddings init --model all-mpnet-base-v2
```

### After Each Trading Day (automated in daily summary)
```bash
# Index today's trades
npx ruflo@latest memory store --namespace trades --key "trade:$(date +%Y%m%d):001" \
  --value '{"setup": "orb_breakout", "direction": "long", "or_size": 85, "rsi_entry": 52, "result": "win", "pnl": 180, "time": "10:15"}' \
  --reasoningbank
```

### Analysis Queries
```bash
# "Why did I lose on that wide OR short?"
npx ruflo@latest memory search --namespace trades --query "wide opening range short loss" --reasoningbank

# "What do my winning ORB retests look like?"
npx ruflo@latest memory search --namespace trades --query "orb retest win high profit" --reasoningbank

# "How do I perform in the last 30 minutes before lunch?"
npx ruflo@latest memory search --namespace trades --query "trades between 11:30 and 12:00" --reasoningbank

# "Find trades with similar conditions to today's setup"
npx ruflo@latest memory search --namespace trades --query "or_size 90 rsi 55 bullish 9ema slope positive" --reasoningbank
```

This replaces writing custom pandas analysis scripts for every ad-hoc question. The HNSW index gives approximate semantic matches — when you need exact filtering, use SQL against Supabase directly.

---

## Consensus — Paper Trading Signal Validation

During paper trading (Phase 2), add a consensus layer that evaluates each signal from 3 independent angles before execution. This is an ensemble approach to reduce false breakouts.

### Architecture (paper trading only)
```
Signal Generator detects potential trade
         │
         ▼
┌─────────────────────────────────────────┐
│  Consensus Layer (ruflo hive-mind)      │
│                                         │
│  Agent 1: Mechanics                     │
│  "Is the retest clean? Is the breakout  │
│   candle strong? OR size normal?"       │
│                                         │
│  Agent 2: Regime                        │
│  "Is this a trending day or choppy?     │
│   ATR normal? Gap day?"                 │
│                                         │
│  Agent 3: Cross-Market                  │
│  "Is ES confirming NQ direction?        │
│   VIX stable? Sector rotation?"         │
│                                         │
│  Consensus: 2/3 must agree → EXECUTE    │
│             1/3 or 0/3    → SKIP        │
└─────────────────────────────────────────┘
         │
         ▼
    IBKR Paper Order (if consensus passed)
```

### Implementation
```bash
# In paper trading mode, before each trade:
mcp__claude-flow__swarm_init({ topology: "hierarchical", maxAgents: 3, strategy: "specialized" })

# Each agent gets the same signal data, evaluates independently
Task("Mechanics", "Evaluate this ORB signal: [data]. Is the retest clean? Score 0-10.", "researcher")
Task("Regime", "What's the current market regime? Is this a trending or choppy day? Score 0-10.", "researcher")  
Task("CrossMarket", "Check ES/NQ correlation. Is the broader market confirming this direction? Score 0-10.", "researcher")

# Aggregate: if avg score >= 7, execute. Otherwise skip and log reason.
```

### Validation Protocol
- Run consensus alongside standard signals for 50 paper trades
- Track: consensus-approved signals vs all signals
- If consensus-filtered WR > unfiltered WR by 5%+, consider for live
- If no improvement, remove the consensus layer (it's just adding latency)
- Decision stored in memory for future reference

### Live Trading: Consensus OFF by Default
The live execution path stays clean: signal → risk check → bracket order.
Consensus is a paper-trading research tool until proven.
If validated, consensus becomes a pre-trade filter (still no ruflo runtime dependency — the logic gets extracted into a Python function).

---

## Swarm Patterns for This Project

### Phase 2 Build (multi-agent parallel)
```
mcp__claude-flow__swarm_init({ topology: "hierarchical", maxAgents: 6, strategy: "specialized" })

Task("Architect", "Extract shared strategy logic from backtester._process_day() into backtest/strategies/core.py. Store design in memory namespace 'mnq-bot'.", "system-architect")
Task("Coder-Execution", "Build bot/execution/ibkr_executor.py. Bracket orders via ib_insync. Read architect design from memory.", "coder")
Task("Coder-Signal", "Build bot/signal_generator.py importing from shared strategy module.", "coder")
Task("Coder-Safety", "Build flatten watchdog as independent process. Flattens at 15:45 ET regardless.", "coder")
Task("Tester", "TDD on all Phase 2 modules. Mock IBKR. Focus: brackets, risk limits, flatten, connection loss.", "tester")
```

### Execution Code Review (post-build)
```
npx ruflo@latest hooks worker dispatch --trigger audit
npx ruflo@latest hooks worker dispatch --trigger testgaps
npx ruflo@latest security scan --depth full
# AQE TDD cycles on ibkr_executor.py and risk_manager.py
```

### Debug Session (when investigating a losing streak)
```
npx ruflo@latest hooks pre-task --description "Debug losing streak: 5 consecutive losses on ORB retest"
npx ruflo@latest memory search --namespace trades --query "consecutive losses orb retest" --reasoningbank
npx ruflo@latest memory search --namespace mnq-bot --query "regime detection choppy market" --reasoningbank
npx ruflo@latest neural predict --task "why are ORB retests failing in current conditions"
```

---

## Quality Gates by File Path

| Path | On Edit | Workers Dispatched |
|------|---------|--------------------|
| `bot/execution/**` | post-edit + train, pytest, security scan | audit, testgaps |
| `bot/health_monitor.py` | post-edit + train, pytest | audit |
| `bot/signal_generator.py` | post-edit + train, pytest | testgaps |
| `backtest/**` | post-edit + train, pytest tests/test_core.py | — |
| `config/**` | — | — |
| `research/**` | post-edit + train | — |

---

## Tech Stack

**Factory (ruflo — build time only):**
ruflo MCP, hooks, memory, neural, HNSW, AQE plugin, swarm, consensus, workers

**Product (runtime — zero ruflo dependency):**
Python 3.12, pandas, pandas-ta, ib_insync, supabase-py, slack-sdk, fastapi, uvicorn, praw

## Decision Framework
- **Tier 1 / Level 0-1**: Routine coding, tests, logging, docs, bug fixes
- **Tier 2 / Level 1-2**: New deps, schema changes, pattern deviations
- **Tier 3 / Level 2-3**: Trade execution logic, risk params, IBKR handling, git push

Unsure? → Tier 3. Money is at stake.
