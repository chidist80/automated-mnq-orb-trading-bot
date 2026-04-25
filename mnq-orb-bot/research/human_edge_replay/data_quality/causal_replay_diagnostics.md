# Causal Replay Diagnostics

## Data Quality

- RTH dates: 686
- OR-clean dates: 560
- OR-invalid dates: 126
- Full-session clean dates: 527
- OR-clean dates with full-session issues: 33

### OR Tradability Gate

| quality        |   days |
|:---------------|-------:|
| clean          |    560 |
| zero_volume_or |     96 |
| late_start     |     30 |

### Full-Session Quality

| full_session_quality                           |   days |
|:-----------------------------------------------|-------:|
| clean                                          |    527 |
| zero_volume_or_and_post_or                     |     94 |
| or_clean_but_post_or_zero_volume               |     26 |
| zero_volume_post_or+missing_rth_minutes        |     25 |
| zero_volume_or_and_post_or+missing_rth_minutes |      7 |
| short_session                                  |      7 |

## Frozen Replay Check

- Tier 1: trades=95, pnl=$2186.70, pf=1.6845
- Tier 2 shadow: trades=150, pnl=$3668.50, pf=1.5447

## Tier 2 Directional Profile

| day_type   |   trades |   total_pnl |   avg_pnl |   win_rate |       pf |
|:-----------|---------:|------------:|----------:|-----------:|---------:|
| down_day   |       36 |     -708.24 |  -19.6733 |   0.277778 | 0.684503 |
| up_day     |      114 |     4376.74 |   38.3925 |   0.54386  | 1.97484  |

## Tier 2 RTH Return Buckets

| bucket        |   trades |   total_pnl |   avg_pnl |   win_rate |       pf |
|:--------------|---------:|------------:|----------:|-----------:|---------:|
| <= -1%        |        4 |     -105.36 |  -26.34   |   0.25     | 0.593236 |
| -1% to -0.25% |       16 |     -661.44 |  -41.34   |   0.1875   | 0.410702 |
| -0.25% to 0%  |       16 |       58.56 |    3.66   |   0.375    | 1.06782  |
| 0% to 0.25%   |       19 |     -946.46 |  -49.8137 |   0.210526 | 0.269199 |
| 0.25% to 1%   |       77 |     3997.32 |   51.9132 |   0.597403 | 2.49347  |
| >= 1%         |       18 |     1325.88 |   73.66   |   0.666667 | 3.55942  |

Rules are diagnostic only. No strategy thresholds are changed by this report.
