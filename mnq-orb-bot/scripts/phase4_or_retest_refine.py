"""
Phase 4 — focused OR retest refinement.

Phase 3 showed one near-miss: causal long OR retests are stable and frequent,
but the unconditional PF is too weak. This script tests the plausible refinement
directly: long retests on normal/non-wide OR days, especially after 10:00 ET,
with a small set of exit shapes.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from phase3_autonomous_edge_search import (  # type: ignore
    DEFAULT_ROUND_TRIP_SLIPPAGE_POINTS,
    OUT_DIR as PHASE3_OUT,
    RuleConfig,
    run_search,
)

OUT_DIR = PROJECT_ROOT / "research" / "human_edge_replay" / "phase4_or_retest"


def build_refined_configs(slippage_rt: float) -> list[RuleConfig]:
    configs: list[RuleConfig] = []
    for values in itertools.product(
        ["long"],
        ["all", "normal", "normal_tight", "not_wide"],
        ["10:00"],
        ["10:30", "11:00"],
        [5.0],
        ["break_prev"],
        ["fixed", "or_fraction"],
        ["rr", "fixed"],
        ["12:00", "15:55"],
    ):
        (
            direction,
            or_class,
            start,
            end,
            touch_tolerance,
            confirm_pattern,
            stop_mode,
            target_mode,
            time_exit,
        ) = values
        if start >= end:
            continue
        for stop_points in ([20.0, 30.0, 40.0] if stop_mode == "fixed" else [np.nan]):
            for or_stop_fraction in ([0.25] if stop_mode == "or_fraction" else [np.nan]):
                for rr in ([1.25, 1.5, 2.0] if target_mode == "rr" else [np.nan]):
                    for target_points in ([30.0, 45.0] if target_mode == "fixed" else [np.nan]):
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
                            "time_exit": time_exit,
                            "min_reward_points": 8.0,
                            "max_risk_points": 120.0,
                            "slippage_rt": slippage_rt,
                        }
                        rule_id = compact_rule_id("or_retest_refine", direction, params)
                        configs.append(RuleConfig(rule_id, "or_retest", direction, params))
    return configs


def compact_rule_id(prefix: str, direction: str, params: dict) -> str:
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


def write_report(human: pd.DataFrame, full: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = [
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
    lines = [
        "# Phase 4 — OR Retest Refinement",
        "",
        "## Human Window",
        "",
        human[cols].head(20).to_markdown(index=False, floatfmt=".2f") if not human.empty else "No trades.",
        "",
        "## Full History",
        "",
        full[cols].head(20).to_markdown(index=False, floatfmt=".2f") if not full.empty else "No trades.",
        "",
        "## Decision",
        "",
    ]
    passed = full[full["passes_all_core"]] if not full.empty else pd.DataFrame()
    if passed.empty:
        lines.append("No refined OR retest rule clears the core autonomous edge gates.")
    else:
        lines.append("At least one refined OR retest rule clears the core gates; run slippage sensitivity next.")
    (OUT_DIR / "or_retest_refinement.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configs = build_refined_configs(DEFAULT_ROUND_TRIP_SLIPPAGE_POINTS)
    print(f"Testing {len(configs):,} refined OR retest configs")
    human_trades, human_summary = run_search(
        configs,
        "human_window",
        rules_tried_for_adjustment=len(configs),
    )
    # Confirm only human-window positive/stable candidates plus broad top-P&L candidates.
    if human_summary.empty:
        selected = []
    else:
        c = human_summary[(human_summary["trades"] >= 20) & (human_summary["total_pnl"] > 0)]
        c = pd.concat(
            [
                c[c["passes_oos"]].sort_values("profit_factor", ascending=False).head(150),
                c.sort_values("total_pnl", ascending=False).head(150),
            ]
        ).drop_duplicates("rule_id")
        by_id = {config.rule_id: config for config in configs}
        selected = [by_id[rule_id] for rule_id in c["rule_id"] if rule_id in by_id]

    print(f"Confirming {len(selected):,} refined configs on full history")
    full_trades, full_summary = run_search(
        selected,
        "full_history",
        rules_tried_for_adjustment=len(configs),
    )

    human_trades.to_csv(OUT_DIR / "human_window_trades.csv", index=False)
    human_summary.to_csv(OUT_DIR / "human_window_summary.csv", index=False)
    full_trades.to_csv(OUT_DIR / "full_history_trades.csv", index=False)
    full_summary.to_csv(OUT_DIR / "full_history_summary.csv", index=False)
    write_report(human_summary, full_summary)

    print("Top full-history refined candidates:")
    if not full_summary.empty:
        print(
            full_summary.head(12)[
                ["trades", "total_pnl", "profit_factor", "passes_all_core", "rule_id"]
            ].to_string(index=False)
        )
    print(f"Wrote {OUT_DIR / 'or_retest_refinement.md'}")
    print(f"Phase 3 output reference: {PHASE3_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
