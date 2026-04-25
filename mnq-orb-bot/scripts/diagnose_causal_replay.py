"""Generate data-quality and Tier 2 directional diagnostics for causal replay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    TIER1_PILOT,
    TIER2_7500,
    build_day_contexts,
    load_1m_parquet,
    paper_forward_log,
    summarize_decisions,
)


RETURN_BINS = [-np.inf, -0.01, -0.0025, 0.0, 0.0025, 0.01, np.inf]
RETURN_LABELS = ["<= -1%", "-1% to -0.25%", "-0.25% to 0%", "0% to 0.25%", "0.25% to 1%", ">= 1%"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose causal OR-retest replay inputs and Tier 2 profile")
    parser.add_argument("--data", default="data/mnq_1m.parquet", help="Path to 1-minute OHLCV parquet")
    parser.add_argument(
        "--out-root",
        default="research/human_edge_replay",
        help="Research output root",
    )
    return parser.parse_args()


def build_quality_rows(contexts) -> pd.DataFrame:
    rows = []
    for ctx in contexts:
        or_window = ctx.day.between_time("09:30", "09:44")
        rows.append(
            {
                "date": ctx.date,
                "clean": ctx.clean,
                "quality": ctx.quality,
                "bars": len(ctx.day),
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
                "first_timestamp": ctx.day.index[0].isoformat() if not ctx.day.empty else "",
                "last_timestamp": ctx.day.index[-1].isoformat() if not ctx.day.empty else "",
                "or_zero_volume_bars": int((or_window["volume"] == 0).sum()) if not or_window.empty else 0,
                "or_total_volume": float(or_window["volume"].sum()) if not or_window.empty else np.nan,
                "or_size": ctx.or_size,
                "or_class": ctx.or_class,
                "or_history_count": ctx.or_history_count,
            }
        )
    return pd.DataFrame(rows)


def day_return_map(contexts) -> dict[str, float]:
    returns = {}
    for ctx in contexts:
        if ctx.day.empty:
            returns[ctx.date] = np.nan
            continue
        returns[ctx.date] = float(ctx.day.iloc[-1]["close"] / ctx.day.iloc[0]["open"] - 1.0)
    return returns


def summarize_by_return_bucket(trades: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    work = trades.copy()
    work["return_bucket"] = pd.cut(work["rth_return"], bins=RETURN_BINS, labels=RETURN_LABELS)
    rows = []
    for bucket, group in work.groupby("return_bucket", observed=False):
        pnl = group[f"{prefix}_net_pnl"]
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl <= 0].sum())
        rows.append(
            {
                "bucket": str(bucket),
                "trades": int(len(group)),
                "total_pnl": float(pnl.sum()),
                "avg_pnl": float(pnl.mean()) if len(group) else np.nan,
                "win_rate": float((pnl > 0).mean()) if len(group) else np.nan,
                "pf": float(wins / losses) if losses else np.inf,
            }
        )
    return pd.DataFrame(rows)


def summarize_by_direction(trades: pd.DataFrame, prefix: str) -> pd.DataFrame:
    work = trades.copy()
    work["day_type"] = np.where(work["rth_return"] >= 0, "up_day", "down_day")
    rows = []
    for day_type, group in work.groupby("day_type"):
        pnl = group[f"{prefix}_net_pnl"]
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl <= 0].sum())
        rows.append(
            {
                "day_type": day_type,
                "trades": int(len(group)),
                "total_pnl": float(pnl.sum()),
                "avg_pnl": float(pnl.mean()),
                "win_rate": float((pnl > 0).mean()),
                "pf": float(wins / losses) if losses else np.inf,
            }
        )
    return pd.DataFrame(rows)


def write_markdown(
    quality: pd.DataFrame,
    log: pd.DataFrame,
    tier2_direction: pd.DataFrame,
    tier2_buckets: pd.DataFrame,
    out_path: Path,
) -> None:
    tier1_summary = summarize_decisions(log, "tier1")
    tier2_summary = summarize_decisions(log, "tier2_shadow")
    lines = [
        "# Causal Replay Diagnostics",
        "",
        "## Data Quality",
        "",
        f"- RTH dates: {len(quality):,}",
        f"- OR-clean dates: {int(quality['clean'].sum()):,}",
        f"- OR-invalid dates: {int((~quality['clean']).sum()):,}",
        f"- Full-session clean dates: {int(quality['full_session_clean'].sum()):,}",
        f"- OR-clean dates with full-session issues: "
        f"{int((quality['clean'] & ~quality['full_session_clean']).sum()):,}",
        "",
        "### OR Tradability Gate",
        "",
        quality["quality"].value_counts().rename_axis("quality").reset_index(name="days").to_markdown(index=False),
        "",
        "### Full-Session Quality",
        "",
        quality["full_session_quality"]
        .value_counts()
        .rename_axis("full_session_quality")
        .reset_index(name="days")
        .to_markdown(index=False),
        "",
        "## Frozen Replay Check",
        "",
        (
            f"- Tier 1: trades={tier1_summary['trades']}, "
            f"pnl=${tier1_summary['total_pnl']:.2f}, pf={tier1_summary['pf']:.4f}"
        ),
        (
            f"- Tier 2 shadow: trades={tier2_summary['trades']}, "
            f"pnl=${tier2_summary['total_pnl']:.2f}, pf={tier2_summary['pf']:.4f}"
        ),
        "",
        "## Tier 2 Directional Profile",
        "",
        tier2_direction.to_markdown(index=False) if not tier2_direction.empty else "No Tier 2 trades.",
        "",
        "## Tier 2 RTH Return Buckets",
        "",
        tier2_buckets.to_markdown(index=False) if not tier2_buckets.empty else "No Tier 2 trades.",
        "",
        "Rules are diagnostic only. No strategy thresholds are changed by this report.",
        "",
    ]
    out_path.write_text("\n".join(lines))


def main() -> int:
    args = parse_args()
    data_path = PROJECT_ROOT / args.data
    out_root = PROJECT_ROOT / args.out_root

    df = load_1m_parquet(data_path)
    contexts = build_day_contexts(df)
    quality = build_quality_rows(contexts)
    log = paper_forward_log(df, tier1=TIER1_PILOT, tier2=TIER2_7500)
    returns = day_return_map(contexts)
    log["rth_return"] = log["date"].map(returns)

    data_quality_dir = out_root / "data_quality"
    tier2_dir = out_root / "phase5_slippage_robust"
    data_quality_dir.mkdir(parents=True, exist_ok=True)
    tier2_dir.mkdir(parents=True, exist_ok=True)

    quality.to_csv(data_quality_dir / "causal_replay_day_quality.csv", index=False)
    quality["quality"].value_counts().rename_axis("quality").reset_index(name="days").to_csv(
        data_quality_dir / "causal_replay_quality_counts.csv", index=False
    )
    quality["full_session_quality"].value_counts().rename_axis("full_session_quality").reset_index(
        name="days"
    ).to_csv(data_quality_dir / "causal_replay_full_session_quality_counts.csv", index=False)

    tier2_trades = log[log["tier2_shadow_eligible"]].copy()
    tier2_direction = summarize_by_direction(tier2_trades, "tier2_shadow")
    tier2_buckets = summarize_by_return_bucket(tier2_trades, "tier2_shadow")
    tier2_direction.to_csv(tier2_dir / "tier2_directional_profile.csv", index=False)
    tier2_buckets.to_csv(tier2_dir / "tier2_return_bucket_profile.csv", index=False)

    write_markdown(
        quality,
        log,
        tier2_direction,
        tier2_buckets,
        data_quality_dir / "causal_replay_diagnostics.md",
    )
    print(f"Wrote diagnostics under {data_quality_dir} and {tier2_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
