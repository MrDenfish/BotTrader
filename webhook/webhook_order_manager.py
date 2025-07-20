
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Dict, Union, Optional

import pandas as pd

from Config.config_manager import CentralConfig as config
from webhook.webhook_validate_orders import OrderData

# Define the TradeOrderManager class
"""This class  will manage the trade orders."""


class TradeOrderManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api=None, exchange_client=None, shared_utils_precision=None, shared_utils_utility= None, validate=None,
                     logger_manager=None, alerts=None, ccxt_api=None, market_data_updater= None, order_book_manager=None, order_types=None,
                     websocket_helper=None, shared_data_manager=None, session=None, profit_manager=None):
        """
        Singleton method to ensure only one instance of TradeOrderManager exists.
        If already instantiated, returns the existing instance.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision,
                                shared_utils_utility, validate, logger_manager, alerts,
                                ccxt_api, market_data_updater, order_book_manager,
                                order_types, websocket_helper,shared_data_manager,
                                session, profit_manager)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logger_manager,
                 alerts, ccxt_api, market_data_updater, order_book_manager, order_types, websocket_helper, shared_data_manager, session,
                 profit_manager):
        """
        Initializes the TradeOrderManager.
        """
        self.config = config()
        self.coinbase_api = coinbase_api
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_order_amount_fiat = self.config.min_order_amount_fiat
        self._min_sell_value = self.config.min_sell_value
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._order_size_fiat = self.config.order_size_fiat
        self._hodl = self.config.hodl
        self._default_maker_fee = self.config.maker_fee
        self._default_taker_fee = self.config.taker_fee
        self.logger = logger_manager  # 🙂

        self.validate= validate
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.market_data_updater = market_data_updater
        self.websocket_helper = websocket_helper
        self._shared_data_manager = shared_data_manager

        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.profit_manager = profit_manager
        self.session = session


    @property
    def default_maker_fee(self):
        return self._default_maker_fee

    @property
    def fee_info(self):
        return self.shared_data_manager.market_data.get('fee_info', {})

    @property
    def default_taker_fee(self):
        return self._default_taker_fee

    @property
    def shared_data_manager(self):
        return self._shared_data_manager

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def spot_position(self):
        return self.shared_data_manager.market_data.get('spot_positions')


    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get("order_tracker")

    @property
    def passive_orders(self):
        return self.shared_data_manager.order_management.get('passive_orders') or {}

    @property
    def avg_quote_volume(self):
        return Decimal(self.shared_data_manager.market_data['avg_quote_volume'])

    @property
    def hodl(self):
        return self._hodl

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def min_order_amount(self):
        return self._min_order_amount_fiat

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self._max_value_of_crypto_to_buy_more

    @property
    def order_size(self):
        return float(self._order_size_fiat)

    async def build_order_data(
            self,
            source: str,
            trigger: Union[str, Dict[str, str]],
            asset: str,
            product_id: str,
            stop_price: Optional[Decimal] = None,
            order_type: Optional[str] = None,
            side: Optional[str] = None,
            test_mode: bool = False
    ) -> Optional[OrderData]:
        """
        Builds a fully prepared OrderData instance, including validation and test-mode overrides.

        Args:
            source (str): The source of the order (e.g., 'Webhook', 'PassiveMM').
            trigger (str|dict): Triggering reason or trigger dict.
            asset (str): Base asset symbol (e.g., 'ETH').
            product_id (str): Full trading pair (e.g., 'ETH-USD').
            stop_price (Decimal|None): Optional stop price for stop-limit or TP/SL orders.
            order_type (str|None): Order type (e.g., 'limit', 'stop_limit').
            side (str|None): Side ('buy' or 'sell'). Determined automatically if None.
            test_mode (bool): If True, overrides balance and price validation for testing.

        Returns:
            Optional[OrderData]: Fully constructed OrderData or None if validation fails.
        """
        try:
            # ✅ Market data validation
            if not test_mode and self.market_data_updater.get_empty_keys(self.market_data):
                self.logger.warning(f"⚠️ Market data incomplete — skipping {asset}")
                return None
            elif test_mode:
                self.logger.warning(f"⚠️ [TEST MODE] Skipping market data completeness check for {asset}")

            # ✅ Basic setup
            trading_pair = product_id.replace("/", "-")
            spot = self.spot_position.get(asset, {})
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(asset)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)

            passive_order_data = self.passive_orders.get(asset, {})
            usd_data = self.spot_position.get("USD", {})
            usd_balance = Decimal(usd_data.get("total_balance_fiat", 0))
            usd_avail = self.shared_utils_precision.safe_quantize(
                Decimal(usd_data.get("available_to_trade_fiat", 0)),
                quote_quantizer
            )
            min_order_threshold = getattr(self, "min_order_threshold", Decimal("5.00"))

            # ✅ Bid/Ask & initial pricing
            bid_ask = self.bid_ask_spread.get(trading_pair, {})
            bid = Decimal(bid_ask.get("bid", 0))
            ask = Decimal(bid_ask.get("ask", 0))
            current_bid = self.shared_utils_precision.safe_quantize(bid, quote_quantizer)
            current_ask = self.shared_utils_precision.safe_quantize(ask, quote_quantizer)
            spread = Decimal(bid_ask.get("spread", 0))
            price = (current_bid + current_ask) / 2 if (current_bid and current_ask) else Decimal("0")

            # ✅ Determine order amounts before overrides
            total_balance_crypto = Decimal(spot.get("total_balance_crypto", 0))
            available_to_trade = Decimal(spot.get("available_to_trade_crypto", 0))
            fiat_amt = usd_avail
            crypto_amt = available_to_trade
            order_amount_fiat, order_amount_crypto = self.shared_utils_utility.initialize_order_amounts(
                side if side else "buy", fiat_amt, crypto_amt
            )

            # ✅ Centralized test mode overrides
            if test_mode:
                (usd_avail, usd_balance, spot, price,
                 order_amount_fiat, order_amount_crypto) = self.apply_test_mode_overrides(
                    asset=asset,
                    usd_avail=usd_avail,
                    usd_balance=usd_balance,
                    spot=spot,
                    price=price,
                    order_amount_fiat=order_amount_fiat,
                    order_amount_crypto=order_amount_crypto
                )

            # ✅ Handle new assets (with test-mode skip)
            if not spot and not passive_order_data and not side:
                if usd_avail >= min_order_threshold or test_mode:
                    self.logger.info(f"💡 {'[TEST MODE] ' if test_mode else ''}Proceeding with buy for new asset {asset}")
                    spot = {}  # Allow downstream logic
                else:
                    self.logger.warning(f"⚠️ Skipping {asset} — no wallet, no passive order, and USD < {min_order_threshold}")
                    return None

            # ✅ PassiveMM quoting allowed for new assets
            if source == "PassiveMM":
                if not passive_order_data:
                    if usd_avail >= min_order_threshold or test_mode:
                        self.logger.info(
                            f"💡 {'[TEST MODE] ' if test_mode else ''}PassiveMM initializing first-time quote for {asset}"
                        )
                        passive_order_data = {}
                    else:
                        self.logger.warning(f"⚠️ PassiveMM skipping {asset} — no passive data and insufficient USD.")
                        return None
                self.shared_utils_utility.get_passive_order_data(passive_order_data)

            # ✅ Final price validation (after overrides)
            if price == 0:
                self.logger.warning(f"⚠️ Price is zero for {trading_pair} — skipping order")
                return None

            # ✅ Side fallback logic
            if side is None:
                side = "buy" if usd_avail >= self.order_size or test_mode else "sell"

            # ✅ Skip 24h price change checks in test_mode
            if side == "buy" and not test_mode:
                try:
                    usd_pairs = self.usd_pairs.set_index("asset")
                    price_change_24h = usd_pairs.loc[asset, 'price_percentage_change_24h'] if asset in usd_pairs.index else None
                    if price_change_24h is None or Decimal(price_change_24h) <= 0:
                        self.logger.info(f"📉 Skipping BUY for {asset} — 24h price change {price_change_24h}% not favorable")
                        return None
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to check price change for {asset}: {e}")
                    return None

            # ✅ Fee setup
            if not self.fee_info:
                maker_fee, taker_fee = self.default_maker_fee, self.default_taker_fee
            else:
                maker_fee = Decimal(self.fee_info.get('fee_rates', {}).get('maker') or self.default_maker_fee)
                taker_fee = Decimal(self.fee_info.get('fee_rates', {}).get('taker') or self.default_taker_fee)

            # ✅ Trigger formatting
            trigger_note = f"triggered by {trigger}" if isinstance(trigger, str) else trigger.get("trigger_note", "")
            trigger_dict = trigger if isinstance(trigger, dict) else self.build_trigger(trigger, trigger_note)

            # ✅ Return final OrderData
            return OrderData(
                trading_pair=trading_pair,
                time_order_placed=None,
                type=order_type or "limit",
                order_id="UNKNOWN",
                side=side,
                order_amount_fiat=order_amount_fiat,
                order_amount_crypto=order_amount_crypto,
                filled_price=None,
                base_currency=asset,
                quote_currency="USD",
                usd_avail_balance=usd_avail,
                usd_balance=usd_balance,
                base_avail_balance=Decimal(spot.get("available_to_trade_crypto", 0)),
                total_balance_crypto=Decimal(spot.get("total_balance_crypto", 0)),
                available_to_trade_crypto=Decimal(spot.get("available_to_trade_crypto", 0)),
                base_decimal=base_deci,
                quote_decimal=quote_deci,
                quote_increment=quote_quantizer,
                highest_bid=current_bid,
                lowest_ask=current_ask,
                maker=maker_fee,
                taker=taker_fee,
                spread=spread,
                open_orders={},
                status="UNKNOWN",
                source=source,
                trigger=trigger_dict,
                price=price,
                cost_basis=Decimal("0"),
                limit_price=price,
                average_price=None,
                avg_quote_volume=self.avg_quote_volume,
                adjusted_price=None,
                adjusted_size=None,
                stop_loss_price=stop_price,
                take_profit_price=None,
                volume_24h=None
            )

        except Exception as e:
            self.logger.error(f"❌ Error in build_order_data for {asset} {trigger}: {e}", exc_info=True)
            return None

    def apply_test_mode_overrides(
            self,
            asset: str,
            usd_avail: Decimal,
            usd_balance: Decimal,
            spot: dict,
            price: Optional[Decimal] = None,
            order_amount_fiat: Optional[Decimal] = None,
            order_amount_crypto: Optional[Decimal] = None
    ) -> tuple:
        """
        Centralized test mode overrides for balances, price, and order amounts.
        Ensures safe dummy values for testing without affecting live trading logic.

        Args:
            asset (str): The base asset being traded (e.g., 'ETH').
            usd_avail (Decimal): Available USD balance before overrides.
            usd_balance (Decimal): Total USD balance before overrides.
            spot (dict): Spot balance dict for the asset (crypto amounts).
            price (Decimal|None): Current calculated price (fallbacks to 1.00 if zero).
            order_amount_fiat (Decimal|None): Fiat order amount before overrides.
            order_amount_crypto (Decimal|None): Crypto order amount before overrides.

        Returns:
            tuple: (usd_avail, usd_balance, spot, price, order_amount_fiat, order_amount_crypto)
                   with test mode adjustments applied.
        """
        self.logger.warning(f"⚠️ [TEST MODE] Applying overrides for {asset}")

        # ✅ Ensure dummy USD balances (cap to configured order size)
        usd_avail = min(usd_avail, self.order_size)
        usd_balance = min(usd_balance, self.order_size)

        # ✅ Ensure dummy crypto balances (safe fallback for SELL or TP/SL logic)
        spot = {
            "total_balance_crypto": max(Decimal(spot.get("total_balance_crypto", 0)), Decimal("1.0")),
            "available_to_trade_crypto": max(Decimal(spot.get("available_to_trade_crypto", 0)), Decimal("1.0"))
        }

        # ✅ Force safe fallback price if missing or zero
        if price is not None and price <= 0:
            self.logger.warning(f"⚠️ [TEST MODE] Forcing dummy price for {asset}")
            price = Decimal("1.00")

        # ✅ Ensure reasonable order amounts for testing
        if order_amount_fiat is not None:
            order_amount_fiat = min(order_amount_fiat, usd_avail)
        if order_amount_crypto is not None:
            order_amount_crypto = max(order_amount_crypto, Decimal("0.01"))

        return usd_avail, usd_balance, spot, price, order_amount_fiat, order_amount_crypto

    def build_trigger(self,trigger_type: str, note: str = "") -> dict:
        return {
            "trigger": trigger_type.upper(),
            "trigger_note": note
        }

    async def place_order(self, raw_order_data: OrderData, precision_data=None) -> tuple[bool, dict]:
        try:
            self.shared_utils_utility.log_event_loop("place_order")
            trading_pair = raw_order_data.trading_pair
            all_open_orders = self.open_orders  # shared state
            has_open_order, open_order = self.shared_utils_utility.has_open_orders(trading_pair, all_open_orders)

            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
            raw_order_data.open_orders = not open_orders.empty and raw_order_data.trading_pair in open_orders.symbol.values

            if not precision_data:
                precision_data = self.shared_utils_precision.fetch_precision(raw_order_data.trading_pair)
                base_deci, quote_deci, _, _ = precision_data
                raw_order_data.base_decimal = base_deci
                raw_order_data.quote_decimal = quote_deci

            # Step 1: Light validation
            validation_result = self.validate.validate_order_conditions(raw_order_data, open_orders)
            if not validation_result["is_valid"] or has_open_order:
                return False, validation_result

            # Step 2: Get order book
            order_book_details = self.bid_ask_spread.get(trading_pair)

            # Step 3: Full validation
            validation_result = self.validate.fetch_and_validate_rules(raw_order_data)
            if not validation_result["is_valid"]:
                return False, validation_result

            # Step 4: Construct final OrderData
            order_data = self.validate.build_order_data_from_validation_result(
                validation_result, order_book_details, precision_data
            )

            return await self.handle_order(order_data, order_book_details)

        except Exception as ex:
            self.logger.error(ex, exc_info=True)
            return False, {
                "success": False,
                "status": "rejected",
                "reason": "INVALID_LIMIT_PRICE_POST_ONLY",
                "trigger": raw_order_data.trigger,
                "source": raw_order_data.source,
                "symbol": raw_order_data.trading_pair,
                "side": raw_order_data.side.upper(),
                "price": str(raw_order_data.adjusted_price),
                "amount": str(raw_order_data.adjusted_size),
                "attempts": 0,
                "message": "Would match immediately",
                "note": "Post-only rule violation",
                "response": None,
            }

    async def handle_order(self, order_data: OrderData, order_book_details: dict) -> tuple[bool, dict]:
        """
        Handles a validated order: adjusts price and size, calculates TP/SL, and attempts order placement.

        Args:
            order_data (OrderData): Fully validated and normalized order details.

        Returns:
            Tuple[bool, dict]: Success flag and the order response (or error).
        """
        try:
            self.logger.debug(f"⚙️ Handling order for {order_data.trading_pair}")

            side = order_data.side.lower()
            type = order_data.type.lower()
            maker_fee = order_data.maker
            taker_fee = order_data.taker

            base_deci = order_data.base_decimal
            quote_deci = order_data.quote_decimal

            # Adjust price and size
            adjusted_price, adjusted_size_of_order_qty = self.shared_utils_precision.adjust_price_and_size(
                {
                    'side': side,
                    'type': type,
                    'maker_fee': maker_fee,
                    'taker_fee': taker_fee,
                    'base_avail_to_trade': order_data.base_avail_balance,
                    'sell_amount': order_data.base_avail_balance,
                    'order_amount_fiat': order_data.order_amount_fiat,
                    'quote_decimal': quote_deci,
                    'base_decimal': base_deci
                },
                {
                    'highest_bid': order_data.highest_bid,
                    'lowest_ask': order_data.lowest_ask
                }
            )

            if adjusted_price is None or adjusted_size_of_order_qty is None:
                return False, {
                    "success": False,
                    "status": "rejected",
                    "reason": "PRICE_OR_SIZE_ADJUSTMENT_FAILED",
                    "symbol": order_data.trading_pair,
                    "side": order_data.side.upper(),
                    "price": str(order_data.adjusted_price),
                    "amount": str(order_data.adjusted_size),
                    "trigger": order_data.trigger,
                    "message": "Failed to adjust price or size",
                    "note": "Adjustment returned None",
                    "source": order_data.source,
                    "response": {}
                }

            # Calculate TP/SL
            take_profit_price = adjusted_price * (1 + self.take_profit)
            stop_loss_price = adjusted_price * (1 + self.stop_loss)

            # Apply precision
            tp_adjusted = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, take_profit_price, convert="quote")
            sl_adjusted = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, stop_loss_price, convert="quote")

            # Update OrderData
            order_data.adjusted_price = adjusted_price
            order_data.adjusted_size = adjusted_size_of_order_qty
            order_data.take_profit_price = tp_adjusted
            order_data.stop_loss_price = sl_adjusted

            # Choose order type
            order_type = self.order_type_to_use(side, order_data)

            self.logger.debug(f"🧠 Order Type: {order_type} | Adjusted Price: {adjusted_price} | Size: {adjusted_size_of_order_qty}")
            return await self.attempt_order_placement(order_data, order_type)

        except Exception as ex:
            self.logger.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False, {
                "success": False,
                "status": "rejected",
                "reason": "HANDLE_ORDER_EXCEPTION",
                "symbol": order_data.trading_pair,
                "side": order_data.side.upper(),
                "price": str(order_data.adjusted_price),
                "amount": str(order_data.adjusted_size),
                "trigger": order_data.trigger,
                "message": str(ex),
                "note": "Exception caught in handle_order",
                "source": order_data.source,
                "response": {}
            }

    async def attempt_order_placement(self, order_data: OrderData, order_type: str, max_attempts: int = 3) -> tuple[bool, dict]:
        symbol = order_data.trading_pair
        side = order_data.side.lower()
        quote_deci = order_data.quote_decimal
        base_deci = order_data.base_decimal

        response = None
        await self.market_data_updater.run_single_refresh_market_data()

        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.debug(f"📤 Attempt #{attempt} to place {order_type} order for {symbol}...")

                # Refresh bid/ask
                order_book = self.bid_ask_spread.get(symbol)
                highest_bid = self.shared_utils_precision.safe_quantize(Decimal(order_book['bid']), Decimal(f"1e-{quote_deci}"))
                lowest_ask = self.shared_utils_precision.safe_quantize(Decimal(order_book['ask']), Decimal(f"1e-{quote_deci}"))
                spread = self.shared_utils_precision.safe_quantize(lowest_ask - highest_bid, Decimal(f"1e-{quote_deci}"))

                order_data.highest_bid = highest_bid
                order_data.lowest_ask = lowest_ask
                order_data.spread = spread

                # Post-only price adjustment
                order_data.adjusted_price = self.get_post_only_price(highest_bid, lowest_ask, order_data.quote_increment, side)
                order_data.adjusted_price = order_data.adjusted_price.quantize(Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP)

                if getattr(order_data, 'post_only', False):
                    if (side == 'buy' and order_data.adjusted_price >= lowest_ask) or \
                            (side == 'sell' and order_data.adjusted_price <= highest_bid):
                        return False, {
                            'success': False,
                            'status': 'rejected',
                            'reason': 'INVALID_LIMIT_PRICE_POST_ONLY',
                            'trigger': order_data.trigger,
                            'source': order_data.source,
                            'symbol': symbol,
                            'side': side.upper(),
                            'price': str(order_data.adjusted_price),
                            'amount': str(order_data.adjusted_size),
                            'attempts': attempt,
                            'message': 'Would match immediately',
                            'note': 'Post-only rule violation',
                        }

                # TP/SL calc
                if order_type in ['tp_sl', 'limit', 'bracket']:
                    tp, sl = await self.profit_manager.calculate_tp_sl(order_data)
                    order_data.take_profit_price, order_data.stop_loss_price = tp, sl

                # Balance check
                if side == 'buy':
                    cost = order_data.adjusted_price * order_data.adjusted_size
                    if cost > order_data.usd_avail_balance:
                        if not self.test_mode:
                            return False, {
                                'success': False,
                                'status': 'rejected',
                                'reason': 'INSUFFICIENT_USD',
                                'trigger': order_data.trigger,
                                'source': order_data.source,
                                'symbol': symbol,
                                'side': side.upper(),
                                'price': str(order_data.adjusted_price),
                                'amount': str(order_data.adjusted_size),
                                'attempts': attempt,
                                'message': f"Need ${cost:.2f}, have ${order_data.usd_avail_balance:.2f}",
                                'note': 'Balance check failed'
                            }

                # Order submission
                if order_type == 'limit':
                    response = await self.order_types.place_limit_order(order_data.source, order_data)
                elif order_type == 'tp_sl':
                    response = await self.order_types.process_limit_and_tp_sl_orders(order_data.source, order_data, tp, sl)
                elif order_type == 'trailing_stop':
                    response = await self.order_types.place_trailing_stop_order(order_book, order_data, highest_bid)
                else:
                    return False, {
                        'success': False,
                        'status': 'rejected',
                        'reason': 'UNKNOWN_ORDER_TYPE',
                        'trigger': order_data.trigger,
                        'source': order_data.source,
                        'symbol': symbol,
                        'side': side.upper(),
                        'message': f"Order type {order_type} not recognized"
                    }

                # Success
                if response.get('success') and response.get('order_id'):
                    order_data.order_id = response.get('order_id')
                    return True, {
                        'success': True,
                        'status': 'placed',
                        'order_id': order_data.order_id,
                        'trigger': order_data.trigger,
                        'source': order_data.source,
                        'symbol': symbol,
                        'side': side.upper(),
                        'price': str(order_data.adjusted_price),
                        'amount': str(order_data.adjusted_size)
                    }

                # Failure — propagate standard failure details
                error_resp = response.get('response', {}).get('error_response', {})
                return False, {
                    'success': False,
                    'status': response.get('status', 'rejected'),
                    'trigger': order_data.trigger,
                    'source': order_data.source,
                    'symbol': symbol,
                    'side': side.upper(),
                    'price': str(order_data.adjusted_price),
                    'amount': str(order_data.adjusted_size),
                    'attempts': attempt,
                    'reason': error_resp.get('error', response.get('reason', 'unknown')),
                    'message': error_resp.get('message', response.get('message', '')),
                    'response': response,
                    'note': f"Failed after {attempt} attempt(s) — check funds, size, or post-only rules"
                }

            except Exception as ex:
                self.logger.error(f"❌ Exception during {symbol} order attempt #{attempt}: {ex}", exc_info=True)
                return False, {
                    'success': False,
                    'status': 'error',
                    'trigger': order_data.trigger,
                    'source': order_data.source,
                    'symbol': symbol,
                    'side': side.upper(),
                    'price': str(order_data.adjusted_price),
                    'amount': str(order_data.adjusted_size),
                    'attempts': attempt,
                    'reason': 'EXCEPTION',
                    'message': str(ex),
                    'note': f"Exception raised on attempt #{attempt}"
                }

        # All attempts exhausted
        return False, {
            'success': False,
            'status': 'failed',
            'trigger': order_data.trigger,
            'source': order_data.source,
            'symbol': symbol,
            'side': side.upper(),
            'price': str(order_data.adjusted_price),
            'amount': str(order_data.adjusted_size),
            'attempts': max_attempts,
            'reason': 'MAX_ATTEMPTS_REACHED',
            'message': "All retry attempts failed.",
            'note': "Order placement failed after retries."
        }

    def order_type_to_use(self, side, order_data):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.

        if order_data.trigger and order_data.trigger.get("trigger") == "passive_buy":
            validation_result = 'limit'
            return validation_result
        if side == 'buy':
            validation_result = 'tp_sl'
            return validation_result
        elif side == 'sell':
            validation_result = 'limit'
            return validation_result

    def get_post_only_price(self, highest_bid, lowest_ask, quote_increment, side):
        adjustment = quote_increment * 2  # or 1 if tighter spacing is acceptable
        if side == 'buy':
            return (lowest_ask - adjustment).quantize(quote_increment, rounding=ROUND_HALF_UP)
        else:
            return (highest_bid + adjustment).quantize(quote_increment, rounding=ROUND_HALF_UP)



