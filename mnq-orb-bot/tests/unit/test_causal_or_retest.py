import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    A_PLUS_SHADOW,
    OR_HISTORY_WINDOW,
    TIER1_PILOT,
    bootstrap_drawdown_gate,
    build_day_contexts,
    evaluate_rule_on_day,
    paper_forward_log,
    rule_hash,
    _peak_relative_drawdown,
)
from scripts.run_causal_paper_forward import append_journal  # noqa: E402


def _signal_day(
    date: str = "2026-01-02",
    outcome: str = "target",
    end: str = "10:05",
    or_size: float = 20.0,
    signal_minute: str = "10:00",
    signal_close_delta: float = 6.0,
) -> pd.DataFrame:
    idx = pd.date_range(f"{date} 09:30", f"{date} {end}", freq="1min", tz="US/Eastern")
    or_high = 10010.0
    or_low = or_high - or_size
    df = pd.DataFrame(
        {
            "open": or_high - 10.0,
            "high": or_high - 5.0,
            "low": or_high - 15.0,
            "close": or_high - 10.0,
            "volume": 1000,
        },
        index=idx,
    )

    # Opening range: high 10010, low 9990, close position 0.75.
    or_idx = df.between_time("09:30", "09:44").index
    df.loc[or_idx, ["open", "high", "low", "close"]] = [or_high - 10.0, or_high, or_low, or_low]
    df.loc[pd.Timestamp(f"{date} 09:44", tz="US/Eastern"), "close"] = or_low + or_size * 0.75

    # Completed upside breakout before the retest window.
    df.loc[pd.Timestamp(f"{date} 09:45", tz="US/Eastern"), ["open", "high", "low", "close"]] = [
        or_high - 5.0,
        or_high + 6.0,
        or_high - 6.0,
        or_high + 5.0,
    ]

    # Prior bar high for the break-prev confirmation.
    signal_ts = pd.Timestamp(f"{date} {signal_minute}", tz="US/Eastern")
    prev_ts = signal_ts - pd.Timedelta(minutes=1)
    if prev_ts in df.index:
        df.loc[prev_ts, ["open", "high", "low", "close"]] = [
            or_high + 2.0,
            or_high + 4.0,
            or_high - 2.0,
            or_high - 1.0,
        ]

    # Retest confirmation bar. Its high crosses where the target would later be,
    # but the strategy may not fill until the next minute open.
    df.loc[signal_ts, ["open", "high", "low", "close"]] = [
        or_high + 2.0,
        or_high + 80.0,
        or_high,
        or_high + signal_close_delta,
    ]

    entry_ts = signal_ts + pd.Timedelta(minutes=1)
    if entry_ts in df.index:
        df.loc[entry_ts, ["open", "high", "low", "close"]] = [
            or_high + 10.0,
            or_high + 20.0,
            or_high,
            or_high + 10.0,
        ]
    if outcome == "target":
        target_ts = entry_ts + pd.Timedelta(minutes=1)
        if target_ts in df.index:
            df.loc[target_ts, ["open", "high", "low", "close"]] = [
                or_high + 10.0,
                or_high + 70.0,
                or_high + 5.0,
                or_high + 65.0,
            ]
    elif outcome == "ambiguous_stop":
        df.loc[entry_ts, ["high", "low", "close"]] = [or_high + 70.0, or_high - 30.0, or_high + 10.0]
    elif outcome == "time_exit":
        pass
    else:
        raise ValueError(outcome)

    return df


