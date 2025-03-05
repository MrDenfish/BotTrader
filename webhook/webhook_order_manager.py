from Api_manager.api_exceptions import CoinbaseAPIError
from Shared_Utils.config_manager import CentralConfig as config
from decimal import Decimal, getcontext
import pandas as pd
import time
import requests
import json
import traceback

# Define the TradeOrderManager class
"""This class  will manage the trade orders."""


class TradeOrderManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api=None, exchange_client=None, shared_utils_precision=None,
                     validate=None, logmanager=None, alerts=None, ccxt_api=None,
                     order_book_manager=None, order_types=None, websocket_helper=None, session=None, market_data=None):
        """
        Singleton method to ensure only one instance of TradeOrderManager exists.
        If already instantiated, returns the existing instance.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, validate, logmanager, alerts,
                                ccxt_api, order_book_manager, order_types, websocket_helper, session, market_data)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, validate, logmanager,
                 alerts, ccxt_api, order_book_manager, order_types, websocket_helper, session, market_data):
        """
        Initializes the TradeOrderManager.
        """
        self.config = config()
        self.coinbase_api = coinbase_api
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._order_size = self.config.order_size
        self._hodl = self.config.hodl
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.validate = validate
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.market_data = market_data
        self.shared_utils_precision = shared_utils_precision
        self.session = session

    # def set_trade_parameters(self, market_data, order_management, start_time=None):
    #
    #     self.start_time = start_time
    #     self.market_data = market_data
    #     self.order_management = order_management
    #     self.ticker_cache = market_data.get('ticker_cache')
    #     self.non_zero_balances = order_management.get('non_zero_balances', {})
    #     self.order_tracker = order_management.get('order_tracker', {})
    #     self.market_cache_usd = market_data['usd_pairs_cache']
    #     self.market_cache_vol = market_data['filtered_vol']

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
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def order_size(self):
        return float(self._order_size)

    async def place_order(self, order_details, precision_data):
        try:
            all_open_orders, has_open_order,_ = await self.websocket_helper.refresh_open_orders(
                                                trading_pair=order_details['trading_pair'], order_data=order_details
            )
            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()

            if not self.validate_order_conditions(order_details, open_orders) or has_open_order:
                return False

            order_book_details = await self.order_book_manager.get_order_book(order_details)
            validate_data = self.build_validate_data(order_details, open_orders, order_book_details)

            base_coin_balance, valid_order, condition = self.validate.fetch_and_validate_rules(validate_data)
            if not valid_order:
                return False
            event_type = 'webhook'
            return await self.handle_order(validate_data, order_book_details, precision_data)


        except Exception as ex:
            self.log_manager.error(ex, exc_info=True)
            return False

    def validate_order_conditions(self, order_details, open_orders):
        """
        Validates order conditions based on account balances and active open orders.

        Args:
        - order_data (dict): Data about the order to validate.
        - quote_bal (float): Current balance of the quote currency.
        - base_balance (float): Current balance of the base currency.
        - open_orders (DataFrame): DataFrame of active open orders.

        Returns:
        - bool: True if conditions for the order are met, False otherwise.
        """

        side = order_details.get('side')
        quote_amount = order_details.get('quote_amount', 0)
        quote_bal = order_details.get('quote_balance', 0)
        base_balance = order_details.get('base_balance', 0)
        symbol = order_details.get('trading_pair', '').replace('/', '-')
        trailing_stop_active = False  # Flag for trailing stop orders

        try:
            # First check if there are any open orders at all
            if open_orders.empty or 'product_id' not in open_orders.columns:
                # No open orders; check balance conditions for buy/sell actions
                if side == 'buy':
                    if quote_bal < quote_amount:
                        self.log_manager.info(
                            f"Insufficient quote balance to buy {symbol}. Required: {quote_amount}, Available: {quote_bal}"
                        )
                        return False
                    return True
                elif side == 'sell':
                    if base_balance <= 0:
                        return False
                    return True
                else:
                    self.log_manager.error(f"Unknown order side: {side}")
                    return False

            # Check if any open orders exist for the specific symbol
            if symbol not in open_orders['product_id'].values:
                # No matching open orders; proceed with balance checks as above
                if side == 'buy':
                    if quote_bal < quote_amount:
                        self.log_manager.info(
                            f"Insufficient quote balance to buy {symbol}. Required: {quote_amount}, Available: {quote_bal}"
                        )
                        return False
                    return True
                elif side == 'sell':
                    if base_balance <= 0:
                        return False
                    return True

            # Check open orders for trailing stop conditions if any exist for the symbol
            if side == 'sell':
                # Filter open orders to check for an active trailing stop for the symbol
                trailing_stop_orders = open_orders[
                    (open_orders['product_id'] == symbol) &
                    (open_orders['trigger_status'] == 'STOP_PENDING')
                    ]

                trailing_stop_active = not trailing_stop_orders.empty
                if trailing_stop_active:
                    self.log_manager.info(f"Active trailing stop order found for {symbol}.")
                    return True
                elif base_balance <= 0:
                    return False

            # No conditions met for order execution
            return False

        except KeyError as e:
            self.log_manager.error(f"KeyError: Missing key in order_data or open_orders: {e}", exc_info=True)
            return False
        except Exception as e:
            self.log_manager.error(f"Error validating order condition: {e}", exc_info=True)
            return False

        except KeyError as e:
            self.log_manager.error(f"KeyError: Missing key in order_data or open_orders: {e}", exc_info=True)
            return False
        except Exception as e:
            self.log_manager.error(f"Error validating order condition: {e}", exc_info=True)
            return False

    def build_validate_data(self, order_details, open_orders, order_book_details):

        return {
            **order_details,
            'base_balance_free': order_details['base_balance'],
            'quote_balance': order_details['quote_balance'],
            'highest_bid': order_book_details['highest_bid'],
            'lowest_ask': order_book_details['lowest_ask'],
            'spread': order_book_details['spread'],
            'open_orders': open_orders
        }

    # async def handle_order(self, validate_data, order_book_details, precision_data):
    #     try:
    #         take_profit_price = None
    #         highest_bid = Decimal(order_book_details['highest_bid'])
    #         lowest_ask = Decimal(order_book_details['lowest_ask'])
    #         spread = Decimal(order_book_details['spread'])
    #         base_deci, quote_deci, _, _ = precision_data
    #         adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(validate_data, order_book_details)
    #
    #         self.log_manager.debug(f"Adjusted price: {adjusted_price}, Adjusted size: {adjusted_size}")
    #
    #         # Calculate take profit and stop loss prices
    #         if validate_data['side'] == 'buy':
    #             take_profit_price = adjusted_price * (1 + self.take_profit)
    #             adjusted_take_profit_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci,
    #                                                                                       take_profit_price, convert='quote')
    #             stop_loss_price = adjusted_price * (1 + self.stop_loss)
    #         else:  # side == 'sell'
    #             take_profit_price = adjusted_price * (1 + self.take_profit)
    #             adjusted_take_profit_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci,
    #                                                                                       take_profit_price, convert='quote')
    #
    #             stop_loss_price = adjusted_price * (1 + self.stop_loss)
    #
    #
    #         adjusted_stop_loss_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, stop_loss_price,
    #                                                                convert='quote')
    #         adjusted_size = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_size,
    #                                                     convert='base')
    #         order_data = {
    #             **validate_data,
    #             'adjusted_price': adjusted_price,
    #             'adjusted_size': adjusted_size,
    #             'trading_pair': validate_data['trading_pair'],
    #             'side': validate_data['side'],
    #             'stop_loss_price': adjusted_stop_loss_price,
    #             'usd_available': validate_data['quote_balance'],
    #             'take_profit_price': adjusted_take_profit_price,
    #
    #         }
    #
    #         # Decide whether to place a bracket order or a trailing stop order
    #         if self.should_use_trailing_stop(adjusted_price, highest_bid, lowest_ask):
    #             return await self.attempt_order_placement(validate_data, order_data, order_type='trailing_stop')
    #         else:
    #             return await self.attempt_order_placement(validate_data, order_data, order_type='bracket')
    #     except Exception as ex:
    #         self.log_manager.debug(ex)
    #         return False
    #     except Exception as ex:
    #         self.log_manager.debug(ex)
    #         return False
    async def handle_order(self, validate_data, order_book_details, precision_data):
        try:
            # Extract key data
            asset = validate_data.get("base_currency", "")
            product_id = validate_data.get("trading_pair", "").replace("/", "-")  # Format to match build_order_data

            # ✅ Fetch order data using `build_order_data`
            order_data = await self.build_order_data(asset, product_id)
            if not order_data:
                self.log_manager.error(f"❌ Failed to build order data for {product_id}. Order not placed.")
                return False

            # ✅ Ensure critical fields are present
            adjusted_price = Decimal(order_data.get('lowest_ask', 0))
            adjusted_size = Decimal(order_data.get('adjusted_size', 0))
            highest_bid = Decimal(order_book_details['highest_bid'])
            lowest_ask = Decimal(order_book_details['lowest_ask'])
            spread = Decimal(order_book_details['spread'])
            base_deci, quote_deci, _, _ = precision_data

            # ✅ Calculate take profit and stop loss prices
            take_profit_price = adjusted_price * (1 + self.take_profit)
            adjusted_take_profit_price = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, take_profit_price, convert='quote'
            )

            stop_loss_price = adjusted_price * (1 + self.stop_loss)
            adjusted_stop_loss_price = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, stop_loss_price, convert='quote'
            )

            # ✅ Update `order_data` with additional parameters
            order_data.update({
                'stop_loss_price': adjusted_stop_loss_price,
                'take_profit_price': adjusted_take_profit_price,
                'usd_available': validate_data.get('quote_balance', 0),
                'quote_decimal': quote_deci
            })

            self.log_manager.debug(f"� Final Order Data: {order_data}")

            # ✅ Decide order type and place order
            if self.should_use_trailing_stop(adjusted_price, highest_bid, lowest_ask):
                return await self.attempt_order_placement(validate_data, order_data, order_type='trailing_stop')
            else:
                return await self.attempt_order_placement(validate_data, order_data, order_type='bracket')

        except Exception as ex:
            self.log_manager.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False

    def should_use_trailing_stop(self, adjusted_price, highest_bid, lowest_ask):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.

        # Placeholder logic:
        return True # while developing
        # return adjusted_price > (highest_bid + lowest_ask) / 2

    async def attempt_order_placement(self, validate_data, order_data, order_type):
        """
        Attempts to place different types of orders (limit, bracket, trailing stop) based on the order type specified.
        If the order is rejected with a return value of 'amend', the function adjusts the order and retries the placement.
        Returns a tuple (bool, dict/None), where bool indicates success, and dict contains the response or error.
        """
        try:
            response = None  # Initialize response to avoid UnboundLocalError
            max_attempts = 5
            attempt = 0

            while attempt < max_attempts:
                attempt += 1
                try:
                    order_book = await self.order_book_manager.get_order_book(order_data)

                    highest_bid = Decimal(order_book['highest_bid'])
                    # Adjust price for post-only orders
                    if order_data['side'] == 'buy':
                        order_price = min(order_book['highest_bid'], order_book['lowest_ask'] - Decimal('0.0001'))
                    else:
                        order_price = max(order_book['highest_bid'], order_book['lowest_ask'] + Decimal('0.0001'))

                    if order_data['side'] == 'buy':
                        response = await self.order_types.place_limit_order(order_data)
                        print(f"Attempt # {attempt} "
                              f"{order_data['trading_pair']}: Adjusted stop price: {order_data['adjusted_price']}, "
                              f"highest bid price {highest_bid}")  # debug
                    elif order_type == 'bracket':
                        response, market_price, trailing_price = await self.order_types._handle_bracket_order(order_data, order_book)
                    elif order_type == 'trailing_stop':
                        print(f"Placing trailing stop order for {order_data['trading_pair']}:  order data stop-loss price: "
                              f"{order_data.get('stop_loss_price')}, highest bid: {highest_bid}")  # debug
                        response = await self.order_types.place_trailing_stop_order(order_book, order_data, order_data.get
                        ('highest_bid'))
                    elif order_data['side'] == 'sell':
                        response = await self.order_types.place_limit_order(order_data)
                        print(f"Attempt # {attempt} "
                              f"{order_data['trading_pair']}: Adjusted stop price: {order_data['adjusted_price']}, "
                              f"highest bid price {highest_bid}")  # debug
                    else:
                        raise ValueError("Unknown order type specified")

                    # Process the response based on its type and content
                    if isinstance(response, dict):
                        error_response = response.get('error_response', {})

                        if error_response.get(
                                'message') == 'amend' or 'Too many decimals in order price' in error_response.get('message',
                                                                                                                  ''):
                            self.log_manager.info(
                                f"Order amendment required, adjusting order (Attempt {attempt}/{max_attempts})")
                            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = self.shared_utils_precision.adjust_precision(
                                order_data['base_decimal'], order_data['quote_decimal'], adjusted_price, convert='quote')
                            order_data['adjusted_size'] = adjusted_size
                            continue  # Retry the loop with the adjusted order

                        elif 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in error_response.get('preview_failure_reason', ''):
                            self.log_manager.info(
                                f"Stop price below last trade price, adjusting order (Attempt {attempt}/{max_attempts})")
                            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = adjusted_price * Decimal(
                                '1.0002')  # Small increment to move above last trade price
                            continue  # Retry the loop with the adjusted order

                        elif len(response.get('id') ) > 0:
                            self.log_manager.buy(f"{order_data['trading_pair']}, "
                                                                 f"{order_data['adjusted_price']}, {order_data['side']}")
                            return True, response

                        else:
                            self.log_manager.error(f"Unexpected response format: {response}", exc_info=True)
                            return False, response

                except Exception as ex:
                    self.log_manager.error(f"Error during attempt #{attempt}: {str(ex)}", exc_info=True)
                    if attempt >= max_attempts:
                        break  # Exit the loop if the maximum attempts are reached

            # Handle the case where all attempts have been exhausted
            self.log_manager.info(f"Order placement ultimately failed after {max_attempts} attempts.")
            return False, response

        except Exception as ex:
            self.log_manager.error(f"Error in attempt_order_placement: {str(ex)}", exc_info=True)
            return False, None

    async def build_order_data(self, asset, product_id):
        """
        Constructs order data for placing a limit order.

        Args:
            asset (str): Base asset (e.g., "BTC").
            product_id (str): Trading pair in the format "BASE-QUOTE" (e.g., "BTC-USD").

        Returns:
            dict: Prepared order data for further processing.
        """
        try:
            # Fetch market data
            spot_position = self.market_data.get('spot_positions', {})
            usd_pairs = self.market_data.get('usd_pairs_cache', {})

            # Fetch precision details
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)

            # Get available balance
            balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
            cryto_avail_to_trade = spot_position.get(asset,{}).get('available_to_trade_crypto',0)
            if cryto_avail_to_trade ==0:
                side='BUY'
            else:
                side='SELL'

            usd_bal = float(spot_position.get('USD', {}).get('available_to_trade_fiat', 0))

            # Convert trading pair format
            trading_pair = product_id.replace('-', '/')

            # Get latest price and calculate potential order size
            price = float(self.market_data.get('current_prices', {}).get(trading_pair, 0))
            fiat_avail = float(min(self.order_size, usd_bal))
            size = round(fiat_avail / price, 8) if price > 0 else 0  # Prevent division by zero

            # Prepare initial order data
            order_data = {
                'quote_decimal': quote_deci,
                'base_decimal': base_deci,
                'trading_pair': trading_pair,
            }

            # Fetch order book data
            order_book = await self.order_book_manager.get_order_book(order_data)

            # Temporary order details for price/size adjustments
            temp_order = {
                'side': side,
                'base_balance': balance,
                'quote_amount': fiat_avail,
                'quote_decimal': quote_deci,
            }
            temp_book = {
                'highest_bid': float(order_book.get('highest_bid', 0)),
                'lowest_ask': float(order_book.get('lowest_ask', 0)),
            }

            # Adjust price and size
            adjusted_bid, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book)
            adjusted_bid = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_bid, 'quote')
            adjusted_ask, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book)
            adjusted_ask = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_ask, 'quote')

            adjusted_size = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, size, 'base')

            # Final order data
            order_data = {
                'lowest_ask': float(adjusted_ask),
                'highest_bid': float(adjusted_bid),
                'adjusted_size': float(adjusted_size),
                'trading_pair': trading_pair,
                'usd_available': usd_bal,
                'base_balance': balance,
                'side': side,
                'quote_decimal': quote_deci,
                'base_decimal': base_deci,
                'status_of_order': 'STOP_LIMIT/BUY/ROC'
            }

            return order_data

        except Exception as e:
            self.log_manager.error(f"Error in build_order_data: {e}", exc_info=True)
            return None
