"""
ROC Peak-Drawdown Strategy State Machine
==========================================

Implements the core strategy logic for ROC peak-drawdown momentum trading:
- Entry: ROC threshold + volume filter
- Primary exit: ROC drops X% from post-entry peak
- Risk management: Dual ATR% stops (fast trailing, slow catastrophic)
- Arming mechanisms to prevent premature exits

Date: January 28, 2026
Spec: /docs/planning/roc_dual_atrpct_strategy_spec.md
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime
from decimal import Decimal


@dataclass
class PeakDrawdownConfig:
    """
    Configuration for ROC peak-drawdown strategy.

    All parameters must be explicitly set (no defaults except eps).
    """
    # Strategy identification
    variant_name: str  # "A" or "B" or custom name

    # ROC parameters
    roc_len: int  # Lookback period for ROC
    roc_ema_len: int  # EMA smoothing length (1 = no smoothing)
    roc_entry_threshold: float  # Entry threshold (e.g., 0.003 = 0.3%)
    roc_arm_threshold: float  # Minimum ROC peak before enabling peak-DD exit

    # Volume filter
    vol_sma_len: int  # Volume SMA period
    vol_ratio_min: float  # Minimum volume ratio for entry

    # ATR_fast (profit protection)
    atr_fast_len: int  # ATR period on 1m data
    M_fast: float  # Multiplier for fast trailing stop
    fast_trail_activation_k: float  # Profit arm threshold (in ATR units)

    # ATR_slow (catastrophic stop)
    tf_slow: str  # Timeframe for slow ATR ("15min" or "1H")
    atr_slow_len: int  # ATR period on higher TF
    M_slow: float  # Multiplier for slow stop

    # ROC peak-drawdown exit
    X_dd: float  # Drawdown threshold from ROC peak (e.g., 0.40 = 40%)

    # Execution parameters
    min_bars_after_entry_for_exit: int = 3  # Prevent immediate churn
    eps: float = 1e-12  # Small constant to avoid division by zero

    # Position sizing
    order_size: Decimal = Decimal("100.00")  # USD per trade
    fee_rate: Decimal = Decimal("0.006")  # 0.6% (maker+taker combined)

    # Side (LONG only for now)
    side: str = "LONG"

    def __post_init__(self):
        """Validate configuration"""
        assert self.side == "LONG", "Only LONG side implemented currently"
        assert self.roc_len > 0, "roc_len must be positive"
        assert self.roc_ema_len >= 1, "roc_ema_len must be >= 1"
        assert self.X_dd > 0, "X_dd must be positive"
        assert self.M_fast > 0 and self.M_slow > 0, "ATR multipliers must be positive"
        assert self.tf_slow in ['15min', '1H', '60min'], f"Invalid tf_slow: {self.tf_slow}"


@dataclass
class PositionState:
    """
    State tracking for an open position.

    All tracking variables needed for arming, peak updates, and stop calculations.
    """
    symbol: str
    entry_price: Decimal
    entry_time: datetime
    size: Decimal  # Position size in USD

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

    # Entry indicators (for logging/analysis)
    entry_roc: float = 0.0
    entry_atr_slow_pct: float = 0.0
    entry_atr_fast_pct: float = 0.0
    entry_vol_ratio: float = 0.0

    def __post_init__(self):
        """Initialize high_since_entry if not set"""
        if self.high_since_entry == Decimal("0"):
            self.high_since_entry = self.entry_price


@dataclass
class TradeResult:
    """
    Record of a completed trade with all relevant metrics.
    """
    # Basic info
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal

    # Exit info
    exit_reason: str
    bars_held: int

    # P&L
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    pnl_pct: float

    # Entry indicators
    entry_roc: float
    entry_atr_slow_pct: float
    entry_atr_fast_pct: float
    entry_vol_ratio: float

    # Exit indicators
    exit_roc: float
    roc_peak: float
    dd_at_exit: float  # ROC drawdown from peak at exit

    # Stops at exit
    stop_slow_at_exit: Decimal
    stop_fast_at_exit: Decimal
    armed_roc_dd: bool
    armed_fast_trail: bool

    # MAE/MFE
    mae_pct: float  # Maximum Adverse Excursion (%)
    mfe_pct: float  # Maximum Favorable Excursion (%)


class PeakDrawdownStrategy:
    """
    ROC Peak-Drawdown Strategy Implementation.

    Maintains state for multiple symbols and generates entry/exit signals.
    """

    def __init__(self, config: PeakDrawdownConfig):
        self.config = config

        # Position tracking (symbol -> PositionState)
        self.positions: Dict[str, PositionState] = {}

        # Completed trades
        self.trades: List[TradeResult] = []

        # Statistics
        self.total_entries = 0
        self.total_exits = 0

    def check_entry_signal(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> bool:
        """
        Check if entry conditions are met.

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators (roc, vol_ratio, etc.)

        Returns:
            True if entry signal triggered
        """
        # Don't enter if already in position
        if symbol in self.positions:
            return False

        # Entry condition: ROC >= threshold AND vol_ratio >= min
        roc = indicators.get('roc', 0.0)
        vol_ratio = indicators.get('vol_ratio', 0.0)

        entry_signal = (
            roc >= self.config.roc_entry_threshold and
            vol_ratio >= self.config.vol_ratio_min
        )

        return entry_signal

    def enter_position(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """
        Open a new position.

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators
        """
        entry_price = Decimal(str(bar['close']))
        entry_time = bar['time'] if 'time' in bar.index else bar.name

        # Create position state
        position = PositionState(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=entry_time,
            size=self.config.order_size,
            high_since_entry=Decimal(str(bar['high'])),
            roc_peak=indicators['roc'],
            entry_roc=indicators['roc'],
            entry_atr_slow_pct=indicators['atr_slow_pct'],
            entry_atr_fast_pct=indicators['atr_fast_pct'],
            entry_vol_ratio=indicators.get('vol_ratio', 0.0)
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

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators
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

        # Arm ROC-dd exit once peak is meaningful
        if position.roc_peak >= self.config.roc_arm_threshold:
            position.armed_roc_dd = True

        # Arm fast trail ONLY when trade shows profit
        # FIX: Changed from (profit_arm OR momentum_arm) to (profit_arm ONLY)
        # This prevents immediate arming before capturing any move
        close_price = Decimal(str(bar['close']))
        atr_fast_pct = Decimal(str(indicators['atr_fast_pct']))

        profit_arm_threshold = position.entry_price * (
            Decimal("1") + Decimal(str(self.config.fast_trail_activation_k)) * atr_fast_pct
        )
        profit_arm = close_price >= profit_arm_threshold

        # ARM ONLY ON PROFIT (removed momentum_arm condition)
        if profit_arm:
            position.armed_fast_trail = True

        # Update stops
        # Slow stop: fixed at entry (based on entry ATR_slow)
        # (Already calculated at entry, no need to update)

        # Fast stop: trailing (if armed)
        if position.armed_fast_trail:
            atr_fast_pct_current = Decimal(str(indicators['atr_fast_pct']))
            candidate_fast = position.high_since_entry * (
                Decimal("1") - Decimal(str(self.config.M_fast)) * atr_fast_pct_current
            )

            # Ratchet up only (never lower)
            if candidate_fast > position.stop_fast:
                position.stop_fast = candidate_fast

    def check_exit_conditions(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ) -> Optional[str]:
        """
        Check if any exit condition is met.

        Priority order:
        1. ATR_slow stop hit (catastrophic)
        2. ATR_fast stop hit (profit protection, if armed)
        3. ROC peak-drawdown exit (if armed and min bars elapsed)

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators

        Returns:
            Exit reason string or None
        """
        position = self.positions[symbol]

        bar_low = Decimal(str(bar['low']))

        # 1. ATR_slow stop hit
        if bar_low <= position.stop_slow:
            return "ATR_SLOW_STOP"

        # 2. ATR_fast stop hit (if armed)
        if position.armed_fast_trail and bar_low <= position.stop_fast:
            return "ATR_FAST_STOP"

        # 3. ROC peak-drawdown exit
        if (
            position.armed_roc_dd and
            position.bars_in_trade >= self.config.min_bars_after_entry_for_exit
        ):
            current_roc = indicators['roc']

            # Calculate drawdown from peak
            dd = (position.roc_peak - current_roc) / max(abs(position.roc_peak), self.config.eps)

            if dd >= self.config.X_dd:
                return "ROC_PEAK_DD"

        return None

    def exit_position(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series,
        exit_reason: str
    ):
        """
        Close a position and record the trade.

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators
            exit_reason: Reason for exit
        """
        position = self.positions[symbol]

        # Determine exit price based on reason
        if exit_reason in ["ATR_SLOW_STOP", "ATR_FAST_STOP"]:
            # Fill at stop price (or could use bar open for conservative model)
            if exit_reason == "ATR_SLOW_STOP":
                exit_price = position.stop_slow
            else:
                exit_price = position.stop_fast
        else:
            # Fill at close for ROC exits
            exit_price = Decimal(str(bar['close']))

        exit_time = bar['time'] if 'time' in bar.index else bar.name

        # Calculate P&L
        # Quantity in base currency (BTC, ETH, etc.)
        qty = position.size / position.entry_price

        # Gross P&L (price difference * quantity)
        gross_pnl = (exit_price - position.entry_price) * qty

        # Fees calculated on notional value (qty * price)
        # Entry fee: notional_entry * fee_rate
        # Exit fee: notional_exit * fee_rate
        notional_entry = abs(qty * position.entry_price)  # Should equal position.size
        notional_exit = abs(qty * exit_price)
        fee_entry = notional_entry * self.config.fee_rate
        fee_exit = notional_exit * self.config.fee_rate
        fees = fee_entry + fee_exit

        net_pnl = gross_pnl - fees
        pnl_pct = float((exit_price - position.entry_price) / position.entry_price)

        # Calculate ROC drawdown at exit
        current_roc = indicators['roc']
        dd_at_exit = (position.roc_peak - current_roc) / max(abs(position.roc_peak), self.config.eps)

        # Calculate MAE/MFE (simplified - would need full bar history for accurate calculation)
        # For now, use high_since_entry for MFE and assume entry was worst for MAE
        mfe_pct = float((position.high_since_entry - position.entry_price) / position.entry_price)
        mae_pct = min(0.0, pnl_pct)  # Simplified: assume worst was exit or entry

        # Create trade record
        trade = TradeResult(
            symbol=symbol,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size=position.size,
            exit_reason=exit_reason,
            bars_held=position.bars_in_trade,
            gross_pnl=gross_pnl,
            fees=fees,
            net_pnl=net_pnl,
            pnl_pct=pnl_pct,
            entry_roc=position.entry_roc,
            entry_atr_slow_pct=position.entry_atr_slow_pct,
            entry_atr_fast_pct=position.entry_atr_fast_pct,
            entry_vol_ratio=position.entry_vol_ratio,
            exit_roc=current_roc,
            roc_peak=position.roc_peak,
            dd_at_exit=dd_at_exit,
            stop_slow_at_exit=position.stop_slow,
            stop_fast_at_exit=position.stop_fast,
            armed_roc_dd=position.armed_roc_dd,
            armed_fast_trail=position.armed_fast_trail,
            mae_pct=mae_pct,
            mfe_pct=mfe_pct
        )

        self.trades.append(trade)
        self.total_exits += 1

        # Remove position
        del self.positions[symbol]

    def process_bar(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series
    ):
        """
        Process a single bar for entry/exit signals.

        Args:
            symbol: Trading symbol
            bar: Current OHLCV bar
            indicators: Calculated indicators
        """
        # Check if in position
        if symbol in self.positions:
            # Update position state
            self.update_position_state(symbol, bar, indicators)

            # Check exit conditions
            exit_reason = self.check_exit_conditions(symbol, bar, indicators)
            if exit_reason:
                self.exit_position(symbol, bar, indicators, exit_reason)
        else:
            # Check entry signal
            if self.check_entry_signal(symbol, bar, indicators):
                self.enter_position(symbol, bar, indicators)

    def get_summary_stats(self) -> Dict:
        """
        Calculate summary statistics for all completed trades.

        Returns:
            Dictionary with performance metrics
        """
        if not self.trades:
            return {
                'total_trades': 0,
                'total_pnl': Decimal("0"),
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_win': Decimal("0"),
                'avg_loss': Decimal("0"),
                'avg_bars_held': 0.0
            }

        # Basic counts
        total_trades = len(self.trades)
        winners = [t for t in self.trades if t.net_pnl > 0]
        losers = [t for t in self.trades if t.net_pnl <= 0]

        # P&L stats
        total_pnl = sum(t.net_pnl for t in self.trades)
        win_rate = 100 * len(winners) / total_trades if total_trades > 0 else 0

        total_wins = sum(t.net_pnl for t in winners)
        total_losses = abs(sum(t.net_pnl for t in losers))
        profit_factor = float(total_wins / total_losses) if total_losses > 0 else float('inf')

        avg_win = total_wins / len(winners) if winners else Decimal("0")
        avg_loss = -total_losses / len(losers) if losers else Decimal("0")
        avg_bars_held = sum(t.bars_held for t in self.trades) / total_trades

        # Exit reason distribution
        exit_reasons = {}
        for trade in self.trades:
            exit_reasons[trade.exit_reason] = exit_reasons.get(trade.exit_reason, 0) + 1

        return {
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_bars_held': avg_bars_held,
            'exit_reasons': exit_reasons
        }


# ============================================================================
# Variant Configs (Spec Defaults)
# ============================================================================

def get_variant_a_config() -> PeakDrawdownConfig:
    """
    Variant A: 20m-style impulse capture.

    Shorter horizon, tighter stops, lower drawdown tolerance.
    """
    return PeakDrawdownConfig(
        variant_name="A_20M_STYLE",

        # ROC parameters
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.003,  # 0.30% (start mid-range of 0.002-0.004)
        roc_arm_threshold=0.0015,   # 0.15%

        # Volume filter
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # ATR_fast
        atr_fast_len=14,
        M_fast=1.8,
        fast_trail_activation_k=1.0,

        # ATR_slow
        tf_slow="15min",
        atr_slow_len=14,
        M_slow=3.0,

        # ROC peak-drawdown
        X_dd=0.40,  # 40% drawdown from peak

        # Execution
        min_bars_after_entry_for_exit=3,
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )


def get_variant_b_config() -> PeakDrawdownConfig:
    """
    Variant B: 24h-style trend participation.

    Longer horizon, wider stops, higher drawdown tolerance.
    """
    return PeakDrawdownConfig(
        variant_name="B_24H_STYLE",

        # ROC parameters
        roc_len=60,
        roc_ema_len=9,
        roc_entry_threshold=0.009,  # 0.90% (mid-range of 0.006-0.012)
        roc_arm_threshold=0.0030,   # 0.30%

        # Volume filter
        vol_sma_len=45,  # Mid-range of 30-60
        vol_ratio_min=2.0,  # Mid-range of 1.5-2.5

        # ATR_fast
        atr_fast_len=21,
        M_fast=2.2,
        fast_trail_activation_k=1.0,

        # ATR_slow
        tf_slow="1H",
        atr_slow_len=14,
        M_slow=3.5,

        # ROC peak-drawdown
        X_dd=0.60,  # 60% drawdown from peak

        # Execution
        min_bars_after_entry_for_exit=3,
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )


if __name__ == '__main__':
    # Quick test of config creation
    config_a = get_variant_a_config()
    config_b = get_variant_b_config()

    print("✅ Variant A config created:")
    print(f"   ROC length: {config_a.roc_len}, Entry threshold: {config_a.roc_entry_threshold:.3%}")
    print(f"   ATR_slow: {config_a.tf_slow}, M_slow: {config_a.M_slow}")
    print(f"   Peak drawdown: {config_a.X_dd:.1%}")
    print()
    print("✅ Variant B config created:")
    print(f"   ROC length: {config_b.roc_len}, Entry threshold: {config_b.roc_entry_threshold:.3%}")
    print(f"   ATR_slow: {config_b.tf_slow}, M_slow: {config_b.M_slow}")
    print(f"   Peak drawdown: {config_b.X_dd:.1%}")
