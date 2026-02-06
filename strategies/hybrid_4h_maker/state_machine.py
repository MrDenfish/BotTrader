"""
State machine dispatch for the 4h Hybrid Maker Strategy.

Extracted from Hybrid4hStrategy: the 5 state handlers that form
the core state machine:
  FLAT -> SETUP_ACTIVE -> RETEST_ORDER_WORKING -> CHASE_ORDER_WORKING -> IN_POSITION

Each handler returns an optional action dict for logging.
"""

from typing import Optional
from datetime import timedelta

from strategies.hybrid_4h_maker import entry_logic, exit_logic, filters


def handle_flat_state(strategy, symbol: str, bar, indicators_4h: dict,
                      indicators_1d: dict, is_4h_close: bool) -> Optional[dict]:
    """Check for new setup on 4h close."""
    from backtest.strategy_4h_hybrid import Setup, StrategyState

    if not is_4h_close:
        return None

    config = strategy.config

    # Check base filters
    regime_ok = filters.check_regime_filter(bar, indicators_1d, config)
    viability_ok, _ = filters.check_viability_filter(
        indicators_4h, config,
        bb_width_pct_history=strategy.bb_width_history.get(symbol, []),
    )

    base_ok = regime_ok and viability_ok
    if not base_ok:
        return None

    # Check setup trigger
    if config.setup_mode == "donchian":
        triggered, breakout_level = filters.check_donchian_setup(
            bar, indicators_4h, strategy.bb_width_history.get(symbol, []), config
        )
    elif config.setup_mode == "roc_score":
        triggered, breakout_level = filters.check_roc_score_setup(
            bar, indicators_4h, config,
            bb_width_pct_history=strategy.bb_width_history.get(symbol, [])
        )
    else:
        triggered, breakout_level = False, 0.0

    if triggered:
        strategy.gate_audit['structure_ok'] += 1
        strategy.gate_audit['setup_trigger_ok'] += 1
    else:
        return None

    # Create setup
    strategy.signal_count += 1
    strategy.gate_audit['setup_created'] += 1

    setup = Setup(
        symbol=symbol,
        setup_time=bar.timestamp,
        setup_type=config.setup_mode,
        breakout_level=breakout_level,
        ttl_bars_4h=config.setup_ttl_bars_4h,
        bb_width_pct_at_setup=indicators_4h.get('bb_width_pct', 0),
        setup_created_ts=bar.timestamp,
        retest_deadline_ts=bar.timestamp + timedelta(minutes=config.retest_ttl_minutes),
        chase_deadline_ts=bar.timestamp + timedelta(
            minutes=config.retest_ttl_minutes + config.chase_ttl_minutes
        ),
        setup_deadline_ts=bar.timestamp + timedelta(hours=config.setup_ttl_bars_4h * 4)
    )

    strategy.pending_setups[symbol] = setup
    strategy.setup_start_time[symbol] = bar.timestamp

    # Attempt immediate retest bid placement
    can_place, retest_price = entry_logic.can_place_immediate_retest_bid(
        bar, breakout_level, config
    )

    if can_place:
        qty = config.notional_usd / retest_price

        setup.entry_order_active = True
        setup.entry_order_type = "RETEST"
        setup.entry_price = retest_price
        setup.entry_created_ts = bar.timestamp
        setup.entry_last_update_ts = bar.timestamp
        setup.entry_price_history.append(retest_price)

        strategy.entry_submitted_count += 1
        strategy.state[symbol] = StrategyState.RETEST_ORDER_WORKING

        return {
            "action": "SETUP_TRIGGERED_WITH_RETEST_BID",
            "setup_type": config.setup_mode,
            "breakout_level": breakout_level,
            "retest_price": retest_price,
            "qty": qty
        }
    else:
        strategy.state[symbol] = StrategyState.SETUP_ACTIVE
        return {
            "action": "SETUP_TRIGGERED_WAITING",
            "setup_type": config.setup_mode,
            "breakout_level": breakout_level,
            "reason": "retest_bid_not_placeable"
        }


