"""
Historical data loader for MNQ backtesting.
Supports CSV, Parquet, and IBKR historical data API.

Expected columns after normalization:
    timestamp, open, high, low, close, volume
    
Timestamp must be in US/Eastern timezone with 15-minute bars during RTH (09:30-16:00 ET).
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def load_csv(filepath: str | Path, **kwargs) -> pd.DataFrame:
    """Load and normalize CSV data.
    
    Handles common CSV formats from TradingView, NinjaTrader, and generic exports.
    Auto-detects column names and timestamp formats.
    """
    df = pd.read_csv(filepath, **kwargs)
    df = _normalize_columns(df)
    df = _normalize_timestamps(df)
    df = _filter_rth(df)
    df = _validate(df)
    logger.info(f"Loaded {len(df)} bars from {filepath} ({df.index[0]} to {df.index[-1]})")
    return df


def load_parquet(filepath: str | Path) -> pd.DataFrame:
    """Load and normalize Parquet data (Databento format)."""
    df = pd.read_parquet(filepath)
    df = _normalize_columns(df)
    df = _normalize_timestamps(df)
    df = _filter_rth(df)
    df = _validate(df)
    logger.info(f"Loaded {len(df)} bars from {filepath} ({df.index[0]} to {df.index[-1]})")
    return df


def load_ibkr_historical(*_args, **_kwargs) -> pd.DataFrame:
    """DEPRECATED — use `python scripts/fetch_data.py` instead.

    The legacy implementation didn't handle:
      - marketDataType=3 (required for DUO demo accounts to receive any data)
      - ContFuture vs Future distinction (ContFuture rejects endDateTime)
      - Quarterly rollover stitching for multi-year fetches
    Those are all handled by `scripts/fetch_data.py`. This stub fails loudly so
    no caller silently regresses to the old broken path.
    """
    raise NotImplementedError(
        "load_ibkr_historical() has been removed. Run "
        "`python scripts/fetch_data.py --months N --continuous` to populate "
        "data/mnq_15m.parquet, then load with load_parquet()."
    )


def resample_to_timeframe(df: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
    """Resample tick or minute data to desired bar size."""
    rule = f"{minutes}min"
    resampled = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled


# --- Internal helpers ---

COLUMN_ALIASES = {
    "time": "timestamp",
    "date": "timestamp",
    "datetime": "timestamp",
    "Date": "timestamp",
    "Time": "timestamp",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Vol": "volume",
    "vol": "volume",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map common column name variants to standard names."""
    df = df.rename(columns=COLUMN_ALIASES)
    required = {"open", "high", "low", "close", "volume"}
    
    # If timestamp is a column (not index), set it as index
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    
    return df[["open", "high", "low", "close", "volume"]].copy()


def _normalize_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure index is timezone-aware datetime in US/Eastern.

    Loud about ambiguous input. A naive index gets localized to US/Eastern but
    the caller sees a WARNING — silent UTC->Eastern shifts a CSV by 4-5h and
    the strategy then trades the wrong session, which is exactly the kind of
    bug we don't want in live execution.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    if df.index.tz is None:
        logger.warning(
            "Timestamp index is timezone-naive — assuming US/Eastern. "
            "If your source data is UTC, pre-localize before loading or you'll trade the wrong session."
        )
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")

    return df.sort_index()


def _filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to Regular Trading Hours only (09:30 - 16:00 ET)."""
    mask = (df.index.time >= pd.Timestamp("09:30").time()) & \
           (df.index.time < pd.Timestamp("16:00").time())
    filtered = df[mask].copy()
    logger.debug(f"RTH filter: {len(df)} → {len(filtered)} bars")
    return filtered


def _validate(df: pd.DataFrame) -> pd.DataFrame:
    """Basic data quality checks."""
    assert len(df) > 0, "Empty DataFrame after loading"
    assert not df.isnull().any().any(), f"NaN values found:\n{df.isnull().sum()}"
    assert (df["high"] >= df["low"]).all(), "High < Low detected"
    assert (df["high"] >= df["open"]).all(), "High < Open detected"
    assert (df["high"] >= df["close"]).all(), "High < Close detected"
    assert (df["volume"] >= 0).all(), "Negative volume detected"
    return df


def get_trading_days(df: pd.DataFrame) -> list[str]:
    """Return list of unique trading dates."""
    return sorted(df.index.date.astype(str).tolist())


def split_by_day(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split DataFrame into per-day DataFrames."""
    days = {}
    for date, group in df.groupby(df.index.date):
        days[str(date)] = group
    return days
