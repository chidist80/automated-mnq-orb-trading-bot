#!/usr/bin/env bash
# SessionStart hook — emit project context to stderr so Claude sees branch,
# phase, and recent Tier 3 changes at session boundary. Signaling only.
#
# Contract: receives JSON on stdin describing the SessionStart event (ignored
# here). Emits to stderr (treated as guidance by Claude). Always exits 0.

set -u

REPO_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$REPO_DIR" 2>/dev/null || exit 0

# Discard stdin so we don't block on the hook payload.
cat >/dev/null

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
LAST_COMMIT="$(git log -1 --pretty='%h %s' 2>/dev/null || echo none)"

# Phase pointer: extract the most recent Phase plan filename from
# docs/superpowers/plans/ (sorted by name = sorted by date).
PHASE_PLAN="$(ls -1 docs/superpowers/plans/*.md 2>/dev/null | sort | tail -1 | xargs -I{} basename {})"

# Tier 3 paths edited in last commit
TIER3_GLOBS=(
  "bot/execution/"
  "bot/watchdog.py"
  "bot/risk_manager.py"
  "bot/health_monitor.py"
  "bot/signal_generator.py"
  "backtest/strategies/core.py"
  "config/risk_params.yaml"
  "config/strategy_params.yaml"
)

CHANGED_T3=""
LAST_DIFF="$(git show --name-only --pretty=format: HEAD 2>/dev/null | grep -v '^$' || true)"
for path in $LAST_DIFF; do
  for g in "${TIER3_GLOBS[@]}"; do
    case "$path" in
      "$g"*) CHANGED_T3="$CHANGED_T3 $path" ;;
    esac
  done
done

{
  echo "[hygiene] SessionStart"
  echo "[hygiene] branch: $BRANCH"
  echo "[hygiene] last commit: $LAST_COMMIT"
  if [ -n "$PHASE_PLAN" ]; then
    echo "[hygiene] phase plan: docs/superpowers/plans/$PHASE_PLAN"
  fi
  if [ -n "$CHANGED_T3" ]; then
    echo "[hygiene] Tier 3 paths in last commit:$CHANGED_T3"
  fi
} >&2

exit 0
