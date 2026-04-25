"""
Phase 5 — slippage-robust autonomous edge search.

This pass starts from the Phase 4 signal that survived reality checks:
causal long OR retests after 10:00 on non-wide opening ranges. It then asks
whether a small, executable variant clears the gates at harsher transaction
costs.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase3_autonomous_edge_search import RuleConfig, run_search  # type: ignore

OUT_DIR = PROJECT_ROOT / "research" / "human_edge_replay" / "phase5_slippage_robust"


def minutes(hhmm: str) -> int:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return hour * 60 + minute


def compact_rule_id(prefix: str, direction: str, params: dict[str, Any]) -> str:
    parts = [prefix, direction]
    for key in sorted(params):
        if key == "slippage_rt":
            continue
        value = params[key]
        if value is None or value == "all":
            continue
        if isinstance(value, float):
            if np.isnan(value):
                continue
            value = f"{value:g}"
        parts.append(f"{key}={value}")
    return "|".join(parts)


def base_params(slippage_rt: float) -> dict[str, Any]:
    return {
        "buffer_points": 0.0,
        "confirm_pattern": "break_prev",
        "max_risk_points": 120.0,
        "max_stop": 120.0,
        "min_reward_points": 8.0,
        "stop_mode": "fixed",
        "target_mode": "rr",
        "slippage_rt": slippage_rt,
    }


def make_config(prefix: str, direction: str, params: dict[str, Any]) -> RuleConfig:
    return RuleConfig(compact_rule_id(prefix, direction, params), "or_retest", direction, params)


def build_timing_shape_configs(slippage_rt: float) -> list[RuleConfig]:
    configs: list[RuleConfig] = []
    for values in itertools.product(
        ["normal", "normal_tight"],
        ["10:00", "10:10", "10:20"],
        ["10:45", "11:00", "11:30"],
        [5.0, 10.0],
        [30.0, 40.0, 60.0],
        [1.25, 1.5, 2.0, 2.5],
        ["12:00", "15:55"],
    ):
        or_class, start, end, touch_tolerance, stop_points, rr, time_exit = values
        if minutes(start) >= minutes(end):
            continue
        params = base_params(slippage_rt)
        params.update(
            {
                "or_class": or_class,
                "start": start,
                "end": end,
                "touch_tolerance": touch_tolerance,
                "stop_points": stop_points,
                "rr": rr,
                "time_exit": time_exit,
            }
        )
        configs.append(make_config("or_retest_slip_shape", "long", params))
    return configs


def one_filter_variants(base: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [base.copy()]
    for value in [2.0, 2.25, 2.5]:
        item = base.copy()
        item["max_signal_volume_ratio"] = value
        variants.append(item)
    for value in [50.0, 70.0, 90.0]:
        item = base.copy()
        item["max_close_ema_delta"] = value
        variants.append(item)
    for value in [35.0, 50.0, 65.0]:
        item = base.copy()
        item["max_close_vwap_delta"] = value
        variants.append(item)
    for value in ["above", "below"]:
        item = base.copy()
        item["signal_vs_ema"] = value
        variants.append(item)
    for value in ["yes", "no"]:
        item = base.copy()
        item["prior_day_zombie"] = value
        variants.append(item)
    for value in ["up", "down"]:
        item = base.copy()
        item["prior_day_direction"] = value
        variants.append(item)
    for value in [50.0, 55.0, 60.0]:
        item = base.copy()
        item["min_signal_rsi"] = value
        variants.append(item)
    for value in [65.0, 70.0]:
        item = base.copy()
        item["max_signal_rsi"] = value
        variants.append(item)
    for value in [10.0, 20.0, 30.0]:
        item = base.copy()
        item["max_signal_ema_slope"] = value
        variants.append(item)
    return variants


def build_context_filter_configs(slippage_rt: float) -> list[RuleConfig]:
    configs: list[RuleConfig] = []
    for values in itertools.product(
        ["normal", "normal_tight"],
        ["10:00", "10:10"],
        [5.0],
        [40.0],
        [1.25, 1.5, 2.0],
        ["15:55"],
    ):
        or_class, start, touch_tolerance, stop_points, rr, time_exit = values
        params = base_params(slippage_rt)
        params.update(
            {
                "or_class": or_class,
                "start": start,
                "end": "11:00",
                "touch_tolerance": touch_tolerance,
                "stop_points": stop_points,
                "rr": rr,
                "time_exit": time_exit,
            }
        )
        for variant in one_filter_variants(params):
            configs.append(make_config("or_retest_slip_filter", "long", variant))
    return configs


def build_configs(slippage_rt: float) -> list[RuleConfig]:
    configs = build_timing_shape_configs(slippage_rt) + build_context_filter_configs(slippage_rt)
    by_id = {config.rule_id: config for config in configs}
    return list(by_id.values())


def select_for_sensitivity(summary: pd.DataFrame) -> list[str]:
    if summary.empty:
        return []
    blocks = [
        summary[summary["passes_all_core"]].sort_values(
            ["profit_factor", "total_pnl"], ascending=[False, False]
        ).head(50),
        summary[(summary["profit_factor"] >= 1.5) & (summary["trades"] >= 80)].sort_values(
            ["passes_oos", "profit_factor", "total_pnl"], ascending=[False, False, False]
        ).head(50),
        summary.sort_values(["total_pnl", "profit_factor"], ascending=[False, False]).head(50),
    ]
    return list(dict.fromkeys(pd.concat(blocks)["rule_id"].tolist()))


def run_sensitivity(rule_ids: list[str], rules_tried: int) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for slippage_rt in [1.0, 1.5, 3.0, 5.0]:
        configs = build_configs(slippage_rt)
        by_id = {config.rule_id: config for config in configs}
        selected = [by_id[rule_id] for rule_id in rule_ids if rule_id in by_id]
        if not selected:
            continue
        _trades, summary = run_search(selected, "full_history", rules_tried_for_adjustment=rules_tried)
        if not summary.empty:
            summary = summary.copy()
            summary["slippage_rt"] = slippage_rt
            rows.append(summary)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def robust_rank(sensitivity: pd.DataFrame) -> pd.DataFrame:
    if sensitivity.empty:
        return pd.DataFrame()
    rows = []
    for rule_id, group in sensitivity.groupby("rule_id"):
        by_slip = group.set_index("slippage_rt")
        if not {1.0, 1.5, 3.0, 5.0}.issubset(set(by_slip.index)):
            continue
        rows.append(
            {
                "rule_id": rule_id,
                "trades": int(by_slip.loc[1.0, "trades"]),
                "pnl_1": float(by_slip.loc[1.0, "total_pnl"]),
                "pnl_3": float(by_slip.loc[3.0, "total_pnl"]),
                "pnl_5": float(by_slip.loc[5.0, "total_pnl"]),
                "pf_1": float(by_slip.loc[1.0, "profit_factor"]),
                "pf_1_5": float(by_slip.loc[1.5, "profit_factor"]),
                "pf_3": float(by_slip.loc[3.0, "profit_factor"]),
                "pf_5": float(by_slip.loc[5.0, "profit_factor"]),
                "oos_avg_3": float(by_slip.loc[3.0, "oos_avg"]),
                "oos_avg_5": float(by_slip.loc[5.0, "oos_avg"]),
                "pass_1": bool(by_slip.loc[1.0, "passes_all_core"]),
                "pass_1_5": bool(by_slip.loc[1.5, "passes_all_core"]),
                "pass_3": bool(by_slip.loc[3.0, "passes_all_core"]),
                "pass_5": bool(by_slip.loc[5.0, "passes_all_core"]),
                "max_drawdown_5": float(by_slip.loc[5.0, "max_drawdown"]),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["pass_5", "pass_3", "pf_5", "pnl_5"],
        ascending=[False, False, False, False],
    )


def write_report(summary_5: pd.DataFrame, rank: pd.DataFrame) -> None:
    cols_5 = [
        "trades",
        "total_pnl",
        "avg_pnl",
        "profit_factor",
        "max_drawdown",
        "is_avg",
        "oos_avg",
        "passes_all_core",
        "rule_id",
    ]
    rank_cols = [
        "trades",
        "pnl_1",
        "pnl_3",
        "pnl_5",
        "pf_1",
        "pf_3",
        "pf_5",
        "oos_avg_5",
        "pass_3",
        "pass_5",
        "rule_id",
    ]
    lines = [
        "# Phase 5 — Slippage-Robust Search",
        "",
        "## Method",
        "",
        "- Starts from the causal long OR retest signal found in Phase 4.",
        "- Uses completed-bar confirmation and next-1-minute-open entry.",
        "- Keeps the search to timing, stop/target shape, and one causal context filter at a time.",
        "- Scores the full clean 1-minute history first at 5.0 points round-trip slippage.",
        "- Rechecks selected rules at 1.0, 1.5, 3.0, and 5.0 points round-trip slippage.",
        "",
        "## Top 5-Point Results",
        "",
        summary_5[cols_5].head(20).to_markdown(index=False, floatfmt=".2f")
        if not summary_5.empty
        else "No trades.",
        "",
        "## Robust Rank",
        "",
        rank[rank_cols].head(20).to_markdown(index=False, floatfmt=".2f")
        if not rank.empty
        else "No robust sensitivity set.",
        "",
        "## Decision",
        "",
    ]
    if not rank.empty and bool(rank.iloc[0]["pass_5"]):
        lines.append("At least one variant clears the core gates through 5.0 points round-trip slippage.")
    elif not rank.empty and bool(rank.iloc[0]["pass_3"]):
        lines.append(
            "At least one variant clears the core gates through 3.0 points round-trip slippage, "
            "but no variant clears the full 5.0-point ideal gate."
        )
    else:
        lines.append("No variant clears the core gates through 3.0 points round-trip slippage.")
    (OUT_DIR / "slippage_robust_search.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configs_5 = build_configs(5.0)
    print(f"Testing {len(configs_5):,} robust OR retest configs at 5.0-point slippage")
    trades_5, summary_5 = run_search(
        configs_5,
        "full_history",
        rules_tried_for_adjustment=len(configs_5),
    )
    trades_5.to_csv(OUT_DIR / "full_history_trades_5pt.csv", index=False)
    summary_5.to_csv(OUT_DIR / "full_history_summary_5pt.csv", index=False)

    selected_ids = select_for_sensitivity(summary_5)
    print(f"Running slippage sensitivity for {len(selected_ids):,} selected configs")
    sensitivity = run_sensitivity(selected_ids, rules_tried=len(configs_5))
    sensitivity.to_csv(OUT_DIR / "slippage_sensitivity.csv", index=False)
    rank = robust_rank(sensitivity)
    rank.to_csv(OUT_DIR / "robust_rank.csv", index=False)
    write_report(summary_5, rank)

    if not rank.empty:
        print(rank.head(20).to_string(index=False))
    print(f"Wrote {OUT_DIR / 'slippage_robust_search.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
