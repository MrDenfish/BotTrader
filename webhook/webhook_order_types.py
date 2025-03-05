
from Shared_Utils.config_manager import CentralConfig as Config
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from inspect import stack
import json
import uuid


# Define the OrderTypeManager class
"""This class  will manage the order types 
    -Limit 
    -Market 
    -Bracket.
"""


class OrderTypeManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api, exchange_client, shared_utils_precision, validate,
                     logmanager, alerts, ccxt_api, order_book_manager, websocket_helper, session):
        """
        Singleton method to ensure only one instance of OrderTypeManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, validate,
                                logmanager, alerts, ccxt_api, order_book_manager, websocket_helper, session)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, validate, logmanager,
                 alerts, ccxt_api, order_book_manager, websocket_helper, session):
        self.config = Config()
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        # self.base_url = self.config._api_url
        self.log_manager = logmanager
        self.validate = validate
        self.order_book = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.session = session  # Store the session as an attribute
        self.start_time = self.ticker_cache = self.non_zero_balances = self.market_data = None
        self.order_tracker = self.market_cache_usd = self.market_cache_vol = self.order_management = None

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


    # def set_trade_parameters(self, market_data, order_management,  start_time=None):
        # self.start_time = start_time
        # self.market_data = market_data
        # self.order_management = order_management
        # self.ticker_cache = market_data.get('ticker_cache')
        # self.non_zero_balances = order_management.get('non_zero_balances', {})
        # self.order_tracker = order_management.get('order_tracker', {})
        # self.market_cache_usd = market_data['usd_pairs_cache']
        # self.market_cache_vol = market_data['filtered_vol']

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

    async def place_limit_order(self, order_data):
        """
        Places a limit order and returns the order response or None if it fails.

        Args:
            order_data (dict): Data required to place the order.

        Returns:
            dict or None: Response from the exchange or None on failure.
        """
        try:
            # Debug: Log the incoming order_data
            self.log_manager.debug(f"Placing limit order with data: {order_data}")
            caller_function_name = stack()[1].function
            # Ensure all required fields are present
            required_fields = ['trading_pair', 'usd_available', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask']
            for field in required_fields:
                if field not in order_data or order_data[field] is None:
                    self.log_manager.error(f"Missing required field in order_data: {field} called by:{caller_function_name}")
                    return None

            params= {'post_only': True}
            price = 0
            # Call the API to create the order
            symbol= order_data.get('trading_pair').replace('/', '-')
            side = order_data['side']
            amount = float(order_data.get('adjusted_size'))

            if side == 'SELL':
                price = float(order_data.get('highest_bid',0))
            elif side == 'BUY':
                price = float(order_data.get('lowest_ask',0)) # Ensure proper decimal formatting

            # price = float(order_data['highest_bid'])
            self.exchange.verbose = False
            print(f"‼️ {symbol}, {'limit'}, {side}, {amount}, {price}, {params}")

            if (amount*price) < order_data.get('usd_available',0):
                # response = await self.exchange.create_order(symbol, 'limit', side, amount, price, params=params)

                response = await self.ccxt_api.ccxt_api_call(self.exchange.create_order,'private',symbol,'limit',side,amount,
                                                         price,params=params)
                if response is None:
                    self.log_manager.error(f"Received None as the response from create_order.")
                    return None
                else:
                    print(f'Order placed: {response}')
            else:
                self.log_manager.info(f"Insufficient funds for limit order: {order_data['trading_pair']}")
                return None
            # Debug: Log the API response
            self.log_manager.debug(f"Limit order response: {response}")

            # Check if the response is None
            if response is None:
                self.log_manager.info(f"Received None as the response from create_order."
                                       f" {response} : {order_data} func: {caller_function_name}", exc_info=True)
                return None

            return response

        except Exception as ex:
            # Log any exception that occurs
            self.log_manager.error(f"Error in place_limit_order: {str(ex)}", exc_info=True)
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

            # ✅ Ensure order size is valid
            if available_balance <= 0:
                self.log_manager.error(f"Insufficient funds for {symbol}: {available_balance}")
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
            if order_data['side'].upper() == 'BUY':
                trailing_stop_price = market_price * (Decimal('1.0') + trailing_percentage / Decimal('100'))
            else:
                trailing_stop_price = market_price * (Decimal('1.0') - trailing_percentage / Decimal('100'))

            # ✅ Adjust stop price calculation
            if order_data['side'].upper() == 'BUY':
                stop_price = max(trailing_stop_price, current_price * Decimal('1.002'))  # Ensure stop is higher for BUY
            else:
                stop_price = min(trailing_stop_price, current_price * Decimal('0.998'))  # Ensure stop is lower for SELL

            # ✅ Adjust limit price calculation
            if order_data['side'].upper() == 'BUY':
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
                order_data['base_decimal'], order_data['quote_decimal'], order_data['base_balance'], convert='base'
            )

            adjusted_size = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], order_data['adjusted_size'], convert='base'
            )

            # ✅ Format Trading Pair for Coinbase API
            product_id = order_data['trading_pair'].replace('/', '-').upper()

            # ✅ Set up the payload
            payload = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "SELL" if order_data['side'].upper() == 'SELL' else "BUY",
                "order_configuration": {
                    "stop_limit_stop_limit_gtd": {
                        "base_size": str(adjusted_size) if adjusted_size > 0 else str(base_balance),
                        "stop_price": str(stop_price),
                        "limit_price": str(limit_price),
                        "end_time": (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "stop_direction": "STOP_DIRECTION_STOP_DOWN" if order_data['side'].upper() == 'SELL' \
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

    # async def place_trailing_stop_order(self, order_book, order_data, market_price):
    #     """
    #     Places a trailing stop order. Returns the API response as a dictionary.
    #     """
    #     try:
    #         client_order_id = str(uuid.uuid4())
    #         trailing_percentage = Decimal(self.trailing_percentage)  # Now Decimal for consistency
    #         market_price = Decimal(market_price)
    #         symbol= order_data['trading_pair']
    #         taker_fee = Decimal(self.taker_fee)
    #         maker_fee = Decimal(self.maker_fee)
    #         highest_bid = Decimal(order_book['highest_bid'])
    #         lowest_ask = Decimal(order_book['lowest_ask'])
    #         asset = symbol.split('/')[0]
    #         spot_position = self.market_data.get('spot_positions', {})
    #         usd_pairs = self.market_data.get('usd_pairs_cache', {})
    #         base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset,
    #                                                                                   usd_pairs)
    #         balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
    #         usd_bal = Decimal(spot_position.get('USD', {}).get('total_balance_crypto', 0))
    #         endpoint = 'public'
    #         ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, endpoint, symbol)
    #         current_price = Decimal(ticker_data['last'])
    #         if market_price is None:
    #             raise ValueError("Could not retrieve the latest trade price.")
    #
    #         # Calculate the trailing stop price
    #         trailing_stop_price = market_price * (Decimal('1.0') - trailing_percentage / Decimal('100')) \
    #             if order_data['side'].upper() == 'SELL' else market_price * (
    #                     Decimal('1.0') + trailing_percentage / Decimal('100'))
    #
    #         # Adjust stop and limit prices for sell orders
    #         if order_data['side'].upper() == 'SELL':
    #             stop_price = min(trailing_stop_price, (current_price * (Decimal('1.0') - self.trailing_stop))) # Ensures
    #             # stop <
    #             # last trade price
    #             limit_price = stop_price * (Decimal('1.0') + trailing_percentage ) + (stop_price * maker_fee)  # Adjusted
    #             # for precision
    #         else:
    #             if order_data['side'].upper() == 'BUY':
    #                 stop_price = max(trailing_stop_price, current_price * Decimal('1.002'))  # Adjusted to ensure it's higher
    #                 limit_price = stop_price * (Decimal('1.003') + maker_fee)  # Buy limit price must be slightly above
    #             else:
    #                 stop_price = min(trailing_stop_price,
    #                                  current_price * Decimal('0.998'))  # Ensure it's lower for SELL orders
    #
    #                 limit_price = stop_price * (Decimal('0.997') - maker_fee)  # Sell limit price must be slightly below
    #
    #         # Adjust prices for precision
    #         stop_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'], stop_price,
    #                                                  convert='quote')
    #         limit_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'], limit_price,
    #                                                   convert='quote')
    #         base_balance = self.shared_utils_precision.adjust_precision(order_data['base_decimal'],
    #                                                                     order_data['quote_decimal'],
    #                                                                     order_data['base_balance'],convert='base')
    #
    #         adjusted_size = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data[
    #             'quote_decimal'],order_data['adjusted_size'],convert='base')
    #
    #         # Set up the payload
    #         payload = {
    #             "client_order_id": client_order_id,
    #             "product_id": order_data['trading_pair'].replace('/', '-'),
    #             "side": "SELL" if order_data['side'].upper() == 'SELL' else "BUY",
    #             "order_configuration": {
    #                 "stop_limit_stop_limit_gtd": {
    #                     "base_size": str(adjusted_size) if adjusted_size > 0  else str(base_balance),
    #                     "stop_price": str(stop_price),
    #                     "limit_price": str(limit_price),
    #                     "end_time": (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'),
    #                     "stop_direction": "STOP_DIRECTION_STOP_DOWN" if order_data['side'].upper() == 'SELL' \
    #                         else "STOP_DIRECTION_STOP_UP"
    #                 }
    #             }
    #         }
    #
    #         response = await self.coinbase_api.create_order(payload) # used for trailing stop orders instead of ccxt
    #         if response is None:
    #             self.log_manager.error(f"Received None as the response from create_order.")
    #             return None
    #         elif  'Insufficient balance in source account' in response:
    #             print(f'{payload}' ) # debugging
    #             self.log_manager.info(f"Insufficient funds for trailing stop order: {order_data['trading_pair']}")
    #             return None
    #         print(f'Trailing stop order placed for {order_data["trading_pair"]}reporting {response}')# debugging
    #         return response, market_price, trailing_stop_price
    #
    #     except Exception as ex:
    #         self.log_manager.error(f"Error in place_trailing_stop_order: {str(ex)}", exc_info=True)
    #         return None

    async def _handle_bracket_order(self, order_data, order_book_details):
        try:
            currencies = [order_data['trading_pair'].split('/')[0], order_data['trading_pair'].split('/')[1]]
            accounts = await self.shared_utils_precision.get_account_balance(currencies, get_staked=False)
            _, has_open_order = await self.websocket_helper.refresh_open_orders('BracketOrder',
                trading_pair=order_data['trading_pair'], order_data=order_data
            )
            if not has_open_order:
                response = await self.place_bracket_order(order_data, order_book_details)
                self.log_manager.info(order_data['trading_pair'], order_data['adjusted_price'], order_data[
                    'side'])
                return response
            else:
                self.log_manager.info(f'Order already exists for {order_data["trading_pair"]}')
                return {'success': False, 'message': 'Order already exists'}
        except Exception as ex_bracket:
            self.log_manager.error(f"Error in _handle_bracket_order: {str(ex_bracket)}", exc_info=True)
            return None

    async def place_bracket_order(self, order_data, order_book):
        """
        Attempts to place a sell bracket order and returns the response.
        If the order fails, it logs the error and returns None. Bracket orders are market orders and will incur larger fees.
        """
        try:
            client_order_id = str(uuid.uuid4())
            end_time = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')

            # Adjust price and size
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)

            if adjusted_price is None or adjusted_size is None:
                self.log_manager.error("Failed to adjust price or size.")
                return None
            symbol = order_data['trading_pair'].replace('/', '-')

            # Adjust limit and stop prices to the correct precision
            limit_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
                                                      adjusted_price, 'quote')
            stop_trigger_price = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
                                                             order_data['stop_loss_price'], 'quote')
            # Ensure limit price is within bounds
            market_price = Decimal(order_data['adjusted_price'])
            min_price = market_price * self.sell_ratio
            max_price = market_price * self.buy_ratio

            if limit_price < min_price or limit_price > max_price:
                self.log_manager.error(
                    f"Limit price {limit_price} is out of bounds (min: {min_price}, max: {max_price}).")
                return None
            adjust_precision_take_profit = self.shared_utils_precision.adjust_precision(order_data['base_decimal'], order_data[
                'quote_decimal'], order_data['take_profit_price'], convert='quote')
            payload = {
                "client_order_id": client_order_id,
                "product_id": symbol,
                "side": "SELL" if order_data['side'].upper() == 'SELL' else "BUY",
                "order_configuration": {
                    "trigger_bracket_gtd": {
                        "base_size": str(adjusted_size),
                        "limit_price": str(adjust_precision_take_profit),
                        "stop_trigger_price": str(stop_trigger_price),
                        "end_time": end_time
                    }
                }
            }
            response = await self.coinbase_api.create_order(self.session, payload)
            return response
        except Exception as e:
            self.log_manager.error(f"Error placing bracket order: {str(e)}", exc_info=True)
            return None




