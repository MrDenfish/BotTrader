from custom_exceptions import CoinbaseAPIError
import asyncio
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

import socket
import pandas as pd
import math

# Define the TradeBotUtils class


class TradeBotUtils:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, botconfig, logmanager, coinbase_api, exchange_client, ccxt_api, alerts,order_tracker):
        if cls._instance is None:
            cls._instance = cls(botconfig, logmanager, coinbase_api, exchange_client, ccxt_api, alerts,order_tracker)

        return cls._instance

    def __init__(self, botconfig, logmanager, coinbase_api, exchange_client, ccxt_api, alerts, order_tracker):
        if TradeBotUtils._instance is not None:
            raise Exception("This class is a singleton!")
        TradeBotUtils._instance = self
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        self.bot_config = botconfig
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts
        self.order_tracker = order_tracker

    def refresh_authentication(self):
        try:
            # Reload the configuration
            self.bot_config.reload_config()

            # Fetch new API key and secret from BotConfig
            new_api_key = self.bot_config.api_key
            new_api_secret = self.bot_config.api_secret

            # Update the exchange client with new credentials
            if new_api_key and new_api_secret:
                self.exchange.apiKey = new_api_key
                self.exchange.secret = new_api_secret

                # Log the refresh action
                if self.log_manager and hasattr(self.log_manager, 'webhook_logger'):
                    self.log_manager.info("Authentication refreshed.")
                else:
                    print("Authentication refreshed.")  # Fallback logging
            else:
                raise ValueError("API key or secret is missing.")
        except Exception as e:
            error_message = f"Failed to refresh authentication: {e}"
            if self.log_manager and hasattr(self.log_manager, 'webhook_logger'):
                self.log_manager.error(error_message)
            else:
                print(error_message)  # Fallback logging

    @staticmethod
    def get_my_ip_address():
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address

    async def fetch_precision(self, symbol: str) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for.
        :return: A tuple containing base and quote decimal places.
        """
        try:
            markets = []
            endpoint = 'public'  # for rate limiting
            params = {
                'offset': 0,  # Skip the first 0 items
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 10,  # Set the max number of pagination calls if necessary
                'limit': 1000  # Set the max number of items to return
            }
            # Run the synchronous fetch_markets method in the default executor
            markets = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets, endpoint, params=params)
            if markets is None:
                raise ValueError("Failed to fetch markets.")

            for market in markets:
                if market['symbol'] == symbol:
                    base_precision = float(market['precision']['amount'])  # float
                    quote_precision = float(market['precision']['price'])  # float
                    base_increment = market['info']['base_increment']  # string
                    quote_increment = market['info']['quote_increment']  # string

                    if base_precision == 0 or quote_precision == 0:
                        raise ValueError("Precision value is zero, which may cause a division error.")
                    base_decimal_places = -int(math.log10(base_precision))
                    quote_decimal_places = -int(math.log10(quote_precision))
                    # Check for negative decimal places
                    if base_decimal_places < 0:
                        raise ValueError("Base decimal places cannot be negative.")

                    return base_decimal_places, quote_decimal_places, base_increment, quote_increment
        except self.exchange.NetworkError as e:
            self.log_manager.error(f"Network issue when fetching markets: {e}")
        except self.exchange.ExchangeError as e:
            self.log_manager.error(f"Exchange issue encountered: {e}")
        except Exception as e:
            self.log_manager.error(f"Unexpected error in fetch_precision: {e}", exc_info=True)

        return None, None, None, None  # Default return on error

    async def get_open_orders(self, order_data):
        base_balance = Decimal(0)
        quote_bal = Decimal(0)
        open_order = []
        try:
            endpoint = 'private'
            params = {
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 10  # Set the max number of pagination calls if necessary
            }
            fetched_open_orders = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_open_orders, endpoint,
                                                                           params=params)

            # Ensure format_open_orders returns a DataFrame
            all_open_orders = await self.format_open_orders(fetched_open_orders) if fetched_open_orders else pd.DataFrame()
            coin = self.get_symbol_or_trading_pair(order_data)
            base_currency = coin.split('/')[0]
            quote_currency = coin.split('/')[1]
            accounts, balances = await self.get_account_balance([base_currency, quote_currency])

            if None in accounts.values():
                self.log_manager.warning(
                    'None values detected in balances. Refreshing authentication and retrying.')
                self.refresh_authentication()
                accounts, balances = await self.get_account_balance([base_currency, quote_currency])

            base_value = accounts.get(order_data['trading_pair'].split('/')[0], 0)
            base_balance = self.float_to_decimal(base_value['free'], order_data['base_decimal']) \
                if base_value is not None else (Decimal(0))
            quote_value = accounts.get(order_data['quote_currency'], 0)
            quote_balance = self.float_to_decimal(quote_value['free'], order_data['quote_decimal']) \
                if (quote_value is not None) else (Decimal(0))

            # Check if all_open_orders DataFrame is empty
            if all_open_orders.empty or all_open_orders is None:
                self.log_manager.debug(
                    f'get_open_orders: No open orders found for {order_data["trading_pair"]}. Coin Balance: '
                    f'{base_balance}', exc_info=True)

                return quote_balance, base_balance, pd.DataFrame(), open_order  # Return an empty DataFrame instead of None
            else:
                symbol = order_data['trading_pair'].replace('/', '-')
                open_order = symbol in all_open_orders['product_id'].values
            return quote_balance, base_balance, all_open_orders, open_order
        except CoinbaseAPIError as e:
            self.log_manager.error(f'get_open_orders: Coinbase API Error occurred: {e}', exc_info=True)
            return None, None, None, None
        except Exception as e:  # Basic but it does not seem to catch errors
            self.log_manager.exception(f'get_open_orders: Error occurred: {e} {order_data}', exc_info=True)
            return None, None, None, None

    async def check_order_status(self, symbol, order_id):
        """
        Check the status of an order using the order_id.
        Returns the status as a string (e.g., 'OPEN', 'FILLED', 'CANCELED').
        """
        try:
            # Extract order details

            endpoint = 'private'

            # Fetch detailed order information
            order_info = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_order, endpoint, order_id, symbol)

            if order_info:
                return order_info.get('status', 'UNKNOWN')  # return raw order info if available
            else:
                print(f"Order {order_id} not found.")
                return 'NOT_FOUND'
        except Exception as e:
            print(f"Error checking order status for {order_id}: {e}")
            return 'ERROR'

    async def check_funds(self, required_amount):
        account_balance = await self.coinbase_api.get_account_balance()  # Implement this method to fetch account balance
        if account_balance >= required_amount:
            return True
        else:
            print(f"Insufficient funds. Required: {required_amount}, Available: {account_balance}")
            return False


    @staticmethod
    def get_symbol_or_trading_pair(order):
        # Check if 'symbol' is in the dictionary (for nested structures)
        if isinstance(order, dict):
            for key, value in order.items():
                if isinstance(value, dict):
                    if 'symbol' in value:
                        return value['symbol']
                elif key == 'trading_pair':
                    return order['trading_pair']

        # If 'symbol' or 'trading_pair' was not found at this point, raise an exception or return None
        return None

    @staticmethod
    async def format_open_orders(open_orders: list) -> pd.DataFrame:
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.
        Parameters:
        Returns:
        - list: A list of dictionaries containing the required data.
        """

        data_to_load = [{
                'order_id': order['id'],
                'product_id': order['info']['product_id'],
                'side': order['info']['side'],
                'size': order['amount'],
                'price': order['price'],
                'trigger_status': order['info']['trigger_status'],
                'trigger_price': order['triggerPrice'],
                'stop_price': order['stopPrice'],
                'filled': order['filled'],
                'remaining': order['remaining'],
                'time active': order['info']['created_time']
        } for order in open_orders]
        df = pd.DataFrame(data_to_load)

        return df

    async def fetch_wallets(self):
        """fetch wallet holdings and available balance."""

        endpoint = 'private'
        params = {'paginate': True, 'paginationCalls': 100}
        wallets = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_accounts, endpoint, params)
        filtered_wallets = self.filter_non_zero_wallets(wallets)
        return filtered_wallets

    def filter_non_zero_wallets(self, wallets):
        try:
            non_zero_wallets = []
            for wallet in wallets:
                if wallet['code'] == 'BCH':  # Skip BCH wallet for now
                    pass
                available_balance = Decimal(wallet['info']['available_balance']['value'])
                hold_balance = Decimal(wallet['info']['hold']['value'])
                total_balance = available_balance + hold_balance
                if total_balance > 0:
                    non_zero_wallets.append(wallet)
            return non_zero_wallets
        except Exception as e:
            self.log_manager.sighook_logger.error(f'filter_non_zero_wallets: {e}', exc_info=True)

    async def get_account_balance(self, currencies, get_staked=False):

        balances = {}
        accounts = None
        try:
            endpoint = 'private'  # for rate limiting
            params = {
                'offset': 0,  # Skip the first 0 items
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 50,  # Set the max number of pagination calls if necessary
                'limit': 300  # Set the max number of items to return
            }
            accounts = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance, endpoint, params=params)
            wallets = await self.fetch_wallets()
            if get_staked:
                return accounts, wallets  # Return the full accounts object
            # Check if accounts is None
            if wallets is None:
                self.log_manager.error('get_account_balance: Failed to fetch accounts', exc_info=True)
                return accounts, {currency: None for currency in currencies}

            for wallet in wallets:
                if currencies[0] in wallet['code']:
                    balances = {'asset': currencies[0],
                                'free': float(wallet['info']['available_balance']['value']),
                                'hold': float(wallet['info']['hold']['value'])}
                    break
                else:
                    balances = None
                    self.log_manager.debug(f'get_account_balance: {wallet} not found in accounts')
            # print(f'balances: {balances}')
            return accounts, balances
        except Exception as e:
            my_ip = self.get_my_ip_address()
            self.log_manager.debug(
                f'get_account_balance: Exception occurred during API call from IP {my_ip}: {e}', exc_info=True)
            return accounts, {currency: None for currency in currencies}

    def float_to_decimal(self, value: float, decimal_places: int) -> Decimal:
        """
        Convert a float to a Decimal with a specified number of decimal places.
        """
        try:
            # Construct a string representing the desired decimal format
            decimal_format = '0.' + '0' * decimal_places if decimal_places > 0 else '0'

            # Convert the float to a Decimal
            value_decimal = Decimal(str(value))

            # Quantize the Decimal to the desired number of decimal places
            value_decimal = value_decimal.quantize(Decimal(decimal_format), rounding=ROUND_DOWN)

            return value_decimal
        except Exception as e:
            self.log_manager.error(f'float_to_decimal: An error occurred: {e}. Value: {value},'
                                                  f'Decimal places: {decimal_places}', exc_info=True)
            raise

    async def fetch_spot(self, symbol):
        try:
            endpoint = 'public'
            ticker = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, endpoint, symbol)
            if ticker is None or 'last' not in ticker:
                self.log_manager.error(f"Failed to fetch ticker or 'last' price missing for {symbol}")
                return None
            # Extract the spot price (last price)
            spot_price = ticker['last']
            self.log_manager.debug(f'fetch_spot: {symbol} spot price: {spot_price}')
            return spot_price
        except asyncio.CancelledError:
            self.log_manager.error(f"fetch_ticker: Task was cancelled for {symbol}")
        except Exception as ex:
            self.log_manager.error(f'fetch_spot: Error fetching ticker {symbol}: {ex}', exc_info=True)

    def adjust_precision(self, base_deci, quote_deci, num_to_adjust, convert):

        """"" general utility method that can be used throughout the application to ensure any numeric value is adjusted to
        the correct precision Adjust the amount based on the number of decimal places required for the symbol.
         base_deci and quote_deci are determined by the symbol presicion from markets and is the number of decimal places
         for the currency used in a particular market.  For example, for BTC/USD, base_deci is 8 and quote_deci is 2."""
        try:
            if convert == 'base':
                decimal_places = base_deci
            elif convert == 'usd':
                decimal_places = 2
            elif convert == 'quote':
                decimal_places = quote_deci
            else:
                decimal_places = 8
            adjusted_precision = self.float_to_decimal(num_to_adjust, decimal_places)

            return adjusted_precision
        except Exception as e:
            self.log_manager.error(f'adjust_precision: An error occurred: {e}')
            return None

    def adjust_price_and_size(self, order_data, order_book, response=None) -> tuple[Decimal, Decimal]:
        try:
            side = order_data['side'].upper()

            if side == 'SELL':
                adjusted_price = Decimal(order_book['highest_bid'])
                adjusted_size = Decimal(order_data.get('base_balance', 0))
            elif side == 'BUY':
                adjusted_price = Decimal(order_book['lowest_ask'])
                quote_amount = Decimal(order_data.get('quote_amount', 0))
                if adjusted_price == 0:
                    raise ValueError("Adjusted price cannot be zero for BUY order.")
                adjusted_size = quote_amount / adjusted_price
            else:
                raise ValueError(f"Unsupported side: {side}")

            # Capture best bid/ask prices
            best_bid_price = Decimal(order_book['highest_bid'])
            best_ask_price = Decimal(order_book['lowest_ask'])
            spread = best_ask_price - best_bid_price

            # Dynamic adjustment factor based on a percentage of the spread
            adjustment_percentage = Decimal('0.0015')  # 0.2%
            adjustment_factor = spread * adjustment_percentage

            # Ensure the adjustment is significant given the currency's precision
            precision_str = '1e-{}'.format(order_data.get('quote_decimal', 2))
            adjustment_factor = max(adjustment_factor, Decimal(precision_str))
            print(f'Calculated adjustment_factor: {adjustment_factor}')

            # Apply the adjustment factor depending on the side
            if side == 'BUY':
                adjusted_price += adjustment_factor  # Slightly increase the buy price
            elif side == 'SELL':
                adjusted_price -= adjustment_factor  # Slightly decrease the sell price

            return adjusted_price, adjusted_size
        except Exception as e:
            self.log_manager.error(f'adjust_price_and_size: An error occurred: {e}', exc_info=True)
            return None, None

    @staticmethod
    def get_decimal_format(base_decimal: int) -> Decimal:
        """
        Generate a Decimal format string based on the number of decimal places.

        :param base_decimal: The number of decimal places for the base value.
        :return: A Decimal object representing the format.
        """
        if base_decimal < 0:
            raise ValueError("base_decimal must be a positive integer")

        decimal_format = '0.' + ('0' * (base_decimal - 1)) + '1'
        return Decimal(decimal_format)  # example 0.00000001

    @staticmethod
    def convert_timestamp_to_datetime(timestamp_ms):
        # Divide by 1000 to convert milliseconds to seconds
        timestamp_s = float(timestamp_ms) / 1000.0
        # Create datetime object from timestamp
        dt = datetime.fromtimestamp(timestamp_s)
        return dt
