"""
Fetch MNQ historical data from IBKR.

IBKR limits historical data requests to ~1 year per call and enforces
pacing rules (max 60 requests in 10 minutes). This script handles:
  - Chunked downloads (3 months at a time to stay well within limits)
  - Automatic contract rollover (MNQ is quarterly: H/M/U/Z)
  - Deduplication across chunks
  - Saves to Parquet, defaulting to data/mnq_15m.parquet

Requirements:
  - TWS or IB Gateway running with API enabled on the configured port
  - Market data: live CME MNQ subscription (live/paper) OR delayed mode (demo)

Usage:
    python scripts/fetch_data.py                    # Default: 18 months, delayed data
    python scripts/fetch_data.py --months 24        # Custom duration
    python scripts/fetch_data.py --bar-size "1 min" --output data/mnq_1m.parquet
    python scripts/fetch_data.py --port 4002        # IB Gateway port
    python scripts/fetch_data.py --market-data-type 1   # Live (funded paper/live only)
    python scripts/fetch_data.py --output data/mnq_15m_custom.parquet

Account compatibility:
  - DUO demo accounts: must use --market-data-type 3 (default). Live (1) returns no data.
  - DU paper / U live with CME subscription: --market-data-type 1 for real-time.
"""

import argparse
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# IBKR pacing: sleep between requests to avoid "Pacing violation"
REQUEST_PAUSE_SECONDS = 12


def connect_ibkr(host: str, port: int, client_id: int, market_data_type: int = 3):
    """Connect to TWS/Gateway and return IB instance.

    market_data_type: 1=live, 2=frozen, 3=delayed, 4=delayed-frozen.
    Demo (DUO) accounts must use 3 — they cannot subscribe to live data.
    """
    try:
        from ib_insync import IB
    except ImportError:
        logger.error("ib_insync not installed. Run: pip install ib_insync")
        sys.exit(1)

    ib = IB()
    logger.info(f"Connecting to IBKR at {host}:{port} (clientId={client_id})...")
    try:
        ib.connect(host, port, clientId=client_id, timeout=20)
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        logger.error("Make sure TWS or IB Gateway is running with API enabled.")
        sys.exit(1)

    ib.reqMarketDataType(market_data_type)
    logger.info(f"Connected to IBKR (marketDataType={market_data_type}).")
    return ib


def get_mnq_continuous_contract(ib):
    """Return a qualified MNQ continuous future (front-month auto-stitched).

    ContFuture rejects endDateTime in reqHistoricalData, so this contract is
    only usable in continuous mode (single request up to "now").
    """
    from ib_insync import ContFuture

    contract = ContFuture("MNQ", exchange="CME")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        logger.error("Failed to qualify MNQ continuous future.")
        sys.exit(1)
    logger.info(f"  Qualified continuous: {qualified[0]}")
    return qualified[0]


