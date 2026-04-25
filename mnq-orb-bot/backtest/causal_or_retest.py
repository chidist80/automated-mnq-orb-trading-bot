"""Causal 1-minute OR retest replay for the Phase 5/6 candidate rules.

This module is intentionally isolated from the older 15-minute strategy
backtester. It models the execution contract we need for forward validation:

- opening range from completed 09:30-09:44 ET 1-minute bars
- completed 1-minute breakout and retest signal
- entry on the next 1-minute open
- stop/target sequencing on 1-minute bars
- conservative same-minute ambiguity: stop wins
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest.indicators import ema, rsi, volume_ratio

EASTERN = "US/Eastern"
MNQ_MULTIPLIER = 2.0
COMMISSION_RT = 1.34
DEFAULT_SLIPPAGE_RT = 5.0
RULE_VERSION = "phase6_causal_or_retest_v1"
OR_HISTORY_WINDOW = 20
EXPECTED_RTH_START = "09:30"
EXPECTED_RTH_END = "15:59"
EXPECTED_RTH_BAR_COUNT = 390


@dataclass(frozen=True)
class RuleSpec:
    name: str
    or_classes: tuple[str, ...]
    start: str = "10:00"
    end: str = "11:00"
    touch_tolerance: float = 5.0
    stop_points: float = 40.0
    rr: float = 1.25
    time_exit: str = "15:55"
    slippage_rt: float = DEFAULT_SLIPPAGE_RT
    max_close_vwap_delta: float | None = None
    max_signal_volume_ratio: float | None = None
    min_signal_rsi: float | None = None
    max_signal_rsi: float | None = None
    max_signal_ema_slope: float | None = None
    min_signal_ema_slope: float | None = None
    min_or_close_pos: float | None = None
    min_signal_minute: int | None = None


TIER1_PILOT = RuleSpec(
    name="tier1_pilot",
    or_classes=("normal",),
    rr=1.25,
    max_close_vwap_delta=65.0,
)

TIER2_7500 = RuleSpec(
    name="tier2_7500",
    or_classes=("normal", "tight"),
    rr=2.0,
    max_signal_volume_ratio=2.0,
)

A_PLUS_SHADOW = replace(
    TIER1_PILOT,
    name="a_plus_shadow",
    max_signal_ema_slope=20.0,
    min_or_close_pos=0.4,
)


@dataclass
class DayContext:
    date: str
    day: pd.DataFrame
    features: pd.DataFrame
    clean: bool
    quality: str
    rth_bar_count: int = 0
    expected_rth_bar_count: int = EXPECTED_RTH_BAR_COUNT
    missing_minute_count: int = 0
    zero_volume_rth_count: int = 0
    zero_volume_post_or_count: int = 0
    session_end_ts: pd.Timestamp | None = None
    expected_session_end_ts: pd.Timestamp | None = None
    early_close_flag: bool = False
    full_session_clean: bool = False
    full_session_quality: str = "unknown"
    or_high: float = np.nan
    or_low: float = np.nan
    or_midpoint: float = np.nan
    or_size: float = np.nan
    or_open: float = np.nan
    or_close: float = np.nan
    or_class: str = "unknown"
    or_percentile: float = np.nan
    or_history_count: int = 0
    or_history_window: int = OR_HISTORY_WINDOW

    @property
    def or_close_position(self) -> float:
        if not np.isfinite(self.or_size) or self.or_size <= 0:
            return np.nan
        return (self.or_close - self.or_low) / self.or_size


@dataclass
class RuleDecision:
    rule_name: str
    date: str
    eligible: bool
    rejection_reason: str = ""
    signal_ts: pd.Timestamp | None = None
    breakout_ts: pd.Timestamp | None = None
    entry_ts: pd.Timestamp | None = None
    raw_entry_price: float = np.nan
    entry_price: float = np.nan
    stop_price: float = np.nan
    target_price: float = np.nan
    exit_ts: pd.Timestamp | None = None
    raw_exit_price: float = np.nan
    exit_price: float = np.nan
    exit_reason: str = ""
    net_pnl: float = np.nan


def load_1m_parquet(path: str | Path) -> pd.DataFrame:
    """Load a local 1-minute OHLCV parquet file and normalize timestamps."""
    return normalize_1m_bars(pd.read_parquet(path))


def rule_to_dict(rule: RuleSpec) -> dict[str, Any]:
    """Serialize a frozen rule spec for metadata and review logs."""
    payload = asdict(rule)
    payload["or_classes"] = list(rule.or_classes)
    payload["rule_version"] = RULE_VERSION
    return payload


def rule_hash(rule: RuleSpec) -> str:
    """Stable short hash for detecting rule-definition drift."""
    encoded = json.dumps(rule_to_dict(rule), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def normalize_1m_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLCV columns and timestamps for causal replay."""
    out = df.rename(columns={column: str(column).lower() for column in df.columns}).copy()
    if "timestamp" in out.columns:
        out = out.set_index("timestamp")
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    if out.index.tz is None:
        out.index = out.index.tz_localize(EASTERN)
    else:
        out.index = out.index.tz_convert(EASTERN)
    return out.sort_index()[["open", "high", "low", "close", "volume"]].copy()


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    return df.between_time("09:30", "15:59").copy()


