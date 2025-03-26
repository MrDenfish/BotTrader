import json
import uuid
from datetime import datetime, timedelta
from datetime import timezone
from decimal import Decimal, ROUND_DOWN
from inspect import stack
from typing import Optional, Union

import pandas as pd
from cachetools import TTLCache

from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData

# Define the OrderTypeManager class
"""This class  will manage the order types 
    -Limit 
    -Market 
    -Bracket.
"""


class OrderTypeManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility,validate,
                     logmanager, alerts, ccxt_api, order_book_manager, websocket_helper, session):
        """
        Singleton method to ensure only one instance of OrderTypeManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility,validate,
                                logmanager, alerts, ccxt_api, order_book_manager, websocket_helper, session)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logmanager,
                 alerts, ccxt_api, order_book_manager, websocket_helper, session):
        self.config = Config()
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        # self.base_url = self.config._api_url
        self.log_manager = logmanager
        self.validate = validate
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.session = session  # Store the session as an attribute
        self.start_time = self.ticker_cache = self.non_zero_balances = self.market_data = None
        self.order_tracker = self.market_cache_usd = self.market_cache_vol = self.order_management = None
        # ✅ Tracks recent orders to prevent duplicate placements
        self.recent_orders = TTLCache(maxsize=1000, ttl=10)  # Stores recent orders for 10 seconds

        # trade parameters
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._taker_fee = Decimal(self.config.taker_fee)
        self._maker_fee = Decimal(self.config.maker_fee)
        self._sell_ratio = Decimal(self.config.sell_ratio)
        self._buy_ratio = Decimal(self.config.buy_ratio)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._trailing_limit = Decimal(self.config.trailing_limit)
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._hodl = Config.hodl

    @property
    def hodl(self):
        return self._hodl

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def sell_ratio(self):
        return self._sell_ratio

    @property
    def buy_ratio(self):
        return self._buy_ratio

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def trailing_stop(self):
        return self._trailing_stop

    @property
    def trailing_limit(self):
        return self._trailing_limit

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def maker_fee(self):
        return self._maker_fee

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def process_limit_and_tp_sl_orders(
            self, source: str, order_data: OrderData, take_profit: Optional[Decimal] = None,
            stop_loss: Optional[Decimal] = None
    ) -> Union[dict, None]:
        """
        Processes limit orders with attached Take Profit (TP) and Stop Loss (SL).

        Args:
            source (str): 'WebSocket' or 'Webhook' for tracking the order source.
            order_data (OrderData): Order details wrapped in a dataclass.
            take_profit (Decimal, optional): Take profit price.
            stop_loss (Decimal, optional): Stop loss price.

        Returns:
            dict or None: The API response if successful, None otherwise.
        """
        try:
            caller_function = stack()[1].function
            self.shared_utils_utility.log_event_loop(f"{caller_function}")

            asset = order_data.base_currency
            trading_pair = order_data.trading_pair

            print(f"✅ Processing Limit Order from {source}: {order_data}")

            # ✅ Step 1: Check for existing open orders
            all_open_orders, has_open_order, _ = await self.websocket_helper.refresh_open_orders(trading_pair=trading_pair)
            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()

            if has_open_order:
                return {
                    'error': 'open_order',
                    'code': 411,
                    'message': f"⚠️ Order Blocked - Existing Open Order for {trading_pair}"
                }

            # ✅ Step 2: Revalidate
            validation_result = self.validate.fetch_and_validate_rules(order_data)

            if not validation_result.get('is_valid'):
                condition = validation_result.details.get("condition", validation_result.get('error'))
                return {
                    'error': 'order_not_valid',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked {asset} - Trading Rules Violation: {condition}"
                }

            # ✅ Step 3: Adjust size to precision
            base_decimal = Decimal(f"1e-{order_data.base_decimal}")
            adjusted_size = Decimal(order_data.adjusted_size).quantize(base_decimal, rounding=ROUND_DOWN)

            if take_profit:
                take_profit = Decimal(take_profit).quantize(base_decimal, rounding=ROUND_DOWN)
            if stop_loss:
                stop_loss = Decimal(stop_loss).quantize(base_decimal, rounding=ROUND_DOWN)

            # ✅ Step 4: Ensure sufficient balances
            side = order_data.side.upper()
            usd_required = adjusted_size * Decimal(order_data.adjusted_price)

            if side == 'BUY' and usd_required > Decimal(order_data.usd_balance):
                return {
                    'error': 'Insufficient_USD',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked - Insufficient USD (${order_data.usd_balance}) for {asset} BUY. Required: ${usd_required}"
                }

            if side == 'SELL' and adjusted_size > Decimal(order_data.available_to_trade_crypto):
                return {
                    'error': 'Insufficient_crypto',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked - Insufficient Crypto to sell {asset}."
                }

            # ✅ Step 5: Build order payload
            client_order_id = str(uuid.uuid4())
            order_payload = {
                "client_order_id": client_order_id,
                "product_id": trading_pair.replace("/", "-"),
                "side": side,
                "order_configuration": {
                    "limit_limit_gtc": {
                        "baseSize": str(adjusted_size),
                        "limitPrice": str(order_data.adjusted_price)
                    }
                },
                "attached_order_configuration": {
                    "trigger_bracket_gtc": {
                        "limit_price": str(take_profit),
                        "stop_trigger_price": str(stop_loss)
                    }
                }
            }

            self.log_manager.info(f"� Submitting Order: {order_payload}")
            print(f' ⚠️ process_limit_and_tp_sl_orders - Order Data: {order_data.debug_summary(verbose=True)}')  # Debug
            # ✅ Step 6: Execute Order
            response_data = await self.coinbase_api.create_order(order_payload)

            if response_data.get('success'):
                order_id = response_data.get('success_response', {}).get('order_id')
                self.log_manager.info(f"✅ Order Placed Successfully with TP/SL: {order_id}")
                return response_data

            return response_data

        except Exception as e:
            self.log_manager.error(f"❌ Error in process_limit_and_tp_sl_orders: {e}", exc_info=True)
            return None

    # async def process_limit_and_tp_sl_orders(self,source: str,order_data: OrderData,take_profit: Optional[Decimal] = None,
    #                                          stop_loss: Optional[Decimal] = None) -> Union[dict, None]:
    #
    #     """
    #     Processes limit orders with attached Take Profit (TP) and Stop Loss (SL).
    #
    #     Args:
    #         source (str): 'WebSocket' or 'Webhook' for tracking the order source.
    #         order_data (dict): Order details.
    #         take_profit (Decimal, optional): Take profit price.
    #         stop_loss (Decimal, optional): Stop loss price.
    #
    #     Returns:
    #         dict or None: The API response if successful, None otherwise.
    #     """
    #     try:
    #         caller_function_name = stack()[1].function  # Debug
    #         self.shared_utils_utility.log_event_loop(f"{caller_function_name}")  # Debug log
    #
    #         print(f"✅ Processing Limit Order from {source}: {order_data}")
    #         asset = order_data.get('trading_pair').split('/')[0]
    #
    #         # ✅ Step 1: Check for Existing Open Orders
    #         all_open_orders, has_open_order, _ = await self.websocket_helper.refresh_open_orders(trading_pair=order_data['trading_pair'])
    #         open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
    #
    #         if has_open_order:
    #             return {
    #                 'error': 'open_order',
    #                 'code': 411,
    #                 'message': f"⚠️ Order Blocked - Existing Open Order for {order_data['trading_pair']}"
    #             }
    #
    #         # ✅ Refresh Order Book
    #         order_book = await self.order_book_manager.get_order_book(order_data)
    #         highest_bid, lowest_ask = Decimal(order_book['highest_bid']), Decimal(order_book['lowest_ask'])
    #
    #         # ✅ Adjust Price to Avoid Rejections
    #         order_price = (
    #             min(highest_bid, lowest_ask - Decimal('0.0001'))
    #             if order_data.get('details', {}).get('side').lower() == 'buy'
    #
    #             else max(highest_bid, lowest_ask + Decimal('0.0001'))
    #         )
    #         order_data['adjusted_price'] = order_price
    #         order_data['highest_bid'] = highest_bid
    #         order_data['lowest_ask'] = lowest_ask
    #
    #         # ✅ Step 2: Validate Order Against Trading Rules
    #         response_msg = self.validate.fetch_and_validate_rules(order_data)
    #         if not response_msg.get('details', {}).get('condition'):
    #             msg = response_msg.get('error')
    #         else:
    #             msg = response_msg.get('details', {}).get('condition')
    #
    #         if not response_msg.get('is_valid'):
    #            return {
    #                 'error': 'order_not_valid',
    #                 'code': response_msg.get('code'),
    #                 'message': f"⚠️ Order Blocked {asset} - Trading Rules Violation: {msg}"
    #             }
    #
    #         # ✅ Step 3: Adjust Order Price & Validate Data
    #         order_data, validated_order = await self.validate.validate_and_adjust_order(order_data)
    #
    #         if not validated_order['is_valid']:
    #             return {
    #                 'error': 'price_adjustment_failed',
    #                 'code':  response_msg.get('code'),
    #                 'message': f"⚠️ Order Blocked - Price Adjustment Failed: {validated_order.get('details', {}).get('condition')}"
    #             }
    #
    #         # ✅ Step 4: Adjust Order Size
    #         base_decimal = Decimal('1').scaleb(-validated_order.get('base_decimal', 2))
    #         amount = Decimal(order_data['adjusted_size']).quantize(base_decimal, rounding=ROUND_DOWN)
    #
    #         if take_profit:
    #             take_profit = Decimal(take_profit).quantize(base_decimal, rounding=ROUND_DOWN)
    #         if stop_loss:
    #             stop_loss = Decimal(stop_loss).quantize(base_decimal, rounding=ROUND_DOWN)
    #
    #         # ✅ Step 5: Ensure Sufficient Balance
    #         side = order_data['side'].upper()
    #         usd_available = Decimal(order_data.get('usd_available', 0))
    #         required_usd = amount * Decimal(order_data['adjusted_price'])
    #
    #         if side == 'BUY' and required_usd > usd_available:
    #             return {
    #                 'error': 'Insufficient_USD',
    #                 'code':  response_msg.get('code'),
    #                 'message': f"⚠️ Order Blocked - Insufficient USD (${usd_available}) for {asset} BUY order. Required: ${required_usd}"
    #             }
    #
    #         if side == 'SELL' and amount > Decimal(order_data.get('available_to_trade_crypto', 0)):
    #             return {
    #                 'error': 'Insufficient_crypto',
    #                 'code':  response_msg.get('code'),
    #                 'message': f"⚠️ Order Blocked - Insufficient Crypto to sell {asset}."
    #             }
    #
    #         # ✅ Step 6: Build Order Payload
    #         client_order_id = str(uuid.uuid4())  # Generate unique order ID
    #         order_payload = {
    #             "client_order_id": client_order_id,
    #             "product_id": order_data['trading_pair'].replace('/', '-'),
    #             "side": order_data['side'].upper(),
    #             "order_configuration": {
    #                 "limit_limit_gtc": {
    #                     "baseSize": str(amount),
    #                     "limitPrice": str(order_data['adjusted_price'])
    #                 }
    #             },
    #             "attached_order_configuration": {
    #                 "trigger_bracket_gtc": {
    #                     "limit_price": str(take_profit),
    #                     "stop_trigger_price": str(stop_loss)
    #                 }
    #             }
    #         }
    #
    #         self.log_manager.info(f"� Submitting Order: {order_payload}")
    #
    #         # ✅ Step 7: Execute Order
    #         response_data = await self.coinbase_api.create_order(order_payload)
    #
    #         if response_data.get('success'):
    #             order_id = response_data.get('success_response', {}).get('order_id')
    #             self.log_manager.info(f"✅ Order Placed Successfully with TP/SL: {order_id}")
    #             return response_data
    #
    #         # ✅ Step 8: Handle Specific Errors
    #         return response_data
    #
    #     except Exception as e:
    #         self.log_manager.error(f"❌ Error in process_limit_and_tp_sl_orders: {e}", exc_info=True)
    #         return None

    async def place_limit_order(self, order_data: OrderData):
        """
        Places a limit order and returns the order response or None if it fails.
        Handles price validation in fast-moving markets.
        """
        try:
            self.log_manager.debug(f"Placing limit order with data: {order_data}")
            caller_function_name = stack()[1].function

            # ✅ Required fields check
            required_fields = ['trading_pair', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask', 'available_to_trade_crypto']
            missing_fields = [field for field in required_fields if order_data.get(field) is None]
            if missing_fields:
                self.log_manager.error(f"Missing required fields: {missing_fields} called by: {caller_function_name}")
                return None

            # ✅ Extracting values
            symbol = order_data['trading_pair'].replace('/', '-')
            side = order_data['side'].upper()
            amount = Decimal(str(order_data['base_avail_to_trade']))
            price = Decimal(str(order_data.get('highest_bid' if side == 'sell' else 'lowest_ask', 0)))
            available_crypto = Decimal(str(order_data.get('available_to_trade_crypto', 0)))
            usd_available = Decimal(str(order_data.get('usd_available', 0)))
            params = {'post_only': True}

            # ✅ Ensure valid price
            if price <= 0:
                self.log_manager.error(f"Invalid price ({price}) for {side} order on {symbol}. Order data: {order_data}")
                return None

            # ✅ Ensure sufficient balance
            if side == 'BUY' and (amount * price) > usd_available:
                self.log_manager.info(f"Insufficient USD for BUY order on {symbol}. Required: {amount * price}, Available: {usd_available}")
                return None
            if side == 'SELL' and amount >= available_crypto:
                self.log_manager.info(f"Insufficient {symbol} balance for SELL order. Trying to sell: {amount}, Available: {available_crypto}")
                return None

            # ✅ Refresh order book to get latest bid/ask
            latest_order_book = await self.order_book_manager.k(order_data, symbol)
            latest_lowest_ask = Decimal(str(latest_order_book['order_book']['asks'][0][0])) \
                if latest_order_book['order_book']['asks'] else price
            latest_highest_bid = Decimal(str(latest_order_book['order_book']['bids'][0][0])) \
                if latest_order_book['order_book']['bids'] else price

            # ✅ Define dynamic buffer as a percentage of the price
            price_buffer_pct = Decimal('0.001')  # 0.1% buffer
            min_buffer = Decimal('0.0000001')  # Minimum buffer for micro-priced assets

            # ✅ Adjust price dynamically to avoid post-only rejection
            if side == 'BUY' and price >= latest_lowest_ask:
                price = max(latest_lowest_ask * (Decimal('1') - price_buffer_pct), latest_lowest_ask - min_buffer)
            elif side == 'SELL' and price <= latest_highest_bid:
                price = min(latest_highest_bid * (Decimal('1') + price_buffer_pct), latest_highest_bid + min_buffer)

            self.log_manager.info(f"✅ Adjusted {side} limit order price: {price} for {symbol}")

            # ✅ Place the order
            self.exchange.verbose = False
            self.log_manager.info(f"Placing {side} limit order: {symbol}, Amount: {amount}, Price: {price}, Params: {params}")

            response = await self.ccxt_api.ccxt_api_call(
                self.exchange.create_order, 'private', symbol, 'limit', side, amount, price, params=params
            )

            if response:
                self.log_manager.info(f"✅ Order placed successfully: {response}")
                return response
            else:
                self.log_manager.error(f"⚠️ Received None from create_order for {symbol}. Order data: {order_data}")
                return None

        except Exception as ex:
            self.log_manager.error(f"❌ Error in place_limit_order: {ex}", exc_info=True)
            return None

    async def place_trailing_stop_order(self, order_book, order_data, market_price):
        """
        Places a trailing stop order. Returns the API response as a dictionary.
        """
        try:
            client_order_id = str(uuid.uuid4())
            trailing_percentage = Decimal(self.trailing_percentage)  # Now Decimal for consistency
            market_price = Decimal(market_price)
            symbol = order_data['trading_pair']
            asset = symbol.split('/')[0]

            # ✅ Use Available Balance Instead of Total Balance
            spot_position = self.market_data.get('spot_positions', {})
            available_balance = Decimal(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
            order_value = market_price * available_balance

            # ✅ Ensure order size is valid and remove bad orders
            if order_value < Decimal(1.0) and order_data.get('side').lower() == 'buy':
                self.log_manager.bad_order(f"There is a balance of {available_balance} for {symbol}and the buy order will not be placed")
                return None
            elif order_value < Decimal(1.0) and order_data.get('side').lower() == 'sell':
                self.log_manager.bad_order(f"The min value of  this order is less than the $1.00 threshold and will not be placed"
                                      f" {symbol}: {available_balance} ~${order_value}")
                return None
            # ✅ Adjust for Fee Deduction
            maker_fee = Decimal(self.maker_fee)
            base_size = available_balance - (available_balance * maker_fee)

            # ✅ Ensure correct decimal precision
            base_size = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], base_size, convert='base'
            )

            # ✅ Fetch latest price
            endpoint = 'public'
            ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, endpoint, symbol)
            current_price = Decimal(ticker_data['last'])

            # ✅ Prevent invalid price calculations
            if market_price is None:
                raise ValueError("Could not retrieve the latest trade price.")

            # ✅ Calculate the trailing stop price (Fixed for BUY orders)
            if order_data['side'].upper() == 'buy':
                trailing_stop_price = market_price * (Decimal('1.0') + trailing_percentage / Decimal('100'))
            else:
                trailing_stop_price = market_price * (Decimal('1.0') - trailing_percentage / Decimal('100'))

            # ✅ Adjust stop price calculation
            if order_data['side'].upper() == 'buy':
                stop_price = max(trailing_stop_price, current_price * Decimal('1.002'))  # Ensure stop is higher for BUY
            else:
                stop_price = min(trailing_stop_price, current_price * Decimal('0.998'))  # Ensure stop is lower for SELL

            # ✅ Adjust limit price calculation
            if order_data['side'].upper() == 'buy':
                limit_price = stop_price * (
                            Decimal('1.003') + maker_fee)  # Buy limit price must be slightly above stop price
            else:
                limit_price = stop_price * (
                            Decimal('0.997') - maker_fee)  # Sell limit price must be slightly below stop price

            # ✅ Adjust prices for precision
            stop_price = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], stop_price, convert='quote'
            )
            limit_price = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], limit_price, convert='quote'
            )

            base_balance = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], order_data['base_avail_balance'], convert='base'
            )

            adjusted_size = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], order_data['available_to_trade_crypto'], convert='quote'
            )

            # ✅ Format Trading Pair for Coinbase API
            product_id = order_data['trading_pair'].replace('/', '-').upper()

            # ✅ Set up the payload
            payload = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "sell" if order_data['side'].upper() == 'sell' else "buy",
                "order_configuration": {
                    "stop_limit_stop_limit_gtd": {
                        "base_size": str(adjusted_size) if adjusted_size > 0 else str(base_balance),
                        "stop_price": str(stop_price),
                        "limit_price": str(limit_price),
                        "end_time": (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "stop_direction": "STOP_DIRECTION_STOP_DOWN" if order_data['side'].upper() == 'sell' \
                            else "STOP_DIRECTION_STOP_UP"
                    }
                }
            }

            # ✅ Debugging
            print(f"Payload before sending: {json.dumps(payload, indent=4)}")

            # ✅ Send order request to Coinbase API
            response = await self.coinbase_api.create_order(payload)

            if response is None:
                self.log_manager.error(f"Received None as the response from create_order.")
                return None
            elif 'Insufficient balance in source account' in response:
                print(f"� Debugging: Available balance {available_balance}, base_size {base_size}")
                self.log_manager.info(f"Insufficient funds for trailing stop order: {order_data['trading_pair']}")
                return None

            print(f'Trailing stop order placed for {order_data["trading_pair"]}, response: {response}')  # Debugging
            return response, market_price, trailing_stop_price

        except Exception as ex:
            self.log_manager.error(f"Error in place_trailing_stop_order: {str(ex)}", exc_info=True)
            return None

    @staticmethod
    async def update_order_payload(order_id, symbol, trailing_stop_price, limit_price, amount):
        return{
            "order_id":order_id,
            "price":str(limit_price),
            "size":str(amount)
        }

    # async def _handle_bracket_order(self, order_data, order_book_details):
    #     """Not Currently in USE"""
    #     try:
    #         currencies = [order_data['trading_pair'].split('/')[0], order_data['trading_pair'].split('/')[1]]
    #         accounts = await self.shared_utils_precision.get_account_balance(currencies, get_staked=False)
    #         _, has_open_order = await self.websocket_helper.refresh_open_orders(trading_pair=order_data['trading_pair'])
    #         if not has_open_order:
    #             response = await self.place_bracket_order(order_data, order_book_details)
    #             self.log_manager.info(order_data['trading_pair'], order_data['adjusted_price'], order_data[
    #                 'side'])
    #             return response
    #         else:
    #             self.log_manager.info(f'Order already exists for {order_data["trading_pair"]}')
    #             return {'success': False, 'message': 'Order already exists'}
    #     except Exception as ex_bracket:
    #         self.log_manager.error(f"Error in _handle_bracket_order: {str(ex_bracket)}", exc_info=True)
    #         return None

    # async def place_bracket_order(self, order_data, order_book):
    print(f"‼️ NOT IMPLEMENTED: place_bracket_order")
    #     """
    #     Attempts to place a sell bracket order and returns the response.
    #     If the order fails, it logs the error and returns None. Bracket orders are market orders and will incur larger fees.
    #     """
    #     try:
    #         client_order_id = str(uuid.uuid4())
    #         end_time = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
    #
    #         # Adjust price and size
    #         adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
    #
    #         if adjusted_price is None or adjusted_size is None:
    #             self.log_manager.error("Failed to adjust price or size.")
    #             return None
    #         symbol = order_data['trading_pair'].replace('/', '-')
    #
    #         # Adjust limit and stop prices to the correct precision
    #         limit_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
    #                                                   adjusted_price, 'quote')
    #         stop_trigger_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
    #                                                          order_data['stop_loss_price'], 'quote')
    #         # Ensure limit price is within bounds
    #         market_price = Decimal(order_data['adjusted_price'])
    #         min_price = market_price * self.sell_ratio
    #         max_price = market_price * self.buy_ratio
    #
    #         if limit_price < min_price or limit_price > max_price:
    #             self.log_manager.error(
    #                 f"Limit price {limit_price} is out of bounds (min: {min_price}, max: {max_price}).")
    #             return None
    #         adjust_precision_take_profit = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data[
    #             'quote_decimal'], order_data['take_profit_price'], convert='quote')
    #         payload = {
    #             "client_order_id": client_order_id,
    #             "product_id": symbol,
    #             "side": "sell" if order_data['side'].upper() == 'sell' else "buy",
    #             "order_configuration": {
    #                 "trigger_bracket_gtd": {
    #                     "base_size": str(adjusted_size),
    #                     "limit_price": str(adjust_precision_take_profit),
    #                     "stop_trigger_price": str(stop_trigger_price),
    #                     "end_time": end_time
    #                 }
    #             }
    #         }
    #         response = await self.coinbase_api.create_order(payload)
    #         return response
    #     except Exception as e:
    #         self.log_manager.error(f"Error placing bracket order: {str(e)}", exc_info=True)
    #         return None

    # async def handle_order_errors(self, order_payload, response_data):
    #     """
    #     Handles specific order errors and attempts corrections where possible.
    #
    #     Args:
    #         order_payload (dict): The original order request payload.
    #         response_data (dict): The API response containing error details.
    #
    #     Returns:
    #         tuple: (response_data, None, None) or (updated_response, new_tp, new_sl)
    #     """
    #     error_response = response_data.get('error_response', {})
    #     preview_failure_reason = error_response.get('preview_failure_reason', '')
    #
    #     # if "PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE_OUT_OF_BOUNDS" in preview_failure_reason:
    #     #     self.log_manager.warning(f"⚠️ Take Profit price out of bounds. Adjusting TP for {order_payload['product_id']}")
    #     #
    #     #     # Adjust TP price by a small percentage
    #     #     new_tp = Decimal(order_payload['attached_order_configuration']['trigger_bracket_gtc']['limit_price']) * Decimal('0.99')
    #     #
    #     #     # Update order payload
    #     #     order_payload['attached_order_configuration']['trigger_bracket_gtc']['limit_price'] = str(new_tp)
    #     #
    #     #     self.log_manager.info(f"� Retrying Order with Adjusted TP: {new_tp}")
    #     #
    #     #     # Retry order placement
    #     #     updated_response = await self.coinbase_api.create_order(order_payload)
    #     #
    #     #     if updated_response.get('success'):
    #     #         return updated_response, new_tp, order_payload['attached_order_configuration']['trigger_bracket_gtc']['stop_trigger_price']
    #     #
    #     #     self.log_manager.error(f"❌ Order retry failed after TP adjustment: {updated_response}")
    #     #
    #     # else:
    #     #     self.log_manager.error(f"❌ Order failed with unknown error: {error_response}")
    #
    #     return response_data, None, None
