# Human Edge Replay

- Topstep account/share: 14244466
- Trade date request: 2025-11-07 through 2026-04-25
- 1-minute data: `/Users/tisha/Documents/GitHub/automated-mnq-orb-trading-bot/mnq-orb-bot/data/mnq_1m.parquet`
- Merge rule: same symbol, same direction, same ET date, entry within 90 minutes of prior idea exit
- Raw Topstep rows imported: 664
- Normalized trade ideas: 200
- Ideas covered by local RTH 1-minute data: 138
- Covered profitable ideas: 126 (91.3%)
- Covered net P&L: $15,869.56

## Coverage Notes

The local replay tape is RTH-only. Imported Topstep ideas outside 09:30-16:00 ET are retained but labeled `outside_rth_1m`, so their context is not guessed from unavailable data.
Pre-entry labels use the last completed 1-minute bar and completed 5/15-minute bars only. EMA/RSI/volume-ratio indicators are computed on the full RTH history before daily slicing, not reset at each open. The replay MAE/MFE uses 1-minute OHLC bars, so intraminute ordering remains approximate.

## Grouping Sensitivity

|   merge_gap_minutes |   trade_ideas |   avg_rows_per_idea |   multi_fill_ideas |   max_fill_count |
|--------------------:|--------------:|--------------------:|-------------------:|-----------------:|
|                   0 |           481 |             1.38046 |                 92 |               12 |
|                   1 |           439 |             1.51253 |                 94 |               19 |
|                   2 |           409 |             1.62347 |                105 |               19 |
|                   5 |           352 |             1.88636 |                112 |               19 |
|                  10 |           314 |             2.11465 |                112 |               19 |
|                  15 |           288 |             2.30556 |                117 |               19 |
|                  30 |           256 |             2.59375 |                119 |               19 |
|                  60 |           222 |             2.99099 |                116 |               19 |
|                  90 |           200 |             3.32    |                113 |               19 |
|                 120 |           191 |             3.47644 |                112 |               19 |

## Top Covered Context Groups

| setup_label                      | direction   | time_bucket   | or_classification   | rsi_regime      | entry_vs_vwap   | entry_vs_15m_ema9   | trend_state         |   total_ideas |   profitable_ideas |   total_net_pnl |   avg_net_pnl |   median_net_pnl |   avg_mfe_points |   avg_mae_points |   win_rate |
|:---------------------------------|:------------|:--------------|:--------------------|:----------------|:----------------|:--------------------|:--------------------|--------------:|-------------------:|----------------:|--------------:|-----------------:|-----------------:|-----------------:|-----------:|
| pre_or_locked_trade              | long        | 09:30-10:00   | tight               | unknown         | below           | unknown             | unknown             |             1 |                  1 |         1172.14 |       1172.14 |          1172.14 |           159.12 |            45.38 |       1.00 |
| discretionary_or_unclassified    | long        | 11:00-12:00   | normal              | neutral         | below           | below               | mixed               |             1 |                  1 |         1104.90 |       1104.90 |          1104.90 |           370.99 |           229.76 |       1.00 |
| discretionary_or_unclassified    | long        | 10:00-11:00   | tight               | neutral         | below           | below               | downtrend_below_ema |             1 |                  1 |          726.92 |        726.92 |           726.92 |           104.37 |            80.63 |       1.00 |
| discretionary_or_unclassified    | short       | 09:30-10:00   | normal              | bull_trend_zone | above           | above               | uptrend_above_ema   |             2 |                  2 |          646.98 |        323.49 |           323.49 |            88.63 |            11.50 |       1.00 |
| discretionary_or_unclassified    | short       | 10:00-11:00   | normal              | bull_trend_zone | above           | above               | uptrend_above_ema   |             7 |                  7 |          642.88 |         91.84 |            85.32 |            92.41 |            47.98 |       1.00 |
| pre_or_locked_trade              | short       | 09:30-10:00   | normal              | unknown         | above           | unknown             | unknown             |             3 |                  3 |          611.26 |        203.75 |           251.90 |           145.73 |            73.77 |       1.00 |
| discretionary_or_unclassified    | short       | 10:00-11:00   | wide                | neutral         | above           | above               | uptrend_above_ema   |             2 |                  2 |          602.52 |        301.26 |           301.26 |           183.02 |            47.23 |       1.00 |
| ema_continuation_candidate       | short       | 10:00-11:00   | normal              | neutral         | above           | near                | downtrend_below_ema |             2 |                  2 |          550.90 |        275.45 |           275.45 |           118.00 |            38.75 |       1.00 |
| discretionary_or_unclassified    | short       | 11:00-12:00   | normal              | neutral         | above           | above               | uptrend_above_ema   |             2 |                  1 |          550.10 |        275.05 |           275.05 |           187.67 |            48.08 |       0.50 |
| pre_or_locked_trade              | long        | 09:30-10:00   | wide                | unknown         | below           | unknown             | unknown             |             2 |                  2 |          443.08 |        221.54 |           221.54 |            48.35 |            58.52 |       1.00 |
| discretionary_or_unclassified    | short       | 09:30-10:00   | normal              | neutral         | below           | below               | downtrend_below_ema |             3 |                  3 |          404.06 |        134.69 |           119.28 |           115.12 |            83.54 |       1.00 |
| discretionary_or_unclassified    | short       | 09:30-10:00   | normal              | neutral         | below           | above               | uptrend_above_ema   |             1 |                  1 |          400.52 |        400.52 |           400.52 |           254.38 |            83.88 |       1.00 |
| discretionary_or_unclassified    | short       | 12:00-13:30   | tight               | neutral         | above           | above               | uptrend_above_ema   |             2 |                  2 |          379.36 |        189.68 |           189.68 |            59.29 |            55.46 |       1.00 |
| pre_or_locked_trade              | short       | 09:30-10:00   | normal              | unknown         | below           | unknown             | unknown             |             2 |                  2 |          374.66 |        187.33 |           187.33 |           152.98 |            47.65 |       1.00 |
| orb_breakout_or_retest_candidate | long        | 12:00-13:30   | normal              | neutral         | below           | below               | downtrend_below_ema |             2 |                  2 |          367.86 |        183.93 |           183.93 |            79.20 |            66.42 |       1.00 |

## Coverage Breakdown

| coverage_status            |   ideas |   net_pnl |
|:---------------------------|--------:|----------:|
| covered                    |     138 |  15869.56 |
| outside_rth_1m             |      51 |   3400.06 |
| unclean_day_zero_volume_or |       6 |    153.62 |
| missing_day                |       5 |    439.56 |