def test_retest_entry_waits_for_next_minute_open_after_confirmation_bar():
    ctx = build_day_contexts(_signal_day())[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert decision.eligible
    assert decision.signal_ts == pd.Timestamp("2026-01-02 10:00", tz="US/Eastern")
    assert decision.entry_ts == pd.Timestamp("2026-01-02 10:01", tz="US/Eastern")
    assert decision.exit_ts == pd.Timestamp("2026-01-02 10:02", tz="US/Eastern")
    assert decision.raw_entry_price == 10020.0
    assert decision.entry_price == 10022.5
    assert decision.entry_price < 10090.0
    assert round(decision.net_pnl, 2) == 93.66


def test_stop_wins_when_stop_and_target_print_in_same_minute():
    ctx = build_day_contexts(_signal_day(outcome="ambiguous_stop"))[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert decision.eligible
    assert decision.exit_ts == pd.Timestamp("2026-01-02 10:01", tz="US/Eastern")
    assert decision.exit_reason == "stop"
    assert round(decision.net_pnl, 2) == -86.34


def test_late_start_day_is_rejected_before_or_calculation():
    late = _signal_day().iloc[1:].copy()
    ctx = build_day_contexts(late)[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert not ctx.clean
    assert ctx.quality == "late_start"
    assert not decision.eligible
    assert decision.rejection_reason == "late_start"


def test_paper_forward_log_records_tier1_execution_and_a_plus_shadow_tag():
    log = paper_forward_log(_signal_day())
    row = log.iloc[0]

    assert row["rule_version"]
    assert row["tier1_rule_hash"] == rule_hash(TIER1_PILOT)
    assert bool(row["tier1_eligible"])
    assert bool(row["tier1_a_plus_tag"])
    assert bool(row["a_plus_shadow_eligible"])
    assert row["execute_rule"] == "tier1_pilot"
    assert round(row["tier1_net_pnl"], 2) == 93.66


def test_full_session_zero_volume_after_or_is_surfaced_without_changing_or_clean_gate():
    day = _signal_day(end="15:59")
    zero_volume_times = [
        pd.Timestamp("2026-01-02 12:00", tz="US/Eastern"),
        pd.Timestamp("2026-01-02 12:01", tz="US/Eastern"),
    ]
    day.loc[zero_volume_times, "volume"] = 0

    log = paper_forward_log(day)
    row = log.iloc[0]

    assert bool(row["clean"])
    assert bool(row["tier1_eligible"])
    assert not bool(row["full_session_clean"])
    assert row["full_session_quality"] == "or_clean_but_post_or_zero_volume"
    assert row["rth_bar_count"] == 390
    assert row["expected_rth_bar_count"] == 390
    assert row["missing_minute_count"] == 0
    assert row["zero_volume_rth_count"] == 2
    assert row["zero_volume_post_or_count"] == 2

    strict_row = paper_forward_log(day, require_full_session_clean=True).iloc[0]
    assert not bool(strict_row["tier1_eligible"])
    assert strict_row["tier1_rejection_reason"] == "or_clean_but_post_or_zero_volume"


def test_missing_post_or_minute_is_surfaced_in_full_session_quality():
    missing_ts = pd.Timestamp("2026-01-02 12:34", tz="US/Eastern")
    day = _signal_day(end="15:59").drop(index=missing_ts)

    log = paper_forward_log(day)
    row = log.iloc[0]

    assert bool(row["clean"])
    assert bool(row["tier1_eligible"])
    assert not bool(row["full_session_clean"])
    assert row["full_session_quality"] == "missing_rth_minutes"
    assert row["rth_bar_count"] == 389
    assert row["expected_rth_bar_count"] == 390
    assert row["missing_minute_count"] == 1
    assert row["zero_volume_rth_count"] == 0


def test_vwap_delta_filter_rejects_extended_signal():
    day = _signal_day(signal_close_delta=100.0)
    ctx = build_day_contexts(day)[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert not decision.eligible
    assert decision.rejection_reason == "signal_filtered"
    signal = ctx.features.loc[pd.Timestamp("2026-01-02 10:00", tz="US/Eastern")]
    assert signal["close"] - signal["vwap"] > 65.0


def test_one_trade_per_day_takes_first_qualifying_retest_only():
    day = _signal_day(end="10:10")
    second_signal = pd.Timestamp("2026-01-02 10:04", tz="US/Eastern")
    day.loc[second_signal - pd.Timedelta(minutes=1), ["open", "high", "low", "close"]] = [
        10012.0,
        10014.0,
        10010.0,
        10012.0,
    ]
    day.loc[second_signal, ["open", "high", "low", "close"]] = [10012.0, 10090.0, 10010.0, 10016.0]
    day.loc[second_signal + pd.Timedelta(minutes=1), ["open", "high", "low", "close"]] = [
        10025.0,
        10080.0,
        10020.0,
        10070.0,
    ]
    ctx = build_day_contexts(day)[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert decision.eligible
    assert decision.signal_ts == pd.Timestamp("2026-01-02 10:00", tz="US/Eastern")
    assert decision.entry_ts == pd.Timestamp("2026-01-02 10:01", tz="US/Eastern")


def test_tier1_rejects_non_normal_or_class():
    days = [_signal_day(f"2026-01-{day:02d}", or_size=20.0) for day in range(2, 7)]
    days.append(_signal_day("2026-01-07", or_size=5.0))
    contexts = build_day_contexts(pd.concat(days).sort_index())

    decision = evaluate_rule_on_day(contexts[-1], TIER1_PILOT)

    assert contexts[-1].or_class == "tight"
    assert not decision.eligible
    assert decision.rejection_reason == "or_class_tight"


def test_no_upside_breakout_rejection():
    day = _signal_day()
    day.loc[pd.Timestamp("2026-01-02 09:45", tz="US/Eastern") :, ["high", "close"]] = [10009.0, 10008.0]
    ctx = build_day_contexts(day)[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert not decision.eligible
    assert decision.rejection_reason == "no_upside_breakout"


def test_time_exit_path_uses_last_bar_at_configured_exit_time():
    day = _signal_day(outcome="time_exit", end="15:59")
    ctx = build_day_contexts(day)[0]

    decision = evaluate_rule_on_day(ctx, TIER1_PILOT)

    assert decision.eligible
    assert decision.exit_ts == pd.Timestamp("2026-01-02 15:55", tz="US/Eastern")
    assert decision.exit_reason == "time_exit"


def test_retest_window_includes_1000_and_1100_boundaries():
    ten = build_day_contexts(_signal_day(signal_minute="10:00"))[0]
    eleven = build_day_contexts(_signal_day(end="11:02", signal_minute="11:00"))[0]

    ten_decision = evaluate_rule_on_day(ten, TIER1_PILOT)
    eleven_decision = evaluate_rule_on_day(eleven, TIER1_PILOT)

    assert ten_decision.signal_ts == pd.Timestamp("2026-01-02 10:00", tz="US/Eastern")
    assert eleven_decision.signal_ts == pd.Timestamp("2026-01-02 11:00", tz="US/Eastern")


def test_signal_vwap_includes_completed_signal_bar():
    day = _signal_day()
    ctx = build_day_contexts(day)[0]
    signal_ts = pd.Timestamp("2026-01-02 10:00", tz="US/Eastern")
    through_signal = day.loc[:signal_ts]
    typical = (through_signal["high"] + through_signal["low"] + through_signal["close"]) / 3.0
    expected_vwap = float((typical * through_signal["volume"]).sum() / through_signal["volume"].sum())

    assert ctx.features.loc[signal_ts, "vwap"] == expected_vwap


def test_filtered_paper_forward_log_preserves_full_history_or_class():
    sizes = [100.0] * 5 + [10.0] * 19 + [50.0]
    days = [_signal_day(f"2026-01-{day + 1:02d}", or_size=size) for day, size in enumerate(sizes)]
    df = pd.concat(days).sort_index()

    full = paper_forward_log(df)
    filtered = paper_forward_log(df, start="2026-01-25", end="2026-01-25")

    full_row = full[full["date"] == "2026-01-25"].iloc[0]
    filtered_row = filtered.iloc[0]
    assert full_row["or_class"] == "wide"
    assert filtered_row["or_class"] == full_row["or_class"]
    assert filtered_row["or_history_count"] == OR_HISTORY_WINDOW


def test_or_history_truncates_to_rolling_20_before_classification():
    sizes = [100.0] * 5 + [10.0] * 19 + [50.0]
    contexts = build_day_contexts(
        pd.concat([_signal_day(f"2026-02-{day + 1:02d}", or_size=size) for day, size in enumerate(sizes)])
    )

    assert contexts[-1].or_history_count == OR_HISTORY_WINDOW
    assert contexts[-1].or_class == "wide"


def test_append_journal_skips_duplicate_date_rule_version(tmp_path):
    log = paper_forward_log(_signal_day(), metadata={"run_timestamp_utc": "first"})
    out = tmp_path / "journal.csv"
    log.to_csv(out, index=False)

    updated, skipped, added = append_journal(log, out)

    assert skipped == 1
    assert added == 0
    assert len(updated) == 1


def test_append_journal_conflicting_duplicate_raises_and_leaves_file_unchanged(tmp_path):
    log = paper_forward_log(_signal_day(), metadata={"data_file_hash": "hash-one"})
    out = tmp_path / "journal.csv"
    log.to_csv(out, index=False)
    before = out.read_text()
    conflicting = log.copy()
    conflicting.loc[0, "data_file_hash"] = "hash-two"
    conflicting.loc[0, "tier1_net_pnl"] = conflicting.loc[0, "tier1_net_pnl"] + 1.0

    with pytest.raises(ValueError, match="date=2026-01-02.*rule_version=.*data_file_hash"):
        append_journal(conflicting, out)

    assert out.read_text() == before


def test_append_journal_schema_mismatch_still_raises(tmp_path):
    log = paper_forward_log(_signal_day())
    out = tmp_path / "journal.csv"
    log.drop(columns=["tier1_net_pnl"]).to_csv(out, index=False)

    with pytest.raises(ValueError, match="schema differs"):
        append_journal(log, out)


def test_peak_relative_drawdown_uses_current_peak_denominator():
    max_dd, max_dd_pct, final_equity = _peak_relative_drawdown(
        [-100.0, 500.0, -100.0],
        account_size=1000.0,
    )

    assert max_dd == 100.0
    assert max_dd_pct == pytest.approx(0.10)
    assert final_equity == 1300.0


def test_bootstrap_drawdown_gate_is_deterministic_with_fixed_seed():
    pnls = [100.0, -50.0, 25.0, -75.0]

    first = bootstrap_drawdown_gate(pnls, account_size=1000.0, simulations=250, seed=7)
    second = bootstrap_drawdown_gate(pnls, account_size=1000.0, simulations=250, seed=7)

    assert first == second
    assert first["trades"] == 4
    assert 0.0 <= first["ruin_probability"] <= 1.0


def test_rule_hash_changes_when_rule_threshold_changes():
    changed = replace(A_PLUS_SHADOW, max_signal_ema_slope=19.0)

    assert rule_hash(changed) != rule_hash(A_PLUS_SHADOW)