def handle_setup_active_state(strategy, symbol: str, bar,
                              indicators_4h: dict) -> Optional[dict]:
    """Setup exists but retest bid not yet placeable."""
    from backtest.strategy_4h_hybrid import StrategyState

    config = strategy.config
    setup = strategy.pending_setups[symbol]

    setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar.low)
    setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar.high)

    # Check if setup expired
    if bar.timestamp >= setup.setup_deadline_ts:
        _record_expired_setup(strategy, symbol, setup, "setup_ttl_expired_before_placeable")
        strategy.expired_setup_count += 1
        del strategy.pending_setups[symbol]
        strategy.state[symbol] = StrategyState.FLAT
        return {"action": "SETUP_EXPIRED_BEFORE_PLACEABLE"}

    # Try to place retest bid
    can_place, retest_price = entry_logic.can_place_immediate_retest_bid(
        bar, setup.breakout_level, config
    )

    if can_place:
        qty = config.notional_usd / retest_price

        setup.entry_order_active = True
        setup.entry_order_type = "RETEST"
        setup.entry_price = retest_price
        setup.entry_created_ts = bar.timestamp
        setup.entry_last_update_ts = bar.timestamp
        setup.entry_price_history.append(retest_price)

        strategy.state[symbol] = StrategyState.RETEST_ORDER_WORKING
        return {
            "action": "RETEST_BID_NOW_PLACEABLE",
            "retest_price": retest_price,
            "qty": qty
        }

    return None


def handle_retest_order_working_state(strategy, symbol: str, bar,
                                       indicators_4h: dict) -> Optional[dict]:
    """Retest bid is resting, waiting for fill or escalation."""
    from backtest.strategy_4h_hybrid import StrategyState

    config = strategy.config
    setup = strategy.pending_setups[symbol]

    setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar.low)
    setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar.high)

    # Check if filled
    if bar.low <= setup.entry_price:
        strategy.entry_count += 1
        strategy.gate_audit['entry_filled'] += 1
        strategy.retest_fill_count += 1

        entry_wait_minutes = (bar.timestamp - setup.setup_created_ts).total_seconds() / 60

        notional_entry = abs((config.notional_usd / setup.entry_price) * setup.entry_price)
        entry_fee = notional_entry * config.maker_fee

        qty = config.notional_usd / setup.entry_price

        position = exit_logic.create_position(
            symbol, bar.timestamp, setup.entry_price, qty, entry_fee, indicators_4h,
            config, strategy.bb_width_history.get(symbol, []),
            entry_type="RETEST", entry_wait_minutes=entry_wait_minutes
        )

        strategy.positions[symbol] = position
        strategy.state[symbol] = StrategyState.IN_POSITION

        del strategy.pending_setups[symbol]

        return {
            "action": "RETEST_FILLED",
            "price": setup.entry_price,
            "qty": qty,
            "fee": entry_fee,
            "wait_minutes": entry_wait_minutes,
            "stop": position.stop_price,
            "tp1": position.tp1_price,
            "tp2": position.tp2_price
        }

    # Check if retest deadline reached
    if bar.timestamp >= setup.retest_deadline_ts:
        if config.enable_chase_entry:
            strategy.chase_attempt_count += 1

            atr_pct_4h = indicators_4h.get('atr_pct', 0.01)
            chase_offset = entry_logic.calculate_chase_offset(atr_pct_4h, config)
            chase_price = bar.close * (1 - chase_offset)

            if entry_logic.can_place_post_only_buy(bar, chase_price):
                setup.entry_order_type = "CHASE"
                setup.entry_price = chase_price
                setup.entry_last_update_ts = bar.timestamp
                setup.entry_reprices = 0
                setup.entry_price_history.append(chase_price)

                strategy.entry_submitted_count += 1
                strategy.state[symbol] = StrategyState.CHASE_ORDER_WORKING

                return {
                    "action": "ESCALATED_TO_CHASE",
                    "chase_price": chase_price,
                    "chase_offset": chase_offset
                }
            else:
                _record_expired_setup(strategy, symbol, setup,
                                     "chase_not_placeable_after_retest_ttl")
                strategy.expired_setup_count += 1
                del strategy.pending_setups[symbol]
                strategy.state[symbol] = StrategyState.FLAT
                return {"action": "CHASE_NOT_PLACEABLE_EXPIRED"}
        else:
            _record_expired_setup(strategy, symbol, setup,
                                 "retest_ttl_expired_chase_disabled")
            strategy.expired_setup_count += 1
            del strategy.pending_setups[symbol]
            strategy.state[symbol] = StrategyState.FLAT
            return {"action": "RETEST_TTL_EXPIRED"}

    # Check if setup expired (hard stop)
    if bar.timestamp >= setup.setup_deadline_ts:
        _record_expired_setup(strategy, symbol, setup,
                             "setup_ttl_expired_during_retest")
        strategy.expired_setup_count += 1
        del strategy.pending_setups[symbol]
        strategy.state[symbol] = StrategyState.FLAT
        return {"action": "SETUP_EXPIRED_DURING_RETEST"}

    return None


