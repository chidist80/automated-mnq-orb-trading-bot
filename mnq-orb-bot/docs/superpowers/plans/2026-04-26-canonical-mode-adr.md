# ADR: Canonical Paper-Forward Mode (Phase 6 Causal OR-Retest)

**Date:** 2026-04-26
**Status:** Accepted (canonical Tier 1 + A+); v3-v6 combined/regime additions RETRACTED
**Tag:** `phase6-causal-or-retest-v1-frozen`

> **2026-04-26 RETRACTION NOTICE.** Subsequent research v3-v6 produced combined
> LONG+SHORT recommendations that depended on a non-causal regime computation
> (look-ahead in 5d_ret / atr_pct / ATR-target). The independent verifier
> identified the bug; my own causal rerun confirmed it (`findings_v7_RETRACTION.md`).
> All combined-shadow and regime-conditioned recommendations are withdrawn.
> The canonical Tier 1 strict and A+ specifications below are unaffected —
> they use only causal signal-bar features (signal-bar VWAP, EMA9 slope at
> signal time, OR close position from 09:30-09:44 bars). **Phase 6.5
> paper-forward should track ONLY canonical Tier 1 + A+, NOT the v3-v6
> combined rules.**

## Context

Two candidate execution rules survived the QA pass on the causal 1-minute OR
retest harness:

- **Tier 1 strict** — long-only, normal OR class, 40pt stop / 1.25R, 65pt VWAP
  delta cap, 5pt RT slippage.
- **A+ shadow** — Tier 1 plus EMA9 slope ≤ 20 and OR close position ≥ 0.4.

The harness has two switches that materially affect the historical sample:

| Switch | Default | Strict |
|---|---|---|
| Full-session-clean gate | off (frozen 95-trade Tier 1 baseline includes `2023-12-19` and 5 other contaminated days) | on (excludes those 6 days) |
| 2023 inclusion | included | (already excluded by strict, since the only 2023 Tier 1 trade is `2023-12-19`) |

Data-quality audit by year:

| Year | Days | OR-clean % | Full-session-clean % |
|---|---|---|---|
| 2023 | 105 | 1.0% | 0.0% |
| 2024 | 252 | 93.3% | 82.1% |
| 2025 | 250 | 98.4% | 97.2% |
| 2026 | 79 | 98.7% | 97.5% |

2023 demo-feed bars are unusable.

## Decision

**Canonical paper-forward mode is `--require-full-session-clean --start 2024-01-01`.**

Rule prefixes evaluated as execution candidates:

- **`tier1`** — primary candidate. Goes live first.
- **`a_plus_shadow`** — secondary candidate. Tracked forward; promotion to
  live only after 50+ forward A+ trades pass the gate.
- **`tier2_shadow`** — informational only (capital band $7,500+ outside
  current account).

## Frozen canonical baseline

Verified via:

```bash
python scripts/run_causal_paper_forward.py \
  --require-full-session-clean \
  --start 2024-01-01 \
  --out research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv
```

| Metric | Tier 1 | A+ shadow |
|---|---|---|
| Trades | 89 | 51 |
| Total PnL | $2,035.74 | $2,076.66 |
| Avg PnL | $22.87 | $40.72 |
| Win rate | 60.67% | 70.59% |
| Profit factor | 1.6737 | 2.6035 |
| Max DD | $417.06 | $424.38 |
| MC ruin @ $3,750 | 3.13% | 0.06% |
| MC ruin @ $5,000 | 0.68% | 0.005% |
| MC ruin @ $7,500 | 0.035% | 0.000% |

`research/human_edge_replay/paper_forward/canonical_mc_ruin.json` records the
full Monte Carlo distribution (20k bootstrap, seed 42).

## Capital sizing note

The current IBKR account holds $2,500. Both Tier 1 and A+ baselines were
sized against $3,750-$7,499 (Tier 1) and $3,750+ (A+). **Live execution must
not start until the account is funded to $3,750 minimum**, regardless of
forward gate status. At $2,500, Tier 1 ruin probability is materially higher
than the 20% ceiling in stress scenarios.

## Mode is not a tuning knob

The canonical mode is bound to the immutable `(date, rule_version)` key in
the journal. Switching modes mid-stream creates conflicting evidence and
will hard-fail `append_journal`. To change canonical mode, bump
`RULE_VERSION` and regenerate the journal as a new artifact.

## Promotion gate

Forward execution must clear `scripts/check_paper_forward_gate.py` against
this canonical baseline before any live-account ramp. Gate criteria:

