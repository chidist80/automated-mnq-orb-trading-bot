# Execution Hygiene Design — MNQ ORB Trading Bot

**Date:** 2026-04-25
**Status:** Approved by user (council debate, 2026-04-25)
**Scope:** Phase 2 (live execution) and beyond. Applies to this repo plus globally to all projects on this machine.
**Branch at design time:** `phase2/live-execution`

---

## 1. Why this exists

The user is about to write Tier 3 live-execution code (bracket orders, watchdog, risk manager) for a real-money MNQ futures account ($2,500, 1 contract, IBKR). The current configuration has three problems:

1. **Stale prompts.** Project `CLAUDE.md`, `GLOBAL_CLAUDE_MD_ADDITIONS.md`, and `mnq-orb-bot/.claude/settings.json` all describe ruflo as the "build factory." Commit `b7da8e5` dropped ruflo from Phase 2. The prompts and the plan now disagree — the model reads instructions Anthropic's 4.7 release notes call "literally," so contradictions create drift.
2. **No behavioral floor.** Global `CLAUDE.md` is plugin-routing only. There is no anti-shortcut rule, no anti-fabrication invocation, no verification gate, no research-first rule, no multi-aspect review pattern — none of the things the user asked for.
3. **No project-tuned review surface.** Generic code reviewers cannot catch trading-domain bugs (tick-value drift, OCA orphaning, look-ahead bias, reconciliation gaps). The local `/audit-project` command is whole-codebase scoped and uses generic agents.

The user explicitly asked for `/ultrareview`/`/ultraplan` *behavior* without paying the cloud bill. The design replicates the multi-aspect parallel-review pattern using locally installed agents plus one project-tuned addition.

---

## 2. Council debate that produced this design

A 5-seat council debate ran in parallel: Minimalist, Maximalist, Token-Economist, Trading-Domain, Anthropic-Prompting. Three returned full arguments; two hit a per-account rate limit and were steelmanned by the synthesizer.

### Convergence (3+ seats)

- CLAUDE.md must be terse — global ≤110 lines, project ≤80 lines. Opus 4.7 tokenizer is 1.0–1.35× heavier; every line re-loads each turn.
- Stale ruflo content must be **deleted**, not archived. Archive doc rot is real.
- Don't *restate* skill content in CLAUDE.md — *invoke* skills. Otherwise drift between two sources of truth.
- Hooks emit structured stderr signals; they don't auto-run pytest, and they don't block via exit codes. Auto-running tests on every save is hostile during exploratory edits, and 4.7 reads stderr as guidance better than it tolerates exit-code blocking.

### Divergence and resolution

| Question | Decision | Why |
|---|---|---|
| Build `/deepreview` or rely on `/audit-project`? | **Build `/deepreview`** | `/audit-project` is whole-codebase and uses generic agents. We need a project-tuned panel firing per-task on Tier 3 changes. |
| Build `/deepplan`? | **No** | `superpowers:brainstorming → writing-plans → executing-plans` is the existing pipeline. Adding a wrapper duplicates it. |
| Custom domain reviewer agent? | **Yes — non-negotiable** | Trading-Domain seat named 5 specific bugs (tick-value drift, OCA orphaning, RTH/ETH bar contamination, look-ahead, reconciliation gap) that no generic reviewer flags. |
| Hook enforcement strength? | **Soft signaling** | Hard exit-code blocking frustrates the loop in 4.7. Structured stderr signals route the model correctly without breaking turns. |
| Auto-pytest on edit? | **No** | Exploratory edits get hijacked. Replace with a Stop-hook checklist signal. |

---

## 3. The design

### 3.1 Global `~/.claude/CLAUDE.md`

**Cap:** ≤110 lines (currently 75; ~35 lines of room for the three new sections).

Keep the existing **plugin auto-routing table** unchanged.

Add three new sections, each rule using a *rule + reason + scope* triple (4.7-correct format — bare imperatives drift):

- **Verification & Honesty.** Before claiming any code change complete, invoke `superpowers:verification-before-completion`. *Why:* 4.7 will otherwise produce confident completion claims when test signal is absent. *Scope:* edits to source code; excludes pure docs.
- **Research-First.** Before editing code that imports an unfamiliar library API, run a `context7` lookup OR cite an existing usage in the repo. *Why:* 4.7 misremembers signatures; an outdated method call costs hours or money. *Scope:* edits importing external SDKs (financial, broker, database, messaging).
- **Multi-aspect Review.** Meaningful work products go through `/deepreview` (project-local, when defined) or `/audit-project --recent` before being declared done. *Why:* single-pass review misses cross-cutting concerns. *Scope:* changes that close a planned task or epic.

