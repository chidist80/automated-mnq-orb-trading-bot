"""Fetch daily bars for VIX index and ES (S&P 500 e-mini) from IBKR.

VIX: CBOE volatility index, ticker VIX, secType IND, exchange CBOE.
ES:  S&P 500 e-mini front-month continuous, ticker ES, secType CONTFUT, exchange CME.

Daily bars over 2023-01-01 to today; useRTH=True. Saves to:
  data/vix_1d.parquet
  data/es_1d.parquet
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def connect(host: str, port: int, client_id: int, market_data_type: int):
    from ib_insync import IB
    ib = IB()
    logger.info(f"Connecting to IBKR at {host}:{port} ...")
    ib.connect(host, port, clientId=client_id, timeout=20)
    ib.reqMarketDataType(market_data_type)
    logger.info(f"Connected (marketDataType={market_data_type})")
    return ib


def fetch_index_daily(ib, contract, duration: str = "4 Y") -> pd.DataFrame:
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        timeout=120,
    )
    if not bars:
        logger.error(f"No data returned for {contract}")
        return pd.DataFrame()
    df = pd.DataFrame(bars).rename(columns={"date": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")
    return df.sort_index()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=88)
    parser.add_argument("--market-data-type", type=int, default=3)
    parser.add_argument("--duration", default="4 Y")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    out_vix = project_root / "data/vix_1d.parquet"
    out_es = project_root / "data/es_1d.parquet"

    ib = connect(args.host, args.port, args.client_id, args.market_data_type)
    try:
        from ib_insync import Index, ContFuture

        # VIX index — uses TRADES on CBOE
        vix = Index("VIX", "CBOE", "USD")
        ib.qualifyContracts(vix)
        logger.info("Fetching VIX daily ...")
        vix_df = fetch_index_daily(ib, vix, args.duration)
        if not vix_df.empty:
            # VIX requires whatToShow='HISTORICAL_VOLATILITY' or use TRADES gracefully
            vix_df.to_parquet(out_vix)
            logger.info(f"Saved {len(vix_df)} VIX bars to {out_vix}: {vix_df.index[0].date()} -> {vix_df.index[-1].date()}")
        else:
            # Retry with MIDPOINT for indices that lack TRADES
            logger.warning("Retrying VIX with MIDPOINT...")
            bars = ib.reqHistoricalData(
                vix, endDateTime="", durationStr=args.duration,
                barSizeSetting="1 day", whatToShow="MIDPOINT", useRTH=True,
                formatDate=1, timeout=120,
            )
            if bars:
                df = pd.DataFrame(bars).rename(columns={"date": "timestamp"})
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")[["open", "high", "low", "close"]].copy()
                df["volume"] = 0  # no volume on indices
                if df.index.tz is None:
                    df.index = df.index.tz_localize("US/Eastern")
                df.to_parquet(out_vix)
                logger.info(f"Saved {len(df)} VIX bars to {out_vix} via MIDPOINT")

        # ES continuous future
        es = ContFuture("ES", exchange="CME")
        qualified = ib.qualifyContracts(es)
        if qualified:
            logger.info(f"Fetching ES continuous daily ({qualified[0].localSymbol})...")
            es_df = fetch_index_daily(ib, qualified[0], args.duration)
            if not es_df.empty:
                es_df.to_parquet(out_es)
                logger.info(f"Saved {len(es_df)} ES bars to {out_es}: {es_df.index[0].date()} -> {es_df.index[-1].date()}")
        else:
            logger.error("Could not qualify ES continuous")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
