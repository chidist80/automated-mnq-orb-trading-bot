---
description: Project-tuned multi-aspect review for Tier 3 changes. Dispatches silent-failure-hunter, pr-test-analyzer, type-design-analyzer, and risk-mechanics-reviewer in parallel; iterates fixes until zero critical/high findings remain. Local replica of /ultrareview tuned for the MNQ ORB bot. Trigger at task boundaries on Tier 3 work — see CLAUDE.md "Tier 3 (real-money paths)".
argument-hint: "[scope-glob] [--base SHA] [--head SHA] [--quick]"
allowed-tools: Bash(git:*), Bash(pytest:*), Read, Grep, Glob, Task
---

# /deepreview — Tier 3 multi-aspect review

Use when the change being closed touched any Tier 3 path. Mandatory before declaring a Phase 2 epic task complete.

## Step 1: Resolve scope

Determine the changeset:

- If `--base SHA --head SHA` were passed, use those.
- Else: `BASE=$(git merge-base HEAD origin/main)` and `HEAD=$(git rev-parse HEAD)`.
- Optional `[scope-glob]` further restricts the file list.

Read the diff: `git diff $BASE..$HEAD --name-only`. List the Tier 3 files touched (use `.claude/hooks/tier3_paths.py` as the canonical filter).

If zero Tier 3 files were touched, ABORT — `/deepreview` is not the right command. Use `/audit-project --recent` instead.

## Step 2: Dispatch the panel in parallel

Use the Task tool with multiple invocations in a SINGLE assistant message so the four agents run concurrently:

1. **silent-failure-hunter** (`pr-review-toolkit:silent-failure-hunter`)
   - Prompt: "Review the diff between $BASE and $HEAD for silent failures, swallowed exceptions, broad `except`, fallback hides, sentinel-on-error patterns. Focus files: <Tier 3 files>. Return JSON findings only (file, line, severity, category, description, suggestion, confidence)."

2. **pr-test-analyzer** (`pr-review-toolkit:pr-test-analyzer`)
   - Prompt: "Verify new code in <Tier 3 files> is exercised by meaningful tests, not just path-matched. Look for assertion-free tests, missing edge cases (partial fills, errors, reconnects), idealized mocks. Return JSON findings."

3. **type-design-analyzer** (`pr-review-toolkit:type-design-analyzer`)
   - Prompt: "Review newly introduced or modified types in <Tier 3 files> — Order, Bracket, Position, Signal, RiskCheck, etc. Score encapsulation, invariant expression, usefulness, enforcement. Return JSON findings."

4. **risk-mechanics-reviewer** (project-local, `.claude/agents/risk-mechanics-reviewer.md`)
   - Prompt: "Review the diff between $BASE and $HEAD using your 7-area charter (contract arithmetic, OCA integrity, session boundaries, look-ahead, reconciliation, mock fidelity, operator UX). Tier 3 files: <list>. Return JSON findings."

## Step 3: Aggregate

Merge findings across all four agents. Deduplicate by `(file, line, category)`. Sort by severity: critical → high → medium → low.

Print a summary table to the user:

```
Findings: <N total> (critical=<a>, high=<b>, medium=<c>, low=<d>)
By agent: silent-failure-hunter=<x>, pr-test-analyzer=<y>, type-design-analyzer=<z>, risk-mechanics-reviewer=<w>
```

## Step 4: Iterate

If `--quick` was passed, STOP — report only.

Otherwise:

1. Apply fixes for all `critical` and `high` findings (skip `false_positive=true`).
2. Re-run any tests touched by the fixes.
3. Re-dispatch the panel on the new diff (`HEAD` advances).
4. Repeat until critical+high count is **zero**.
5. Cap at 3 iterations. After 3, escalate to the user with remaining findings.

## Step 5: Verify and report

Final pass:

- Run the affected tests: `pytest <changed test paths> -v`. Print result.
- Print the final findings table.
- Print: `/deepreview complete. Critical/high count: 0. Task may be declared complete subject to superpowers:verification-before-completion.`

## Output contract (each agent)

Each panel agent returns:

```json
{
  "findings": [
    {
      "file": "bot/execution/ibkr_executor.py",
      "line": 142,
      "severity": "critical|high|medium|low",
      "category": "<agent-domain>",
      "description": "...",
      "code_quote": "...",
      "suggestion": "...",
      "confidence": "high|medium|low",
      "false_positive": false
    }
  ]
}
```

## Anti-patterns

- Don't run `/deepreview` on docs-only changes.
- Don't skip the iteration loop "because the findings looked minor."
- Don't silently flip `false_positive: true` to make findings disappear — escalate to the user instead.
- Don't run all four agents on a 1-line diff. If diff is <5 lines and clearly cosmetic, use `/audit-project --recent --quick` instead.
