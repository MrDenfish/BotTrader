
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from Shared_Utils.config_manager import CentralConfig as Config
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
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._hodl = self.config.hodl  # ✅ Ensure this is used elsewhere
        self._version = self.config.program_version  # ✅ Ensure this is required

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value  # Minimum value of a sell order

    @property
    def version(self):
        return self._version

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
                    self.log_manager.error(f"Unknown order side: {side}", exc_info=True)
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

    def build_validate_data(self, order_details, open_orders, order_book_details):

        return {
            **order_details,
            'base_balance_free': order_details['base_balance'],
            'quote_amount': order_details['quote_amount'],
            'highest_bid': order_book_details['highest_bid'],
            'lowest_ask': order_book_details['lowest_ask'],
            'spread': order_book_details['spread'],
            'open_orders': open_orders
        }

    def fetch_and_validate_rules(self, validate_data):
        """ Fetch available balance, validate order conditions, and ensure no duplicate orders exist. """
        try:
            base_balance, base_balance_value, valid_order, condition = self.validate_orders(validate_data)

            if valid_order:
                return base_balance, True, condition  # ✅ Valid order

            # ✅ Handle case where base balance is greater than 1.0
            if base_balance is not None and base_balance_value > 1.0:
                return None, False, condition

            # ✅ Handle case where base balance is zero or missing
            if base_balance is None or base_balance == 0.0:
                open_orders = validate_data.get('open_orders', pd.DataFrame())

                if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
                    # Extract `product_id` from `info` safely
                    open_orders = open_orders.copy()  # Prevent modifying original DataFrame
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
                    return None, False, condition  # � Block duplicate orders

                else:
                    self.log_manager.info(
                        f'fetch_and_validate_rules: {validate_data["side"]} order not valid. '
                        f'{validate_data["trading_pair"]} balance is {base_balance}'
                    )

            return None, False, condition  # � Order is not valid

        except Exception as e:
            self.log_manager.error(f'fetch_and_validate_rules: {e}', exc_info=True)
            return None, False, None

    def validate_orders(self, validate_data):
        """ Validate whether an order should be placed, considering open orders and balances. """

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
            quote_currency = validate_data.get('quote_currency', trading_pair.split('/')[1] )
            base_currency = validate_data.get('base_currency', trading_pair.split('/')[0] )
            side = validate_data.get('side', '')

            # Extract numerical values
            quote_balance = get_decimal_value('usd_available')
            base_balance = get_decimal_value('available_to_trade_crypto')
            highest_bid = get_decimal_value('highest_bid')
            lowest_ask = get_decimal_value('lowest_ask')
            quote_price = get_decimal_value('quote_price',(highest_bid+lowest_ask)/2)
            # quote_price = get_decimal_value('quote_price',
            quote_amount = get_decimal_value('quote_amount')
            order_size = get_decimal_value('adjusted_size')
            base_deci = validate_data.get('base_decimal', 0)
            quote_deci = validate_data.get('quote_decimal', 0)
            open_orders = validate_data.get('open_orders', None)

            condition = None
            valid_order = False

            # Adjust precision for quote balance
            convert = 'usd' if quote_currency == 'USD' else 'quote'
            adjusted_quote_balance = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, quote_balance, convert=convert
            )

            # Compute base balance value in USD equivalent
            base_balance_value = Decimal(0)
            if base_currency != 'USD' and not base_balance.is_zero():
                base_balance_value = base_balance *  quote_price
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
                    condition = f'open order exists for {trading_pair}'
                    return base_balance, base_balance_value, valid_order, condition  # Block new order

            # ✅ **Determine whether an order should be placed**
            hodling = base_currency in self.hodl

            if side == 'buy':
                if adjusted_quote_balance < quote_amount:
                    self.log_manager.info(
                        f'validate_orders: Insufficient funds ${adjusted_quote_balance} to {side} {trading_pair}. '
                        f'Required: ${quote_amount:.2f}'
                    )
                elif adjusted_quote_balance > quote_amount and (hodling or base_balance_value <= Decimal('10.01')):
                    condition = 'buy'
                    valid_order = True

            elif side == 'sell' and not hodling:
                condition = 'not hodling'
                valid_order = base_balance_value > Decimal('1.0')

            return base_balance, base_balance_value, valid_order, condition

        except Exception as e:
            self.log_manager.error(f'validate_orders: {e}', exc_info=True)
            return None, None, False, None

    async def validate_and_adjust_order(self, order_data):
        """
        Validates order data and adjusts price dynamically to ensure post-only compliance.
        """
        try:
            # ✅ Required field check
            required_fields = ['trading_pair', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask', 'available_to_trade_crypto']
            missing_fields = [field for field in required_fields if order_data.get(field) is None]
            if missing_fields:
                self.log_manager.error(f"Missing required fields: {missing_fields}")
                return None

            side = order_data['side']
            symbol = order_data['trading_pair'].replace('/', '-')
            price = Decimal(order_data.get('highest_bid' if side == 'sell' else 'lowest_ask', 0))

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
                # ✅ Apply quantize() using the correctly formatted precision
                price = Decimal(price).quantize(quote_decimal, rounding=ROUND_DOWN)



            # ✅ Ensure sufficient balance
            available_crypto = Decimal(order_data.get('available_to_trade_crypto', 0))
            usd_available = Decimal(order_data.get('usd_available', 0))
            amount = Decimal(order_data['adjusted_size'])

            if side == 'sell' and amount > available_crypto:
                amount = Decimal(available_crypto).quantize(order_data.get('base_deci', 8), rounding=ROUND_DOWN)

            if side == 'buy' and (amount * price) > usd_available:
                self.log_manager.info(f"Insufficient USD for BUY order: Required {amount * price}, Available {usd_available}")
                return None
            if side == 'sell' and amount > available_crypto:
                self.log_manager.info(f"Insufficient {symbol} balance for SELL order. Trying to sell {amount}, Available {available_crypto}")
                return None

            # ✅ Update order data with adjusted price
            order_data['adjusted_price'] = price
            return order_data

        except Exception as e:
            self.log_manager.error(f"Error in validate_and_adjust_order: {e}", exc_info=True)
            return None



