from Api_manager.api_exceptions import CoinbaseAPIError
from Shared_Utils.config_manager import CentralConfig as config
from decimal import Decimal, getcontext, ROUND_HALF_UP
from typing import Dict, List, Tuple, Union
import asyncio
import ccxt
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
    def get_instance(cls, coinbase_api=None, exchange_client=None, shared_utils_precision=None, validate=None,
                     logmanager=None, alerts=None, ccxt_api=None, order_book_manager=None, order_types=None, websocket_helper=None,
                     session=None, market_data=None):
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
        self._min_sell_value = self.config.min_sell_value
        self._max_value_to_buy = self.config.max_value_to_buy
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
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.market_data = market_data
        self.shared_utils_precision = shared_utils_precision
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
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def max_value_to_buy(self):
        return self._max_value_to_buy

    @property
    def order_size(self):
        return float(self._order_size)

    async def place_order(self, order_details, precision_data):
        try:
            all_open_orders, has_open_order,_ = await self.websocket_helper.refresh_open_orders(
                                                trading_pair=order_details['trading_pair'], order_data=order_details
            )
            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()

            # if order conditions are met True else False
            if not self.validate.validate_order_conditions(order_details, open_orders) or has_open_order:
                return False

            order_book_details = await self.order_book_manager.get_order_book(order_details)
            validate_data = self.validate.build_validate_data(order_details, open_orders, order_book_details)

            base_coin_balance, valid_order, condition = self.validate.fetch_and_validate_rules(validate_data)
            if not valid_order:
                return False,
            event_type = 'webhook'
            return await self.handle_order(validate_data, order_book_details, precision_data)


        except Exception as ex:
            self.log_manager.error(ex, exc_info=True)
            return False

    async def handle_order(self, validate_data, order_book_details, precision_data):
        try:
            # Extract key data
            asset = validate_data.get("base_currency", "")
            product_id = validate_data.get("trading_pair", "").replace("/", "-")  # Format to match build_order_data

            # ✅ Fetch order data using `build_order_data`
            order_data = await self.build_order_data(asset, product_id)
            if not order_data:
                self.log_manager.error(f"❌ Failed to build order data for {product_id}. Order not placed.")
                return False, False

            # ✅ Ensure critical fields are present
            highest_bid = Decimal(order_book_details['highest_bid'])
            lowest_ask = Decimal(order_book_details['lowest_ask'])
            spread = Decimal(order_book_details['spread'])
            base_deci, quote_deci, _, _ = precision_data

            # ✅ Calculate adjusted price
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book_details)

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
            order_data.update(
                {
                    'adjusted_price': adjusted_price,
                    'adjusted_size': adjusted_size,  # Now properly quantized
                    'stop_loss_price': adjusted_stop_loss_price,
                    'take_profit_price': adjusted_take_profit_price,
                    'usd_available': validate_data.get('quote_balance', 0),
                    'quote_decimal': quote_deci
                }
            )

            print(f"✅ Final Order Data: {order_data}")

            # ✅ Decide order type and place order
            order_type = self.order_type_to_use(order_data, adjusted_price, highest_bid, lowest_ask)
            response = await self.attempt_order_placement(validate_data, order_data, order_type=order_type)

            return response

        except Exception as ex:
            self.log_manager.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False

    def order_type_to_use(self, order_data, adjusted_price, highest_bid, lowest_ask):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.
        order_type = 'limit'

        if order_data.get('side') == 'buy':
            order_type = 'tp_sl'
            return  order_type
        elif order_data.get('side') == 'sell':
            order_type = 'tp_sl'
            return order_type

    async def attempt_order_placement(self, validate_data, order_data, order_type):
        """
        Attempts to place different types of orders (limit, bracket, trailing stop) based on the order type specified.
        If the order is rejected with a return value of 'amend', the function adjusts the order and retries the placement.

        Args:
            validate_data (dict): Data validated against trading rules.
            order_data (dict): Contains order details (size, price, trading pair, etc.).
            order_type (str): Type of order ('limit', 'bracket', 'trailing_stop').

        Returns:
            tuple: (bool, response) where bool indicates success, and response contains the order result or error.
        """
        try:
            response = None  # Initialize response to avoid UnboundLocalError
            max_attempts = 5
            attempt = 0

            while attempt < max_attempts:
                attempt += 1
                try:
                    # ✅ Refresh order book to ensure latest bid/ask prices
                    order_book = await self.order_book_manager.get_order_book(order_data)
                    highest_bid = Decimal(order_book['highest_bid'])
                    lowest_ask = Decimal(order_book['lowest_ask'])

                    # ✅ Adjust price dynamically to avoid post-only rejection
                    if order_data['side'].lower() == 'buy':
                        order_price = min(highest_bid, lowest_ask - Decimal('0.0001'))
                    else:
                        order_price = max(highest_bid, lowest_ask + Decimal('0.0001'))

                    order_data.update({'adjusted_price': order_price})

                    # ✅ Choose the appropriate order execution method
                    if order_type == 'limit':
                        response, _, _ = await self.order_types.process_limit_and_tp_sl_orders("Webhook", order_data)

                    elif order_type == 'tp_sl':
                        tp = order_price * Decimal('1.02')
                        sl = order_price * Decimal('0.99')
                        order_data['take_profit'] = tp
                        order_data['stop_loss'] = sl

                        response,tp, sl = await self.order_types.process_limit_and_tp_sl_orders("Webhook", order_data, take_profit=tp, stop_loss=sl)

                    elif order_type == 'bracket':
                        response = await self.order_types.place_bracket_order(order_book, order_data, highest_bid)

                    elif order_type == 'trailing_stop':
                        #await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", btc_order_data)
                        response = await self.order_types.place_trailing_stop_order(order_book, order_data, highest_bid)
                    else:
                        raise ValueError(f"Unknown order type: {order_type}")
                    if not response:  # get out of the while loop if the order is not valid
                        return False, response
                    # ✅ Process API Response
                    if isinstance(response, dict):
                        error_response = response.get('error_response', {})

                        # Amend Order if Required
                        if error_response.get('message') == 'amend' or 'Too many decimals' in error_response.get('message', ''):
                            self.log_manager.info(f"⚠️ Order amendment required (Attempt {attempt}/{max_attempts}). Adjusting order...")
                            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = self.shared_utils_precision.adjust_precision(
                                order_data['base_decimal'], order_data['quote_decimal'], adjusted_price, convert='quote'
                            )
                            order_data['adjusted_size'] = adjusted_size
                            continue  # Retry with adjusted order

                        elif error_response.get('preview_failure_reason', '') == 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE':
                            self.log_manager.info(f"⚠️ Stop price below last trade price. Adjusting order (Attempt {attempt}/{max_attempts})...")
                            adjusted_price, _ = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = adjusted_price * Decimal('1.0002')  # Slight buffer
                            continue  # Retry

                        elif response.get('id'):  # ✅ Order Placed Successfully
                            self.log_manager.buy(f"✅ {order_data['trading_pair']} Limit Order Executed at {order_data['adjusted_price']}")
                            return True, response

                        else:  # Unexpected API Response
                            self.log_manager.error(f"❌ Unexpected response format: {response}")
                            return False, response

                except Exception as ex:
                    self.log_manager.error(f"⚠️ Error during attempt #{attempt}: {ex}", exc_info=True)
                    if attempt >= max_attempts:
                        break  # Stop retrying if max attempts reached

            # Order placement failed after all retries
            self.log_manager.info(f"� Order placement failed after {max_attempts} attempts.")
            return False, response

        except Exception as ex:
            self.log_manager.error(f"❌ Error in attempt_order_placement: {ex}", exc_info=True)
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
            balance = balance.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_HALF_UP)

            cryto_avail_to_trade = spot_position.get(asset,{}).get('available_to_trade_crypto',0)
            crypto_value_usd = spot_position.get(asset,{}).get('total_balance_fiat',0)
            if crypto_value_usd <= self.max_value_to_buy:
                side='buy'
            else:
                side='sell'

            usd_bal = float(spot_position.get('USD', {}).get('available_to_trade_fiat', 0))

            # Convert trading pair format
            trading_pair = product_id.replace('-', '/')

            # Get latest price and calculate potential order size
            price = float(self.market_data.get('current_prices', {}).get(trading_pair, 0))
            if usd_bal > self.order_size:
                fiat_avail_for_order = self.order_size
                usd_avail = usd_bal-self.order_size
            else:
                fiat_avail_for_order = usd_bal
                usd_avail = 0
            if side == 'buy':
                buy_amount = round(fiat_avail_for_order / price, 8) if price > 0 else 0  # Prevent division by zero
                sell_amount =0

            elif side == 'sell':
                sell_amount = float(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
                buy_amount =0
            else:
                buy_amount = 0
                sell_amount = 0


            # Prepare initial order data
            if side == 'buy':
                size = buy_amount
            elif side == 'sell':
                size = sell_amount
            else:
                size = 0


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
                'base_avail_to_trade': balance, # was base_balance
                'sell_amount':sell_amount, # order size in crypto
                'buy_amount': buy_amount, # order size in fiat
                'quote_decimal':order_data.get('quote_decimal'),
                'base_decimal':order_data.get('base_decimal')
            }

            # Prepare book data
            temp_book = {
                'highest_bid': float(order_book.get('highest_bid', 0)),
                'lowest_ask': float(order_book.get('lowest_ask', 0)),
                'quote_avail_balance': fiat_avail_for_order, # was quote_amount
                'quote_decimal': quote_deci,
            }
            temp_book = {
                'highest_bid': float(order_book.get('highest_bid', 0)),
                'lowest_ask': float(order_book.get('lowest_ask', 0)),
            }

            # Adjust price and size
            if side == 'sell':
                adjusted_bid, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book) # sell price
                adjusted_bid = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_bid, 'quote')
                adjusted_ask = 0
            elif side == 'buy':
                adjusted_ask, adjusted_size = self.shared_utils_precision.adjust_price_and_size(temp_order, temp_book) # buying price
                adjusted_ask = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, adjusted_ask, 'quote')
                adjusted_bid = 0

            # Adjust size based on precision
            adjusted_size = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, size, 'base')

            # Final order data
            order_data = {
                'lowest_ask': float(adjusted_ask),
                'highest_bid': float(adjusted_bid),
                'adjusted_size': float(adjusted_size),
                'order_amount':float(fiat_avail_for_order),
                'trading_pair': trading_pair,
                'usd_available': usd_avail,
                'base_avail_balance': float(balance),
                'available_to_trade_crypto': cryto_avail_to_trade,
                'side': side,
                'quote_decimal': quote_deci,
                'base_decimal': base_deci,
                'status_of_order': 'LIMIT/'+side+'/ROC'
            }

            return order_data

        except Exception as e:
            self.log_manager.error(f"Error in build_order_data: {e}", exc_info=True)
            return None