**Anti-pattern (do not include):** anti-lying lectures, "be thorough" exhortations, restated skill checklists, decision trees longer than 6 lines.

### 3.2 Project `mnq-orb-bot/CLAUDE.md` — wholesale rewrite

**Cap:** ≤80 lines.

Delete every ruflo reference. New structure:

- **Project Facts** (≤20 lines). Account size $2,500; contract MNQ ($2/point, $0.50/tick, multiplier 2); position cap 1 contract until 50+ paper trades pass graduation; IBKR demo account `DUO707586`, market data type 3 (delayed); RTH only.
- **Tier 3 file list** (≤10 lines). The 7 paths: `bot/execution/**`, `bot/watchdog.py`, `bot/risk_manager.py`, `bot/health_monitor.py`, `bot/signal_generator.py`, `backtest/strategies/core.py`, `config/risk_params.yaml`, `config/strategy_params.yaml`. Editing any of them requires `/deepreview` before task closure.
- **Phase pointer** (≤5 lines). Current phase = Phase 2 live execution. Plan: `mnq-orb-bot/docs/superpowers/plans/2026-04-25-phase2-live-execution.md`. Branch: `phase2/live-execution`.
- **Mock-fidelity rule** (≤8 lines). Execution-path tests must replay recorded ib_insync events (with errors, partial fills, ≥100ms simulated latency). Idealized mocks (`fill = limit_price` instantly) are rejected.
- **No-shortcut covenant** (≤8 lines). When blocked, raise the blocker explicitly. Don't fabricate a workaround that "looks right." Don't suppress an exception to make a test pass. Don't disable the watchdog to make a check green.
- **Brainstorming brief for Tier 3 epics** (≤10 lines). Any Tier 3 epic brainstorm must produce explicit answers to: max position, max daily loss, connection-loss behavior, watchdog independence proof, reconciliation behavior on reconnect.

### 3.3 `/deepreview` slash command (NEW)

**Path:** `mnq-orb-bot/.claude/commands/deepreview.md`.

**Trigger:** task boundaries — when the model is about to declare a Phase 2 epic task complete and the change touched any Tier 3 path.

**Pattern:** dispatch 4 review agents in parallel; aggregate findings by severity; iterate fixes (audit-project loop) until zero critical/high open.

**Agent panel:**

1. `pr-review-toolkit:silent-failure-hunter` — catches swallowed exceptions, broad `except`, fallback hides, sentinel-on-error patterns.
2. `pr-review-toolkit:pr-test-analyzer` — verifies new code is exercised by meaningful tests, not path-matched.
3. `pr-review-toolkit:type-design-analyzer` — invariants on `Order`, `Bracket`, `Position`, `Signal` types — encapsulation and enforcement quality.
4. `risk-mechanics-reviewer` (custom, see §3.4) — trade-killer bugs and operator UX.

**Output contract:** each agent returns JSON findings (file:line, severity ∈ {critical, high, medium, low}, category, description, suggested fix). The orchestrator (the calling Claude turn) merges, deduplicates, applies fixes for critical/high, re-runs verification, re-dispatches the panel, and only declares the task complete when critical/high count is zero.

**Cost envelope:** ~30–50k input + ~10–20k output per fire ≈ $0.40–$0.70. Across Phase 2 (estimated 25–40 task-boundary fires) = $10–$30 total.

### 3.4 `risk-mechanics-reviewer` custom agent (NEW)

**Path:** `mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md`.

**Charter:** review code from a trading-systems perspective, not a generic engineering perspective. Focus areas:

1. **Contract arithmetic.** Tick/point/dollar conversions must reference the contract spec, not hardcoded constants. MNQ = $2/point, $0.50/tick, multiplier 2. A "10-point stop" must be derivable from the spec object.
2. **OCA group integrity.** Bracket orders bind parent → stop + target via OCA. On amendment or partial fill, the OCA group ID must be reused, not regenerated. Verify resubmission paths preserve the group.
3. **Session boundaries.** RTH bars only. `whatToShow='TRADES'` and `useRTH=1` on historical requests; bar 0 timestamp must be 9:30:00 ET, not 18:00 ET prior day.
4. **Look-ahead bias.** Signals on bar `t` use bar `t-1` close. Indicator state must be frozen at the moment the signal fires.
5. **Reconnection reconciliation.** On every reconnect, call `reqPositions()` and reconcile broker state with bot state before any new order submission.
6. **Mock fidelity.** Reject mocks that return success in <100ms simulated time, don't model partial fills, don't surface IBKR error codes (201 margin, 202 cancelled, 100 max-rate).
7. **Operator UX.** Alerts must answer "is my money safe right now?" in line one — flat/long/short, qty, broker state, recommended action. Stack traces alone are insufficient.

