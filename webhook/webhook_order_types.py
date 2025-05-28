import json
import uuid
from datetime import datetime, timedelta
from datetime import timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
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
    def get_instance(cls, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility,
                     shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                     websocket_helper, session):
        """
        Singleton method to ensure only one instance of OrderTypeManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility,
                                shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                                websocket_helper, session)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_data_manager,
                 validate, logger_manager, alerts, ccxt_api, order_book_manager, websocket_helper, session):
        self.config = Config()
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        # self.base_url = self.config._api_url
        self.logger = logger_manager  # 🙂

        self.validate = validate
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_data_manager = shared_data_manager
        self.session = session  # Store the session as an attribute
        self.start_time = self.ticker_cache = self.non_zero_balances = self.market_data = None
        self.order_tracker = self.market_cache_usd = self.market_cache_vol = self.order_management = None
        # ✅ Tracks recent orders to prevent duplicate placements
        self.recent_orders = TTLCache(maxsize=1000, ttl=10)  # Stores recent orders for 10 seconds

        # trade parameters
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
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
    def min_sell_value(self):
        return self._min_sell_value

    async def process_limit_and_tp_sl_orders(
            self, source: str, order_data: OrderData,
            take_profit: Optional[Decimal] = None,
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
            print_order_data = self.shared_utils_utility.pretty_summary(order_data)
            print(f"✅ Processing TP SL Order from {source}: {print_order_data}")

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
            base_quant = Decimal(f"1e-{order_data.base_decimal}")
            quote_quant = Decimal(f"1e-{order_data.quote_decimal}")
            adjusted_size = Decimal(order_data.adjusted_size).quantize(base_quant, rounding=ROUND_DOWN)

            if take_profit:
                take_profit = Decimal(take_profit).quantize(quote_quant, rounding=ROUND_DOWN)
            if stop_loss:
                stop_loss = Decimal(stop_loss).quantize(quote_quant, rounding=ROUND_DOWN)

            # ✅ Step 4: Ensure sufficient balances
            side = order_data.side.upper()
            usd_required = adjusted_size * order_data.adjusted_price * (1 + order_data.maker)
            usd_required = Decimal(usd_required).quantize(quote_quant, rounding=ROUND_DOWN)

            if side == 'BUY' and usd_required > Decimal(order_data.usd_balance):
                return {
                    'error': 'Insufficient_USD',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked - Insufficient USD (${order_data.usd_balance}) for {asset} BUY. Required: ${usd_required}"
                }
            elif side == 'BUY' and adjusted_size == 0.0:
                return {
                    'error': 'Zero_Size',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked - Zero Size for {asset} BUY."
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

            self.logger.debug(f"� Submitting Order: {order_payload}")
            order_data.time_order_placed = datetime.now()
            print(f'')
            print(f' ⚠️ process_limit_and_tp_sl_orders - Order Data: {order_data.debug_summary(verbose=True)}   ⚠️')  # Debug
            print(f'')
            # ✅ Step 6: Execute Order
            response = await self.coinbase_api.create_order(order_payload)

            if response.get('success') and response.get('success_response',{}).get('order_id'):
                order_id = response.get('success_response', {}).get('order_id')
                print(f"✅ Order Placed Successfully with TP/SL: {order_id} ✅")
                # 📝 Record the trade
                trade_data = {
                    'symbol': response.get('success_response',{}).get('product_id'),
                    'side': response.get('success_response',{}).get('side').lower(),
                    'amount': response.get('order_configuration',{}).get('limit_limit_gtc',{}).get('base_size'),
                    'pnl':None,
                    'total_fees':None,
                    'price': response.get('order_configuration',{}).get('limit_limit_gtc',{}).get('limit_price'),
                    'order_id': response.get('success_response',{}).get('order_id'),  # or client_order_id
                    'parent_id':response.get('success_response',{}).get('order_id'),
                    'order_time': datetime.utcnow(),  # Or response.get('timestamp') if exists
                    'trigger': 'limit',
                    'status': 'placed'
                }
                await self.shared_data_manager.trade_recorder.record_trade(trade_data)

                return response
            else:
                print(f"❗️ Order Rejected TP/SL: {response.get('error_response', {}).get('message')} ❗️") # debug

            return response

        except Exception as e:
            self.logger.error(f"❌ Error in process_limit_and_tp_sl_orders: {e}", exc_info=True)
            return None

    async def place_limit_order(self, source, order_data: OrderData):
        """
        Places a limit order and returns the order response or None if it fails.
        Handles price validation in fast-moving markets.
        """
        try:

            self.logger.debug(f"Placing limit order from {source} with data: {order_data}")
            caller_function_name = stack()[1].function

            # ✅ Required fields check
            required_fields = ['trading_pair', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask', 'available_to_trade_crypto']
            missing_fields = [field for field in required_fields if order_data is None]
            if missing_fields:
                self.logger.error(f"Missing required fields: {missing_fields} called by: {caller_function_name}")
                return None

            # ✅  Step 1: Extracting values
            symbol = order_data.trading_pair.replace('/', '-')
            asset = symbol.split('-')[0]
            side = order_data.side.upper()
            amount = Decimal(str(order_data.adjusted_size))
            price = Decimal(str(order_data.highest_bid) if side == 'sell' else order_data.lowest_ask)
            available_crypto = Decimal(str(order_data.available_to_trade_crypto))

            # ✅ Step 2: Revalidate
            validation_result = self.validate.fetch_and_validate_rules(order_data)

            if not validation_result.get('is_valid'):
                condition = validation_result.details.get("condition", validation_result.get('error'))
                return {
                    'error': 'order_not_valid',
                    'code': validation_result.get('code'),
                    'message': f"⚠️ Order Blocked {asset} - Trading Rules Violation: {condition}"
                }

            if order_data.side == 'buy':
                usd_available = Decimal(str(order_data.usd_avail_balance))
                usd_required = amount * price * (1 + order_data.maker)
                if usd_required > Decimal(order_data.usd_balance):
                    return {
                        'error': 'Insufficient_USD',
                        'code': validation_result.get('code'),
                        'message': f"⚠️ Order Blocked - Insufficient USD (${order_data.usd_balance}) for {asset} BUY. Required: ${usd_required}"
                    }
            else:
                usd_available = Decimal(str(order_data.usd_balance))

            params = {'post_only': True}

            # ✅ Ensure valid price
            if price <= 0:
                self.logger.error(f"Invalid price ({price}) for {side} order on {symbol}. Order data: {order_data}")
                return None

            # ✅ Ensure sufficient balance
            if side == 'BUY' and (amount * price) > usd_available:
                self.logger.info(f"Insufficient USD for BUY order on {symbol}. Required: {amount * price}, Available: {usd_available}")
                return None
            if side == 'SELL' and amount > available_crypto:
                self.logger.info(f"Insufficient {symbol} balance for SELL order. Trying to sell: {amount}, Available: {available_crypto}")
                return None

            # ✅ Refresh order book to get latest bid/ask
            latest_order_book = await self.order_book_manager.get_order_book(order_data, symbol)
            latest_lowest_ask = Decimal(str(latest_order_book['order_book']['asks'][0][0])) \
                if latest_order_book['order_book']['asks'] else price
            latest_highest_bid = Decimal(str(latest_order_book['order_book']['bids'][0][0])) \
                if latest_order_book['order_book']['bids'] else price

            # ✅ Define dynamic buffer as a percentage of the price
            price_buffer_pct = Decimal('0.001')  # 0.1% buffer
            min_buffer = Decimal('0.0000001')  # Minimum buffer for micro-priced assets

            # ✅ Adjust price dynamically to avoid post-only rejection
            if side == 'BUY' and price >= latest_lowest_ask:
                # lower than the ask to ensure no match
                adjusted = latest_lowest_ask * (Decimal('1') - price_buffer_pct)
                price = (max(adjusted, latest_lowest_ask - min_buffer)).quantize(
                    Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_DOWN
                )

            elif side == 'SELL' and price <= latest_highest_bid:
                # higher than the bid to ensure no match
                adjusted = latest_highest_bid * (Decimal('1') + price_buffer_pct)
                price = (min(adjusted, latest_highest_bid + min_buffer)).quantize(
                    Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_UP
                )

            self.logger.info(f"✅ Adjusted {side} limit order price: {price} for {symbol}")

            # ✅ Place the order
            self.exchange.verbose = False
            self.logger.info(f"Placing {side} limit order: {symbol}, Amount: {amount}, Price: {price}, Params: {params}")

            print(
                f" Post-only check — side: {side}, adjusted price: {price}, "
                f"lowest_ask: {latest_lowest_ask}, highest_bid: {latest_highest_bid}"
            )
            formatted_price = f"{price:.{order_data.quote_decimal}f}"
            formatted_amount = f"{amount:.{order_data.base_decimal}f}"
            response = await self.ccxt_api.ccxt_api_call(
                self.exchange.create_order, 'private', symbol, 'limit', side, formatted_amount, formatted_price, params=params
            )
            if not response:
                self.logger.error("❌ Order placement failed — response is None")
                return {
                    "success": False,
                    "code": "NULL_RESPONSE",
                    "message": "No response returned from ccxt API (likely rejected preflight)",
                    "error": "NoResponse",
                }

            if response and response.get('success'):
                print(f"✅ Order placed successfully: {response.get('side')} {response.get('symbol')} ✅")
                # 📝 Record the trade
                trade_data = {
                    'symbol': response.get('success_response', {}).get('product_id'),
                    'side': response.get('success_response', {}).get('side').lower(),
                    'amount': response.get('order_configuration', {}).get('limit_limit_gtc', {}).get('base_size'),
                    'price': response.get('order_configuration', {}).get('limit_limit_gtc', {}).get('limit_price'),
                    'order_id': response.get('success_response', {}).get('order_id'),  # or client_order_id
                    'order_time': datetime.now(),  # Or response.get('timestamp') if exists
                    'trigger': 'limit',
                    'status': 'placed'
                }
                await self.shared_data_manager.trade_recorder.record_trade(trade_data)

                return response
            elif response.get('info', {}).get('order_id') is not None:
                info = response.get('info')
                print(f"✅ Order placed successfully: {info.get('side')} {info.get('symbol')} ✅")
                # 📝 Record the trade
                trade_data = {
                    'symbol': response.get('symbol'),
                    'side': info.get('side').lower(),
                    'amount':"",
                    'price': "",
                    'order_id': info.get('order_id'),  # or client_order_id
                    'order_time': datetime.now(),  # Or response.get('timestamp') if exists
                    'trigger': 'limit',
                    'status': 'placed'
                }
                await self.shared_data_manager.trade_recorder.record_trade(trade_data)
                return response
            else:
                self.logger.warning(f"❗️ Order Rejected Limit Order: {response.get('status')}:{response.get('reason') }❗️")
                print(f"❗️ Order Rejected Limit Order: {response.get('status')}:{response.get('reason') }❗️")
                return response

        except Exception as ex:
            self.logger.error(f"❌ Error in place_limit_order: {ex}", exc_info=True)
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
                self.logger.bad_order(f"There is a balance of {available_balance} for {symbol}and the buy order will not be placed")
                return None
            elif order_value < Decimal(1.0) and order_data.get('side').lower() == 'sell':
                self.logger.bad_order(f"The min value of  this order is less than the $1.00 threshold and will not be placed"
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
                self.logger.error(f"Received None as the response from create_order.")
                return None
            elif 'Insufficient balance in source account' in response:
                print(f"� Debugging: Available balance {available_balance}, base_size {base_size}")
                self.logger.info(f"Insufficient funds for trailing stop order: {order_data['trading_pair']}")
                return None

            print(f'Trailing stop order placed for {order_data["trading_pair"]}, response: {response}')  # Debugging
            return response, market_price, trailing_stop_price

        except Exception as ex:
            self.logger.error(f" ❌ Error in place_trailing_stop_order: {str(ex)}", exc_info=True)
            return None

    @staticmethod
    async def update_order_payload(order_id, symbol, trailing_stop_price, limit_price, amount):
        return{
            "order_id":order_id,
            "price":str(limit_price),
            "size":str(amount)
        }