def classify_or(or_size: float, history: list[float], wide_percentile: float = 80.0) -> tuple[str, float]:
    if len(history) < 5:
        return "normal", 50.0
    percentile = sum(1 for value in history if value <= or_size) / len(history) * 100.0
    if percentile <= 25.0:
        return "tight", percentile
    if percentile >= wide_percentile:
        return "wide", percentile
    return "normal", percentile


def day_quality(day: pd.DataFrame) -> tuple[bool, str]:
    if day.empty:
        return False, "missing_day"
    if day.index[0].time() != pd.Timestamp("09:30").time():
        return False, "late_start"
    or_window = day.between_time("09:30", "09:44")
    if len(or_window) != 15:
        return False, "incomplete_or"
    if (or_window["volume"] == 0).any():
        return False, "zero_volume_or"
    if float(or_window["high"].max() - or_window["low"].min()) <= 0:
        return False, "zero_range_or"
    return True, "clean"


def expected_rth_index(date_key: str) -> pd.DatetimeIndex:
    return pd.date_range(
        f"{date_key} {EXPECTED_RTH_START}",
        f"{date_key} {EXPECTED_RTH_END}",
        freq="1min",
        tz=EASTERN,
    )


def _is_short_continuous_session(day: pd.DataFrame, expected_index: pd.DatetimeIndex) -> bool:
    if day.empty:
        return False
    actual = day.index.drop_duplicates()
    if actual[0] != expected_index[0] or actual[-1] >= expected_index[-1]:
        return False
    expected_through_observed_end = expected_index[expected_index <= actual[-1]]
    return actual.equals(expected_through_observed_end)


