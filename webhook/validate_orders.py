
from log_manager import LoggerManager

from decimal import Decimal, InvalidOperation


class ValidateOrders:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, utility):
        if cls._instance is None:
            cls._instance = cls(logmanager, utility)
        return cls._instance

    def __init__(self, logmanager, utility):
        # self.id = ValidateOrders._instance_count
        # ValidateOrders._instance_count += 1
        # print(f"ValidateOrders Instance ID: {self.id}")
        self.utility = utility
        self.log_manager = logmanager
        self.base_currency, self.quote_currency, self.trading_pair = None, None, None
        self.base_deci, self.quote_deci, self.balances = None, None, None
        self.base_incri, self.quote_incri = None, None

    def set_trade_parameters(self, trading_pair, base_currency, quote_currency, base_decimal, quote_decimal,
                             base_increment, quote_increment, balances):
        self.base_currency = base_currency
        self.quote_currency = quote_currency
        self.trading_pair = trading_pair
        self.base_deci = base_decimal
        self.quote_deci = quote_decimal
        self.base_incri = base_increment
        self.quote_incri = quote_increment
        self.balances = balances

    @LoggerManager.log_method_call
    def fetch_and_validate_rules(self, side, highest_bid, usd_amount, base_balance, quote_bal,
                                 open_orders, quote_amount, quote_price):
        # Fetch and validate available balance
        # Return available_balance and coin balance
        # buy coin bal is wrong must be 0.0 when buy
        base_balance, base_balance_value, valid_order = self.validate_orders(side, highest_bid, usd_amount, base_balance,
                                                                             open_orders, quote_amount, quote_price)
        if valid_order:
            return base_balance, True
        else:
            if base_balance is not None and base_balance_value > 10.0:
                return None, False
            elif base_balance is None or base_balance == 0.0:
                if open_orders is not None:
                    if not open_orders.empty:
                        # Handle the case where open_orders is a non-empty DataFrame
                        self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: {side} order will not be placed '
                                                             f'for {self.trading_pair} there is an open order.')
                        return None, False  # not a valid order
                else:
                    self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: {side} order is not valid. '
                                                         f'{self.trading_pair}  balance is {base_balance} ')

            # if there is an open order for symbol, cancel it may be stale
            return None, False

    @LoggerManager.log_method_call
    def validate_orders(self, side, highest_bid, order_size, base_balance, open_orders, quote_amount, quote_price):
        try:
            # Initialize base_balance_value

            base_balance_value = Decimal(0)

            # Safely convert quote_balance using try-except to handle potential conversion issues
            try:
                quote_balance_float = Decimal(self.balances.get(self.quote_currency, 0))  # Safely get quote value of account
            except InvalidOperation:
                quote_balance_float = Decimal(0)

            convert = 'usd' if self.quote_currency == 'USD' else 'quote'
            quote_balance = self.utility.adjust_precision(quote_balance_float, convert=convert)

            # Simplified logic for base_balance_value calculation
            if self.base_currency != 'USD' and base_balance is not None:
                try:
                    base_balance_value = (base_balance * highest_bid) * quote_price  # dollar value of base balance
                    base_balance_value = self.utility.adjust_precision(base_balance_value, convert=convert)
                except InvalidOperation:
                    base_balance = Decimal(0)

            # Check for open orders
            # Replace hyphens with slashes in 'product_id' column
            if open_orders is not None and not open_orders.empty:
                open_orders['product_id'] = open_orders['product_id'].str.replace('-', '/')

                # Iterate over the rows of the DataFrame
                for index, order in open_orders.iterrows():
                    # Check if the order has a 'product_id' and 'remaining'
                    if 'product_id' in order and 'remaining' in order:
                        if order['product_id'] == self.trading_pair and order['remaining'] > 0:
                            return base_balance, base_balance_value, False  # Return False if an open order exists for the
                            # trading pair
                    else:
                        self.log_manager.webhook_logger.debug(f'Invalid order format: {order}')

            # Logic for buy and sell sides
            if side == 'buy':
                # must have more $quote than the order requires and  must be less than $10.01 worth of coin to buy
                if quote_balance < quote_amount:
                    self.log_manager.webhook_logger.info(
                        f'validate_orders: Insufficient funds ${quote_balance} to {side}: '
                        f'{self.trading_pair} ${quote_amount}.00 is required. ')
                elif base_balance is not None and base_balance_value > 10.0:
                    self.log_manager.webhook_logger.info(f'validate_orders: {side} order will not be placed for '
                                                         f'{self.trading_pair} there is a  balance of '
                                                         f'{base_balance}{self.base_currency}. ')
                return base_balance, base_balance_value, quote_balance > quote_amount and base_balance_value <= Decimal(
                    '10.01')
            elif side == 'sell':
                return base_balance, base_balance_value, base_balance_value > Decimal('1.0')  # must be more than $1.0 to
                # sell

            return base_balance, base_balance_value, False

        except Exception as e:
            self.log_manager.webhook_logger.debug(f'validate_orders: An unexpected error occurred: {e}. ')
            return None, None, False
