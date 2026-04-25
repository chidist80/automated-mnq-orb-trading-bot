# Execution Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the council-debated execution-hygiene design into the repo: rewrite stale CLAUDE.md / settings.json, add `/deepreview` slash command + `risk-mechanics-reviewer` custom agent, add three signaling hooks, and lock everything behind hygiene tests so configs can't silently drift back to the ruflo-flavored state.

**Architecture:** Pure Claude-facing configuration. No runtime code touched. New files live under `~/.claude/CLAUDE.md` (global, append-only) and `mnq-orb-bot/.claude/` (project-local). Hygiene tests under `mnq-orb-bot/tests/hygiene/` assert content rules so drift is caught by `pytest`.

**Tech Stack:** Markdown, JSON, bash, Python 3.11+ (stdlib only — `json`, `re`, `pathlib`, `subprocess`). Tests use the project's existing `pytest`. Claude Code hook contract: JSON on stdin, stderr → model guidance, exit 0 unless explicitly blocking.

**Spec:** `mnq-orb-bot/docs/superpowers/specs/2026-04-25-execution-hygiene-design.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `~/.claude/CLAUDE.md` | Append | Global behavioral floor: 3 rule+reason+scope sections (verification, research-first, multi-aspect review) |
| `mnq-orb-bot/CLAUDE.md` | Wholesale rewrite | Project facts, Tier 3 list, mock-fidelity rule, no-shortcut covenant, brainstorming brief |
| `mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md` | Delete | Stale ruflo doc |
| `mnq-orb-bot/.claude/settings.json` | Wholesale rewrite | Permissions + 3 signaling hooks (no ruflo) |
| `mnq-orb-bot/.claude/hooks/session_start.sh` | Create | SessionStart signal: branch + commit + Tier 3 changes + phase pointer |
| `mnq-orb-bot/.claude/hooks/post_tier3_edit.py` | Create | PostToolUse signal on Tier 3 path edits |
| `mnq-orb-bot/.claude/hooks/stop_completion_check.py` | Create | Stop signal when completion claim detected without fresh test evidence |
| `mnq-orb-bot/.claude/hooks/tier3_paths.py` | Create | Single source of truth for Tier 3 globs (importable by hooks + tests) |
| `mnq-orb-bot/.claude/commands/deepreview.md` | Create | Slash command: orchestrates 4-agent review panel + iteration loop |
| `mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md` | Create | Custom agent definition: 7-area trading-systems review charter |
| `mnq-orb-bot/tests/hygiene/__init__.py` | Create | Package marker |
| `mnq-orb-bot/tests/hygiene/test_claude_md.py` | Create | Asserts on global + project CLAUDE.md content (no ruflo, line caps, required sections) |
| `mnq-orb-bot/tests/hygiene/test_settings.py` | Create | Asserts on settings.json structure (hooks present, no ruflo) |
| `mnq-orb-bot/tests/hygiene/test_hooks.py` | Create | Asserts hook scripts execute cleanly and emit expected stderr signals |
| `mnq-orb-bot/tests/hygiene/test_command_files.py` | Create | Asserts /deepreview and risk-mechanics-reviewer files have required structure |
| `mnq-orb-bot/tests/hygiene/test_tier3_consistency.py` | Create | Asserts Tier 3 list is consistent between hooks (canonical), CLAUDE.md prose, and /deepreview prose |

---

## Task 1: Set up hygiene test scaffold

**Files:**
- Create: `mnq-orb-bot/tests/hygiene/__init__.py`
- Create: `mnq-orb-bot/tests/hygiene/conftest.py`
- Create: `mnq-orb-bot/tests/hygiene/test_smoke.py`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p mnq-orb-bot/tests/hygiene
```

Write `mnq-orb-bot/tests/hygiene/__init__.py`:

```python
"""Hygiene tests — assert on Claude-facing configuration so it can't silently drift.

These tests do not exercise the bot. They guard CLAUDE.md, settings.json, hook
scripts, and slash command files against the kind of regression that turned
`mnq-orb-bot/CLAUDE.md` into a ruflo-themed document after ruflo was dropped
from the plan (commit b7da8e5).
"""
```

- [ ] **Step 2: Create a conftest with a `repo_root` fixture**

Write `mnq-orb-bot/tests/hygiene/conftest.py`:

```python
"""Shared fixtures for hygiene tests."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the path to mnq-orb-bot/ (the dir containing CLAUDE.md and pyproject.toml)."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "CLAUDE.md").exists():
            return candidate
    raise RuntimeError("Could not locate mnq-orb-bot/ root (need CLAUDE.md + pyproject.toml)")


@pytest.fixture(scope="session")
def claude_dir(repo_root: Path) -> Path:
    """Return mnq-orb-bot/.claude/."""
    return repo_root / ".claude"


@pytest.fixture(scope="session")
def global_claude_md() -> Path:
    """Return ~/.claude/CLAUDE.md."""
    return Path.home() / ".claude" / "CLAUDE.md"
```

- [ ] **Step 3: Write a smoke test that proves the runner discovers the dir**

Write `mnq-orb-bot/tests/hygiene/test_smoke.py`:

```python
"""Smoke test: hygiene fixtures resolve the right repo root."""
from __future__ import annotations

from pathlib import Path


def test_repo_root_resolves(repo_root: Path) -> None:
    assert (repo_root / "pyproject.toml").exists()
    assert (repo_root / "CLAUDE.md").exists()


def test_claude_dir_exists(claude_dir: Path) -> None:
    assert claude_dir.exists()
    assert claude_dir.is_dir()


def test_global_claude_md_exists(global_claude_md: Path) -> None:
    assert global_claude_md.exists(), f"Expected {global_claude_md} to exist"
```

- [ ] **Step 4: Run the smoke test to confirm it passes**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_smoke.py -v`
Expected: `3 passed`. If it fails on `claude_dir` (because `mnq-orb-bot/.claude/` doesn't exist or the existing file lacks pyproject), inspect output and fix the fixture before continuing.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/tests/hygiene/
git commit -m "test(hygiene): scaffold hygiene test package with repo-root fixtures"
```

---

## Task 2: Delete stale ruflo doc + assert it stays gone

**Files:**
- Delete: `mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md`
- Create: `mnq-orb-bot/tests/hygiene/test_no_stale_docs.py`

- [ ] **Step 1: Write a failing test that asserts the file is gone**

Write `mnq-orb-bot/tests/hygiene/test_no_stale_docs.py`:

