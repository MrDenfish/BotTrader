
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
        return self.shared_data_manager.order_management.get("passive_orders")

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
            side: Optional[str] = None
    ) -> Optional[OrderData]:
        try:
            # Abort if market data is missing or incomplete
            if self.market_data_updater.get_empty_keys(self.market_data):
                self.logger.warning(f"⚠️ Market data incomplete — skipping {asset}")
                return None

            trading_pair = product_id.replace("/", "-")
            spot = self.spot_position.get(asset, {})
            # 🔧 Precision and quantization setup
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(asset)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)
            passive_order_data = self.passive_orders.get(asset, {})
            usd_data = self.spot_position.get("USD", {})
            usd_balance = Decimal(usd_data.get("total_balance_fiat", 0))
            usd_avail = Decimal(usd_data.get("available_to_trade_fiat", 0))
            usd_avail = self.shared_utils_precision.safe_quantize(usd_avail, quote_quantizer)

            min_order_threshold = getattr(self, "min_order_threshold", Decimal("5.00"))

            # 🔍 Handle assets not yet in wallet or passive tracker
            if not spot and not passive_order_data and not side:
                if usd_avail >= min_order_threshold:
                    self.logger.info(f"💡 Proceeding with buy for new asset {asset} — USD available, no wallet entry or passive order.")
                    spot = {}  # Allow downstream logic
                else:
                    self.logger.warning(f"⚠️ Skipping {asset} — no wallet, no passive order, and USD < {min_order_threshold}")
                    return None

            # 🔍 Allow PassiveMM to quote new assets
            if source == "PassiveMM":
                if not passive_order_data:
                    if usd_avail >= min_order_threshold:
                        self.logger.info(f"💡 PassiveMM: initializing first-time quote for {asset} — no passive order data.")
                        passive_order_data = {}
                    else:
                        self.logger.warning(f"⚠️ PassiveMM skipping {asset} — no passive data and insufficient USD.")
                        return None
                self.shared_utils_utility.get_passive_order_data(passive_order_data)



            # 🔧 Balance and available-to-trade values
            total_balance_crypto = Decimal(spot.get("total_balance_crypto", 0))
            available_to_trade = Decimal(spot.get("available_to_trade_crypto", 0))

            bid = Decimal(self.bid_ask_spread.get(trading_pair, {}).get("bid", 0))
            ask = Decimal(self.bid_ask_spread.get(trading_pair, {}).get("ask", 0))
            current_bid = self.shared_utils_precision.safe_quantize(bid, quote_quantizer)
            current_ask = self.shared_utils_precision.safe_quantize(ask, quote_quantizer)
            spread = Decimal(self.bid_ask_spread.get(trading_pair, {}).get("spread", 0))

            price = (current_bid + current_ask) / 2 if (current_bid and current_ask) else Decimal("0")
            if price == 0:
                self.logger.warning(f"⚠️ Price is zero for {trading_pair} — skipping order")
                return None

            # 🔧 Side fallback logic
            if side is None:
                side = "buy" if usd_avail >= self.order_size else "sell"

            # 🔧 Fee setup
            if not self.fee_info:
                maker_fee, taker_fee = self.default_maker_fee, self.default_taker_fee
            else:
                maker_fee = Decimal(self.fee_info.get('fee_rates', {}).get('maker') or self.default_maker_fee)
                taker_fee = Decimal(self.fee_info.get('fee_rates', {}).get('taker') or self.default_taker_fee)

            # 🔧 Determine amount to order
            fiat_amt = min(self.order_size, usd_avail)
            crypto_amt = available_to_trade
            order_amount_fiat, order_amount_crypto = self.shared_utils_utility.initialize_order_amounts(
                side, fiat_amt, crypto_amt
            )

            trigger_note = f"triggered by {trigger}" if isinstance(trigger, str) else trigger.get("trigger_note", "")
            trigger_dict = trigger if isinstance(trigger, dict) else self.build_trigger(trigger, trigger_note)

            # ✅ Construct final OrderData
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
                base_avail_balance=available_to_trade,
                total_balance_crypto=total_balance_crypto,
                available_to_trade_crypto=available_to_trade,
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

    def build_trigger(self,trigger_type: str, note: str = "") -> dict:
        return {
            "trigger": trigger_type.upper(),
            "trigger_note": note
        }


    def build_response(self, success: bool, message: str, code: Union[str, int],
                       details: Optional[dict] = None, error_response: Optional[dict] = None) -> Dict:
        return {
            "success": success,
            "message": message,
            "code": code,
            "details": details or {},
            "error_response": error_response or {}
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

            # print(f' ⚠️ place_order - Order Data: {order_data.debug_summary(verbose=True)}')
            return await self.handle_order(order_data, order_book_details)

        except Exception as ex:
            self.logger.error(ex, exc_info=True)
            return False, self.build_response(str(ex), "500", raw_order_data.__dict__)

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
                return False, self.build_response(f"Failed to adjust price or size for {order_data.trading_pair}", "500", order_data.__dict__)

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

            self.logger.debug(f"� Order Type: {order_type} | Adjusted Price: {adjusted_price} | Size: {adjusted_size_of_order_qty}")
            #print(f' ⚠️ handle_order - Order Data: {order_data.debug_summary(verbose=True)}')

            return await self.attempt_order_placement(order_data, order_type)

        except Exception as ex:
            self.logger.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False, self.build_response(str(ex), "500", order_data.__dict__)

    async def attempt_order_placement(self, order_data: OrderData, order_type: str, max_attempts: int = 3) -> tuple[bool, dict]:
        symbol = order_data.trading_pair
        asset = order_data.base_currency
        quote_deci = order_data.quote_decimal
        base_deci = order_data.base_decimal

        response = None

        # Step 0: Refresh market data
        await self.market_data_updater.run_single_refresh_market_data()

        for attempt in range(max_attempts):
            try:
                self.logger.debug(f"📤 Attempt #{attempt + 1} to place {order_type} order for {symbol}...")

                # Step 1: Refresh order book
                order_book = self.bid_ask_spread.get(symbol)
                highest_bid = Decimal(order_book['bid'])
                lowest_ask = Decimal(order_book['ask'])

                #reformat deci size:
                quote_quantizer = Decimal("1").scaleb(-quote_deci)
                base_quantizer = Decimal("1").scaleb(-base_deci)
                highest_bid = self.shared_utils_precision.safe_quantize(Decimal(highest_bid), quote_quantizer)
                order_data.highest_bid = highest_bid
                lowest_ask = self.shared_utils_precision.safe_quantize(Decimal(lowest_ask), quote_quantizer)
                order_data.lowest_ask = lowest_ask
                spread = self.shared_utils_precision.safe_quantize(Decimal(order_data.spread), quote_quantizer)
                order_data.spread = spread

                # Step 1.1: Adjust price
                side = order_data.side.lower()
                adjusted_price = self.get_post_only_price(highest_bid,
                                                          lowest_ask,
                                                          order_data.quote_increment,
                                                          side)

                order_data.adjusted_price = adjusted_price

                order_data.adjusted_price = adjusted_price.quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_HALF_UP)

                # Step 1.5: Validate post-only logic
                if getattr(order_data, 'post_only', False):
                    if (side == 'buy' and order_data.adjusted_price >= lowest_ask) or \
                            (side == 'sell' and order_data.adjusted_price <= highest_bid):
                        self.logger.warning(f"⚠️ Invalid post-only {side.upper()} price for {symbol}.")
                        return False, self.build_response(
                            success=False, code="422", message="Post-only price violation", details=order_data.__dict__,
                            error_response={"error": "INVALID_LIMIT_PRICE_POST_ONLY", "message": "Would match immediately"}
                        )

                # Step 2: Recalculate TP/SL if applicable
                if order_type in ['tp_sl', 'limit', 'bracket']:
                    tp, sl = await self.profit_manager.calculate_tp_sl(order_data)
                    order_data.take_profit_price, order_data.stop_loss_price = tp, sl

                # Step 3: Balance check
                if side == 'buy':
                    estimated_cost = order_data.adjusted_price * order_data.adjusted_size
                    if estimated_cost > order_data.usd_avail_balance:
                        return False, self.build_response(
                            success=False, code="422", message="Insufficient USD", details=order_data.__dict__,
                            error_response={"error": "INSUFFICIENT_USD",
                                            "message": f"Need ${estimated_cost:.2f}, have ${order_data.usd_avail_balance:.2f}"}
                        )

                # Step 4: Attempt order placement
                if order_type == 'limit':
                    response = await self.order_types.place_limit_order("Webhook", order_data)

                elif order_type == 'tp_sl':
                    response = await self.order_types.process_limit_and_tp_sl_orders("Webhook", order_data, tp, sl)
                elif order_type == 'trailing_stop':
                    response = await self.order_types.place_trailing_stop_order(order_book, order_data, highest_bid)
                else:
                    return False, self.build_response(False, f"Unknown order type: {order_type}", "400", order_data.__dict__)

                # Step 5: Handle known failure format: {'status': 'failed', 'reason': ...}
                if isinstance(response, dict) and response.get('status') == 'failed':
                    reason =  response.get('reason', '')
                    if reason == 'insufficient_balance':
                        return False, self.build_response(
                            success=False, code="422", message="Insufficient balance", details=order_data.__dict__,
                            error_response={"error": "INSUFFICIENT_BALANCE", "message": "Exchange reported insufficient funds"}
                        )
                    # Add more custom 'reason' cases here as needed

                # Step 6: Handle success
                if response.get('success') and response.get('order_id'):
                    success_response = response.get('success_response', {})
                    order_config = response.get('order_configuration', {})

                    order_id = response['order_id']
                    order_data.order_id = order_id
                    order_data.source = response.get('source')
                    if success_response.get('side').lower() == 'buy':
                        order_data.parent_order_id = order_id
                    self.logger.info(f"✅ Successfully placed {order_type} order for {symbol}")
                    return True, self.build_response(True, "Order placed", "200", order_data.__dict__)

                # Step 7: Handle recoverable or known errors
                error_response = response.get('error_response', {})
                error_code = error_response.get('error', '') or response.get('error', '')
                error_msg = error_response.get('message', '') or response.get('message', '')

                if error_code in ['amend', 'Too many decimals', 'INVALID_SIZE_PRECISION']:
                    self.logger.warning(f"⚠️ Fixing precision (Attempt {attempt + 1}/{max_attempts})")
                    adjusted_price, adjusted_size_of_order_qty = self.shared_utils_precision.adjust_price_and_size(order_data.__dict__, order_book)
                    order_data.adjusted_price = self.shared_utils_precision.adjust_precision(order_data.base_decimal, order_data.quote_decimal,
                                                                                             adjusted_price, 'quote')
                    order_data.adjusted_size = adjusted_size_of_order_qty
                    continue

                if 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in error_code:
                    self.logger.warning("⚠️ Stop price too low, adjusting...")
                    order_data.adjusted_price *= Decimal('1.0002')
                    continue

                if 'PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE' in error_code:
                    self.logger.warning("⚠️ Take profit out of bounds.")
                    continue

                if error_code == 'INVALID_LIMIT_PRICE_POST_ONLY' and attempt == max_attempts - 1:
                    self.logger.warning(f"⚠️ Retrying without post-only constraint for {symbol}")
                    order_data.post_only = False
                    continue

                if error_code == 'Insufficient_USD':
                    self.logger.warning(f"⚠️ {error_msg}")
                    order_data.status = 'FAILED'
                    order_data.post_only = False
                    break
                if not error_code and not error_response and error_msg == 'Unknown Error':
                    self.logger.warning(f"⚠️ {response}")
                    print(f'{order_data}')
                    order_data.status = 'FAILED'
                    order_data.post_only = False
                    break

                if attempt == max_attempts - 1:
                    self.logger.warning(f"🛠️ Last attempt: rebuilding OrderData for {symbol}")
                    rebuilt_order_data = await self.build_order_data(
                        order_data.source,
                        order_data.trigger,
                        order_data.base_currency,
                        order_data.trading_pair,
                        None,
                        None
                    )
                    if rebuilt_order_data:
                        changes = [f"{attr}: {getattr(order_data, attr)} ➜ {getattr(rebuilt_order_data, attr)}"
                                   for attr in ['adjusted_price', 'adjusted_size', 'usd_avail_balance', 'limit_price']
                                   if getattr(order_data, attr) != getattr(rebuilt_order_data, attr)]
                        if changes:
                            self.logger.info("🔁 Rebuilt OrderData with changes:\n" + "\n".join(changes))
                        else:
                            self.logger.info("⚠️ Rebuilt OrderData but no changes found.")
                        order_data = rebuilt_order_data
                        continue
                    else:
                        self.logger.error(f"❌ Failed to rebuild OrderData on final attempt for {symbol}")
                        return False, self.build_response(False, "Failed to rebuild order", "500", order_data.__dict__)

                return False, self.build_response(False, error_msg or "Order placement failed", "500", order_data.__dict__,
                                                  error_response=error_response)

            except Exception as ex:
             self.logger.error(f"❌ Exception during attempt #{attempt + 1}: {ex}", exc_info=True)

        self.logger.info(f"❌ All {max_attempts} attempts to place order for {symbol} failed.")
        return False, self.build_response(False, "Order placement failed after retries", "500", order_data.__dict__)

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