def fetch_continuous(ib, contract, duration: str, bar_size: str) -> pd.DataFrame:
    """Single request against ContFuture with no endDateTime."""
    logger.info(f"  Requesting {duration} continuous {bar_size} bars (ending now)...")
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        timeout=180,
    )
    if not bars:
        logger.error("  Continuous request returned no data.")
        return pd.DataFrame()

    df = pd.DataFrame(bars).rename(columns={"date": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")
    logger.info(f"  Got {len(df)} bars: {df.index[0]} → {df.index[-1]}")
    return df


def get_mnq_quarterly_contracts(ib, months_back: int):
    """Build qualified MNQ quarterly Future contracts spanning the period.

    MNQ rolls quarterly: Mar (H), Jun (M), Sep (U), Dec (Z). Each contract
    trades for ~6-9 months before expiry. We grab every quarterly whose
    trading life overlaps [now - months_back, now], then fetch each one's
    bars and stitch them together. We use specific Future contracts
    (not ContFuture) because ContFuture rejects endDateTime in
    reqHistoricalData (IBKR error 10339).
    """
    from ib_insync import Future

    end = datetime.now()
    start = end - timedelta(days=months_back * 31)

    raw = []
    for year in range(start.year - 1, end.year + 2):
        for month in (3, 6, 9, 12):
            # Approx expiry mid-month; contract trades for ~9 months prior.
            expiry_approx = datetime(year, month, 15)
            life_start = expiry_approx - timedelta(days=270)
            if expiry_approx < start or life_start > end:
                continue
            ym = f"{year}{month:02d}"
            contract = Future("MNQ", lastTradeDateOrContractMonth=ym, exchange="CME")
            contract.includeExpired = True
            raw.append(contract)

    qualified = []
    for c in raw:
        try:
            q = ib.qualifyContracts(c)
        except Exception as exc:
            logger.debug(f"  Skip MNQ {c.lastTradeDateOrContractMonth}: {exc}")
            continue
        if q:
            qualified.append(q[0])
            logger.info(
                f"  Qualified: {q[0].localSymbol} expires {q[0].lastTradeDateOrContractMonth}"
            )

    if not qualified:
        logger.error("Failed to qualify any MNQ quarterly contract.")
        sys.exit(1)

    qualified.sort(key=lambda c: c.lastTradeDateOrContractMonth)
    return qualified


def fetch_chunk(
    ib,
    contract,
    end_dt: datetime,
    duration: str = "6 M",
    bar_size: str = "15 mins",
) -> pd.DataFrame:
    """Fetch one chunk of historical data ending at end_dt.

    Returns DataFrame with OHLCV indexed by US/Eastern datetime.
    Pass end_dt=None (or empty) to request data up to "now".
    """
    end_str = "" if end_dt is None else end_dt.strftime("%Y%m%d-%H:%M:%S")
    logger.info(
        f"  {contract.localSymbol}: requesting {duration} of {bar_size} "
        f"ending {end_str or 'now'}..."
    )

    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_str,
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        timeout=120,
    )

    if not bars:
        logger.warning(f"  No data returned for {contract.localSymbol} ending {end_str or 'now'}")
        return pd.DataFrame()

    df = pd.DataFrame(bars)
    df = df.rename(columns={"date": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].copy()

    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")
    else:
        df.index = df.index.tz_convert("US/Eastern")

    logger.info(f"  Got {len(df)} bars: {df.index[0]} → {df.index[-1]}")
    return df


def _duration_to_timedelta(duration: str) -> timedelta:
    """Convert simple IBKR duration strings like '1 M' or '2 W' to a timedelta."""
    amount_s, unit = duration.strip().split(maxsplit=1)
    amount = int(amount_s)
    unit = unit.upper()
    if unit.startswith("D"):
        return timedelta(days=amount)
    if unit.startswith("W"):
        return timedelta(weeks=amount)
    if unit.startswith("M"):
        return timedelta(days=amount * 31)
    if unit.startswith("Y"):
        return timedelta(days=amount * 366)
    raise ValueError(f"Unsupported duration unit in {duration!r}")


def _date_filter(
    df: pd.DataFrame,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Trim to inclusive US/Eastern calendar dates."""
    if df.empty:
        return df

    out = df
    if start_date:
        start = pd.Timestamp(start_date, tz="US/Eastern")
        out = out[out.index >= start]
    if end_date:
        end = pd.Timestamp(end_date, tz="US/Eastern") + pd.Timedelta(days=1)
        out = out[out.index < end]
    return out


def fetch_all(
    ib,
    contracts,
    months: int,
    bar_size: str = "15 mins",
    chunk_duration: str = "9 M",
    request_pause_seconds: float = REQUEST_PAUSE_SECONDS,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch full history by walking through quarterly contracts.

    For each contract, request the 9 months ending at its expiry — this
    covers its full active trading life. Concatenate, dedupe across
    rollover overlaps, and trim to the requested window.
    """
    chunks = []
    cutoff_start = (
        datetime.fromisoformat(start_date)
        if start_date
        else datetime.now() - timedelta(days=months * 31)
    )
    cutoff_end = datetime.fromisoformat(end_date) + timedelta(days=1) if end_date else datetime.now()
    chunk_delta = _duration_to_timedelta(chunk_duration)

    coverage_start = cutoff_start

    for i, contract in enumerate(contracts):
        # Build approx expiry datetime from YYYYMM contract month.
        ym = contract.lastTradeDateOrContractMonth[:6]
        if len(contract.lastTradeDateOrContractMonth) >= 8:
            expiry_dt = datetime.strptime(contract.lastTradeDateOrContractMonth[:8], "%Y%m%d") + timedelta(days=1)
        else:
            expiry_dt = datetime(int(ym[:4]), int(ym[4:6]), 28)

        # Roll forward through contracts instead of downloading every contract's
        # full active life. That avoids huge overlap for 1-minute validation pulls.
        contract_start = coverage_start
        contract_end = min(expiry_dt, cutoff_end)
        if contract_end <= contract_start:
            continue

        cursor = contract_end
        while cursor > contract_start:
            chunk = fetch_chunk(
                ib,
                contract,
                cursor,
                duration=chunk_duration,
                bar_size=bar_size,
            )
            if not chunk.empty:
                chunks.append(chunk)
                earliest = chunk.index[0].tz_convert("US/Eastern").tz_localize(None)
                cursor = min(cursor - timedelta(seconds=1), earliest - timedelta(seconds=1))
            else:
                cursor = cursor - chunk_delta

            if cursor > contract_start:
                logger.info(f"  Pacing pause ({request_pause_seconds:g}s)...")
                time.sleep(request_pause_seconds)

        coverage_start = max(coverage_start, contract_end)

        if coverage_start >= cutoff_end:
            break

        if i < len(contracts) - 1:
            logger.info(f"  Contract pacing pause ({request_pause_seconds:g}s)...")
            time.sleep(request_pause_seconds)

    if not chunks:
        logger.error("No data fetched at all.")
        sys.exit(1)

    # Combine, deduplicate (rollover overlaps), trim to requested window
    combined = pd.concat(chunks)
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.sort_index()
    combined = _date_filter(combined, start_date=start_date, end_date=end_date)

    # Filter RTH only (09:30 - 16:00 ET)
    rth_mask = (combined.index.time >= pd.Timestamp("09:30").time()) & \
               (combined.index.time < pd.Timestamp("16:00").time())
    combined = combined[rth_mask]

    logger.info(f"Total: {len(combined)} bars from {combined.index[0]} to {combined.index[-1]}")
    return combined


def validate(df: pd.DataFrame) -> bool:
    """Run basic quality checks on the fetched data."""
    issues = []

    if len(df) < 1000:
        issues.append(f"Only {len(df)} bars — need at least ~5,000 for 12 months")

    if df.isnull().any().any():
        null_counts = df.isnull().sum()
        issues.append(f"NaN values: {null_counts[null_counts > 0].to_dict()}")

    bad_hl = df["high"] < df["low"]
    if bad_hl.any():
        issues.append(f"{bad_hl.sum()} bars with high < low")

    neg_vol = df["volume"] < 0
    if neg_vol.any():
        issues.append(f"{neg_vol.sum()} bars with negative volume")

    zero_vol = df["volume"] == 0
    if zero_vol.any():
        pct = zero_vol.sum() / len(df) * 100
        if pct > 5:
            issues.append(f"{pct:.1f}% of bars have zero volume")
        else:
            logger.warning(f"  {zero_vol.sum()} bars with zero volume ({pct:.1f}%) — acceptable")

    # Check for gaps (missing trading days)
    dates = pd.Series(df.index.date).unique()
    date_range = pd.bdate_range(dates[0], dates[-1])
    missing = set(date_range.date) - set(dates)
    # Filter out holidays (rough — just flag if >10% missing)
    if len(missing) > len(date_range) * 0.1:
        issues.append(f"{len(missing)} missing trading days out of {len(date_range)} business days")

    if issues:
        logger.error("Data quality issues found:")
        for issue in issues:
            logger.error(f"  - {issue}")
        return False

    logger.info("Data quality checks passed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch MNQ historical data from IBKR")
    parser.add_argument("--months", type=int, default=18, help="Months of history (default: 18)")
    parser.add_argument("--host", default="127.0.0.1", help="IBKR host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7497, help="IBKR port (default: 7497 paper)")
    parser.add_argument("--client-id", type=int, default=99, help="Client ID (default: 99)")
    parser.add_argument("--output", default="data/mnq_15m.parquet", help="Output path")
    parser.add_argument("--bar-size", default="15 mins", help='IBKR bar size, e.g. "15 mins" or "1 min"')
    parser.add_argument("--chunk-duration", default=None, help='IBKR chunk duration for quarterly contracts, e.g. "1 M"')
    parser.add_argument("--start-date", default=None, help="Inclusive US/Eastern start date, YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Inclusive US/Eastern end date, YYYY-MM-DD")
    parser.add_argument("--request-pause", type=float, default=REQUEST_PAUSE_SECONDS, help="Seconds between IBKR requests")
    parser.add_argument(
        "--market-data-type",
        type=int,
        default=3,
        choices=[1, 2, 3, 4],
        help="1=live, 2=frozen, 3=delayed (default, required for DUO demo accounts), 4=delayed-frozen",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Use ContFuture (single request, IBKR auto-stitches front-month). Cannot use endDateTime, so total span is whatever IBKR returns up to 'now'.",
    )
    args = parser.parse_args()

    # Resolve output path relative to project root
    project_root = Path(__file__).parent.parent
    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ib = connect_ibkr(args.host, args.port, args.client_id, args.market_data_type)

    try:
        if args.continuous:
            contract = get_mnq_continuous_contract(ib)
            # IBKR durationStr: 'N M' / 'N Y'. Use Y for >=12 months, rounded up.
            duration = f"{args.months} M" if args.months < 12 else f"{(args.months + 11) // 12} Y"
            df = fetch_continuous(ib, contract, duration, args.bar_size)
            if df.empty:
                logger.error("Continuous fetch returned nothing. Falling back is up to you.")
                sys.exit(1)
            # RTH filter (already useRTH=True at request, but be defensive)
            rth_mask = (df.index.time >= pd.Timestamp("09:30").time()) & \
                       (df.index.time < pd.Timestamp("16:00").time())
            df = df[rth_mask].sort_index()
            df = _date_filter(df, start_date=args.start_date, end_date=args.end_date)
        else:
            contracts = get_mnq_quarterly_contracts(ib, args.months)
            chunk_duration = args.chunk_duration
            if chunk_duration is None:
                chunk_duration = "9 M" if args.bar_size == "15 mins" else "1 M"
            df = fetch_all(
                ib,
                contracts,
                args.months,
                bar_size=args.bar_size,
                chunk_duration=chunk_duration,
                request_pause_seconds=args.request_pause,
                start_date=args.start_date,
                end_date=args.end_date,
            )

        if not validate(df):
            logger.error("Data validation failed. Saving anyway for inspection.")

        df.to_parquet(output_path)
        logger.info(f"Saved {len(df)} bars to {output_path}")

        # Print summary
        trading_days = len(pd.Series(df.index.date).unique())
        bars_per_day = len(df) / trading_days if trading_days > 0 else 0
        print(f"\n{'='*50}")
        print(f"MNQ {args.bar_size} Data Summary")
        print(f"{'='*50}")
        print(f"  Bars:         {len(df):,}")
        print(f"  Trading days: {trading_days}")
        print(f"  Bars/day:     {bars_per_day:.1f}")
        print(f"  Date range:   {df.index[0].date()} → {df.index[-1].date()}")
        print(f"  Months:       {(df.index[-1] - df.index[0]).days / 30:.1f}")
        print(f"  Output:       {output_path}")
        print(f"{'='*50}")

    finally:
        ib.disconnect()
        logger.info("Disconnected from IBKR.")


if __name__ == "__main__":
    main()
