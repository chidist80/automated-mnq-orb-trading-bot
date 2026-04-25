"""Generate the Phase 6 causal OR-retest paper-forward log.

This script does not place trades. It replays local 1-minute bars and records
the executable Tier 1 decision plus Tier 2/A+ shadow decisions for each RTH day.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.causal_or_retest import (  # noqa: E402
    A_PLUS_SHADOW,
    TIER1_PILOT,
    TIER2_7500,
    load_1m_parquet,
    paper_forward_log,
    summarize_decisions,
)

JOURNAL_KEY_COLUMNS = ("date", "rule_version")
JOURNAL_VOLATILE_COLUMNS = {"run_timestamp_utc"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run causal OR-retest paper-forward replay")
    parser.add_argument("--data", default="data/mnq_1m.parquet", help="Path to 1-minute OHLCV parquet")
    parser.add_argument(
        "--out",
        default="research/human_edge_replay/paper_forward/paper_forward_log.csv",
        help="Output CSV path",
    )
    parser.add_argument("--start", help="Optional start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date, YYYY-MM-DD")
    parser.add_argument(
        "--mode",
        choices=["regenerate", "append"],
        default="regenerate",
        help="regenerate overwrites the output; append adds non-duplicate date/rule_version rows",
    )
    parser.add_argument(
        "--require-full-session-clean",
        action="store_true",
        help=(
            "Opt-in strict mode: require full-session clean diagnostics before rule evaluation. "
            "Default preserves frozen OR-only historical replay parity."
        ),
    )
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_normalized(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(frame.to_csv(index=False)))


def _values_equal(existing_value, incoming_value) -> bool:
    if pd.isna(existing_value) and pd.isna(incoming_value):
        return True
    if pd.isna(existing_value) or pd.isna(incoming_value):
        return False
    return existing_value == incoming_value


def _format_conflict_value(value) -> str:
    if pd.isna(value):
        return "<NA>"
    text = repr(value)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _journal_conflicts(existing_row: pd.Series, incoming_row: pd.Series) -> list[tuple[str, object, object]]:
    conflicts = []
    for col in existing_row.index:
        if col in JOURNAL_KEY_COLUMNS or col in JOURNAL_VOLATILE_COLUMNS:
            continue
        if not _values_equal(existing_row[col], incoming_row[col]):
            conflicts.append((col, existing_row[col], incoming_row[col]))
    return conflicts


def append_journal(new_rows: pd.DataFrame, out_path: Path) -> tuple[pd.DataFrame, int, int]:
    """Append immutable date/rule_version rows and reject conflicting evidence."""
    if not out_path.exists():
        new_rows.to_csv(out_path, index=False)
        return new_rows.copy(), 0, len(new_rows)

    existing = pd.read_csv(out_path)
    if list(existing.columns) != list(new_rows.columns):
        raise ValueError("Existing journal schema differs from new rows; use regenerate or a new journal path")

    for col in JOURNAL_KEY_COLUMNS:
        if col not in new_rows.columns:
            raise ValueError(f"Missing required journal key column: {col}")

    comparable_new = _csv_normalized(new_rows)
    existing_keys = existing.assign(
        _date=existing["date"].astype(str),
        _rule_version=existing["rule_version"].astype(str),
    )
    if existing_keys.duplicated(["_date", "_rule_version"]).any():
        duplicate = existing_keys.loc[existing_keys.duplicated(["_date", "_rule_version"], keep=False)].iloc[0]
        raise ValueError(
            "Existing journal contains duplicate immutable key "
            f"date={duplicate['_date']} rule_version={duplicate['_rule_version']}"
        )

    existing_by_key = {
        (str(row["date"]), str(row["rule_version"])): row for _, row in existing.iterrows()
    }

    keep_mask = []
    skipped = 0
    for idx, incoming_row in comparable_new.iterrows():
        key = (str(incoming_row["date"]), str(incoming_row["rule_version"]))
        existing_row = existing_by_key.get(key)
        if existing_row is None:
            keep_mask.append(True)
            continue

        conflicts = _journal_conflicts(existing_row, incoming_row)
        if conflicts:
            details = "; ".join(
                f"{col}: existing={_format_conflict_value(existing_value)} "
                f"incoming={_format_conflict_value(incoming_value)}"
                for col, existing_value, incoming_value in conflicts[:8]
            )
            if len(conflicts) > 8:
                details += f"; ... {len(conflicts) - 8} more differing columns"
            raise ValueError(
                "Conflicting journal row for immutable key "
                f"date={key[0]} rule_version={key[1]}: {details}"
            )

        keep_mask.append(False)
        skipped += 1

    additions = new_rows.loc[keep_mask].copy()
    if not additions.empty:
        additions.to_csv(out_path, mode="a", header=False, index=False)
    combined = pd.concat([existing, additions], ignore_index=True, sort=False)
    return combined, skipped, len(additions)


def main() -> int:
    args = parse_args()
    data_path = PROJECT_ROOT / args.data
    out_path = PROJECT_ROOT / args.out
    df = load_1m_parquet(data_path)
    metadata = {
        "data_file_hash": file_sha256(data_path),
        "data_last_timestamp": df.index.max().isoformat() if not df.empty else "",
        "run_timestamp_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }

    log = paper_forward_log(
        df,
        tier1=TIER1_PILOT,
        tier2=TIER2_7500,
        a_plus=A_PLUS_SHADOW,
        start=args.start,
        end=args.end,
        metadata=metadata,
        require_full_session_clean=args.require_full_session_clean,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "append":
        log, skipped, added = append_journal(log, out_path)
        print(f"Append mode: added={added:,} skipped_duplicates={skipped:,}")
    else:
        log.to_csv(out_path, index=False)

    print(f"Wrote {len(log):,} daily rows to {out_path} ({args.mode})")
    if args.require_full_session_clean:
        print("Strict full-session clean mode enabled")
    quality_warnings = log[log["clean"] & ~log["full_session_clean"]]
    if not quality_warnings.empty:
        print(
            f"WARNING: {len(quality_warnings):,} OR-clean rows have full-session quality issues; "
            "see full_session_* columns."
        )
        print(quality_warnings["full_session_quality"].value_counts().to_string())
    for prefix in ["tier1", "tier2_shadow", "a_plus_shadow"]:
        summary = summarize_decisions(log, prefix)
        print(
            f"{prefix}: trades={summary['trades']} "
            f"pnl={summary['total_pnl']:.2f} "
            f"avg={summary['avg_pnl']:.2f} "
            f"win_rate={summary['win_rate']:.2%} "
            f"pf={summary['pf']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
