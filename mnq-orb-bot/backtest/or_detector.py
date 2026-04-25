"""
Opening Range detection and classification.

Identifies the Opening Range from the first N minutes of each trading day,
classifies it by size (normal, tight, wide), and provides the levels
that all three strategies reference.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class OpeningRange:
    """Opening Range for a single trading day."""
    date: str
    high: float
    low: float
    size: float              # high - low in points
    open_price: float        # First bar open
    close_price: float       # First bar close (OR candle close)
    midpoint: float          # (high + low) / 2
    direction: str           # "bullish" if close > open, "bearish" if close < open
    classification: str      # "tight", "normal", "wide"
    percentile: float        # Size percentile vs rolling lookback
    volume: float            # Volume during OR period


@dataclass
class BreakoutEvent:
    """Detected breakout of the Opening Range."""
    timestamp: pd.Timestamp
    direction: str           # "long" or "short"
    breakout_price: float    # Close of the breakout candle
    bar_index: int           # Which bar after OR lock this occurred
    candle_close: float
    candle_high: float
    candle_low: float
    volume: float


@dataclass
class RetestEvent:
    """Detected retest of the Opening Range level after breakout."""
    timestamp: pd.Timestamp
    direction: str           # Same as breakout direction
    retest_level: float      # The OR level being retested
    entry_price: float       # Suggested entry (retest level)
    stop_price: float        # Opposite side of OR
    risk_points: float       # Distance from entry to stop
    bar_index: int


def detect_opening_range(
    day_bars: pd.DataFrame,
    or_period_minutes: int = 15,
    lookback_ors: Optional[list[float]] = None,
    wide_percentile: float = 80,
) -> OpeningRange:
    """Detect and classify the Opening Range for a single day.
    
    Args:
        day_bars: 15-minute bars for a single trading day
        or_period_minutes: Minutes to define the opening range (15 = first candle)
        lookback_ors: List of recent OR sizes for percentile classification
        wide_percentile: Percentile threshold for "wide" classification
        
    Returns:
        OpeningRange dataclass with all computed properties
    """
    # Guard: live signal generator may call this with partial / empty data.
    if len(day_bars) == 0:
        raise ValueError("detect_opening_range: day_bars is empty")
    if or_period_minutes < 15 or or_period_minutes % 15 != 0:
        raise ValueError(
            f"detect_opening_range: or_period_minutes must be a positive multiple of 15, got {or_period_minutes}"
        )

    n_bars = or_period_minutes // 15
    if len(day_bars) < n_bars:
        raise ValueError(
            f"detect_opening_range: need at least {n_bars} bars for {or_period_minutes}-min OR, got {len(day_bars)}"
        )

    if or_period_minutes == 15:
        or_bar = day_bars.iloc[0]
        or_high = or_bar["high"]
        or_low = or_bar["low"]
        or_volume = or_bar["volume"]
    else:
        or_bars = day_bars.iloc[:n_bars]
        or_high = or_bars["high"].max()
        or_low = or_bars["low"].min()
        or_volume = or_bars["volume"].sum()

    size = or_high - or_low
    open_price = day_bars.iloc[0]["open"]
    close_price = day_bars.iloc[0]["close"] if or_period_minutes == 15 else day_bars.iloc[n_bars - 1]["close"]
    midpoint = (or_high + or_low) / 2

    # Direction: handle doji (close == open) as neutral
    if close_price > open_price:
        direction = "bullish"
    elif close_price < open_price:
        direction = "bearish"
    else:
        direction = "neutral"

    # Classification based on rolling percentile
    if lookback_ors and len(lookback_ors) >= 5:
        pct_rank = sum(1 for x in lookback_ors if x <= size) / len(lookback_ors) * 100
        
        if pct_rank <= 25:
            classification = "tight"
        elif pct_rank >= wide_percentile:
            classification = "wide"
        else:
            classification = "normal"
    else:
        pct_rank = 50.0
        classification = "normal"

    return OpeningRange(
        date=str(day_bars.index[0].date()),
        high=or_high,
        low=or_low,
        size=size,
        open_price=open_price,
        close_price=close_price,
        midpoint=midpoint,
        direction=direction,
        classification=classification,
        percentile=pct_rank,
        volume=or_volume,
    )


def detect_breakout(
    day_bars: pd.DataFrame,
    opening_range: OpeningRange,
    or_period_minutes: int = 15,
) -> Optional[BreakoutEvent]:
    """Detect the first breakout of the Opening Range.
    
    A breakout occurs when a 15m candle CLOSES above OR high (long)
    or CLOSES below OR low (short).
    
    Args:
        day_bars: Full day of 15m bars
        opening_range: Previously detected OR
        or_period_minutes: Skip bars within the OR period
        
    Returns:
        BreakoutEvent if breakout detected, None otherwise
    """
    start_bar = or_period_minutes // 15  # Skip OR bars
    
    for i in range(start_bar, len(day_bars)):
        bar = day_bars.iloc[i]
        
        # Long breakout: candle CLOSES above OR high
        if bar["close"] > opening_range.high:
            return BreakoutEvent(
                timestamp=day_bars.index[i],
                direction="long",
                breakout_price=bar["close"],
                bar_index=i,
                candle_close=bar["close"],
                candle_high=bar["high"],
                candle_low=bar["low"],
                volume=bar["volume"],
            )
        
        # Short breakout: candle CLOSES below OR low
        if bar["close"] < opening_range.low:
            return BreakoutEvent(
                timestamp=day_bars.index[i],
                direction="short",
                breakout_price=bar["close"],
                bar_index=i,
                candle_close=bar["close"],
                candle_high=bar["high"],
                candle_low=bar["low"],
                volume=bar["volume"],
            )
    
    return None


def detect_retest(
    day_bars: pd.DataFrame,
    opening_range: OpeningRange,
    breakout: BreakoutEvent,
    tolerance_points: float = 5.0,
    timeout_bars: int = 8,
) -> Optional[RetestEvent]:
    """Detect a retest of the broken OR level after breakout.
    
    For a long breakout, we wait for price to pull back to OR high.
    For a short breakout, we wait for price to pull back to OR low.
    
    Args:
        day_bars: Full day of 15m bars
        opening_range: The Opening Range
        breakout: The detected breakout event
        tolerance_points: How close to OR level counts as a retest
        timeout_bars: Max bars to wait for retest after breakout
        
    Returns:
        RetestEvent if retest detected, None otherwise
    """
    start_bar = breakout.bar_index + 1
    end_bar = min(start_bar + timeout_bars, len(day_bars))
    
    if breakout.direction == "long":
        retest_level = opening_range.high
        stop_level = opening_range.low
        
        for i in range(start_bar, end_bar):
            bar = day_bars.iloc[i]
            
            # Price pulls back to OR high (within tolerance)
            # Bar low touches or goes through the retest level
            if bar["low"] <= retest_level + tolerance_points:
                # Confirmation: bar closes ABOVE the retest level (holds as support)
                if bar["close"] > retest_level:
                    return RetestEvent(
                        timestamp=day_bars.index[i],
                        direction="long",
                        retest_level=retest_level,
                        entry_price=retest_level,
                        stop_price=stop_level,
                        risk_points=retest_level - stop_level,
                        bar_index=i,
                    )
    
    elif breakout.direction == "short":
        retest_level = opening_range.low
        stop_level = opening_range.high
        
        for i in range(start_bar, end_bar):
            bar = day_bars.iloc[i]
            
            # Price pulls back to OR low (within tolerance)
            if bar["high"] >= retest_level - tolerance_points:
                # Confirmation: bar closes BELOW the retest level (holds as resistance)
                if bar["close"] < retest_level:
                    return RetestEvent(
                        timestamp=day_bars.index[i],
                        direction="short",
                        retest_level=retest_level,
                        entry_price=retest_level,
                        stop_price=stop_level,
                        risk_points=stop_level - retest_level,
                        bar_index=i,
                    )
    
    return None


def detect_failed_breakout(
    day_bars: pd.DataFrame,
    opening_range: OpeningRange,
    breakout: BreakoutEvent,
    failure_bars: int = 3,
) -> bool:
    """Detect if a breakout has failed (for Inverse ORB setup).
    
    A failed breakout occurs when price breaks the OR, then the next N bars
    close back inside the OR.
    
    Args:
        day_bars: Full day of 15m bars
        opening_range: The Opening Range
        breakout: The detected breakout event
        failure_bars: How many bars after breakout to check for failure
        
    Returns:
        True if breakout appears to have failed
    """
    start_bar = breakout.bar_index + 1
    end_bar = min(start_bar + failure_bars, len(day_bars))
    
    for i in range(start_bar, end_bar):
        bar = day_bars.iloc[i]
        
        if breakout.direction == "long":
            # Failed long breakout: price closes back below OR high
            if bar["close"] < opening_range.high:
                return True
        elif breakout.direction == "short":
            # Failed short breakout: price closes back above OR low
            if bar["close"] > opening_range.low:
                return True
    
    return False
