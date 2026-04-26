"""Fetch MNQ 1-minute bars covering BOTH RTH and ETH (extended hours).

Reuses fetch_data.py infrastructure but sets useRTH=False to capture overnight
session. Saves to data/mnq_full_1m.parquet (separate from RTH-only file).

Usage:
  python scripts/fetch_data_eth.py --port 7497 --months 36
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REQUEST_PAUSE = 12


def connect_ibkr(host, port, client_id, market_data_type):
    from ib_insync import IB
    ib = IB()
    logger.info(f"Connecting to IBKR at {host}:{port} clientId={client_id}")
    ib.connect(host, port, clientId=client_id, timeout=20)
    ib.reqMarketDataType(market_data_type)
    return ib


def get_mnq_quarterly(ib, months_back):
    from ib_insync import Future
    end = datetime.now()
    start = end - timedelta(days=months_back * 31)
    raw = []
    for year in range(start.year - 1, end.year + 2):
        for month in (3, 6, 9, 12):
            expiry_approx = datetime(year, month, 15)
            life_start = expiry_approx - timedelta(days=270)
            if expiry_approx < start or life_start > end:
                continue
            ym = f"{year}{month:02d}"
            c = Future("MNQ", lastTradeDateOrContractMonth=ym, exchange="CME")
            c.includeExpired = True
            raw.append(c)
    qualified = []
    for c in raw:
        try:
            q = ib.qualifyContracts(c)
        except Exception:
            continue
        if q:
            qualified.append(q[0])
            logger.info(f"  Qualified: {q[0].localSymbol}")
    qualified.sort(key=lambda c: c.lastTradeDateOrContractMonth)
    return qualified


def fetch_chunk_eth(ib, contract, end_dt, duration="1 M"):
    end_str = "" if end_dt is None else end_dt.strftime("%Y%m%d-%H:%M:%S")
    logger.info(f"  {contract.localSymbol}: {duration} ETH ending {end_str or 'now'}")
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_str,
        durationStr=duration,
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False,  # KEY DIFFERENCE
        formatDate=1,
        timeout=180,
    )
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars).rename(columns={"date": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")
    logger.info(f"  Got {len(df)} bars: {df.index[0]} -> {df.index[-1]}")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=77)
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--market-data-type", type=int, default=3)
    parser.add_argument("--output", default="data/mnq_full_1m.parquet")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    out = project_root / args.output
    ib = connect_ibkr(args.host, args.port, args.client_id, args.market_data_type)
    try:
        contracts = get_mnq_quarterly(ib, args.months)
        cutoff_start = datetime.now() - timedelta(days=args.months * 31)
        cutoff_end = datetime.now()
        coverage_start = cutoff_start

        chunks = []
        for contract in contracts:
            ym = contract.lastTradeDateOrContractMonth[:6]
            if len(contract.lastTradeDateOrContractMonth) >= 8:
                expiry_dt = datetime.strptime(contract.lastTradeDateOrContractMonth[:8], "%Y%m%d") + timedelta(days=1)
            else:
                expiry_dt = datetime(int(ym[:4]), int(ym[4:6]), 28)
            contract_start = coverage_start
            contract_end = min(expiry_dt, cutoff_end)
            if contract_end <= contract_start:
                continue
            cursor = contract_end
            consecutive_empty = 0
            while cursor > contract_start and consecutive_empty < 3:
                chunk = fetch_chunk_eth(ib, contract, cursor, duration="1 M")
                if not chunk.empty:
                    chunks.append(chunk)
                    earliest = chunk.index[0].tz_convert("US/Eastern").tz_localize(None)
                    cursor = min(cursor - timedelta(seconds=1), earliest - timedelta(seconds=1))
                    consecutive_empty = 0
                else:
                    cursor = cursor - timedelta(days=31)
                    consecutive_empty += 1
                if cursor > contract_start:
                    time.sleep(REQUEST_PAUSE)
            coverage_start = max(coverage_start, contract_end)

        if not chunks:
            logger.error("No data fetched")
            sys.exit(1)
        combined = pd.concat(chunks)
        combined = combined[~combined.index.duplicated(keep="first")].sort_index()
        out.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(out)
        logger.info(f"Saved {len(combined)} bars to {out}: {combined.index[0]} -> {combined.index[-1]}")
        # Quick stats
        eth_bars = combined.between_time("16:00", "09:29")
        rth_bars = combined.between_time("09:30", "15:59")
        logger.info(f"ETH bars (16:00-09:29): {len(eth_bars):,}")
        logger.info(f"RTH bars (09:30-15:59): {len(rth_bars):,}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
