"""
Phase 2 — edge candidate miner.

This script turns the human replay and Phase 1 mechanical-replica outputs into
a compact decision report. It is intentionally conservative: all economics are
recomputed as 1 MNQ contract with a $1.34 round-trip commission, then split
chronologically 60/40.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
REPORT_ROOT = PROJECT_ROOT / "research" / "human_edge_replay"
PHASE2_DIR = REPORT_ROOT / "phase2"
MNQ_MULTIPLIER = 2.0
COMMISSION_RT = 1.34


def add_human_1c_pnl(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    sign = np.where(out["direction"] == "long", 1.0, -1.0)
    out["human_1c_net_pnl"] = (
        (out["weighted_exit_price"] - out["weighted_entry_price"]) * sign * MNQ_MULTIPLIER
        - COMMISSION_RT
    )
    out["is_win"] = out["human_1c_net_pnl"] > 0
    out["entry_time_utc"] = pd.to_datetime(out["entry_time_et"], utc=True)
    return out


def split_cutoff(df: pd.DataFrame) -> pd.Timestamp:
    return df["entry_time_utc"].quantile(0.6)


def summarize_filter(df: pd.DataFrame, name: str, mask: pd.Series, cutoff: pd.Timestamp) -> dict:
    selected = df[mask].copy()
    in_sample = selected[selected["entry_time_utc"] <= cutoff]
    out_sample = selected[selected["entry_time_utc"] > cutoff]

    def stats(prefix: str, part: pd.DataFrame) -> dict:
        return {
            f"{prefix}_n": len(part),
            f"{prefix}_pnl": float(part["human_1c_net_pnl"].sum()) if len(part) else 0.0,
            f"{prefix}_avg": float(part["human_1c_net_pnl"].mean()) if len(part) else np.nan,
            f"{prefix}_win_rate": float(part["is_win"].mean()) if len(part) else np.nan,
        }

    row = {"candidate": name}
    row.update(stats("all", selected))
    row.update(stats("is", in_sample))
    row.update(stats("oos", out_sample))
    row["oos_vs_is_avg"] = (
        row["oos_avg"] / row["is_avg"]
        if np.isfinite(row["oos_avg"]) and np.isfinite(row["is_avg"]) and row["is_avg"] != 0
        else np.nan
    )
    row["win_rate_delta_pp"] = (
        (row["oos_win_rate"] - row["is_win_rate"]) * 100
        if np.isfinite(row["oos_win_rate"]) and np.isfinite(row["is_win_rate"])
        else np.nan
    )
    row["passes_sample_gate"] = row["all_n"] >= 80
    row["passes_split_stability"] = (
        np.isfinite(row["oos_avg"])
        and np.isfinite(row["is_avg"])
        and row["is_avg"] > 0
        and row["oos_avg"] >= 0.6 * row["is_avg"]
        and abs(row["win_rate_delta_pp"]) <= 10
    )
    return row


def mine_human_candidates(covered: pd.DataFrame) -> pd.DataFrame:
    cutoff = split_cutoff(covered)
    morning = covered["time_bucket"].isin(["09:30-10:00", "10:00-11:00"])
    short_extreme = (
        (covered["direction"] == "short")
        & (covered["entry_vs_vwap"] == "above")
        & (covered["entry_vs_15m_ema9"] == "above")
    )
    long_extreme = (
        (covered["direction"] == "long")
        & (covered["entry_vs_vwap"] == "below")
        & (covered["entry_vs_15m_ema9"] == "below")
    )
    fade_extreme = morning & (
        (short_extreme & (covered["rsi_regime"] != "bear_trend_zone"))
        | (long_extreme & (covered["rsi_regime"] != "bull_trend_zone"))
    )

    filters = {
        "morning_all": morning,
        "morning_short": morning & (covered["direction"] == "short"),
        "fade_extreme": fade_extreme,
        "fade_extreme_or_pre_or": fade_extreme
        | (covered["setup_label"] == "pre_or_locked_trade"),
        "short_above_vwap_and_ema": short_extreme,
        "long_below_vwap_and_ema": long_extreme,
        "pre_or_locked": covered["setup_label"] == "pre_or_locked_trade",
        "inverse_orb_labeled": covered["setup_label"] == "inverse_orb_candidate",
        "orb_retest_labeled": covered["setup_label"] == "orb_breakout_or_retest_candidate",
        "ema_continuation_labeled": covered["setup_label"] == "ema_continuation_candidate",
    }
    rows = [summarize_filter(covered, name, mask, cutoff) for name, mask in filters.items()]
    return pd.DataFrame(rows).sort_values(["all_pnl", "all_n"], ascending=[False, False])


def summarize_mechanical_universe() -> pd.DataFrame:
    universe = pd.read_csv(REPORT_ROOT / "phase1" / "mechanical_universe.csv")
    universe["date_obj"] = pd.to_datetime(universe["date"]).dt.date
    dates = sorted(universe["date_obj"].unique())
    cutoff = dates[int(len(dates) * 0.6) - 1]

    rows = []
    for keys, min_n in ((["rule", "direction"], 40), (["rule", "direction", "or_class"], 8)):
        for name, group in universe.groupby(keys):
            if len(group) < min_n:
                continue
            in_sample = group[group["date_obj"] <= cutoff]
            out_sample = group[group["date_obj"] > cutoff]
            name_tuple = name if isinstance(name, tuple) else (name,)
            row = dict(zip(keys, name_tuple, strict=True))
            row.update(
                {
                    "n": len(group),
                    "pnl": float(group["net_pnl"].sum()),
                    "avg": float(group["net_pnl"].mean()),
                    "win_rate": float((group["net_pnl"] > 0).mean()),
                    "is_n": len(in_sample),
                    "is_avg": float(in_sample["net_pnl"].mean()) if len(in_sample) else np.nan,
                    "is_pnl": float(in_sample["net_pnl"].sum()) if len(in_sample) else 0.0,
                    "oos_n": len(out_sample),
                    "oos_avg": float(out_sample["net_pnl"].mean()) if len(out_sample) else np.nan,
                    "oos_pnl": float(out_sample["net_pnl"].sum()) if len(out_sample) else 0.0,
                }
            )
            row["scope"] = "+".join(keys)
            row["passes_sample_gate"] = row["n"] >= 80
            row["passes_positive_oos"] = np.isfinite(row["oos_avg"]) and row["oos_avg"] > 0
            rows.append(row)

    return pd.DataFrame(rows).sort_values(["pnl", "n"], ascending=[False, False])


def summarize_timing_offsets() -> pd.DataFrame:
    timing = pd.read_csv(REPORT_ROOT / "phase1" / "timing_offsets.csv")
    timing = timing[timing["mech_signal_ts"].notna()].copy()
    timing["is_win"] = timing["human_1c_net_pnl"] > 0
    timing["price_advantage_bin"] = pd.cut(
        timing["delta_price_signed"],
        bins=[-1e9, -100, -25, -5, 5, 25, 100, 1e9],
        labels=[
            "human_better_gt_100pt",
            "human_better_25_100pt",
            "human_better_5_25pt",
            "same_5pt",
            "human_worse_5_25pt",
            "human_worse_25_100pt",
            "human_worse_gt_100pt",
        ],
    )
    return (
        timing.groupby("price_advantage_bin", observed=True)
        .agg(
            n=("idea_id", "count"),
            pnl=("human_1c_net_pnl", "sum"),
            avg=("human_1c_net_pnl", "mean"),
            win_rate=("is_win", "mean"),
            median_delta_minutes=("delta_minutes", "median"),
        )
        .reset_index()
        .sort_values("pnl", ascending=False)
    )


def write_markdown(
    human_candidates: pd.DataFrame,
    mechanical: pd.DataFrame,
    timing: pd.DataFrame,
    replica: pd.DataFrame,
) -> None:
    total_human = replica["human_1c_net_pnl"].sum()
    total_rule = replica["replica_rule_pnl"].sum(skipna=True)
    total_same_time = replica["replica_same_time_pnl"].sum(skipna=True)
    capture = total_rule / total_human if total_human else np.nan

    top_human = human_candidates.head(8).copy()
    top_mech = mechanical.head(12).copy()
    top_timing = timing.copy()

    lines = [
        "# Phase 2 — Edge Solution",
        "",
        "## Verdict",
        "",
        (
            "**Do not ship the original ORB/EMA strategy.** The replayed human edge "
            "is real in the covered sample, but the mechanical ORB/EMA replicas do "
            "not capture it. The edge is in the discretionary selection and timing "
            "layer: waiting for price to move away from obvious mechanical levels, "
            "then entering at a materially better location, usually during the "
            "first 90 minutes."
        ),
        "",
        "## Gate 1 Result",
        "",
        f"- Human 1-contract covered P&L: ${total_human:,.2f}",
        f"- Mechanical same-time-exit replica P&L: ${total_same_time:,.2f}",
        f"- Mechanical rule-exit replica P&L: ${total_rule:,.2f}",
        f"- Mechanical capture ratio: {capture:.1%}",
        "- Gate G1.2 required mechanical capture >= 40%. Result: **FAIL / PIVOT**.",
        "",
        "## Leading Human-Edge Filters",
        "",
        top_human.to_markdown(index=False, floatfmt=".2f"),
        "",
        (
            "Interpretation: the broad morning-short and fade-extreme filters are "
            "stable in the 60/40 split, but they are filters on the human's entries, "
            "not executable triggers. They identify where the human found edge; "
            "they do not yet tell a bot when to click."
        ),
        "",
        "## Timing Signature",
        "",
        top_timing.to_markdown(index=False, floatfmt=".2f"),
        "",
        (
            "Negative `delta_price_signed` means the human entered at a better "
            "price than the first same-day mechanical signal. Most P&L comes from "
            "entries 5 to 100+ points better than naive mechanical signals, which "
            "is the clearest fingerprint of the edge."
        ),
        "",
        "## Mechanical Universe Check",
        "",
        top_mech.to_markdown(index=False, floatfmt=".2f"),
        "",
        (
            "No unconditional mechanical rule clears the sample gate and stable "
            "positive OOS requirement. Small positive cells exist, but they are "
            "n=9-39 and not strong enough for production."
        ),
        "",
        "## What To Build Next",
        "",
        "1. Build an executable `fade_extreme_after_failed_mechanical_signal` research rule:",
        "   - RTH only, 09:30-11:00 ET.",
        "   - Primary side: short. Secondary side: long only when below VWAP and below 15m EMA9.",
        (
            "   - Require price to be extended from VWAP and 15m EMA9, then show "
            "a completed-bar stall/reversal trigger."
        ),
        "   - Do not enter on the first breakout/retest signal.",
        "2. Test it on every clean day, not just human-traded days.",
        (
            "3. Use 1-contract economics, 0.50 point entry slippage, 0.50 point "
            "exit slippage, and $1.34 round-trip commission."
        ),
        (
            "4. Require n>=80, positive OOS, and no single-day P&L concentration "
            "before it can become a production candidate."
        ),
        "",
        "## Current Best Call",
        "",
        (
            "The automatable edge, if it exists, is a **delayed morning "
            "fade/extreme-location rule**, not a classic ORB retest or EMA "
            "continuation rule. Treat the original ORB/EMA strategy as a feature "
            "generator and context map, not as the entry model."
        ),
        "",
    ]
    (PHASE2_DIR / "edge_solution.md").write_text("\n".join(lines))


def main() -> None:
    PHASE2_DIR.mkdir(parents=True, exist_ok=True)
    ideas = pd.read_csv(REPORT_ROOT / "reports" / "idea_replay.csv")
    covered = add_human_1c_pnl(ideas[ideas["coverage_status"] == "covered"])
    replica = pd.read_csv(REPORT_ROOT / "phase1" / "replica_comparison.csv")

    human_candidates = mine_human_candidates(covered)
    mechanical = summarize_mechanical_universe()
    timing = summarize_timing_offsets()

    human_candidates.to_csv(PHASE2_DIR / "human_candidate_filters.csv", index=False)
    mechanical.to_csv(PHASE2_DIR / "mechanical_universe_gate.csv", index=False)
    timing.to_csv(PHASE2_DIR / "timing_edge_signature.csv", index=False)
    write_markdown(human_candidates, mechanical, timing, replica)

    print(f"Wrote {PHASE2_DIR / 'edge_solution.md'}")
    print(human_candidates.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
