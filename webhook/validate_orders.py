
from decimal import Decimal, InvalidOperation


class ValidateOrders:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, utility, config):
        if cls._instance is None:
            cls._instance = cls(logmanager, utility, config)
        return cls._instance

    def __init__(self, logmanager, utility, config):
        # self.id = ValidateOrders._instance_count
        # ValidateOrders._instance_count += 1
        # print(f"ValidateOrders Instance ID: {self.id}")
        self.utility = utility
        self._version = config.program_version
        self.log_manager = logmanager

    @property
    def version(self):
        return self._version

    def fetch_and_validate_rules(self, validate_data):
        # Fetch and validate available balance
        # Return available_balance and coin balance
        # buy coin bal is wrong must be 0.0 when buy
        base_balance, base_balance_value, valid_order = (self.validate_orders(validate_data))
        if valid_order:
            return base_balance, True
        else:
            if base_balance is not None and base_balance_value > 10.0:
                return None, False
            elif base_balance is None or base_balance == 0.0:
                if validate_data['open_order'] is not None:
                    if not validate_data['open_order'].empty:
                        # Handle the case where open_orders is a non-empty DataFrame
                        self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: '
                                                             f'{validate_data["side"]} order will not be placed '
                                                             f'for {validate_data["trading_pair"]} there is an open order.')
                        return None, False  # not a valid order
                else:
                    self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: {validate_data["side"]} order not '
                                                         f'valid. {validate_data["trading_pair"]} balance is {base_balance}')

            return None, False

    def validate_orders(self, validate_data):

        quote_currency = validate_data['quote_currency']
        quote_balance = validate_data['quote_balance']
        base_deci = validate_data['base_decimal']
        quote_deci = validate_data['quote_decimal']
        base_currency = validate_data['base_currency']
        base_balance = validate_data['base_balance']
        highest_bid = validate_data['highest_bid']
        quote_price = validate_data['quote_price']
        open_orders = validate_data['open_order']
        trading_pair = validate_data['trading_pair']
        quote_amount = validate_data['quote_amount']
        side = validate_data['side']

        try:
            # Initialize base_balance_value

            base_balance_value = Decimal(0)

            # Safely convert quote_balance using try-except to handle potential conversion issues
            try:
                quote_balance_float = Decimal(quote_balance)  # Safely get quote value of account
            except InvalidOperation:
                quote_balance_float = Decimal(0)

            convert = 'usd' if quote_currency == 'USD' else 'quote'
            quote_balance = self.utility.adjust_precision(base_deci, quote_deci, quote_balance_float, convert=convert)

            # Simplified logic for base_balance_value calculation
            if base_currency != 'USD' and base_balance is not None:
                try:
                    base_balance_value = (base_balance * highest_bid) * quote_price  # dollar value of base balance
                    base_balance_value = self.utility.adjust_precision(base_deci, quote_deci, base_balance_value,
                                                                       convert=convert)
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
                        if order['product_id'] == trading_pair and order['remaining'] > 0:
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
                        f'{trading_pair} ${quote_amount}.00 is required. ')
                elif base_balance is not None and base_balance_value > 10.0:
                    self.log_manager.webhook_logger.info(f'validate_orders: {side} order will not be placed for '
                                                         f'{trading_pair} there is a  balance of '
                                                         f'{base_balance}{base_currency}. ')
                return base_balance, base_balance_value, quote_balance > quote_amount and base_balance_value <= Decimal(
                    '10.01')
            elif side == 'sell':
                return base_balance, base_balance_value, base_balance_value > Decimal('1.0')  # must be more than $1.0 to
                # sell
            return base_balance, base_balance_value, False

        except Exception as e:

            self.log_manager.webhook_logger.debug(f'validate_orders: An unexpected error occurred: {e}. ', exc_info=True)
            return None, None, False
