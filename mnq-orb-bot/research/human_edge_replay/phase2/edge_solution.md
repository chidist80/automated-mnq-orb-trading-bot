# Phase 2 — Edge Solution

## Verdict

**Do not ship the original ORB/EMA strategy.** The replayed human edge is real in the covered sample, but the mechanical ORB/EMA replicas do not capture it. The edge is in the discretionary selection and timing layer: waiting for price to move away from obvious mechanical levels, then entering at a materially better location, usually during the first 90 minutes.

## Gate 1 Result

- Human 1-contract covered P&L: $4,138.70
- Mechanical same-time-exit replica P&L: $-12,608.66
- Mechanical rule-exit replica P&L: $-1,131.66
- Mechanical capture ratio: -27.3%
- Gate G1.2 required mechanical capture >= 40%. Result: **FAIL / PIVOT**.

## Leading Human-Edge Filters

| candidate                |   all_n |   all_pnl |   all_avg |   all_win_rate |   is_n |   is_pnl |   is_avg |   is_win_rate |   oos_n |   oos_pnl |   oos_avg |   oos_win_rate |   oos_vs_is_avg |   win_rate_delta_pp | passes_sample_gate   | passes_split_stability   |
|:-------------------------|--------:|----------:|----------:|---------------:|-------:|---------:|---------:|--------------:|--------:|----------:|----------:|---------------:|----------------:|--------------------:|:---------------------|:-------------------------|
| morning_all              |      92 |   3300.33 |     35.87 |           0.90 |     57 |  1876.72 |    32.92 |          0.89 |      35 |   1423.61 |     40.67 |           0.91 |            1.24 |                1.95 | True                 | True                     |
| morning_short            |      72 |   2689.81 |     37.36 |           0.92 |     41 |  1507.59 |    36.77 |          0.90 |      31 |   1182.22 |     38.14 |           0.94 |            1.04 |                3.30 | False                | True                     |
| fade_extreme_or_pre_or   |      60 |   1908.82 |     31.81 |           0.90 |     42 |  1038.69 |    24.73 |          0.88 |      18 |    870.12 |     48.34 |           0.94 |            1.95 |                6.35 | False                | True                     |
| fade_extreme             |      45 |   1481.45 |     32.92 |           0.89 |     30 |   728.58 |    24.29 |          0.87 |      15 |    752.88 |     50.19 |           0.93 |            2.07 |                6.67 | False                | True                     |
| short_above_vwap_and_ema |      51 |   1182.40 |     23.18 |           0.86 |     29 |   595.79 |    20.54 |          0.90 |      22 |    586.61 |     26.66 |           0.82 |            1.30 |               -7.84 | False                | True                     |
| long_below_vwap_and_ema  |      27 |    672.21 |     24.90 |           0.89 |     21 |   398.70 |    18.99 |          0.86 |       6 |    273.51 |     45.59 |           1.00 |            2.40 |               14.29 | False                | False                    |
| pre_or_locked            |      15 |    427.36 |     28.49 |           0.93 |     12 |   310.11 |    25.84 |          0.92 |       3 |    117.25 |     39.08 |           1.00 |            1.51 |                8.33 | False                | True                     |
| orb_retest_labeled       |      21 |    350.85 |     16.71 |           0.81 |     13 |   222.33 |    17.10 |          0.77 |       8 |    128.51 |     16.06 |           0.88 |            0.94 |               10.58 | False                | False                    |

Interpretation: the broad morning-short and fade-extreme filters are stable in the 60/40 split, but they are filters on the human's entries, not executable triggers. They identify where the human found edge; they do not yet tell a bot when to click.

## Timing Signature

| price_advantage_bin   |   n |     pnl |   avg |   win_rate |   median_delta_minutes |
|:----------------------|----:|--------:|------:|-----------:|-----------------------:|
| human_better_gt_100pt |  51 | 1109.34 | 21.75 |       0.94 |                  42.42 |
| human_better_25_100pt |  34 |  950.45 | 27.95 |       0.82 |                   9.92 |
| human_better_5_25pt   |  17 |  862.89 | 50.76 |       0.88 |                   1.29 |
| human_worse_25_100pt  |  15 |  483.69 | 32.25 |       1.00 |                  56.24 |
| same_5pt              |   5 |  458.28 | 91.66 |       1.00 |                   5.06 |
| human_worse_gt_100pt  |   4 |  110.40 | 27.60 |       0.75 |                 124.52 |
| human_worse_5_25pt    |   7 |   83.36 | 11.91 |       0.71 |                  -3.92 |