def handle_chase_order_working_state(strategy, symbol: str, bar,
                                      indicators_4h: dict) -> Optional[dict]:
    """Chase bid is resting, may be repriced."""
    from backtest.strategy_4h_hybrid import StrategyState

    config = strategy.config
    setup = strategy.pending_setups[symbol]

    setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar.low)
    setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar.high)

    # Check if filled
    if bar.low <= setup.entry_price:
        strategy.entry_count += 1
        strategy.gate_audit['entry_filled'] += 1
        strategy.chase_fill_count += 1

        entry_wait_minutes = (bar.timestamp - setup.setup_created_ts).total_seconds() / 60

        notional_entry = abs((config.notional_usd / setup.entry_price) * setup.entry_price)
        entry_fee = notional_entry * config.maker_fee

        qty = config.notional_usd / setup.entry_price

        position = exit_logic.create_position(
            symbol, bar.timestamp, setup.entry_price, qty, entry_fee, indicators_4h,
            config, strategy.bb_width_history.get(symbol, []),
            entry_type="CHASE", entry_wait_minutes=entry_wait_minutes
        )

        strategy.positions[symbol] = position
        strategy.state[symbol] = StrategyState.IN_POSITION

        del strategy.pending_setups[symbol]

        return {
            "action": "CHASE_FILLED",
            "price": setup.entry_price,
            "qty": qty,
            "fee": entry_fee,
            "wait_minutes": entry_wait_minutes,
            "reprices": setup.entry_reprices
        }

    # Check chase max-extension guard
    extension = (bar.close / setup.breakout_level) - 1.0
    if extension > config.chase_max_extension:
        _record_expired_setup(strategy, symbol, setup, "chase_max_extension_exceeded")
        strategy.expired_setup_count += 1
        del strategy.pending_setups[symbol]
        strategy.state[symbol] = StrategyState.FLAT
        return {"action": "CHASE_MAX_EXTENSION_EXCEEDED", "extension": extension}

    # Check if should reprice
    time_since_last_update = (bar.timestamp - setup.entry_last_update_ts).total_seconds() / 60
    can_reprice = (
        time_since_last_update >= config.chase_reprice_interval_minutes
        and setup.entry_reprices < config.chase_max_reprices
    )

    if can_reprice:
        atr_pct_4h = indicators_4h.get('atr_pct', 0.01)
        chase_offset = entry_logic.calculate_chase_offset(atr_pct_4h, config)
        new_chase_price = bar.close * (1 - chase_offset)

        old_price = setup.entry_price
        max_increase = old_price * (1 + config.max_step_up_per_reprice)
        new_price_capped = min(new_chase_price, max_increase)

        maker_safe_price = bar.close * (1 - config.maker_safety_offset)
        final_price = min(new_price_capped, maker_safe_price)

        if final_price > old_price:
            setup.entry_price = final_price
            setup.entry_reprices += 1
            setup.entry_last_update_ts = bar.timestamp
            setup.entry_price_history.append(final_price)

            return {
                "action": "CHASE_REPRICED",
                "old_price": old_price,
                "new_price": final_price,
                "reprice_num": setup.entry_reprices
            }

    # Check if chase expired
    if bar.timestamp >= setup.chase_deadline_ts:
        _record_expired_setup(strategy, symbol, setup, "chase_ttl_expired")
        strategy.expired_setup_count += 1
        del strategy.pending_setups[symbol]
        strategy.state[symbol] = StrategyState.FLAT
        return {"action": "CHASE_TTL_EXPIRED"}

    # Check if setup expired (hard stop)
    if bar.timestamp >= setup.setup_deadline_ts:
        _record_expired_setup(strategy, symbol, setup, "setup_ttl_expired_during_chase")
        strategy.expired_setup_count += 1
        del strategy.pending_setups[symbol]
        strategy.state[symbol] = StrategyState.FLAT
        return {"action": "SETUP_EXPIRED_DURING_CHASE"}

    return None