```python
"""Stale-doc hygiene: files known to be obsolete must stay deleted."""
from __future__ import annotations

from pathlib import Path


def test_global_claude_md_additions_is_deleted(repo_root: Path) -> None:
    """commit b7da8e5 dropped ruflo from Phase 2.

    GLOBAL_CLAUDE_MD_ADDITIONS.md described ruflo as the build factory and is
    now actively misleading. It must not return.
    """
    stale = repo_root / "GLOBAL_CLAUDE_MD_ADDITIONS.md"
    assert not stale.exists(), (
        f"{stale} is stale ruflo content — see "
        f"docs/superpowers/specs/2026-04-25-execution-hygiene-design.md §3.6"
    )
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_no_stale_docs.py -v`
Expected: FAIL — file still exists.

- [ ] **Step 3: Delete the stale file**

```bash
git rm mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md
```

- [ ] **Step 4: Re-run the test to confirm it passes**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_no_stale_docs.py -v`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/tests/hygiene/test_no_stale_docs.py
git commit -m "chore: delete stale GLOBAL_CLAUDE_MD_ADDITIONS.md (ruflo-era), assert it stays gone"
```

---

## Task 3: Tier 3 paths — canonical source of truth

**Files:**
- Create: `mnq-orb-bot/.claude/hooks/__init__.py`
- Create: `mnq-orb-bot/.claude/hooks/tier3_paths.py`
- Create: `mnq-orb-bot/tests/hygiene/test_tier3_paths.py`

The Tier 3 list is referenced from three places (hook script, CLAUDE.md, /deepreview). Centralize it as a Python module and have the hook + tests import from it; CLAUDE.md and /deepreview cite it verbatim, with a consistency test.

- [ ] **Step 1: Write the failing test first**

Write `mnq-orb-bot/tests/hygiene/test_tier3_paths.py`:

```python
"""Canonical Tier 3 path list lives in .claude/hooks/tier3_paths.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_tier3_module(claude_dir: Path):
    spec = importlib.util.spec_from_file_location(
        "tier3_paths_under_test",
        claude_dir / "hooks" / "tier3_paths.py",
    )
    assert spec and spec.loader, "Could not load tier3_paths module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tier3_globs_present(claude_dir: Path) -> None:
    module = _load_tier3_module(claude_dir)
    globs = module.TIER3_GLOBS
    assert isinstance(globs, tuple)
    assert all(isinstance(g, str) for g in globs)
    expected = {
        "bot/execution/**",
        "bot/watchdog.py",
        "bot/risk_manager.py",
        "bot/health_monitor.py",
        "bot/signal_generator.py",
        "backtest/strategies/core.py",
        "config/risk_params.yaml",
        "config/strategy_params.yaml",
    }
    assert set(globs) == expected, f"Tier 3 globs drifted: {set(globs) ^ expected}"


def test_is_tier3_path_matches_known_files(claude_dir: Path) -> None:
    module = _load_tier3_module(claude_dir)
    is_tier3 = module.is_tier3_path
    assert is_tier3("bot/execution/ibkr_executor.py") is True
    assert is_tier3("bot/execution/order_state.py") is True
    assert is_tier3("bot/watchdog.py") is True
    assert is_tier3("config/risk_params.yaml") is True
    assert is_tier3("backtest/strategies/core.py") is True
    # Negative cases
    assert is_tier3("bot/main.py") is False
    assert is_tier3("tests/unit/test_executor.py") is False
    assert is_tier3("docs/PHASE2_DEPLOYMENT.md") is False


def test_is_tier3_path_handles_absolute_paths(claude_dir: Path, repo_root: Path) -> None:
    module = _load_tier3_module(claude_dir)
    abs_path = str(repo_root / "bot" / "execution" / "ibkr_executor.py")
    assert module.is_tier3_path(abs_path) is True
    abs_outside = "/tmp/scratch.py"
    assert module.is_tier3_path(abs_outside) is False
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_tier3_paths.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 3: Create the hooks package marker**

Write `mnq-orb-bot/.claude/hooks/__init__.py`:

```python
"""Project-local hook scripts and shared helpers."""
```

- [ ] **Step 4: Implement `tier3_paths.py`**

Write `mnq-orb-bot/.claude/hooks/tier3_paths.py`:

```python
"""Canonical Tier 3 file list for the MNQ ORB bot.

Tier 3 = real-money paths. Edits to these files require /deepreview before
the task can be declared complete. See docs/superpowers/specs/
2026-04-25-execution-hygiene-design.md §3.2.

Anything that imports this module is the canonical reader. CLAUDE.md and
/deepreview prose copies are validated against this list by
tests/hygiene/test_tier3_consistency.py.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

TIER3_GLOBS: tuple[str, ...] = (
    "bot/execution/**",
    "bot/watchdog.py",
    "bot/risk_manager.py",
    "bot/health_monitor.py",
    "bot/signal_generator.py",
    "backtest/strategies/core.py",
    "config/risk_params.yaml",
    "config/strategy_params.yaml",
)


def _repo_relative(path: str) -> str | None:
    """Return path relative to mnq-orb-bot/ if it lives inside, else None."""
    p = Path(path)
    if not p.is_absolute():
        return path.replace("\\", "/")
    here = Path(__file__).resolve().parents[2]  # .claude/hooks/tier3_paths.py -> mnq-orb-bot/
    try:
        return p.resolve().relative_to(here).as_posix()
    except ValueError:
        return None


def is_tier3_path(path: str) -> bool:
    """True if `path` matches any Tier 3 glob.

    Accepts relative paths (matched directly) or absolute paths (relativized
    against mnq-orb-bot/ first). Paths outside the repo return False.
    """
    rel = _repo_relative(path)
    if rel is None:
        return False
    for pattern in TIER3_GLOBS:
        if fnmatch.fnmatch(rel, pattern):
            return True
        # fnmatch doesn't honor `**` recursive globs; emulate by stripping the
        # trailing /** and matching prefix-ish.
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
    return False
```

- [ ] **Step 5: Re-run tests to confirm pass**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_tier3_paths.py -v`
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add mnq-orb-bot/.claude/hooks/__init__.py mnq-orb-bot/.claude/hooks/tier3_paths.py mnq-orb-bot/tests/hygiene/test_tier3_paths.py
git commit -m "feat(hygiene): canonical Tier 3 path list with is_tier3_path() helper"
```

---

## Task 4: Project `mnq-orb-bot/CLAUDE.md` — wholesale rewrite

**Files:**
- Modify: `mnq-orb-bot/CLAUDE.md` (currently 241 lines, ruflo-themed; replace with ≤80 lines)
- Create: `mnq-orb-bot/tests/hygiene/test_project_claude_md.py`

- [ ] **Step 1: Write the failing assertions**

Write `mnq-orb-bot/tests/hygiene/test_project_claude_md.py`:

```python
"""Project CLAUDE.md hygiene: ≤80 lines, no ruflo, required sections present."""
from __future__ import annotations

import re
from pathlib import Path


def _read(repo_root: Path) -> str:
    return (repo_root / "CLAUDE.md").read_text()


def test_line_count_under_cap(repo_root: Path) -> None:
    body = _read(repo_root)
    line_count = len(body.splitlines())
    assert line_count <= 80, (
        f"CLAUDE.md is {line_count} lines; cap is 80 (spec §3.2). "
        f"Opus 4.7 tokenizer is 1.0-1.35x heavier; trim it."
    )


def test_no_ruflo_references(repo_root: Path) -> None:
    body = _read(repo_root).lower()
    forbidden = ("ruflo", "claude-flow", "agentic-qe", "hive-mind")
    found = [w for w in forbidden if w in body]
    assert not found, f"Stale ruflo terms in CLAUDE.md: {found} (commit b7da8e5 dropped ruflo)"


def test_required_sections_present(repo_root: Path) -> None:
    body = _read(repo_root)
    required_headings = (
        "Project Facts",
        "Tier 3",
        "Phase",
        "Mock",
        "shortcut",
        "Brainstorming",
    )
    missing = [h for h in required_headings if h.lower() not in body.lower()]
    assert not missing, f"Missing required sections: {missing}"


def test_tier3_files_listed(repo_root: Path) -> None:
    body = _read(repo_root)
    tier3 = (
        "bot/execution/",
        "bot/watchdog.py",
        "bot/risk_manager.py",
        "bot/health_monitor.py",
        "bot/signal_generator.py",
        "backtest/strategies/core.py",
        "config/risk_params.yaml",
        "config/strategy_params.yaml",
    )
    missing = [p for p in tier3 if p not in body]
    assert not missing, f"Tier 3 paths missing from CLAUDE.md: {missing}"


def test_phase_pointer_present(repo_root: Path) -> None:
    body = _read(repo_root)
    assert "phase2/live-execution" in body, "Phase pointer (branch) missing"
    assert "2026-04-25-phase2-live-execution.md" in body, "Phase plan path missing"
```

- [ ] **Step 2: Run tests; expect 5 failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_project_claude_md.py -v`
Expected: 5 failures (file is 241 lines, contains "ruflo" 30+ times, lacks the new section names).

- [ ] **Step 3: Replace `CLAUDE.md` wholesale**

Write `mnq-orb-bot/CLAUDE.md`:

```markdown
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
```

- [ ] **Step 4: Re-run tests, expect all 5 to pass**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_project_claude_md.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Verify line count manually**

Run: `wc -l mnq-orb-bot/CLAUDE.md`
Expected: ≤80 lines.

- [ ] **Step 6: Commit**

```bash
git add mnq-orb-bot/CLAUDE.md mnq-orb-bot/tests/hygiene/test_project_claude_md.py
git commit -m "feat(claude-md): rewrite project CLAUDE.md (drop ruflo, add Tier 3 + mock-fidelity rules)"
```

---

## Task 5: Project `.claude/settings.json` — clean rewrite + hooks wiring

**Files:**
- Modify: `mnq-orb-bot/.claude/settings.json` (currently ruflo-heavy)
- Create: `mnq-orb-bot/tests/hygiene/test_settings.py`

- [ ] **Step 1: Write the failing assertions**

Write `mnq-orb-bot/tests/hygiene/test_settings.py`:

```python
"""Project settings.json hygiene: no ruflo, three signaling hooks present."""
from __future__ import annotations

import json
from pathlib import Path


def _load(claude_dir: Path) -> dict:
    return json.loads((claude_dir / "settings.json").read_text())


def test_no_ruflo_references(claude_dir: Path) -> None:
    raw = (claude_dir / "settings.json").read_text().lower()
    forbidden = ("ruflo", "claude-flow", "claudeflow")
    found = [w for w in forbidden if w in raw]
    assert not found, f"Stale ruflo refs in settings.json: {found}"


def test_required_hooks_present(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    hooks = settings.get("hooks", {})
    assert "SessionStart" in hooks, "SessionStart hook missing (spec §3.5)"
    assert "PostToolUse" in hooks, "PostToolUse hook missing (spec §3.5)"
    assert "Stop" in hooks, "Stop hook missing (spec §3.5)"


def test_hook_commands_reference_project_scripts(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    hooks = settings.get("hooks", {})

    def _all_commands(event: str) -> list[str]:
        out = []
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                if h.get("type") == "command":
                    out.append(h.get("command", ""))
        return out

    session_cmds = " ".join(_all_commands("SessionStart"))
    assert "session_start.sh" in session_cmds

    post_cmds = " ".join(_all_commands("PostToolUse"))
    assert "post_tier3_edit.py" in post_cmds

    stop_cmds = " ".join(_all_commands("Stop"))
    assert "stop_completion_check.py" in stop_cmds


def test_post_tooluse_matches_edit_write(claude_dir: Path) -> None:
    settings = _load(claude_dir)
    post = settings.get("hooks", {}).get("PostToolUse", [])
    matchers = [entry.get("matcher", "") for entry in post]
    matched = " ".join(matchers)
    for tool in ("Edit", "Write", "MultiEdit"):
        assert tool in matched, f"PostToolUse matcher missing tool {tool}: {matchers}"
```

- [ ] **Step 2: Run tests; expect failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_settings.py -v`
Expected: 4 failures (current file has ruflo, no SessionStart, etc.).

- [ ] **Step 3: Replace settings.json**

Write `mnq-orb-bot/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $CLAUDE_PROJECT_DIR/.claude/hooks/session_start.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/post_tier3_edit.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/stop_completion_check.py"
          }
        ]
      }
    ]
  }
}
```

Note: hook scripts referenced here will be created in Tasks 6, 7, 8. Tests for `test_settings.py` only check structure, not script existence — that's fine.

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_settings.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/.claude/settings.json mnq-orb-bot/tests/hygiene/test_settings.py
git commit -m "feat(hygiene): rewrite project settings.json with signaling hooks (drop ruflo)"
```

---

## Task 6: SessionStart hook

**Files:**
- Create: `mnq-orb-bot/.claude/hooks/session_start.sh`
- Create: `mnq-orb-bot/tests/hygiene/test_session_start_hook.py`

- [ ] **Step 1: Write the failing test**

Write `mnq-orb-bot/tests/hygiene/test_session_start_hook.py`:

```python
"""SessionStart hook emits structured stderr signal with branch + phase context."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run_hook(claude_dir: Path, repo_root: Path) -> tuple[int, str, str]:
    """Run session_start.sh with the project as cwd and return (rc, stdout, stderr)."""
    script = claude_dir / "hooks" / "session_start.sh"
    payload = json.dumps({"hook_event_name": "SessionStart"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["bash", str(script)],
        input=payload,
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_session_start_exits_zero(claude_dir: Path, repo_root: Path) -> None:
    rc, _, _ = _run_hook(claude_dir, repo_root)
    assert rc == 0, "SessionStart hook must not block (signaling only)"


def test_session_start_emits_branch_marker(claude_dir: Path, repo_root: Path) -> None:
    _, _, err = _run_hook(claude_dir, repo_root)
    # Marker prefix MUST be present so Claude treats the lines as guidance.
    assert "[hygiene]" in err, f"Expected [hygiene] marker; got: {err!r}"
    assert "branch" in err.lower(), f"Expected branch info; got: {err!r}"


def test_session_start_emits_phase_pointer(claude_dir: Path, repo_root: Path) -> None:
    _, _, err = _run_hook(claude_dir, repo_root)
    assert "phase" in err.lower(), f"Expected phase pointer; got: {err!r}"
```

- [ ] **Step 2: Run test to confirm fails**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_session_start_hook.py -v`
Expected: 3 failures (script doesn't exist).

- [ ] **Step 3: Implement the hook**

Write `mnq-orb-bot/.claude/hooks/session_start.sh`:

```bash
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
```

Make it executable:

```bash
chmod +x mnq-orb-bot/.claude/hooks/session_start.sh
```

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_session_start_hook.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Manual smoke**

Run from inside `mnq-orb-bot/`:

```bash
echo '{"hook_event_name":"SessionStart"}' | bash .claude/hooks/session_start.sh 2>&1 1>/dev/null
```

Expected: 3-5 `[hygiene]` lines describing branch / commit / phase plan / Tier 3 paths.

- [ ] **Step 6: Commit**

```bash
git add mnq-orb-bot/.claude/hooks/session_start.sh mnq-orb-bot/tests/hygiene/test_session_start_hook.py
git commit -m "feat(hooks): SessionStart hook prints branch, phase, recent Tier 3 changes"
```

---

## Task 7: PostToolUse Tier 3 edit hook

**Files:**
- Create: `mnq-orb-bot/.claude/hooks/post_tier3_edit.py`
- Create: `mnq-orb-bot/tests/hygiene/test_post_tier3_hook.py`

- [ ] **Step 1: Write the failing tests**

Write `mnq-orb-bot/tests/hygiene/test_post_tier3_hook.py`:

```python
"""PostToolUse hook emits a Tier 3 reminder when a Tier 3 path is edited."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run_hook(claude_dir: Path, repo_root: Path, payload: dict) -> tuple[int, str, str]:
    script = claude_dir / "hooks" / "post_tier3_edit.py"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["python3", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _payload(tool: str, file_path: str) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
    }


def test_tier3_edit_emits_reminder(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Edit", "bot/execution/ibkr_executor.py"))
    assert rc == 0
    assert "[hygiene]" in err
    assert "Tier 3" in err
    assert "/deepreview" in err


def test_non_tier3_edit_silent(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Edit", "bot/main.py"))
    assert rc == 0
    assert err.strip() == "", f"Non-Tier 3 edit should be silent; got: {err!r}"


def test_write_tool_also_handled(claude_dir: Path, repo_root: Path) -> None:
    rc, _, err = _run_hook(claude_dir, repo_root, _payload("Write", "bot/risk_manager.py"))
    assert rc == 0
    assert "Tier 3" in err


def test_tool_without_file_path_silent(claude_dir: Path, repo_root: Path) -> None:
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    rc, _, err = _run_hook(claude_dir, repo_root, payload)
    assert rc == 0
    assert err.strip() == ""


def test_malformed_input_does_not_crash(claude_dir: Path, repo_root: Path) -> None:
    script = claude_dir / "hooks" / "post_tier3_edit.py"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["python3", str(script)],
        input="not json at all",
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    assert proc.returncode == 0
```

- [ ] **Step 2: Run tests; expect failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_post_tier3_hook.py -v`
Expected: 5 failures (script doesn't exist).

- [ ] **Step 3: Implement the hook**

Write `mnq-orb-bot/.claude/hooks/post_tier3_edit.py`:

```python
#!/usr/bin/env python3
"""PostToolUse hook — emit a Tier 3 reminder when an Edit/Write touched a
real-money path. Signaling only (always exits 0). Silent on non-Tier 3 edits.

Contract: JSON on stdin with `tool_name` and `tool_input.file_path`. Emits to
stderr only; never blocks the turn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the sibling tier3_paths module importable.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from tier3_paths import is_tier3_path  # type: ignore  # noqa: E402
except Exception:
    sys.exit(0)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in {"Edit", "Write", "MultiEdit"}:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0

    if is_tier3_path(file_path):
        sys.stderr.write(
            f"[hygiene] Tier 3 path touched: {file_path}\n"
            f"[hygiene] Reminder: invoke /deepreview at the next task boundary "
            f"before claiming this work complete.\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Make it executable:

```bash
chmod +x mnq-orb-bot/.claude/hooks/post_tier3_edit.py
```

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_post_tier3_hook.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/.claude/hooks/post_tier3_edit.py mnq-orb-bot/tests/hygiene/test_post_tier3_hook.py
git commit -m "feat(hooks): PostToolUse Tier 3 reminder (signaling only)"
```

---

## Task 8: Stop completion-claim hook

**Files:**
- Create: `mnq-orb-bot/.claude/hooks/stop_completion_check.py`
- Create: `mnq-orb-bot/tests/hygiene/test_stop_hook.py`

The Stop hook reads the transcript JSONL pointed at by `transcript_path` in the payload, scans the most recent assistant turn for completion-claim language, and signals if no fresh `pytest`/`python -m pytest` invocation appears in the same turn.

- [ ] **Step 1: Write the failing tests**

Write `mnq-orb-bot/tests/hygiene/test_stop_hook.py`:

```python
"""Stop hook signals when a completion claim is made without fresh test evidence."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _make_transcript(tmp_path: Path, lines: list[dict]) -> Path:
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return p


def _run_hook(claude_dir: Path, repo_root: Path, transcript: Path) -> tuple[int, str, str]:
    script = claude_dir / "hooks" / "stop_completion_check.py"
    payload = {"hook_event_name": "Stop", "transcript_path": str(transcript)}
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)}
    proc = subprocess.run(
        ["python3", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(repo_root),
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_claim_without_test_run_emits_warning(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done. Tests pass."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert "[hygiene]" in err
    assert "verification-before-completion" in err


def test_claim_with_pytest_in_turn_silent(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "pytest tests/unit/ -v"}}]}},
        {"type": "tool_result", "tool_use_id": "x", "content": "5 passed"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "All tests pass — done."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert err.strip() == "", f"Claim with fresh pytest should be silent; got: {err!r}"


def test_no_claim_silent(claude_dir, repo_root, tmp_path) -> None:
    transcript = _make_transcript(tmp_path, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the file."}]}},
    ])
    rc, _, err = _run_hook(claude_dir, repo_root, transcript)
    assert rc == 0
    assert err.strip() == ""


