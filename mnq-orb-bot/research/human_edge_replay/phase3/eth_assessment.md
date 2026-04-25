# Phase 3 — ETH Coverage Assessment

## Question
Is fetching ETH (extended-hours) 1-minute MNQ data worth the effort to extend the replay to the 51 ideas currently labeled `outside_rth_1m`?

## Evidence

The 51 outside-RTH ideas:

| Metric | As-traded | 1-contract simulated |
|---|---:|---:|
| Sum P&L | $3,400.06 | $814.41 |
| Mean per idea | $66.67 | $15.97 |
| Median per idea | $30.26 | $13.66 |
| Win rate | — | 92.2% |

The $3,400 as-traded headline is dominated by **4 conviction-sized trades** at 15, 39, 44, 60 contracts. A $2,500 IBKR account capped at 1 contract cannot replicate that sizing.

### Time-of-day distribution
- Pre-RTH (45 ideas): heavily concentrated 02:00–08:00 ET — European session overnight
- Post-RTH (6 ideas): clustered ~18:00 ET — Asian-session open

### Direction skew
- Pre-market shorts: 44 ideas, $716 sum (1c sim), $16.3/idea
- Pre-market longs: 7 ideas, $99 sum (1c sim), $14.1/idea

The pattern is suggestive (overnight short bias on overseas-session weakness) but **n=44 with $716 sum is too small to validate as a standalone edge**.

## Decision

**Skip ETH data fetch in this research phase.**

Rationale:
1. Adding ETH coverage adds at most **$815 over 5 months** to the 1-contract addressable universe — a 16% lift on a $4,138 base.
2. Phase 1 already showed the RTH human edge fails Gate 1 mechanical replication. Adding ETH data won't change that conclusion; it would only make a bigger universe to fail-to-replicate against.
3. The IBKR connection has had timeout issues in past sessions (memory ID #74); fetching 5 months of ETH 1m data is non-trivial and not on the critical path.
4. The pre-market short bias warrants a future, separate experiment — but only if the bot's account, watchdog, and overnight-handling infrastructure mature enough to operate outside RTH. That's well beyond Phase 2 live-execution scope.

## Carry-forward note for the production handoff (if Phase 5 fires)

If the bot eventually graduates to ETH operation, the suggestive pattern to test is:
- Direction: short
- Time window: 02:00–08:00 ET pre-market
- Hypothesis: shorting overnight-session strength fades into US open

That experiment requires: ETH 1m data, an overnight-safe execution path, and explicit risk_params for overnight position carry. **Not in scope for this phase.**
