"""Fallback: fetch VIX and SPX daily bars from Yahoo Finance.

IBKR demo account doesn't have CBOE/CME subscriptions for VIX/ES historical
data. Yahoo provides free daily data sufficient for regime computation.

Tickers:
  ^VIX = CBOE Volatility Index
  ^GSPC = S&P 500 Index
  ^NDX = Nasdaq-100 Index (more relevant for MNQ)

Saves to:
  data/vix_1d.parquet
  data/spx_1d.parquet
  data/ndx_1d.parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf


def fetch(ticker: str, label: str, out_path: Path):
    print(f"Fetching {ticker} ({label})...")
    df = yf.download(ticker, start="2023-01-01", end="2026-04-30", progress=False, auto_adjust=False)
    if df.empty:
        print(f"  ERROR: no data returned for {ticker}")
        return None
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    if "adj close" in df.columns:
        df = df.drop(columns=["adj close"])
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path)
    print(f"  Saved {len(df)} bars to {out_path}: {df.index.min().date()} -> {df.index.max().date()}")
    print(df.tail(3))
    return df


def main():
    project_root = Path(__file__).parent.parent
    fetch("^VIX", "CBOE Volatility Index", project_root / "data/vix_1d.parquet")
    fetch("^GSPC", "S&P 500", project_root / "data/spx_1d.parquet")
    fetch("^NDX", "Nasdaq-100", project_root / "data/ndx_1d.parquet")


if __name__ == "__main__":
    main()
