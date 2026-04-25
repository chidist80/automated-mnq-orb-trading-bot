"""
Technical indicator calculations used as confluence filters.

All functions operate on pandas DataFrames with OHLCV columns
and return Series that can be merged back into the main DataFrame.
"""

import pandas as pd
import numpy as np
from typing import Optional


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    
    # Handle division by zero: if no losses, RSI = 100; if no gains, RSI = 0
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # Where avg_loss was 0, RSI should be 100 (all gains)
    result = result.fillna(100.0)
    return result


def ema_slope(series: pd.Series, period: int, lookback: int = 1) -> pd.Series:
    """Slope of an EMA (points per bar).
    
    Positive slope = trending up, negative = trending down.
    """
    ema_values = ema(series, period)
    return ema_values.diff(lookback)


def volume_ratio(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Volume as a multiple of the rolling average.

    Returns: 1.0 = average, 2.0 = double average volume, etc. When the rolling
    average is zero or NaN (cold start, holiday-thin sessions), returns NaN —
    downstream confluence checks treat NaN as "fail", which is the safe default
    rather than letting `inf` from division-by-zero pass every threshold.
    """
    avg_vol = volume.rolling(window=lookback, min_periods=5).mean()
    return volume / avg_vol.replace(0, np.nan)


def swing_low(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """Rolling swing low (lowest low over lookback period)."""
    return df["low"].rolling(window=lookback, min_periods=1).min()


def swing_high(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """Rolling swing high (highest high over lookback period)."""
    return df["high"].rolling(window=lookback, min_periods=1).max()


def add_all_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add all configured indicators to the DataFrame.
    
    Args:
        df: OHLCV DataFrame
        config: strategy_params.yaml loaded as dict
        
    Returns:
        DataFrame with indicator columns added
    """
    df = df.copy()
    
    # EMA — column name MUST match the period; backtester resolves it dynamically
    # via _ema_col. If you write ema_9 here while ema_period is 20, you get a silent
    # KeyError downstream. Keep this in sync.
    ema_period = config.get("ema_continuation", {}).get("ema_period", 9)
    df[f"ema_{ema_period}"] = ema(df["close"], ema_period)
    df[f"ema_{ema_period}_slope"] = ema_slope(df["close"], ema_period)
    
    # RSI
    rsi_period = config.get("confluences", {}).get("rsi", {}).get("period", 14)
    df["rsi"] = rsi(df["close"], rsi_period)
    
    # Volume ratio
    vol_lookback = config.get("confluences", {}).get("volume", {}).get("lookback_bars", 20)
    df["volume_ratio"] = volume_ratio(df["volume"], vol_lookback)
    
    # Swing levels
    df["swing_low_5"] = swing_low(df, 5)
    df["swing_high_5"] = swing_high(df, 5)
    
    return df


def check_confluences(
    bar: pd.Series,
    direction: str,
    config: dict,
) -> dict[str, bool]:
    """Check all confluence filters for a potential entry.
    
    Args:
        bar: The current bar with indicators already computed
        direction: "long" or "short"
        config: Confluence config section
        
    Returns:
        Dict of {filter_name: passed} for each enabled confluence
    """
    results = {}
    conf = config.get("confluences", {})
    
    # RSI check
    if conf.get("rsi", {}).get("enabled", True):
        rsi_val = bar.get("rsi", 50)
        ob = conf["rsi"].get("overbought", 70)
        os_ = conf["rsi"].get("oversold", 30)
        
        if direction == "long":
            results["rsi"] = rsi_val < ob
        else:
            results["rsi"] = rsi_val > os_
    
    # Volume check (breakout candle should have above-average volume)
    if conf.get("volume", {}).get("enabled", True):
        vol_ratio = bar.get("volume_ratio", 1.0)
        min_ratio = conf["volume"].get("breakout_multiplier", 1.5)
        results["volume"] = vol_ratio >= min_ratio
    
    # EMA slope check — read the column matching the configured period, not a hardcoded name
    if conf.get("ema_slope", {}).get("enabled", True):
        ema_period = config.get("ema_continuation", {}).get("ema_period", 9)
        slope = bar.get(f"ema_{ema_period}_slope", 0)
        min_slope = conf["ema_slope"].get("min_slope", 0.5)
        
        if direction == "long":
            results["ema_slope"] = slope > min_slope
        else:
            results["ema_slope"] = slope < -min_slope
    
    return results