def test_missing_transcript_path_silent(claude_dir, repo_root, tmp_path) -> None:
    script = claude_dir / "hooks" / "stop_completion_check.py"
    proc = subprocess.run(
        ["python3", str(script)],
        input=json.dumps({"hook_event_name": "Stop"}),
        text=True,
        capture_output=True,
        cwd=str(repo_root),
    )
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""
```

- [ ] **Step 2: Run tests; expect failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_stop_hook.py -v`
Expected: 4 failures.

- [ ] **Step 3: Implement the hook**

Write `mnq-orb-bot/.claude/hooks/stop_completion_check.py`:

```python
#!/usr/bin/env python3
"""Stop hook — if the assistant's final turn includes a completion-claim phrase
but no fresh pytest invocation was issued in that same turn, emit a reminder
to invoke superpowers:verification-before-completion.

Signaling only (always exits 0).

Contract: JSON on stdin with `transcript_path` pointing to the session's
JSONL transcript. Each line is one entry with `type` ∈ {assistant, user,
tool_result, ...}. We only inspect the LAST assistant turn (everything
between the last user message and EOF).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CLAIM_PATTERNS = [
    r"\btests?\s+pass(es|ed|ing)?\b",
    r"\ball\s+tests?\s+(pass|green)\b",
    r"\b(it\s+)?works\s+now\b",
    r"\ball\s+green\b",
    r"\bworking\s+as\s+expected\b",
    r"\b(implementation|task|feature)\s+(is\s+)?(complete|done|finished)\b",
    r"\bbug\s+is\s+fixed\b",
]
CLAIM_RE = re.compile("|".join(CLAIM_PATTERNS), re.IGNORECASE)

PYTEST_RE = re.compile(r"\bpytest\b|\bpython\s+-m\s+pytest\b", re.IGNORECASE)


def _last_assistant_turn(lines: list[dict]) -> list[dict]:
    """Return the entries from the LAST user message onward."""
    last_user = -1
    for i, entry in enumerate(lines):
        if entry.get("type") == "user":
            last_user = i
    return lines[last_user + 1:] if last_user >= 0 else lines


def _entry_text(entry: dict) -> str:
    """Flatten any text-bearing fields of an entry into one searchable string."""
    msg = entry.get("message") or {}
    content = msg.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            if block.get("type") == "tool_use":
                tool_input = block.get("input") or {}
                # Capture the full bash command text so PYTEST_RE can match.
                cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
                if cmd:
                    parts.append(str(cmd))
    elif isinstance(content, str):
        parts.append(content)
    if entry.get("type") == "tool_result":
        c = entry.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0
    p = Path(transcript_path)
    if not p.exists():
        return 0

    lines: list[dict] = []
    try:
        for raw_line in p.read_text().splitlines():
            if not raw_line.strip():
                continue
            try:
                lines.append(json.loads(raw_line))
            except Exception:
                continue
    except Exception:
        return 0

    turn = _last_assistant_turn(lines)
    text = "\n".join(_entry_text(e) for e in turn)
    if not text.strip():
        return 0

    if not CLAIM_RE.search(text):
        return 0
    if PYTEST_RE.search(text):
        return 0

    sys.stderr.write(
        "[hygiene] Completion claim detected without a fresh pytest invocation in this turn.\n"
        "[hygiene] Re-check superpowers:verification-before-completion before closing the turn.\n"
        "[hygiene] If this is a docs-only or non-code change, ignore.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Make it executable:

```bash
chmod +x mnq-orb-bot/.claude/hooks/stop_completion_check.py
```

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_stop_hook.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/.claude/hooks/stop_completion_check.py mnq-orb-bot/tests/hygiene/test_stop_hook.py
git commit -m "feat(hooks): Stop hook signals unverified completion claims (no exit-code block)"
```

