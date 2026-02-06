"""
Post-only fill simulation for backtesting.

Conservative fill rules:
- Buy limit fills only if low <= limit_price
- Sell limit fills only if high >= limit_price
- Post-only must not cross book at placement
- Stops use taker fills (pessimistic)
"""


def can_fill_buy_limit(bar_low: float, limit_price: float) -> bool:
    """Conservative buy limit fill: low must touch limit price."""
    return bar_low <= limit_price


def can_fill_sell_limit(bar_high: float, limit_price: float) -> bool:
    """Conservative sell limit fill: high must touch limit price."""
    return bar_high >= limit_price


def is_post_only_valid(current_price: float, limit_price: float, side: str) -> bool:
    """
    Check if post-only order wouldn't cross the book.

    For buys: limit must be below current price.
    For sells: limit must be above current price.
    """
    if side == "BUY":
        return limit_price < current_price
    elif side == "SELL":
        return limit_price > current_price
    return False


def calculate_fee(notional: float, fee_rate: float) -> float:
    """Calculate fee from notional amount and rate."""
    return abs(notional) * fee_rate
