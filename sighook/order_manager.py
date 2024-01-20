"""should execute orders based on instructions from TradingStrategy but should not directly depend on the strategy logic"""
from decimal import Decimal


class OrderManager:
    def __init__(self, trading_strategy, exchange, webhook, utility, coms, logmanager, ccxt_api):
        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.coms = coms
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.ticker_cache = None
        self.start_time = None
        self.web_url = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.web_url = web_url
        self.current_holdings = hist_holdings

    def get_open_orders(self, old_portfolio, usd_pairs, fetch_all=True):
        """ Fetch open orders for ALL USD paired coins  and process the data to determine if the order should be
        cancelled."""

        # orders = []
        coin_balance = []
        try:
            symbols_to_check = []
            if fetch_all:
                symbols_to_check = usd_pairs
                for symbol_dict in symbols_to_check:
                    if 'id' in symbol_dict:
                        symbol_dict['id'] = symbol_dict['id'].replace('-', '/')  # change format so it will
                # work with filtered orders
            else:  # check only coins in portfolio
                for symbol in old_portfolio:
                    if symbol['id']:
                        symbols_to_check.append(symbol['id'].replace('-', '/'))

            # fetch all buy/sell open orders
            all_open_orders = self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_open_orders(None))  # includes
            # buy/sell orders
            all_open_orders = self.utility.format_open_orders(all_open_orders)
            if len(all_open_orders) > 0:
                print(f'Open orders: {all_open_orders.to_string(index=False)}')
            else:
                print(f'No open orders found')

            if len(all_open_orders) == 0:  # no open orders for coins in portfolio
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: No open orders found.')
                return coin_balance, None
            else:  # open orders exist
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: Found {len(all_open_orders)}'
                                                      f' open orders.')
                stale_orders = self.cancel_stale_orders(all_open_orders)
                return coin_balance, stale_orders
        except Exception as gooe:
            self.log_manager.sighook_logger.error(f'order_manager: get_open_orders: Exception occurred during '
                                                  f'api(Coinbase Cloud) call: {gooe}')
            return coin_balance, None

    def cancel_stale_orders(self, open_orders):
        """Cancel stale orders. Stale orders are defined as buy orders that are 2% above the current ask price and sell"""
        for index, order in open_orders.iterrows():
            try:
                # Fetch Current Prices for the Order
                symbol = order['product_id'].replace('/', '-')
                ticker = self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_ticker(symbol))
                current_ask = Decimal(ticker['ask'])
                current_bid = Decimal(ticker['bid'])
            except Exception as e:
                symbol = order['product_id'].replace('/', '-')
                self.log_manager.sighook_logger.error(f'order_manager: cancel_stale_orders:An error occurred while '
                                                      f'fetching ticker for {symbol}: {e}')
                continue
            try:
                order_id = order['order_id']
                # Compare and Cancel
                limit_price = Decimal(order['amount'])
                is_buy_order = order['side'] == 'BUY'

                if is_buy_order:
                    if limit_price * Decimal('1.02') < current_ask:
                        self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.cancel_order(order_id))
                        # remove canceled orders from orders list
                        open_orders.drop(open_orders[open_orders['order_id'] == order_id].index, inplace=True)
                        print(f"Cancelled stale buy order for {symbol} at {limit_price}. Current ask: {current_ask}")
                        self.log_manager.sighook_logger.info(f'order_manager: cancel_stale_orders: Cancelled stale buy '
                                                             f'order for {symbol} at {limit_price}.')
                else:  # It's a sell order
                    if limit_price * Decimal('0.98') > current_bid:
                        self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order(order_id))
                        open_orders.drop(open_orders[open_orders['order_id'] == order_id].index, inplace=True)
                        print(f"Cancelled stale sell order for {symbol} at {limit_price}. Current bid: {current_bid}")
                        self.log_manager.sighook_logger.info(f'order_manager: cancel_stale_orders: Cancelled stale '
                                                             f'buy order for {symbol} at {limit_price}.')
            except Exception as e:
                self.log_manager.sighook_logger.error(f'order_manager: cancel_stale_orders:An error occurred while '
                                                      f'processing or canceling the  cancel order for {symbol}: {e}')
                continue
            return open_orders
            # Log (adapt as needed)

    def get_filled_orders(self, product_id):
        try:
            symbol = product_id.replace('-', '/')
            if symbol == 'USD/USD':
                return None
            filled_orders = self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_my_trades(symbol=symbol))
            if filled_orders is None:
                raise ValueError('order_manager:: Received None from get_open_orders api(Coinbase Cloud) call')
            else:
                return filled_orders

        except Exception as e:
            self.log_manager.sighook_logger.error(f'order_manager:: get_filled_orders: Exception occurred during '
                                                  f'api(Coinbase Cloud) call: {e}')
            return None

    def process_sell_order(self, product_id, current_price, old_portfolio, purchase_decimal, diff_decimal):
        sell_cond = True
        sell_action, sell_pair, sell_limit, sell_order = self.trading_strategy.sell_signal(
            product_id, current_price, sell_cond, old_portfolio)
        if sell_action:
            self.webhook.send_webhook(sell_action, sell_pair, sell_limit, sell_order)
            message = f'Profit opportunity: {product_id},Purchase price:{purchase_decimal}, Close price:' \
                      f'{current_price}. Gain: {diff_decimal}%'
            self.coms.callhome('Profit Opportunity', message)
