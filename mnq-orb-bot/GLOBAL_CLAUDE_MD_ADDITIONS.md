# ============================================================
# RUFLO INTEGRATION — Append to ~/.claude/CLAUDE.md
# ============================================================
# Ruflo is the factory. The products it builds are dependency-free.
# Full capabilities for building, testing, analysing.
# Zero ruflo dependency in runtime code.

## Setup (one-time)
# claude mcp add claude-flow -- npx -y ruflo@latest mcp start
# npx ruflo@latest init (per-project)
# npx ruflo@latest plugins install @claude-flow/plugin-agentic-qe

## 3-Tier Model Routing (always active)
# Automatically route to cheapest capable model:
# Tier 1 — Agent Booster (WASM): var→const, add-types, add-logging. $0, <1ms.
# Tier 2 — Haiku: simple tasks, low complexity (<30%). $0.0002.
# Tier 3 — Sonnet/Opus: complex reasoning, architecture, security. $0.003-0.015.
# Check for [AGENT_BOOSTER_AVAILABLE] or [TASK_MODEL_RECOMMENDATION] before spawning agents.

## Intelligent Task Routing

Classify every task and deploy the right infrastructure:

### Level 0 — Trivial (no ruflo)
Single file edit, config change, quick question, typo.
→ Just do it.

### Level 1 — Standard (hooks)
1-3 files. Bug fix, adding a function, writing tests.
```
npx ruflo@latest hooks pre-task --description "[task]"
# ... work ...
npx ruflo@latest hooks post-task --task-id "[id]" --success true --train-patterns
npx ruflo@latest hooks post-edit --file "[file]" --train-patterns
```

### Level 2 — Complex (swarm + memory + learning)
Multi-file feature, new subsystem, refactoring across modules.
```
mcp__claude-flow__swarm_init({ topology: "hierarchical", maxAgents: 6, strategy: "specialized" })
# Spawn architect, coder(s), tester in parallel via Task tool
# Store decisions in memory for cross-session persistence
# Post-task: train neural patterns on what worked
npx ruflo@latest hooks post-task --task-id "[id]" --success true --train-neural true
```

### Level 3 — Critical (full pipeline)
Money handling, auth, unattended execution, external APIs with side effects.
Everything from Level 2, plus:
```
npx ruflo@latest hooks worker dispatch --trigger audit        # Security scan
npx ruflo@latest hooks worker dispatch --trigger testgaps     # Coverage analysis
npx ruflo@latest security scan --depth full                   # Full security review
# AQE: TDD cycle on critical paths
# Consensus validation if applicable (multiple agents evaluate independently)
```

### Auto-Detection
Level 2+ when: 4+ files, new module, new external integration, schema changes.
Level 3 when: financial transactions, credentials, unattended code, data loss risk.

## Neural Learning — Cross-Session Intelligence

Ruflo learns from every task. Enable the full loop:

```
# After successful complex task — train patterns
npx ruflo@latest hooks post-task --task-id "[id]" --success true --train-neural true

# Before starting similar task — retrieve learned patterns
npx ruflo@latest neural predict --task "[task description]"

# Periodic training on accumulated patterns
npx ruflo@latest neural train --model-type moe --epochs 10

# View what's been learned
npx ruflo@latest neural patterns --recent 20
```

Use neural learning for: parameter optimization priors, recurring build patterns, debugging patterns.
Neural learning does NOT touch runtime code — it informs the developer/agent, not the product.

## HNSW Embeddings — Semantic Search Over Project Knowledge

Index project artifacts for fast semantic search:

```
# Initialize embeddings
npx ruflo@latest embeddings init --model all-mpnet-base-v2

# Store and index artifacts
npx ruflo@latest memory store --namespace project --key "[key]" --value "[content]" --reasoningbank

# Semantic search
npx ruflo@latest memory search --namespace project --query "[natural language query]" --reasoningbank
```

Use for: searching trade history patterns, finding similar past decisions, debugging by similarity.

## Memory — Persistent Cross-Session State

```
# Store decisions, validated results, patterns
npx ruflo@latest memory store --namespace [project] --key "[key]" --value "[value]"

# Search
npx ruflo@latest memory search --namespace [project] --query "[topic]"

# Retrieve specific
npx ruflo@latest memory retrieve --namespace [project] --key "[key]"
```

Store: architectural decisions, validated metrics, gotchas, patterns that worked/failed.
Do NOT store: code (belongs in files), config values (belongs in YAML), temporary state.

## Workers — Background Quality Gates

```
npx ruflo@latest hooks worker dispatch --trigger testgaps   # Find untested code
npx ruflo@latest hooks worker dispatch --trigger audit      # Security analysis
npx ruflo@latest hooks worker dispatch --trigger optimize   # Performance analysis
npx ruflo@latest hooks worker dispatch --trigger deepdive   # Deep code analysis
```

## Hooks — Always Active (via .claude/settings.json)

| Hook | When | What |
|------|------|------|
| post-edit | Every file save | Pattern learning, auto-test on critical files |
| pre-task | Before work | Retrieve relevant patterns + memory |
| post-task | After work | Store patterns, update metrics, train neural |
| teammate-idle | Agent finishes | Auto-assign next task |
| task-completed | Task done | Train patterns from success/failure |

## Existing Decision Framework (preserved)
Tier 1/2/3 from global CLAUDE.md still applies.
Ruflo levels govern HOW to coordinate. Tiers govern WHAT to escalate.
