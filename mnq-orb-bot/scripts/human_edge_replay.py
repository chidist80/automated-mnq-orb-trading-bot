"""
Replay a human MNQ trading record against local 1-minute RTH data.

The goal is not to backfit a strategy. It imports Topstep trade rows, groups
platform rows into approximate trade ideas, then labels the market context that
was knowable before each idea began.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
TOPSTEP_USER_API = "https://userapi.topstepx.com"
EASTERN = "US/Eastern"


@dataclass(frozen=True)
class NormalizedFill:
    row_id: str
    symbol: str
    contract: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    size: int
    gross_pnl: float
    fees: float
    commissions: float | None
    source_position_size: float | None

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


def iso_utc_day_start(day: str) -> str:
    return f"{day}T00:00:00.000Z"


def iso_utc_day_end(day: str) -> str:
    return f"{day}T23:59:59.999Z"


def fetch_topstep_trades(account_id: int, start: str, end: str) -> list[dict[str, Any]]:
    body = json.dumps(
        {
            "tradingAccountId": account_id,
            "start": iso_utc_day_start(start),
            "end": iso_utc_day_end(end),
        }
    ).encode()
    request = urllib.request.Request(
        f"{TOPSTEP_USER_API}/Trade/range",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://topstepx.com",
            "Referer": f"https://topstepx.com/share/stats?share={account_id}",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode(errors="replace")
        raise RuntimeError(f"Topstep Trade/range returned HTTP {exc.code}: {detail}") from exc


def load_json_records(path: Path) -> list[dict[str, Any]]:
    with open(path) as file:
        payload = json.load(file)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("trades", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError(f"{path} does not contain a Topstep trade list")


def load_csv_records(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="") as file:
        return list(csv.DictReader(file))


def parse_number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def parse_timestamp(value: Any, naive_timezone: str) -> pd.Timestamp:
    if value is None:
        raise ValueError("missing timestamp")
    ts = pd.to_datetime(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(naive_timezone)
    else:
        ts = ts.tz_convert(EASTERN)
    return ts.tz_convert(EASTERN)


def infer_direction(row: dict[str, Any]) -> tuple[str, float | None]:
    position_size = first_present(row, "positionSize", "Position Size", "position_size")
    if position_size is not None:
        signed_size = float(position_size)
        if signed_size < 0:
            return "long", signed_size
        if signed_size > 0:
            return "short", signed_size

    trade_type = first_present(row, "type", "Type", "side", "Side", "direction", "Direction")
    if trade_type is not None:
        text = str(trade_type).strip().lower()
        if text in {"0", "long", "buy"}:
            return "long", None
        if text in {"1", "short", "sell"}:
            return "short", None

    entry = parse_number(first_present(row, "entryPrice", "Entry Price", "entry_price"))
    exit_ = parse_number(first_present(row, "exitPrice", "Exit Price", "exit_price"))
    pnl = parse_number(first_present(row, "pnL", "pnl", "P&L", "Pnl"))
    if (exit_ > entry and pnl >= 0) or (exit_ < entry and pnl < 0):
        return "long", None
    return "short", None


def normalize_records(
    records: list[dict[str, Any]],
    naive_timezone: str,
    include_voided: bool = False,
) -> list[NormalizedFill]:
    fills: list[NormalizedFill] = []
    for row in records:
        if not include_voided and str(row.get("voided", "false")).lower() == "true":
            continue

        direction, signed_size = infer_direction(row)
        size_value = first_present(row, "size", "Size", "positionSize", "Position Size")
        size = max(1, int(abs(float(size_value)))) if size_value is not None else 1

        entry_time = parse_timestamp(
            first_present(row, "createdAt", "enteredAt", "Entered At", "entry_time"),
            naive_timezone,
        )
        exit_raw = first_present(row, "exitedAt", "Exited At", "exit_time")
        exit_time = parse_timestamp(exit_raw, naive_timezone) if exit_raw else entry_time

        fill = NormalizedFill(
            row_id=str(first_present(row, "id", "Id", "tradeId", "trade_id") or len(fills) + 1),
            symbol=str(first_present(row, "symbolId", "symbol", "Symbol") or ""),
            contract=str(
                first_present(row, "contractDisplayName", "contractName", "contract", "Contract")
                or ""
            ),
            direction=direction,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=parse_number(
                first_present(row, "entryPrice", "Entry Price", "entry_price")
            ),
            exit_price=parse_number(first_present(row, "exitPrice", "Exit Price", "exit_price")),
            size=size,
            gross_pnl=parse_number(first_present(row, "pnL", "pnl", "P&L", "Pnl")),
            fees=parse_number(first_present(row, "fees", "Fees")),
            commissions=(
                parse_number(first_present(row, "commissions", "Commissions"))
                if first_present(row, "commissions", "Commissions") is not None
                else None
            ),
            source_position_size=signed_size,
        )
        fills.append(fill)
    return sorted(fills, key=lambda fill: (fill.entry_time, fill.exit_time, fill.row_id))


def max_overlapping_contracts(fills: list[NormalizedFill]) -> int:
    events: list[tuple[pd.Timestamp, int]] = []
    for fill in fills:
        events.append((fill.entry_time, fill.size))
        events.append((fill.exit_time, -fill.size))
    current = 0
    max_seen = 0
    for _ts, delta in sorted(events, key=lambda item: (item[0], -item[1])):
        current += delta
        max_seen = max(max_seen, current)
    return max_seen


def weighted_average(fills: list[NormalizedFill], field: str) -> float:
    total_size = sum(fill.size for fill in fills)
    if total_size <= 0:
        return float("nan")
    return sum(getattr(fill, field) * fill.size for fill in fills) / total_size


def idea_record(idea_id: int, fills: list[NormalizedFill]) -> dict[str, Any]:
    first = fills[0]
    return {
        "idea_id": idea_id,
        "fill_count": len(fills),
        "source_row_ids": "|".join(fill.row_id for fill in fills),
        "symbol": first.symbol,
        "contract": "|".join(sorted({fill.contract for fill in fills if fill.contract})),
        "direction": first.direction,
        "entry_time_et": min(fill.entry_time for fill in fills),
        "exit_time_et": max(fill.exit_time for fill in fills),
        "trade_date_et": str(min(fill.entry_time for fill in fills).date()),
        "duration_minutes": (
            max(fill.exit_time for fill in fills) - min(fill.entry_time for fill in fills)
        ).total_seconds()
        / 60.0,
        "weighted_entry_price": weighted_average(fills, "entry_price"),
        "weighted_exit_price": weighted_average(fills, "exit_price"),
        "contract_turnover": sum(fill.size for fill in fills),
        "max_overlapping_contracts": max_overlapping_contracts(fills),
        "gross_pnl": sum(fill.gross_pnl for fill in fills),
        "fees": sum(fill.fees for fill in fills),
        "net_pnl": sum(fill.net_pnl for fill in fills),
    }


def group_trade_ideas(
    fills: list[NormalizedFill],
    merge_gap_minutes: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fill_rows = []
    for fill in fills:
        fill_rows.append(
            {
                "row_id": fill.row_id,
                "symbol": fill.symbol,
                "contract": fill.contract,
                "direction": fill.direction,
                "entry_time_et": fill.entry_time,
                "exit_time_et": fill.exit_time,
                "entry_price": fill.entry_price,
                "exit_price": fill.exit_price,
                "size": fill.size,
                "gross_pnl": fill.gross_pnl,
                "fees": fill.fees,
                "net_pnl": fill.net_pnl,
                "source_position_size": fill.source_position_size,
            }
        )

    gap = pd.Timedelta(minutes=merge_gap_minutes)
    ideas: list[list[NormalizedFill]] = []
    current: list[NormalizedFill] = []

    for fill in fills:
        if not current:
            current = [fill]
            continue

        current_start = min(item.entry_time for item in current)
        current_end = max(item.exit_time for item in current)
        first = current[0]
        same_context = (
            fill.symbol == first.symbol
            and fill.direction == first.direction
            and fill.entry_time.date() == current_start.date()
        )
        if same_context and fill.entry_time <= current_end + gap:
            current.append(fill)
        else:
            ideas.append(current)
            current = [fill]

    if current:
        ideas.append(current)

    idea_rows = [idea_record(index + 1, grouped) for index, grouped in enumerate(ideas)]
    return pd.DataFrame(fill_rows), pd.DataFrame(idea_rows)


def load_1m_data(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df.rename(columns={column: str(column).lower() for column in df.columns})
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize(EASTERN)
    else:
        df.index = df.index.tz_convert(EASTERN)
    return df.sort_index()[["open", "high", "low", "close", "volume"]].copy()


def resample_bars(day: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        day.resample(rule, closed="left", label="left")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(100.0)


def classify_or(
    or_size: float,
    history: list[float],
    wide_percentile: float = 80.0,
) -> tuple[str, float]:
    if len(history) < 5:
        return "normal", 50.0
    percentile = sum(1 for value in history if value <= or_size) / len(history) * 100.0
    if percentile <= 25:
        return "tight", percentile
    if percentile >= wide_percentile:
        return "wide", percentile
    return "normal", percentile


def day_quality(day: pd.DataFrame) -> tuple[bool, str]:
    or_window = day.between_time("09:30", "09:44")
    if day.empty:
        return False, "missing_day"
    if day.index[0].time() != pd.Timestamp("09:30").time():
        return False, "late_start"
    if len(or_window) != 15:
        return False, "incomplete_or"
    if (or_window["volume"] == 0).any():
        return False, "zero_volume_or"
    if float(or_window["high"].max() - or_window["low"].min()) <= 0:
        return False, "zero_range_or"
    return True, "clean"


def build_daily_contexts(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    or_history: list[float] = []
    prior_stats: dict[str, Any] | None = None
    rth = df.between_time("09:30", "15:59")
    bars_15m_all = add_indicators(resample_bars(rth, "15min"))
    bars_5m_all = add_indicators(resample_bars(rth, "5min"))

    for date, day in rth.groupby(rth.index.date):
        day = day.between_time("09:30", "15:59")
        date_key = str(date)
        clean, quality = day_quality(day)
        day_15m = bars_15m_all[bars_15m_all.index.date == date]
        day_5m = bars_5m_all[bars_5m_all.index.date == date]
        context: dict[str, Any] = {
            "date": date_key,
            "day": day,
            "clean": clean,
            "quality": quality,
            "prior_day_trend_direction": None,
            "prior_day_trend_efficiency": np.nan,
            "prior_day_zombie_proxy": False,
        }

        if prior_stats is not None:
            context.update(prior_stats)

        if clean:
            or_window = day.between_time("09:30", "09:44")
            or_high = float(or_window["high"].max())
            or_low = float(or_window["low"].min())
            or_size = or_high - or_low
            or_class, or_percentile = classify_or(or_size, or_history)
            context.update(
                {
                    "or_high": or_high,
                    "or_low": or_low,
                    "or_midpoint": (or_high + or_low) / 2.0,
                    "or_size": or_size,
                    "or_open": float(or_window.iloc[0]["open"]),
                    "or_close": float(or_window.iloc[-1]["close"]),
                    "or_volume": float(or_window["volume"].sum()),
                    "or_classification": or_class,
                    "or_percentile": or_percentile,
                    "bars_15m": day_15m,
                    "bars_5m": day_5m,
                }
            )
            or_history.append(or_size)
            if len(or_history) > 20:
                or_history = or_history[-20:]

        if not day.empty:
            day_range = float(day["high"].max() - day["low"].min())
            day_move = float(day.iloc[-1]["close"] - day.iloc[0]["open"])
            efficiency = abs(day_move) / day_range if day_range > 0 else np.nan
            prior_stats = {
                "prior_day_trend_direction": (
                    "up" if day_move > 0 else "down" if day_move < 0 else "flat"
                ),
                "prior_day_trend_efficiency": efficiency,
                "prior_day_zombie_proxy": bool(day_range > 0 and efficiency >= 0.65),
            }

        contexts[date_key] = context

    return contexts


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars
    out = bars.copy()
    out["ema9"] = ema(out["close"], 9)
    out["ema9_slope"] = out["ema9"].diff()
    out["rsi14"] = rsi(out["close"], 14)
    out["volume_ratio20"] = out["volume"] / out["volume"].rolling(20, min_periods=5).mean()
    return out


def completed_bar_at(
    bars: pd.DataFrame,
    entry_time: pd.Timestamp,
    minutes: int,
) -> pd.Series | None:
    if bars.empty:
        return None
    completed = bars[bars.index + pd.Timedelta(minutes=minutes) <= entry_time]
    if completed.empty:
        return None
    return completed.iloc[-1]


def completed_15m_before(bars: pd.DataFrame, entry_time: pd.Timestamp) -> pd.DataFrame:
    if bars.empty:
        return bars
    return bars[bars.index + pd.Timedelta(minutes=15) <= entry_time]


def relation(
    value: float,
    level: float,
    tolerance: float,
    above_label: str,
    below_label: str,
) -> str:
    if not np.isfinite(value) or not np.isfinite(level):
        return "unknown"
    if abs(value - level) <= tolerance:
        return "near"
    return above_label if value > level else below_label


def price_vs_or(entry_price: float, or_low: float, or_high: float, tolerance: float = 5.0) -> str:
    if not all(np.isfinite([entry_price, or_low, or_high])):
        return "unknown"
    if abs(entry_price - or_high) <= tolerance:
        return "near_or_high"
    if abs(entry_price - or_low) <= tolerance:
        return "near_or_low"
    if entry_price > or_high:
        return "above_or"
    if entry_price < or_low:
        return "below_or"
    return "inside_or"


def rsi_regime(value: float) -> str:
    if not np.isfinite(value):
        return "unknown"
    if value >= 65:
        return "bull_trend_zone"
    if value <= 35:
        return "bear_trend_zone"
    return "neutral"


def time_bucket(ts: pd.Timestamp) -> str:
    minutes = ts.hour * 60 + ts.minute
    buckets = [
        ("pre_rth", 0, 9 * 60 + 30),
        ("09:30-10:00", 9 * 60 + 30, 10 * 60),
        ("10:00-11:00", 10 * 60, 11 * 60),
        ("11:00-12:00", 11 * 60, 12 * 60),
        ("12:00-13:30", 12 * 60, 13 * 60 + 30),
        ("13:30-15:00", 13 * 60 + 30, 15 * 60),
        ("15:00-16:00", 15 * 60, 16 * 60),
    ]
    for label, start, end in buckets:
        if start <= minutes < end:
            return label
    return "post_rth"


def first_completed_breakout(
    completed_15m: pd.DataFrame,
    or_high: float,
    or_low: float,
) -> str:
    after_or = completed_15m[
        completed_15m.index >= completed_15m.index.min() + pd.Timedelta(minutes=15)
    ]
    for _ts, bar in after_or.iterrows():
        if bar["close"] > or_high:
            return "long"
        if bar["close"] < or_low:
            return "short"
    return "none"


def infer_setup_label(row: dict[str, Any]) -> str:
    if row["coverage_status"] != "covered":
        return row["coverage_status"]
    if not row["or_locked_at_entry"]:
        return "pre_or_locked_trade"

    direction = row["direction"]
    price_relation = row["entry_vs_or"]
    breakout = row["first_completed_or_breakout"]
    near_ema = row["entry_vs_15m_ema9"] == "near"
    ema_slope = row["ema9_slope_15m"]
    rsi_value = row["rsi14_15m"]
    wide_or = row["or_classification"] == "wide"

    if wide_or and breakout == "long" and direction == "short" and price_relation in {
        "above_or",
        "near_or_high",
        "inside_or",
    }:
        return "inverse_orb_candidate"
    if wide_or and breakout == "short" and direction == "long" and price_relation in {
        "below_or",
        "near_or_low",
        "inside_or",
    }:
        return "inverse_orb_candidate"
    if direction == "long" and breakout == "long" and price_relation in {
        "above_or",
        "near_or_high",
        "inside_or",
    }:
        return "orb_breakout_or_retest_candidate"
    if direction == "short" and breakout == "short" and price_relation in {
        "below_or",
        "near_or_low",
        "inside_or",
    }:
        return "orb_breakout_or_retest_candidate"
    if direction == "long" and near_ema and ema_slope > 0 and rsi_value >= 50:
        return "ema_continuation_candidate"
    if direction == "short" and near_ema and ema_slope < 0 and rsi_value <= 50:
        return "ema_continuation_candidate"
    return "discretionary_or_unclassified"


def replay_path_stats(
    day: pd.DataFrame,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    entry_price: float,
    direction: str,
) -> dict[str, float]:
    if day.empty:
        return {"mae_points": np.nan, "mfe_points": np.nan}

    start = entry_time.floor("min")
    end = max(exit_time.ceil("min"), start)
    replay = day[(day.index >= start) & (day.index <= end)]
    if replay.empty:
        return {"mae_points": np.nan, "mfe_points": np.nan}

    if direction == "long":
        mfe = float(replay["high"].max() - entry_price)
        mae = float(entry_price - replay["low"].min())
    else:
        mfe = float(entry_price - replay["low"].min())
        mae = float(replay["high"].max() - entry_price)
    return {"mae_points": mae, "mfe_points": mfe}


def move_after_minutes(
    day: pd.DataFrame,
    entry_time: pd.Timestamp,
    entry_price: float,
    direction: str,
    minutes: int,
) -> float:
    if day.empty:
        return np.nan
    target_time = entry_time.floor("min") + pd.Timedelta(minutes=minutes)
    eligible = day[day.index <= target_time]
    if eligible.empty or target_time < day.index[0]:
        return np.nan
    price = float(eligible.iloc[-1]["close"])
    return price - entry_price if direction == "long" else entry_price - price


def label_ideas(ideas: pd.DataFrame, one_minute: pd.DataFrame) -> pd.DataFrame:
    contexts = build_daily_contexts(one_minute)
    rows: list[dict[str, Any]] = []

    for raw in ideas.to_dict("records"):
        entry_time = pd.Timestamp(raw["entry_time_et"]).tz_convert(EASTERN)
        exit_time = pd.Timestamp(raw["exit_time_et"]).tz_convert(EASTERN)
        entry_price = float(raw["weighted_entry_price"])
        date_key = str(entry_time.date())
        context = contexts.get(date_key)
        output = dict(raw)
        output.update(
            {
                "entry_hour_et": entry_time.hour,
                "minutes_after_rth_open": (
                    entry_time
                    - pd.Timestamp(f"{date_key} 09:30", tz=EASTERN)
                ).total_seconds()
                / 60.0,
                "time_bucket": time_bucket(entry_time),
                "or_locked_at_entry": entry_time
                >= pd.Timestamp(f"{date_key} 09:45", tz=EASTERN),
                "coverage_status": "missing_day",
                "data_quality": "missing_day",
            }
        )

        if context is None:
            output["setup_label"] = infer_setup_label(output)
            rows.append(output)
            continue

        day = context["day"]
        output["data_quality"] = context["quality"]
        if day.empty or not (day.index[0] <= entry_time <= day.index[-1] + pd.Timedelta(minutes=1)):
            output["coverage_status"] = "outside_rth_1m"
            output["setup_label"] = infer_setup_label(output)
            rows.append(output)
            continue
        if not context["clean"]:
            output["coverage_status"] = f"unclean_day_{context['quality']}"
            output["setup_label"] = infer_setup_label(output)
            rows.append(output)
            continue

        pre_ts = entry_time.floor("min") - pd.Timedelta(minutes=1)
        pre_day = day[day.index <= pre_ts]
        pre_1m = pre_day.iloc[-1] if not pre_day.empty else None
        bars_15m = context["bars_15m"]
        bars_5m = context["bars_5m"]
        prior_15m = completed_bar_at(bars_15m, entry_time, 15)
        prior_5m = completed_bar_at(bars_5m, entry_time, 5)
        completed_15m = completed_15m_before(bars_15m, entry_time)

        typical = (pre_day["high"] + pre_day["low"] + pre_day["close"]) / 3.0
        dollar_volume = (typical * pre_day["volume"]).sum() if not pre_day.empty else np.nan
        volume_sum = pre_day["volume"].sum() if not pre_day.empty else np.nan
        vwap = float(dollar_volume / volume_sum) if volume_sum and volume_sum > 0 else np.nan
        current_15m_start = entry_time.floor("15min")
        minutes_to_15m_close = (
            current_15m_start + pd.Timedelta(minutes=15) - entry_time
        ).total_seconds() / 60.0

        ema9_15m = float(prior_15m["ema9"]) if prior_15m is not None else np.nan
        ema9_slope = float(prior_15m["ema9_slope"]) if prior_15m is not None else np.nan
        rsi14 = float(prior_15m["rsi14"]) if prior_15m is not None else np.nan
        ema9_5m = float(prior_5m["ema9"]) if prior_5m is not None else np.nan
        volume_ratio_15m = (
            float(prior_15m["volume_ratio20"]) if prior_15m is not None else np.nan
        )
        entry_vs_ema9 = relation(entry_price, ema9_15m, 10.0, "above", "below")
        entry_vs_vwap = relation(entry_price, vwap, 5.0, "above", "below")
        entry_vs_5m_ema9 = relation(entry_price, ema9_5m, 5.0, "above", "below")

        replay_stats = replay_path_stats(
            day=day,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            direction=str(raw["direction"]),
        )

        output.update(
            {
                "coverage_status": "covered",
                "pre_entry_1m_close": float(pre_1m["close"]) if pre_1m is not None else np.nan,
                "or_high": context["or_high"],
                "or_low": context["or_low"],
                "or_midpoint": context["or_midpoint"],
                "or_size": context["or_size"],
                "or_classification": context["or_classification"],
                "or_percentile": context["or_percentile"],
                "entry_vs_or": price_vs_or(entry_price, context["or_low"], context["or_high"]),
                "vwap_pre_entry": vwap,
                "entry_vs_vwap": entry_vs_vwap,
                "ema9_15m": ema9_15m,
                "ema9_slope_15m": ema9_slope,
                "entry_vs_15m_ema9": entry_vs_ema9,
                "ema9_5m": ema9_5m,
                "entry_vs_5m_ema9": entry_vs_5m_ema9,
                "rsi14_15m": rsi14,
                "rsi_regime": rsi_regime(rsi14),
                "volume_ratio_15m": volume_ratio_15m,
                "first_completed_or_breakout": first_completed_breakout(
                    completed_15m,
                    context["or_high"],
                    context["or_low"],
                )
                if len(completed_15m) >= 2
                else "none",
                "minutes_to_15m_close_at_entry": minutes_to_15m_close,
                "prior_day_trend_direction": context["prior_day_trend_direction"],
                "prior_day_trend_efficiency": context["prior_day_trend_efficiency"],
                "prior_day_zombie_proxy": context["prior_day_zombie_proxy"],
                "mae_points": replay_stats["mae_points"],
                "mfe_points": replay_stats["mfe_points"],
                "move_after_5m_points": move_after_minutes(
                    day, entry_time, entry_price, str(raw["direction"]), 5
                ),
                "move_after_15m_points": move_after_minutes(
                    day, entry_time, entry_price, str(raw["direction"]), 15
                ),
                "move_after_30m_points": move_after_minutes(
                    day, entry_time, entry_price, str(raw["direction"]), 30
                ),
                "move_after_60m_points": move_after_minutes(
                    day, entry_time, entry_price, str(raw["direction"]), 60
                ),
            }
        )
        output["trend_state"] = trend_state(output)
        output["setup_label"] = infer_setup_label(output)
        rows.append(output)

    return pd.DataFrame(rows)


def trend_state(row: dict[str, Any]) -> str:
    if not np.isfinite(row.get("ema9_slope_15m", np.nan)):
        return "unknown"
    if row["ema9_slope_15m"] > 0 and row.get("entry_vs_15m_ema9") in {"above", "near"}:
        return "uptrend_above_ema"
    if row["ema9_slope_15m"] < 0 and row.get("entry_vs_15m_ema9") in {"below", "near"}:
        return "downtrend_below_ema"
    return "mixed"


def grouping_sensitivity(fills: list[NormalizedFill], gaps: list[float]) -> pd.DataFrame:
    rows = []
    for gap in gaps:
        _fill_df, idea_df = group_trade_ideas(fills, gap)
        rows.append(
            {
                "merge_gap_minutes": gap,
                "trade_ideas": len(idea_df),
                "avg_rows_per_idea": len(fills) / len(idea_df) if len(idea_df) else np.nan,
                "multi_fill_ideas": int((idea_df["fill_count"] > 1).sum()) if len(idea_df) else 0,
                "max_fill_count": int(idea_df["fill_count"].max()) if len(idea_df) else 0,
            }
        )
    return pd.DataFrame(rows)


def context_summary(labeled: pd.DataFrame) -> pd.DataFrame:
    if labeled.empty:
        return pd.DataFrame()
    group_cols = [
        "setup_label",
        "direction",
        "time_bucket",
        "or_classification",
        "rsi_regime",
        "entry_vs_vwap",
        "entry_vs_15m_ema9",
        "trend_state",
    ]
    present_cols = [col for col in group_cols if col in labeled.columns]
    covered = labeled[labeled["coverage_status"] == "covered"].copy()
    if covered.empty:
        return pd.DataFrame()
    grouped = covered.groupby(present_cols, dropna=False)
    summary = grouped.agg(
        total_ideas=("idea_id", "count"),
        profitable_ideas=("net_pnl", lambda values: int((values > 0).sum())),
        total_net_pnl=("net_pnl", "sum"),
        avg_net_pnl=("net_pnl", "mean"),
        median_net_pnl=("net_pnl", "median"),
        avg_mfe_points=("mfe_points", "mean"),
        avg_mae_points=("mae_points", "mean"),
    ).reset_index()
    summary["win_rate"] = summary["profitable_ideas"] / summary["total_ideas"]
    return summary.sort_values(
        ["total_net_pnl", "profitable_ideas", "total_ideas"],
        ascending=[False, False, False],
    )


def profitable_conditions(labeled: pd.DataFrame) -> pd.DataFrame:
    if labeled.empty:
        return pd.DataFrame()
    profitable = labeled[
        (labeled["coverage_status"] == "covered") & (labeled["net_pnl"] > 0)
    ].copy()
    columns = [
        "idea_id",
        "entry_time_et",
        "exit_time_et",
        "direction",
        "fill_count",
        "net_pnl",
        "setup_label",
        "time_bucket",
        "or_classification",
        "entry_vs_or",
        "first_completed_or_breakout",
        "entry_vs_vwap",
        "entry_vs_15m_ema9",
        "rsi_regime",
        "trend_state",
        "volume_ratio_15m",
        "prior_day_trend_direction",
        "prior_day_zombie_proxy",
        "minutes_to_15m_close_at_entry",
        "mae_points",
        "mfe_points",
        "move_after_15m_points",
        "move_after_30m_points",
    ]
    return profitable[[col for col in columns if col in profitable.columns]].sort_values(
        "net_pnl", ascending=False
    )


def format_pct(value: float) -> str:
    if not np.isfinite(value):
        return "n/a"
    return f"{value:.1%}"


def write_summary(
    output_path: Path,
    account_id: int | None,
    start: str,
    end: str,
    fills: pd.DataFrame,
    ideas: pd.DataFrame,
    labeled: pd.DataFrame,
    summary: pd.DataFrame,
    sensitivity: pd.DataFrame,
    data_path: Path,
    merge_gap_minutes: float,
) -> None:
    covered = labeled[labeled["coverage_status"] == "covered"] if not labeled.empty else labeled
    profitable = covered[covered["net_pnl"] > 0] if not covered.empty else covered
    lines = [
        "# Human Edge Replay",
        "",
        f"- Topstep account/share: {account_id if account_id is not None else 'local file'}",
        f"- Trade date request: {start} through {end}",
        f"- 1-minute data: `{data_path}`",
        (
            "- Merge rule: same symbol, same direction, same ET date, "
            f"entry within {merge_gap_minutes:g} minutes of prior idea exit"
        ),
        f"- Raw Topstep rows imported: {len(fills):,}",
        f"- Normalized trade ideas: {len(ideas):,}",
        f"- Ideas covered by local RTH 1-minute data: {len(covered):,}",
        (
            f"- Covered profitable ideas: {len(profitable):,} "
            f"({format_pct(len(profitable) / len(covered)) if len(covered) else 'n/a'})"
        ),
        (
            f"- Covered net P&L: ${covered['net_pnl'].sum():,.2f}"
            if len(covered)
            else "- Covered net P&L: n/a"
        ),
        "",
        "## Coverage Notes",
        "",
        (
            "The local replay tape is RTH-only. Imported Topstep ideas outside "
            "09:30-16:00 ET are retained but labeled `outside_rth_1m`, so their "
            "context is not guessed from unavailable data."
        ),
        (
            "Pre-entry labels use the last completed 1-minute bar and completed "
            "5/15-minute bars only. EMA/RSI/volume-ratio indicators are computed "
            "on the full RTH history before daily slicing, not reset at each open. "
            "The replay MAE/MFE uses 1-minute OHLC bars, so intraminute ordering "
            "remains approximate."
        ),
        "",
        "## Grouping Sensitivity",
        "",
    ]
    if sensitivity.empty:
        lines.append("No grouping sensitivity available.")
    else:
        lines.append(sensitivity.to_markdown(index=False))

    lines.extend(["", "## Top Covered Context Groups", ""])
    if summary.empty:
        lines.append("No covered context groups available.")
    else:
        top = summary.head(15).copy()
        lines.append(top.to_markdown(index=False, floatfmt=".2f"))

    lines.extend(["", "## Coverage Breakdown", ""])
    if labeled.empty:
        lines.append("No labeled ideas.")
    else:
        coverage = (
            labeled.groupby("coverage_status", dropna=False)
            .agg(ideas=("idea_id", "count"), net_pnl=("net_pnl", "sum"))
            .reset_index()
            .sort_values("ideas", ascending=False)
        )
        lines.append(coverage.to_markdown(index=False, floatfmt=".2f"))

    output_path.write_text("\n".join(lines) + "\n")


def save_dataframe(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    out.to_csv(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topstep-share", type=int, default=14244466)
    parser.add_argument("--start", default="2025-11-07")
    parser.add_argument("--end", default="2026-04-25")
    parser.add_argument("--trades-json")
    parser.add_argument("--trades-csv")
    parser.add_argument("--mnq-1m", default="data/mnq_1m.parquet")
    parser.add_argument("--output-dir", default="research/human_edge_replay/reports")
    parser.add_argument("--raw-output")
    parser.add_argument("--merge-gap-minutes", type=float, default=90.0)
    parser.add_argument("--naive-timezone", default="America/Chicago")
    parser.add_argument("--include-voided", action="store_true")
    args = parser.parse_args()

    if args.trades_json and args.trades_csv:
        raise SystemExit("Use only one of --trades-json or --trades-csv")

    if args.trades_json:
        records = load_json_records(PROJECT_ROOT / args.trades_json)
        account_id = None
    elif args.trades_csv:
        records = load_csv_records(PROJECT_ROOT / args.trades_csv)
        account_id = None
    else:
        records = fetch_topstep_trades(args.topstep_share, args.start, args.end)
        account_id = args.topstep_share
        raw_output = (
            PROJECT_ROOT / args.raw_output
            if args.raw_output
            else PROJECT_ROOT
            / "data"
            / f"topstep_trades_{args.topstep_share}_{args.start}_{args.end}.json"
        )
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        raw_output.write_text(json.dumps(records, indent=2))

    fills = normalize_records(records, args.naive_timezone, include_voided=args.include_voided)
    fill_df, idea_df = group_trade_ideas(fills, args.merge_gap_minutes)
    sensitivity = grouping_sensitivity(fills, [0, 1, 2, 5, 10, 15, 30, 60, 90, 120])

    one_minute_path = PROJECT_ROOT / args.mnq_1m
    one_minute = load_1m_data(one_minute_path)
    labeled = label_ideas(idea_df, one_minute)
    summary = context_summary(labeled)
    profitable = profitable_conditions(labeled)

    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    save_dataframe(fill_df, output_dir / "topstep_fills.csv")
    save_dataframe(idea_df, output_dir / "trade_ideas.csv")
    save_dataframe(labeled, output_dir / "idea_replay.csv")
    save_dataframe(profitable, output_dir / "profitable_ideas.csv")
    save_dataframe(summary, output_dir / "profitable_context_summary.csv")
    save_dataframe(sensitivity, output_dir / "grouping_sensitivity.csv")
    write_summary(
        output_dir / "summary.md",
        account_id=account_id,
        start=args.start,
        end=args.end,
        fills=fill_df,
        ideas=idea_df,
        labeled=labeled,
        summary=summary,
        sensitivity=sensitivity,
        data_path=one_minute_path,
        merge_gap_minutes=args.merge_gap_minutes,
    )

    covered = labeled[labeled["coverage_status"] == "covered"]
    print(f"Imported fills: {len(fill_df):,}")
    print(f"Normalized ideas: {len(idea_df):,}")
    print(f"RTH-covered ideas: {len(covered):,}")
    print(f"Reports written to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
