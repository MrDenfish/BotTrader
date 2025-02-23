
from decimal import Decimal, InvalidOperation
from Shared_Utils.config_manager import CentralConfig as Config
import pandas as pd

class ValidateOrders:
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, shared_utils_precision):
        """
        Singleton method to ensure only one instance of ValidateOrders exists.
        """
        if cls._instance is None:
            cls._instance = cls(logmanager, shared_utils_precision)
        return cls._instance

    def __init__(self, logmanager, shared_utils_precision):
        """
        Initializes the ValidateOrders instance.
        """
        self.config = Config()
        self.shared_utils_precision = shared_utils_precision
        self.log_manager = logmanager

        # Only store necessary attributes
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._hodl = self.config.hodl  # ✅ Ensure this is used elsewhere
        self._version = self.config.program_version  # ✅ Ensure this is required


    # def set_trade_parameters(self, market_data, order_management,  start_time=None):
    #     self.start_time = start_time
    #     self.ticker_cache = market_data['ticker_cache']
    #     self.non_zero_balances = order_management['non_zero_balances']
    #     self.order_tracker = order_management['order_tracker']
    #     self.market_cache_usd = market_data['usd_pairs_cache']
    #     self.market_cache_vol = market_data['filtered_vol']

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value  # Minimum value of a sell order

    @property
    def version(self):
        return self._version

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
            quote_currency = validate_data.get('quote_currency', '')
            base_currency = validate_data.get('base_currency', '')
            trading_pair = validate_data.get('trading_pair', '')
            side = validate_data.get('side', '')

            # Extract numerical values
            quote_balance = get_decimal_value('quote_balance')
            base_balance = get_decimal_value('base_balance_free')
            highest_bid = get_decimal_value('highest_bid')
            quote_price = get_decimal_value('quote_price')
            quote_amount = get_decimal_value('quote_amount')
            order_size = get_decimal_value('order_size')
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
                base_balance_value = base_balance * highest_bid * quote_price
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


