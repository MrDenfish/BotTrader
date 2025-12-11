"""
Position Monitor - Smart LIMIT Exit Strategy with Signal Integration

Monitors open positions and places LIMIT sell orders based on:
1. Risk exits: Hard stop (-5%), Soft stop (-2.5%)
2. Signal + profit exit: SELL signal + P&L >= 0% (Phase 5)
3. Profit management: Trailing activation at +3.5%, ATR-based trailing
4. Once trailing active: ignore SELL signals, let trends run

Exit Priority:
- Hard Stop (-5%) → Soft Stop (-2.5%) → SELL Signal + Profitable → Trailing Activation/Stop

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
        self.trailing_stops = {}  # {symbol: {last_high, stop_price, last_atr, trailing_active}}

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
        self.trailing_activation_pct = Decimal(os.getenv('TRAILING_ACTIVATION_PCT', '0.035'))  # +3.5%

        # Signal-based exit configuration (Phase 5)
        self.signal_exit_enabled = os.getenv('SIGNAL_EXIT_ENABLED', 'true').lower() == 'true'
        self.signal_exit_min_profit = Decimal(os.getenv('SIGNAL_EXIT_MIN_PROFIT_PCT', '0.0'))  # Exit on SELL if P&L >= 0%

        # Position check interval (seconds)
        self.check_interval = int(os.getenv('POSITION_CHECK_INTERVAL', '30'))

        self.logger.info(
            f"[POS_MONITOR] Configuration loaded: "
            f"max_loss={self.max_loss_pct:.2%}, min_profit={self.min_profit_pct:.2%}, "
            f"hard_stop={self.hard_stop_pct:.2%}, trailing_enabled={self.trailing_enabled}, "
            f"signal_exit_enabled={self.signal_exit_enabled}"
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
                self.logger.debug(
                    f"[POS_MONITOR] Skipping check: {elapsed:.1f}s elapsed, "
                    f"interval is {self.check_interval}s"
                )
                return  # Silent return during interval

        self.last_check_time = now
        self.logger.info(
            f"[POS_MONITOR] Starting position check cycle "
            f"(interval: {self.check_interval}s)"
        )

        try:
            # Load HODL list from environment (do not sell these assets)
            hodl_list = os.getenv('HODL', '').split(',')
            hodl_assets = {asset.strip().upper() for asset in hodl_list if asset.strip()}

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
                # Skip HODL assets
                if symbol.upper() in hodl_assets:
                    continue
                total_balance = Decimal(str(position_data.get('total_balance_crypto', 0)))
                if total_balance > 0:
                    positions_to_check += 1

            self.logger.debug(
                f"[POS_MONITOR] Checking {positions_to_check} position(s) "
                f"(total positions: {len(spot_positions)}, HODL assets: {hodl_assets})"
            )

            # Check each position (skip USD and HODL assets)
            for symbol, position_data in spot_positions.items():
                if symbol == 'USD':
                    continue

                # Skip HODL assets
                if symbol.upper() in hodl_assets:
                    self.logger.debug(f"[POS_MONITOR] Skipping {symbol} - marked as HODL (no sells allowed)")
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

            # Construct product_id (assuming USD quote)
            product_id = f"{symbol}-USD"

            # Get unrealized P&L to calculate average entry price
            unrealized_pnl_data = position_data.get('unrealized_pnl', {})
            if isinstance(unrealized_pnl_data, dict):
                unrealized_pnl = Decimal(str(unrealized_pnl_data.get('value', 0)))
            else:
                unrealized_pnl = Decimal(str(unrealized_pnl_data or 0))

            # Fetch current price from bid_ask_spread in market_data
            market_data = self.shared_data_manager.market_data or {}
            bid_ask_spread = market_data.get('bid_ask_spread', {})
            bid_ask = bid_ask_spread.get(product_id, {})
            current_bid = Decimal(str(bid_ask.get('bid', 0)))
            current_ask = Decimal(str(bid_ask.get('ask', 0)))
            # Use mid-price for P&L calculation
            current_price = (current_bid + current_ask) / Decimal('2') if (current_bid > 0 and current_ask > 0) else Decimal('0')

            # Calculate avg_entry_price from unrealized_pnl
            # Formula: unrealized_pnl = (current_price - avg_entry_price) * balance
            # Therefore: avg_entry_price = current_price - (unrealized_pnl / balance)
            if current_price > 0 and total_balance_crypto > 0:
                avg_entry_price = current_price - (unrealized_pnl / total_balance_crypto)
            else:
                avg_entry_price = Decimal('0')

            # DEBUG: Log fetched price data
            self.logger.debug(
                f"[POS_MONITOR] {symbol} calculated prices: "
                f"avg_entry={avg_entry_price}, current={current_price}, "
                f"unrealized_pnl={unrealized_pnl}, balance={total_balance_crypto}"
            )

            if avg_entry_price <= 0 or current_price <= 0:
                self.logger.debug(
                    f"[POS_MONITOR] {symbol} skipped: invalid prices "
                    f"(entry={avg_entry_price}, current={current_price})"
                )
                return

            # Calculate P&L
            pnl_pct = (current_price - avg_entry_price) / avg_entry_price

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

            # ✅ Task 3: Check for active bracket orders (coordination)
            bracket_info = await self._has_active_bracket_order(product_id)
            has_bracket = bracket_info.get('has_bracket', False)

            if has_bracket:
                self.logger.debug(
                    f"[POS_MONITOR] {product_id} has active bracket "
                    f"(SL: ${bracket_info.get('stop_price'):.4f}, TP: ${bracket_info.get('tp_price'):.4f})"
                )

            # Phase 5: New Exit Priority Logic (with Bracket Coordination)
            # Priority: Hard Stop → Soft Stop → (Check Bracket → Signal/Trailing)
            exit_reason = None
            use_market_order = False
            override_bracket = False  # New flag for coordination

            # 1. EMERGENCY HARD STOP (always override bracket)
            if pnl_pct <= -self.hard_stop_pct:
                exit_reason = f"HARD_STOP (P&L: {pnl_pct:.2%})"
                use_market_order = True  # Emergency exit
                override_bracket = True  # Always override for emergency

            # 2. SOFT STOP (coordinate with bracket)
            elif pnl_pct <= -self.max_loss_pct:
                # ✅ FIX: Use market orders for severe SOFT_STOP losses (> -3%) to ensure execution
                if pnl_pct <= Decimal("-0.03"):  # Loss worse than -3%
                    use_market_order = True
                    self.logger.warning(
                        f"[POS_MONITOR] {product_id} SEVERE LOSS detected (P&L: {pnl_pct:.2%}). "
                        f"Using MARKET order for immediate exit."
                    )

                if has_bracket:
                    # Bracket exists - check if it's at same level
                    bracket_sl_pct = (bracket_info['stop_price'] - avg_entry_price) / avg_entry_price

                    if abs(bracket_sl_pct - (-self.max_loss_pct)) < 0.005:  # Within 0.5%
                        # Bracket will handle it, don't place redundant order
                        self.logger.debug(
                            f"[POS_MONITOR] {product_id} SOFT_STOP level matches bracket "
                            f"(bracket: {bracket_sl_pct:.2%}, monitor: {-self.max_loss_pct:.2%}), "
                            f"deferring to bracket"
                        )
                        return  # Let bracket do its job
                    else:
                        # Bracket exists but at different level - log warning
                        self.logger.warning(
                            f"[POS_MONITOR] {product_id} SOFT_STOP mismatch! "
                            f"Bracket SL: {bracket_sl_pct:.2%}, Monitor SL: {-self.max_loss_pct:.2%}"
                        )
                        exit_reason = f"SOFT_STOP (P&L: {pnl_pct:.2%}, overriding bracket)"
                        override_bracket = True
                else:
                    # No bracket - position monitor handles exit
                    exit_reason = f"SOFT_STOP (P&L: {pnl_pct:.2%}, no bracket)"

            # 2. PROFIT MANAGEMENT (only if no risk exit triggered)
            elif self.trailing_enabled:
                # Check if trailing is already active for this position
                trailing_state = self.trailing_stops.get(product_id, {})
                trailing_active = trailing_state.get('trailing_active', False)

                if trailing_active:
                    # Trailing is active - IGNORE signal exits, only check trailing stop
                    self.logger.debug(f"[POS_MONITOR] {product_id} trailing active, checking stop only")
                    trailing_exit = await self._check_trailing_stop(symbol, product_id, current_price, avg_entry_price)
                    if trailing_exit:
                        exit_reason = f"TRAILING_STOP (P&L: {pnl_pct:.2%})"
                else:
                    # Trailing not active - check for activation or signal exit
                    if pnl_pct >= self.trailing_activation_pct:
                        # Activate trailing stop at +3.5%
                        self.logger.info(
                            f"[POS_MONITOR] {product_id} TRAILING ACTIVATED at P&L={pnl_pct:.2%} "
                            f"(threshold: {self.trailing_activation_pct:.2%})"
                        )
                        # Initialize trailing stop
                        await self._check_trailing_stop(symbol, product_id, current_price, avg_entry_price)
                        # Mark as active
                        if product_id in self.trailing_stops:
                            self.trailing_stops[product_id]['trailing_active'] = True
                        # Continue monitoring (don't exit yet, just activated)
                        self.logger.debug(f"[POS_MONITOR] {product_id} trailing initialized, monitoring continues")
                        return

                    # Check signal-based exit (only if trailing not active AND P&L >= 0%)
                    elif self.signal_exit_enabled:
                        current_signal = self._get_current_signal(symbol)
                        if current_signal == 'sell' and pnl_pct >= self.signal_exit_min_profit:
                            exit_reason = f"SIGNAL_EXIT (P&L: {pnl_pct:.2%}, signal=SELL)"

            # 3. TAKE PROFIT (coordinate with bracket)
            elif not self.trailing_enabled and pnl_pct >= self.min_profit_pct:
                if has_bracket:
                    # Check if bracket TP will handle it
                    bracket_tp_pct = (bracket_info['tp_price'] - avg_entry_price) / avg_entry_price

                    if abs(bracket_tp_pct - self.min_profit_pct) < 0.005:  # Within 0.5%
                        self.logger.debug(
                            f"[POS_MONITOR] {product_id} TP level matches bracket "
                            f"(bracket: {bracket_tp_pct:.2%}, monitor: {self.min_profit_pct:.2%}), "
                            f"deferring to bracket"
                        )
                        return  # Let bracket handle it
                    else:
                        # Bracket TP different - override
                        exit_reason = f"TAKE_PROFIT (P&L: {pnl_pct:.2%}, overriding bracket)"
                        override_bracket = True
                else:
                    # No bracket - position monitor handles exit
                    exit_reason = f"TAKE_PROFIT (P&L: {pnl_pct:.2%}, no bracket)"

            if not exit_reason:
                self.logger.debug(f"[POS_MONITOR] {product_id} no exit condition met, monitoring continues")
                return  # No exit threshold met

            # ✅ Task 3: Coordination decision logging
            if override_bracket and has_bracket:
                self.logger.warning(
                    f"[COORD] {product_id} overriding bracket order: {exit_reason}"
                )
            elif has_bracket and not override_bracket:
                self.logger.info(
                    f"[COORD] {product_id} deferring to bracket order: {exit_reason}"
                )
                return  # Let bracket handle it
            else:
                self.logger.info(
                    f"[COORD] {product_id} placing exit (no bracket): {exit_reason}"
                )

            # Place exit order
            self.logger.info(
                f"[POS_MONITOR] {product_id} exit triggered: {exit_reason} | "
                f"Entry: ${avg_entry_price:.4f}, Current: ${current_price:.4f}, "
                f"Balance: {total_balance_crypto:.6f}"
            )

            await self._place_exit_order(
                symbol=symbol,
                product_id=product_id,
                size=total_balance_crypto,  # Use total balance, not available (which could be 0 if locked)
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

    async def _has_active_bracket_order(self, product_id: str) -> dict:
        """
        ✅ Task 2: Check if position has active bracket orders on exchange.

        Args:
            product_id: Trading pair (e.g., 'BTC-USD')

        Returns:
            dict with 'has_bracket', 'stop_price', 'tp_price', or empty dict if no bracket
        """
        try:
            bracket_orders = self.shared_data_manager.order_management.get('bracket_orders', {})
            bracket = bracket_orders.get(product_id)

            if not bracket:
                return {}

            # Check if bracket is still active
            if bracket.get('status') != 'active':
                self.logger.debug(f"[BRACKET_CHECK] {product_id} bracket exists but not active (status: {bracket.get('status')})")
                return {}

            # Bracket exists and is active
            return {
                'has_bracket': True,
                'stop_price': bracket.get('stop_price'),
                'tp_price': bracket.get('tp_price'),
                'entry_price': bracket.get('entry_price'),
                'entry_order_id': bracket.get('entry_order_id'),
                'stop_order_id': bracket.get('stop_order_id'),
                'tp_order_id': bracket.get('tp_order_id')
            }

        except Exception as e:
            self.logger.debug(f"[BRACKET_CHECK] Error checking bracket for {product_id}: {e}")
            return {}

    def _get_current_signal(self, symbol: str) -> Optional[str]:
        """
        Query current BUY/SELL signal from cached buy_sell_matrix.

        Args:
            symbol: Asset symbol (e.g., 'BTC')

        Returns:
            'buy', 'sell', or None if signal unavailable
        """
        try:
            if not self.signal_exit_enabled:
                return None

            # Get cached buy_sell_matrix from shared_data
            market_data = self.shared_data_manager.market_data or {}
            buy_sell_matrix = market_data.get('buy_sell_matrix')

            if buy_sell_matrix is None or buy_sell_matrix.empty:
                self.logger.debug(f"[SIGNAL] buy_sell_matrix not available")
                return None

            # Check if symbol is in matrix
            if symbol not in buy_sell_matrix.index:
                self.logger.debug(f"[SIGNAL] {symbol} not in buy_sell_matrix")
                return None

            # Get signals (tuples: (decision, score, threshold, reason))
            buy_signal = buy_sell_matrix.loc[symbol, 'Buy Signal']
            sell_signal = buy_sell_matrix.loc[symbol, 'Sell Signal']

            # Check which signal is active (decision == 1)
            buy_active = buy_signal[0] == 1 if isinstance(buy_signal, tuple) and len(buy_signal) > 0 else False
            sell_active = sell_signal[0] == 1 if isinstance(sell_signal, tuple) and len(sell_signal) > 0 else False

            if sell_active:
                return 'sell'
            elif buy_active:
                return 'buy'
            else:
                return None

        except Exception as e:
            self.logger.debug(f"[SIGNAL] Error getting signal for {symbol}: {e}")
            return None

    async def _cancel_existing_orders(self, product_id: str):
        """
        Cancel all existing orders for this product to free up locked balance.
        This is critical before placing exit orders to avoid the "available_to_trade = 0" loop.

        Args:
            product_id: Trading pair (e.g., 'BTC-USD')
        """
        try:
            order_tracker = self.shared_data_manager.order_management.get('order_tracker', {})

            # Find all orders for this product
            orders_to_cancel = []
            for oid, order_info in list(order_tracker.items()):
                if order_info.get('symbol') == product_id or order_info.get('product_id') == product_id:
                    orders_to_cancel.append((oid, order_info))

            if not orders_to_cancel:
                self.logger.debug(f"[POS_MONITOR] No existing orders to cancel for {product_id}")
                return

            # Cancel each order
            for oid, order_info in orders_to_cancel:
                self.logger.info(
                    f"[POS_MONITOR] Canceling existing {order_info.get('side', 'unknown')} order "
                    f"{oid} for {product_id} before placing exit order"
                )

                # Use the trade_order_manager's coinbase_api to cancel
                try:
                    cancel_resp = await self.trade_order_manager.coinbase_api.cancel_order([oid])

                    # Validate cancellation
                    results = (cancel_resp or {}).get("results") or []
                    entry = next((r for r in results if str(r.get("order_id")) == str(oid)), None)

                    if entry and entry.get("success"):
                        # Remove from order tracker
                        if oid in order_tracker:
                            del order_tracker[oid]
                        self.logger.info(f"[POS_MONITOR] ✅ Successfully cancelled order {oid}")
                    else:
                        failure_reason = entry.get("failure_reason") if entry else "Unknown"
                        self.logger.warning(
                            f"[POS_MONITOR] Failed to cancel order {oid}: {failure_reason}"
                        )
                except Exception as cancel_error:
                    self.logger.warning(
                        f"[POS_MONITOR] Error canceling order {oid}: {cancel_error}"
                    )

        except Exception as e:
            self.logger.warning(f"[POS_MONITOR] Error in _cancel_existing_orders for {product_id}: {e}")

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
            # Cancel any existing orders for this symbol to free up locked balance
            # This prevents the "available_to_trade = 0" issue when balance is locked
            await self._cancel_existing_orders(product_id)

            # Get precision data
            precision_data = self.shared_utils_precision.fetch_precision(product_id)
            if not precision_data:
                self.logger.warning(f"[POS_MONITOR] Could not fetch precision for {product_id}")
                return

            base_deci, quote_deci, _, _ = precision_data

            # Get current order book for limit price calculation
            market_data = self.shared_data_manager.market_data or {}
            bid_ask_spread = market_data.get('bid_ask_spread', {})
            bid_ask = bid_ask_spread.get(product_id, {})
            highest_bid = Decimal(str(bid_ask.get('bid', current_price)))
            lowest_ask = Decimal(str(bid_ask.get('ask', current_price)))

            # Build order data
            trigger = self.trade_order_manager.build_trigger(
                "position_monitor_exit",
                f"{reason} - exiting position"
            )

            # ✅ FIX: For sell orders, price must be AT OR BELOW bid to fill immediately
            # Setting above bid causes orders to chase the price down and never fill
            if use_market:
                # Emergency exit: Place well below bid to ensure immediate fill
                exit_price = highest_bid * Decimal('0.995')  # 0.5% below bid for guaranteed fill
                self.logger.warning(
                    f"[POS_MONITOR] {product_id} using aggressive limit price "
                    f"(0.5% below bid) for emergency exit"
                )
            else:
                # Regular exit: Place slightly below bid for quick fill
                exit_price = highest_bid * Decimal('0.9995')  # 0.05% below bid

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
                # Note: build_order_data will set size based on available balance
            )

            if not order_data:
                self.logger.warning(f"[POS_MONITOR] Failed to build order data for {product_id} exit")
                return

            # Set exit price and order amounts
            # IMPORTANT: We need to set order_amount_crypto, not just adjusted_size,
            # because handle_order recalculates adjusted_size from order_amount_crypto
            order_data.price = exit_price
            order_data.limit_price = exit_price
            order_data.order_amount_crypto = size  # This is what adjust_price_and_size uses
            order_data.adjusted_price = exit_price  # Pre-set to guide price calculation
            order_data.adjusted_size = size  # Pre-set to guide size calculation

            # DEBUG: Log complete OrderData state before placement
            self.logger.debug(
                f"[POS_MONITOR] OrderData before placement: "
                f"product_id={getattr(order_data, 'trading_pair', None)}, "
                f"side={getattr(order_data, 'side', None)}, "
                f"price={getattr(order_data, 'price', None)}, "
                f"limit_price={getattr(order_data, 'limit_price', None)}, "
                f"order_amount_crypto={getattr(order_data, 'order_amount_crypto', None)}, "
                f"adjusted_size={getattr(order_data, 'adjusted_size', None)}, "
                f"adjusted_price={getattr(order_data, 'adjusted_price', None)}, "
                f"order_type={getattr(order_data, 'type', None)}, "
                f"source={getattr(order_data, 'source', None)}, "
                f"trigger={getattr(order_data, 'trigger', None)}"
            )

            # Place order
            success, response = await self.trade_order_manager.place_order(order_data, precision_data)

            if success:
                order_id = response.get('order_id')
                self.logger.info(
                    f"[POS_MONITOR] ✅ Exit order placed for {product_id}: "
                    f"order_id={order_id}, price=${exit_price:.4f}, size={size:.6f}, reason={reason}"
                )

                # ✅ Task 4: Exit source logging and tracking
                exit_source = 'EMERGENCY_STOP' if use_market else 'POSITION_MONITOR'
                exit_type = 'MARKET' if use_market else 'LIMIT'

                self.logger.info(
                    f"[EXIT_SOURCE] {product_id} | Reason: {reason} | "
                    f"Source: {exit_source} | Order Type: {exit_type} | "
                    f"Order ID: {order_id}"
                )

                # Store exit metadata for reporting and analysis
                from datetime import datetime, timezone
                exit_metadata = {
                    'product_id': product_id,
                    'exit_source': exit_source,
                    'exit_reason': reason,
                    'exit_type': exit_type,
                    'exit_time': datetime.now(timezone.utc),
                    'exit_price': float(exit_price),
                    'exit_size': float(size),
                    'order_id': order_id
                }

                # Initialize exit_tracking dict if needed
                if 'exit_tracking' not in self.shared_data_manager.order_management:
                    self.shared_data_manager.order_management['exit_tracking'] = []

                # Store for daily report analysis
                self.shared_data_manager.order_management['exit_tracking'].append(exit_metadata)

                self.logger.debug(f"[EXIT_TRACK] Stored exit metadata for {product_id}: {exit_metadata}")

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

        Implements ATR-based trailing logic with:
        - 2×ATR distance below highest price
        - 0.5×ATR step size for raising stops
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
        try:
            # Get ATR from cache
            market_data = self.shared_data_manager.market_data or {}
            atr_pct_cache = market_data.get('atr_pct_cache') or {}
            atr_pct = atr_pct_cache.get(product_id)

            if not atr_pct:
                # Try to calculate from atr_price_cache
                atr_price_cache = market_data.get('atr_price_cache') or {}
                atr_price = atr_price_cache.get(product_id)
                if atr_price and current_price > 0:
                    atr_pct = Decimal(str(atr_price)) / current_price
                else:
                    self.logger.debug(f"[TRAILING] {product_id}: No ATR data available, skipping trailing stop")
                    return False
            else:
                atr_pct = Decimal(str(atr_pct))

            # Initialize state for this position if not exists
            if product_id not in self.trailing_stops:
                # First time seeing this position
                self.trailing_stops[product_id] = {
                    'last_high': current_price,
                    'stop_price': None,  # Will be set when position becomes profitable
                    'last_atr': atr_pct,
                    'trailing_active': False  # Phase 5: tracks if trailing is activated
                }
                self.logger.info(
                    f"[TRAILING] {product_id}: Initialized trailing stop state | "
                    f"Entry: ${avg_entry:.4f}, Current: ${current_price:.4f}, ATR: {atr_pct:.2%}"
                )
                return False

            state = self.trailing_stops[product_id]

            # Update last_high if current price is higher
            new_high = False
            if current_price > state['last_high']:
                state['last_high'] = current_price
                state['last_atr'] = atr_pct
                new_high = True

            # Calculate stop price based on 2×ATR distance from last_high
            atr_distance = state['last_high'] * atr_pct * self.trailing_atr_mult
            calculated_stop = state['last_high'] - atr_distance

            # Apply distance constraints (1-2% from current price)
            min_stop = current_price * (Decimal('1') - self.trailing_max_dist_pct)  # Max 2% below current
            max_stop = current_price * (Decimal('1') - self.trailing_min_dist_pct)  # Min 1% below current

            # Constrain the calculated stop
            constrained_stop = max(min_stop, min(calculated_stop, max_stop))

            # Only raise the stop, never lower it
            if state['stop_price'] is None:
                # First time setting stop - only set if position is profitable
                pnl_pct = (current_price - avg_entry) / avg_entry
                if pnl_pct > Decimal('0'):  # Only activate trailing stop when profitable
                    state['stop_price'] = constrained_stop
                    self.logger.info(
                        f"[TRAILING] {product_id}: Activated trailing stop | "
                        f"Stop: ${state['stop_price']:.4f}, High: ${state['last_high']:.4f}, "
                        f"Current: ${current_price:.4f}, ATR: {atr_pct:.2%}"
                    )
                return False
            else:
                # Update stop only if new stop is higher (raise only, never lower)
                step_size = state['last_high'] * state['last_atr'] * self.trailing_step_mult

                if constrained_stop > state['stop_price']:
                    # Check if price has moved enough (0.5×ATR step) to warrant an update
                    price_move = state['last_high'] - (state['stop_price'] + (state['stop_price'] * state['last_atr'] * self.trailing_atr_mult))

                    if new_high or price_move >= step_size:
                        old_stop = state['stop_price']
                        state['stop_price'] = constrained_stop
                        self.logger.info(
                            f"[TRAILING] {product_id}: Raised stop | "
                            f"Old: ${old_stop:.4f} → New: ${state['stop_price']:.4f}, "
                            f"High: ${state['last_high']:.4f}, Current: ${current_price:.4f}"
                        )

            # Check if trailing stop is hit
            if state['stop_price'] and current_price <= state['stop_price']:
                self.logger.info(
                    f"[TRAILING] {product_id}: STOP HIT! | "
                    f"Current: ${current_price:.4f} ≤ Stop: ${state['stop_price']:.4f}, "
                    f"Entry: ${avg_entry:.4f}, High: ${state['last_high']:.4f}"
                )
                # Clear state after triggering
                del self.trailing_stops[product_id]
                return True

            return False

        except Exception as e:
            self.logger.error(f"[TRAILING] {product_id}: Error in trailing stop logic: {e}", exc_info=True)
            return False
