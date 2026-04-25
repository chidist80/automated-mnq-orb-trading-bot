"""
Core backtesting engine.

Processes historical data day-by-day, detects Opening Range setups,
checks confluences, simulates entries/exits, and tracks P&L.

Usage:
    from backtest.backtester import Backtester
    
    bt = Backtester("config/strategy_params.yaml", "config/risk_params.yaml")
    results = bt.run("data/mnq_15m.csv")
    results.summary()
    results.plot_equity_curve()
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import logging

from backtest.data_loader import load_csv, load_parquet, split_by_day
from backtest.or_detector import (
    OpeningRange, BreakoutEvent,
    detect_opening_range, detect_breakout,
)
from backtest.indicators import add_all_indicators, check_confluences

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Record of a single simulated trade."""
    date: str
    setup: str                  # "orb_breakout", "ema_continuation", "inverse_orb"
    direction: str              # "long" or "short"
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""       # "target", "stop", "trail", "eod_flatten", "time_limit"
    stop_price: float = 0.0
    target_price: Optional[float] = None
    risk_points: float = 0.0
    pnl_points: float = 0.0
    pnl_dollars: float = 0.0
    contracts: int = 1
    confluences: dict = field(default_factory=dict)
    or_size: float = 0.0
    or_classification: str = ""


