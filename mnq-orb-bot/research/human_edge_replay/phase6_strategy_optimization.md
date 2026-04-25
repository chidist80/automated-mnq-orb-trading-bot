# Phase 6 — Strategy Optimization Pass

## Objective

Do not optimize for win rate. Optimize for deployable expectancy:

- full-history core gates pass at 5.0 points round-trip slippage
- corrected Monte Carlo 20% drawdown risk stays below 5%
- OOS average remains positive and reasonably close to IS
- total P&L and CAGR improve only after those constraints are met

## Main result

The current `$3,750` pilot should stay unchanged. The extra pass did not find a better `$3,750` version that improves net return while keeping corrected drawdown risk below 5%.

Current pilot:

`or_retest_slip_filter|long|buffer_points=0|confirm_pattern=break_prev|end=11:00|max_close_vwap_delta=65|max_risk_points=120|max_stop=120|min_reward_points=8|or_class=normal|rr=1.25|start=10:00|stop_mode=fixed|stop_points=40|target_mode=rr|time_exit=15:55|touch_tolerance=5`

At 5.0 points round-trip slippage:

| Capital | Trades | P&L | Avg | Win Rate | PF | Max DD | MC 20% DD Risk | Active CAGR |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| $3,750 | 95 | $2,186.70 | $23.02 | 61.05% | 1.6845 | -$417.06 | 3.30% | 21.66% |
| $7,500 | 95 | $2,186.70 | $23.02 | 61.05% | 1.6845 | -$417.06 | 0.01% | 11.54% |

## Why not change the `$3,750` rule

The tempting filters improved PF or win rate, but they either lost sample size, lost total expectancy, or failed the core gates.

Examples at 5.0 points round-trip slippage:

| Variant | Trades | P&L | Win Rate | PF | Gate Issue |
| --- | ---: | ---: | ---: | ---: | --- |
| Add `max_signal_volume_ratio=2.5` | 85 | $1,970.10 | 61.18% | 1.6908 | Lower P&L, no real win-rate gain |
| Add `max_signal_volume_ratio=2.25` | 82 | $1,869.12 | 60.98% | 1.6770 | Lower P&L, no real win-rate gain |
| Use `1.0R` target | 95 | $1,846.70 | 66.32% | 1.6680 | Higher win rate, lower expectancy |
| Add `EMA slope <= 20` and `OR close pos >= 0.4` | 55 | $2,220.30 | 70.91% | 2.6070 | Too few trades |

Conclusion: the current pilot is already the best `$3,750` balance of return, sample size, slippage robustness, and corrected drawdown risk.

## Capital-tiered upgrade

At `$7,500+`, the optimizer points to a stronger rule:

`or_retest_slip_filter|long|buffer_points=0|confirm_pattern=break_prev|end=11:00|max_risk_points=120|max_signal_volume_ratio=2|max_stop=120|min_reward_points=8|or_class=normal_tight|rr=2|start=10:00|stop_mode=fixed|stop_points=40|target_mode=rr|time_exit=15:55|touch_tolerance=5`

Plain English changes versus the `$3,750` pilot:

- allows normal and tight OR days
- uses a 2.0 max signal-volume-ratio filter
- uses a 2R target instead of 1.25R
- keeps the same 40-point stop and next-1-minute-open execution

At 5.0 points round-trip slippage:

| Capital | Trades | P&L | Avg | Win Rate | PF | Max DD | MC 20% DD Risk | Active CAGR |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| $3,750 | 150 | $3,668.50 | $24.46 | 48.00% | 1.5447 | -$556.08 | 17.41% | 33.79% |
| $7,500 | 150 | $3,668.50 | $24.46 | 48.00% | 1.5447 | -$556.08 | 0.95% | 18.52% |

Conclusion: this is not a `$3,750` rule. It is the better growth rule only after capital rises to `$7,500+`.

## Final optimization plan

Use a two-tier strategy, not a single over-tuned rule.

Tier 1, `$3,750-$7,499`:

- Run the current pilot unchanged.
- 1 MNQ max size.
- 40-point stop, 1.25R target.
- Normal OR only.
- VWAP extension guard: signal close no more than 65 points above VWAP.

Tier 2, `$7,500+`:

- Switch to the higher-throughput variant.
- 1 MNQ max size initially.
- 40-point stop, 2R target.
- Normal plus tight OR.
- Signal volume ratio <= 2.0.

Do not blend both rules live. They are overlapping expressions of the same edge, not independent systems. Pick the tier based on capital and execute one rule.

## Shadow research tags

Track these forward, but do not deploy them yet:

- `EMA slope <= 20 and OR close position >= 0.4`: high PF and high win rate, but only 55 historical trades.
- `1.0R target`: viable if psychology or prop-firm consistency matters more than CAGR, but it is not the best expectancy version.
- selloff-day avoidance proxies: high signal volume ratio and excessive EMA extension are the most plausible causal warnings.

## Decision

The optimized path is capital-tiered:

- `$3,750`: keep the frozen pilot.
- `$7,500+`: graduate to the higher-throughput normal/tight OR, volume-capped, 2R variant after forward-paper validation.

The next engineering step remains the same: implement the isolated causal backtester/tests and paper-forward logger. The only addition is that the paper-forward logger should score both capital-tier rules side by side while executing only the Tier 1 pilot.
