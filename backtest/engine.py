"""
Backtest Engine

Core backtesting engine that simulates strategy execution on historical data.
"""

import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Dict
import pandas as pd
from sqlalchemy import create_engine, text

from backtest.config import StrategyConfig, BacktestConfig
from backtest.models import (
    Position, Trade, BacktestResults,
    TradeType, ExitReason
)


class BacktestEngine:
    """
    Backtesting engine that replays historical data and simulates trading decisions.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        backtest_config: BacktestConfig,
        db_url: str
    ):
        self.strategy = strategy_config
        self.config = backtest_config
        self.db_url = db_url

        # State
        self.positions: Dict[str, Position] = {}  # symbol -> Position
        self.capital = backtest_config.initial_capital
        self.equity_curve = []

        # Results
        self.results = BacktestResults(
            strategy_name="BotTrader Strategy",
            start_date=backtest_config.start_date,
            end_date=backtest_config.end_date,
            initial_capital=backtest_config.initial_capital
        )

    def run(self) -> BacktestResults:
        """Execute the backtest"""
        print(f"\n🚀 Starting Backtest")
        print(f"   Period: {self.config.start_date.date()} to {self.config.end_date.date()}")
        print(f"   Initial Capital: ${self.capital:,.2f}")
        print()

        # Load historical data
        print("📊 Loading historical OHLCV data...")
        data = self._load_data()

        # Make config dates timezone-aware to match database timestamps
        import pytz
        start_tz = self.config.start_date.replace(tzinfo=pytz.UTC) if self.config.start_date.tzinfo is None else self.config.start_date
        end_tz = self.config.end_date.replace(tzinfo=pytz.UTC) if self.config.end_date.tzinfo is None else self.config.end_date

        # Filter to actual backtest window (after loading extra data for ROC)
        data_filtered = data[
            (data['time'] >= start_tz) &
            (data['time'] <= end_tz)
        ]
        print(f"   Loaded {len(data_filtered)} candles for backtest period")
        print(f"   (Plus {len(data) - len(data_filtered)} historical candles for ROC calculation)")
        print()

        # Group by time and iterate chronologically
        print("⏳ Simulating trades...")
        timestamps = sorted(data_filtered['time'].unique())

        for i, timestamp in enumerate(timestamps):
            if i % 1000 == 0 and i > 0:
                print(f"   Processed {i}/{len(timestamps)} timestamps ({i/len(timestamps)*100:.1f}%)")

            # Get all candles for this timestamp (from full dataset with ROC)
            candles = data[data['time'] == timestamp]

            # Update existing positions
            self._update_positions(candles, timestamp)

            # Check for new entry signals
            self._check_entry_signals(candles, timestamp)

        # Close any remaining positions
        self._close_remaining_positions()

        # Finalize results
        self.results.final_capital = self.capital
        self.results.calculate_metrics()

        print(f"\n✅ Backtest Complete")
        print(f"   Total Trades: {self.results.total_trades}")
        print(f"   Final Capital: ${self.results.final_capital:,.2f}")
        print()

        return self.results

    def _load_data(self) -> pd.DataFrame:
        """Load historical OHLCV data from database"""
        engine = create_engine(self.db_url)

        # Load extra data for ROC calculation (need historical lookback)
        # 5-min ROC: needs 5 periods back
        # 24-hour ROC: needs 288 periods back (24h * 60min / 5min candles)
        lookback_hours = 48  # Load 48 hours before start to ensure enough data
        adjusted_start = self.config.start_date - timedelta(hours=lookback_hours)

        query = text("""
            SELECT
                time,
                symbol,
                open,
                high,
                low,
                close,
                volume
            FROM ohlcv_data
            WHERE time >= :start_date
              AND time <= :end_date
            ORDER BY time ASC, symbol ASC
        """)

        with engine.connect() as conn:
            df = pd.read_sql(
                query,
                conn,
                params={
                    "start_date": adjusted_start,
                    "end_date": self.config.end_date
                }
            )

        # Convert to Decimal for precise calculations
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].apply(Decimal)

        # Calculate ROC for each symbol
        df = self._calculate_roc_indicators(df)

        return df

    def _calculate_roc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate ROC and RSI indicators for each symbol.
        Matches production: indicators.py calculate_indicators()

        ROC: 5-period rate of change (production uses ROC_WINDOW=5)
        ROC_Diff: First derivative of ROC
        ROC_Diff_STD20: 20-period std dev of ROC_Diff
        RSI: 7-period Relative Strength Index (production uses RSI_WINDOW=7)
        """
        # Process each symbol separately
        symbol_dfs = []

        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol].copy()

            # Sort by time to ensure correct ordering
            symbol_df = symbol_df.sort_values('time')

            # Convert close to float for pandas calculations
            close_float = symbol_df['close'].astype(float)

            # === ROC Calculation (matches production indicators.py:129) ===
            symbol_df['roc'] = close_float.pct_change(periods=self.strategy.roc_window) * 100

            # ROC_Diff: Rate of change of ROC (acceleration)
            symbol_df['roc_diff'] = symbol_df['roc'].diff().fillna(0)

            # ROC_Diff_STD20: Rolling std dev for adaptive acceleration threshold
            symbol_df['roc_diff_std20'] = (
                symbol_df['roc_diff'].rolling(20, min_periods=5).std()
                .fillna(symbol_df['roc_diff'].abs().rolling(5).mean())
                .fillna(0.3)
            )

            # === RSI Calculation (matches production indicators.py:112-119) ===
            delta = close_float.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)

            # Rolling average over RSI window
            avg_gain = gain.rolling(window=self.strategy.rsi_window).mean()
            avg_loss = loss.rolling(window=self.strategy.rsi_window).mean()

            # Calculate RS and RSI
            rs = avg_gain / avg_loss.replace(0, float('nan'))
            symbol_df['rsi'] = (100 - (100 / (1 + rs))).clip(0, 100).fillna(50)

            # Fill NaN for initial periods
            symbol_df['roc'] = symbol_df['roc'].fillna(0)
            symbol_df['rsi'] = symbol_df['rsi'].fillna(50)

            symbol_dfs.append(symbol_df)

        # Recombine all symbols
        result = pd.concat(symbol_dfs, ignore_index=True)
        return result.sort_values(['time', 'symbol'])

    def _update_positions(self, candles: pd.DataFrame, timestamp: datetime):
        """Update existing positions and check exit conditions"""
        for symbol in list(self.positions.keys()):
            # Get candle for this symbol
            symbol_data = candles[candles['symbol'] == symbol]
            if symbol_data.empty:
                continue

            candle = symbol_data.iloc[0]
            position = self.positions[symbol]

            # Update position P&L
            current_price = Decimal(str(candle['close']))
            position.calculate_unrealized_pnl(current_price)

            # Update peak tracking with smoothing (production uses 5-min SMA)
            position.update_peak_with_smoothing(current_price, self.strategy.peak_smoothing_periods)

            # Check exit conditions
            exit_reason = self._check_exit_conditions(position, current_price, timestamp)

            if exit_reason:
                self._close_position(position, current_price, timestamp, exit_reason)

    def _check_exit_conditions(
        self,
        position: Position,
        current_price: Decimal,
        timestamp: datetime
    ) -> Optional[ExitReason]:
        """
        Check if any exit conditions are met.
        Matches production: position_monitor.py:213-319 (peak tracking)

        Exit Priority (production order):
        1. Hard Stop Loss (emergency -4.5%)
        2. Peak Tracking (if enabled and activated)
        3. Take Profit (+3.5%)
        4. Soft Stop Loss (-4.5% with checks)
        5. Max Hold Time (24 hours for peak tracking)
        """

        # Calculate return percentage
        pnl_pct = (current_price - position.entry_price) / position.entry_price

        # === 1. HARD STOP LOSS (Emergency Exit) ===
        if pnl_pct <= -self.strategy.stop_loss_pct:
            return ExitReason.STOP_LOSS

        # === 2. PEAK TRACKING EXIT (Price-based, not ROC-based!) ===
        if self.strategy.peak_tracking_enabled and position.trade_type == TradeType.ROC_MOMENTUM:

            # Check max hold time first (24 hours)
            hold_hours = (timestamp - position.entry_time).total_seconds() / 3600
            if hold_hours >= self.strategy.peak_max_hold_hours:
                return ExitReason.MAX_HOLD_TIME

            # Activate peak tracking after hitting min_profit threshold (+6%)
            if not position.peak_tracking_active and pnl_pct >= self.strategy.peak_min_profit_pct:
                position.peak_tracking_active = True

            # Activate breakeven stop after hitting breakeven threshold (+6%)
            if not position.breakeven_stop_active and pnl_pct >= self.strategy.peak_breakeven_pct:
                position.breakeven_stop_active = True

            # If peak tracking is active, check for price drawdown from peak
            if position.peak_tracking_active:
                # Calculate drawdown from peak price (not entry price!)
                drawdown_from_peak = (position.peak_price - current_price) / position.peak_price

                if drawdown_from_peak >= self.strategy.peak_drawdown_pct:
                    return ExitReason.ROC_PEAK_DROP  # Reusing enum for peak price drop

            # If breakeven stop is active, exit if price drops below entry
            if position.breakeven_stop_active and pnl_pct <= 0:
                return ExitReason.ROC_REVERSAL  # Reusing enum for breakeven stop

        # === 3. TAKE PROFIT ===
        if pnl_pct >= self.strategy.take_profit_pct:
            return ExitReason.TAKE_PROFIT

        return None

    def _check_entry_signals(self, candles: pd.DataFrame, timestamp: datetime):
        """
        Check for new ROC momentum entry signals.
        Matches production: signal_manager.py:346-376

        THREE conditions must be met:
        1. ROC > roc_buy_threshold (7.5% in production)
        2. ROC Acceleration: |ROC_Diff| > max(0.3, 0.5 × ROC_Diff_STD20)
        3. RSI Filter: 40 <= RSI <= 60 (neutral zone only)
        """
        # Don't open new positions if we're at max
        if len(self.positions) >= 10:  # Max 10 concurrent positions
            return

        for _, candle in candles.iterrows():
            symbol = candle['symbol']

            # Skip if already in position
            if symbol in self.positions:
                continue

            # Get current price and indicators
            current_price = Decimal(str(candle['close']))
            roc = Decimal(str(candle.get('roc', 0)))
            roc_diff = Decimal(str(candle.get('roc_diff', 0)))
            roc_diff_std20 = Decimal(str(candle.get('roc_diff_std20', 0.3)))
            rsi = Decimal(str(candle.get('rsi', 50)))

            # === CONDITION 1: ROC Threshold ===
            if roc < self.strategy.roc_buy_threshold:
                continue

            # === CONDITION 2: ROC Acceleration Gate ===
            if self.strategy.roc_accel_enabled:
                accel_threshold = max(
                    self.strategy.roc_accel_min,
                    self.strategy.roc_accel_std_mult * roc_diff_std20
                )
                if abs(roc_diff) < accel_threshold:
                    continue  # ROC not accelerating enough

            # === CONDITION 3: RSI Neutral Zone Filter ===
            if self.strategy.rsi_filter_enabled:
                if not (self.strategy.rsi_neutral_low <= rsi <= self.strategy.rsi_neutral_high):
                    continue  # RSI outside neutral zone (overbought/oversold)

            # All conditions met - open position
            self._open_position(
                symbol=symbol,
                entry_price=current_price,
                entry_time=timestamp,
                trade_type=TradeType.ROC_MOMENTUM,
                order_size=self.strategy.order_size_roc,
                initial_roc=None  # Don't track ROC for exits
            )

    def _open_position(
        self,
        symbol: str,
        entry_price: Decimal,
        entry_time: datetime,
        trade_type: TradeType,
        order_size: Decimal,
        initial_roc: Optional[Decimal] = None
    ):
        """Open a new position"""
        # Calculate position size in crypto
        size = order_size / entry_price

        # Calculate fees
        entry_fee = order_size * self.strategy.fee_rate

        # Check if we have enough capital
        total_cost = order_size + entry_fee
        if total_cost > self.capital:
            return  # Not enough capital

        # Deduct from capital
        self.capital -= total_cost

        # Create position (no peak_roc parameter - using price-based tracking now)
        position = Position(
            symbol=symbol,
            side="buy",
            entry_price=entry_price,
            size=size,
            entry_time=entry_time,
            trade_type=trade_type,
            entry_fee=entry_fee,
            peak_price=entry_price,
            price_history=[],  # Initialize price history for smoothing
            peak_tracking_active=False,
            breakeven_stop_active=False
        )

        self.positions[symbol] = position

        if self.config.verbose and len(self.positions) % 10 == 0:
            print(f"   {entry_time.date()} | OPEN {symbol} @ ${entry_price:,.2f} | Positions: {len(self.positions)}")

    def _close_position(
        self,
        position: Position,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: ExitReason
    ):
        """Close an existing position"""
        # Calculate exit values
        exit_value = exit_price * position.size
        exit_fee = exit_value * self.strategy.fee_rate

        # Calculate P&L
        gross_pnl = (exit_price - position.entry_price) * position.size
        net_pnl = gross_pnl - position.entry_fee - exit_fee

        # Add proceeds back to capital
        self.capital += exit_value - exit_fee

        # Calculate hold time
        hold_time = (exit_time - position.entry_time).total_seconds() / 3600

        # Calculate return %
        return_pct = ((exit_price - position.entry_price) / position.entry_price) * Decimal("100")

        # Create trade record
        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            trade_type=position.trade_type,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            entry_fee=position.entry_fee,
            exit_price=exit_price,
            exit_time=exit_time,
            exit_fee=exit_fee,
            exit_reason=exit_reason,
            size=position.size,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=return_pct,
            hold_time_hours=hold_time,
            peak_price=position.peak_price,
            peak_roc=None  # No longer tracking ROC for exits
        )

        # Add to results
        self.results.add_trade(trade)

        # Remove from positions
        del self.positions[position.symbol]

        if self.config.verbose and self.results.total_trades % 10 == 0:
            print(f"   {exit_time.date()} | CLOSE {position.symbol} @ ${exit_price:,.2f} | P&L: ${net_pnl:+,.2f} | Reason: {exit_reason.value}")

    def _close_remaining_positions(self):
        """Close all open positions at end of backtest"""
        import pytz
        # Make end_date timezone-aware to match position entry_time
        end_tz = self.config.end_date.replace(tzinfo=pytz.UTC) if self.config.end_date.tzinfo is None else self.config.end_date

        for symbol, position in list(self.positions.items()):
            # Use last known price (simplified - would query actual last price)
            self._close_position(
                position,
                position.entry_price,  # Breakeven close
                end_tz,
                ExitReason.END_OF_BACKTEST
            )