Negative `delta_price_signed` means the human entered at a better price than the first same-day mechanical signal. Most P&L comes from entries 5 to 100+ points better than naive mechanical signals, which is the clearest fingerprint of the edge.

## Mechanical Universe Check

| rule             | direction   |   n |    pnl |   avg |   win_rate |   is_n |   is_avg |   is_pnl |   oos_n |   oos_avg |   oos_pnl | scope                   | passes_sample_gate   | passes_positive_oos   | or_class   |
|:-----------------|:------------|----:|-------:|------:|-----------:|-------:|---------:|---------:|--------:|----------:|----------:|:------------------------|:---------------------|:----------------------|:-----------|
| inverse_orb      | long        |  19 | 255.54 | 13.45 |       0.42 |      8 |     5.16 |    41.28 |      11 |     19.48 |    214.26 | rule+direction+or_class | False                | True                  | tight      |
| ema_continuation | short       |  39 | 252.24 |  6.47 |       0.38 |     23 |     7.38 |   169.68 |      16 |      5.16 |     82.56 | rule+direction+or_class | False                | True                  | normal     |
| orb_retest       | long        |  25 | 241.50 |  9.66 |       0.40 |     10 |     9.66 |    96.60 |      15 |      9.66 |    144.90 | rule+direction+or_class | False                | True                  | tight      |
| orb_break        | short       |  11 | 214.26 | 19.48 |       0.45 |      9 |    -2.34 |   -21.06 |       2 |    117.66 |    235.32 | rule+direction+or_class | False                | True                  | wide       |
| ema_continuation | short       |  16 | 207.06 | 12.94 |       0.44 |     13 |    16.47 |   214.08 |       3 |     -2.34 |     -7.02 | rule+direction+or_class | False                | False                 | wide       |
| inverse_orb      | long        |   9 | 158.94 | 17.66 |       0.44 |      7 |    40.52 |   283.62 |       2 |    -62.34 |   -124.68 | rule+direction+or_class | False                | False                 | wide       |
| ema_continuation | long        |  27 | 150.82 |  5.59 |       0.41 |     13 |     0.54 |     7.08 |      14 |     10.27 |    143.74 | rule+direction+or_class | False                | True                  | tight      |
| orb_retest       | short       |  10 |  96.60 |  9.66 |       0.40 |      8 |   -17.34 |  -138.72 |       2 |    117.66 |    235.32 | rule+direction+or_class | False                | True                  | wide       |
| orb_break        | long        |  13 |  89.58 |  6.89 |       0.38 |     10 |    27.66 |   276.60 |       3 |    -62.34 |   -187.02 | rule+direction+or_class | False                | False                 | wide       |
| orb_retest       | long        |  34 |  40.44 |  1.19 |       0.35 |     18 |    17.66 |   317.88 |      16 |    -17.34 |   -277.44 | rule+direction+or_class | False                | False                 | normal     |
| orb_retest       | long        |  72 |  11.52 |  0.16 |       0.35 |     38 |     8.71 |   331.08 |      34 |     -9.40 |   -319.56 | rule+direction          | False                | False                 | nan        |
| orb_break        | short       |  21 | -49.14 | -2.34 |       0.33 |     10 |    27.66 |   276.60 |      11 |    -29.61 |   -325.74 | rule+direction+or_class | False                | False                 | tight      |

No unconditional mechanical rule clears the sample gate and stable positive OOS requirement. Small positive cells exist, but they are n=9-39 and not strong enough for production.

## What To Build Next

1. Build an executable `fade_extreme_after_failed_mechanical_signal` research rule:
   - RTH only, 09:30-11:00 ET.
   - Primary side: short. Secondary side: long only when below VWAP and below 15m EMA9.
   - Require price to be extended from VWAP and 15m EMA9, then show a completed-bar stall/reversal trigger.
   - Do not enter on the first breakout/retest signal.
2. Test it on every clean day, not just human-traded days.
3. Use 1-contract economics, 0.50 point entry slippage, 0.50 point exit slippage, and $1.34 round-trip commission.
4. Require n>=80, positive OOS, and no single-day P&L concentration before it can become a production candidate.

## Current Best Call

The automatable edge, if it exists, is a **delayed morning fade/extreme-location rule**, not a classic ORB retest or EMA continuation rule. Treat the original ORB/EMA strategy as a feature generator and context map, not as the entry model.
