from datetime import datetime

from Config.config_manager import CentralConfig as config
from decimal import Decimal, ROUND_HALF_UP
from inspect import stack
import pandas as pd

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

    async def place_order(self, order_details, precision_data=None):
        try:
            self.shared_utils_utility.log_event_loop("place_order")  # debug
            all_open_orders, has_open_order,_ = await self.websocket_helper.refresh_open_orders(trading_pair=order_details['trading_pair'])
            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
            if not precision_data:
                precision_data = self.shared_utils_precision.fetch_precision(order_details['trading_pair'],self.usd_pairs)
                base_deci, quote_deci, _, _ = precision_data
            # if order conditions are met True else False
            validation_result = self.validate.validate_order_conditions(order_details, open_orders) # 🔆

            if not validation_result["is_valid"] or has_open_order:
                return False, validation_result

            order_book_details = await self.order_book_manager.get_order_book(order_details)
            validate_data = self.validate.build_validate_data(order_details, open_orders, order_book_details) # 🔆

            validation_result = self.validate.fetch_and_validate_rules(validate_data) # 🔆

            if not validation_result["is_valid"]:
                return False, validation_result
            event_type = 'webhook'
            return await self.handle_order(validate_data, order_book_details, precision_data)


        except Exception as ex:
            self.log_manager.error(ex, exc_info=True)
            return [False,response_msg]

    async def handle_order(self, validate_data, order_book_details, precision_data):
        try:
            # Extract key data
            asset = validate_data.get("base_currency", "")
            print(f'(validate_data) {validate_data}')
            product_id = validate_data.get("trading_pair", "").replace("/", "-")  # Format to match build_order_data

            # ✅ Ensure critical fields are present
            highest_bid = Decimal(order_book_details['highest_bid'])
            lowest_ask = Decimal(order_book_details['lowest_ask'])
            spread = Decimal(order_book_details['spread'])
            base_deci, quote_deci, _, _ = precision_data

            # ✅ Calculate adjusted price
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(validate_data, order_book_details)

            # ✅ Calculate take profit and stop loss prices
            take_profit_price = adjusted_price * (1 + self.take_profit)
            adjusted_take_profit_price = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, take_profit_price, convert='quote'
            )

            stop_loss_price = adjusted_price * (1 + self.stop_loss)
            adjusted_stop_loss_price = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, stop_loss_price, convert='quote'
            )

            # ✅ Update `validate_data` directly (instead of `order_data`)
            validate_data.update(
                {
                    'adjusted_price': adjusted_price,
                    'adjusted_size': adjusted_size,  # Now properly quantized
                    'stop_loss_price': adjusted_stop_loss_price,
                    'take_profit_price': adjusted_take_profit_price,
                    'usd_available': validate_data.get('usd_balance', 0),
                    'quote_decimal': quote_deci
                }
            )

            print(f"✅ Final Validate Data: {validate_data}")

            # ✅ Decide order type and place order
            order_type = self.order_type_to_use(validate_data, adjusted_price, highest_bid, lowest_ask)
            response = await self.attempt_order_placement(validate_data, order_type=order_type)

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
            order_type = 'limit'
            return order_type

    async def attempt_order_placement(self, validate_data, order_type):
        """
        Attempts to place an order (limit, bracket, trailing stop) with retry logic.

        Args:
            validate_data (dict): Order details.
            order_type (str): Type of order ('limit', 'tp_sl', 'bracket', 'trailing_stop').

        Returns:
            tuple: (bool, response) where bool indicates success, and response contains the order result or error.
        """
        caller_function_name = stack()[1].function  # Debug
        response = None
        max_attempts = 5

        for attempt in range(max_attempts):
            try:
                # ✅ Refresh Order Book
                order_book = await self.order_book_manager.get_order_book(validate_data)
                highest_bid, lowest_ask = Decimal(order_book['highest_bid']), Decimal(order_book['lowest_ask'])

                # ✅ Adjust Price to Avoid Rejections
                order_price = (
                    min(highest_bid, lowest_ask - Decimal('0.0001'))
                    if validate_data['side'].lower() == 'buy'
                    else max(highest_bid, lowest_ask + Decimal('0.0001'))
                )
                validate_data['adjusted_price'] = order_price

                # ✅ Calculate TP & SL if needed
                if order_type in ['tp_sl', 'bracket']:
                    base_deci, quote_deci = validate_data['base_decimal'], validate_data['quote_decimal']
                    tp, sl = await self.profit_manager.calculate_tp_sl(order_price, base_deci, quote_deci)
                    validate_data.update({'take_profit': tp, 'stop_loss': sl})

                # ✅ Execute Order Based on Type
                if order_type == 'limit':
                    response = await self.order_types.process_limit_and_tp_sl_orders("Webhook", validate_data)
                elif order_type == 'tp_sl':
                    response  = await self.order_types.process_limit_and_tp_sl_orders(
                        "Webhook", validate_data, take_profit=tp, stop_loss=sl
                        )
                elif order_type == 'bracket':
                    response = await self.order_types.place_bracket_order(order_book, validate_data, highest_bid)
                elif order_type == 'trailing_stop':
                    response = await self.order_types.place_trailing_stop_order(order_book, validate_data, highest_bid)
                else:
                    raise ValueError(f"Unknown order type: {order_type}")

                # ✅ Handle API Response
                if not response:
                    return False, response

                if isinstance(response, dict):
                    error_response = response.get('error_response', {})
                    order_id = response.get('success_response', {}).get('order_id')
                    symbol = validate_data['trading_pair']
                    if order_id:
                        self.log_manager.info(f"✅ Successfully placed {order_type} order for {symbol}.")
                        return True, response
                    # ✅ Handle Different Error Cases
                    if 'insufficient_crypto_Sell' in error_response:
                        self.log_manager.info(f"⚠️ Insufficient crypto to sell. {response.get('details')}")
                        return False, response
                    elif error_response == 'open_order':
                        self.log_manager.info(f"⚠️ Open order exists. {response.get('Condition')}")
                        return False, response
                    elif error_response in ['amend', 'Too many decimals']:
                        self.log_manager.info(f"⚠️ Order amendment required (Attempt {attempt + 1}/{max_attempts}). Adjusting order...")
                        adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(validate_data, order_book)
                        validate_data.update(
                            {
                                'adjusted_price': self.shared_utils_precision.adjust_precision(
                                    validate_data['base_decimal'], validate_data['quote_decimal'], adjusted_price, convert='quote'
                                    ),
                                'adjusted_size': adjusted_size
                            }
                        )
                        continue  # Retry
                    elif 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in error_response['preview_failure_reason']:
                        self.log_manager.info(f"⚠️ Stop price below last trade price {symbol}: "
                                              f"Adjusting order (Attempt {attempt + 1}{max_attempts})...")
                        validate_data['adjusted_price'] *= Decimal('1.0002')  # Slight buffer
                        continue  # Retry
                    elif 'PREVIEW_INVALID_ATTACHED_TAKE_PROFIT_PRICE_OUT_OF_BOUNDS' in error_response['preview_failure_reason']:
                        self.log_manager.info(f"⚠️ Take profit price out of bounds for {symbol}. "
                                              f"Adjusting order (Attempt {attempt + 1}/{max_attempts})...")
                        continue  # Retry
                    elif ('Insufficient USD for BUY order' in error_response['preview_failure_reason'] or 'PREVIEW_INSUFFICIENT_FUND' in
                          error_response):
                        self.log_manager.info(f"⚠️ Insufficient USD for BUY order. {response.get('details')}")
                        return False, response
                    elif 'PREVIEW_INVALID_ORDER_CONFIG' in error_response['preview_failure_reason']:
                        self.log_manager.info(f"⚠️ Invalid order config. {response.get('details')}")
                        return False, response
                    elif 'PREVIEW_INVALID_LIMIT_PRICE' in error_response['preview_failure_reason']:
                        self.log_manager.info(f"⚠️ Invalid limit price for  {symbol}")
                        response.get('order_configuration',{}).get('limit_limit_gtc',{}).get('limit_price')
                        return False, response
                    elif 'PREVIEW_INVALID_ATTACHED_STOP_LOSS_PRICE_OUT_OF_BOUNDS' in error_response['preview_failure_reason']:
                        self.log_manager.info(f"⚠️ Stop loss price out of bounds. {symbol}")
                        return False, response
                    elif response.get('success_response', {}).get('order_id'):
                        self.log_manager.info(f"✅ {validate_data['trading_pair']} Order Executed at {validate_data['adjusted_price']}")
                        return True, response
                    else:
                        self.log_manager.error(f"❌ Unexpected response format: {response}", exc_info=True)
                        return False, response

            except Exception as ex:
                self.log_manager.error(f"⚠️ Error during attempt #{attempt + 1}: {ex}", exc_info=True)
                if attempt >= max_attempts - 1:
                    break  # Stop retrying if max attempts reached

        self.log_manager.info(f"❌ Order placement failed after {max_attempts} attempts.")
        return False, response

    async def build_order_data(self, asset, product_id):
        """
        Constructs order data for placing a limit order for websockets.

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
            if crypto_value_usd <= self.max_value_of_crypto_to_buy_more:
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
                usd_avail = fiat_avail_for_order - float(self.min_order_amount)
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
                'lowest_ask': float(adjusted_ask),#✅
                'highest_bid': float(adjusted_bid),#✅
                'adjusted_size': float(adjusted_size),
                'order_amount':float(fiat_avail_for_order),#✅
                'size_of_the_order': float(adjusted_size),
                'available_to_trade_crypto': cryto_avail_to_trade,#❌
                'spread': order_book['spread'],#❌
                'trading_pair': trading_pair,
                'usd_balance':usd_bal,
                'usd_available': usd_avail,
                'base_avail_balance': float(balance),#✅
                'available_to_trade_crypto': cryto_avail_to_trade,
                'side': side,
                'quote_decimal': quote_deci,
                'base_decimal': base_deci,
                'order_created_for': 'WEBSOCKET_signal',
                'status_of_order': 'LIMIT/'+side+'/ROC'
            }

            return order_data

        except Exception as e:
            self.log_manager.error(f"Error in build_order_data: {e}", exc_info=True)
            return None


    def build_order_details_webhooks(self,shared_utils_precision, trade_data: dict, base_balance: str, base_price: Decimal,
                                     quote_price: Decimal,base_order_size: Decimal, quote_avail_balance: Decimal, usd_balance: Decimal,
                                     cryto_avail_to_trade:float, precision_data: tuple) -> dict:

        quote_deci, base_deci, quote_increment, base_increment = precision_data
        return {
            'side': trade_data['side'],
            'base_increment': shared_utils_precision.float_to_decimal(base_increment, base_deci),
            'base_decimal': base_deci,
            'quote_decimal': quote_deci,
            'base_currency': trade_data['base_currency'],
            'quote_currency': trade_data['quote_currency'],
            'trading_pair': trade_data['trading_pair'],
            'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'quote_price': quote_price,
            'order_amount': trade_data['order_amount'], #110
            'size_of_the_order': (float(trade_data['order_amount']/base_price)),
            'base_balance': base_balance,
            'base_price': base_price,
            'base_order_size': base_order_size,
            'quote_avail_balance': quote_avail_balance,
            'base_avail_balance': base_balance,
            'available_to_trade_crypto': cryto_avail_to_trade,
            'usd_available': usd_balance,
            'order_created_for': 'WEBSOCKET_signal',
            'status_of_order': 'LIMIT/'+trade_data['side']+'/ROC'
        }
