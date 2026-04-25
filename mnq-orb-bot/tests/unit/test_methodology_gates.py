import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.data_loader import _drop_invalid_or_days
from backtest.monte_carlo import _max_drawdown_pct


def _bars(start: str, count: int, volume: int = 1000) -> pd.DataFrame:
    idx = pd.date_range(start, periods=count, freq="15min", tz="US/Eastern")
    return pd.DataFrame(
        {
            "open": [19000.0] * count,
            "high": [19020.0] * count,
            "low": [18990.0] * count,
            "close": [19010.0] * count,
            "volume": [volume] * count,
        },
        index=idx,
    )


def test_max_drawdown_pct_uses_peak_at_time_of_drawdown():
    equity = np.array([2500.0, 2000.0, 10000.0])
    peak = np.maximum.accumulate(equity)
    drawdowns = peak - equity

    assert _max_drawdown_pct(drawdowns, peak) == 0.20


def test_drop_invalid_or_days_removes_late_start_sessions():
    good = _bars("2025-06-16 09:30", 2)
    late = _bars("2025-06-17 09:45", 2)
    df = pd.concat([good, late])

    cleaned = _drop_invalid_or_days(df)

    assert sorted({str(date) for date in cleaned.index.date}) == ["2025-06-16"]


def test_drop_invalid_or_days_removes_synthetic_first_bar_sessions():
    synthetic = _bars("2025-06-18 09:30", 2)
    synthetic.iloc[0, synthetic.columns.get_loc("high")] = 19000.0
    synthetic.iloc[0, synthetic.columns.get_loc("low")] = 19000.0
    synthetic.iloc[0, synthetic.columns.get_loc("volume")] = 1
    good = _bars("2025-06-19 09:30", 2)
    df = pd.concat([synthetic, good])

    cleaned = _drop_invalid_or_days(df)

    assert sorted({str(date) for date in cleaned.index.date}) == ["2025-06-19"]