@dataclass
class BacktestResults:
    """Aggregated backtest results."""
    trades: list[Trade]
    equity_curve: pd.Series
    config: dict
    
    @property
    def total_trades(self) -> int:
        return len(self.trades)
    
    @property
    def winners(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl_dollars > 0]
    
    @property
    def losers(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl_dollars <= 0]
    
    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return len(self.winners) / len(self.trades)
    
    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl_dollars for t in self.winners)
        gross_loss = abs(sum(t.pnl_dollars for t in self.losers))
        if gross_loss == 0:
            return float("inf")
        return gross_profit / gross_loss
    
    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_dollars for t in self.trades)
    
    @property
    def avg_winner(self) -> float:
        if not self.winners:
            return 0.0
        return np.mean([t.pnl_dollars for t in self.winners])
    
    @property
    def avg_loser(self) -> float:
        if not self.losers:
            return 0.0
        return np.mean([t.pnl_dollars for t in self.losers])
    
    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        peak = self.equity_curve.expanding().max()
        drawdown = self.equity_curve - peak
        return drawdown.min()
    
    @property
    def max_consecutive_losses(self) -> int:
        max_streak = 0
        current_streak = 0
        for t in self.trades:
            if t.pnl_dollars <= 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        return max_streak
    
    def summary(self) -> str:
        """Print formatted summary of backtest results."""
        by_setup = {}
        for t in self.trades:
            by_setup.setdefault(t.setup, []).append(t)
        
        lines = [
            "=" * 60,
            "BACKTEST RESULTS",
            "=" * 60,
            f"Total Trades:         {self.total_trades}",
            f"Win Rate:             {self.win_rate:.1%}",
            f"Profit Factor:        {self.profit_factor:.2f}",
            f"Total P&L:            ${self.total_pnl:,.2f}",
            f"Avg Winner:           ${self.avg_winner:,.2f}",
            f"Avg Loser:            ${self.avg_loser:,.2f}",
            f"Max Drawdown:         ${self.max_drawdown:,.2f}",
            f"Max Consec. Losses:   {self.max_consecutive_losses}",
            "",
            "BY SETUP:",
        ]
        
        for setup, trades in by_setup.items():
            wins = [t for t in trades if t.pnl_dollars > 0]
            wr = len(wins) / len(trades) if trades else 0
            pnl = sum(t.pnl_dollars for t in trades)
            lines.append(f"  {setup:25s}  {len(trades):3d} trades  {wr:.0%} WR  ${pnl:,.2f}")
        
        lines.append("=" * 60)
        report = "\n".join(lines)
        print(report)
        return report
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert trades to DataFrame for analysis."""
        records = []
        for t in self.trades:
            records.append({
                "date": t.date,
                "setup": t.setup,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "stop_price": t.stop_price,
                "risk_points": t.risk_points,
                "pnl_points": t.pnl_points,
                "pnl_dollars": t.pnl_dollars,
                "or_size": t.or_size,
                "or_classification": t.or_classification,
            })
        return pd.DataFrame(records)


class Backtester:
    """Main backtesting engine."""
    
    def __init__(
        self,
        strategy_config: str | dict = "config/strategy_params.yaml",
        risk_config: str | dict = "config/risk_params.yaml",
    ):
        if isinstance(strategy_config, str):
            with open(strategy_config) as f:
                self.strategy = yaml.safe_load(f)
        else:
            self.strategy = strategy_config
            
        if isinstance(risk_config, str):
            with open(risk_config) as f:
                self.risk = yaml.safe_load(f)
        else:
            self.risk = risk_config
        
        self.point_value = self.strategy["instrument"]["point_value"]
        self.trades: list[Trade] = []
        self.or_history: list[float] = []  # Rolling OR sizes for percentile calc (inverse_orb)
        self.regime_or_history: list[float] = []  # Longer-window OR history for regime filter
        self._running_pnl: float = 0.0      # Cumulative P&L for account drawdown check
        self._peak_pnl: float = 0.0         # Peak cumulative P&L
    
    def seed_regime_history(self, df: pd.DataFrame) -> None:
        """Pre-compute daily OR widths from a prior period (e.g. walk-forward train data)
        and seed regime_or_history. Lets the regime filter activate from day 1 of the
        test window instead of waiting for warmup. Capped at regime_filter.max_history_days.
        """
        from backtest.data_loader import split_by_day

        days = split_by_day(df)
        or_period = self.strategy["opening_range"]["period_minutes"]
        widths: list[float] = []
        for _date_str, day_bars in sorted(days.items()):
            if len(day_bars) < 1:
                continue
            if or_period == 15:
                or_bar = day_bars.iloc[0]
                widths.append(float(or_bar["high"] - or_bar["low"]))
            else:
                n = max(or_period // 15, 1)
                or_bars = day_bars.iloc[:n]
                widths.append(float(or_bars["high"].max() - or_bars["low"].min()))

        regime_max = self.strategy.get("regime_filter", {}).get("max_history_days", 60)
        self.regime_or_history = widths[-regime_max:]
        logger.info(f"Seeded regime_or_history with {len(self.regime_or_history)} days from prior data")

    def run(self, data_path: str | Path) -> BacktestResults:
        """Run backtest on historical data.
        
        Args:
            data_path: Path to CSV or Parquet file with 15m MNQ bars
            
        Returns:
            BacktestResults with all trades and equity curve
        """
        path = Path(data_path)
        if path.suffix == ".parquet":
            df = load_parquet(path)
        else:
            df = load_csv(path)
        
        # Add indicators
        df = add_all_indicators(df, self.strategy)
        
        # Drop warmup bars where indicators are NaN (RSI needs 14 bars, EMA needs ~20)
        warmup = max(
            self.strategy.get("confluences", {}).get("rsi", {}).get("period", 14),
            self.strategy.get("confluences", {}).get("volume", {}).get("lookback_bars", 20),
            self.strategy.get("ema_continuation", {}).get("ema_period", 9) * 2,
        )
        if len(df) > warmup:
            df = df.iloc[warmup:]
        
        # Resolve EMA column name dynamically
        ema_period = self.strategy.get("ema_continuation", {}).get("ema_period", 9)
        self._ema_col = f"ema_{ema_period}"
        
        # Split into daily chunks
        days = split_by_day(df)
        
        self.trades = []
        self.or_history = []
        # Note: do NOT reset regime_or_history here — it may have been pre-seeded
        # from prior data (e.g., walk-forward train period). Reset is via __init__.
        self._running_pnl = 0.0
        self._peak_pnl = 0.0
        
        for date_str, day_bars in sorted(days.items()):
            if len(day_bars) < 4:  # Need minimum bars for a valid day
                continue
            
            day_trades = self._process_day(day_bars, date_str)
            self.trades.extend(day_trades)
        
        # Build equity curve
        equity = self._build_equity_curve()
        
        results = BacktestResults(
            trades=self.trades,
            equity_curve=equity,
            config={**self.strategy, **self.risk},
        )
        
        logger.info(f"Backtest complete: {results.total_trades} trades, "
                     f"{results.win_rate:.1%} WR, PF {results.profit_factor:.2f}")
        
        return results
    
    def _process_day(self, day_bars: pd.DataFrame, date_str: str) -> list[Trade]:
        """Process a single trading day through all three strategies."""
        trades = []
        daily_pnl = 0.0
        daily_trades = 0
        daily_losses = 0
        
        # Step 1: Detect Opening Range
        or_period = self.strategy["opening_range"]["period_minutes"]
        wide_pct = self.strategy.get("inverse_orb", {}).get("wide_or_percentile", 80)
        
        opening_range = detect_opening_range(
            day_bars, or_period, self.or_history, wide_pct
        )
        self.or_history.append(opening_range.size)
        
        # Keep rolling lookback window for inverse_orb (small, regime-insensitive use)
        lookback = self.strategy.get("inverse_orb", {}).get("lookback_days", 20)
        if len(self.or_history) > lookback:
            self.or_history = self.or_history[-lookback:]

        # Maintain longer regime-filter window separately (cap at 60).
        regime_max = self.strategy.get("regime_filter", {}).get("max_history_days", 60)
        self.regime_or_history.append(opening_range.size)
        if len(self.regime_or_history) > regime_max:
            self.regime_or_history = self.regime_or_history[-regime_max:]
        
        # Skip if OR too small
        min_size = self.strategy["opening_range"].get("min_size_points", 20)
        max_stop = self.strategy["orb_breakout"].get("stop_max_points", 120)
        
        if opening_range.size < min_size:
            logger.debug(f"{date_str}: OR too tight ({opening_range.size:.1f} pts), skipping")
            return []
        
        # Check account-level circuit breaker (#23)
        if self._check_account_circuit_breaker():
            logger.warning(f"{date_str}: Account circuit breaker active, skipping day")
            return []
        
        # Step 2: Detect breakout (filter to trading window)
        breakout = detect_breakout(day_bars, opening_range, or_period)
        
        if breakout is None:
            logger.debug(f"{date_str}: No breakout detected")
            return []
        
        # Check if breakout is within trading window (#6)
        if not self._in_trading_window(breakout.timestamp):
            logger.debug(f"{date_str}: Breakout outside trading window")
            return []
        
        # Step 3: Route to appropriate strategy
        # FIX #13: Exclusive routing — inverse ORB and standard ORB are mutually exclusive
        
        took_inverse = False
        
        # 3a: Inverse ORB (wide OR + resting re-entry stop)
        if (opening_range.classification == "wide" 
            and self.strategy.get("inverse_orb", {}).get("enabled", True)
            and self._check_risk_limits(daily_pnl, daily_trades, daily_losses)):  # FIX #2: check BEFORE
            
            trade = self._simulate_inverse_orb(
                day_bars, opening_range, breakout, date_str
            )
            if trade:
                trades.append(trade)
                daily_pnl += trade.pnl_dollars
                daily_trades += 1
                took_inverse = True
                if trade.pnl_dollars <= 0:
                    daily_losses += 1
                else:
                    daily_losses = 0

        # 3b: ORB Breakout + Retest (skip if inverse ORB already taken)
        if (not took_inverse
            and opening_range.size <= max_stop
            and self.strategy.get("orb_breakout", {}).get("enabled", True)
            and self._check_risk_limits(daily_pnl, daily_trades, daily_losses)):

            breakout_bar = day_bars.iloc[breakout.bar_index]
            confluences = check_confluences(breakout_bar, breakout.direction, self.strategy)
            if all(confluences.values()):
                trade = self._simulate_orb_trade(
                    day_bars, opening_range, breakout, confluences, date_str
                )
                if trade:
                    trades.append(trade)
                    daily_pnl += trade.pnl_dollars
                    daily_trades += 1
                    if trade.pnl_dollars <= 0:
                        daily_losses += 1
                    else:
                        daily_losses = 0
        
        # 3c: 9EMA Continuation (only if no active position from above — FIX #12)
        # Regime filter: skip EMA continuation on high-OR-width days (Filter A).
        # Window #1 study showed EMA continuation loses ~100% in top-decile-OR regimes
        # while ORB/inverse ORB hold. or_history already includes today's OR (appended
        # earlier), so compare today against the trailing window excluding today.
        regime_cfg = self.strategy.get("regime_filter", {})
        regime_skip_ema = False
        if regime_cfg.get("enabled", False):
            min_hist = regime_cfg.get("min_history_days", 10)
            # regime_or_history already includes today's OR; exclude it for the threshold
            if len(self.regime_or_history) >= min_hist + 1:
                history = self.regime_or_history[:-1]
                threshold_pct = regime_cfg.get("ema_skip_or_percentile", 90)
                threshold = np.percentile(history, threshold_pct)
                if opening_range.size >= threshold:
                    regime_skip_ema = True
                    logger.debug(
                        f"{date_str}: regime filter — OR {opening_range.size:.1f} "
                        f">= p{threshold_pct} ({threshold:.1f}), skipping EMA continuation"
                    )

        if (self.strategy.get("ema_continuation", {}).get("enabled", True)
            and not regime_skip_ema
            and self._check_risk_limits(daily_pnl, daily_trades, daily_losses)):
            
            # Pass the bar index of the last trade exit so we don't overlap
            last_exit_bar = 0
            if trades:
                last_trade = trades[-1]
                if last_trade.exit_time:
                    # Find the bar index for the exit time
                    exit_mask = day_bars.index >= last_trade.exit_time
                    if exit_mask.any():
                        last_exit_bar = exit_mask.argmax()
            
            cont_trades = self._find_ema_continuations(
                day_bars, opening_range, breakout, date_str,
                daily_pnl, daily_trades, daily_losses,
                min_start_bar=max(breakout.bar_index + 2, last_exit_bar),
            )
            trades.extend(cont_trades)
        
        return trades
    
    def _simulate_orb_trade(
        self,
        day_bars: pd.DataFrame,
        opening_range: OpeningRange,
        breakout: BreakoutEvent,
        confluences: dict,
        date_str: str,
    ) -> Optional[Trade]:
        """Simulate an executable ORB breakout + resting retest-limit order."""
        orb_config = self.strategy["orb_breakout"]
        timeout_bars = orb_config.get("retest_timeout_bars", 8)
        direction = breakout.direction

        if direction == "long":
            entry_price = opening_range.high
            stop_price = opening_range.low
        else:
            entry_price = opening_range.low
            stop_price = opening_range.high

        fill_idx = self._find_resting_level_fill(
            day_bars,
            start_bar=breakout.bar_index + 1,
            end_bar=min(breakout.bar_index + 1 + timeout_bars, len(day_bars)),
            direction=direction,
            entry_price=entry_price,
        )
        if fill_idx is None:
            return None

        fill_time = day_bars.index[fill_idx]
        if not self._in_trading_window(fill_time):
            return None

        if direction == "long":
            risk_points = entry_price - stop_price
        else:
            risk_points = stop_price - entry_price
        if risk_points <= 0:
            return None

        if not self._check_trade_risk_cap(risk_points, date_str, "orb_breakout"):
            return None
        
        # Calculate target based on config
        target_config = orb_config["target"]
        target_price = None
        
        if target_config["method"] in ("fixed_rr", "partial_trail"):
            rr = target_config.get("fixed_rr", 1.0)
            if direction == "long":
                target_price = entry_price + (risk_points * rr)
            else:
                target_price = entry_price - (risk_points * rr)
        
        # Walk forward bar-by-bar from entry to simulate exit
        trade = Trade(
            date=date_str,
            setup="orb_breakout",
            direction=direction,
            entry_time=fill_time,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            risk_points=risk_points,
            contracts=1,
            confluences=confluences,
            or_size=opening_range.size,
            or_classification=opening_range.classification,
        )
        
        trade = self._walk_forward_exit(day_bars, trade, fill_idx, entry_bar_stop_only=True)
        return trade
    
    def _simulate_inverse_orb(
        self,
        day_bars: pd.DataFrame,
        opening_range: OpeningRange,
        breakout: BreakoutEvent,
        date_str: str,
    ) -> Optional[Trade]:
        """Simulate an executable inverse ORB resting re-entry order."""
        inv_config = self.strategy.get("inverse_orb", {})
        buffer = inv_config.get("stop_buffer_points", 10)
        window_minutes = inv_config.get("time_window_minutes", 60)
        max_bars = max(window_minutes // 15, 1)
        
        # Entry: after failed breakout, enter in opposite direction
        if breakout.direction == "long":
            # Failed long breakout → go short
            direction = "short"
            entry_price = opening_range.high  # Enter at OR high (fading back in)
            stop_price = breakout.candle_high + buffer
            target_price = opening_range.midpoint
        else:
            # Failed short breakout → go long
            direction = "long"
            entry_price = opening_range.low
            stop_price = breakout.candle_low - buffer
            target_price = opening_range.midpoint
        
        fill_idx = self._find_resting_level_fill(
            day_bars,
            start_bar=breakout.bar_index + 1,
            end_bar=min(breakout.bar_index + 1 + max_bars, len(day_bars)),
            direction=direction,
            entry_price=entry_price,
        )
        if fill_idx is None:
            return None

        fill_time = day_bars.index[fill_idx]
        if not self._in_trading_window(fill_time):
            return None

        if direction == "long":
            risk_points = entry_price - stop_price
        else:
            risk_points = stop_price - entry_price
        if risk_points <= 0:
            return None

        if not self._check_trade_risk_cap(risk_points, date_str, "inverse_orb"):
            return None
        
        trade = Trade(
            date=date_str,
            setup="inverse_orb",
            direction=direction,
            entry_time=fill_time,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            risk_points=risk_points,
            contracts=1,
            or_size=opening_range.size,
            or_classification=opening_range.classification,
        )
        
        trade = self._walk_forward_exit(day_bars, trade, fill_idx, entry_bar_stop_only=True)
        return trade
    
    def _find_ema_continuations(
        self,
        day_bars: pd.DataFrame,
        opening_range: OpeningRange,
        breakout: BreakoutEvent,
        date_str: str,
        daily_pnl: float,
        daily_trades: int,
        daily_losses: int,
        min_start_bar: int = 0,
    ) -> list[Trade]:
        """Find 9EMA pullback continuation entries after breakout."""
        trades = []
        ema_config = self.strategy.get("ema_continuation", {})
        max_entries = ema_config.get("max_entries_per_day", 2)
        min_distance = ema_config.get("min_distance_from_or", 30)
        ema_col = getattr(self, '_ema_col', 'ema_9')
        
        direction = breakout.direction
        start_from = max(breakout.bar_index + 2, min_start_bar)
        
        for i in range(start_from, len(day_bars)):
            if len(trades) >= max_entries:
                break
            if not self._check_risk_limits(daily_pnl, daily_trades + len(trades), daily_losses):
                break
            
            bar = day_bars.iloc[i]
            bar_time = day_bars.index[i]
            if trades and trades[-1].exit_time and bar_time <= trades[-1].exit_time:
                continue
            
            # Check trading window
            if not self._in_trading_window(bar_time):
                continue
            
            ema_val = bar.get(ema_col, None)
            if ema_val is None or np.isnan(ema_val):
                continue
            
            if direction == "long":
                distance = bar["close"] - opening_range.high
                if distance < min_distance:
                    continue

                confluences = check_confluences(bar, direction, self.strategy)
                confluences.pop("volume", None)

                if all(confluences.values()):
                    stop_buffer = ema_config.get("stop_buffer_points", 5)
                    swing = bar.get("swing_low_5", ema_val - 10)
                    stop_price = min(swing, ema_val) - stop_buffer
                    entry_price = ema_val + 2
                    fill_idx = self._find_resting_level_fill(
                        day_bars, i + 1, min(i + 2, len(day_bars)), direction, entry_price
                    )
                    if fill_idx is None:
                        continue
                    fill_time = day_bars.index[fill_idx]
                    if not self._in_trading_window(fill_time):
                        continue
                    risk_points = entry_price - stop_price
                    if risk_points <= 0:
                        continue
                    if not self._check_trade_risk_cap(risk_points, date_str, "ema_continuation"):
                        continue
                    
                    trade = Trade(
                        date=date_str,
                        setup="ema_continuation",
                        direction="long",
                        entry_time=fill_time,
                        entry_price=entry_price,
                        stop_price=stop_price,
                        risk_points=risk_points,
                        contracts=1,
                        confluences=confluences,
                        or_size=opening_range.size,
                        or_classification=opening_range.classification,
                    )
                    trade = self._walk_forward_exit(day_bars, trade, fill_idx, entry_bar_stop_only=True)
                    if trade:
                        trades.append(trade)
                        daily_pnl += trade.pnl_dollars
                        if trade.pnl_dollars <= 0:
                            daily_losses += 1
                        else:
                            daily_losses = 0
            
            elif direction == "short":
                distance = opening_range.low - bar["close"]
                if distance < min_distance:
                    continue

                confluences = check_confluences(bar, direction, self.strategy)
                confluences.pop("volume", None)

                if all(confluences.values()):
                    stop_buffer = ema_config.get("stop_buffer_points", 5)
                    swing = bar.get("swing_high_5", ema_val + 10)
                    stop_price = max(swing, ema_val) + stop_buffer
                    entry_price = ema_val - 2
                    fill_idx = self._find_resting_level_fill(
                        day_bars, i + 1, min(i + 2, len(day_bars)), direction, entry_price
                    )
                    if fill_idx is None:
                        continue
                    fill_time = day_bars.index[fill_idx]
                    if not self._in_trading_window(fill_time):
                        continue
                    risk_points = stop_price - entry_price
                    if risk_points <= 0:
                        continue
                    if not self._check_trade_risk_cap(risk_points, date_str, "ema_continuation"):
                        continue
                    
                    trade = Trade(
                        date=date_str,
                        setup="ema_continuation",
                        direction="short",
                        entry_time=fill_time,
                        entry_price=entry_price,
                        stop_price=stop_price,
                        risk_points=risk_points,
                        contracts=1,
                        confluences=confluences,
                        or_size=opening_range.size,
                        or_classification=opening_range.classification,
                    )
                    trade = self._walk_forward_exit(day_bars, trade, fill_idx, entry_bar_stop_only=True)
                    if trade:
                        trades.append(trade)
                        daily_pnl += trade.pnl_dollars
                        if trade.pnl_dollars <= 0:
                            daily_losses += 1
                        else:
                            daily_losses = 0
        
        return trades

    def _find_resting_level_fill(
        self,
        day_bars: pd.DataFrame,
        start_bar: int,
        end_bar: int,
        direction: str,
        entry_price: float,
    ) -> Optional[int]:
        """Find the first bar where a pre-placed entry order would fill."""
        for i in range(start_bar, end_bar):
            bar = day_bars.iloc[i]
            if direction == "long" and bar["low"] <= entry_price:
                return i
            if direction == "short" and bar["high"] >= entry_price:
                return i
        return None
    
    def _walk_forward_exit(
        self,
        day_bars: pd.DataFrame,
        trade: Trade,
        start_bar: int,
        entry_bar_stop_only: bool = False,
    ) -> Trade:
        """Walk forward bar-by-bar to determine trade exit.
        
        Checks: stop hit, target hit, EMA trail exit, trading window exit, EOD flatten.
        Handles same-bar stop+target by checking proximity to bar open (#3).
        """
        ema_col = getattr(self, '_ema_col', 'ema_9')
        be_enabled = self.risk.get("trade_management", {}).get("move_stop_to_breakeven", True)
        be_buffer = self.risk.get("trade_management", {}).get("breakeven_buffer", 5)
        
        current_stop = trade.stop_price
        partial_taken = False
        
        for i in range(start_bar, len(day_bars)):
            bar = day_bars.iloc[i]
            bar_time = day_bars.index[i]
            
            # FIX #6: Force exit if past flatten time
            flatten_time_str = self.strategy.get("schedule", {}).get("flatten_time", "15:45")
            flatten_h, flatten_m = map(int, flatten_time_str.split(":"))
            if bar_time.hour > flatten_h or (bar_time.hour == flatten_h and bar_time.minute >= flatten_m):
                trade.exit_price = bar["open"]  # Exit at open of this bar
                trade.exit_time = bar_time
                trade.exit_reason = "flatten_time"
                break
            
            if trade.direction == "long":
                stop_hit = bar["low"] <= current_stop
                target_hit = trade.target_price and bar["high"] >= trade.target_price
                if entry_bar_stop_only and i == start_bar:
                    if stop_hit:
                        trade.exit_price = current_stop
                        trade.exit_time = bar_time
                        trade.exit_reason = "stop"
                        break
                    continue
                
                # FIX #3: If both stop and target hit on same bar, check which is closer to open
                if stop_hit and target_hit:
                    dist_to_stop = bar["open"] - current_stop
                    dist_to_target = trade.target_price - bar["open"]
                    if dist_to_stop <= dist_to_target:
                        # Stop was likely hit first (price dropped to stop before reaching target)
                        trade.exit_price = current_stop
                        trade.exit_reason = "stop"
                    else:
                        trade.exit_price = trade.target_price
                        trade.exit_reason = "target"
                    trade.exit_time = bar_time
                    break
                
                if stop_hit:
                    trade.exit_price = current_stop
                    trade.exit_time = bar_time
                    trade.exit_reason = "stop"
                    break
                
                if target_hit:
                    trade.exit_price = trade.target_price
                    trade.exit_time = bar_time
                    trade.exit_reason = "target"
                    if be_enabled and not partial_taken:
                        current_stop = trade.entry_price + be_buffer
                        partial_taken = True
                    break
                
                # EMA trail exit (for continuation trades)
                if trade.setup == "ema_continuation":
                    ema_val = bar.get(ema_col, None)
                    if ema_val and bar["close"] < ema_val:
                        trade.exit_price = bar["close"]
                        trade.exit_time = bar_time
                        trade.exit_reason = "trail_ema"
                        break
            
            elif trade.direction == "short":
                stop_hit = bar["high"] >= current_stop
                target_hit = trade.target_price and bar["low"] <= trade.target_price
                if entry_bar_stop_only and i == start_bar:
                    if stop_hit:
                        trade.exit_price = current_stop
                        trade.exit_time = bar_time
                        trade.exit_reason = "stop"
                        break
                    continue
                
                # FIX #3: Same-bar resolution for shorts
                if stop_hit and target_hit:
                    dist_to_stop = current_stop - bar["open"]
                    dist_to_target = bar["open"] - trade.target_price
                    if dist_to_stop <= dist_to_target:
                        trade.exit_price = current_stop
                        trade.exit_reason = "stop"
                    else:
                        trade.exit_price = trade.target_price
                        trade.exit_reason = "target"
                    trade.exit_time = bar_time
                    break
                
                if stop_hit:
                    trade.exit_price = current_stop
                    trade.exit_time = bar_time
                    trade.exit_reason = "stop"
                    break
                
                if target_hit:
                    trade.exit_price = trade.target_price
                    trade.exit_time = bar_time
                    trade.exit_reason = "target"
                    if be_enabled and not partial_taken:
                        current_stop = trade.entry_price - be_buffer
                        partial_taken = True
                    break
                
                # EMA trail exit
                if trade.setup == "ema_continuation":
                    ema_val = bar.get(ema_col, None)
                    if ema_val and bar["close"] > ema_val:
                        trade.exit_price = bar["close"]
                        trade.exit_time = bar_time
                        trade.exit_reason = "trail_ema"
                        break
        else:
            # End of day — flatten
            last_bar = day_bars.iloc[-1]
            trade.exit_price = last_bar["close"]
            trade.exit_time = day_bars.index[-1]
            trade.exit_reason = "eod_flatten"
        
        # Calculate P&L
        if trade.direction == "long":
            trade.pnl_points = trade.exit_price - trade.entry_price
        else:
            trade.pnl_points = trade.entry_price - trade.exit_price
        
        # FIX #7: Apply slippage (conservative: adverse direction)
        slippage_points = self.risk.get("execution", {}).get("slippage_points", 1.0)
        trade.pnl_points -= slippage_points  # Slippage always hurts
        
        trade.pnl_dollars = trade.pnl_points * self.point_value * trade.contracts
        
        # FIX #8: Deduct commissions
        commission_per_contract = self.risk.get("execution", {}).get("commission_per_side", 0.25)
        total_commission = commission_per_contract * 2 * trade.contracts  # Round trip
        trade.pnl_dollars -= total_commission
        
        # Track for account-level drawdown
        self._running_pnl += trade.pnl_dollars
        self._peak_pnl = max(self._peak_pnl, self._running_pnl)
        
        return trade
    
    def _check_risk_limits(
        self,
        daily_pnl: float,
        daily_trades: int,
        consecutive_losses: int,
    ) -> bool:
        """Check if we're allowed to take another trade today."""
        limits = self.risk["daily_limits"]
        
        if daily_pnl <= limits["max_loss"]:
            return False
        if daily_trades >= limits["max_trades"]:
            return False
        if consecutive_losses >= limits["consecutive_loss_halt"]:
            return False
        
        return True

    def _check_trade_risk_cap(self, risk_points: float, date_str: str, setup: str) -> bool:
        """Check per-trade point-risk cap before accepting a signal."""
        max_risk = self.risk.get("position_sizing", {}).get("max_risk_points")
        if max_risk is None:
            return True
        if risk_points > max_risk:
            logger.debug(
                f"{date_str}: {setup} risk {risk_points:.1f} pts exceeds "
                f"max_risk_points={max_risk}, skipping"
            )
            return False
        return True
    
    def _in_trading_window(self, timestamp: pd.Timestamp) -> bool:
        """Check if a timestamp falls within the configured trading window."""
        schedule = self.strategy.get("schedule", {})
        start_str = schedule.get("trading_start", "09:45")
        end_str = schedule.get("trading_end", "12:00")
        
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
        
        bar_minutes = timestamp.hour * 60 + timestamp.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        
        return start_minutes <= bar_minutes <= end_minutes
    
    def _check_account_circuit_breaker(self) -> bool:
        """Check if account-level drawdown circuit breaker is triggered.
        
        Returns True if we should STOP trading (breaker triggered).
        """
        cb = self.risk.get("circuit_breakers", {})
        max_dd_pct = cb.get("account_drawdown_pct", 0.20)
        starting_balance = self.risk["account"]["starting_balance"]
        
        current_equity = starting_balance + self._running_pnl
        peak_equity = starting_balance + self._peak_pnl
        
        if peak_equity > 0:
            drawdown_pct = (peak_equity - current_equity) / peak_equity
            if drawdown_pct >= max_dd_pct:
                return True
        
        return False
    
    def _build_equity_curve(self) -> pd.Series:
        """Build cumulative equity curve from trades."""
        if not self.trades:
            return pd.Series(dtype=float)
        
        starting_balance = self.risk["account"]["starting_balance"]
        
        pnl_by_date = {}
        for trade in self.trades:
            date = trade.date
            pnl_by_date[date] = pnl_by_date.get(date, 0) + trade.pnl_dollars
        
        dates = sorted(pnl_by_date.keys())
        cumulative = starting_balance
        curve = {}
        
        for date in dates:
            cumulative += pnl_by_date[date]
            curve[date] = cumulative
        
        return pd.Series(curve)


def run_backtest(
    data_path: str,
    strategy_config: str = "config/strategy_params.yaml",
    risk_config: str = "config/risk_params.yaml",
) -> BacktestResults:
    """Convenience function to run a backtest."""
    bt = Backtester(strategy_config, risk_config)
    results = bt.run(data_path)
    results.summary()
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 2:
        print("Usage: python -m backtest.backtester <data_path>")
        print("Example: python -m backtest.backtester data/mnq_15m_2025.csv")
        sys.exit(1)
    
    results = run_backtest(sys.argv[1])