**Output format:** same JSON finding schema as the other panel agents.

### 3.5 Hooks — light, signaling only

**File:** `mnq-orb-bot/.claude/settings.json`. Wholesale rewrite — remove all ruflo hooks.

Three hooks, all emit structured stderr (text the model treats as guidance), none block the turn:

- **SessionStart.** Print active branch, last commit message, current Phase 2 epic from the plan file, and any Tier 3 paths edited in the last commit.
- **PostToolUse on `Edit`/`Write`** matching Tier 3 globs. Emit: `[hygiene] Tier 3 path touched: <path>. Reminder: invoke /deepreview at task boundary before claiming done.`
- **Stop.** If the turn's transcript contains completion-claim language (`tests pass`, `fixed`, `done`, `works now`, `all green`) without a fresh pytest invocation in the same turn, emit: `[hygiene] Completion claim detected without fresh test evidence. Re-check verification-before-completion before closing the turn.`

**Why signaling, not blocking:** Anthropic-Prompting seat is correct that 4.7 routes well on stderr guidance and reacts poorly to exit-code blocks. Token-Economist seat is correct that hooks should let the model plan around them, not gate the turn.

### 3.6 Cleanup

- **Delete:** `mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md` (stale ruflo doc).
- **Rewrite:** `mnq-orb-bot/CLAUDE.md` (per §3.2).
- **Rewrite:** `mnq-orb-bot/.claude/settings.json` (per §3.5; current file is full of ruflo `npx` hooks).
- **Append-only:** `~/.claude/CLAUDE.md` (per §3.1; existing routing table preserved).

---

## 4. Files changed

| Path | Action |
|---|---|
| `~/.claude/CLAUDE.md` | Append three sections (Verification & Honesty, Research-First, Multi-aspect Review) |
| `mnq-orb-bot/CLAUDE.md` | Wholesale rewrite |
| `mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md` | Delete |
| `mnq-orb-bot/.claude/settings.json` | Wholesale rewrite (ruflo removed, signaling hooks added) |
| `mnq-orb-bot/.claude/commands/deepreview.md` | Create |
| `mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md` | Create |

Existing project commands (`build-phase2.md`, `debug.md`, `eval-consensus.md`, `recalibrate.md`, `validate.md`) remain untouched in scope of this design — they are stale-ruflo-flavored and will be revisited in a follow-up sweep, not here.

---

## 5. Acceptance criteria

- [ ] Global `~/.claude/CLAUDE.md` contains three new triple-format sections; total file ≤110 lines.
- [ ] Project `CLAUDE.md` ≤80 lines, no `ruflo` references, includes Tier 3 file list and mock-fidelity rule.
- [ ] `mnq-orb-bot/.claude/settings.json` has zero `ruflo`/`claude-flow` references.
- [ ] `/deepreview` command file exists, dispatches the 4-agent panel, includes the iteration loop.
- [ ] `risk-mechanics-reviewer` agent file exists with all 7 charter areas covered.
- [ ] Hooks emit structured stderr (verifiable by inspecting hook scripts) and never call exit non-zero on hygiene violations.
- [ ] `GLOBAL_CLAUDE_MD_ADDITIONS.md` deleted from project tree.
- [ ] Running `/deepreview` against the regression-test commit (`c0f6333`) produces JSON findings without errors.

---

## 6. Out of scope

- Rewriting the existing project commands (`build-phase2.md`, `debug.md`, `eval-consensus.md`, `recalibrate.md`, `validate.md`). They are stale-ruflo-flavored but unused on the current path; revisit in a follow-up.
- Building a `/deepplan` slash command. The existing `brainstorming → writing-plans → executing-plans` pipeline covers this.
- New superpowers skills. The existing surface is sufficient.
- Anything in `bot/`, `backtest/`, `tests/`, or `scripts/` source code. This design is purely about Claude-facing configuration.
