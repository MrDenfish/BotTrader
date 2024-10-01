
from decimal import Decimal
import traceback
import asyncio
import uuid
from datetime import datetime, timedelta

# Define the OrderTypeManager class
"""This class  will manage the order types 
    -Limit 
    -Market 
    -Bracket.
"""


class OrderTypeManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, config, coinbase_api, exchange_client, utility, validate, logmanager, alerts, ccxt_api,
                     order_book, session):
        if cls._instance is None:
            cls._instance = cls(config, coinbase_api, exchange_client, utility, validate, logmanager, alerts, ccxt_api,
                                order_book, session)
        return cls._instance

    def __init__(self, config, coinbase_api, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book,
                 session):
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self._trailing_percentage = Decimal(config.trailing_percentage)
        self._min_sell_value = Decimal(config.min_sell_value)
        self._hodl = config.hodl
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        self.base_url = config.api_url
        self.log_manager = logmanager
        self.validate = validate
        self.order_book = order_book
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts
        self.utils = utility
        self.session = session  # Store the session as an attribute

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
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def original_place_limit_order(self, order_data):

        """ ***********
        THIS SHOULD BE KEPT UNTIL THE NEW FUNCTION IS TESTED AND WORKING
        DO NOT DELETE
        ***************
        Attempts to place a limit order and returns the response.
        If the order fails, it logs the error and returns None.
        """
        trading_pair = order_data['trading_pair']

        try:
            side = order_data['side']
            adjusted_size = order_data['adjusted_size']
            adjusted_price = order_data['adjusted_price']
            endpoint = 'private'
            response = await self.ccxt_exceptions.ccxt_api_call(self.exchange.create_limit_order, endpoint, trading_pair,
                                                                side, adjusted_size, adjusted_price, {'post_only': True})
            order_id = response['id']
            order_attempts = 0
            while True:  # Check order status until it is filled
                await asyncio.sleep(0.1)  # Yield back to the event loop
                try:
                    order_status = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_order, endpoint, order_id,
                                                                            trading_pair)
                    if order_status['status'] == 'closed':
                        print('Primary buy order filled')
                        break
                    elif order_attempts > 4:
                        print('Primary buy order was not filled after 5 attempts')
                        break
                    else:
                        order_attempts += 1
                except Exception as e:
                    print(f'Error fetching order status: {e}')
                await asyncio.sleep(5)  # Check order status every 5 seconds

            if response:
                if response == 'amend':
                    return False  # Order needs amendment
                elif response == 'insufficient base balance':
                    return False  # Insufficient balance
                elif response == 'order_size_too_small':
                    return False  # Order size too small
                elif response['status'] == 'open':
                    return False  # Order was not filled
                else:
                    return response  # order placed successfully
            else:
                return False
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.error(f'place_limit_order: {error_details}')
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{trading_pair}')
                return 'amend'
            else:
                self.log_manager.error(f'Error placing limit order: {ex}')
                return 'amend'

    async def place_limit_order(self, order_data):
        """
        Places a buy limit order. Returns a dictionary with the order response.
        """
        try:
            response = await self.ccxt_exceptions.ccxt_api_call(
                self.exchange.create_order,
                endpoint_type='private',
                symbol=order_data['trading_pair'],
                type='limit',
                side=order_data['side'],
                amount=order_data['adjusted_size'],
                price=order_data['highest_bid'],
                params={'post_only': True}
            )
            return response
        except Exception as ex:
            self.log_manager.error(f"Error in place_limit_order: {str(ex)}", exc_info=True)
            return None

    async def execute_market_sell(self, symbol, current_price):
        """
        Executes a market sell order immediately at the current price.
        """
        try:
            order_data = {
                "product_id": symbol.replace('/', '-'),
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": "FULL",  # Sell full quantity
                    }
                }
            }
            response = await self.coinbase_api.create_order(order_data)
            if response.get('success', False):
                print(f"Market sell executed successfully: {response}")
            else:
                print(f"Failed to execute market sell: {response}")
        except Exception as e:
            print(f"Error executing market sell: {e}")
    async def place_market_order(self, trading_pair, side, adjusted_size, adjusted_price):
        """
        This function coordinates the process. It calculates the order parameters, attempts to place
        an order, checks if the order is accepted, and retries if necessary.
        """
        response = None
        try:
            endpoint = 'private'
            response = await self.ccxt_exceptions.ccxt_api_call(self.exchange.create_market_order(trading_pair, side,
                                                                adjusted_size, adjusted_price), endpoint, trading_pair)
            if response:
                return response
        except Exception as ex:
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.info(f'Market order was not accepted, placing new market order for '
                                                     f'{trading_pair}')
                return response
            else:
                self.log_manager.error(f'Error placing market order: {ex}')

        return None  # Return None

    # <><><><><><><><><><><><><><><>NOT YET IMPLEMENTED in CCXT 07/26/2024 <>><><><><><><><><><><><><><><><><><><><><><><>

    async def place_trailing_stop_order(self, order_data, market_price):
        """
        Places a trailing stop order. Returns the API response as a dictionary.
        """
        try:

            client_order_id = str(uuid.uuid4())
            trailing_percentage = self.trailing_percentage

            # Calculate the trailing stop price
            trailing_price = market_price * (1 - trailing_percentage / 100) \
                if order_data['side'].upper() == 'SELL' else market_price * (1 + trailing_percentage / 100)

            # Adjust the stop and limit prices
            if order_data['side'].upper() == 'SELL':
                stop_price = trailing_price
                limit_price = trailing_price * Decimal('0.999')  # Slightly lower than stop price
            else:
                stop_price = trailing_price
                limit_price = trailing_price * Decimal('1.001')  # Slightly higher than stop price

            stop_price = self.utils.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], stop_price, convert='quote')
            limit_price = self.utils.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], limit_price, convert='quote')

            symbol = order_data['trading_pair'].replace('/', '-')
            base_size = order_data.get('adjusted_size', order_data['base_balance'])
            print(f"Base size: {base_size}")  # debug
            payload = {
                "client_order_id": client_order_id,
                "product_id": symbol,
                "side": "SELL" if order_data['side'].upper() == 'SELL' else "BUY",
                "order_configuration": {
                    "stop_limit_stop_limit_gtd": {
                        "base_size": str(base_size),
                        "stop_price": str(stop_price),
                        "limit_price": str(limit_price),
                        "end_time": (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "stop_direction": "STOP_DIRECTION_STOP_DOWN" if order_data['side'].upper() == 'SELL'
                        else "STOP_DIRECTION_STOP_UP"
                    }
                }
            }

            response = await self.coinbase_api.create_order(payload)
            if 'INSUFFICIENT_FUND' in response:
               pass
            return response, market_price, trailing_price

        except Exception as ex:
            self.log_manager.error(f"Error in place_trailing_stop_order: {str(ex)}", exc_info=True)
            return None

    # Helper method for bracket order placement
    async def _handle_bracket_order(self, order_data, order_book_details):
        try:
            currencies = [order_data['trading_pair'].split('/')[0], order_data['trading_pair'].split('/')[1]]
            accounts = await self.utils.get_account_balance(currencies, get_staked=False)
            quote_balance, base_balance, _, open_order = await self.utils.get_open_orders(order_data)
            if not open_order:
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
            end_time = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')

            # Adjust price and size
            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(order_data, order_book)

            if adjusted_price is None or adjusted_size is None:
                self.log_manager.error("Failed to adjust price or size.")
                return None
            symbol = order_data['trading_pair'].replace('/', '-')

            # Adjust limit and stop prices to the correct precision
            limit_price = self.utils.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
                                                      adjusted_price, 'quote')
            stop_trigger_price = self.utils.adjust_precision(order_data['base_decimal'], order_data['quote_decimal'],
                                                             order_data['stop_loss_price'], 'quote')
            # Ensure limit price is within bounds
            market_price = Decimal(order_data['adjusted_price'])
            min_price = market_price * Decimal('0.95')
            max_price = market_price * Decimal('1.05')

            if limit_price < min_price or limit_price > max_price:
                self.log_manager.error(
                    f"Limit price {limit_price} is out of bounds (min: {min_price}, max: {max_price}).")
                return None
            adjust_precision_take_profit = self.utils.adjust_precision(order_data['base_decimal'], order_data[
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
