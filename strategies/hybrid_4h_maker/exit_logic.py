"""
Exit logic for the 4h Hybrid Maker Strategy.

Extracted from Hybrid4hStrategy: position creation, stop exits,
TP fills, runner trail management, and position finalization.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from strategies.hybrid_4h_maker.filters import check_recent_compression


def create_position(
    symbol: str,
    entry_time: datetime,
    entry_price: float,
    qty: float,
    entry_fee: float,
    indicators_4h: dict,
    config,
    bb_width_history: list,
    entry_type: str = "RETEST",
    entry_wait_minutes: float = 0.0,
) -> 'Position':
    """
    Initialize position with stops and targets.

    Calculates stop price, TP1/TP2 prices, scale-out quantities,
    and captures compression context for adaptive runner policy.
    """
    # Import here to avoid circular imports
    from backtest.strategy_4h_hybrid import Position  # noqa: E402

    atr_pct = indicators_4h['atr_pct']

    # Stop
    stop_price = entry_price * (1 - config.stop_mult * atr_pct)

    # Profit targets (fee-multiple vs ATR-multiple, whichever is larger)
    fee_rt = config.round_trip_maker_fee

    tp1_return = max(
        config.tp1_fee_mult * fee_rt,
        config.tp1_atr_mult * atr_pct
    )
    tp2_return = max(
        config.tp2_fee_mult * fee_rt,
        config.tp2_atr_mult * atr_pct
    )

    tp1_price = entry_price * (1 + tp1_return)
    tp2_price = entry_price * (1 + tp2_return)

    # Compression context at entry time
    entry_compressed_recent_12 = False

    if len(bb_width_history) >= 12:
        entry_compressed_recent_12 = check_recent_compression(
            bb_width_pct_history=bb_width_history,
            threshold=config.bb_width_pct_threshold,
            lookback=12
        )

    # Apply compression-aware runner/trail parameters (if enabled)
    if config.use_compression_runner_policy and entry_compressed_recent_12:
        runner_qty_frac = config.runner_qty_frac_compressed
        trail_mult = config.trail_mult_compressed
    else:
        runner_qty_frac = config.runner_qty_frac_normal
        trail_mult = config.trail_mult_normal

    # Scale-out quantities
    tp1_qty = qty * config.tp1_qty_frac
    tp2_qty = qty * config.tp2_qty_frac
    runner_qty = qty * runner_qty_frac

    position = Position(
        symbol=symbol,
        entry_time=entry_time,
        entry_price=entry_price,
        qty=qty,
        stop_price=stop_price,
        tp1_price=tp1_price,
        tp1_qty=tp1_qty,
        tp2_price=tp2_price,
        tp2_qty=tp2_qty,
        runner_qty=runner_qty,
        entry_fee=entry_fee,
        highest_close_4h_since_entry=entry_price,
        trail_mult=trail_mult
    )

    position.entry_type_diagnostic = entry_type
    position.entry_wait_minutes_diagnostic = entry_wait_minutes
    position.entry_compressed_recent_12 = entry_compressed_recent_12

    return position


def update_mfe_mae(position, bar) -> None:
    """Update max favorable/adverse excursion."""
    entry = position.entry_price
    mfe = (bar.high - entry) / entry
    mae = (bar.low - entry) / entry

    position.mfe_pct = max(position.mfe_pct, mfe)
    position.mae_pct = min(position.mae_pct, mae)


def exit_position_stop(
    symbol: str,
    bar,
    position,
    config,
    trades: list,
    positions: dict,
    state: dict,
) -> dict:
    """Exit entire position at stop (taker)."""
    from backtest.strategy_4h_hybrid import Trade, StrategyState

    exit_price = position.stop_price
    qty = position.qty

    notional_exit = abs(qty * exit_price)
    exit_fee = notional_exit * config.taker_fee

    gross_pnl = (exit_price - position.entry_price) * qty
    fees_total = position.entry_fee + position.exit_fees + exit_fee
    net_pnl = gross_pnl - fees_total

    trade = Trade(
        symbol=symbol,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=bar.timestamp,
        stop_hit=True,
        qty_total=qty,
        gross_pnl=gross_pnl,
        fees_total=fees_total,
        net_pnl=net_pnl,
        mfe_pct=position.mfe_pct,
        mae_pct=position.mae_pct,
        bars_held=position.bars_held,
        setup_type=config.setup_mode,
        entry_mode=config.entry_mode,
        entry_type=position.entry_type_diagnostic,
        entry_wait_minutes=position.entry_wait_minutes_diagnostic,
        entry_compressed_recent_12=position.entry_compressed_recent_12
    )

    trades.append(trade)

    del positions[symbol]
    state[symbol] = StrategyState.FLAT

    return {
        "action": "STOP_HIT",
        "price": exit_price,
        "qty": qty,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl
    }


def fill_tp1(symbol: str, bar, position, config) -> dict:
    """Fill TP1 (post-only maker)."""
    exit_price = position.tp1_price
    qty = position.tp1_qty

    notional_exit = abs(qty * exit_price)
    exit_fee = notional_exit * config.maker_fee

    pnl_tp1 = (exit_price - position.entry_price) * qty

    position.tp1_filled = True
    position.tp1_fill_time = bar.timestamp
    position.exit_fees += exit_fee
    position.gross_pnl += pnl_tp1

    return {
        "action": "TP1_FILLED",
        "price": exit_price,
        "qty": qty,
        "pnl": pnl_tp1
    }


def fill_tp2(
    symbol: str,
    bar,
    position,
    config,
    trades: list,
    positions: dict,
    state: dict,
) -> dict:
    """Fill TP2 (post-only maker)."""
    exit_price = position.tp2_price
    qty = position.tp2_qty

    notional_exit = abs(qty * exit_price)
    exit_fee = notional_exit * config.maker_fee

    pnl_tp2 = (exit_price - position.entry_price) * qty

    position.tp2_filled = True
    position.tp2_fill_time = bar.timestamp
    position.exit_fees += exit_fee
    position.gross_pnl += pnl_tp2

    if position.runner_qty == 0:
        return finalize_position(symbol, bar, "TP2_FILLED_NO_RUNNER",
                                 config, trades, positions, state)

    return {
        "action": "TP2_FILLED",
        "price": exit_price,
        "qty": qty,
        "pnl": pnl_tp2
    }


def update_runner_trail(position, bar, indicators_4h: dict) -> None:
    """Update trailing stop for runner on 4h closes."""
    close = bar.close
    atr_pct = indicators_4h['atr_pct']

    if close > position.highest_close_4h_since_entry:
        position.highest_close_4h_since_entry = close

    trail_price = position.highest_close_4h_since_entry * (1 - position.trail_mult * atr_pct)

    if position.trail_price is None or trail_price > position.trail_price:
        position.trail_price = trail_price


def exit_runner_trail(
    symbol: str,
    bar,
    position,
    config,
    trades: list,
    positions: dict,
    state: dict,
) -> dict:
    """Exit runner at trailing stop (taker)."""
    exit_price = position.trail_price
    qty = position.runner_qty

    notional_exit = abs(qty * exit_price)
    exit_fee = notional_exit * config.taker_fee

    pnl_runner = (exit_price - position.entry_price) * qty

    position.exit_fees += exit_fee
    position.gross_pnl += pnl_runner

    return finalize_position(symbol, bar, "TRAIL_HIT",
                             config, trades, positions, state)


def finalize_position(
    symbol: str,
    bar,
    exit_reason: str,
    config,
    trades: list,
    positions: dict,
    state: dict,
) -> dict:
    """Finalize and record completed trade."""
    from backtest.strategy_4h_hybrid import Trade, StrategyState

    position = positions[symbol]

    fees_total = position.entry_fee + position.exit_fees
    net_pnl = position.gross_pnl - fees_total

    trade = Trade(
        symbol=symbol,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=bar.timestamp,
        tp1_filled=position.tp1_filled,
        tp2_filled=position.tp2_filled,
        runner_exit_reason=exit_reason,
        qty_total=position.qty,
        gross_pnl=position.gross_pnl,
        fees_total=fees_total,
        net_pnl=net_pnl,
        mfe_pct=position.mfe_pct,
        mae_pct=position.mae_pct,
        bars_held=position.bars_held,
        setup_type=config.setup_mode,
        entry_mode=config.entry_mode,
        entry_type=position.entry_type_diagnostic,
        entry_wait_minutes=position.entry_wait_minutes_diagnostic,
        entry_compressed_recent_12=position.entry_compressed_recent_12
    )

    trades.append(trade)

    del positions[symbol]
    state[symbol] = StrategyState.FLAT

    return {
        "action": "POSITION_CLOSED",
        "reason": exit_reason,
        "gross_pnl": position.gross_pnl,
        "net_pnl": net_pnl,
        "fees": fees_total
    }
