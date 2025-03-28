
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
                     logmanager=None, alerts=None, ccxt_api=None, order_book_manager=None, order_types=None, websocket_helper=None,
                     session=None, market_data=None, profit_manager=None):
        """
        Singleton method to ensure only one instance of TradeOrderManager exists.
        If already instantiated, returns the existing instance.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logmanager, alerts,
                                ccxt_api, order_book_manager, order_types, websocket_helper, session, market_data, profit_manager)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logmanager,
                 alerts, ccxt_api, order_book_manager, order_types, websocket_helper, session, market_data, profit_manager):
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
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.validate= validate
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.market_data = market_data
        self.usd_pairs = self.market_data.get('usd_pairs_cache', {})  # usd pairs
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

    async def build_order_data(self, source: str, asset: str, product_id: str) -> Optional[OrderData]:
        try:
            # Fetch data
            if asset == 'USD':  # debug
                pass
            endpoint = 'private'
            params = {'paginate': True, 'paginationCalls': 2}
            spot_position = self.market_data.get('spot_positions', {})
            usd_pairs = self.market_data.get('usd_pairs_cache', {})
            cost_basis = spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0)
            total_balance_crypto = spot_position.get(asset, {}).get('total_balance_crypto', 0)

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
                        active_open_order_type = row.get('type', 'UNKNOWN')
                        active_open_order_side = row.get('side', 'UNKNOWN')
                        break  # Stop after first match

            # Fetch fees
            fee_info = await self.coinbase_api.get_fee_rates()
            maker_fee = Decimal(fee_info['maker_fee'])
            taker_fee = Decimal(fee_info['taker_fee'])

            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)

            balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0)).quantize(
                Decimal(f'1e-{base_deci}'),
                rounding=ROUND_HALF_UP
            )
            cost_basis = Decimal(cost_basis).quantize(Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP)
            total_balance_crypto = Decimal(total_balance_crypto).quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_HALF_UP)
            available_to_trade = Decimal(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
            usd_bal = Decimal(spot_position.get('USD', {}).get('total_balance_fiat', 0)).quantize(
                Decimal(f'1e-{quote_deci}'),
                rounding=ROUND_HALF_UP
            )
            usd_avail = Decimal(spot_position.get('USD', {}).get('available_to_trade_fiat', 0)).quantize(Decimal(f'1e-{quote_deci}'),
                                                                                                         rounding=ROUND_HALF_UP)

            pair = product_id.replace('-', '/')  # normalize first
            base_currency, quote_currency = pair.split('/')
            trading_pair = product_id.replace('-', '/')
            price = Decimal(self.market_data.get('current_prices', {}).get(trading_pair, 0)).quantize(
                Decimal(f'1e-{base_deci}'),
                rounding=ROUND_HALF_UP
            )

            # Determine side
            side = 'buy' if Decimal(
                spot_position.get(asset, {}).get('total_balance_fiat', 0)
            ) <= self.max_value_of_crypto_to_buy_more else 'sell'

            # Set fiat allocation
            fiat_avail_for_order = Decimal(min(self.order_size, usd_avail)).quantize(Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP)

            # Calculate order size
            if side == 'buy':
                size = (fiat_avail_for_order / price).quantize(Decimal(f'1e-{base_deci}')) if price > 0 else Decimal(0)
            else:
                size = available_to_trade

            # Fetch order book
            temp_data = {'quote_decimal': quote_deci, 'base_decimal': base_deci, 'trading_pair': trading_pair}
            temp_data = OrderData.from_dict(temp_data)
            order_book = await self.order_book_manager.get_order_book(temp_data, trading_pair)

            temp_order = {
                'side': side,
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

            return OrderData(
                trading_pair=trading_pair,
                type=None,
                price=price,
                order_id=None,
                side=side,
                order_amount=fiat_avail_for_order,
                base_currency=asset,
                quote_currency=quote_currency,
                usd_avail_balance=usd_avail,
                usd_balance=usd_bal,
                cost_basis=cost_basis,
                base_avail_balance=balance,
                available_to_trade_crypto=available_to_trade,
                total_balance_crypto=total_balance_crypto,
                base_decimal=base_deci,
                quote_decimal=quote_deci,
                highest_bid=Decimal(temp_book['highest_bid']).quantize(Decimal(f'1e-{quote_deci}')),
                lowest_ask=Decimal(temp_book['lowest_ask']).quantize(Decimal(f'1e-{quote_deci}')),
                spread=Decimal(order_book.get('spread', 0)),
                open_orders={'open_order': active_open_order, 'type': active_open_order_type, 'side': active_open_order_side},
                limit_price=adjusted_price,
                filled_price=None,
                maker_fee=maker_fee,
                taker_fee=taker_fee,
                adjusted_price=adjusted_price,
                adjusted_size=adjusted_size,
                stop_loss_price=None,
                take_profit_price=None
            )

        except Exception as e:
            self.log_manager.error(f"Error in build_order_data: {e}", exc_info=True)
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
                precision_data = self.shared_utils_precision.fetch_precision(raw_order_data.trading_pair, self.usd_pairs)
                base_deci, quote_deci, _, _ = precision_data
                raw_order_data.base_decimal = base_deci
                raw_order_data.quote_decimal = quote_deci

            # Step 1: Light validation
            validation_result = self.validate.validate_order_conditions(raw_order_data, open_orders)
            if not validation_result["is_valid"] or has_open_order:
                return False, validation_result

            # Step 2: Get order book
            order_book_details = await self.order_book_manager.get_order_book(raw_order_data)

            # Step 3: Full validation
            validation_result = self.validate.fetch_and_validate_rules(raw_order_data)
            if not validation_result["is_valid"]:
                return False, validation_result

            # Step 4: Construct final OrderData
            order_data = self.validate.build_order_data_from_validation_result(
                validation_result, order_book_details, precision_data
            )

            print(f' ⚠️ place_order - Order Data: {order_data.debug_summary(verbose=True)}')
            return await self.handle_order(order_data)

        except Exception as ex:
            self.log_manager.error(ex, exc_info=True)
            return False, self.build_response(str(ex), "500", raw_order_data.__dict__)

    async def handle_order(self, order_data: OrderData) -> tuple[bool, dict]:
        """
        Handles a validated order: adjusts price and size, calculates TP/SL, and attempts order placement.

        Args:
            order_data (OrderData): Fully validated and normalized order details.

        Returns:
            Tuple[bool, dict]: Success flag and the order response (or error).
        """
        try:
            self.log_manager.info(f"⚙️ Handling order for {order_data.trading_pair}")

            side = order_data.side.lower()
            base_deci = order_data.base_decimal
            quote_deci = order_data.quote_decimal

            # Adjust price and size
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(
                {
                    'side': side,
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

            self.log_manager.info(f"� Order Type: {order_type} | Adjusted Price: {adjusted_price} | Size: {adjusted_size}")
            print(f' ⚠️ handle_order - Order Data: {order_data.debug_summary(verbose=True)}')

            return await self.attempt_order_placement(order_data, order_type)

        except Exception as ex:
            self.log_manager.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
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

    async def attempt_order_placement(self, order_data: OrderData, order_type: str) -> tuple[bool, dict]:
        """
        Attempts to place an order (limit, tp_sl, bracket, trailing_stop) with retry logic.

        Args:
            order_data (OrderData): Fully validated order data.
            order_type (str): The type of order to place.

        Returns:
            Tuple[bool, dict]: Tuple of (success status, response data or error).
        """
        max_attempts = 5
        symbol = order_data.trading_pair

        for attempt in range(max_attempts):
            try:
                self.log_manager.debug(f"� Attempt #{attempt + 1} to place {order_type} order for {symbol}...")

                # Refresh order book
                order_book = await self.order_book_manager.get_order_book(order_data, symbol)
                highest_bid = Decimal(order_book['highest_bid'])
                lowest_ask = Decimal(order_book['lowest_ask'])

                # Adjust price

                adjustment = Decimal('0.0001')
                side = order_data.side.lower()
                if side == 'buy':
                    price = min(highest_bid, lowest_ask - adjustment)
                else:
                    price = max(highest_bid, lowest_ask + adjustment)
                order_data.adjusted_price = price.quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_HALF_UP)

                # Recalculate TP/SL if needed
                if order_type in ['tp_sl', 'bracket']:
                    tp, sl = await self.profit_manager.calculate_tp_sl(
                        order_data.adjusted_price, order_data.base_decimal, order_data.quote_decimal
                    )
                    order_data.take_profit_price = tp
                    order_data.stop_loss_price = sl

                # Submit order
                if order_type == 'limit':
                    response = await self.order_types.place_limit_order("Webhook", order_data)
                elif order_type == 'tp_sl':
                    response = await self.order_types.process_limit_and_tp_sl_orders("Webhook", order_data, take_profit=tp, stop_loss=sl)
                elif order_type == 'bracket':
                    return False, self.build_response("Bracket order not supported", "417", order_data.__dict__)
                elif order_type == 'trailing_stop':
                    response = await self.order_types.place_trailing_stop_order(order_book, order_data, highest_bid)
                else:
                    return False, self.build_response(f"Unknown order type: {order_type}", "400", order_data.__dict__)

                print(f' ⚠️ attempt_order_placement - Order Data: {order_data.debug_summary(verbose=True)}')  # Debug

                # Handle success
                if response is not None:
                    if response.get('success_response', {}).get('order_id') or response.get('id') is not None:
                        self.log_manager.info(f"✅ Successfully placed {order_type} order for {symbol}")
                        return True, self.build_response(
                            success=True,
                            message="Order placed",
                            code="200",
                            details=order_data.__dict__
                        )

                    # Handle known errors
                    code = response.get('code', '')
                    error_response_preview = response.get('error_response', {}).get('preview_failure_reason', '')
                    error_response_msg = response.get('error_response', {}).get('message', '')
                    error_response_error = response.get('error') if response.get('error') == 'open_order' else response.get('error_response',
                                                                                                                            {}).get('error', '')
                    error_response_details = response.get('error_response', {}).get('details', '')
                    order_config = response.get('order_configuration', {}).get('limiit_limit_gtc')
                    if not error_response_msg and not error_response_details and error_response_preview:
                        if 'PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE' in error_response_preview:
                            continue
                    # � Retry-able errors
                    if error_response_error in ['amend', 'Too many decimals']:
                        self.log_manager.warning(f"⚠️ Order amendment required (Attempt {attempt + 1}/{max_attempts})")
                        adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data.__dict__, order_book)
                        order_data.adjusted_price = self.shared_utils_precision.adjust_precision(
                            order_data.base_decimal, order_data.quote_decimal, adjusted_price, convert='quote'
                        )
                        order_data.adjusted_size = adjusted_size
                        continue  # retry

                    if 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in error_response_error:
                        self.log_manager.warning(f"⚠️ Stop price too low, adjusting (Attempt {attempt + 1}/{max_attempts})")
                        order_data.adjusted_price *= Decimal('1.0002')
                        continue

                    if 'PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE_OUT_OF_BOUNDS' in error_response_error:
                        self.log_manager.warning("⚠️ Take profit out of bounds.")
                        continue

                    # ❌ Non-retryable errors
                    if code in ['401', '413', '414', '415', '416', '417']:
                        return False, self.build_response(error_response_error, code, order_data.__dict__,
                                                          error_response=response.get('error_response'))

                    if 'PREVIEW_INVALID_LIMIT_PRICE' in error_response_error or 'PREVIEW_INVALID_ORDER_CONFIG' in error_response_msg:
                        return False, self.build_response(error_response_msg, "420", order_data.__dict__,
                                                          error_response=response.get('error_response'))

                    if 'PREVIEW_INVALID_ATTACHED_STOP_LOSS_PRICE_OUT_OF_BOUNDS' in error_response_error:
                        return False, self.build_response(error_response_msg, "421", order_data.__dict__,
                                                          error_response=response.get('error_response'))

                    if 'INVALID_PRICE_PRECISION' in error_response_error:
                        return False, self.build_response(error_response_msg, "422", order_data.__dict__,
                                                          error_response=response.get('error_response'))

                    if 'INSUFFICIENT_FUND' in error_response_error:
                        return False, self.build_response(error_response_msg, "422", order_data.__dict__,
                                                          error_response=response.get('error_response'))

                # ❓ Unknown error
                self.log_manager.error(f"❌ Unexpected response: {response}", exc_info=True)
                return False, self.build_response(
                    "Unexpected response from exchange", "500", order_data.__dict__, error_response=response.get('error_response')
                )

            except Exception as ex:
                self.log_manager.error(f"⚠️ Error during attempt #{attempt + 1}: {ex}", exc_info=True)

        self.log_manager.info(f"❌ Order placement failed after {max_attempts} attempts for {symbol}.")
        return False, self.build_response("Order placement failed after retries", "500", order_data.__dict__)

    # async def place_order(self, order_details, precision_data=None):
    #     try:
    #         self.shared_utils_utility.log_event_loop("place_order")  # debug
    #         all_open_orders, has_open_order,_ = await self.websocket_helper.refresh_open_orders(trading_pair=order_details['trading_pair'])
    #         open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
    #         if not precision_data:
    #             precision_data = self.shared_utils_precision.fetch_precision(order_details['trading_pair'],self.usd_pairs)
    #             base_deci, quote_deci, _, _ = precision_data
    #         # if order conditions are met True else False
    #         validation_result = self.validate.validate_order_conditions(order_details, open_orders) # 🔆
    #
    #         if not validation_result["is_valid"] or has_open_order: # in valid orders get rejected.
    #             return False, validation_result
    #
    #         order_book_details = await self.order_book_manager.get_order_book(order_details)
    #         validate_data = self.validate.build_validate_data(order_details, open_orders, order_book_details) # 🔆
    #
    #         validation_result = self.validate.fetch_and_validate_rules(validate_data) # 🔆
    #
    #         if not validation_result["is_valid"]:
    #             return False, validation_result
    #         event_type = 'webhook'
    #         return await self.handle_order(validation_result, order_book_details, precision_data)
    #
    #
    #     except Exception as ex:
    #         self.log_manager.error(ex, exc_info=True)
    #         return [False,response_msg]

    # async def handle_order(self, valid_order, order_book_details, precision_data):
    #     try:
    #         # Extract key data
    #         print(f'(validation_result) {valid_order}')
    #         trading_pair = valid_order.get('details',{}).get("trading_pair", "")  # Format to match build_order_data
    #         asset = trading_pair.split("/")[0]
    #         quote = trading_pair.split("/")[1]
    #         product_id = trading_pair.replace("/", "-")
    #         # ✅ Ensure critical fields are present
    #         highest_bid = Decimal(order_book_details['highest_bid'])
    #         lowest_ask = Decimal(order_book_details['lowest_ask'])
    #         spread = Decimal(order_book_details['spread'])
    #         base_deci, quote_deci, _, _ = precision_data
    #         # Temporary order details for price/size adjustments
    #         temp_valid_order = {
    #             'side': valid_order.get("details", {}).get("side"),
    #             'base_avail_to_trade': valid_order.get("details", {}).get('base_avail_to_trade'),  # was base_balance
    #             'sell_amount': valid_order.get("details", {}).get('sell_amount'),  # order size in crypto
    #             'order_amount': valid_order.get("details", {}).get('order_amount'),  # order size in fiat
    #             'quote_decimal': valid_order.get("details", {}).get('quote_decimal'),
    #             'base_decimal': valid_order.get("details", {}).get('base_decimal')
    #         }
    #         # ✅ Calculate adjusted price
    #         adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_valid_order, order_book_details)
    #
    #         # ✅ Calculate take profit and stop loss prices
    #         take_profit_price = adjusted_price * (1 + self.take_profit)
    #         adjusted_take_profit_price = self.shared_utils_precision.adjust_precision(
    #             base_deci, quote_deci, take_profit_price, convert='quote'
    #         )
    #
    #         stop_loss_price = adjusted_price * (1 + self.stop_loss)
    #         adjusted_stop_loss_price = self.shared_utils_precision.adjust_precision(
    #             base_deci, quote_deci, stop_loss_price, convert='quote'
    #         )
    #
    #         # ✅ Update `validation_result` directly (instead of `order_data`)
    #         valid_order.update(
    #             {
    #                 'trading_pair':trading_pair,
    #                 'lowest_ask': lowest_ask,
    #                 'highest_bid': highest_bid,
    #                 'adjusted_price': adjusted_price,
    #                 'adjusted_size': adjusted_size,
    #                 'stop_loss_price': adjusted_stop_loss_price,
    #                 'take_profit_price': adjusted_take_profit_price,
    #                 'usd_avail_balance': valid_order.get('details',{}).get('usd_avail_balance', 0),
    #                 'quote_decimal': quote_deci
    #             }
    #         )
    #
    #         print(f"✅ Final Validate Data: {valid_order}")
    #         side = valid_order.get('details',{}).get('side', None)
    #         # ✅ Decide order type and place order
    #         order_type = self.order_type_to_use(side, adjusted_price, highest_bid, lowest_ask)
    #         response = await self.attempt_order_placement(valid_order, order_type=order_type)
    #
    #         return response
    #
    #
    #     except Exception as ex:
    #         self.log_manager.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
    #         return False

    # # async def attempt_order_placement(self, valid_order, order_type):
    # #     """
    # #     Attempts to place an order (limit, bracket, trailing stop) with retry logic.
    # #
    # #     Args:
    # #         valid_order (dict): Order details.
    # #         order_type (str): Type of order ('limit', 'tp_sl', 'bracket', 'trailing_stop').
    # #
    # #     Returns:
    # #         tuple: (bool, response) where bool indicates success, and response contains the order result or error.
    # #     """
    # #     caller_function_name = stack()[1].function  # Debug
    # #     response = None
    # #     max_attempts = 5
    # #
    # #     for attempt in range(max_attempts):
    # #         try:
    # #             # ✅ Refresh Order Book
    # #             order_book = await self.order_book_manager.get_order_book(valid_order)
    # #             highest_bid, lowest_ask = Decimal(order_book['highest_bid']), Decimal(order_book['lowest_ask'])
    # #
    # #             # ✅ Adjust Price to Avoid Rejections
    # #             order_price = (
    # #                 min(highest_bid, lowest_ask - Decimal('0.0001'))
    # #                 if valid_order.get('details',{}).get('side').lower() == 'buy'
    # #
    # #                 else max(highest_bid, lowest_ask + Decimal('0.0001'))
    # #             )
    # #             valid_order['adjusted_price'] = order_price
    # #
    # #             # ✅ Calculate TP & SL if needed
    # #             if order_type in ['tp_sl', 'bracket']:
    # #                 base_deci, quote_deci = valid_order.get('details',{}).get('base_decimal'), valid_order.get('details',{}).get('quote_decimal')
    # #                 tp, sl = await self.profit_manager.calculate_tp_sl(order_price, base_deci, quote_deci)
    # #                 valid_order.update({'take_profit_price': tp, 'stop_loss_price': sl})
    # #
    # #             # ✅ Execute Order Based on Type
    # #             if order_type == 'limit':
    # #                 #response = attempt_order_placement(valid_order, order_type=order_type)
    # #                 response = await self.order_types.process_limit_and_tp_sl_orders("Webhook", valid_order)
    # #             elif order_type == 'tp_sl':
    # #                 response  = await self.order_types.process_limit_and_tp_sl_orders(
    # #                     "Webhook", valid_order, take_profit=tp, stop_loss=sl
    # #                     )
    # #             elif order_type == 'bracket':
    # #                 print(f"‼️ BRACKET ORDER METHOD NOT IMPLEMENTED YET")
    # #                 #response = await self.order_types.place_bracket_order(order_book, validation_result, highest_bid)
    # #             elif order_type == 'trailing_stop':
    # #                 response = await self.order_types.place_trailing_stop_order(order_book, valid_order, highest_bid)
    # #             else:
    # #                 raise ValueError(f"Unknown order type: {order_type}")
    # #
    # #             # ✅ Handle API Response
    # #             if not response:
    # #                 return False, response
    # #
    # #             # 401-‘open_order’
    # #             # 402-‘price_adjustement_failed’
    # #             # 403_’Insufficiant USD’
    # #             # 404- ‘Insufficient Crypto’
    # #             # 405-‘Crypto Balance value greater than $1.00
    # #             # 406-‘Crypto balance is 0 or missing
    # #             # 407-‘Invalid order’
    # #
    # #             if isinstance(response, dict):
    # #                 code=response.get('code')
    # #                 if not response.get('error_response', {}):
    # #                     error_response = response.get('error', {})
    # #                     message = response.get('message', {})
    # #                 else:
    # #                     error_response = response.get('error_response', {})
    # #                 order_id = response.get('success_response', {}).get('order_id')
    # #                 symbol = valid_order['trading_pair']
    # #                 if order_id:
    # #                     self.log_manager.info(f"✅ Successfully placed {order_type} order for {symbol}.")
    # #                     return True, response
    # #                 # ✅ Handle Different Error Cases
    # #                 elif code == '401':
    # #                     self.log_manager.info(f"⚠️ Open order exists. {response.get('condition')}")
    # #                     return False, response
    # #                 if code =='414':
    # #                     self.log_manager.info(f"⚠️ Insufficient crypto to sell. {response.get('details')}")
    # #                     return False, response
    # #                 elif (code == '413' or 'INSUFFICIENT_FUND' in response.get('error_response').get('error')):
    # #                     self.log_manager.info(f"⚠️ Insufficient USD for BUY order. {response.get('details')}")
    # #                     return False, response
    # #                 elif error_response in ['amend', 'Too many decimals']:
    # #                     self.log_manager.info(f"⚠️ Order amendment required (Attempt {attempt + 1}/{max_attempts}). Adjusting order...")
    # #                     adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(valid_order, order_book)
    # #                     valid_order.update(
    # #                         {
    # #                             'adjusted_price': self.shared_utils_precision.adjust_precision(
    # #                                 valid_order['base_decimal'], valid_order['quote_decimal'], adjusted_price, convert='quote'
    # #                                 ),
    # #                             'adjusted_size': adjusted_size
    # #                         }
    # #                     )
    # #                     continue  # Retry
    # #                 elif 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in message:
    # #                     self.log_manager.info(f"⚠️ Stop price below last trade price {symbol}: "
    # #                                           f"Adjusting order (Attempt {attempt + 1}{max_attempts})...")
    # #                     valid_order['adjusted_price'] *= Decimal('1.0002')  # Slight buffer
    # #                     continue  # Retry
    # #                 elif 'PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE_OUT_OF_BOUNDS' in message:
    # #                     self.log_manager.info(f"⚠️ Take profit price out of bounds for {symbol}. "
    # #                                           f"Adjusting order (Attempt {attempt + 1}/{max_attempts})...")
    # #                     continue  # Retry
    # #
    # #                 elif 'PREVIEW_INVALID_ORDER_CONFIG' in message:
    # #                     self.log_manager.info(f"⚠️ Invalid order config. {response.get('details')}")
    # #                     return False, response
    # #                 elif 'PREVIEW_INVALID_LIMIT_PRICE' in message:
    # #                     self.log_manager.info(f"⚠️ Invalid limit price for  {symbol}")
    # #                     response.get('order_configuration',{}).get('limit_limit_gtc',{}).get('limit_price')
    # #                     return False, response
    # #                 elif 'PREVIEW_INVALID_ATTACHED_STOP_LOSS_PRICE_OUT_OF_BOUNDS' in message:
    # #                     self.log_manager.info(f"⚠️ Stop loss price out of bounds. {symbol}")
    # #                     return False, response
    # #                 elif response.get('success_response', {}).get('order_id'):
    # #                     self.log_manager.info(f"✅ {valid_order['trading_pair']} Order Executed at {valid_order['adjusted_price']}")
    # #                     return True, response
    # #                 else:
    # #                     self.log_manager.error(f"❌ Unexpected response format: {response}", exc_info=True)
    # #                     return False, response
    # #
    # #         except Exception as ex:
    # #             self.log_manager.error(f"⚠️ Error during attempt #{attempt + 1}: {ex}", exc_info=True)
    # #             if attempt >= max_attempts - 1:
    # #                 break  # Stop retrying if max attempts reached
    #
    #     self.log_manager.info(f"❌ Order placement failed after {max_attempts} attempts.")
    #     return False, response

    # async def build_order_data(self, source,asset, product_id):
    #     """
    #     Constructs order data for placing a limit order for websockets.
    #
    #     Args:
    #         asset (str): Base asset (e.g., "BTC").
    #         product_id (str): Trading pair in the format "BASE-QUOTE" (e.g., "BTC-USD").
    #
    #     Returns:
    #         dict: Prepared order data for further processing.
    #     """
    #     try:
    #         # Fetch market data
    #         endpoint = 'private'
    #         params = {'paginate': True, 'paginationCalls': 2}
    #         spot_position = self.market_data.get('spot_positions', {})
    #         usd_pairs = self.market_data.get('usd_pairs_cache', {})
    #         # fetch current fee tier values
    #         fee_info = await self.coinbase_api.get_fee_rates()
    #         maker_fee = Decimal(fee_info['maker_fee'])
    #         taker_fee = Decimal(fee_info['taker_fee'])
    #
    #         # Fetch precision details
    #         base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)
    #
    #         # Get available balance
    #         balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
    #         balance = balance.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_HALF_UP)
    #
    #         cryto_avail_to_trade = spot_position.get(asset,{}).get('available_to_trade_crypto',0)
    #         crypto_value_usd = spot_position.get(asset,{}).get('total_balance_fiat',0)
    #         if crypto_value_usd <= self.max_value_of_crypto_to_buy_more:
    #             side='buy'
    #         else:
    #             side='sell'
    #
    #         usd_bal = float(spot_position.get('USD', {}).get('available_to_trade_fiat', 0))
    #
    #         # Convert trading pair format
    #         trading_pair = product_id.replace('-', '/')
    #
    #         # Get latest price and calculate potential order size
    #         price = float(self.market_data.get('current_prices', {}).get(trading_pair, 0))
    #         if usd_bal > self.order_size:
    #             fiat_avail_for_order = self.order_size
    #             usd_avail = usd_bal-self.order_size
    #         else:
    #             fiat_avail_for_order = usd_bal
    #             usd_avail = fiat_avail_for_order - float(self.min_order_amount)
    #         if side == 'buy':
    #             buy_amount = round(fiat_avail_for_order / price, 8) if price > 0 else 0  # Prevent division by zero
    #             sell_amount =0
    #
    #         elif side == 'sell':
    #             sell_amount = float(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
    #             buy_amount =0
    #         else:
    #             buy_amount = 0
    #             sell_amount = 0
    #
    #
    #         # Prepare initial order data
    #         if side == 'buy':
    #             size = buy_amount
    #         elif side == 'sell':
    #             size = sell_amount
    #         else:
    #             size = 0
    #
    #
    #         order_data = {
    #             'quote_decimal': quote_deci,
    #             'base_decimal': base_deci,
    #             'trading_pair': trading_pair,
    #         }
    #
    #         # Fetch order book data
    #         order_book = await self.order_book_manager.get_order_book(order_data)
    #
    #         # Temporary order details for price/size adjustments
    #         temp_order = {
    #             'side': side,
    #             'base_avail_to_trade': balance, # was base_balance
    #             'sell_amount':sell_amount, # order size in crypto
    #             'buy_amount': buy_amount, # order size in fiat
    #             'quote_decimal':order_data.get('quote_decimal'),
    #             'base_decimal':order_data.get('base_decimal')
    #         }
    #
    #         # Prepare book data
    #         temp_book = {
    #             'highest_bid': float(order_book.get('highest_bid', 0)),
    #             'lowest_ask': float(order_book.get('lowest_ask', 0)),
    #             'quote_avail_balance': fiat_avail_for_order, # was quote_amount
    #             'quote_decimal': quote_deci,
    #         }
    #         temp_book = {
    #             'highest_bid': float(order_book.get('highest_bid', 0)),
    #             'lowest_ask': float(order_book.get('lowest_ask', 0)),
    #         }
    #
    #         # Adjust price and size
    #         if side == 'sell':
    #             adjusted_bid, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book) # sell price
    #             adjusted_bid = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_bid, 'quote')
    #             adjusted_ask = 0
    #         elif side == 'buy':
    #             adjusted_ask, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book) # buying price
    #             adjusted_ask = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_ask, 'quote')
    #             adjusted_bid = 0
    #
    #         # Adjust size based on precision
    #         adjusted_size = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, size, 'base')
    #
    #         # Final order data
    #         order_data = {
    #             'lowest_ask': float(adjusted_ask),#✅
    #             'highest_bid': float(adjusted_bid),#✅
    #             'adjusted_size': float(adjusted_size),
    #             'order_amount':float(fiat_avail_for_order),#✅
    #             'size_of_the_order': float(adjusted_size),
    #             'spread': order_book['spread'],#❌
    #             'trading_pair': trading_pair,
    #             'usd_avail_balance': usd_bal,
    #             'base_avail_balance': float(balance),#✅
    #             'available_to_trade_crypto': cryto_avail_to_trade,
    #             'maker_fee': float(maker_fee),
    #             'taker_fee': float(taker_fee),
    #             'side': side,
    #             'quote_decimal': quote_deci,
    #             'base_decimal': base_deci,
    #             'stop_loss': None,
    #             'take_profit': None,
    #             'order_created_for': source+'_signal',
    #             'status_of_order': 'LIMIT/'+side+'/ROC'
    #         }
    #
    #         return order_data
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in build_order_data: {e}", exc_info=True)
    #         return None

    # def build_order_details_webhooks(self,shared_utils_precision, trade_data: dict, base_balance: str, base_price: Decimal,
    #                                  quote_price: Decimal,base_order_size: Decimal, quote_avail_balance: Decimal, usd_balance: Decimal,
    #                                  cryto_avail_to_trade:float, precision_data: tuple) -> dict:
    #
    #     quote_deci, base_deci, quote_increment, base_increment = precision_data
    #     return {
    #         'side': trade_data['side'],
    #         'base_increment': shared_utils_precision.float_to_decimal(base_increment, base_deci),
    #         'base_decimal': base_deci,
    #         'quote_decimal': quote_deci,
    #         'base_currency': trade_data['base_currency'],
    #         'quote_currency': trade_data['quote_currency'],
    #         'trading_pair': trade_data['trading_pair'],
    #         'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    #         'quote_price': quote_price,
    #         'order_amount': trade_data['order_amount'], #110
    #         'size_of_the_order': (float(trade_data['order_amount']/base_price)),
    #         'base_balance': base_balance,
    #         'base_price': base_price,
    #         'base_order_size': base_order_size,
    #         'quote_avail_balance': quote_avail_balance,
    #         'base_avail_balance': base_balance,
    #         'available_to_trade_crypto': cryto_avail_to_trade,
    #         'usd_avail_balance': usd_balance,
    #         'order_created_for': 'WEBSOCKET_signal',
    #         'status_of_order': 'LIMIT/'+trade_data['side']+'/ROC'
    #     }
