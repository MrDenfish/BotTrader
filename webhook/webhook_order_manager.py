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
                     logger_manager=None, alerts=None, ccxt_api=None, order_book_manager=None, order_types=None, websocket_helper=None,
                     shared_data_manager=None, session=None, profit_manager=None):
        """
        Singleton method to ensure only one instance of TradeOrderManager exists.
        If already instantiated, returns the existing instance.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logger_manager, alerts,
                                ccxt_api, order_book_manager, order_types, websocket_helper, shared_data_manager, session, profit_manager)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logger_manager,
                 alerts, ccxt_api, order_book_manager, order_types, websocket_helper, shared_data_manager, session, profit_manager):
        """
        Initializes the TradeOrderManager.
        """
        self.config = config()
        self.coinbase_api = coinbase_api
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_order_amount = self.config.min_order_amount
        self._min_sell_value = self.config.min_sell_value
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._order_size = self.config.order_size
        self._hodl = self.config.hodl
        self._default_maker_fee = self.config.maker_fee
        self._default_taker_fee = self.config.taker_fee
        self.logger = logger_manager  # 🙂

        self.validate= validate
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self._shared_data_manager = shared_data_manager

        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
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
    def default_taker_fee(self):
        return self._default_taker_fee

    @property
    def shared_data_manager(self):
        return self._shared_data_manager

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def usd_pairs(self):
        return self.market_data.get('usd_pairs_cache')

    @property
    def current_prices(self):
        return self.market_data.get('current_prices')

    @property
    def open_orders(self):
        return self.order_management.get('order_tracker')

    @property
    def avg_quote_volume(self):
        return Decimal(self.market_data['avg_quote_volume'])

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
        return self._min_order_amount

    @property
    def min_sell_amount(self):
        return self._min_sell_amount

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self._max_value_of_crypto_to_buy_more

    @property
    def order_size(self):
        return float(self._order_size)

    async def build_order_data(self, source: str, trigger: str, asset: str, product_id: str, stop_price: Optional[Decimal] = None,
                               fee_info: Optional[dict] = None, order_type: Optional[str] = None) -> Optional[OrderData]:
        try:
            # Fetch data
            volume_24h = 0
            endpoint = 'private'
            params = {'paginate': True, 'paginationCalls': 2}
            spot_position = self.market_data.get('spot_positions', {})
            usd_pairs = self.market_data.get('usd_pairs_cache', {})
            cost_basis = spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0)
            total_balance_crypto = spot_position.get(asset, {}).get('total_balance_crypto', 0)
            # volume_leaders = self.market_data.get('filtered_vol', {})
            for index, row in usd_pairs.iterrows():
                if row["asset"] == asset:
                    volume_24h = row["24h_quote_volume"]
                    break

            if volume_24h is not None:
                print(f"24h volume for {asset}: {volume_24h}")
            else:
                print(f"{asset} not found in usd_pairs.")


            product_id = product_id.replace('-', '/')

            # Open orders
            all_open_orders, has_open_order, _ = await self.websocket_helper.refresh_open_orders(trading_pair=product_id)

            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
            active_open_order = False
            active_open_order_type = None
            active_open_order_side = None

            # Make sure the 'symbol' column and 'info' column exist
            if 'symbol' in open_orders.columns and 'info' in open_orders.columns:
                for _, row in open_orders.iterrows():
                    info = row.get('info', {})
                    symbol = info.get('product_id', '').replace('-', '/')

                    if product_id == symbol:  # `symbol` should be in format 'XXX/USD'
                        active_open_order = True
                        active_open_order_type = row.get('type', 'UNKNOWN')
                        active_open_order_side = row.get('side', 'UNKNOWN')
                        break  # Stop after first match

            # Fetch fees
            if not fee_info:
                fee_info = await self.coinbase_api.get_fee_rates()
            if fee_info.get('error'):
                maker_fee = self.default_maker_fee
                taker_fee = self.default_taker_fee
            else:
                maker_fee = Decimal(fee_info['maker'])
                taker_fee = Decimal(fee_info['taker'])

            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)
            base_quantizer = Decimal("1").scaleb(-base_deci)
            balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0)).quantize(
                Decimal(f'1e-{base_deci}'),
                rounding=ROUND_HALF_UP
            )
            volume_24h = Decimal(volume_24h).quantize(Decimal(1))
            cost_basis = Decimal(cost_basis).quantize(quote_quantizer, rounding=ROUND_HALF_UP)
            total_balance_crypto = Decimal(total_balance_crypto).quantize(base_quantizer, rounding=ROUND_HALF_UP)
            available_to_trade = Decimal(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
            available_to_trade = Decimal(available_to_trade).quantize(base_quantizer, rounding=ROUND_HALF_UP)
            usd_bal = self.shared_utils_precision.safe_decimal(
                spot_position.get("USD", {}).get("total_balance_fiat")).quantize(quote_quantizer, rounding=ROUND_HALF_UP)
            usd_avail = Decimal(
                spot_position.get('USD', {}).get('available_to_trade_fiat', 0)).quantize(quote_quantizer,rounding=ROUND_HALF_UP)
            print(f'‼️ USD Avail: {usd_avail}')
            print(f'‼️ USD Bal: {usd_bal}')
            pair = product_id.replace('-', '/')  # normalize first
            base_currency, quote_currency = pair.split('/')
            trading_pair = product_id.replace('-', '/')
            price = Decimal(self.market_data.get('current_prices', {}).get(trading_pair, 0)).quantize(
                quote_quantizer, rounding=ROUND_HALF_UP
            )

            # Determine side
            side = 'buy' if Decimal(
                spot_position.get(asset, {}).get('total_balance_fiat', 0)
            ) <= self.max_value_of_crypto_to_buy_more else 'sell'

            # Set fiat allocation
            fiat_avail_for_order = Decimal(min(self.order_size, usd_avail)).quantize(quote_quantizer, rounding=ROUND_HALF_UP)

            # Calculate order size
            if side == 'buy':
                size = (fiat_avail_for_order / price).quantize(base_quantizer) if price > 0 else Decimal(0)
                if (price * size) < self.min_order_amount:
                    print(f'‼️ Insufficient fiat balance to place buy order: {trading_pair}')
                    return None

            else:
                size = available_to_trade

            # Fetch order book
            temp_data = {'quote_decimal': quote_deci, 'base_decimal': base_deci, 'trading_pair': trading_pair}
            temp_data = OrderData.from_dict(temp_data)
            order_book = await self.order_book_manager.get_order_book(temp_data, trading_pair)
            if trigger == 'ROC':
                type = 'limit'
            temp_order = {
                'side': side,
                'type': None,
                'maker_fee': maker_fee,
                'taker_fee': taker_fee,
                'usd_avail_balance': usd_avail,
                'usd_balance': usd_bal,
                'base_avail_to_trade': balance,
                'sell_amount': size if side == 'sell' else Decimal(0),
                'buy_amount': size if side == 'buy' else Decimal(0),
                'quote_decimal': quote_deci,
                'base_decimal': base_deci
            }
            temp_book = {
                'highest_bid': float(order_book.get('highest_bid', 0)),
                'lowest_ask': float(order_book.get('lowest_ask', 0))
            }

            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book)
            adjusted_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_price, 'quote')
            adjusted_size = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, size, 'base')
            fiat_avail_for_order = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, fiat_avail_for_order, 'quote')
            usd_bal = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, usd_bal, 'quote')
            usd_avail = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, usd_avail, 'quote')
            spread = Decimal(order_book.get('spread'))
            return OrderData(
                trading_pair=trading_pair,
                time_order_placed=None,
                type=order_type,
                order_id='UNKNOWN',
                side=side,
                order_amount=fiat_avail_for_order,
                filled_price=None,
                base_currency=asset,
                quote_currency=quote_currency,
                usd_avail_balance=usd_avail,
                usd_balance=usd_bal,
                base_avail_balance=balance,
                total_balance_crypto=total_balance_crypto,
                available_to_trade_crypto=available_to_trade,
                base_decimal=base_deci,
                quote_decimal=quote_deci,
                quote_increment=Decimal("1") / (Decimal("10") ** quote_deci),
                highest_bid=Decimal(temp_book['highest_bid']).quantize(quote_quantizer),
                lowest_ask=Decimal(temp_book['lowest_ask']).quantize(quote_quantizer),
                maker=maker_fee,
                taker=taker_fee,
                spread=spread,
                open_orders={'open_order': active_open_order, 'type': active_open_order_type, 'side': active_open_order_side},
                status='UNKNOWN',
                source=source,
                trigger=trigger,
                price=price,
                cost_basis=cost_basis,
                limit_price=adjusted_price,
                average_price=None,
                adjusted_price=adjusted_price,
                adjusted_size=adjusted_size,
                stop_loss_price=stop_price,
                take_profit_price=None,
                volume_24h=volume_24h,

            )

        except Exception as e:
            self.logger.error(f"Error in build_order_data: {e}", exc_info=True)
            return None

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

            all_open_orders, has_open_order, _ = await self.websocket_helper.refresh_open_orders(
                trading_pair=raw_order_data.trading_pair
            )
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
            order_book_details = await self.order_book_manager.get_order_book(raw_order_data)

            if len(order_book_details) == 0:
                pass
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
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(
                {
                    'side': side,
                    'type': type,
                    'maker_fee': maker_fee,
                    'taker_fee': taker_fee,
                    'base_avail_to_trade': order_data.available_to_trade_crypto,
                    'sell_amount': order_data.available_to_trade_crypto,
                    'order_amount': order_data.order_amount,
                    'quote_decimal': quote_deci,
                    'base_decimal': base_deci
                },
                {
                    'highest_bid': order_data.highest_bid,
                    'lowest_ask': order_data.lowest_ask
                }
            )
            if adjusted_price is None or adjusted_size is None:
                return False, self.build_response(f"Failed to adjust price or size for {order_data.trading_pair}", "500", order_data.__dict__)

            # Calculate TP/SL
            take_profit_price = adjusted_price * (1 + self.take_profit)
            stop_loss_price = adjusted_price * (1 + self.stop_loss)

            # Apply precision
            tp_adjusted = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, take_profit_price, convert="quote")
            sl_adjusted = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, stop_loss_price, convert="quote")

            # Update OrderData
            order_data.adjusted_price = adjusted_price
            order_data.adjusted_size = adjusted_size
            order_data.take_profit_price = tp_adjusted
            order_data.stop_loss_price = sl_adjusted

            # Choose order type
            order_type = self.order_type_to_use(side, order_data.adjusted_price, order_data.highest_bid, order_data.lowest_ask)

            self.logger.debug(f"� Order Type: {order_type} | Adjusted Price: {adjusted_price} | Size: {adjusted_size}")
            #print(f' ⚠️ handle_order - Order Data: {order_data.debug_summary(verbose=True)}')

            return await self.attempt_order_placement(order_data, order_type)

        except Exception as ex:
            self.logger.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False, self.build_response(str(ex), "500", order_data.__dict__)

    def order_type_to_use(self, side, adjusted_price, highest_bid, lowest_ask):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.
        validation_result = 'limit'

        if side == 'buy':
            validation_result = 'tp_sl'
            return validation_result
        elif side == 'sell':
            validation_result = 'limit'
            return validation_result

    async def attempt_order_placement(self, order_data: OrderData, order_type: str, max_attempts: int = 3) -> tuple[bool, dict]:
        symbol = order_data.trading_pair
        asset = order_data.base_currency

        for attempt in range(max_attempts):
            try:
                self.logger.debug(f"📤 Attempt #{attempt + 1} to place {order_type} order for {symbol}...")

                # Step 1: Refresh order book
                order_book = await self.order_book_manager.get_order_book(order_data, symbol)
                highest_bid = Decimal(order_book['highest_bid'])
                lowest_ask = Decimal(order_book['lowest_ask'])

                # Step 1.1: Adjust price
                adjustment = Decimal('0.0001')
                side = order_data.side.lower()
                price = min(highest_bid, lowest_ask - adjustment) if side == 'buy' else max(highest_bid, lowest_ask + adjustment)
                order_data.adjusted_price = price.quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_HALF_UP)

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
                    reason = response.get('reason', '')
                    if reason == 'insufficient_balance':
                        return False, self.build_response(
                            success=False, code="422", message="Insufficient balance", details=order_data.__dict__,
                            error_response={"error": "INSUFFICIENT_BALANCE", "message": "Exchange reported insufficient funds"}
                        )
                    # Add more custom 'reason' cases here as needed

                # Step 6: Handle success
                if response.get('success') and response.get('success_response', {}).get('order_id'):
                    order_id = response['success_response']['order_id']
                    order_data.order_id = order_id
                    order_data.parent_order_id = order_id
                    self.logger.info(f"✅ Successfully placed {order_type} order for {symbol}")
                    return True, self.build_response(True, "Order placed", "200", order_data.__dict__)

                # Step 7: Handle recoverable or known errors
                error_response = response.get('error_response', {})
                error_code = error_response.get('error', '') or response.get('error', '')
                error_msg = error_response.get('message', '') or response.get('message', '')

                if error_code in ['amend', 'Too many decimals', 'INVALID_SIZE_PRECISION']:
                    self.logger.warning(f"⚠️ Fixing precision (Attempt {attempt + 1}/{max_attempts})")
                    adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data.__dict__, order_book)
                    order_data.adjusted_price = self.shared_utils_precision.adjust_precision(order_data.base_decimal, order_data.quote_decimal,
                                                                                             adjusted_price, 'quote')
                    order_data.adjusted_size = adjusted_size
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

                if attempt == max_attempts - 1:
                    self.logger.warning(f"🛠️ Last attempt: rebuilding OrderData for {symbol}")
                    rebuilt_order_data = await self.trade_order_manager.build_order_data(
                        order_data.source, order_data.trigger, order_data.base_currency, order_data.trading_pair, None, None
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