def full_session_diagnostics(
    day: pd.DataFrame,
    date_key: str,
    or_clean: bool,
) -> dict[str, Any]:
    """Classify full-session data quality without changing the OR-only clean gate."""
    expected_index = expected_rth_index(date_key)
    expected_end = expected_index[-1]
    if day.empty:
        return {
            "rth_bar_count": 0,
            "expected_rth_bar_count": len(expected_index),
            "missing_minute_count": len(expected_index),
            "zero_volume_rth_count": 0,
            "zero_volume_post_or_count": 0,
            "session_end_ts": None,
            "expected_session_end_ts": expected_end,
            "early_close_flag": False,
            "full_session_clean": False,
            "full_session_quality": "missing_day",
        }

    actual = day.index.drop_duplicates()
    missing_minute_count = int(len(expected_index.difference(actual)))
    zero_volume_rth_count = int((day["volume"] == 0).sum())
    post_or_start = pd.Timestamp(f"{date_key} 09:45", tz=EASTERN)
    zero_volume_post_or_count = int((day[day.index >= post_or_start]["volume"] == 0).sum())
    or_window = day.between_time("09:30", "09:44")
    zero_volume_or_count = int((or_window["volume"] == 0).sum()) if not or_window.empty else 0
    early_close_flag = (
        missing_minute_count > 0
        and zero_volume_rth_count == 0
        and _is_short_continuous_session(day, expected_index)
    )

    reasons: list[str] = []
    if zero_volume_rth_count:
        if zero_volume_or_count and zero_volume_post_or_count:
            reasons.append("zero_volume_or_and_post_or")
        elif zero_volume_or_count:
            reasons.append("zero_volume_or")
        elif zero_volume_post_or_count and or_clean:
            reasons.append("or_clean_but_post_or_zero_volume")
        elif zero_volume_post_or_count:
            reasons.append("zero_volume_post_or")
        else:
            reasons.append("zero_volume_rth")

    if missing_minute_count:
        reasons.append("short_session" if early_close_flag else "missing_rth_minutes")

    if not reasons and len(day) != len(expected_index):
        reasons.append("unexpected_rth_bar_count")

    full_session_quality = "clean" if not reasons else "+".join(reasons)
    return {
        "rth_bar_count": int(len(day)),
        "expected_rth_bar_count": int(len(expected_index)),
        "missing_minute_count": missing_minute_count,
        "zero_volume_rth_count": zero_volume_rth_count,
        "zero_volume_post_or_count": zero_volume_post_or_count,
        "session_end_ts": day.index[-1],
        "expected_session_end_ts": expected_end,
        "early_close_flag": bool(early_close_flag),
        "full_session_clean": full_session_quality == "clean",
        "full_session_quality": full_session_quality,
    }


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.resample(rule, closed="left", label="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def add_15m_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    out = bars.copy()
    out["ema9"] = ema(out["close"], 9)
    out["ema9_slope"] = out["ema9"].diff()
    out["rsi14"] = rsi(out["close"], 14)
    out["volume_ratio20"] = volume_ratio(out["volume"], 20)
    return out


def add_day_features(day: pd.DataFrame, bars_15m: pd.DataFrame) -> pd.DataFrame:
    features = day.copy()
    typical = (features["high"] + features["low"] + features["close"]) / 3.0
    volume_sum = features["volume"].cumsum().replace(0, np.nan)
    features["vwap"] = (typical * features["volume"]).cumsum() / volume_sum
    features["prev_high"] = features["high"].shift(1)
    features["prev_low"] = features["low"].shift(1)
    features["prev_close"] = features["close"].shift(1)

    if bars_15m.empty:
        for col in ["ema9", "ema9_slope", "rsi14", "volume_ratio20"]:
            features[col] = np.nan
        return features

    right = bars_15m[["ema9", "ema9_slope", "rsi14", "volume_ratio20"]].reset_index()
    right = right.rename(columns={right.columns[0]: "bar_start"})
    right["available_at"] = right["bar_start"] + pd.Timedelta(minutes=15)
    left = features.reset_index().rename(columns={features.index.name or "index": "ts"})
    merged = pd.merge_asof(
        left.sort_values("ts"),
        right.sort_values("available_at"),
        left_on="ts",
        right_on="available_at",
        direction="backward",
    ).set_index("ts")
    for col in ["ema9", "ema9_slope", "rsi14", "volume_ratio20"]:
        features[col] = merged[col]
    return features


def build_day_contexts(df: pd.DataFrame) -> list[DayContext]:
    rth = filter_rth(normalize_1m_bars(df))
    bars_15m_all = add_15m_indicators(resample_ohlcv(rth, "15min"))
    contexts: list[DayContext] = []
    or_history: list[float] = []

    for date, raw_day in rth.groupby(rth.index.date):
        day = raw_day.between_time("09:30", "15:59")
        date_key = str(date)
        clean, quality = day_quality(day)
        full_session = full_session_diagnostics(day, date_key, clean)
        day_15m = bars_15m_all[bars_15m_all.index.date == date]
        features = add_day_features(day, day_15m)
        ctx = DayContext(
            date=date_key,
            day=day,
            features=features,
            clean=clean,
            quality=quality,
            **full_session,
        )
        ctx.or_history_count = len(or_history)
        ctx.or_history_window = OR_HISTORY_WINDOW

        if clean:
            or_window = day.between_time("09:30", "09:44")
            ctx.or_high = float(or_window["high"].max())
            ctx.or_low = float(or_window["low"].min())
            ctx.or_midpoint = (ctx.or_high + ctx.or_low) / 2.0
            ctx.or_size = ctx.or_high - ctx.or_low
            ctx.or_open = float(or_window.iloc[0]["open"])
            ctx.or_close = float(or_window.iloc[-1]["close"])
            ctx.or_class, ctx.or_percentile = classify_or(ctx.or_size, or_history)
            or_history.append(ctx.or_size)
            if len(or_history) > OR_HISTORY_WINDOW:
                or_history = or_history[-OR_HISTORY_WINDOW:]

        contexts.append(ctx)
    return contexts


def timestamp_on_day(ctx: DayContext, hhmm: str) -> pd.Timestamp:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return pd.Timestamp(ctx.date, tz=EASTERN) + pd.Timedelta(hours=hour, minutes=minute)


def time_window_mask(df: pd.DataFrame, start: str, end: str) -> pd.Series:
    start_time = pd.Timestamp(start).time()
    end_time = pd.Timestamp(end).time()
    return (df.index.time >= start_time) & (df.index.time <= end_time)


def apply_slippage(price: float, direction: str, side: str, round_trip_points: float) -> float:
    half = round_trip_points / 2.0
    if side == "entry":
        return price + half if direction == "long" else price - half
    return price - half if direction == "long" else price + half


def net_pnl(entry: float, exit_: float, direction: str, commission_rt: float = COMMISSION_RT) -> float:
    gross_points = exit_ - entry if direction == "long" else entry - exit_
    return gross_points * MNQ_MULTIPLIER - commission_rt


def first_long_breakout(ctx: DayContext) -> pd.Timestamp | None:
    start_ts = timestamp_on_day(ctx, "09:45")
    sub = ctx.features[ctx.features.index >= start_ts]
    hits = sub[sub["close"] > ctx.or_high]
    return None if hits.empty else hits.index[0]


def next_open(features: pd.DataFrame, signal_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    eligible = features[features.index > signal_ts]
    if eligible.empty:
        return None
    row = eligible.iloc[0]
    return row.name, float(row["open"])


def signal_passes_optional_filters(signal: pd.Series, ctx: DayContext, rule: RuleSpec) -> bool:
    if rule.min_signal_minute is not None:
        current_minute = signal.name.hour * 60 + signal.name.minute
        if current_minute < rule.min_signal_minute:
            return False

    if rule.min_or_close_pos is not None:
        if not np.isfinite(ctx.or_close_position) or ctx.or_close_position < rule.min_or_close_pos:
            return False

    if rule.max_close_vwap_delta is not None:
        delta = float(signal["close"] - signal["vwap"])
        if not np.isfinite(delta) or delta > rule.max_close_vwap_delta:
            return False

    if rule.max_signal_volume_ratio is not None:
        value = float(signal.get("volume_ratio20", np.nan))
        if not np.isfinite(value) or value > rule.max_signal_volume_ratio:
            return False

    if rule.min_signal_rsi is not None:
        value = float(signal.get("rsi14", np.nan))
        if not np.isfinite(value) or value < rule.min_signal_rsi:
            return False

    if rule.max_signal_rsi is not None:
        value = float(signal.get("rsi14", np.nan))
        if not np.isfinite(value) or value > rule.max_signal_rsi:
            return False

    if rule.max_signal_ema_slope is not None:
        value = float(signal.get("ema9_slope", np.nan))
        if not np.isfinite(value) or value > rule.max_signal_ema_slope:
            return False

    if rule.min_signal_ema_slope is not None:
        value = float(signal.get("ema9_slope", np.nan))
        if not np.isfinite(value) or value < rule.min_signal_ema_slope:
            return False

    return True


def candidate_signals(ctx: DayContext, rule: RuleSpec, breakout_ts: pd.Timestamp) -> pd.DataFrame:
    after = ctx.features[
        (ctx.features.index > breakout_ts) & time_window_mask(ctx.features, rule.start, rule.end)
    ]
    mask = after["low"] <= ctx.or_high + rule.touch_tolerance
    mask &= after["close"] > ctx.or_high
    mask &= after["close"] > after["prev_high"]
    return after[mask]


def simulate_long_exit(
    ctx: DayContext,
    entry_ts: pd.Timestamp,
    entry_price: float,
    stop_price: float,
    target_price: float,
    time_exit: str,
) -> tuple[pd.Timestamp, float, str] | None:
    if not stop_price < entry_price < target_price:
        return None
    end_ts = timestamp_on_day(ctx, time_exit)
    sub = ctx.features[(ctx.features.index >= entry_ts) & (ctx.features.index <= end_ts)]
    if sub.empty:
        return None

    for ts, bar in sub.iterrows():
        stop_hit = bar["low"] <= stop_price
        target_hit = bar["high"] >= target_price
        if stop_hit:
            return ts, stop_price, "stop"
        if target_hit:
            return ts, target_price, "target"

    last = sub.iloc[-1]
    return last.name, float(last["close"]), "time_exit"


def evaluate_rule_on_day(
    ctx: DayContext,
    rule: RuleSpec,
    require_full_session_clean: bool = False,
) -> RuleDecision:
    if not ctx.clean:
        return RuleDecision(rule.name, ctx.date, False, ctx.quality)
    if require_full_session_clean and not ctx.full_session_clean:
        return RuleDecision(rule.name, ctx.date, False, ctx.full_session_quality)
    if ctx.or_class not in rule.or_classes:
        return RuleDecision(rule.name, ctx.date, False, f"or_class_{ctx.or_class}")

    breakout_ts = first_long_breakout(ctx)
    if breakout_ts is None:
        return RuleDecision(rule.name, ctx.date, False, "no_upside_breakout")

    signals = candidate_signals(ctx, rule, breakout_ts)
    if signals.empty:
        return RuleDecision(rule.name, ctx.date, False, "no_retest_signal", breakout_ts=breakout_ts)

    filtered_signal_seen = False
    for signal_ts, signal in signals.iterrows():
        if not signal_passes_optional_filters(signal, ctx, rule):
            filtered_signal_seen = True
            continue
        entry = next_open(ctx.features, signal_ts)
        if entry is None:
            return RuleDecision(rule.name, ctx.date, False, "no_next_open", signal_ts, breakout_ts)
        entry_ts, raw_entry = entry
        entry_price = apply_slippage(raw_entry, "long", "entry", rule.slippage_rt)
        stop_price = entry_price - rule.stop_points
        target_price = entry_price + rule.rr * rule.stop_points
        exit_result = simulate_long_exit(
            ctx, entry_ts, entry_price, stop_price, target_price, rule.time_exit
        )
        if exit_result is None:
            return RuleDecision(rule.name, ctx.date, False, "no_exit_path", signal_ts, breakout_ts)
        exit_ts, raw_exit, exit_reason = exit_result
        exit_price = apply_slippage(raw_exit, "long", "exit", rule.slippage_rt)
        return RuleDecision(
            rule_name=rule.name,
            date=ctx.date,
            eligible=True,
            signal_ts=signal_ts,
            breakout_ts=breakout_ts,
            entry_ts=entry_ts,
            raw_entry_price=raw_entry,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            exit_ts=exit_ts,
            raw_exit_price=raw_exit,
            exit_price=exit_price,
            exit_reason=exit_reason,
            net_pnl=net_pnl(entry_price, exit_price, "long"),
        )

    reason = "signal_filtered" if filtered_signal_seen else "no_retest_signal"
    return RuleDecision(rule.name, ctx.date, False, reason, breakout_ts=breakout_ts)


def result_columns(prefix: str, decision: RuleDecision) -> dict[str, Any]:
    return {
        f"{prefix}_eligible": decision.eligible,
        f"{prefix}_rejection_reason": decision.rejection_reason,
        f"{prefix}_breakout_ts": decision.breakout_ts,
        f"{prefix}_signal_ts": decision.signal_ts,
        f"{prefix}_entry_ts": decision.entry_ts,
        f"{prefix}_raw_entry_price": decision.raw_entry_price,
        f"{prefix}_entry_price": decision.entry_price,
        f"{prefix}_stop_price": decision.stop_price,
        f"{prefix}_target_price": decision.target_price,
        f"{prefix}_exit_ts": decision.exit_ts,
        f"{prefix}_raw_exit_price": decision.raw_exit_price,
        f"{prefix}_exit_price": decision.exit_price,
        f"{prefix}_exit_reason": decision.exit_reason,
        f"{prefix}_net_pnl": decision.net_pnl,
    }


def _bar_ohlcv_columns(prefix: str, ctx: DayContext, decision: RuleDecision) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for label, ts in [
        ("signal_bar", decision.signal_ts),
        ("entry_bar", decision.entry_ts),
        ("exit_bar", decision.exit_ts),
    ]:
        for col in ["open", "high", "low", "close", "volume", "vwap"]:
            fields[f"{prefix}_{label}_{col}"] = np.nan
        if ts is None or ts not in ctx.features.index:
            continue
        row = ctx.features.loc[ts]
        for col in ["open", "high", "low", "close", "volume", "vwap"]:
            fields[f"{prefix}_{label}_{col}"] = float(row[col]) if col in row else np.nan
    return fields


def is_a_plus_tag(ctx: DayContext, tier1_decision: RuleDecision) -> bool:
    if not tier1_decision.eligible or tier1_decision.signal_ts is None:
        return False
    signal = ctx.features.loc[tier1_decision.signal_ts]
    return signal_passes_optional_filters(signal, ctx, A_PLUS_SHADOW)


def paper_forward_log(
    df: pd.DataFrame,
    tier1: RuleSpec = TIER1_PILOT,
    tier2: RuleSpec = TIER2_7500,
    a_plus: RuleSpec = A_PLUS_SHADOW,
    start: str | None = None,
    end: str | None = None,
    metadata: dict[str, Any] | None = None,
    require_full_session_clean: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metadata = metadata or {}
    for ctx in build_day_contexts(df):
        if not _date_in_range(ctx.date, start, end):
            continue
        tier1_decision = evaluate_rule_on_day(ctx, tier1, require_full_session_clean)
        tier2_decision = evaluate_rule_on_day(ctx, tier2, require_full_session_clean)
        a_plus_decision = evaluate_rule_on_day(ctx, a_plus, require_full_session_clean)
        row: dict[str, Any] = {
            "date": ctx.date,
            "rule_version": RULE_VERSION,
            "tier1_rule_hash": rule_hash(tier1),
            "tier2_rule_hash": rule_hash(tier2),
            "a_plus_rule_hash": rule_hash(a_plus),
            "data_file_hash": metadata.get("data_file_hash", ""),
            "data_last_timestamp": metadata.get("data_last_timestamp", ""),
            "run_timestamp_utc": metadata.get("run_timestamp_utc", ""),
            "clean": ctx.clean,
            "quality": ctx.quality,
            "rth_bar_count": ctx.rth_bar_count,
            "expected_rth_bar_count": ctx.expected_rth_bar_count,
            "missing_minute_count": ctx.missing_minute_count,
            "zero_volume_rth_count": ctx.zero_volume_rth_count,
            "zero_volume_post_or_count": ctx.zero_volume_post_or_count,
            "session_end_ts": ctx.session_end_ts,
            "expected_session_end_ts": ctx.expected_session_end_ts,
            "early_close_flag": ctx.early_close_flag,
            "full_session_clean": ctx.full_session_clean,
            "full_session_quality": ctx.full_session_quality,
            "or_class": ctx.or_class,
            "or_percentile": ctx.or_percentile,
            "or_history_count": ctx.or_history_count,
            "or_history_window": ctx.or_history_window,
            "or_high": ctx.or_high,
            "or_low": ctx.or_low,
            "or_size": ctx.or_size,
            "or_close_position": ctx.or_close_position,
            "execute_rule": tier1.name,
            "tier1_a_plus_tag": is_a_plus_tag(ctx, tier1_decision),
        }
        row.update(result_columns("tier1", tier1_decision))
        row.update(_bar_ohlcv_columns("tier1", ctx, tier1_decision))
        row.update(result_columns("tier2_shadow", tier2_decision))
        row.update(_bar_ohlcv_columns("tier2_shadow", ctx, tier2_decision))
        row.update(result_columns("a_plus_shadow", a_plus_decision))
        row.update(_bar_ohlcv_columns("a_plus_shadow", ctx, a_plus_decision))
        rows.append(row)
    return pd.DataFrame(rows)


def _date_in_range(date_value: str, start: str | None, end: str | None) -> bool:
    date = pd.Timestamp(date_value).date()
    if start and date < pd.Timestamp(start).date():
        return False
    if end and date > pd.Timestamp(end).date():
        return False
    return True


def _peak_relative_drawdown(
    trade_pnls: list[float] | np.ndarray | pd.Series,
    account_size: float,
) -> tuple[float, float, float]:
    pnls = np.asarray(trade_pnls, dtype=float)
    equity = account_size + np.cumsum(pnls)
    equity = np.insert(equity, 0, account_size)
    peak = np.maximum.accumulate(equity)
    drawdowns = peak - equity
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.divide(drawdowns, peak, out=np.zeros_like(drawdowns), where=peak > 0)
    return float(np.max(drawdowns)), float(np.max(dd_pct)), float(equity[-1])


def bootstrap_drawdown_gate(
    trade_pnls: list[float] | np.ndarray | pd.Series,
    account_size: float,
    simulations: int = 20_000,
    ruin_threshold_pct: float = 0.20,
    seed: int = 42,
) -> dict[str, float | int]:
    """Bootstrap trade sequences with replacement and measure peak-relative DD risk."""
    pnls = np.asarray(trade_pnls, dtype=float)
    if pnls.size == 0:
        raise ValueError("No trade P&Ls supplied")

    rng = np.random.default_rng(seed)
    max_dds = np.zeros(simulations)
    max_dd_pcts = np.zeros(simulations)
    final_equities = np.zeros(simulations)
    ruin_count = 0

    for i in range(simulations):
        sample = rng.choice(pnls, size=pnls.size, replace=True)
        max_dds[i], max_dd_pcts[i], final_equities[i] = _peak_relative_drawdown(sample, account_size)
        if max_dd_pcts[i] >= ruin_threshold_pct:
            ruin_count += 1

    return {
        "simulations": int(simulations),
        "account_size": float(account_size),
        "trades": int(pnls.size),
        "ruin_threshold_pct": float(ruin_threshold_pct),
        "ruin_count": int(ruin_count),
        "ruin_probability": float(ruin_count / simulations),
        "median_max_drawdown": float(np.median(max_dds)),
        "p95_max_drawdown": float(np.percentile(max_dds, 95)),
        "p99_max_drawdown": float(np.percentile(max_dds, 99)),
        "median_max_drawdown_pct": float(np.median(max_dd_pcts)),
        "p95_max_drawdown_pct": float(np.percentile(max_dd_pcts, 95)),
        "p99_max_drawdown_pct": float(np.percentile(max_dd_pcts, 99)),
        "median_final_equity": float(np.median(final_equities)),
    }


def summarize_decisions(log: pd.DataFrame, prefix: str) -> dict[str, float | int]:
    if log.empty or f"{prefix}_eligible" not in log:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": np.nan, "win_rate": np.nan, "pf": np.nan}
    trades = log[log[f"{prefix}_eligible"]].copy()
    if trades.empty:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": np.nan, "win_rate": np.nan, "pf": np.nan}
    pnl = trades[f"{prefix}_net_pnl"]
    wins = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl <= 0].sum())
    return {
        "trades": int(len(trades)),
        "total_pnl": float(pnl.sum()),
        "avg_pnl": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "pf": float(wins / losses) if losses else np.inf,
    }
