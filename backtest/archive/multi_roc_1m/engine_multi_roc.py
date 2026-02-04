"""
Multi-ROC Backtest Engine
==========================

Backtesting engine specifically for ROC_MOMO_20M and ROC_MOMO_24H strategies.

Date: January 27, 2026
Purpose: Test Multi-ROC momentum strategies with multiple exit configurations
"""

import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import pandas as pd
from sqlalchemy import create_engine, text

from backtest.config_multi_roc import MultiROCConfig
from backtest.models import (
    Position, Trade, BacktestResults,
    TradeType, ExitReason
)


class MultiROCBacktestEngine:
    """
    Backtesting engine for Multi-ROC strategies (ROC_MOMO_20M and ROC_MOMO_24H)
    """

    def __init__(
        self,
        config: MultiROCConfig,
        start_date: datetime,
        end_date: datetime,
        db_url: str,
        test_20m: bool = True,
        test_24h: bool = True,
        initial_capital: Decimal = Decimal("10000.00"),
        verbose: bool = True
    ):
        """
        Args:
            config: MultiROCConfig with entry/exit parameters
            start_date: Backtest start date
            end_date: Backtest end date
            db_url: Database connection string
            test_20m: Test ROC_MOMO_20M strategy
            test_24h: Test ROC_MOMO_24H strategy
            initial_capital: Starting capital
            verbose: Print progress messages
        """
        self.config = config
        self.start_date = start_date
        self.end_date = end_date
        self.db_url = db_url
        self.test_20m = test_20m
        self.test_24h = test_24h
        self.initial_capital = initial_capital
        self.verbose = verbose

        # State
        self.positions: Dict[str, Position] = {}
        self.capital = initial_capital
        self.equity_curve = []

        # Results
        self.results = BacktestResults(
            strategy_name=f"Multi-ROC: {config.name}",
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital
        )

    def run(self) -> BacktestResults:
        """Execute the backtest"""
        if self.verbose:
            print(f"\n🚀 Starting Multi-ROC Backtest")
            print(f"   Config: {self.config.name}")
            print(f"   Period: {self.start_date.date()} to {self.end_date.date()}")
            print(f"   Testing: ", end="")
            if self.test_20m and self.test_24h:
                print("Both 20M and 24H")
            elif self.test_20m:
                print("20M only")
            elif self.test_24h:
                print("24H only")
            print()

        # Load historical data
        if self.verbose:
            print("📊 Loading historical OHLCV data...")
        data = self._load_data()

        if self.verbose:
            print(f"   Loaded {len(data)} candles")
            print()

        # Iterate chronologically
        if self.verbose:
            print("⏳ Simulating trades...")

        timestamps = sorted(data['time'].unique())
        for i, timestamp in enumerate(timestamps):
            if self.verbose and i % 1000 == 0 and i > 0:
                print(f"   Processed {i}/{len(timestamps)} ({i/len(timestamps)*100:.1f}%)")

            # Get candles for this timestamp
            candles = data[data['time'] == timestamp]

            # Update existing positions
            self._update_positions(candles, timestamp)

            # Check for new entry signals
            self._check_entry_signals(candles, timestamp)

        # Close remaining positions
        self._close_remaining_positions(data)

        # Finalize results
        self.results.final_capital = self.capital
        self.results.calculate_metrics()

        if self.verbose:
            print(f"\n✅ Backtest Complete")
            print(f"   Trades: {len(self.results.trades)}")
            print(f"   Final P&L: ${self.capital - self.initial_capital:.2f}")
            print()

        return self.results

    def _load_data(self) -> pd.DataFrame:
        """Load OHLCV data from database with indicators"""
        engine = create_engine(self.db_url)

        # Load extra data for indicator calculation
        # Need 24 hours = 1440 1-min candles for 24h ROC
        # Need 20 1-min candles for 20m ROC
        # Use 48-hour lookback to ensure sufficient data for all indicators
        lookback_hours = 48

        query_start = self.start_date - timedelta(hours=lookback_hours)

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
            WHERE time >= :start_time
                AND time <= :end_time
            ORDER BY time, symbol
        """)

        with engine.connect() as conn:
            df = pd.read_sql(
                query,
                conn,
                params={
                    'start_time': query_start,
                    'end_time': self.end_date
                }
            )

        # Calculate indicators
        df = self._calculate_indicators(df)

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate ROC, RSI, and other indicators"""
        # Process each symbol separately
        symbol_dfs = []

        for symbol in df['symbol'].unique():
            symbol_df = df[df['symbol'] == symbol].copy()
            symbol_df = symbol_df.sort_values('time')

            close_float = symbol_df['close'].astype(float)

            # ============================================================
            # ROC 20M (from 1-min candles)
            # ============================================================
            # 20 minutes = 20 periods of 1-min candles
            symbol_df['roc_20m'] = close_float.pct_change(periods=20) * 100

            # ============================================================
            # ROC 24H (from 1-min candles)
            # ============================================================
            # 24 hours = 1440 periods of 1-min candles (1440 minutes = 24 hours)
            symbol_df['roc_24h'] = close_float.pct_change(periods=1440) * 100

            # ============================================================
            # RSI
            # ============================================================
            delta = close_float.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)

            # Use config RSI window
            avg_gain = gain.rolling(window=self.config.rsi_window).mean()
            avg_loss = loss.rolling(window=self.config.rsi_window).mean()

            rs = avg_gain / avg_loss.replace(0, float('nan'))
            symbol_df['rsi'] = (100 - (100 / (1 + rs))).clip(0, 100).fillna(50)

            # ============================================================
            # ATR (for trailing stops)
            # ============================================================
            high_float = symbol_df['high'].astype(float)
            low_float = symbol_df['low'].astype(float)

            tr1 = high_float - low_float
            tr2 = abs(high_float - close_float.shift())
            tr3 = abs(low_float - close_float.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            symbol_df['atr'] = tr.rolling(window=self.config.atr_window).mean()

            # Fill NaN
            symbol_df['roc_20m'] = symbol_df['roc_20m'].fillna(0)
            symbol_df['roc_24h'] = symbol_df['roc_24h'].fillna(0)
            symbol_df['rsi'] = symbol_df['rsi'].fillna(50)
            symbol_df['atr'] = symbol_df['atr'].fillna(0)

            symbol_dfs.append(symbol_df)

        result = pd.concat(symbol_dfs, ignore_index=True)
        return result.sort_values(['time', 'symbol'])

    def _check_entry_signals(self, candles: pd.DataFrame, timestamp: datetime):
        """Check for ROC_MOMO entry signals"""
        # Don't open too many positions
        if len(self.positions) >= 10:
            return

        for _, candle in candles.iterrows():
            symbol = candle['symbol']

            # Skip if already in position
            if symbol in self.positions:
                continue

            current_price = Decimal(str(candle['close']))
            roc_20m = Decimal(str(candle.get('roc_20m', 0)))
            roc_24h = Decimal(str(candle.get('roc_24h', 0)))
            rsi = Decimal(str(candle.get('rsi', 50)))

            # ============================================================
            # CHECK ROC_MOMO_20M ENTRY
            # ============================================================
            if self.test_20m:
                if (roc_20m > self.config.roc_20m_buy_threshold and
                    self.config.roc_20m_rsi_low <= rsi <= self.config.roc_20m_rsi_high):

                    self._open_position(
                        symbol=symbol,
                        entry_price=current_price,
                        entry_time=timestamp,
                        trade_type=TradeType.ROC_MOMO_20M,
                        order_size=self.config.order_size_20m,
                        candle=candle
                    )
                    continue  # Don't check 24H if 20M triggered (priority)

            # ============================================================
            # CHECK ROC_MOMO_24H ENTRY
            # ============================================================
            if self.test_24h:
                if (roc_24h > self.config.roc_24h_buy_threshold and
                    self.config.roc_24h_rsi_low <= rsi <= self.config.roc_24h_rsi_high):

                    self._open_position(
                        symbol=symbol,
                        entry_price=current_price,
                        entry_time=timestamp,
                        trade_type=TradeType.ROC_MOMO_24H,
                        order_size=self.config.order_size_24h,
                        candle=candle
                    )

    def _open_position(
        self,
        symbol: str,
        entry_price: Decimal,
        entry_time: datetime,
        trade_type: TradeType,
        order_size: Decimal,
        candle: pd.Series
    ):
        """Open a new position"""
        # Calculate position size
        size = order_size / entry_price

        # Calculate fees
        entry_fee = order_size * self.config.fee_rate

        # Check capital
        total_cost = order_size + entry_fee
        if total_cost > self.capital:
            return

        # Deduct from capital
        self.capital -= total_cost

        # Get ATR for trailing stop
        atr = Decimal(str(candle.get('atr', 0)))

        # Create position
        position = Position(
            symbol=symbol,
            side="buy",
            entry_price=entry_price,
            size=size,
            entry_time=entry_time,
            trade_type=trade_type,
            entry_fee=entry_fee,
            peak_price=entry_price,
            price_history=[],
            peak_tracking_active=False,
            breakeven_stop_active=False,
            trailing_stop_price=None,
            trailing_stop_active=False,
            last_atr=atr
        )

        self.positions[symbol] = position

    def _update_positions(self, candles: pd.DataFrame, timestamp: datetime):
        """Update existing positions and check exits"""
        for symbol in list(self.positions.keys()):
            # Get candle for this symbol
            symbol_data = candles[candles['symbol'] == symbol]
            if symbol_data.empty:
                continue

            candle = symbol_data.iloc[0]
            position = self.positions[symbol]

            current_price = Decimal(str(candle['close']))
            roc_20m = Decimal(str(candle.get('roc_20m', 0)))
            roc_24h = Decimal(str(candle.get('roc_24h', 0)))
            atr = Decimal(str(candle.get('atr', 0)))

            # Update P&L
            position.calculate_unrealized_pnl(current_price)
            pnl_pct = position.calculate_return_pct(current_price)

            # Update peak tracking
            position.update_peak_with_smoothing(current_price, self.config.peak_smoothing_periods)

            # Update trailing stop
            self._update_trailing_stop(position, current_price, atr)

            # Check exit conditions
            exit_reason = self._check_exit_conditions(
                position, current_price, pnl_pct, roc_20m, roc_24h, timestamp
            )

            if exit_reason:
                self._close_position(position, current_price, timestamp, exit_reason)

    def _update_trailing_stop(self, position: Position, current_price: Decimal, atr: Decimal):
        """Update ATR-based trailing stop (for ROC_MOMO_20M)"""
        if position.trade_type != TradeType.ROC_MOMO_20M:
            return

        if not self.config.use_trailing_stop_20m:
            return

        pnl_pct = position.calculate_return_pct(current_price)

        # Activate trailing stop at threshold
        if not position.trailing_stop_active and pnl_pct >= self.config.trailing_stop_activation_pct * 100:
            position.trailing_stop_active = True

        if position.trailing_stop_active:
            # Calculate stop distance
            if atr > 0:
                stop_distance = atr * self.config.trailing_stop_atr_mult

                # Clamp to min/max distance
                min_distance = current_price * self.config.trailing_min_distance_pct
                max_distance = current_price * self.config.trailing_max_distance_pct
                stop_distance = max(min_distance, min(stop_distance, max_distance))

                new_stop = current_price - stop_distance

                # Only move stop up, never down
                if position.trailing_stop_price is None or new_stop > position.trailing_stop_price:
                    position.trailing_stop_price = new_stop

            position.last_atr = atr

    def _check_exit_conditions(
        self,
        position: Position,
        current_price: Decimal,
        pnl_pct: Decimal,
        roc_20m: Decimal,
        roc_24h: Decimal,
        timestamp: datetime
    ) -> Optional[ExitReason]:
        """Check all exit conditions for a position"""

        # ============================================================
        # MAX HOLD TIME
        # ============================================================
        hold_hours = (timestamp - position.entry_time).total_seconds() / 3600
        max_hold = (self.config.max_hold_hours_20m if position.trade_type == TradeType.ROC_MOMO_20M
                   else self.config.max_hold_hours_24h)

        if hold_hours >= max_hold:
            return ExitReason.MAX_HOLD_TIME

        # ============================================================
        # STOP LOSS (if configured)
        # ============================================================
        if self.config.stop_loss is not None:
            if pnl_pct <= self.config.stop_loss * 100:
                return ExitReason.STOP_LOSS

        # ============================================================
        # TRAILING STOP (20M only)
        # ============================================================
        if (position.trade_type == TradeType.ROC_MOMO_20M and
            position.trailing_stop_active and
            position.trailing_stop_price is not None):
            if current_price <= position.trailing_stop_price:
                return ExitReason.TRAILING_STOP

        # ============================================================
        # ROC THRESHOLD REVERSAL
        # ============================================================
        if self.config.use_roc_threshold_exit:
            if position.trade_type == TradeType.ROC_MOMO_20M:
                if roc_20m < self.config.roc_20m_sell_threshold:
                    return ExitReason.ROC_REVERSAL_20M
            elif position.trade_type == TradeType.ROC_MOMO_24H:
                if roc_24h < self.config.roc_24h_sell_threshold:
                    return ExitReason.ROC_REVERSAL_24H

        # ============================================================
        # PEAK TRACKING (24H only)
        # ============================================================
        if (position.trade_type == TradeType.ROC_MOMO_24H and
            self.config.use_peak_tracking_24h):

            # Activate peak tracking at threshold
            if not position.peak_tracking_active and pnl_pct >= self.config.peak_tracking_activation_pct * 100:
                position.peak_tracking_active = True

            if position.peak_tracking_active:
                # Check drawdown from peak
                if position.peak_price > 0:
                    drawdown_from_peak = (position.peak_price - current_price) / position.peak_price
                    if drawdown_from_peak >= self.config.peak_tracking_drawdown_pct:
                        return ExitReason.PEAK_TRACKING

        # ============================================================
        # TAKE PROFIT (if configured)
        # ============================================================
        if position.trade_type == TradeType.ROC_MOMO_20M and self.config.take_profit_20m is not None:
            if pnl_pct >= self.config.take_profit_20m * 100:
                return ExitReason.TAKE_PROFIT

        if position.trade_type == TradeType.ROC_MOMO_24H and self.config.take_profit_24h is not None:
            if pnl_pct >= self.config.take_profit_24h * 100:
                return ExitReason.TAKE_PROFIT

        return None

    def _close_position(
        self,
        position: Position,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: ExitReason
    ):
        """Close a position and record the trade"""
        # Calculate P&L
        gross_pnl = (exit_price - position.entry_price) * position.size
        exit_fee = (exit_price * position.size) * self.config.fee_rate
        net_pnl = gross_pnl - position.entry_fee - exit_fee

        # Return capital
        exit_value = exit_price * position.size
        self.capital += exit_value - exit_fee

        # Create trade record
        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            trade_type=position.trade_type,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            entry_fee=position.entry_fee,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_fee=exit_fee,
            exit_reason=exit_reason,
            size=position.size,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=float(position.calculate_return_pct(exit_price)),
            hold_time_hours=float((exit_time - position.entry_time).total_seconds() / 3600),
            peak_price=position.peak_price if hasattr(position, 'peak_price') else None
        )

        self.results.trades.append(trade)

        # Remove from positions
        del self.positions[position.symbol]

    def _close_remaining_positions(self, data: pd.DataFrame):
        """Close all remaining positions at end of backtest"""
        if not self.positions:
            return

        # Get final prices
        final_candles = data[data['time'] == data['time'].max()]

        for symbol, position in list(self.positions.items()):
            symbol_data = final_candles[final_candles['symbol'] == symbol]
            if symbol_data.empty:
                continue

            final_price = Decimal(str(symbol_data.iloc[0]['close']))
            final_time = symbol_data.iloc[0]['time']

            self._close_position(position, final_price, final_time, ExitReason.END_OF_BACKTEST)
