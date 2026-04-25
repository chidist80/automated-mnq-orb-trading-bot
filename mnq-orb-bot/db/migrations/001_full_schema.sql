-- MNQ ORB Bot — Supabase Schema
-- Run these migrations in order

-- ============================================================
-- 001: Core trade logging
-- ============================================================

CREATE TABLE IF NOT EXISTS trades (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    -- Trade identifiers
    date            DATE NOT NULL,
    setup           TEXT NOT NULL CHECK (setup IN ('orb_breakout', 'ema_continuation', 'inverse_orb')),
    direction       TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    
    -- Execution
    entry_time      TIMESTAMPTZ NOT NULL,
    entry_price     NUMERIC(10,2) NOT NULL,
    exit_time       TIMESTAMPTZ,
    exit_price      NUMERIC(10,2),
    exit_reason     TEXT CHECK (exit_reason IN ('target', 'stop', 'trail_ema', 'eod_flatten', 'flatten_time', 'manual', 'circuit_breaker')),
    
    -- Risk
    stop_price      NUMERIC(10,2) NOT NULL,
    target_price    NUMERIC(10,2),
    risk_points     NUMERIC(10,2) NOT NULL,
    contracts       INTEGER DEFAULT 1,
    
    -- P&L
    pnl_points      NUMERIC(10,2),
    pnl_dollars     NUMERIC(10,2),
    slippage_points NUMERIC(10,2) DEFAULT 0,
    commission      NUMERIC(10,2) DEFAULT 0,
    
    -- Context
    or_size         NUMERIC(10,2),
    or_classification TEXT CHECK (or_classification IN ('tight', 'normal', 'wide')),
    confluences     JSONB DEFAULT '{}',
    
    -- Source: 'backtest', 'paper', 'live'
    source          TEXT NOT NULL DEFAULT 'backtest',
    
    -- Indexes
    CONSTRAINT valid_pnl CHECK (exit_price IS NULL OR pnl_dollars IS NOT NULL)
);

CREATE INDEX idx_trades_date ON trades(date);
CREATE INDEX idx_trades_setup ON trades(setup);
CREATE INDEX idx_trades_source ON trades(source);


-- ============================================================
-- 002: Signal log (every signal generated, whether taken or not)
-- ============================================================

CREATE TABLE IF NOT EXISTS signals (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    date            DATE NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    setup           TEXT NOT NULL,
    direction       TEXT NOT NULL,
    
    -- Signal details
    entry_price     NUMERIC(10,2) NOT NULL,
    stop_price      NUMERIC(10,2) NOT NULL,
    target_price    NUMERIC(10,2),
    risk_points     NUMERIC(10,2) NOT NULL,
    grade           TEXT CHECK (grade IN ('A+', 'A', 'B', 'C')),
    confluences     JSONB DEFAULT '{}',
    
    -- Execution status
    taken           BOOLEAN DEFAULT false,
    skip_reason     TEXT,  -- 'risk_limit', 'confluence_fail', 'outside_window', 'manual_skip'
    trade_id        UUID REFERENCES trades(id),
    
    -- Context
    or_high         NUMERIC(10,2),
    or_low          NUMERIC(10,2),
    or_size         NUMERIC(10,2)
);

CREATE INDEX idx_signals_date ON signals(date);
CREATE INDEX idx_signals_taken ON signals(taken);


-- ============================================================
-- 003: Daily P&L summary
-- ============================================================

CREATE TABLE IF NOT EXISTS daily_pnl (
    date            DATE PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    trades_taken    INTEGER DEFAULT 0,
    winners         INTEGER DEFAULT 0,
    losers          INTEGER DEFAULT 0,
    gross_pnl       NUMERIC(10,2) DEFAULT 0,
    commissions     NUMERIC(10,2) DEFAULT 0,
    net_pnl         NUMERIC(10,2) DEFAULT 0,
    
    -- Running totals
    cumulative_pnl  NUMERIC(10,2) DEFAULT 0,
    equity          NUMERIC(10,2) DEFAULT 0,
    peak_equity     NUMERIC(10,2) DEFAULT 0,
    drawdown        NUMERIC(10,2) DEFAULT 0,
    drawdown_pct    NUMERIC(6,4) DEFAULT 0,
    
    -- Best/worst
    best_trade      NUMERIC(10,2),
    worst_trade     NUMERIC(10,2),
    
    -- Source
    source          TEXT NOT NULL DEFAULT 'backtest'
);