---

## Task 9: `risk-mechanics-reviewer` custom agent

**Files:**
- Create: `mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md`
- Create: `mnq-orb-bot/tests/hygiene/test_agent_files.py`

- [ ] **Step 1: Write the failing assertions**

Write `mnq-orb-bot/tests/hygiene/test_agent_files.py`:

```python
"""Custom agent files have required structure."""
from __future__ import annotations

from pathlib import Path

REQUIRED_AREAS = (
    "Contract arithmetic",
    "OCA",
    "Session boundaries",
    "Look-ahead",
    "reconciliation",
    "Mock fidelity",
    "Operator UX",
)


def test_risk_mechanics_reviewer_exists(claude_dir: Path) -> None:
    p = claude_dir / "agents" / "risk-mechanics-reviewer.md"
    assert p.exists(), "risk-mechanics-reviewer.md missing"


def test_risk_mechanics_reviewer_has_frontmatter(claude_dir: Path) -> None:
    p = claude_dir / "agents" / "risk-mechanics-reviewer.md"
    body = p.read_text()
    assert body.startswith("---\n"), "Agent file must start with YAML frontmatter"
    assert "name:" in body[:200]
    assert "description:" in body[:500]


def test_risk_mechanics_reviewer_covers_all_areas(claude_dir: Path) -> None:
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    missing = [a for a in REQUIRED_AREAS if a.lower() not in body.lower()]
    assert not missing, f"Missing charter areas: {missing}"


def test_risk_mechanics_reviewer_specifies_output_schema(claude_dir: Path) -> None:
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    for token in ("severity", "file", "line", "JSON"):
        assert token in body, f"Output schema missing {token!r}"


def test_risk_mechanics_reviewer_cites_mnq_specs(claude_dir: Path) -> None:
    """The agent must know MNQ contract math to flag tick-value drift."""
    body = (claude_dir / "agents" / "risk-mechanics-reviewer.md").read_text()
    for fact in ("$2/point", "$0.50/tick"):
        assert fact in body, f"Missing MNQ spec fact: {fact}"
```

