"""
Entry logic for the 4h Hybrid Maker Strategy.

Extracted from Hybrid4hStrategy: breakout retest, chase entry,
post-only placement, and immediate retest bid.
"""

from typing import Optional
from datetime import timedelta


def check_breakout_retest(bar, setup, config) -> tuple[bool, float]:
    """Check if price retested breakout level and place entry."""
    level = setup.breakout_level
    retest_upper = level * (1 + config.retest_band)

    if bar.low <= retest_upper and bar.close > level:
        entry_price = level * (1 - config.entry_offset)
        return True, entry_price

    return False, 0.0


def can_place_post_only_buy(bar, limit_price: float) -> bool:
    """Check if post-only buy can be placed (must not cross book)."""
    return limit_price < bar.close


def can_place_immediate_retest_bid(
    bar, breakout_level: float, config
) -> tuple[bool, Optional[float]]:
    """
    Check if immediate retest bid is placeable (post-only).

    Returns:
        (placeable, bid_price)
    """
    retest_price = breakout_level * (1 - config.retest_offset)

    if retest_price >= bar.close:
        return (False, None)

    return (True, retest_price)


def calculate_chase_offset(atr_pct_4h: float, config) -> float:
    """
    Calculate volatility-aware chase offset.
    """
    if not config.use_dynamic_chase_offset:
        return config.chase_offset

    if atr_pct_4h <= 0:
        return config.chase_offset_min

    dynamic_offset = config.chase_atr_mult * atr_pct_4h
    return max(config.chase_offset_min, dynamic_offset)


def place_chase_order(
    symbol: str,
    bar,
    setup,
    entry_order,
    indicators_4h: dict,
    config,
    chase_attempt_count_ref: list,
    expired_setup_count_ref: list,
) -> None:
    """
    Place chase entry order (fallback after retest TTL).

    Still post-only for maker fees.

    Hardening:
    - Max-extension guard: Don't chase if price has run too far
    - Dynamic ATR-scaled offset: Adapt to volatility
    - Expansion confirmation: Only chase if BB expanding
    """
    if not config.enable_chase_entry:
        return

    if setup.chase_attempted:
        return

    # Max-extension guard
    extension = (bar.close / setup.breakout_level) - 1.0
    if extension > config.chase_max_extension:
        expired_setup_count_ref[0] += 1
        return

    # Expansion confirmation
    if config.require_expansion_for_chase:
        lookback = config.expansion_lookback
        if len(setup.bb_width_history) >= lookback + 1:
            current_width = setup.bb_width_history[-1]
            previous_widths = setup.bb_width_history[-(lookback + 1):-1]

            is_expanding = current_width > max(previous_widths) if previous_widths else False

            if not is_expanding:
                expired_setup_count_ref[0] += 1
                return

    # Dynamic chase offset (ATR-based)
    if config.use_dynamic_chase_offset:
        atr_pct = indicators_4h.get('atr_pct', 0)
        if atr_pct > 0:
            chase_offset = config.chase_atr_mult * atr_pct
        else:
            chase_offset = config.chase_offset
    else:
        chase_offset = config.chase_offset

    chase_price = bar.close * (1.0 - chase_offset)

    setup.chase_attempted = True
    setup.chase_order_placed_ts = bar.timestamp
    setup.chase_deadline_ts = bar.timestamp + timedelta(minutes=config.chase_ttl_minutes)

    entry_order.chase_price = chase_price

    chase_attempt_count_ref[0] += 1