-- ============================================================
-- 004: System state (for bot monitoring)
-- ============================================================

CREATE TABLE IF NOT EXISTS system_state (
    key             TEXT PRIMARY KEY,
    value           JSONB NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Insert default state
INSERT INTO system_state (key, value) VALUES
    ('bot_mode', '"active"'),           -- active, alerts_only, halted
    ('current_position', 'null'),       -- Current open position details
    ('daily_stats', '{"trades": 0, "pnl": 0, "consecutive_losses": 0}'),
    ('connection_status', '{"ibkr": false, "data_feed": false, "last_heartbeat": null}'),
    ('last_signal', 'null'),
    ('circuit_breaker', '{"triggered": false, "reason": null}')
ON CONFLICT (key) DO NOTHING;


-- ============================================================
-- 005: Research knowledge base
-- ============================================================

CREATE TABLE IF NOT EXISTS research_posts (
    id              TEXT PRIMARY KEY,  -- Reddit post ID
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    title           TEXT NOT NULL,
    body            TEXT,
    author          TEXT,
    subreddit       TEXT,
    score           INTEGER DEFAULT 0,
    url             TEXT,
    posted_at       TIMESTAMPTZ,
    
    -- Extraction status
    extracted       BOOLEAN DEFAULT false,
    extraction_id   UUID
);

CREATE TABLE IF NOT EXISTS extracted_strategies (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    post_id         TEXT REFERENCES research_posts(id),
    
    -- Extracted parameters (structured)
    strategy_name   TEXT,
    instrument      TEXT,
    timeframe       TEXT,
    setup_type      TEXT,
    
    -- Full extracted JSON from Claude
    parameters      JSONB NOT NULL,
    
    -- Quality
    confidence      NUMERIC(3,2),  -- 0.0 to 1.0
    claimed_win_rate NUMERIC(5,4),
    claimed_profit_factor NUMERIC(6,2),
    verified        BOOLEAN DEFAULT false
);

CREATE INDEX idx_extracted_setup ON extracted_strategies(setup_type);
CREATE INDEX idx_extracted_confidence ON extracted_strategies(confidence);


-- ============================================================
-- 006: Backtest runs (track every backtest for comparison)
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    
    -- Config used
    strategy_params JSONB NOT NULL,
    risk_params     JSONB NOT NULL,
    
    -- Data
    data_start      DATE,
    data_end        DATE,
    total_bars      INTEGER,
    
    -- Results
    total_trades    INTEGER,
    win_rate        NUMERIC(5,4),
    profit_factor   NUMERIC(6,2),
    total_pnl       NUMERIC(10,2),
    max_drawdown    NUMERIC(10,2),
    max_consec_losses INTEGER,
    
    -- Validation
    walk_forward_pass_rate NUMERIC(5,4),
    monte_carlo_ruin_pct   NUMERIC(5,4),
    
    notes           TEXT
);


-- ============================================================
-- RLS Policies (if using Supabase auth)
-- ============================================================

-- Enable RLS on all tables
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_pnl ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE backtest_runs ENABLE ROW LEVEL SECURITY;

-- For now, allow all access (tighten with auth later)
CREATE POLICY "Allow all" ON trades FOR ALL USING (true);
CREATE POLICY "Allow all" ON signals FOR ALL USING (true);
CREATE POLICY "Allow all" ON daily_pnl FOR ALL USING (true);
CREATE POLICY "Allow all" ON system_state FOR ALL USING (true);
CREATE POLICY "Allow all" ON research_posts FOR ALL USING (true);
CREATE POLICY "Allow all" ON extracted_strategies FOR ALL USING (true);
CREATE POLICY "Allow all" ON backtest_runs FOR ALL USING (true);