- ≥ 50 eligible forward trades (Tier 1 or A+, evaluated independently)
- ≥ 90 days elapsed (one calendar quarter)
- PF ≥ 1.4
- Avg PnL ≥ 70% of canonical avg ($16.01 for Tier 1, $28.50 for A+)
- Max forward DD ≤ 1.5× canonical DD ($625.59 for Tier 1, $636.57 for A+)
- One-sided t-test: forward avg has not degraded ≥ 30% at p ≤ 0.10

The script auto-loads the canonical baseline so any future re-tag (e.g.
after a live-feed data refresh) updates the gate without code changes.

## Open dependencies

1. **Live IBKR data feed verification** is in flight. Until it lands, paper
   forward runs against demo (`marketDataType=3`) data. Demo retention and
   feed quality are the floor; further robustness improvements are blocked
   on live verification.

   **Empirical confirmation (2026-04-26):** A fresh 48-month chunked refetch
   against TWS paper produced a strict superset of the prior pull (697 RTH
   days vs 686, +11 days from earlier in 2023) but the 2024+ window was
   bit-identical: same row counts, same zero-volume %, same Tier 1 / A+
   / Tier 2 metrics down to the cent. IBKR's HMDS for 1-minute MNQ is
   deterministic for a given (instrument, period). **Refetching cannot
   improve canonical-window quality; only a live CME feed or alternative
   source can.** Demo only retains ~9 quarterly contracts back from now
   (currently MNQM4 forward); 2022 and earlier 2023 contracts return
   Error 200 and cannot be pulled.
2. **Account funding to ≥ $3,750.** Hard prerequisite for live ramp.
3. **Forward journal does not yet exist.** Created by the first run of
   `python scripts/run_causal_paper_forward.py --mode append --require-full-session-clean --start 2024-01-01 --out research/human_edge_replay/paper_forward/forward_journal.csv`.
   See runbook below.

## Data-refresh runbook

Run when TWS or IB Gateway is up and data has aged > 1 trading day.

```bash
# 1. Confirm IBKR is reachable
nc -z 127.0.0.1 7497 && echo "TWS paper port OPEN" || echo "TWS not running"

# 2. Re-fetch 1-minute MNQ history (DUO demo; will pull what the demo feed retains)
python scripts/fetch_data.py \
  --bar-size "1 min" \
  --output data/mnq_1m.parquet \
  --months 36 \
  --port 7497 \
  --market-data-type 3
# When live feed lands: --market-data-type 1 against the funded account port.

# 3. Regenerate the canonical baseline
python scripts/run_causal_paper_forward.py \
  --require-full-session-clean \
  --start 2024-01-01 \
  --out research/human_edge_replay/paper_forward/canonical_strict_2024plus.csv

# 4. Refresh data-quality diagnostics
python scripts/diagnose_causal_replay.py

# 5. Refresh canonical MC ruin
python scripts/mc_ruin_recheck.py

# 6. Verify nothing surprising shifted
python -m pytest -p no:capture tests/unit -q

# 7. Tag the new baseline if metrics moved
git add -A && git commit -m "chore(data): refresh canonical baseline against fresh data feed"
git tag -a phase6-causal-or-retest-v1-frozen-$(date +%Y%m%d) \
  -m "Canonical baseline refresh against $(date +%Y-%m-%d) data pull"
```

## Daily forward append (once forward journal is initialized)

```bash
# After 16:00 ET each weekday
python scripts/run_causal_paper_forward.py \
  --mode append \
  --require-full-session-clean \
  --start 2024-01-01 \
  --out research/human_edge_replay/paper_forward/forward_journal.csv

# Weekly gate evaluation
python scripts/check_paper_forward_gate.py
```

## What this ADR explicitly does NOT decide

- Tier 3 production wiring (`bot/execution/`, `bot/signal_generator.py`,
  `bot/risk_manager.py`, `bot/watchdog.py`, `bot/health_monitor.py`,
  `backtest/strategies/core.py`, `config/*.yaml`). Per project CLAUDE.md
  these require `superpowers:brainstorming` and `/deepreview` before any
  edits, and this ADR is research-layer only.
- Order-routing semantics, intra-bar execution model, or stop placement
  mechanics in IBKR. Those are Tier 3 brainstorm material.
- Whether to combine Tier 1 and A+ as concurrent strategies on the same
  capital. Treat them as alternative candidates for now; combining is a
  separate brainstorm with explicit position-sizing math.
