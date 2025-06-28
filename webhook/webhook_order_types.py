

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
    def get_instance(cls, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color,
                     shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                     websocket_helper, session):
        """
        Singleton method to ensure only one instance of OrderTypeManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color,
                                shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                                websocket_helper, session)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color, shared_data_manager,
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
        self.shared_utils_color = shared_utils_color
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
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get("bid_ask_spread", {})

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
            # all_open_orders, has_open_order, _ = await self.websocket_helper.refresh_open_orders(trading_pair=trading_pair)
            all_open_orders = self.open_orders # shared state
            # ✅ Check if there is an open order for the specific `trading_pair`
            has_open_order, open_order = self.shared_utils_utility.has_open_orders(trading_pair, all_open_orders)
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
                    'trigger': order_data.trigger,
                    'status': 'placed',
                    'source':order_data.source
                }
                await self.shared_data_manager.trade_recorder.record_trade(trade_data)
                response['trigger'] = order_data.trigger
                response['status'] = 'placed'
                response['source'] = order_data.source
                return response
            else:
                print(f"❗️ Order Rejected TP/SL: {response.get('error_response', {}).get('message')} ❗️") # debug

            return response

        except Exception as e:
            self.logger.error(f"❌ Error in process_limit_and_tp_sl_orders: {e}", exc_info=True)
            return None

    async def place_limit_order(self, source, order_data: OrderData):
        """
        Places a post-only limit order with retries and dynamic buffer adjustment to avoid rejections.
        """

        def is_post_only_rejection(resp: dict) -> bool:
            msg = (resp.get('message') or "").lower()
            reason = (resp.get('reason') or "").lower()
            return any(k in msg for k in ["post-only", "priced below", "match existing"]) or \
                any(k in reason for k in ["post-only", "invalid_limit_price"])

        response = None  # Ensure scope for error handler

        try:
            symbol = order_data.trading_pair.replace('/', '-')
            asset = symbol.split('-')[0]
            side = order_data.side.upper()
            caller_function_name = stack()[1].function

            amount = self.shared_utils_precision.safe_convert(order_data.adjusted_size, order_data.base_decimal)
            price = self.shared_utils_precision.safe_convert(
                order_data.highest_bid if side == 'SELL' else order_data.lowest_ask,
                order_data.quote_decimal
            )
            available_crypto = self.shared_utils_precision.safe_convert(order_data.available_to_trade_crypto, order_data.base_decimal)
            usd_available = self.shared_utils_precision.safe_convert(order_data.usd_balance, order_data.quote_decimal)

            params = {'post_only': True}
            attempts = 0
            price_buffer_pct = Decimal('0.001')  # Initial buffer: 0.1%
            min_buffer = Decimal('0.0000001')
            max_buffer = Decimal('0.01')

            while attempts < 3:
                attempts += 1

                # ✅ Required field check
                required_fields = ['trading_pair', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask']
                missing_fields = [f for f in required_fields if getattr(order_data, f) is None]

                if missing_fields:
                    self.logger.error(f"Missing required fields in OrderData: {missing_fields} | Data: {order_data}")
                    return {
                        'error': 'order_not_valid',
                        'code': 'MISSING_FIELDS',
                        'message': f"⚠️ Order Blocked - Incomplete OrderData: missing {missing_fields}"
                    }

                # ✅ Revalidate
                validation_result = self.validate.fetch_and_validate_rules(order_data)
                if not validation_result.get('is_valid'):
                    return {
                        'error': 'order_not_valid',
                        'code': validation_result.get('code'),
                        'message': f"⚠️ Order Blocked {asset} - Trading Rules Violation: {validation_result.details.get('condition')}"
                    }

                # ✅ Balance check
                if side == 'BUY':
                    usd_required = amount * price * (1 + order_data.maker)
                    if usd_required > usd_available:
                        return {
                            'error': 'Insufficient_USD',
                            'code': 'INSUFFICIENT_FUNDS',
                            'message': f"⚠️ Not enough USD (${order_data.usd_balance}) for BUY. Required: ${usd_required}"
                        }
                else:
                    if amount > available_crypto:
                        return {
                            'error': 'Insufficient_Balance',
                            'code': 'INSUFFICIENT_CRYPTO',
                            'message': f"⚠️ Not enough {asset} for SELL. Trying to sell: {amount}, Available: {available_crypto}"
                        }

                # ✅ Refresh order book
                latest_order_book = self.bid_ask_spread.get(order_data.trading_pair)
                latest_ask = self.shared_utils_precision.safe_convert(latest_order_book['ask'], order_data.quote_decimal) if latest_order_book[
                    'ask'] else price
                latest_bid = self.shared_utils_precision.safe_convert(latest_order_book['bid'], order_data.quote_decimal) if latest_order_book[
                    'bid'] else price

                # ✅ Apply buffer
                if side == 'BUY':
                    price = min(
                        latest_ask * (1 - price_buffer_pct),
                        latest_ask - min_buffer
                    ).quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_DOWN)
                else:  # SELL
                    price = max(
                        latest_bid * (1 + price_buffer_pct),
                        latest_bid + min_buffer
                    ).quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_UP)

                self.logger.info(f"🟡 Adjusted {side} limit price for {symbol}: {price} (Attempt {attempts})")

                formatted_price = f"{price:.{order_data.quote_decimal}f}"
                formatted_amount = f"{amount:.{order_data.base_decimal}f}"
                client_order_id = str(uuid.uuid4())
                payload = {
                    "client_order_id": f"{order_data.source}-{uuid.uuid4().hex[:8]}",
                    "product_id": symbol,
                    "side": side.upper(),
                    "order_configuration": {
                        "limit_limit_gtc": {
                            "base_size": str(formatted_amount),
                            "limit_price": str(formatted_price),
                            "post_only": True
                        }
                    }
                }
                response = await self.coinbase_api.create_order(payload)
                trigger_type = (order_data.trigger or {}).get("trigger", "UNKNOWN")

                color = {
                    "websocket": self.shared_utils_color.CYAN,
                    "PassiveMM": self.shared_utils_color.BLUE,
                    "webhook": self.shared_utils_color.YELLOW
                }.get(order_data.source, self.shared_utils_color.MAGENTA)

                print(self.shared_utils_color.format(
                    f"{order_data.source.upper()} ORDER ({trigger_type}) {symbol}: {response}",
                    color
                ))


                # if order_data.source == "websocket":
                #     print(self.shared_utils_color.format(f"'WEBSOCKET ORDER:  {response}", self.shared_utils_color.CYAN))
                #
                # elif order_data.source == 'PassiveMM' :
                #     print(self.shared_utils_color.format(f"PASSIVE ORDER:   {response}", self.shared_utils_color.BLUE))
                # elif order_data.source == 'webhook' :
                #     print(self.shared_utils_color.format(f"'WEBHOOK ORDER:{response}", self.shared_utils_color.YELLOW))

                if not response.get("success"):
                    return {
                        'success': False,
                        'error': response.get("error"),
                        'trigger': order_data.trigger,
                        'status': 'failed',
                        'message': response.get("details", "Unknown Error"),
                        'source': order_data.source
                    }

                if response.get("success"):
                    order_id = response.get("success_response", {}).get("order_id")
                    response["source"] = order_data.source
                    response["trigger"] = order_data.trigger
                    response["order_id"] = order_id
                    response["status"] = "placed"

                    await self.shared_data_manager.trade_recorder.record_trade({
                        'symbol': symbol,
                        'side': side.lower(),
                        'amount': formatted_amount,
                        'price': formatted_price,
                        'order_id': order_id,
                        'order_time': datetime.now(),
                        'trigger': order_data.trigger,
                        'status': 'placed',
                        'source': order_data.source,
                        'order_configuration': response['order_configuration']
                    })

                    return response

                if is_post_only_rejection(response):
                    self.logger.warning(f"🔁 Post-only rejection on attempt {attempts}: {response.get('message')}")
                    price_buffer_pct = min(price_buffer_pct + Decimal('0.0005'), max_buffer)
                    continue

                break  # Some other failure

            self.logger.warning(f"❗️ Order Rejected Limit Order: {symbol}:{order_data.trigger}", exc_info=True)
            print(f' ⚠️ process_limit_and_tp_sl_orders - Order Data: {order_data.debug_summary(verbose=True)}   ⚠️')



        except Exception as ex:
            self.logger.error(f"❌ Error in place_limit_order: {ex}", exc_info=True)
            return {
                'success': False,
                'error': str(ex),
                'trigger': 'limit',
                'status': response.get('status', 'failed') if response else 'failed',
                'message': response.get('message', str(ex)) if response else str(ex),
            }

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
            response['trigger'] = order_data.trigger
            response['status'] = 'placed'
            response['source'] = order_data.source
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
