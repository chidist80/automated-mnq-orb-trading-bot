# Phase 5 Edge Decision Plan

## Current conclusion

We have a grounded autonomous edge candidate, but it is narrower than the original discretionary description.

The executable edge is not "15m ORB/EMA discretionary trading." The recoverable mechanical signal is:

- long only
- normal/non-wide opening ranges
- delayed retest after a completed upside OR breakout
- completed-bar confirmation
- next 1-minute-open entry
- one trade per day
- fixed 40-point stop

The key recovery came from preserving execution reality. The original 15-minute backtest allowed same-bar touch-and-close confirmation fills that could not be reproduced live. The new candidate waits for the 1-minute bar to complete, enters on the next 1-minute open, and tests stop/target order on 1-minute bars with conservative same-minute ambiguity handling.

## Recommended pilot candidate

Use this as the first autonomous candidate because it clears the gates at `$3,750` capital and survives 1.0, 1.5, 3.0, and 5.0 point round-trip slippage.

Rule id:

`or_retest_slip_filter|long|buffer_points=0|confirm_pattern=break_prev|end=11:00|max_close_vwap_delta=65|max_risk_points=120|max_stop=120|min_reward_points=8|or_class=normal|rr=1.25|start=10:00|stop_mode=fixed|stop_points=40|target_mode=rr|time_exit=15:55|touch_tolerance=5`

Plain English:

1. Build the OR from 09:30-09:44 ET using 1-minute RTH bars.
2. Trade only normal OR days. Skip tight and wide OR days.
3. Require a completed upside OR breakout first: close above OR high.
4. From 10:00 through 11:00 ET, look for a completed retest bar where:
   - low trades to OR high + 5 points or lower
   - close remains above the OR high
   - close breaks the prior 1-minute high
   - close is no more than 65 points above VWAP
5. Enter long at the next 1-minute open.
6. Stop: 40 MNQ points.
7. Target: 1.25R, or 50 MNQ points.
8. Time exit: 15:55 ET.
9. One trade per day.

Full-history result at 5.0 points round-trip slippage:

| Metric | Value |
| --- | ---: |
| Trades | 95 |
| Total P&L | $2,186.70 |
| Avg/trade | $23.02 |
| Win rate | 61.05% |
| Profit factor | 1.6845 |
| Max drawdown | -$417.06 |
| IS avg | $26.45 |
| OOS avg | $17.87 |
| Largest-day concentration | 4.28% |

Slippage sensitivity:

| Round-trip slippage | Trades | P&L | Avg | PF | Max DD | Core gates |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1.0 | 95 | $2,750.70 | $28.95 | 1.9280 | -$381.06 | pass |
| 1.5 | 95 | $2,702.70 | $28.45 | 1.9063 | -$385.56 | pass |
| 3.0 | 95 | $2,558.70 | $26.93 | 1.8427 | -$399.06 | pass |
| 5.0 | 95 | $2,186.70 | $23.02 | 1.6845 | -$417.06 | pass |

Monte Carlo at 5.0 points round-trip slippage:

| Capital | Deterministic DD % | MC 20% DD risk | MC loss risk |
| ---: | ---: | ---: | ---: |
| $2,500 | 12.06% | 14.51% | 0.60% |
| $3,750 | 8.86% | 3.38% | 0.54% |
| $7,500 | 4.93% | 0.01% | 0.47% |

Decision: use `$3,750` as the minimum capital gate for this candidate. `$2,500` fails the corrected drawdown-risk methodology.

## Higher-expectancy alternate

This variant earns more but should require `$7,500` capital.

Rule id:

`or_retest_slip_filter|long|buffer_points=0|confirm_pattern=break_prev|end=11:00|max_risk_points=120|max_stop=120|min_reward_points=8|min_signal_rsi=60|or_class=normal_tight|rr=2|start=10:10|stop_mode=fixed|stop_points=40|target_mode=rr|time_exit=15:55|touch_tolerance=5`

Full-history result at 5.0 points round-trip slippage:

| Metric | Value |
| --- | ---: |
| Trades | 86 |
| Total P&L | $2,712.26 |
| Avg/trade | $31.54 |
| Win rate | 51.16% |
| Profit factor | 1.7479 |
| Max drawdown | -$450.72 |
| IS avg | $37.29 |
| OOS avg | $23.16 |

Monte Carlo at 5.0 points round-trip slippage:

| Capital | Deterministic DD % | MC 20% DD risk | MC loss risk |
| ---: | ---: | ---: | ---: |
| $2,500 | 14.40% | 23.29% | 0.60% |
| $3,750 | 10.29% | 7.03% | 0.60% |
| $7,500 | 5.54% | 0.19% | 0.62% |

Decision: do not run this variant at `$3,750`; its corrected Monte Carlo drawdown risk is too high.

## Why this is executable

- The OR is computed from completed 1-minute bars.
- The breakout is known before the retest signal is allowed.
- The retest signal uses only a completed 1-minute bar.
- Entry is at the next 1-minute open, not at a touched level inside the confirmation bar.
- Stop/target sequencing is simulated on 1-minute bars.
- If stop and target both print in the same minute, the stop wins.
- Slippage is tested as a round-trip sensitivity up to 5.0 points.

## Remaining weakness

No candidate passes the strict `adjusted_t >= 1.0` multiple-comparison screen. The recommended candidate has `adjusted_t = 0.075` because this phase tried 1,152 variants. Treat this as a candidate edge that cleared operational gates, not as a statistically final system.

The sample is also modest: 95 trades on the recommended candidate. That is enough for a pilot gate, not enough for blind production sizing.

## Next steps

1. Freeze the candidate spec above and stop searching on this same sample until validation is separated.
2. Implement an isolated causal backtester test for this exact rule:
   - proves no same-bar fill after close confirmation
   - proves one trade per day
   - proves normal-only OR classification
   - proves stop-first ambiguity handling
   - proves slippage accounting
3. Run a contract-roll/data-quality review on `data/mnq_1m.parquet`.
4. Add a forward-paper harness:
   - emit daily eligible/not-eligible decision
   - log OR class, breakout time, signal time, VWAP delta, entry, stop, target, exit
   - compare live/paper fills to 1-minute replay expectations
5. Run paper forward until either:
   - 20 eligible trades complete with PF >= 1.3 and no execution divergence, or
   - the candidate loses 2R net from model slippage-adjusted expectancy, whichever comes first.
6. Only after the forward-paper gate passes, wire the strategy into production with `$3,750` minimum capital and 1 MNQ max size.
7. Keep the `$7,500` higher-expectancy alternate as a second-stage rule, not the initial autonomous deployment.
