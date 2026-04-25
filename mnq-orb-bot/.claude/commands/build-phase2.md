Build Phase 2 — live signal bot and IBKR execution layer.

Pre-flight:
1. Query memory for validated backtest: `npx ruflo@latest memory search --namespace mnq-bot --query "backtest validated"`
2. If no validated backtest exists, STOP. Run /validate first.
3. Retrieve architectural decisions: `npx ruflo@latest memory search --namespace mnq-bot --query "architecture decisions"`

Build with swarm:
```
mcp__claude-flow__swarm_init({ topology: "hierarchical", maxAgents: 6, strategy: "specialized" })

Task("Architect", "Extract shared strategy logic from backtester._process_day() into backtest/strategies/core.py. Both backtester and live bot import from same module. Store design in memory namespace 'mnq-bot'.", "system-architect")
Task("Coder-Execution", "Build bot/execution/ibkr_executor.py. Bracket orders via ib_insync. Read architect design from memory.", "coder")
Task("Coder-Signal", "Build bot/signal_generator.py importing from shared strategy module.", "coder")
Task("Coder-Safety", "Build bot/health_monitor.py with independent flatten watchdog process.", "coder")
Task("Tester", "TDD on all Phase 2 modules. Mock IBKR. Focus: brackets, risk limits, flatten, connection loss.", "tester")
```

Post-build quality gates:
```
npx ruflo@latest hooks worker dispatch --trigger audit
npx ruflo@latest hooks worker dispatch --trigger testgaps
npx ruflo@latest security scan --depth full
```

Store decisions:
```
npx ruflo@latest memory store --namespace mnq-bot --key "decision:shared-strategy-module" --value "Strategy logic lives in backtest/strategies/core.py" --reasoningbank
npx ruflo@latest memory store --namespace mnq-bot --key "decision:bracket-orders" --value "Every entry is a bracket order. Never naked entries." --reasoningbank
npx ruflo@latest memory store --namespace mnq-bot --key "decision:flatten-watchdog" --value "Independent process. Survives main bot crash." --reasoningbank
npx ruflo@latest hooks post-task --task-id "phase2-build" --success true --train-neural true
```

Critical rules:
- Signal generator imports from same module as backtester. NEVER rewrite strategy logic.
- Every entry = bracket order. Never naked entries.
- Flatten watchdog = independent process.
- Connection loss >120s → Slack alert, do NOT flatten on stale connection.
- Runtime code has ZERO ruflo dependency. Vanilla Python + ib_insync only.
