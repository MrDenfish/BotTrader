"""
Order Strategy Selector - Hybrid Order Management System

Determines whether to use:
- Bracket orders (guaranteed execution, higher fees on SL: 0.85% total)
- Limit-only monitoring (lower fees: 0.60% total, requires monitoring)

Based on market conditions:
- Volatility (bid-ask spread)
- Position size
- Market connectivity status
"""

import os
from decimal import Decimal
from typing import Literal, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrderStrategyDecision:
    """Result of strategy selection analysis"""
    strategy: Literal["bracket", "limit_only"]
    reason: str
    conditions: Dict[str, Any]
    fee_estimate_pct: Decimal

    def __str__(self):
        return f"{self.strategy.upper()} ({self.reason}) - Est. fees: {self.fee_estimate_pct:.3%}"


class OrderStrategySelector:
    """
    Analyzes market conditions and determines optimal order strategy.

    Configuration (from .env):
    - USE_LIMIT_ONLY_EXITS: Enable limit-only monitoring (default: true)
    - BRACKET_VOLATILITY_THRESHOLD: Spread threshold for bracket (default: 0.01 = 1%)
    - BRACKET_POSITION_SIZE_MIN: Min position size for bracket (default: $1000)
    - MAKER_FEE: Maker fee rate (default: 0.003 = 0.3%)
    - TAKER_FEE: Taker fee rate (default: 0.0055 = 0.55%)
    """

    def __init__(self):
        # Load configuration from environment
        self.enabled = os.getenv("USE_LIMIT_ONLY_EXITS", "true").lower() == "true"
        self.volatility_threshold = Decimal(os.getenv("BRACKET_VOLATILITY_THRESHOLD", "0.01"))
        self.position_size_min = Decimal(os.getenv("BRACKET_POSITION_SIZE_MIN", "1000"))
        self.maker_fee = Decimal(os.getenv("MAKER_FEE", "0.003"))
        self.taker_fee = Decimal(os.getenv("TAKER_FEE", "0.0055"))

        # Fee calculations
        self.bracket_fee_total = self.maker_fee + self.taker_fee  # Entry (maker) + SL (taker)
        self.limit_only_fee_total = self.maker_fee * 2  # Entry (maker) + Exit (maker)

        logger.info(
            f"OrderStrategySelector initialized: "
            f"enabled={self.enabled}, "
            f"volatility_threshold={self.volatility_threshold:.2%}, "
            f"position_size_min=${self.position_size_min}, "
            f"bracket_fees={self.bracket_fee_total:.3%}, "
            f"limit_only_fees={self.limit_only_fee_total:.3%}"
        )

    def select_strategy(
        self,
        symbol: str,
        entry_price: Decimal,
        position_size_base: Decimal,
        bid: Decimal = None,
        ask: Decimal = None,
        spread_pct: Decimal = None,
        websocket_connected: bool = True,
        force_bracket: bool = False
    ) -> OrderStrategyDecision:
        """
        Determine optimal order strategy based on market conditions.

        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            entry_price: Entry price for position
            position_size_base: Position size in base currency
            bid: Current bid price (optional)
            ask: Current ask price (optional)
            spread_pct: Bid-ask spread as percentage (optional, calculated if bid/ask provided)
            websocket_connected: Whether websocket is connected for monitoring
            force_bracket: Force bracket order regardless of conditions

        Returns:
            OrderStrategyDecision with strategy selection and reasoning
        """

        # Calculate position notional value
        position_notional = entry_price * position_size_base

        # Calculate spread if not provided
        if spread_pct is None and bid and ask and bid > 0:
            spread_pct = (ask - bid) / bid
        elif spread_pct is None:
            spread_pct = Decimal("0")

        conditions = {
            "symbol": symbol,
            "entry_price": float(entry_price),
            "position_notional": float(position_notional),
            "spread_pct": float(spread_pct),
            "websocket_connected": websocket_connected,
            "limit_only_enabled": self.enabled
        }

        # Decision tree for strategy selection

        # 1. Force bracket if explicitly requested
        if force_bracket:
            return OrderStrategyDecision(
                strategy="bracket",
                reason="Force bracket requested",
                conditions=conditions,
                fee_estimate_pct=self.bracket_fee_total
            )

        # 2. Use bracket if limit-only is disabled
        if not self.enabled:
            return OrderStrategyDecision(
                strategy="bracket",
                reason="Limit-only monitoring disabled in config",
                conditions=conditions,
                fee_estimate_pct=self.bracket_fee_total
            )

        # 3. Use bracket if websocket disconnected (can't monitor)
        if not websocket_connected:
            return OrderStrategyDecision(
                strategy="bracket",
                reason="Websocket disconnected - cannot monitor limit orders",
                conditions=conditions,
                fee_estimate_pct=self.bracket_fee_total
            )

        # 4. Use bracket if high volatility (wide spread)
        if spread_pct >= self.volatility_threshold:
            return OrderStrategyDecision(
                strategy="bracket",
                reason=f"High volatility: spread {spread_pct:.2%} >= {self.volatility_threshold:.2%}",
                conditions=conditions,
                fee_estimate_pct=self.bracket_fee_total
            )

        # 5. Use bracket if large position size
        if position_notional >= self.position_size_min:
            return OrderStrategyDecision(
                strategy="bracket",
                reason=f"Large position: ${position_notional:.2f} >= ${self.position_size_min}",
                conditions=conditions,
                fee_estimate_pct=self.bracket_fee_total
            )

        # 6. Default to limit-only (optimal conditions)
        fee_savings = self.bracket_fee_total - self.limit_only_fee_total
        return OrderStrategyDecision(
            strategy="limit_only",
            reason=f"Normal conditions - save {fee_savings:.3%} fees",
            conditions=conditions,
            fee_estimate_pct=self.limit_only_fee_total
        )

    def estimate_fee_savings(self, position_notional: Decimal) -> Dict[str, Decimal]:
        """
        Estimate potential fee savings for limit-only vs bracket strategy.

        Args:
            position_notional: Position size in USD

        Returns:
            Dict with fee comparison details
        """
        bracket_fee_usd = position_notional * self.bracket_fee_total
        limit_only_fee_usd = position_notional * self.limit_only_fee_total
        savings_usd = bracket_fee_usd - limit_only_fee_usd
        savings_pct = (self.bracket_fee_total - self.limit_only_fee_total)

        return {
            "position_notional": position_notional,
            "bracket_fee_pct": self.bracket_fee_total,
            "bracket_fee_usd": bracket_fee_usd,
            "limit_only_fee_pct": self.limit_only_fee_total,
            "limit_only_fee_usd": limit_only_fee_usd,
            "savings_pct": savings_pct,
            "savings_usd": savings_usd,
            "savings_per_trade": savings_usd
        }


# Singleton instance for easy import
_selector_instance = None

def get_strategy_selector() -> OrderStrategySelector:
    """Get or create singleton instance of OrderStrategySelector"""
    global _selector_instance
    if _selector_instance is None:
        _selector_instance = OrderStrategySelector()
    return _selector_instance