def handle_in_position_state(strategy, symbol: str, bar,
                              indicators_4h: dict, is_4h_close: bool) -> Optional[dict]:
    """Manage position: stops, TPs, runner trail."""
    config = strategy.config
    position = strategy.positions[symbol]
    position.bars_held += 1

    exit_logic.update_mfe_mae(position, bar)

    # 1) Check stop (highest priority, taker exit)
    if bar.low <= position.stop_price:
        return exit_logic.exit_position_stop(
            symbol, bar, position, config,
            strategy.trades, strategy.positions, strategy.state
        )

    # 2) Check TP1
    if not position.tp1_filled and bar.high >= position.tp1_price:
        return exit_logic.fill_tp1(symbol, bar, position, config)

    # 3) Check TP2
    if not position.tp2_filled and bar.high >= position.tp2_price:
        return exit_logic.fill_tp2(
            symbol, bar, position, config,
            strategy.trades, strategy.positions, strategy.state
        )

    # 4) Runner trail (only after TP1)
    if position.tp1_filled:
        if is_4h_close:
            exit_logic.update_runner_trail(position, bar, indicators_4h)

        if position.trail_price is not None and bar.low <= position.trail_price:
            return exit_logic.exit_runner_trail(
                symbol, bar, position, config,
                strategy.trades, strategy.positions, strategy.state
            )

    return None


def _record_expired_setup(strategy, symbol: str, setup, reason: str) -> None:
    """Record diagnostics for expired setup."""
    from backtest.strategy_4h_hybrid import ExpiredSetup

    expired = ExpiredSetup(
        symbol=symbol,
        setup_time=setup.setup_time,
        setup_type=setup.setup_type,
        breakout_level=setup.breakout_level,
        min_low_during_ttl=setup.min_low_during_ttl,
        max_high_during_ttl=setup.max_high_during_ttl,
        expiration_reason=reason,
        bb_width_pct_at_setup=setup.bb_width_pct_at_setup
    )

    if setup.entry_order_active:
        expired.retest_limit_price = setup.entry_price if setup.entry_order_type == "RETEST" else None
        expired.chase_limit_price = setup.entry_price if setup.entry_order_type == "CHASE" else None

        if setup.entry_price and setup.min_low_during_ttl != float('inf'):
            gap = setup.entry_price - setup.min_low_during_ttl
            gap_pct = (gap / setup.breakout_level) * 100

            if setup.entry_order_type == "RETEST":
                expired.retest_gap_pct = gap_pct
            elif setup.entry_order_type == "CHASE":
                expired.chase_gap_pct = gap_pct

    strategy.expired_setups.append(expired)

    # Track setup duration
    if symbol in strategy.setup_start_time:
        duration_4h_bars = (bar_ts_from_setup(setup) - strategy.setup_start_time[symbol]).total_seconds() / (60 * 240)
        strategy.setup_active_durations.append(duration_4h_bars)
        del strategy.setup_start_time[symbol]


def bar_ts_from_setup(setup):
    """Get the relevant timestamp from setup for duration tracking."""
    # When recording expired, we use setup_deadline_ts as approximation
    # (the actual bar.name isn't passed to _record_expired_setup in the original)
    return setup.setup_deadline_ts if setup.setup_deadline_ts else setup.setup_time
