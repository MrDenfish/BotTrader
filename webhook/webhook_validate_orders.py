
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
        self._min_sell_value = Decimal(config.min_sell_value)
        self.utility = utility
        self._hodl = config.hodl
        self._version = config.program_version
        self.log_manager = logmanager

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
        # Fetch and validate available balance
        # Return available_balance and coin balance
        # buy coin bal is wrong must be 0.0 when buy
        base_balance, base_balance_value, valid_order, condition = self.validate_orders(validate_data)
        if valid_order:
            return base_balance, True, condition
        else:
            if base_balance is not None and base_balance_value > 1.0:
                return None, False, condition
            elif base_balance is None or base_balance == 0.0:
                if validate_data['open_orders'] is not None:
                    if not validate_data['open_orders'].empty:
                        self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: '
                                                             f'{validate_data["side"]} order will not be placed '
                                                             f'for {validate_data["trading_pair"]} there is an open order.')
                        return None, False, condition  # not a valid order
                else:
                    self.log_manager.webhook_logger.info(f'fetch_and_validate_rules: {validate_data["side"]} order not '
                                                         f'valid. {validate_data["trading_pair"]} balance is {base_balance}')

            return None, False, condition

    def validate_orders(self, validate_data):
        # Use a helper function to fetch and convert data safely
        def get_decimal_value(key, default='0'):
            try:
                value = validate_data.get(key, default)
                if value is None:
                    return Decimal(default)
                return Decimal(value)
            except InvalidOperation:
                return Decimal(default)

        quote_currency = validate_data.get('quote_currency', '')  # buy
        base_currency = validate_data.get('base_currency', '')  # sell
        trading_pair = validate_data.get('trading_pair', '')
        side = validate_data.get('side', '')

        quote_balance = get_decimal_value('quote_balance')
        base_balance = get_decimal_value('base_balance')
        highest_bid = get_decimal_value('highest_bid')
        quote_price = get_decimal_value('quote_price')
        quote_amount = get_decimal_value('quote_amount')
        base_deci = validate_data.get('base_decimal', 0)
        quote_deci = validate_data.get('quote_decimal', 0)
        open_orders = validate_data.get('open_orders', None)
        condition = None
        valid_order = False

        convert = 'usd' if quote_currency == 'USD' else 'quote'
        adjusted_quote_balance = self.utility.adjust_precision(base_deci, quote_deci, quote_balance, convert=convert)

        base_balance_value = Decimal(0)
        if base_currency != 'USD' and base_balance != Decimal(0):
            base_balance_value = (base_balance * highest_bid * quote_price)
            base_balance_value = self.utility.adjust_precision(base_deci, quote_deci, base_balance_value, convert=convert)

        if open_orders is not None and not open_orders.empty:
            open_orders['product_id'] = open_orders['product_id'].str.replace('-', '/')
            for _, order in open_orders.iterrows():
                if order.get('product_id') == trading_pair and order.get('remaining', 0) > 0:
                    condition = 'open order exists'
                    return base_balance, base_balance_value, valid_order, condition  # Order is not valid if open order
                    # exists
        else:
            condition = f'there is a balance of {base_balance_value} '

        hodling = base_currency in self.hodl
        if side == 'buy':
            if adjusted_quote_balance < quote_amount:
                self.log_manager.webhook_logger.info(
                    f'validate_orders: Insufficient funds ${adjusted_quote_balance} to {side}: '
                    f'{trading_pair} ${quote_amount}.00 is required.')
            elif adjusted_quote_balance > quote_amount and (hodling or base_balance_value <= Decimal('10.01')):
                condition = 'buy'
                valid_order = True
        elif side == 'sell' and not hodling:
            condition = 'not hodling'
            valid_order = base_balance_value > Decimal('1.0')

        return base_balance, base_balance_value, valid_order, condition
