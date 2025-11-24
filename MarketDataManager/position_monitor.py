"""
Position Monitor - Smart LIMIT Exit Strategy

Monitors open positions and places LIMIT sell orders based on:
1. P&L thresholds: -2.5% loss, +3.5% profit
2. Hard stop: -5% emergency market exit
3. ATR-based trailing stops (future enhancement)

Runs as part of asset_monitor sweep cycle (every 3 seconds).
"""

import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta

class PositionMonitor:
    """
    Monitors open positions and places smart LIMIT sell orders based on P&L thresholds.
    """

    def __init__(
        self,
        shared_data_manager,
        trade_order_manager,
        shared_utils_precision,
        logger
    ):
        self.shared_data_manager = shared_data_manager
        self.trade_order_manager = trade_order_manager
        self.shared_utils_precision = shared_utils_precision
        self.logger = logger

        # Load configuration from environment
        self._load_config()

        # Track last check time to respect check interval
        self.last_check_time = None

        # Track trailing stop state per position
        self.trailing_stops = {}  # {symbol: {last_high, stop_price, last_atr}}

    def _load_config(self):
        """Load position monitoring configuration from environment."""
        self.max_loss_pct = Decimal(os.getenv('MAX_LOSS_PCT', '0.025'))  # -2.5%
        self.min_profit_pct = Decimal(os.getenv('MIN_PROFIT_PCT', '0.035'))  # +3.5%
        self.hard_stop_pct = Decimal(os.getenv('HARD_STOP_PCT', '0.05'))  # -5%

        # Trailing stop configuration
        self.trailing_enabled = os.getenv('TRAILING_STOP_ENABLED', 'false').lower() == 'true'
        self.trailing_timeframe = os.getenv('TRAILING_STOP_TIMEFRAME', '1h')
        self.trailing_atr_period = int(os.getenv('TRAILING_STOP_ATR_PERIOD', '14'))
        self.trailing_atr_mult = Decimal(os.getenv('TRAILING_STOP_ATR_MULT', '2.0'))
        self.trailing_step_mult = Decimal(os.getenv('TRAILING_STEP_ATR_MULT', '0.5'))
        self.trailing_min_dist_pct = Decimal(os.getenv('TRAILING_MIN_DISTANCE_PCT', '0.01'))
        self.trailing_max_dist_pct = Decimal(os.getenv('TRAILING_MAX_DISTANCE_PCT', '0.02'))

        # Position check interval (seconds)
        self.check_interval = int(os.getenv('POSITION_CHECK_INTERVAL', '30'))

        self.logger.info(
            f"[POS_MONITOR] Configuration loaded: "
            f"max_loss={self.max_loss_pct:.2%}, min_profit={self.min_profit_pct:.2%}, "
            f"hard_stop={self.hard_stop_pct:.2%}, trailing_enabled={self.trailing_enabled}"
        )

    async def check_positions(self):
        """
        Main entry point - check all open positions and place exits if thresholds met.
        Called from asset_monitor sweep cycle.
        """
        # Respect check interval to avoid excessive processing
        now = datetime.now()
        if self.last_check_time:
            elapsed = (now - self.last_check_time).total_seconds()
            if elapsed < self.check_interval:
                return  # Silent return during interval

        self.last_check_time = now
        self.logger.info(f"[POS_MONITOR] Starting position check cycle")

        try:
            # Get all open positions from shared_data
            market_data = self.shared_data_manager.market_data or {}
            spot_positions = market_data.get('spot_positions', {})

            if not spot_positions:
                self.logger.debug("[POS_MONITOR] No spot_positions found in market_data")
                return

            # Count positions to check
            positions_to_check = 0
            for symbol, position_data in spot_positions.items():
                if symbol == 'USD':
                    continue
                total_balance = Decimal(str(position_data.get('total_balance_crypto', 0)))
                if total_balance > 0:
                    positions_to_check += 1

            self.logger.debug(
                f"[POS_MONITOR] Checking {positions_to_check} position(s) "
                f"(total positions: {len(spot_positions)})"
            )

            # Check each position (skip USD)
            for symbol, position_data in spot_positions.items():
                if symbol == 'USD':
                    continue

                # Skip if no holdings
                total_balance = Decimal(str(position_data.get('total_balance_crypto', 0)))
                if total_balance <= 0:
                    self.logger.debug(f"[POS_MONITOR] Skipping {symbol}: zero balance")
                    continue

                self.logger.debug(f"[POS_MONITOR] Calling _check_position for {symbol}")
                await self._check_position(symbol, position_data)

        except Exception as e:
            self.logger.error(f"[POS_MONITOR] Error in check_positions: {e}", exc_info=True)

    async def _check_position(self, symbol: str, position_data: Dict):
        """
        Check a single position and place exit if thresholds met.

        Args:
            symbol: Asset symbol (e.g., 'BTC')
            position_data: Position data from spot_positions
        """
        try:
            # Get position details
            total_balance_crypto = Decimal(str(position_data.get('total_balance_crypto', 0)))
            available_crypto = Decimal(str(position_data.get('available_to_trade_crypto', 0)))
            avg_entry_price = Decimal(str(position_data.get('avg_entry_price', 0)))
            current_price = Decimal(str(position_data.get('current_price', 0)))

            # DEBUG: Log raw position data to diagnose missing prices
            self.logger.debug(
                f"[POS_MONITOR] {symbol} raw data: "
                f"avg_entry={avg_entry_price}, current={current_price}, "
                f"balance={total_balance_crypto}, available={available_crypto}"
            )

            if avg_entry_price <= 0 or current_price <= 0:
                self.logger.debug(
                    f"[POS_MONITOR] {symbol} skipped: invalid prices "
                    f"(entry={avg_entry_price}, current={current_price})"
                )
                return

            # Calculate P&L
            pnl_pct = (current_price - avg_entry_price) / avg_entry_price

            # Construct product_id (assuming USD quote)
            product_id = f"{symbol}-USD"

            # Log position status
            self.logger.debug(
                f"[POS_MONITOR] {product_id}: P&L={pnl_pct:.2%} "
                f"(entry=${avg_entry_price:.4f}, current=${current_price:.4f}, "
                f"balance={total_balance_crypto:.6f})"
            )

            # Check if we already have an open sell order for this position
            if await self._has_open_sell_order(product_id):
                self.logger.debug(f"[POS_MONITOR] {product_id} already has open sell order, skipping")
                return

            # Determine exit reason and action
            exit_reason = None
            use_market_order = False

            if pnl_pct <= -self.hard_stop_pct:
                exit_reason = f"HARD_STOP (P&L: {pnl_pct:.2%})"
                use_market_order = True  # Emergency exit
            elif pnl_pct <= -self.max_loss_pct:
                exit_reason = f"STOP_LOSS (P&L: {pnl_pct:.2%})"
            elif pnl_pct >= self.min_profit_pct:
                exit_reason = f"TAKE_PROFIT (P&L: {pnl_pct:.2%})"
            elif self.trailing_enabled:
                # Check trailing stop logic (future enhancement)
                trailing_exit = await self._check_trailing_stop(symbol, product_id, current_price, avg_entry_price)
                if trailing_exit:
                    exit_reason = f"TRAILING_STOP (P&L: {pnl_pct:.2%})"

            if not exit_reason:
                self.logger.debug(f"[POS_MONITOR] {product_id} no threshold met, monitoring continues")
                return  # No exit threshold met

            # Place exit order
            self.logger.info(
                f"[POS_MONITOR] {product_id} exit triggered: {exit_reason} | "
                f"Entry: ${avg_entry_price:.4f}, Current: ${current_price:.4f}, "
                f"Balance: {total_balance_crypto:.6f}"
            )

            await self._place_exit_order(
                symbol=symbol,
                product_id=product_id,
                size=available_crypto,
                current_price=current_price,
                reason=exit_reason,
                use_market=use_market_order
            )

        except Exception as e:
            self.logger.error(f"[POS_MONITOR] Error checking position {symbol}: {e}", exc_info=True)

    async def _has_open_sell_order(self, product_id: str) -> bool:
        """
        Check if there's already an open sell order for this product.

        Args:
            product_id: Trading pair (e.g., 'BTC-USD')

        Returns:
            True if open sell order exists, False otherwise
        """
        try:
            order_tracker = self.shared_data_manager.order_management.get('order_tracker', {})

            for order_id, order_info in order_tracker.items():
                if order_info.get('symbol') == product_id or order_info.get('product_id') == product_id:
                    if order_info.get('side', '').lower() == 'sell':
                        if order_info.get('status') in {'open', 'OPEN', 'new', 'NEW'}:
                            return True

            return False

        except Exception as e:
            self.logger.debug(f"[POS_MONITOR] Error checking open sell orders for {product_id}: {e}")
            return False

    async def _place_exit_order(
        self,
        symbol: str,
        product_id: str,
        size: Decimal,
        current_price: Decimal,
        reason: str,
        use_market: bool = False
    ):
        """
        Place a LIMIT sell order to exit the position.
        For emergency exits (hard stop), use market order.

        Args:
            symbol: Asset symbol (e.g., 'BTC')
            product_id: Trading pair (e.g., 'BTC-USD')
            size: Amount to sell
            current_price: Current market price
            reason: Exit reason for logging
            use_market: If True, place market order instead of limit
        """
        try:
            # Get precision data
            precision_data = self.shared_utils_precision.fetch_precision(product_id)
            if not precision_data:
                self.logger.warning(f"[POS_MONITOR] Could not fetch precision for {product_id}")
                return

            base_deci, quote_deci, _, _ = precision_data

            # Get current order book for limit price calculation
            bid_ask = self.shared_data_manager.bid_ask_spread.get(product_id, {})
            highest_bid = Decimal(str(bid_ask.get('bid', current_price)))
            lowest_ask = Decimal(str(bid_ask.get('ask', current_price)))

            # Build order data
            trigger = self.trade_order_manager.build_trigger(
                "position_monitor_exit",
                f"{reason} - exiting position"
            )

            # For market orders (emergency exit), we'll still use LIMIT but at current bid
            # to ensure fill while maintaining some price control
            if use_market:
                # Place limit slightly below bid to ensure fill
                exit_price = highest_bid * Decimal('0.999')  # 0.1% below bid
            else:
                # Place limit at or slightly above bid for better fill rate
                exit_price = highest_bid * Decimal('1.0001')  # Just above bid

            # Adjust precision
            exit_price = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, exit_price, convert="quote"
            )

            size = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, size, convert="base"
            )

            # Build order
            order_data = await self.trade_order_manager.build_order_data(
                source="position_monitor",
                trigger=trigger,
                asset=symbol,
                product_id=product_id,
                side="sell",
                price=exit_price,
                # Note: build_order_data will set size based on available balance
            )

            if not order_data:
                self.logger.warning(f"[POS_MONITOR] Failed to build order data for {product_id} exit")
                return

            # Override size if needed
            order_data.adjusted_size = size

            # Place order
            success, response = await self.trade_order_manager.place_order(order_data, precision_data)

            if success:
                order_id = response.get('order_id')
                self.logger.info(
                    f"[POS_MONITOR] ✅ Exit order placed for {product_id}: "
                    f"order_id={order_id}, price=${exit_price:.4f}, size={size:.6f}, reason={reason}"
                )
            else:
                self.logger.warning(
                    f"[POS_MONITOR] ❌ Exit order failed for {product_id}: {response.get('message', 'Unknown error')}"
                )

        except Exception as e:
            self.logger.error(f"[POS_MONITOR] Error placing exit order for {product_id}: {e}", exc_info=True)

    async def _check_trailing_stop(
        self,
        symbol: str,
        product_id: str,
        current_price: Decimal,
        avg_entry: Decimal
    ) -> bool:
        """
        Check if trailing stop should trigger.

        This is a placeholder for Phase 3 implementation.
        Will implement ATR-based trailing logic with:
        - 2×ATR distance
        - 0.5×ATR step size
        - Only raise stop, never lower
        - 1-2% distance constraints

        Args:
            symbol: Asset symbol
            product_id: Trading pair
            current_price: Current market price
            avg_entry: Average entry price

        Returns:
            True if trailing stop should trigger, False otherwise
        """
        # TODO: Implement ATR-based trailing stop logic in Phase 3
        # For now, return False (no trailing stop trigger)
        return False
