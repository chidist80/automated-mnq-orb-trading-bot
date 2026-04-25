"""
Tests for MNQ ORB Bot core logic.

Run with: pytest tests/ -v
"""

import pandas as pd
import numpy as np
import pytest
from datetime import datetime

from backtest.or_detector import (
    detect_opening_range, detect_breakout, detect_retest, detect_failed_breakout,
)
from backtest.indicators import rsi, ema, ema_slope, volume_ratio, check_confluences


# --- Test Data Helpers ---

def make_day_bars(
    bars: list[dict],
    date: str = "2025-06-15",
    start_time: str = "09:30",
) -> pd.DataFrame:
    """Create a test DataFrame of 15-minute bars.
    
    bars: list of dicts with keys: open, high, low, close, volume
    """
    timestamps = []
    base = pd.Timestamp(f"{date} {start_time}", tz="US/Eastern")
    for i in range(len(bars)):
        timestamps.append(base + pd.Timedelta(minutes=15 * i))
    
    df = pd.DataFrame(bars, index=pd.DatetimeIndex(timestamps))
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            if col == "volume":
                df[col] = 1000
            else:
                df[col] = 19000.0
    return df


# --- Opening Range Detection Tests ---

class TestOpeningRangeDetection:
    def test_basic_or_detection(self):
        """First bar defines the Opening Range."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19030, "high": 19070, "low": 19020, "close": 19060, "volume": 3000},
        ])
        or_ = detect_opening_range(bars, 15)
        assert or_.high == 19050
        assert or_.low == 18980
        assert or_.size == 70
        assert or_.direction == "bullish"  # close > open
    
    def test_bearish_or(self):
        """OR candle with close < open is bearish."""
        bars = make_day_bars([
            {"open": 19050, "high": 19060, "low": 18990, "close": 19000, "volume": 5000},
            {"open": 19000, "high": 19010, "low": 18980, "close": 18990, "volume": 3000},
        ])
        or_ = detect_opening_range(bars, 15)
        assert or_.direction == "bearish"
    
    def test_doji_or(self):
        """OR candle with close == open is neutral."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19000, "volume": 5000},
            {"open": 19000, "high": 19010, "low": 18990, "close": 19005, "volume": 3000},
        ])
        or_ = detect_opening_range(bars, 15)
        assert or_.direction == "neutral"
    
    def test_or_classification_with_history(self):
        """Wide OR should be classified based on lookback percentile."""
        bars = make_day_bars([
            {"open": 19000, "high": 19200, "low": 18800, "close": 19100, "volume": 5000},
            {"open": 19100, "high": 19150, "low": 19050, "close": 19120, "volume": 3000},
        ])
        # History of smaller ORs
        lookback = [30, 40, 50, 35, 45, 55, 42, 38, 50, 60]
        or_ = detect_opening_range(bars, 15, lookback, wide_percentile=80)
        assert or_.classification == "wide"  # 400 pts is above 80th percentile of 30-60
    
    def test_tight_or_classification(self):
        """Tight OR should be classified when in bottom 25%."""
        bars = make_day_bars([
            {"open": 19000, "high": 19010, "low": 18995, "close": 19005, "volume": 5000},
            {"open": 19005, "high": 19020, "low": 19000, "close": 19010, "volume": 3000},
        ])
        lookback = [50, 60, 70, 55, 65, 80, 72, 58, 62, 75]
        or_ = detect_opening_range(bars, 15, lookback, wide_percentile=80)
        assert or_.classification == "tight"  # 15 pts is below 25th percentile of 50-80


# --- Breakout Detection Tests ---

class TestBreakoutDetection:
    def test_long_breakout(self):
        """Candle closing above OR high is a long breakout."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19030, "high": 19080, "low": 19020, "close": 19060, "volume": 4000},  # Closes above 19050
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        assert bo is not None
        assert bo.direction == "long"
        assert bo.candle_close == 19060
    
    def test_short_breakout(self):
        """Candle closing below OR low is a short breakout."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19010, "high": 19020, "low": 18950, "close": 18960, "volume": 4000},  # Closes below 18980
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        assert bo is not None
        assert bo.direction == "short"
    
    def test_no_breakout(self):
        """Price staying inside OR produces no breakout."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19010, "high": 19040, "low": 18990, "close": 19020, "volume": 3000},
            {"open": 19020, "high": 19045, "low": 18985, "close": 19010, "volume": 2000},
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        assert bo is None
    
    def test_breakout_requires_close_not_wick(self):
        """A wick above OR high without closing above is NOT a breakout."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19030, "high": 19070, "low": 19010, "close": 19040, "volume": 4000},  # High above but close inside
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        assert bo is None  # Close (19040) < OR high (19050)


# --- Retest Detection Tests ---

class TestRetestDetection:
    def test_long_retest(self):
        """After long breakout, pullback to OR high triggers retest."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19040, "high": 19080, "low": 19030, "close": 19070, "volume": 6000},  # Breakout
            {"open": 19070, "high": 19075, "low": 19045, "close": 19065, "volume": 4000},  # Retest: low touches near OR high
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        assert bo is not None
        
        rt = detect_retest(bars, or_, bo, tolerance_points=5, timeout_bars=4)
        assert rt is not None
        assert rt.direction == "long"
        assert rt.retest_level == 19050
    
    def test_retest_timeout(self):
        """No retest within timeout bars returns None."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19040, "high": 19080, "low": 19030, "close": 19070, "volume": 6000},
            {"open": 19070, "high": 19090, "low": 19065, "close": 19085, "volume": 4000},  # Keeps going up
            {"open": 19085, "high": 19100, "low": 19080, "close": 19095, "volume": 3000},
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        
        rt = detect_retest(bars, or_, bo, tolerance_points=5, timeout_bars=2)
        assert rt is None  # Price never came back to OR high


