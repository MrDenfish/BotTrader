"""
Enhanced 15m ROC Strategy with Professional Upgrades
=====================================================

Key improvements:
1. ATR-normalized ROC (roc_score = roc / atr_fast_pct) - volatility-adaptive
2. 1h trend filter (1h close > 1h EMA(50)) - regime awareness
3. Scale-out profit taking (partial at +k×ATR, let rest run)
4. Tighter stops only after clearing fee hurdle

This addresses the core issues:
- Absolute ROC thresholds fail across volatility regimes
- No trend filter causes "buy spike → fade" instead of "buy breakout in uptrend"
- Current exits give back too much (ROC peak-DD exits late)
- Fees destroy edge before allowing profit to develop

Date: January 30, 2026
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from decimal import Decimal


@dataclass
class Enhanced15mConfig:
    """Configuration for enhanced 15m strategy."""

    variant_name: str

    # Entry: ATR-normalized ROC
    roc_len: int = 9
    roc_ema_len: int = 5
    roc_score_threshold: float = 1.0  # ROC ≥ 1.0 × ATR_fast% (adaptive!)
    vol_ratio_min: float = 2.0
    vol_sma_len: int = 20

    # Trend filter (1h)
    trend_filter_enabled: bool = True
    trend_ema_len: int = 50  # 1h EMA(50)

    # ATR parameters
    atr_fast_len: int = 14  # On 15m
    atr_slow_len: int = 14  # On 4h
    M_fast: float = 2.0
    M_slow: float = 3.0

    # ROC peak-drawdown exit
    X_dd: float = 0.40
    roc_arm_threshold_score: float = 0.5  # ROC peak ≥ 0.5× ATR to arm DD exit

    # Scale-out profit taking
    scale_out_enabled: bool = True
    scale_out_pct: float = 0.5  # Take 50% profit when profit target hit
    profit_target_k: float = 1.0  # Exit 50% at +1.0× ATR_fast%

    # Fee-aware stop arming
    fast_trail_activation_mode: str = "fee_multiple"  # "profit" or "fee_multiple"
    fast_trail_fee_multiple: float = 2.5  # Arm fast trail only after profit > 2.5× fees

    # Entry execution
    entry_mode: str = 'continuation'
    entry_max_bars_to_wait: int = 10
    min_bars_after_entry_for_exit: int = 2

    # Position sizing
    order_size: Decimal = Decimal("100.00")
    fee_rate: Decimal = Decimal("0.006")

    side: str = "LONG"


@dataclass
class PositionEnhanced:
    """Position state for enhanced strategy."""

    symbol: str
    entry_price: Decimal
    entry_time: datetime
    size: Decimal

    # Scale-out tracking
    quantity_remaining: float = 1.0  # 1.0 = 100% still in position
    scaled_out: bool = False
    scale_out_price: Decimal = Decimal("0")

    # Entry conditions
    entry_mode: str = 'continuation'
    signal_bar_high: Decimal = Decimal("0")
    waiting_for_entry: bool = True
    bars_waiting: int = 0

    # Tracking
    bars_in_trade: int = 0
    high_since_entry: Decimal = Decimal("0")
    roc_peak: float = 0.0

    # Arming
    armed_roc_dd: bool = False
    armed_fast_trail: bool = False

    # Stops
    stop_slow: Decimal = Decimal("0")
    stop_fast: Decimal = Decimal("-inf")

    # Entry conditions (for reference)
    entry_roc: float = 0.0
    entry_roc_score: float = 0.0
    entry_atr_fast_pct: float = 0.0
    entry_atr_slow_pct: float = 0.0
    entry_vol_ratio: float = 0.0
    trend_1h_ema: float = 0.0


class Strategy15mEnhanced:
    """
    Enhanced 15m ROC strategy with professional improvements.
    """

    def __init__(self, config: Enhanced15mConfig):
        self.config = config
        self.positions: Dict[str, PositionEnhanced] = {}
        self.signals: Dict[str, PositionEnhanced] = {}
        self.closed_positions: List[Dict] = []

        # Statistics
        self.total_signals = 0
        self.total_entries = 0
        self.total_exits = 0
        self.total_signals_expired = 0
        self.total_scale_outs = 0

    def check_entry_signal(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> bool:
        """
        Check entry conditions:
        1. ROC score ≥ threshold (ATR-normalized)
        2. Volume ratio ≥ min
        3. [Optional] 1h trend filter: 1h close > 1h EMA(50)
        """
        if symbol in self.positions or symbol in self.signals:
            return False

        # Get indicators
        roc = indicators.get('roc', 0.0)
        atr_fast_pct = indicators.get('atr_fast_pct', 0.0)
        vol_ratio = indicators.get('vol_ratio', 0.0)

        if pd.isna(roc) or pd.isna(atr_fast_pct) or atr_fast_pct == 0:
            return False

        # ATR-normalized ROC score
        roc_score = roc / atr_fast_pct

        # Base conditions
        roc_condition = roc_score >= self.config.roc_score_threshold
        vol_condition = vol_ratio >= self.config.vol_ratio_min

        # Trend filter (if enabled)
        trend_condition = True
        if self.config.trend_filter_enabled:
            close_1h = indicators.get('close_1h', 0.0)
            ema_1h = indicators.get('ema50_1h', 0.0)

            if not pd.isna(close_1h) and not pd.isna(ema_1h):
                trend_condition = close_1h > ema_1h
            else:
                trend_condition = False  # No trend data = no signal

        return roc_condition and vol_condition and trend_condition

    def create_pending_signal(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """Create pending signal waiting for continuation entry."""
        signal = PositionEnhanced(
            symbol=symbol,
            entry_price=Decimal("0"),
            entry_time=bar['time'] if 'time' in bar.index else bar.name,
            size=self.config.order_size,
            entry_mode=self.config.entry_mode,
            signal_bar_high=Decimal(str(bar['high'])),
            waiting_for_entry=True,
            bars_waiting=0,
            entry_roc=indicators['roc'],
            entry_roc_score=indicators['roc'] / indicators['atr_fast_pct'],
            entry_atr_fast_pct=indicators['atr_fast_pct'],
            entry_atr_slow_pct=indicators['atr_slow_pct'],
            entry_vol_ratio=indicators.get('vol_ratio', 0.0),
            trend_1h_ema=indicators.get('ema50_1h', 0.0)
        )

        self.signals[symbol] = signal
        self.total_signals += 1

    def check_pending_signals(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> bool:
        """Check if pending signal triggers entry."""
        if symbol not in self.signals:
            return False

        signal = self.signals[symbol]
        signal.bars_waiting += 1

        # Expire if waited too long
        if signal.bars_waiting > self.config.entry_max_bars_to_wait:
            del self.signals[symbol]
            self.total_signals_expired += 1
            return False

        # Continuation: break above signal bar high
        if bar['high'] > float(signal.signal_bar_high):
            self.execute_entry_from_signal(symbol, bar, signal.signal_bar_high, indicators)
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
        """Execute entry from pending signal."""
        signal = self.signals[symbol]

        position = PositionEnhanced(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=bar['time'] if 'time' in bar.index else bar.name,
            size=self.config.order_size,
            high_since_entry=Decimal(str(bar['high'])),
            roc_peak=indicators['roc'],
            entry_roc=signal.entry_roc,
            entry_roc_score=signal.entry_roc_score,
            entry_atr_fast_pct=indicators['atr_fast_pct'],
            entry_atr_slow_pct=signal.entry_atr_slow_pct,
            entry_vol_ratio=signal.entry_vol_ratio,
            trend_1h_ema=signal.trend_1h_ema,
            waiting_for_entry=False
        )

        # Calculate slow stop
        position.stop_slow = entry_price * (
            Decimal("1") - Decimal(str(self.config.M_slow * indicators['atr_slow_pct']))
        )

        position.stop_fast = Decimal("-inf")

        self.positions[symbol] = position
        self.total_entries += 1

    def update_position_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """Update position: check scale-out, update peaks, arm stops."""
        position = self.positions[symbol]
        position.bars_in_trade += 1

        # Update high since entry
        bar_high = Decimal(str(bar['high']))
        if bar_high > position.high_since_entry:
            position.high_since_entry = bar_high

        # Update ROC peak
        current_roc = indicators['roc']
        if current_roc > position.roc_peak:
            position.roc_peak = current_roc

        # Check scale-out profit target
        if self.config.scale_out_enabled and not position.scaled_out:
            current_price = Decimal(str(bar['close']))
            atr_dollar = Decimal(str(position.entry_atr_fast_pct * float(position.entry_price)))
            profit_target = position.entry_price + (atr_dollar * Decimal(str(self.config.profit_target_k)))

            if current_price >= profit_target:
                # Scale out!
                position.scaled_out = True
                position.scale_out_price = current_price
                position.quantity_remaining = 1.0 - self.config.scale_out_pct
                self.total_scale_outs += 1

        # Arm ROC drawdown exit (using ATR-normalized threshold)
        if not position.armed_roc_dd:
            roc_peak_score = position.roc_peak / position.entry_atr_fast_pct
            if roc_peak_score >= self.config.roc_arm_threshold_score:
                position.armed_roc_dd = True

        # Arm fast trailing stop (fee-aware)
        if not position.armed_fast_trail:
            current_price = Decimal(str(bar['close']))
            unrealized_pnl = (current_price - position.entry_price) * Decimal(str(position.size / position.entry_price))

            # Estimate fees for this trade
            notional = abs(float(position.size))
            round_trip_fees = notional * float(self.config.fee_rate) * 2

            if self.config.fast_trail_activation_mode == "fee_multiple":
                # Arm only after profit > fee_multiple × fees
                profit_threshold = Decimal(str(round_trip_fees * self.config.fast_trail_fee_multiple))
                if unrealized_pnl >= profit_threshold:
                    position.armed_fast_trail = True
            else:
                # Original mode: arm on any profit
                if unrealized_pnl > 0:
                    position.armed_fast_trail = True

        # Update fast trailing stop (if armed)
        if position.armed_fast_trail:
            atr_fast_pct = indicators['atr_fast_pct']
            trail_distance = Decimal(str(self.config.M_fast * atr_fast_pct))

            bar_close = Decimal(str(bar['close']))
            new_stop_fast = bar_close * (Decimal("1") - trail_distance)

            if new_stop_fast > position.stop_fast:
                position.stop_fast = new_stop_fast

    def check_exit_conditions(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> Tuple[bool, Optional[str]]:
        """Check exit conditions."""
        position = self.positions[symbol]

        if position.bars_in_trade < self.config.min_bars_after_entry_for_exit:
            return False, None

        bar_low = Decimal(str(bar['low']))
        current_roc = indicators['roc']

        # Slow catastrophic stop
        if bar_low <= position.stop_slow:
            return True, "ATR_SLOW_STOP"

        # Fast trailing stop (if armed)
        if position.armed_fast_trail:
            if bar_low <= position.stop_fast:
                return True, "ATR_FAST_STOP"

        # ROC peak drawdown (if armed)
        if position.armed_roc_dd:
            roc_dd_from_peak = (position.roc_peak - current_roc) / (abs(position.roc_peak) + 1e-12)

            if roc_dd_from_peak >= self.config.X_dd:
                return True, "ROC_PEAK_DD"

        return False, None

    def exit_position(
        self,
        symbol: str,
        bar: pd.Series,
        exit_reason: str
    ):
        """Exit position and record results."""
        position = self.positions[symbol]

        # Determine exit price
        if exit_reason in ["ATR_SLOW_STOP", "ATR_FAST_STOP"]:
            if exit_reason == "ATR_SLOW_STOP":
                final_exit_price = position.stop_slow
            else:
                final_exit_price = position.stop_fast
        else:
            final_exit_price = Decimal(str(bar['close']))

        exit_time = bar['time'] if 'time' in bar.index else bar.name

        # Calculate P&L considering scale-out
        total_qty = position.size / position.entry_price

        if position.scaled_out:
            # Scale-out portion
            qty_scaled = total_qty * Decimal(str(self.config.scale_out_pct))
            pnl_scaled = (position.scale_out_price - position.entry_price) * qty_scaled

            # Remaining portion
            qty_remaining = total_qty * Decimal(str(position.quantity_remaining))
            pnl_remaining = (final_exit_price - position.entry_price) * qty_remaining

            gross_pnl = pnl_scaled + pnl_remaining

            # Fees: entry + scale-out + final exit
            notional_entry = abs(total_qty * position.entry_price)
            notional_scale_out = abs(qty_scaled * position.scale_out_price)
            notional_final = abs(qty_remaining * final_exit_price)

            fees = (notional_entry + notional_scale_out + notional_final) * self.config.fee_rate
        else:
            # No scale-out: simple calculation
            gross_pnl = (final_exit_price - position.entry_price) * total_qty

            notional_entry = abs(total_qty * position.entry_price)
            notional_exit = abs(total_qty * final_exit_price)
            fees = (notional_entry + notional_exit) * self.config.fee_rate

        net_pnl = gross_pnl - fees

        # Record trade
        self.closed_positions.append({
            'symbol': symbol,
            'entry_time': position.entry_time,
            'exit_time': exit_time,
            'entry_price': float(position.entry_price),
            'exit_price': float(final_exit_price),
            'scale_out_price': float(position.scale_out_price) if position.scaled_out else None,
            'scaled_out': position.scaled_out,
            'size': float(position.size),
            'bars_held': position.bars_in_trade,
            'gross_pnl': float(gross_pnl),
            'fees': float(fees),
            'net_pnl': float(net_pnl),
            'exit_reason': exit_reason,
            'entry_roc': position.entry_roc,
            'entry_roc_score': position.entry_roc_score,
            'roc_peak': position.roc_peak,
            'armed_roc_dd': position.armed_roc_dd,
            'armed_fast_trail': position.armed_fast_trail,
            'entry_vol_ratio': position.entry_vol_ratio,
            'trend_1h_ema': position.trend_1h_ema
        })

        del self.positions[symbol]
        self.total_exits += 1

    def process_bar(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """Process one bar."""
        # Check pending signals
        if symbol in self.signals:
            self.check_pending_signals(symbol, bar, indicators)

        # Update and check exits for positions
        if symbol in self.positions:
            self.update_position_state(symbol, bar, indicators)
            should_exit, exit_reason = self.check_exit_conditions(symbol, bar, indicators)
            if should_exit:
                self.exit_position(symbol, bar, exit_reason)
        # Check for new signals
        elif symbol not in self.signals:
            if self.check_entry_signal(symbol, bar, indicators):
                self.create_pending_signal(symbol, bar, indicators)

    def get_results_summary(self) -> Dict:
        """Calculate summary statistics."""
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
                'signal_to_entry_pct': 0.0,
                'scale_outs': self.total_scale_outs
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

        scale_out_rate = (df['scaled_out'].sum() / len(df) * 100) if len(df) > 0 else 0.0

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
            'scale_outs': self.total_scale_outs,
            'scale_out_rate': scale_out_rate,
            'gross_pnl': df['gross_pnl'].sum(),
            'total_fees': df['fees'].sum(),
            'avg_gross_pnl': df['gross_pnl'].mean(),
            'avg_fees': df['fees'].mean()
        }
