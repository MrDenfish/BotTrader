
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from Config.config_manager import CentralConfig as Config
import pandas as pd

class ValidateOrders:
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, order_book,shared_utils_precision):
        """
        Singleton method to ensure only one instance of ValidateOrders exists.
        """
        if cls._instance is None:
            cls._instance = cls(logmanager, order_book,shared_utils_precision)
        return cls._instance

    def __init__(self, logmanager, order_book, shared_utils_precision):
        """
        Initializes the ValidateOrders instance.
        """
        self.config = Config()
        self.order_book = order_book
        self.shared_utils_precision = shared_utils_precision
        self.log_manager = logmanager

        # Only store necessary attributes
        self._min_sell_value = self.config.min_sell_value
        self._min_order_amount = self.config.min_order_amount
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._hodl = self.config.hodl  # ✅ Ensure this is used elsewhere
        self._version = self.config.program_version  # ✅ Ensure this is required

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value  # Minimum value of a sell order

    @property
    def min_order_amount(self):
        return self._min_order_amount  # Minimum order amount

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self.max_value_of_crypto_to_buy_more  # max value of a sell order

    @property
    def version(self):
        return self._version

    def validate_order_conditions(self, order_details, open_orders):
        """
        Validates order conditions based on account balances and active open orders.
        called from:
        place_order()
        process_limit_and_tp_sl_orders(()
        Args:
            order_details (dict): Data about the order to validate.
            open_orders (DataFrame): DataFrame of active open orders.

        Returns:
            dict: Contains validation status, error messages, and detailed information.
        """

        side = order_details.get('side')
        usd_balance = order_details.get('usd_balance', 0)
        order_size = order_details.get('order_amount', 0)
        base_balance = order_details.get('base_avail_balance', 0)
        symbol = order_details.get('trading_pair', '')
        asset =symbol.split('/')[0]



        trailing_stop_active = False  # Flag for trailing stop orders

        response_msg = {
            "is_valid": False,
            "error": None,
            "code": "200",
            "message": f"Validation failed for {symbol}",
            "details": {
                "Order Id": None,
                "Order Type": order_details.get('order_type', ''),
                "Asset": asset,
                "Trading Pair": symbol,
                "Side": side,
                "Quote Available Balance": usd_balance,
                "Base Available to Trade":order_details.get('available_to_trade_crypto', ''),
                "Base Balance": base_balance,
                "Order Size": order_size,
                "Condition":None
            }
        }

        if order_details.get('order_type') == 'market':
            if side == 'buy':
                if usd_balance < order_size:
                    response = {
                        "Base Balance": base_balance,
                        "Order Size": order_size,
                        "Condition":None
                    }

        try:
            if open_orders.empty:
                if side == 'buy' and usd_balance < order_size:
                    response_msg["error"] = f"Insufficient quote balance. Required: {order_size}, Available: {usd_balance}"
                    response_msg["code"] = "400"
                    return response_msg

                if side == 'sell' and base_balance <= 0:
                    response_msg["error"] = f"Insufficient base balance to sell {symbol}."
                    response_msg["code"] = "400"
                    return response_msg

                response_msg["is_valid"] = True
                response_msg["message"] = "Order validated successfully."
                return response_msg

            if symbol in open_orders.symbol.values:
                response_msg["error"] = f"open_order"
                response_msg["code"] = "200"
                response_msg["Condition"] = f"Open order exists for {symbol}."
                return response_msg

            response_msg["is_valid"] = True
            response_msg["message"] = "Order validated successfully."

            return response_msg

        except KeyError as e:
            response_msg["error"] = f"KeyError: Missing key in order_details or open_orders: {e}"
            response_msg["code"] = "500"
            return response_msg

    def build_validate_data(self, order_details, open_orders, order_book_details):
        """Called from:
        place_order()
        process_limit_and_tp_sl_orders()
        """

        return {
            **order_details,
            'base_avail_balance': order_details['base_avail_balance'],
            'base_currency': order_details['trading_pair'].split('/')[0],
            "Base Available to Trade": order_details.get('available_to_trade_crypto', ''),
            'available_to_trade_crypto':order_details['available_to_trade_crypto'],
            'order_amount': order_details['order_amount'],
            'highest_bid': order_book_details['highest_bid'],
            'lowest_ask': order_book_details['lowest_ask'],
            'spread': order_book_details['spread'],
            'open_orders': open_orders
        }

    def fetch_and_validate_rules(self, validate_data):
        """
        Fetch available balance, validate order conditions, and ensure no duplicate orders exist.
        Called from:
        - place_order()
        - process_limit_and_tp_sl_orders()

        Args:
            validate_data (dict): Data containing order details and account balances.

        Returns:
            dict: Contains validation status, error messages, and relevant details.
        """
        try:
            base_balance, base_balance_value, valid_order, condition = self.validate_orders(validate_data)

            response_msg = {
                "is_valid": valid_order,
                "error": None,
                "code": "200",
                "message": f"Order validation failed for {validate_data.get('trading_pair')}",
                "details": {
                    "Order Id": None,  # order_details.get('order_id', ''),
                    "Asset": validate_data.get("base_currency"),
                    "Trading Pair": validate_data.get("trading_pair"),
                    "Side": validate_data.get("side"),
                    "Base Balance": base_balance,
                    "Base Balance Value": base_balance_value,
                    "Base Available to Trade":validate_data.get('available_to_trade_crypto', ''),
                    "Condition": condition,
                    "Open Orders": "N/A",  # Will be updated if open orders exist
                }
            }

            # ✅ If order is valid, return immediately
            if valid_order:
                response_msg["is_valid"] = True
                response_msg["message"] = f"✅ Order validation successful for {validate_data.get('trading_pair')}."
                return response_msg

            # ✅ Handle case where base balance value exceeds threshold
            if base_balance_value is not None and base_balance_value > Decimal("1.0"):
                response_msg["is_valid"] = False
                response_msg["error"] = f"Base balance value {base_balance_value} exceeds limit."
                response_msg["code"] = "400"
                response_msg['Condition'] = condition
                return response_msg

            # ✅ Handle case where base balance is zero or missing
            if base_balance is None or base_balance == 0.0:
                open_orders = validate_data.get('open_orders', pd.DataFrame())

                if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
                    # Prevent modifying original DataFrame
                    open_orders = open_orders.copy()

                    # ✅ Extract `product_id` safely from `info`
                    open_orders['product_id'] = open_orders['info'].apply(
                        lambda x: x.get('product_id', '').replace('-', '/') if isinstance(x, dict) else None
                    )

                    # ✅ Find matching open orders for the trading pair
                    matching_order = open_orders.loc[open_orders['product_id'] == validate_data['trading_pair']]

                    # ✅ Extract the side of the open order (if any)
                    order_side = matching_order.iloc[0]['side'] if not matching_order.empty else 'Unknown'

                    self.log_manager.info(
                        f'fetch_and_validate_rules: {validate_data["side"]} order will not be placed '
                        f'for {validate_data["trading_pair"]} as there is an open order to {order_side}.'
                    )

                    response_msg["is_valid"] = False
                    response_msg["error"] = f"Open order exists for {validate_data['trading_pair']} on side {order_side}."
                    response_msg["code"] = "400"
                    response_msg["details"]["Open Orders"] = order_side
                    return response_msg  # ❌ Block duplicate orders

                else:
                    self.log_manager.info(
                        f'fetch_and_validate_rules: {validate_data["side"]} order not valid. '
                        f'{validate_data["trading_pair"]} balance is {base_balance}'
                    )
                    response_msg["is_valid"] = False
                    response_msg["error"] = f"Insufficient balance to place {validate_data['side']} order."
                    response_msg["code"] = "400"
                    return response_msg

            # ❌ Default return for invalid orders
            response_msg["is_valid"] = False
            condition = response_msg.get('details', {}).get('Condition', None)
            if condition is None:
                response_msg["details"]["Condition"] = "Unknown"
                response_msg["code"] = "400"
                self.log_manager.info(f'fetch_and_validate_rules: Order is not valid due to unknown conditions.')
            else:
                response_msg["error"] = condition
                response_msg["code"] = "200"



            return response_msg

        except Exception as e:
            self.log_manager.error(f'fetch_and_validate_rules: {e}', exc_info=True)
            return {
                "is_valid": False,
                "error": f"Unexpected error: {e}",
                "code": "500",
                "message": "Internal error while validating order.",
                "details": {}
            }

    def validate_orders(self, validate_data):
        """
        Validate whether an order should be placed, considering open orders and balances.

        Called from:
        - fetch_and_validate_rules()

        Args:
            validate_data (dict): Contains trading pair, balances, order details, and open orders.

        Returns:
            tuple: (base_balance, base_balance_value, valid_order, condition)
        """

        def get_decimal_value(key, default='0'):
            """ Safely fetch and convert a value from `validate_data` to Decimal. """
            try:
                value = validate_data.get(key, default)
                return Decimal(value) if value is not None else Decimal(default)
            except InvalidOperation:
                return Decimal(default)

        try:
            # Extract key details from validate_data
            trading_pair = validate_data.get('trading_pair', '')
            quote_currency = validate_data.get('quote_currency', trading_pair.split('/')[1])
            base_currency = validate_data.get('base_currency', trading_pair.split('/')[0])
            side = validate_data.get('side', '')

            # Extract numerical values
            usd_avail_balance = get_decimal_value('usd_balance')
            base_balance = get_decimal_value('base_avail_balance')
            base_available = get_decimal_value('available_to_trade_crypto')
            highest_bid = get_decimal_value('highest_bid')
            lowest_ask = get_decimal_value('lowest_ask')
            quote_price = get_decimal_value('quote_price', (highest_bid + lowest_ask) / 2)
            order_amount = get_decimal_value('order_amount')
            base_balance_value = base_available * highest_bid
            base_deci = validate_data.get('base_decimal', 0)  # Extract base decimal precision
            quote_deci = validate_data.get('quote_decimal', 0)
            open_orders = validate_data.get('open_orders', None)

            condition = None
            valid_order = False

            # ✅ **Quantize base_balance to the correct decimal places**
            base_balance = base_balance.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_HALF_UP)

            # Adjust precision for quote balance
            convert = 'usd' if quote_currency == 'USD' else 'quote'
            adjusted_usd_balance = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, usd_avail_balance, convert=convert
            )

            # Compute base balance value in USD equivalent
            base_balance_value = Decimal(0)
            if base_currency != 'USD' and not base_balance.is_zero():
                base_balance_value = base_balance * quote_price
                base_balance_value = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, base_balance_value, convert=convert
                )

            # ✅ **Check for open orders in DataFrame**
            if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
                # Ensure `product_id` is extracted from `info` column safely
                open_orders['product_id'] = open_orders['info'].apply(
                    lambda x: x.get('product_id').replace('-', '/') if isinstance(x, dict) else None
                )

                # ✅ **Filter open orders by `trading_pair`**
                matching_orders = open_orders.loc[
                    (open_orders['product_id'] == trading_pair) & (open_orders['remaining'] > 0)
                    ]

                if not matching_orders.empty:
                    condition = f'⚠️ Open order exists for {trading_pair}. Blocking new order.'
                    return base_balance, base_balance_value, valid_order, condition  # Block new order

            # ✅ **Determine whether an order should be placed**
            hodling = base_currency in self.hodl

            if side == 'buy':
                if adjusted_usd_balance < order_amount and order_amount > self.min_order_amount:
                    order_amount = round(order_amount, 2)
                    self.log_manager.info(
                        f'⚠️ Order sized reduced: Available after order submitted ${adjusted_usd_balance}. Reduced order ${order_amount} to BUY'
                        f' {trading_pair}.'
                    )
                    condition = f"Reduced order submitted {trading_pair}."
                    valid_order = True
                elif adjusted_usd_balance >= order_amount and (hodling or base_balance_value <= self.min_order_amount):
                    condition = '✅ Buy order conditions met.'
                    valid_order = True

            elif side == 'sell' and not hodling:
                if base_balance_value >= self.min_order_amount:
                    condition = f'✅️ {trading_pair} has sufficient balance to sell.'
                    valid_order = True
                else:
                    condition = f'⚠️ {trading_pair} has insufficient balance to sell.'
                    valid_order = False

            if not valid_order:
                self.log_manager.info(f'❌ Order validation failed for {trading_pair}: {condition}')

            return base_balance, base_balance_value, valid_order, condition

        except Exception as e:
            self.log_manager.error(f'⚠️ validate_orders: {e}', exc_info=True)
            return None, None, False, None

    async def validate_and_adjust_order(self, order_data):
        """
        Validates order data and adjusts price dynamically to ensure post-only compliance.

        Called from:
        - process_limit_and_tp_sl_orders()

        Args:
            order_data (dict): Contains order details including price, size, and available balances.

        Returns:
            dict or None: Adjusted order data with updated price and size, or None if invalid.
        """
        response_msg = {
            "is_valid": False,
            "error": "A required field is missing",
            "code": "400",
            "message": f"Order validation failed for {order_data.get('trading_pair')}",
            "details": {
                "Order Id": order_data.get('order_id', ''),
                "Asset": order_data.get('trading_pair').split('/')[0],
                "Trading Pair": order_data.get("trading_pair"),
                "Side": order_data.get("side"),
                "Base Balance": order_data.get("base_avail_balance"),
                "Base Balance Value": order_data.get("base_avail_balance"),
                "Base Available to Trade":order_data.get('available_to_trade_crypto', ''),
                "Condition": "Missing fields",
                "Open Orders": "N/A",  # Will be updated if open orders exist
            }
        }

        try:
            # ✅ Required field check
            required_fields = [
                'trading_pair', 'side', 'adjusted_size', 'highest_bid',
                'lowest_ask', 'available_to_trade_crypto'
            ]
            missing_fields = [field for field in required_fields if order_data.get(field) is None]

            if missing_fields:
                self.log_manager.error(f"⚠️ Missing required fields: {missing_fields}")
                response_msg['details']['Condition'] = f"Missing fields: {missing_fields}"
                return order_data, response_msg

            side = order_data['side'].lower()
            symbol = order_data['trading_pair'].replace('/', '-')

            # ✅ Fetch and validate price
            price = Decimal(order_data.get('highest_bid' if side == 'sell' else 'lowest_ask', 0))
            formatted_decimal = self.shared_utils_precision.get_decimal_format(order_data.get('quote_decimal'))
            price = price.quantize(formatted_decimal, rounding=ROUND_HALF_UP)

            # ✅ Fetch and adjust order book data
            order_book = await self.order_book.get_order_book(order_data)

            latest_lowest_ask = Decimal(order_book['order_book']['asks'][0][0]) if order_book['order_book']['asks'] else price
            latest_highest_bid = Decimal(order_book['order_book']['bids'][0][0]) if order_book['order_book']['bids'] else price

            # ✅ Apply dynamic price buffer (to avoid post-only rejections)
            price_buffer_pct = Decimal('0.001')  # 0.1% buffer
            min_buffer = Decimal('0.0000001')

            if side == 'buy' and price >= latest_lowest_ask:
                price = max(latest_lowest_ask * (Decimal('1') - price_buffer_pct), latest_lowest_ask - min_buffer)
            elif side == 'sell' and price <= latest_highest_bid:
                price = min(latest_highest_bid * (Decimal('1') + price_buffer_pct), latest_highest_bid + min_buffer)

            # ✅ Retrieve quote_decimal and convert it to a Decimal precision format
            quote_decimal = Decimal('1').scaleb(-order_data.get('quote_decimal', 2))  # Default to 2 decimal places
            price = Decimal(price).quantize(quote_decimal, rounding=ROUND_DOWN)

            # ✅ Ensure sufficient balance
            available_crypto = Decimal(order_data.get('available_to_trade_crypto', 0))
            usd_available = Decimal(order_data.get('usd_balance', 0))
            amount = Decimal(order_data['adjusted_size'])

            if side == 'sell' and amount > available_crypto:
                amount = Decimal(available_crypto).quantize(Decimal(f'1e-{order_data.get("base_decimal", 8)}'), rounding=ROUND_DOWN)

            if side == 'buy' and (amount * price) > usd_available:
                self.log_manager.info(f"⚠️ Insufficient USD for BUY order: Required {round(amount * price,2)}, Available "
                                      f"{round(usd_available,2)}")
                response_msg['error'] = "Insufficient USD for BUY order"
                response_msg['details']['Condition'] = f"Required {amount * price}, Available {usd_available}"
                return order_data, response_msg

            if side == 'sell' and amount > available_crypto:
                self.log_manager.info(f"⚠️ Insufficient {symbol} balance for SELL order. Trying to sell {amount}, Available {available_crypto}")
                response_msg['error'] = "Insufficient balance for SELL order"
                response_msg['details']['Condition'] = f"Required {amount}, Available {available_crypto}"
                return order_data, response_msg

            # ✅ Update order data with adjusted price
            order_data['adjusted_price'] = price
            order_data['adjusted_size'] = amount
            response_msg = {
                "is_valid": True,
                "error": None,
                "code": "200",
                "message": f"Order validation successful for {order_data.get('trading_pair')}",
                "details": {
                    "Order Id": order_data.get('order_id', ''),
                    "Asset": order_data.get('trading_pair').split('/')[0],
                    "Trading Pair": order_data.get("trading_pair"),
                    "Side": order_data.get("side"),
                    "Base Balance": order_data.get("base_avail_balance"),
                    "Base Balance Value": order_data.get("base_avail_balance"),
                    "Base Available to Trade":order_data.get('available_to_trade_crypto', ''),
                    "Condition": "Updated",
                    "Open Orders": "N/A",  # Will be updated if open orders exist
                }
            }

            return order_data, response_msg

        except Exception as e:
            self.log_manager.error(f"❌ Error in validate_and_adjust_order: {e}", exc_info=True)
            response_msg['error'] = "Error in validate_and_adjust_order"
            response_msg['details']['Condition'] = f"{e}"
            return order_data, response_msg