# --- Failed Breakout Tests ---

class TestFailedBreakout:
    def test_failed_long_breakout(self):
        """Price breaks above OR then closes back below — failed."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19040, "high": 19080, "low": 19030, "close": 19070, "volume": 6000},  # Breakout
            {"open": 19060, "high": 19065, "low": 19020, "close": 19030, "volume": 5000},  # Fails: closes below OR high
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        
        failed = detect_failed_breakout(bars, or_, bo, failure_bars=2)
        assert failed is True
    
    def test_successful_breakout_not_failed(self):
        """Price breaks and holds — not failed."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
            {"open": 19040, "high": 19080, "low": 19030, "close": 19070, "volume": 6000},
            {"open": 19070, "high": 19090, "low": 19060, "close": 19085, "volume": 4000},  # Holds above
        ])
        or_ = detect_opening_range(bars, 15)
        bo = detect_breakout(bars, or_, 15)
        
        failed = detect_failed_breakout(bars, or_, bo, failure_bars=2)
        assert failed is False


# --- Indicator Tests ---

class TestIndicators:
    def test_rsi_bounds(self):
        """RSI should always be between 0 and 100."""
        prices = pd.Series([100 + i * 0.5 for i in range(50)])  # Steady uptrend
        result = rsi(prices, 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()
    
    def test_rsi_all_gains(self):
        """RSI should be 100 when price only goes up (no losses)."""
        prices = pd.Series(range(100, 130))
        result = rsi(prices, 14)
        # After warmup, RSI should be close to 100
        assert result.iloc[-1] > 95
    
    def test_rsi_division_by_zero(self):
        """RSI should not produce NaN when avg_loss is zero."""
        prices = pd.Series(range(100, 120))  # Only up
        result = rsi(prices, 14)
        assert not result.iloc[-1] != result.iloc[-1]  # Not NaN
    
    def test_ema_follows_price(self):
        """EMA should follow the direction of price."""
        prices = pd.Series([100 + i for i in range(20)])
        result = ema(prices, 9)
        # EMA should be increasing
        assert result.iloc[-1] > result.iloc[-5]
    
    def test_volume_ratio(self):
        """Volume ratio of 2x average should return ~2.0."""
        volumes = pd.Series([1000] * 20 + [2000])
        result = volume_ratio(volumes, 20)
        assert abs(result.iloc[-1] - 2.0) < 0.1
    
    def test_check_confluences_long_passes(self):
        """Long entry with RSI < 70, positive EMA slope should pass."""
        bar = pd.Series({
            "rsi": 55,
            "volume_ratio": 2.0,
            "ema_9_slope": 1.5,
        })
        config = {
            "confluences": {
                "rsi": {"enabled": True, "period": 14, "overbought": 70, "oversold": 30},
                "volume": {"enabled": True, "breakout_multiplier": 1.5, "lookback_bars": 20},
                "ema_slope": {"enabled": True, "period": 9, "min_slope": 0.5},
            }
        }
        result = check_confluences(bar, "long", config)
        assert all(result.values())
    
    def test_check_confluences_long_blocked_by_rsi(self):
        """Long entry with RSI > 70 should be blocked."""
        bar = pd.Series({
            "rsi": 75,
            "volume_ratio": 2.0,
            "ema_9_slope": 1.5,
        })
        config = {
            "confluences": {
                "rsi": {"enabled": True, "period": 14, "overbought": 70, "oversold": 30},
                "volume": {"enabled": True, "breakout_multiplier": 1.5, "lookback_bars": 20},
                "ema_slope": {"enabled": True, "period": 9, "min_slope": 0.5},
            }
        }
        result = check_confluences(bar, "long", config)
        assert result["rsi"] is False


# --- Edge Case Tests ---

class TestEdgeCases:
    def test_single_bar_day(self):
        """Day with only 1 bar should not crash."""
        bars = make_day_bars([
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
        ])
        or_ = detect_opening_range(bars, 15)
        assert or_.size == 70
        # Breakout detection on single-bar day
        bo = detect_breakout(bars, or_, 15)
        assert bo is None  # No post-OR bars to break out
    
    def test_zero_size_or(self):
        """OR with high == low (doji) should have size 0."""
        bars = make_day_bars([
            {"open": 19000, "high": 19000, "low": 19000, "close": 19000, "volume": 100},
            {"open": 19000, "high": 19050, "low": 18980, "close": 19030, "volume": 5000},
        ])
        or_ = detect_opening_range(bars, 15)
        assert or_.size == 0
    
    def test_very_wide_or_classified_correctly(self):
        """500-point OR should be classified as wide."""
        bars = make_day_bars([
            {"open": 19000, "high": 19500, "low": 19000, "close": 19300, "volume": 10000},
            {"open": 19300, "high": 19350, "low": 19200, "close": 19250, "volume": 5000},
        ])
        lookback = [50, 60, 70, 55, 65, 80, 72, 58, 62, 75]
        or_ = detect_opening_range(bars, 15, lookback, wide_percentile=80)
        assert or_.classification == "wide"