- [ ] **Step 2: Run tests; expect failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_agent_files.py -v`
Expected: 5 failures.

- [ ] **Step 3: Implement the agent file**

Write `mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md`:

```markdown
---
name: risk-mechanics-reviewer
description: Review trading-system code from a futures-execution perspective, not a generic engineering perspective. Use this agent when /deepreview fires on Tier 3 paths (bot/execution/**, bot/watchdog.py, bot/risk_manager.py, bot/health_monitor.py, bot/signal_generator.py, backtest/strategies/core.py, config/*_params.yaml). Catches the bugs that lose retail futures accounts — tick-value drift, OCA orphaning, RTH/ETH contamination, look-ahead bias, reconnection reconciliation gaps, idealized-mock fragility, and operator-hostile alerts.
tools: Read, Glob, Grep, Bash
---

You are reviewing code for an MNQ futures trading bot running on a $2,500 IBKR account. One bad bracket order can cost the account.

You do **not** repeat what generic code reviewers say (style, naming, lint). You look for the seven specific bug categories below and report them as JSON findings.

## Charter

### 1. Contract arithmetic
MNQ specs: **$2/point**, **$0.50/tick**, multiplier 2 (4 ticks/point). Reject any tick/point/dollar conversion that uses a hardcoded constant rather than referencing the contract spec object. A "10-point stop" must be derivable from the spec, not from `entry - 10`. Watch for confusion between price units and dollar units.

### 2. OCA group integrity
Bracket orders bind parent → stop + target via OCA. On amendment, partial fill, or resubmission, the OCA group ID must be **reused**, not regenerated. A regenerated group ID can leave the original stop covering a partial position while a new target covers the full size — naked target leg. Verify ib_insync `ocaGroup` and `ocaType` are propagated on every amend path.

### 3. Session boundaries
RTH bars only. Historical requests must use `whatToShow='TRADES'` and `useRTH=1`. Bar 0 of the trading day must be timestamped 9:30:00 ET, not 18:00 ET prior day. ETH (Globex overnight) bars contaminate the opening range and invalidate ORB strategy entirely.

### 4. Look-ahead bias
Signals on bar `t` use `bar[t-1].close`, never `bar[t].close` (which isn't known until the bar closes). Indicator state must be frozen at the moment the signal fires. Backtest-vs-live divergence here is silent — the backtester uses lookahead happily, live can't.

### 5. Reconnection reconciliation
On every ib_insync reconnect, call `reqPositions()` and reconcile broker state with bot state **before** any new order submission. Bot reconnects assuming flat → broker still holds prior position → bot opens a second contract → margin call. Watchdog has the same hazard with its own connection.

### 6. Mock fidelity
Reject mocks that:
- Return success in <100ms simulated time (no real broker fills that fast)
- Don't model partial fills
- Don't surface IBKR error codes — at minimum: 201 (margin), 202 (cancelled), 100 (max-rate), 1100 (connection lost), 1102 (data farm reconnected)
- Have `fill_price = limit_price` instantly

### 7. Operator UX
Alerts must answer "is my money safe right now?" in **line one**. Position state (flat/long/short, qty), broker state (per `reqPositions`), last known order IDs, recommended action. A stack trace alone is insufficient — at 3am a sleep-deprived human reading "exception in module X" panics and double-flattens, creating the exact bug the watchdog was meant to prevent.

## Output

Return **JSON only**, one finding per issue, in this exact shape:

```json
{
  "findings": [
    {
      "file": "bot/execution/ibkr_executor.py",
      "line": 142,
      "severity": "critical",
      "category": "oca-integrity",
      "description": "On _on_partial_fill, a new OCA group ID is generated when amending the target leg. The original stop's group ID is not updated — leaves stop and target in different OCA groups.",
      "code_quote": "self.ib.placeOrder(contract, target_order)  # ocaGroup not set on amend",
      "suggestion": "Reuse self._oca_group_id; mirror the parent's ocaType=1 (cancel-with-block).",
      "confidence": "high",
      "false_positive": false
    }
  ]
}
```

**Severity discipline:**
- `critical`: bug that can cost real money on a single live trade (orphaned OCA, look-ahead in live signal, reconnect-without-reconcile).
- `high`: bug that degrades safety margin (idealized mock, missing 1100 handler, alert without position state).
- `medium`: hygiene gap that's not yet biting (e.g., contract spec not centralized but math is currently correct).
- `low`: cosmetic — leave for code-quality-reviewer.

If the change introduces zero issues in your charter, return `{"findings": []}`. Don't pad.
```

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_agent_files.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md mnq-orb-bot/tests/hygiene/test_agent_files.py
git commit -m "feat(agents): risk-mechanics-reviewer custom agent (7-area trading-systems charter)"
```

---

## Task 10: `/deepreview` slash command

**Files:**
- Create: `mnq-orb-bot/.claude/commands/deepreview.md`
- Create: `mnq-orb-bot/tests/hygiene/test_deepreview_command.py`

- [ ] **Step 1: Write the failing assertions**

Write `mnq-orb-bot/tests/hygiene/test_deepreview_command.py`:

```python
"""/deepreview slash command file has required structure."""
from __future__ import annotations

from pathlib import Path

REQUIRED_AGENTS = (
    "silent-failure-hunter",
    "pr-test-analyzer",
    "type-design-analyzer",
    "risk-mechanics-reviewer",
)


def test_deepreview_exists(claude_dir: Path) -> None:
    assert (claude_dir / "commands" / "deepreview.md").exists()


def test_deepreview_frontmatter(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    assert body.startswith("---\n")
    assert "description:" in body[:500]


def test_deepreview_lists_all_panel_agents(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    missing = [a for a in REQUIRED_AGENTS if a not in body]
    assert not missing, f"Panel agents missing from /deepreview: {missing}"


def test_deepreview_has_iteration_loop(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    body_lower = body.lower()
    assert "iterate" in body_lower or "loop" in body_lower
    for token in ("critical", "high", "severity"):
        assert token in body_lower, f"Iteration spec missing {token!r}"


def test_deepreview_describes_output_contract(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    for token in ("JSON", "file", "line", "severity"):
        assert token in body
```

- [ ] **Step 2: Run tests; expect failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_deepreview_command.py -v`
Expected: 5 failures.

- [ ] **Step 3: Implement the command**

Write `mnq-orb-bot/.claude/commands/deepreview.md`:

```markdown
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
```

- [ ] **Step 4: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_deepreview_command.py -v`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add mnq-orb-bot/.claude/commands/deepreview.md mnq-orb-bot/tests/hygiene/test_deepreview_command.py
git commit -m "feat(commands): /deepreview — local /ultrareview replica with iteration loop"
```

---

## Task 11: Global `~/.claude/CLAUDE.md` additions

**Files:**
- Modify: `~/.claude/CLAUDE.md` (append three sections, total ≤110 lines)
- Create: `mnq-orb-bot/tests/hygiene/test_global_claude_md.py`

- [ ] **Step 1: Write the failing assertions**

Write `mnq-orb-bot/tests/hygiene/test_global_claude_md.py`:

```python
"""Global CLAUDE.md hygiene: ≤110 lines, three new triple-format sections."""
from __future__ import annotations

import re
from pathlib import Path


def _read(global_claude_md: Path) -> str:
    return global_claude_md.read_text()


def test_under_line_cap(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    line_count = len(body.splitlines())
    assert line_count <= 110, f"Global CLAUDE.md is {line_count} lines; cap is 110 (spec §3.1)."


def test_verification_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Verification" in body or "verification-before-completion" in body
    assert "verification-before-completion" in body


def test_research_first_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Research" in body or "research" in body
    assert "context7" in body


def test_multi_aspect_review_section_present(global_claude_md: Path) -> None:
    body = _read(global_claude_md)
    assert "Multi-aspect" in body or "multi-aspect" in body or "multi-aspect" in body.lower()
    # Universal floor must reference /audit-project
    assert "audit-project" in body


def test_rule_reason_scope_format_used(global_claude_md: Path) -> None:
    """At least one new behavioral section uses rule + reason + scope wording."""
    body = _read(global_claude_md)
    assert re.search(r"\bRule\b", body), "rule+reason+scope format missing 'Rule'"
    assert re.search(r"\b(Reason|Why)\b", body), "format missing 'Reason' or 'Why'"
    assert re.search(r"\bScope\b", body), "format missing 'Scope'"


def test_existing_routing_table_preserved(global_claude_md: Path) -> None:
    """Don't accidentally clobber the plugin auto-routing table."""
    body = _read(global_claude_md)
    assert "Plugin & Skill Auto-Routing" in body or "Plugin" in body
    # Spot-check a few existing plugin entries
    for plugin in ("/ralph-loop", "/review-pr", "/audit-project", "/ship", "/next-task"):
        assert plugin in body, f"Existing plugin reference {plugin} clobbered"
```

- [ ] **Step 2: Run tests; expect 4-5 failures**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_global_claude_md.py -v`
Expected: 4-5 failures (the existing file lacks the new sections).

- [ ] **Step 3: Read current global CLAUDE.md and identify a clean append point**

Run: `cat ~/.claude/CLAUDE.md | tail -20` — confirm the file ends after the existing "Decision Priority" section.

- [ ] **Step 4: Append three new sections**

Append the content below to the end of `/Users/tisha/.claude/CLAUDE.md`. The current last line is "6. If task is **cleanup** → `/deslop`" — preserve everything above it untouched. Append after a blank line:

```markdown

---

## Behavioral Floor (Opus 4.7)

These sections override "be helpful" defaults. Each rule uses **rule + reason + scope** so 4.7 (which interprets prompts literally) can judge edge cases.

### Verification & Honesty

**Rule:** Before claiming any code change complete — including phrasings like "tests pass", "fixed", "done", "all green" — invoke `superpowers:verification-before-completion`.
**Reason:** Opus 4.7 will otherwise produce confident completion claims when test signal is absent. Trust is non-recoverable.
**Scope:** Edits to source code (`*.py`, `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.go`, `*.rs`, `*.java`, etc.). Excludes pure docs (`*.md`) and config-only commits.

### Research-First

**Rule:** Before editing code that imports an unfamiliar library API, run a `context7` lookup OR cite an existing usage in the same repo.
**Reason:** Opus 4.7 misremembers method signatures from training data; an outdated call can cost hours of debugging or, on financial code, money.
**Scope:** Edits importing external SDKs — particularly broker, financial, database, messaging, and ML libraries (e.g., `ib_insync`, `supabase-py`, `slack-sdk`, `boto3`, `stripe`).

### Multi-aspect Review

**Rule:** Meaningful work products go through a multi-aspect review — `/deepreview` (project-local, where defined) or `/audit-project --recent` — before being declared done.
**Reason:** Single-pass review misses cross-cutting concerns (silent failures, type invariants, test coverage, domain semantics) that compound at the boundaries between modules.
**Scope:** Changes that close a planned task or epic. Skip for one-line typo fixes and docs edits.
```

- [ ] **Step 5: Verify line count**

Run: `wc -l ~/.claude/CLAUDE.md`
Expected: ≤110.

- [ ] **Step 6: Re-run tests**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_global_claude_md.py -v`
Expected: `6 passed`.

- [ ] **Step 7: Commit (project-side test only — global CLAUDE.md is outside the repo)**

```bash
git add mnq-orb-bot/tests/hygiene/test_global_claude_md.py
git commit -m "test(hygiene): assert global CLAUDE.md has behavioral floor sections"
```

---

## Task 12: Tier 3 list consistency check (CLAUDE.md prose ↔ canonical)

**Files:**
- Create: `mnq-orb-bot/tests/hygiene/test_tier3_consistency.py`

- [ ] **Step 1: Write the assertion**

Write `mnq-orb-bot/tests/hygiene/test_tier3_consistency.py`:

```python
"""Tier 3 path lists in prose docs must match the canonical Python list."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_globs(claude_dir: Path) -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "tier3_paths_under_test",
        claude_dir / "hooks" / "tier3_paths.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.TIER3_GLOBS)


def test_project_claude_md_lists_all_tier3_paths(repo_root: Path, claude_dir: Path) -> None:
    canonical = _load_globs(claude_dir)
    body = (repo_root / "CLAUDE.md").read_text()
    for glob in canonical:
        # Allow either the glob form ("bot/execution/**") or the prefix-form
        # ("bot/execution/") in prose.
        prose_form = glob.rstrip("*").rstrip("/")
        assert glob in body or (prose_form + "/") in body or prose_form in body, (
            f"Canonical Tier 3 path {glob!r} not cited in CLAUDE.md"
        )


def test_deepreview_command_references_canonical_list(claude_dir: Path) -> None:
    body = (claude_dir / "commands" / "deepreview.md").read_text()
    assert "tier3_paths.py" in body, (
        "/deepreview must reference the canonical Tier 3 list at .claude/hooks/tier3_paths.py"
    )
```

- [ ] **Step 2: Run the test**

Run: `cd mnq-orb-bot && pytest tests/hygiene/test_tier3_consistency.py -v`
Expected: 2 passes if Task 4 (CLAUDE.md) and Task 10 (/deepreview) are done correctly. If `test_deepreview_command_references_canonical_list` fails, edit `mnq-orb-bot/.claude/commands/deepreview.md` to add a sentence that cites `.claude/hooks/tier3_paths.py` as the canonical filter (it should already be there from Task 10 Step 3 — Step 1 of /deepreview says "use `.claude/hooks/tier3_paths.py` as the canonical filter").

- [ ] **Step 3: Commit**

```bash
git add mnq-orb-bot/tests/hygiene/test_tier3_consistency.py
git commit -m "test(hygiene): assert Tier 3 list is consistent across canonical/prose copies"
```

---

## Task 13: Final wrap — full hygiene suite + acceptance criteria check

**Files:**
- (No new files — just verification)

- [ ] **Step 1: Run the entire hygiene suite**

Run: `cd mnq-orb-bot && pytest tests/hygiene/ -v`
Expected: ALL tests pass. Count should be roughly: 3 (smoke) + 1 (no_stale_docs) + 3 (tier3_paths) + 5 (project_claude_md) + 4 (settings) + 3 (session_start_hook) + 5 (post_tier3_hook) + 4 (stop_hook) + 5 (agent_files) + 5 (deepreview_command) + 6 (global_claude_md) + 2 (tier3_consistency) = **46 tests passing**.

If anything fails, fix it before continuing.

- [ ] **Step 2: Verify spec acceptance criteria one by one**

Read `mnq-orb-bot/docs/superpowers/specs/2026-04-25-execution-hygiene-design.md` §5. For each checkbox, find the corresponding test (or run the manual verification) and tick it:

```bash
# Acceptance check 1: Global CLAUDE.md ≤110 lines, has 3 new sections
wc -l ~/.claude/CLAUDE.md          # ≤110
grep -c '^### ' ~/.claude/CLAUDE.md # ≥3 (new headings)

# Acceptance check 2: Project CLAUDE.md ≤80 lines, no ruflo
wc -l mnq-orb-bot/CLAUDE.md
grep -i ruflo mnq-orb-bot/CLAUDE.md  # expect: no matches

# Acceptance check 3: settings.json zero ruflo refs
grep -i ruflo mnq-orb-bot/.claude/settings.json  # expect: no matches

# Acceptance check 4: /deepreview file exists, names 4 agents
ls mnq-orb-bot/.claude/commands/deepreview.md
grep -c -E 'silent-failure-hunter|pr-test-analyzer|type-design-analyzer|risk-mechanics-reviewer' mnq-orb-bot/.claude/commands/deepreview.md  # expect 4

# Acceptance check 5: risk-mechanics-reviewer file exists with 7 charter areas
ls mnq-orb-bot/.claude/agents/risk-mechanics-reviewer.md

# Acceptance check 6: Hooks emit stderr, no exit-code blocks on hygiene
grep -E 'exit (1|2)' mnq-orb-bot/.claude/hooks/*.{sh,py} | grep -v -E 'exit 0|sys.exit\(0\)|exit\\\?\b'  # expect: no matches
# (manual check: open each hook script; confirm only `exit 0` / `sys.exit(0)` / `return 0` paths)

# Acceptance check 7: GLOBAL_CLAUDE_MD_ADDITIONS.md gone
test ! -f mnq-orb-bot/GLOBAL_CLAUDE_MD_ADDITIONS.md && echo "OK: deleted"
```

- [ ] **Step 3: Manual smoke — invoke `/deepreview` against the regression-test commit**

This is the spec's final acceptance criterion. From a fresh Claude Code turn inside `mnq-orb-bot/`:

```
/deepreview --base c0f6333^ --head c0f6333 --quick
```

Expected: command runs, fans out the 4-agent panel, prints a findings table (likely zero or a few low-severity items since c0f6333 is a regression-test-only commit). If it errors, the most likely cause is an agent-name mismatch — check `mnq-orb-bot/.claude/commands/deepreview.md` Step 2 against the actually-installed `pr-review-toolkit` agent ids by running `claude plugin list --installed` and adjusting names if needed.

- [ ] **Step 4: Final commit (if any cleanup)**

```bash
git status
# If clean, nothing to commit. Otherwise:
git add <files>
git commit -m "chore(hygiene): final cleanup after acceptance check"
```

- [ ] **Step 5: Push and announce**

```bash
git push origin phase2/live-execution
```

Tell the user:
- Hygiene suite passing: `pytest tests/hygiene/ -v` shows ~46 tests.
- All 8 spec acceptance criteria met.
- `/deepreview` smoke against c0f6333 returned cleanly.
- Ready to start Task 1.1 of the Phase 2 plan with the new hygiene gates active.

---

## Self-review (run before declaring this plan complete)

**1. Spec coverage:**
- §3.1 global CLAUDE.md → Task 11 ✓
- §3.2 project CLAUDE.md → Task 4 ✓
- §3.3 /deepreview → Task 10 ✓
- §3.4 risk-mechanics-reviewer → Task 9 ✓
- §3.5 hooks (SessionStart, PostToolUse, Stop) → Tasks 5+6+7+8 ✓
- §3.6 cleanup → Tasks 2 (delete), 4 (rewrite project CLAUDE.md), 5 (rewrite settings.json), 11 (append global) ✓
- §5 acceptance criteria → Task 13 ✓

**2. Placeholder scan:** searched for "TBD", "TODO", "implement later", "fill in" — none. Every step has the actual code/command.

**3. Type consistency:** `is_tier3_path` referenced consistently across Tasks 3, 7. `TIER3_GLOBS` referenced consistently across Tasks 3, 12. Hook script paths consistent in Task 5 (settings.json) and Tasks 6/7/8 (creation).

**4. Trap check:** Task 11 modifies `~/.claude/CLAUDE.md` (outside the repo). The test for it lives in the repo (Task 11 Step 7 commits only the test). The global file itself isn't versioned by this repo — that's intentional (it's a shared global), but we lock its content via the in-repo test.
