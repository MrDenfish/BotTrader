"""
ROC Peak-Drawdown Strategy - Multi-Timeframe Version
====================================================

Implements ROC momentum strategy on 5m and 15m timeframes with improved entry logic.

Key differences from 1m version:
- Signal timeframe: 5m or 15m (not 1m)
- Entry modes: 'continuation' (break signal bar high) or 'pullback' (retrace and reclaim)
- Multi-TF ATR: ATR_fast on signal TF, ATR_slow on higher TF (1h for 5m, 4h for 15m)
- All indicators calculated on signal timeframe

Entry Logic:
- 'continuation' mode: Signal bar triggers, then wait for price to break above signal bar high
- 'pullback' mode: Signal bar triggers, then wait for retrace below mid-point and reclaim above mid

This avoids "buying exhaustion close" problem that plagued 1m version.

Date: January 30, 2026
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from decimal import Decimal


@dataclass
class MultiTFConfig:
    """
    Configuration for multi-timeframe ROC peak-drawdown strategy.
    """
    # Strategy identification
    variant_name: str  # "5M" or "15M" or custom name

    # Timeframe configuration
    signal_timeframe: str  # '5m' or '15m' - the timeframe for signals and indicators
    entry_mode: str  # 'continuation' or 'pullback'

    # ROC parameters (calculated on signal_timeframe)
    roc_len: int  # Lookback period for ROC
    roc_ema_len: int  # EMA smoothing length (1 = no smoothing)
    roc_entry_threshold: float  # Entry threshold (e.g., 0.008 = 0.8% for 5m)
    roc_arm_threshold: float  # Minimum ROC peak before enabling peak-DD exit

    # Volume filter (calculated on signal_timeframe)
    vol_sma_len: int  # Volume SMA period
    vol_ratio_min: float  # Minimum volume ratio for entry

    # ATR_fast (profit protection, calculated on signal_timeframe)
    atr_fast_len: int  # ATR period on signal timeframe
    M_fast: float  # Multiplier for fast trailing stop
    fast_trail_activation_k: float  # Profit arm threshold (in ATR units)

    # ATR_slow (catastrophic stop, calculated on higher TF)
    tf_slow: str  # Timeframe for slow ATR ("1h" for 5m signals, "4h" for 15m signals)
    atr_slow_len: int  # ATR period on higher TF
    M_slow: float  # Multiplier for slow stop

    # ROC peak-drawdown exit
    X_dd: float  # Drawdown threshold from ROC peak (e.g., 0.40 = 40%)

    # Entry execution parameters
    entry_max_bars_to_wait: int = 10  # Max bars to wait for continuation/pullback entry
    pullback_retrace_pct: float = 0.5  # For pullback mode: retrace to mid-point of signal bar

    # Execution parameters
    min_bars_after_entry_for_exit: int = 2  # Prevent immediate churn (lower than 1m)
    eps: float = 1e-12  # Small constant to avoid division by zero

    # Position sizing
    order_size: Decimal = Decimal("100.00")  # USD per trade
    fee_rate: Decimal = Decimal("0.006")  # 0.6% (maker+taker combined)

    # Side (LONG only for now)
    side: str = "LONG"

    def __post_init__(self):
        """Validate configuration"""
        assert self.side == "LONG", "Only LONG side implemented currently"
        assert self.signal_timeframe in ['5m', '15m'], "signal_timeframe must be '5m' or '15m'"
        assert self.entry_mode in ['continuation', 'pullback'], "entry_mode must be 'continuation' or 'pullback'"
        assert self.roc_len > 0, "roc_len must be positive"
        assert self.roc_ema_len >= 1, "roc_ema_len must be >= 1"
        assert self.X_dd > 0, "X_dd must be positive"
        assert self.M_fast > 0 and self.M_slow > 0, "ATR multipliers must be positive"

        # Validate tf_slow is appropriate for signal_timeframe
        if self.signal_timeframe == '5m':
            assert self.tf_slow in ['1h', '60min'], "For 5m signals, tf_slow should be '1h'"
        elif self.signal_timeframe == '15m':
            assert self.tf_slow in ['4h', '1h'], "For 15m signals, tf_slow should be '4h' or '1h'"


@dataclass
class PositionStateMultiTF:
    """
    State tracking for an open position in multi-TF strategy.
    """
    symbol: str
    entry_price: Decimal
    entry_time: datetime
    size: Decimal  # Position size in USD

    # Entry mode tracking
    entry_mode: str  # 'continuation' or 'pullback'
    signal_bar_high: Decimal = Decimal("0")  # For continuation: must break above this
    signal_bar_low: Decimal = Decimal("0")  # For pullback: must retrace below this
    signal_bar_mid: Decimal = Decimal("0")  # For pullback: mid-point of signal bar
    waiting_for_entry: bool = True  # True until entry condition met
    bars_waiting: int = 0  # Bars since signal triggered

    # Tracking
    bars_in_trade: int = 0
    high_since_entry: Decimal = Decimal("0")
    roc_peak: float = 0.0

    # Arming flags
    armed_roc_dd: bool = False
    armed_fast_trail: bool = False

    # Stops (initialized when position opens)
    stop_slow: Decimal = Decimal("0")
    stop_fast: Decimal = Decimal("-inf")  # Initialize to -inf for LONG

    # Entry conditions (stored for reference)
    entry_roc: float = 0.0
    entry_atr_slow_pct: float = 0.0
    entry_atr_fast_pct: float = 0.0
    entry_vol_ratio: float = 0.0


class PeakDrawdownStrategyMultiTF:
    """
    Multi-timeframe ROC peak-drawdown strategy state machine.

    Manages positions from signal through entry execution to exit.
    """

    def __init__(self, config: MultiTFConfig):
        self.config = config
        self.positions: Dict[str, PositionStateMultiTF] = {}
        self.signals: Dict[str, PositionStateMultiTF] = {}  # Pending signals waiting for entry
        self.closed_positions: List[Dict] = []

        # Statistics
        self.total_signals = 0
        self.total_entries = 0
        self.total_exits = 0
        self.total_signals_expired = 0

    def check_entry_signal(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> bool:
        """
        Check if ROC + volume conditions are met for entry signal.

        This does NOT execute the entry immediately - it creates a pending signal
        that waits for continuation or pullback condition.
        """
        # Don't signal if already in position or waiting for entry
        if symbol in self.positions or symbol in self.signals:
            return False

        # Entry condition: ROC >= threshold AND vol_ratio >= min
        roc = indicators.get('roc', 0.0)
        vol_ratio = indicators.get('vol_ratio', 0.0)

        entry_signal = (
            roc >= self.config.roc_entry_threshold and
            vol_ratio >= self.config.vol_ratio_min
        )

        return entry_signal

    def create_pending_signal(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """
        Create a pending signal that waits for entry confirmation.

        For continuation mode: wait for price to break signal bar high
        For pullback mode: wait for retrace below mid and reclaim above mid
        """
        signal_bar_high = Decimal(str(bar['high']))
        signal_bar_low = Decimal(str(bar['low']))
        signal_bar_mid = (signal_bar_high + signal_bar_low) / Decimal("2")

        # Create pending signal (not yet a position)
        signal = PositionStateMultiTF(
            symbol=symbol,
            entry_price=Decimal("0"),  # Not yet filled
            entry_time=bar['time'] if 'time' in bar.index else bar.name,
            size=self.config.order_size,
            entry_mode=self.config.entry_mode,
            signal_bar_high=signal_bar_high,
            signal_bar_low=signal_bar_low,
            signal_bar_mid=signal_bar_mid,
            waiting_for_entry=True,
            bars_waiting=0,
            entry_roc=indicators['roc'],
            entry_atr_slow_pct=indicators['atr_slow_pct'],
            entry_atr_fast_pct=indicators['atr_fast_pct'],
            entry_vol_ratio=indicators.get('vol_ratio', 0.0)
        )

        self.signals[symbol] = signal
        self.total_signals += 1

    def check_pending_signals(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> bool:
        """
        Check if pending signal has met entry conditions.

        Returns True if entry condition met and position opened.
        """
        if symbol not in self.signals:
            return False

        signal = self.signals[symbol]
        signal.bars_waiting += 1

        # Expire if waited too long
        if signal.bars_waiting > self.config.entry_max_bars_to_wait:
            del self.signals[symbol]
            self.total_signals_expired += 1
            return False

        bar_high = Decimal(str(bar['high']))
        bar_low = Decimal(str(bar['low']))
        entry_triggered = False
        entry_price = None

        # Continuation mode: break above signal bar high
        if self.config.entry_mode == 'continuation':
            if bar_high > signal.signal_bar_high:
                entry_triggered = True
                # Entry price = breakout price (signal bar high)
                entry_price = signal.signal_bar_high

        # Pullback mode: retrace below mid, then reclaim above mid
        elif self.config.entry_mode == 'pullback':
            # Check if we've retraced below mid
            if bar_low < signal.signal_bar_mid:
                signal.waiting_for_entry = False  # Flag that we've retraced

            # Check if we've reclaimed above mid (after retracing)
            if not signal.waiting_for_entry and bar_high > signal.signal_bar_mid:
                entry_triggered = True
                # Entry price = reclaim price (mid-point)
                entry_price = signal.signal_bar_mid

        # Execute entry if triggered
        if entry_triggered:
            self.execute_entry_from_signal(symbol, bar, entry_price, indicators)
            del self.signals[symbol]
            return True

        return False

    def execute_entry_from_signal(
        self,
        symbol: str,
        bar: pd.Series,
        entry_price: Decimal,
        indicators: pd.Series
    ):
        """
        Execute entry from a pending signal.
        """
        signal = self.signals[symbol]
        entry_time = bar['time'] if 'time' in bar.index else bar.name

        # Convert signal to position
        position = PositionStateMultiTF(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=entry_time,
            size=self.config.order_size,
            entry_mode=signal.entry_mode,
            high_since_entry=Decimal(str(bar['high'])),
            roc_peak=indicators['roc'],
            entry_roc=signal.entry_roc,
            entry_atr_slow_pct=signal.entry_atr_slow_pct,
            entry_atr_fast_pct=indicators['atr_fast_pct'],  # Use current ATR
            entry_vol_ratio=signal.entry_vol_ratio,
            waiting_for_entry=False
        )

        # Calculate initial slow stop
        position.stop_slow = entry_price * (
            Decimal("1") - Decimal(str(self.config.M_slow * indicators['atr_slow_pct']))
        )

        # Initialize fast stop to -inf (will update when armed)
        position.stop_fast = Decimal("-inf")

        self.positions[symbol] = position
        self.total_entries += 1

    def update_position_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """
        Update position tracking (peaks, arming, stops).
        """
        position = self.positions[symbol]

        # Update bars in trade
        position.bars_in_trade += 1

        # Update high since entry
        bar_high = Decimal(str(bar['high']))
        if bar_high > position.high_since_entry:
            position.high_since_entry = bar_high

        # Update ROC peak
        current_roc = indicators['roc']
        if current_roc > position.roc_peak:
            position.roc_peak = current_roc

        # Arm ROC drawdown exit (if ROC peak exceeds threshold)
        if not position.armed_roc_dd:
            if position.roc_peak >= self.config.roc_arm_threshold:
                position.armed_roc_dd = True

        # Arm fast trailing stop (ONLY on profit, not on momentum)
        if not position.armed_fast_trail:
            close_price = Decimal(str(bar['close']))
            atr_fast_dollar = position.entry_atr_fast_pct * float(position.entry_price)
            profit_arm_threshold = position.entry_price + Decimal(str(
                self.config.fast_trail_activation_k * atr_fast_dollar
            ))

            # ARM ONLY ON PROFIT
            if close_price >= profit_arm_threshold:
                position.armed_fast_trail = True

        # Update fast trailing stop (if armed)
        if position.armed_fast_trail:
            atr_fast_pct = indicators['atr_fast_pct']
            trail_distance = Decimal(str(self.config.M_fast * atr_fast_pct))

            bar_close = Decimal(str(bar['close']))
            new_stop_fast = bar_close * (Decimal("1") - trail_distance)

            # Only raise the stop, never lower it
            if new_stop_fast > position.stop_fast:
                position.stop_fast = new_stop_fast

    def check_exit_conditions(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if any exit condition is met.

        Returns:
            (should_exit, exit_reason)
        """
        position = self.positions[symbol]

        # Don't exit too soon (prevent churn)
        if position.bars_in_trade < self.config.min_bars_after_entry_for_exit:
            return False, None

        bar_low = Decimal(str(bar['low']))
        bar_close = Decimal(str(bar['close']))
        current_roc = indicators['roc']

        # Check slow catastrophic stop
        if bar_low <= position.stop_slow:
            return True, "ATR_SLOW_STOP"

        # Check fast trailing stop (if armed)
        if position.armed_fast_trail:
            if bar_low <= position.stop_fast:
                return True, "ATR_FAST_STOP"

        # Check ROC peak drawdown exit (if armed)
        if position.armed_roc_dd:
            roc_dd_from_peak = (position.roc_peak - current_roc) / (
                abs(position.roc_peak) + self.config.eps
            )

            if roc_dd_from_peak >= self.config.X_dd:
                return True, "ROC_PEAK_DD"

        return False, None

    def exit_position(
        self,
        symbol: str,
        bar: pd.Series,
        exit_reason: str
    ):
        """
        Close position and record results.
        """
        position = self.positions[symbol]

        # Determine exit price based on reason
        if exit_reason in ["ATR_SLOW_STOP", "ATR_FAST_STOP"]:
            # Stop hit - use the stop price
            if exit_reason == "ATR_SLOW_STOP":
                exit_price = position.stop_slow
            else:
                exit_price = position.stop_fast
        else:
            # Market exit - use bar close
            exit_price = Decimal(str(bar['close']))

        exit_time = bar['time'] if 'time' in bar.index else bar.name

        # Calculate P&L with proper fee calculation
        qty = position.size / position.entry_price

        # Gross P&L (price difference * quantity)
        gross_pnl = (exit_price - position.entry_price) * qty

        # Fees calculated on notional value (qty * price)
        notional_entry = abs(qty * position.entry_price)
        notional_exit = abs(qty * exit_price)
        fee_entry = notional_entry * self.config.fee_rate
        fee_exit = notional_exit * self.config.fee_rate
        fees = fee_entry + fee_exit

        net_pnl = gross_pnl - fees

        # Record closed position
        self.closed_positions.append({
            'symbol': symbol,
            'entry_time': position.entry_time,
            'exit_time': exit_time,
            'entry_price': float(position.entry_price),
            'exit_price': float(exit_price),
            'entry_mode': position.entry_mode,
            'size': float(position.size),
            'bars_held': position.bars_in_trade,
            'gross_pnl': float(gross_pnl),
            'fees': float(fees),
            'net_pnl': float(net_pnl),
            'exit_reason': exit_reason,
            'entry_roc': position.entry_roc,
            'roc_peak': position.roc_peak,
            'armed_roc_dd': position.armed_roc_dd,
            'armed_fast_trail': position.armed_fast_trail,
            'high_since_entry': float(position.high_since_entry),
        })

        del self.positions[symbol]
        self.total_exits += 1

    def process_bar(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """
        Main bar processing logic - call this for each bar.

        Handles:
        1. Check pending signals for entry execution
        2. Check new entry signals
        3. Update existing positions
        4. Check exit conditions
        """
        # If we have a pending signal, check if entry condition met
        if symbol in self.signals:
            self.check_pending_signals(symbol, bar, indicators)

        # If in position, update and check exits
        if symbol in self.positions:
            self.update_position_state(symbol, bar, indicators)
            should_exit, exit_reason = self.check_exit_conditions(symbol, bar, indicators)
            if should_exit:
                self.exit_position(symbol, bar, exit_reason)
        # If no position and no pending signal, check for new signal
        elif symbol not in self.signals:
            if self.check_entry_signal(symbol, bar, indicators):
                self.create_pending_signal(symbol, bar, indicators)

    def get_results_summary(self) -> Dict:
        """
        Calculate summary statistics from closed positions.
        """
        if not self.closed_positions:
            return {
                'total_trades': 0,
                'total_pnl': 0.0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'avg_bars_held': 0.0,
                'total_signals': self.total_signals,
                'total_entries': self.total_entries,
                'signals_expired': self.total_signals_expired,
                'signal_to_entry_pct': 0.0
            }

        df = pd.DataFrame(self.closed_positions)

        total_pnl = df['net_pnl'].sum()
        total_trades = len(df)

        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] <= 0]

        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

        total_wins = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
        total_losses = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 0.0
        profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

        avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0.0
        avg_loss = losses['net_pnl'].mean() if len(losses) > 0 else 0.0
        avg_bars_held = df['bars_held'].mean()

        signal_to_entry_pct = (self.total_entries / self.total_signals * 100) if self.total_signals > 0 else 0.0

        return {
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_bars_held': avg_bars_held,
            'total_signals': self.total_signals,
            'total_entries': self.total_entries,
            'signals_expired': self.total_signals_expired,
            'signal_to_entry_pct': signal_to_entry_pct,
            'gross_pnl': df['gross_pnl'].sum(),
            'total_fees': df['fees'].sum(),
            'avg_gross_pnl': df['gross_pnl'].mean(),
            'avg_fees': df['fees'].mean()
        }
