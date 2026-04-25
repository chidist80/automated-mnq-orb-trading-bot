# MNQ ORB Trading Bot

Automated MNQ Micro Nasdaq-100 futures trading bot implementing three ORB-derived strategies, backed by a Reddit community research pipeline and comprehensive backtesting engine.

## Strategies

1. **ORB Breakout + Retest** — 15m Opening Range breakout, enter on retest of broken level
2. **9EMA Trend Continuation** — Pullback to 9EMA after trend established from ORB
3. **Inverse ORB** — Fade failed breakouts when Opening Range is abnormally wide

## Architecture

```
Research Pipeline (PRAW + Claude API)
         ↓
   Strategy Knowledge Base (Supabase)
         ↓
   Backtesting Engine (Python + pandas)
         ↓
   Signal Bot (VPS) → IBKR API → Full Auto Execution
         ↓
   Dashboard (Next.js + Supabase Realtime)
```

## Quick Start

### 1. Install dependencies

```bash
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Fill in your API keys
```

### 3. Get historical data

Place MNQ 15-minute bar data as CSV in `data/`:
- Columns: timestamp, open, high, low, close, volume
- Timezone: US/Eastern
- Period: 12+ months recommended

### 4. Run backtest

```bash
python -m backtest.backtester data/mnq_15m.csv
```

### 5. Run research pipeline

```bash
# Scrape Reddit for strategy intelligence
python -m research.reddit_scraper

# Extract structured parameters with Claude
python -m research.claude_extractor research/data/posts_YYYYMMDD.json
```

## Project Structure

```
mnq-orb-bot/
├── config/
│   ├── strategy_params.yaml    # All tunable strategy parameters
│   └── risk_params.yaml        # Risk limits and circuit breakers
├── research/
│   ├── reddit_scraper.py       # PRAW-based Reddit scraper
│   └── claude_extractor.py     # Claude API structured extraction
├── backtest/
│   ├── data_loader.py          # Historical data ingestion
│   ├── or_detector.py          # Opening Range detection + classification
│   ├── indicators.py           # RSI, EMA, volume calculations
│   └── backtester.py           # Core backtesting engine
├── bot/                        # Phase 2: live signal generation + execution
├── dashboard/                  # Phase 3: Next.js monitoring dashboard
└── db/migrations/              # Supabase schema
```

## Build Phases

- [x] Phase 0: Research pipeline (PRAW + Claude extraction)
- [x] Phase 1: Backtesting engine (core logic + validation tools)
- [ ] Phase 1b: Historical data acquisition + backtest validation
- [ ] Phase 2: Live signal bot + IBKR paper trading
- [ ] Phase 3: Monitoring dashboard
- [ ] Phase 4: Live trading with $2-3K capital

## Validation Pipeline

```bash
# 1. Run backtest
python -m backtest.backtester data/mnq_15m.csv

# 2. Parameter sweep (find optimal config)
python -m backtest.param_sweep data/mnq_15m.csv --quick

# 3. Walk-forward validation (detect overfitting)
python -m backtest.walk_forward data/mnq_15m.csv

# 4. Monte Carlo simulation (drawdown probability)
# (run from Python after backtest)
from backtest.monte_carlo import run_monte_carlo
mc = run_monte_carlo(results, account_size=2500)
mc.summary()

# 5. Generate HTML report
from backtest.report import generate_report
generate_report(results, "reports/backtest.html")
```

## Key Parameters (from NeverStoppedout's approach)

| Parameter | Value | Source |
|---|---|---|
| Instrument | MNQ | Confirmed |
| Timeframe | 15m | Confirmed |
| OR period | 15 minutes | Confirmed |
| EMA | 9-period | Confirmed |
| Win rate target | >65% | 74.91% achieved |
| Profit factor target | >1.8 | 2.10 achieved |
| Position size | 1-2 micros | Confirmed |
| Stop method | Opposite side of OR | Confirmed |
| Max stop | ~100 points | Confirmed |
