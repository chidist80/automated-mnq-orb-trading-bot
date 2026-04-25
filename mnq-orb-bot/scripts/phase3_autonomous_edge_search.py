"""
Phase 3 — autonomous edge search.

This is the first pass at turning the human replay signal into executable bot
rules. It searches causal, next-bar-entry rules on every clean RTH day:

- delayed morning fades after extreme VWAP/15m EMA location
- causal OR breakout/retest and inverse ORB variants
- 15m EMA pullback continuation variants

No production code is changed. Outputs live under
research/human_edge_replay/phase3_autonomous/.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from human_edge_replay import EASTERN, build_daily_contexts, load_1m_data  # type: ignore

OUT_DIR = PROJECT_ROOT / "research" / "human_edge_replay" / "phase3_autonomous"
MNQ_MULTIPLIER = 2.0
COMMISSION_RT = 1.34
DEFAULT_ROUND_TRIP_SLIPPAGE_POINTS = 1.0


@dataclass(frozen=True)
class RuleConfig:
    rule_id: str
    family: str
    direction: str
    params: dict[str, Any]


def timestamp_on_day(day: pd.DataFrame, hhmm: str) -> pd.Timestamp:
    hour, minute = [int(x) for x in hhmm.split(":")]
    ts = day.index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute)
    return ts.tz_localize(EASTERN) if ts.tz is None else ts


def points_to_dollars(points: float) -> float:
    return points * MNQ_MULTIPLIER


def apply_slippage(price: float, direction: str, side: str, round_trip_points: float) -> float:
    half = round_trip_points / 2.0
    if side == "entry":
        return price + half if direction == "long" else price - half
    return price - half if direction == "long" else price + half


def net_pnl(entry: float, exit_: float, direction: str) -> float:
    gross_points = exit_ - entry if direction == "long" else entry - exit_
    return points_to_dollars(gross_points) - COMMISSION_RT


def add_day_features(day: pd.DataFrame, bars_15m: pd.DataFrame, ctx: dict[str, Any]) -> pd.DataFrame:
    df = day.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    volume_sum = df["volume"].cumsum().replace(0, np.nan)
    df["vwap"] = (typical * df["volume"]).cumsum() / volume_sum
    df["prev_close"] = df["close"].shift(1)
    df["prev_low"] = df["low"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev2_close"] = df["close"].shift(2)
    df["range"] = df["high"] - df["low"]
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["or_high"] = ctx["or_high"]
    df["or_low"] = ctx["or_low"]
    df["or_mid"] = ctx["or_midpoint"]
    df["or_size"] = ctx["or_size"]

    if bars_15m is not None and not bars_15m.empty:
        right = bars_15m[["ema9", "ema9_slope", "rsi14", "volume_ratio20"]].copy()
        right = right.reset_index()
        right = right.rename(columns={right.columns[0]: "bar_start"})
        right["available_at"] = right["bar_start"] + pd.Timedelta(minutes=15)
        left = df.reset_index()
        left = left.rename(columns={left.columns[0]: "ts"})
        merged = pd.merge_asof(
            left.sort_values("ts"),
            right.sort_values("available_at"),
            left_on="ts",
            right_on="available_at",
            direction="backward",
        ).set_index("ts")
        for col in ["ema9", "ema9_slope", "rsi14", "volume_ratio20"]:
            df[col] = merged[col]
    else:
        for col in ["ema9", "ema9_slope", "rsi14", "volume_ratio20"]:
            df[col] = np.nan
    return df


def in_time_window(df: pd.DataFrame, start: str, end: str) -> pd.Series:
    start_time = pd.Timestamp(start).time()
    end_time = pd.Timestamp(end).time()
    return (df.index.time >= start_time) & (df.index.time <= end_time)


def reversal_mask(df: pd.DataFrame, direction: str, pattern: str) -> pd.Series:
    if direction == "short":
        if pattern == "red_close":
            return (df["close"] < df["open"]) & (df["close"] < df["prev_close"])
        if pattern == "close_prev_low":
            return df["close"] < df["prev_low"]
        if pattern == "upper_wick":
            return (df["upper_wick"] >= 0.35 * df["range"]) & (df["close"] < df["open"])
        if pattern == "two_lower_closes":
            return (df["close"] < df["prev_close"]) & (df["prev_close"] < df["prev2_close"])
    else:
        if pattern == "green_close":
            return (df["close"] > df["open"]) & (df["close"] > df["prev_close"])
        if pattern == "close_prev_high":
            return df["close"] > df["prev_high"]
        if pattern == "lower_wick":
            return (df["lower_wick"] >= 0.35 * df["range"]) & (df["close"] > df["open"])
        if pattern == "two_higher_closes":
            return (df["close"] > df["prev_close"]) & (df["prev_close"] > df["prev2_close"])
    raise ValueError(f"Unknown pattern {pattern!r} for {direction}")


def entry_next_open(df: pd.DataFrame, signal_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    eligible = df[df.index > signal_ts]
    if eligible.empty:
        return None
    bar = eligible.iloc[0]
    return bar.name, float(bar["open"])


def simulate_exit(
    df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    direction: str,
    stop_price: float,
    target_price: float,
    time_exit: str,
) -> tuple[pd.Timestamp, float, str] | None:
    if direction == "long" and not (stop_price < entry_price < target_price):
        return None
    if direction == "short" and not (target_price < entry_price < stop_price):
        return None

    end_ts = timestamp_on_day(df, time_exit)
    sub = df[(df.index >= entry_ts) & (df.index <= end_ts)]
    if sub.empty:
        return None

    for ts, bar in sub.iterrows():
        if direction == "long":
            stop_hit = bar["low"] <= stop_price
            target_hit = bar["high"] >= target_price
        else:
            stop_hit = bar["high"] >= stop_price
            target_hit = bar["low"] <= target_price

        # Conservative ambiguity handling: stop wins when both print in a minute.
        if stop_hit:
            return ts, stop_price, "stop"
        if target_hit:
            return ts, target_price, "target"

    last = sub.iloc[-1]
    return last.name, float(last["close"]), "time_exit"


def stop_and_target(
    signal_bar: pd.Series,
    entry_price: float,
    direction: str,
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> tuple[float, float] | None:
    stop_mode = params["stop_mode"]
    target_mode = params["target_mode"]
    buffer_points = params.get("buffer_points", 0.0)

    if stop_mode == "signal_extreme":
        if direction == "long":
            stop = float(signal_bar["low"] - buffer_points)
        else:
            stop = float(signal_bar["high"] + buffer_points)
    elif stop_mode == "fixed":
        stop_points = params["stop_points"]
        stop = entry_price - stop_points if direction == "long" else entry_price + stop_points
    elif stop_mode == "or_fraction":
        stop_points = max(10.0, min(params["or_stop_fraction"] * ctx["or_size"], params["max_stop"]))
        stop = entry_price - stop_points if direction == "long" else entry_price + stop_points
    else:
        raise ValueError(stop_mode)

    risk = entry_price - stop if direction == "long" else stop - entry_price
    if not np.isfinite(risk) or risk <= 0 or risk > params.get("max_risk_points", 120.0):
        return None

    if target_mode == "rr":
        reward = params["rr"] * risk
        target = entry_price + reward if direction == "long" else entry_price - reward
    elif target_mode == "fixed":
        target_points = params["target_points"]
        target = entry_price + target_points if direction == "long" else entry_price - target_points
    elif target_mode == "vwap":
        target = float(signal_bar["vwap"])
    elif target_mode == "or_mid":
        target = float(ctx["or_midpoint"])
    else:
        raise ValueError(target_mode)

    reward = target - entry_price if direction == "long" else entry_price - target
    if not np.isfinite(reward) or reward < params.get("min_reward_points", 8.0):
        return None
    return stop, target


def detect_fade_extreme(df: pd.DataFrame, ctx: dict[str, Any], config: RuleConfig) -> dict | None:
    p = config.params
    direction = config.direction
    mask = in_time_window(df, p["start"], p["end"])
    mask &= df["ema9"].notna() & df["vwap"].notna()

    if direction == "short":
        mask &= df["close"] >= df["vwap"] + p["vwap_ext"]
        mask &= df["close"] >= df["ema9"] + p["ema_ext"]
        if p["or_location"] == "beyond_or":
            mask &= df["close"] >= ctx["or_high"] + p["or_ext"]
        if p["rsi_mode"] == "not_bear":
            mask &= df["rsi14"] > 35
        elif p["rsi_mode"] == "bull_or_neutral":
            mask &= df["rsi14"] >= 50
    else:
        mask &= df["close"] <= df["vwap"] - p["vwap_ext"]
        mask &= df["close"] <= df["ema9"] - p["ema_ext"]
        if p["or_location"] == "beyond_or":
            mask &= df["close"] <= ctx["or_low"] - p["or_ext"]
        if p["rsi_mode"] == "not_bull":
            mask &= df["rsi14"] < 65
        elif p["rsi_mode"] == "bear_or_neutral":
            mask &= df["rsi14"] <= 50

    mask &= reversal_mask(df, direction, p["pattern"])
    candidates = df[mask]
    if candidates.empty:
        return None

    signal = candidates.iloc[0]
    entry = entry_next_open(df, signal.name)
    if entry is None:
        return None
    entry_ts, raw_entry = entry
    entry_price = apply_slippage(raw_entry, direction, "entry", p["slippage_rt"])
    levels = stop_and_target(signal, entry_price, direction, ctx, p)
    if levels is None:
        return None
    stop, target = levels
    exit_result = simulate_exit(df, entry_ts, entry_price, direction, stop, target, p["time_exit"])
    if exit_result is None:
        return None
    exit_ts, raw_exit, reason = exit_result
    exit_price = apply_slippage(raw_exit, direction, "exit", p["slippage_rt"])
    return trade_row(config, ctx, signal.name, entry_ts, entry_price, exit_ts, exit_price, reason)


def first_or_breakout(df: pd.DataFrame, ctx: dict[str, Any], direction: str) -> pd.Timestamp | None:
    start = timestamp_on_day(df, "09:45")
    sub = df[df.index >= start]
    if direction == "long":
        hits = sub[sub["close"] > ctx["or_high"]]
    else:
        hits = sub[sub["close"] < ctx["or_low"]]
    return None if hits.empty else hits.index[0]


def detect_or_retest(df: pd.DataFrame, ctx: dict[str, Any], config: RuleConfig) -> dict | None:
    p = config.params
    direction = config.direction
    or_class = p.get("or_class", "all")
    if or_class == "not_wide" and ctx["or_classification"] == "wide":
        return None
    if or_class == "normal_tight" and ctx["or_classification"] not in {"normal", "tight"}:
        return None
    if or_class not in {"all", "not_wide", "normal_tight"} and ctx["or_classification"] != or_class:
        return None
    breakout_ts = first_or_breakout(df, ctx, direction)
    if breakout_ts is None:
        return None
    level = ctx["or_high"] if direction == "long" else ctx["or_low"]
    after = df[(df.index > breakout_ts) & in_time_window(df, p["start"], p["end"])]
    if direction == "long":
        mask = (after["low"] <= level + p["touch_tolerance"]) & (after["close"] > level)
        pattern = "green_close" if p["confirm_pattern"] == "simple" else "close_prev_high"
    else:
        mask = (after["high"] >= level - p["touch_tolerance"]) & (after["close"] < level)
        pattern = "red_close" if p["confirm_pattern"] == "simple" else "close_prev_low"
    mask &= reversal_mask(after, direction, pattern)
    candidates = after[mask]
    if candidates.empty:
        return None
    for _ts, signal in candidates.iterrows():
        if signal_passes_optional_filters(signal, ctx, p):
            return enter_from_signal(df, ctx, config, signal)
    return None


def signal_passes_optional_filters(
    signal: pd.Series,
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> bool:
    """Apply causal filters used by later research passes."""

    def finite_value(name: str) -> float:
        value = signal.get(name, np.nan)
        return float(value) if np.isfinite(value) else np.nan

    signal_minute = params.get("min_signal_minute")
    if signal_minute is not None:
        current_minute = signal.name.hour * 60 + signal.name.minute
        if current_minute < signal_minute:
            return False

    min_or_close_pos = params.get("min_or_close_pos")
    if min_or_close_pos is not None:
        or_size = float(ctx.get("or_size", np.nan))
        or_low = float(ctx.get("or_low", np.nan))
        if not np.isfinite(or_size) or or_size <= 0:
            return False
        or_close_pos = (float(ctx.get("or_close", np.nan)) - or_low) / or_size
        if not np.isfinite(or_close_pos) or or_close_pos < min_or_close_pos:
            return False

    max_volume_ratio = params.get("max_signal_volume_ratio")
    if max_volume_ratio is not None:
        volume_ratio = finite_value("volume_ratio20")
        if not np.isfinite(volume_ratio) or volume_ratio > max_volume_ratio:
            return False

    min_rsi = params.get("min_signal_rsi")
    if min_rsi is not None:
        rsi_value = finite_value("rsi14")
        if not np.isfinite(rsi_value) or rsi_value < min_rsi:
            return False

    max_rsi = params.get("max_signal_rsi")
    if max_rsi is not None:
        rsi_value = finite_value("rsi14")
        if not np.isfinite(rsi_value) or rsi_value > max_rsi:
            return False

    max_ema_slope = params.get("max_signal_ema_slope")
    if max_ema_slope is not None:
        ema_slope = finite_value("ema9_slope")
        if not np.isfinite(ema_slope) or ema_slope > max_ema_slope:
            return False

    min_ema_slope = params.get("min_signal_ema_slope")
    if min_ema_slope is not None:
        ema_slope = finite_value("ema9_slope")
        if not np.isfinite(ema_slope) or ema_slope < min_ema_slope:
            return False

    max_close_vwap_delta = params.get("max_close_vwap_delta")
    if max_close_vwap_delta is not None:
        vwap_delta = float(signal["close"] - signal["vwap"])
        if not np.isfinite(vwap_delta) or vwap_delta > max_close_vwap_delta:
            return False

    max_close_ema_delta = params.get("max_close_ema_delta")
    if max_close_ema_delta is not None:
        ema_delta = float(signal["close"] - signal["ema9"])
        if not np.isfinite(ema_delta) or ema_delta > max_close_ema_delta:
            return False

    signal_vs_ema = params.get("signal_vs_ema")
    if signal_vs_ema == "above" and not signal["close"] >= signal["ema9"]:
        return False
    if signal_vs_ema == "below" and not signal["close"] < signal["ema9"]:
        return False

    prior_day_zombie = params.get("prior_day_zombie")
    if prior_day_zombie == "yes" and not ctx.get("prior_day_zombie_proxy", False):
        return False
    if prior_day_zombie == "no" and ctx.get("prior_day_zombie_proxy", False):
        return False

    prior_day_direction = params.get("prior_day_direction")
    if prior_day_direction not in (None, "all"):
        if ctx.get("prior_day_trend_direction") != prior_day_direction:
            return False

    return True


def detect_inverse_orb(df: pd.DataFrame, ctx: dict[str, Any], config: RuleConfig) -> dict | None:
    p = config.params
    if p["or_class"] != "all" and ctx["or_classification"] != p["or_class"]:
        return None
    direction = config.direction
    breakout_dir = "short" if direction == "long" else "long"
    breakout_ts = first_or_breakout(df, ctx, breakout_dir)
    if breakout_ts is None:
        return None
    after = df[(df.index > breakout_ts) & in_time_window(df, p["start"], p["end"])]
    inside = (after["close"] >= ctx["or_low"]) & (after["close"] <= ctx["or_high"])
    pattern = "green_close" if direction == "long" else "red_close"
    candidates = after[inside & reversal_mask(after, direction, pattern)]
    if candidates.empty:
        return None
    return enter_from_signal(df, ctx, config, candidates.iloc[0])


def detect_ema_pullback(df: pd.DataFrame, ctx: dict[str, Any], config: RuleConfig) -> dict | None:
    p = config.params
    direction = config.direction
    mask = in_time_window(df, p["start"], p["end"]) & df["ema9"].notna()
    if direction == "long":
        mask &= df["ema9_slope"] >= p["min_slope"]
        mask &= df["rsi14"] >= p["rsi_threshold"]
        mask &= (df["low"] <= df["ema9"] + p["touch_tolerance"]) & (df["close"] >= df["ema9"])
        pattern = "green_close" if p["confirm_pattern"] == "simple" else "close_prev_high"
    else:
        mask &= df["ema9_slope"] <= -p["min_slope"]
        mask &= df["rsi14"] <= 100 - p["rsi_threshold"]
        mask &= (df["high"] >= df["ema9"] - p["touch_tolerance"]) & (df["close"] <= df["ema9"])
        pattern = "red_close" if p["confirm_pattern"] == "simple" else "close_prev_low"
    mask &= reversal_mask(df, direction, pattern)
    candidates = df[mask]
    if candidates.empty:
        return None
    return enter_from_signal(df, ctx, config, candidates.iloc[0])


def enter_from_signal(
    df: pd.DataFrame,
    ctx: dict[str, Any],
    config: RuleConfig,
    signal: pd.Series,
) -> dict | None:
    direction = config.direction
    p = config.params
    entry = entry_next_open(df, signal.name)
    if entry is None:
        return None
    entry_ts, raw_entry = entry
    entry_price = apply_slippage(raw_entry, direction, "entry", p["slippage_rt"])
    levels = stop_and_target(signal, entry_price, direction, ctx, p)
    if levels is None:
        return None
    stop, target = levels
    exit_result = simulate_exit(df, entry_ts, entry_price, direction, stop, target, p["time_exit"])
    if exit_result is None:
        return None
    exit_ts, raw_exit, reason = exit_result
    exit_price = apply_slippage(raw_exit, direction, "exit", p["slippage_rt"])
    return trade_row(config, ctx, signal.name, entry_ts, entry_price, exit_ts, exit_price, reason)


def trade_row(
    config: RuleConfig,
    ctx: dict[str, Any],
    signal_ts: pd.Timestamp,
    entry_ts: pd.Timestamp,
    entry_price: float,
    exit_ts: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    pnl = net_pnl(entry_price, exit_price, config.direction)
    return {
        "rule_id": config.rule_id,
        "family": config.family,
        "direction": config.direction,
        "date": ctx["date"],
        "or_class": ctx["or_classification"],
        "or_size": ctx["or_size"],
        "signal_ts": signal_ts,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "net_pnl": pnl,
    }


def build_rule_configs(slippage_rt: float = DEFAULT_ROUND_TRIP_SLIPPAGE_POINTS) -> list[RuleConfig]:
    configs: list[RuleConfig] = []

    fade_patterns = {
        "short": ["red_close", "close_prev_low", "upper_wick", "two_lower_closes"],
        "long": ["green_close", "close_prev_high", "lower_wick", "two_higher_closes"],
    }
    for direction in ["short", "long"]:
        extension_pairs = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)]
        rsi_modes = ["not_bear"] if direction == "short" else ["not_bull"]
        for values in itertools.product(
            [("09:45", "11:00"), ("10:00", "11:00")],
            extension_pairs,
            ["none", "beyond_or"],
            fade_patterns[direction][:2],
            rsi_modes,
            ["signal_extreme", "fixed"],
            ["rr", "vwap", "or_mid"],
        ):
            window, ext_pair, or_location, pattern, rsi_mode, stop_mode, target_mode = values
            start, end = window
            vwap_ext, ema_ext = ext_pair
            if start >= end:
                continue
            for stop_points in ([30.0] if stop_mode == "fixed" else [np.nan]):
                for buffer_points in ([8.0] if stop_mode == "signal_extreme" else [0.0]):
                    for rr in ([1.5] if target_mode == "rr" else [np.nan]):
                        params = {
                            "start": start,
                            "end": end,
                            "vwap_ext": vwap_ext,
                            "ema_ext": ema_ext,
                            "or_location": or_location,
                            "or_ext": 0.0,
                            "pattern": pattern,
                            "rsi_mode": rsi_mode,
                            "stop_mode": stop_mode,
                            "stop_points": stop_points,
                            "buffer_points": buffer_points,
                            "target_mode": target_mode,
                            "rr": rr,
                            "target_points": np.nan,
                            "time_exit": "12:00",
                            "min_reward_points": 8.0,
                            "max_risk_points": 120.0,
                            "slippage_rt": slippage_rt,
                        }
                        rule_id = compact_rule_id("fade", direction, params)
                        configs.append(RuleConfig(rule_id, "fade_extreme", direction, params))

    for family in ["or_retest", "inverse_orb"]:
        for direction in ["long", "short"]:
            for values in itertools.product(
                ["09:45"],
                ["11:00", "12:00"],
                [5.0],
                ["simple", "break_prev"],
                ["fixed", "or_fraction"],
                ["rr", "fixed"],
            ):
                start, end, touch_tolerance, confirm_pattern, stop_mode, target_mode = values
                for stop_points in ([30.0] if stop_mode == "fixed" else [np.nan]):
                    for or_stop_fraction in ([0.25] if stop_mode == "or_fraction" else [np.nan]):
                        for rr in ([1.5] if target_mode == "rr" else [np.nan]):
                            for target_points in ([30.0] if target_mode == "fixed" else [np.nan]):
                                for or_class in (["all", "tight", "wide"] if family == "inverse_orb" else ["all"]):
                                    params = {
                                        "start": start,
                                        "end": end,
                                        "touch_tolerance": touch_tolerance,
                                        "confirm_pattern": confirm_pattern,
                                        "stop_mode": stop_mode,
                                        "stop_points": stop_points,
                                        "or_stop_fraction": or_stop_fraction,
                                        "max_stop": 80.0,
                                        "buffer_points": 0.0,
                                        "target_mode": target_mode,
                                        "rr": rr,
                                        "target_points": target_points,
                                        "or_class": or_class,
                                        "time_exit": "12:00",
                                        "min_reward_points": 8.0,
                                        "max_risk_points": 120.0,
                                        "slippage_rt": slippage_rt,
                                    }
                                    rule_id = compact_rule_id(family, direction, params)
                                    configs.append(RuleConfig(rule_id, family, direction, params))

    for direction in ["long", "short"]:
        for values in itertools.product(
            ["09:45"],
            ["11:00", "12:00"],
            [5.0],
            [0.0, 2.0],
            [50.0],
            ["simple", "break_prev"],
            ["fixed", "signal_extreme"],
            ["rr", "fixed"],
        ):
            start, end, touch_tolerance, min_slope, rsi_threshold, confirm, stop_mode, target_mode = values
            for stop_points in ([30.0] if stop_mode == "fixed" else [np.nan]):
                for buffer_points in ([8.0] if stop_mode == "signal_extreme" else [0.0]):
                    for rr in ([1.5] if target_mode == "rr" else [np.nan]):
                        for target_points in ([30.0] if target_mode == "fixed" else [np.nan]):
                            params = {
                                "start": start,
                                "end": end,
                                "touch_tolerance": touch_tolerance,
                                "min_slope": min_slope,
                                "rsi_threshold": rsi_threshold,
                                "confirm_pattern": confirm,
                                "stop_mode": stop_mode,
                                "stop_points": stop_points,
                                "buffer_points": buffer_points,
                                "target_mode": target_mode,
                                "rr": rr,
                                "target_points": target_points,
                                "time_exit": "12:00",
                                "min_reward_points": 8.0,
                                "max_risk_points": 120.0,
                                "slippage_rt": slippage_rt,
                            }
                            rule_id = compact_rule_id("ema_pullback", direction, params)
                            configs.append(RuleConfig(rule_id, "ema_pullback", direction, params))

    return configs


def compact_rule_id(prefix: str, direction: str, params: dict[str, Any]) -> str:
    parts = [prefix, direction]
    for key in sorted(params):
        if key == "slippage_rt":
            continue
        value = params[key]
        if isinstance(value, float):
            if np.isnan(value):
                continue
            value = f"{value:g}"
        parts.append(f"{key}={value}")
    return "|".join(parts)


def run_search(
    configs: list[RuleConfig],
    scope: str,
    rules_tried_for_adjustment: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    one_minute = load_1m_data(PROJECT_ROOT / "data" / "mnq_1m.parquet")
    contexts = build_daily_contexts(one_minute)

    if scope == "human_window":
        ideas = pd.read_csv(PROJECT_ROOT / "research" / "human_edge_replay" / "reports" / "idea_replay.csv")
        dates = pd.to_datetime(ideas["trade_date_et"]).dt.date
        start_date, end_date = dates.min(), dates.max()
    elif scope == "full_history":
        start_date = pd.Timestamp("1900-01-01").date()
        end_date = pd.Timestamp("2100-01-01").date()
    else:
        raise ValueError(scope)

    prepared_days = []
    for date_key, ctx in contexts.items():
        date_obj = pd.to_datetime(date_key).date()
        if date_obj < start_date or date_obj > end_date or not ctx.get("clean", False):
            continue
        features = add_day_features(ctx["day"], ctx.get("bars_15m"), ctx)
        prepared_days.append((date_key, ctx, features))

    trades: list[dict[str, Any]] = []
    detectors = {
        "fade_extreme": detect_fade_extreme,
        "or_retest": detect_or_retest,
        "inverse_orb": detect_inverse_orb,
        "ema_pullback": detect_ema_pullback,
    }
    for i, config in enumerate(configs, start=1):
        if i == 1 or i % 500 == 0 or i == len(configs):
            print(f"{scope}: {i:,}/{len(configs):,} configs", flush=True)
        detector = detectors[config.family]
        for _date_key, ctx, features in prepared_days:
            result = detector(features, ctx, config)
            if result is not None:
                result["scope"] = scope
                trades.append(result)

    trades_df = pd.DataFrame(trades)
    summary = summarize_trades(
        trades_df,
        rules_tried_for_adjustment or len(configs),
        scope,
    )
    return trades_df, summary


def select_confirmation_configs(
    configs: list[RuleConfig],
    human_summary: pd.DataFrame,
    max_configs: int = 300,
) -> list[RuleConfig]:
    if human_summary.empty:
        return []
    candidates = human_summary.copy()
    candidates = candidates[
        (candidates["trades"] >= 20)
        & (candidates["total_pnl"] > 0)
        & (candidates["oos_pnl"] > 0)
    ].copy()
    if candidates.empty:
        candidates = human_summary[
            (human_summary["trades"] >= 20) & (human_summary["total_pnl"] > 0)
        ].copy()
    if candidates.empty:
        return []

    chosen_ids: list[str] = []
    for _family, group in candidates.groupby("family"):
        chosen_ids.extend(group.sort_values("total_pnl", ascending=False).head(50)["rule_id"])
    chosen_ids.extend(candidates.sort_values("total_pnl", ascending=False).head(max_configs)["rule_id"])

    ordered_unique = list(dict.fromkeys(chosen_ids))[:max_configs]
    by_id = {config.rule_id: config for config in configs}
    return [by_id[rule_id] for rule_id in ordered_unique if rule_id in by_id]


def summarize_trades(trades: pd.DataFrame, rules_tried: int, scope: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for rule_id, group in trades.groupby("rule_id"):
        group = group.sort_values("entry_ts").copy()
        dates = sorted(pd.to_datetime(group["date"]).dt.date.unique())
        cutoff = dates[int(len(dates) * 0.6) - 1] if dates else None
        is_part = group[pd.to_datetime(group["date"]).dt.date <= cutoff]
        oos_part = group[pd.to_datetime(group["date"]).dt.date > cutoff]
        pnl = group["net_pnl"]
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))
        std = float(pnl.std(ddof=1)) if len(pnl) > 1 else np.nan
        sharpe_trade = float(pnl.mean() / std) if std and np.isfinite(std) and std > 0 else np.nan
        adjusted_t = (
            sharpe_trade * np.sqrt(len(pnl)) / np.sqrt(rules_tried)
            if np.isfinite(sharpe_trade)
            else np.nan
        )
        max_day = group.groupby("date")["net_pnl"].sum().max()
        total_pnl = float(pnl.sum())
        row = {
            "scope": scope,
            "rule_id": rule_id,
            "family": group["family"].iloc[0],
            "direction": group["direction"].iloc[0],
            "trades": len(group),
            "total_pnl": total_pnl,
            "avg_pnl": float(pnl.mean()),
            "median_pnl": float(pnl.median()),
            "win_rate": float((pnl > 0).mean()),
            "profit_factor": gross_profit / gross_loss if gross_loss else np.inf,
            "max_drawdown": max_drawdown(group),
            "sharpe_trade": sharpe_trade,
            "adjusted_t": adjusted_t,
            "is_trades": len(is_part),
            "is_pnl": float(is_part["net_pnl"].sum()) if len(is_part) else 0.0,
            "is_avg": float(is_part["net_pnl"].mean()) if len(is_part) else np.nan,
            "is_win_rate": float((is_part["net_pnl"] > 0).mean()) if len(is_part) else np.nan,
            "oos_trades": len(oos_part),
            "oos_pnl": float(oos_part["net_pnl"].sum()) if len(oos_part) else 0.0,
            "oos_avg": float(oos_part["net_pnl"].mean()) if len(oos_part) else np.nan,
            "oos_win_rate": float((oos_part["net_pnl"] > 0).mean()) if len(oos_part) else np.nan,
            "largest_day_pnl": float(max_day),
            "largest_day_concentration": float(max_day / total_pnl) if total_pnl > 0 else np.nan,
            "rules_tried": rules_tried,
        }
        row["passes_n80"] = row["trades"] >= 80
        row["passes_oos"] = (
            np.isfinite(row["is_avg"])
            and np.isfinite(row["oos_avg"])
            and row["is_avg"] > 0
            and row["oos_avg"] >= 0.6 * row["is_avg"]
            and abs((row["oos_win_rate"] - row["is_win_rate"]) * 100) <= 10
        )
        row["passes_concentration"] = (
            np.isfinite(row["largest_day_concentration"])
            and row["largest_day_concentration"] <= 0.25
        )
        row["passes_pf"] = row["profit_factor"] >= 1.5
        row["passes_adjusted_t"] = row["adjusted_t"] >= 1.0
        row["passes_all_core"] = (
            row["passes_n80"]
            and row["passes_oos"]
            and row["passes_concentration"]
            and row["passes_pf"]
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["passes_all_core", "total_pnl", "profit_factor"],
        ascending=[False, False, False],
    )


def max_drawdown(group: pd.DataFrame) -> float:
    equity = group.sort_values("exit_ts")["net_pnl"].cumsum()
    peak = equity.cummax()
    return float((equity - peak).min()) if len(equity) else 0.0


def write_report(human_summary: pd.DataFrame, full_summary: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 3 Autonomous Edge Search",
        "",
        "## Method",
        "",
        "- Uses local 1-minute MNQ RTH bars only.",
        "- Uses only completed-bar information.",
        "- Enters on the next 1-minute open after a signal.",
        "- Applies 1.0 point round-trip slippage and $1.34 commission.",
        "- Tests delayed fades, causal OR retests/inverse ORB, and EMA pullbacks.",
        "- Scores both the human-account window and the full clean local 1-minute history.",
        "",
        "## Top Human-Window Candidates",
        "",
        top_table(human_summary),
        "",
        "## Top Full-History Candidates",
        "",
        top_table(full_summary),
        "",
        "## Decision",
        "",
        decision_text(human_summary, full_summary),
        "",
    ]
    (OUT_DIR / "autonomous_edge_search.md").write_text("\n".join(lines))


def top_table(summary: pd.DataFrame) -> str:
    if summary.empty:
        return "No trades."
    cols = [
        "family",
        "direction",
        "trades",
        "total_pnl",
        "avg_pnl",
        "win_rate",
        "profit_factor",
        "max_drawdown",
        "is_avg",
        "oos_avg",
        "largest_day_concentration",
        "passes_all_core",
        "rule_id",
    ]
    return summary[cols].head(15).to_markdown(index=False, floatfmt=".2f")


def decision_text(human_summary: pd.DataFrame, full_summary: pd.DataFrame) -> str:
    human_pass = human_summary[human_summary["passes_all_core"]] if not human_summary.empty else pd.DataFrame()
    full_pass = full_summary[full_summary["passes_all_core"]] if not full_summary.empty else pd.DataFrame()
    if not human_pass.empty and not full_pass.empty:
        return (
            "At least one rule clears the core gates in both scopes. Treat it as a "
            "candidate for slippage sensitivity, Monte Carlo, and code review."
        )
    if not human_pass.empty:
        return (
            "Some rules clear the human-window gates but fail full-history confirmation. "
            "Do not promote yet; investigate whether the edge is regime-specific."
        )
    return (
        "No autonomous candidate clears the core gate set. The human signal remains "
        "useful as a map, but the tested triggers are not yet a deployable edge."
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configs = build_rule_configs(DEFAULT_ROUND_TRIP_SLIPPAGE_POINTS)
    print(f"Testing {len(configs):,} rule configs")

    human_trades, human_summary = run_search(
        configs,
        "human_window",
        rules_tried_for_adjustment=len(configs),
    )
    confirmation_configs = select_confirmation_configs(configs, human_summary)
    print(f"Confirming {len(confirmation_configs):,} selected configs on full history")
    full_trades, full_summary = run_search(
        confirmation_configs,
        "full_history",
        rules_tried_for_adjustment=len(configs),
    )

    human_trades.to_csv(OUT_DIR / "human_window_trades.csv", index=False)
    human_summary.to_csv(OUT_DIR / "human_window_summary.csv", index=False)
    pd.DataFrame(
        [{"rule_id": config.rule_id, "family": config.family, "direction": config.direction}
         for config in confirmation_configs]
    ).to_csv(OUT_DIR / "confirmation_configs.csv", index=False)
    full_trades.to_csv(OUT_DIR / "full_history_trades.csv", index=False)
    full_summary.to_csv(OUT_DIR / "full_history_summary.csv", index=False)
    write_report(human_summary, full_summary)

    print("Human-window top candidates:")
    print(human_summary.head(10)[["family", "direction", "trades", "total_pnl", "profit_factor", "passes_all_core"]].to_string(index=False))
    print()
    print("Full-history top candidates:")
    print(full_summary.head(10)[["family", "direction", "trades", "total_pnl", "profit_factor", "passes_all_core"]].to_string(index=False))
    print(f"\nWrote {OUT_DIR / 'autonomous_edge_search.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
