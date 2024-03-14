"""should execute orders based on instructions from TradingStrategy but should not directly depend on the strategy logic"""
from decimal import Decimal

import pandas as pd

import traceback


class OrderManager:
    def __init__(self, trading_strategy, exchange, webhook, utility, alerts, logmanager, ccxt_api, profit_helper, config):
        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.alerts = alerts
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.profit_helper = profit_helper
        self._version = config.program_version
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache,  web_url, hist_holdings):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    @property
    def version(self):
        return self._version

    def get_open_orders(self, holdings, usd_pairs, fetch_all=True):  # async
        """ Fetch open orders for ALL USD paired coins  and process the data to determine if the order should be
        cancelled."""

        try:
            symbols_to_check = []
            if fetch_all:
                symbols_to_check = usd_pairs
                for symbol_dict in symbols_to_check:
                    if 'id' in symbol_dict:
                        symbol_dict['id'] = symbol_dict['id'].replace('-', '/')  # change format so it will
                # work with filtered orders
            else:  # check only coins in portfolio
                for symbol in holdings:
                    if symbol['id']:
                        symbols_to_check.append(symbol['id'].replace('-', '/'))

            params = {
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 10  # Set the max number of pagination calls if necessary
            }
            # fetch all buy/sell open orders
            all_open_orders = self.ccxt_exceptions.ccxt_api_call(lambda:  # await
                                                                       self.exchange.fetch_open_orders(None, params=params))

            # includes
            # buy/sell orders
            all_open_orders = self.format_open_orders(all_open_orders)

            if len(all_open_orders) == 0:  # no open orders for coins in portfolio
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: No open orders found.')
                return None
            else:  # open orders exist
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: Found {len(all_open_orders)}'
                                                      f' open orders.')
                stale_orders = self.cancel_stale_orders(all_open_orders)  # await
                return stale_orders
        except Exception as gooe:
            self.log_manager.sighook_logger.error(f'order_manager: get_open_orders: Exception occurred during '
                                                  f'api(Coinbase Cloud) call: {gooe}')
            return None

    def cancel_stale_orders(self, open_orders):  # async
        """Cancel stale orders. Stale orders are defined as buy orders that are 2% above the current ask price and sell"""
        stale_order_indices = []  # Collect indices of stale orders
        order_id = None
        for index, order in open_orders.iterrows():
            try:
                # Fetch Current Prices for the Order
                symbol = order['product_id'].replace('/', '-')
                # Pass function and arguments separately to ccxt_exceptions.ccxt_api_call
                ticker = self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, symbol)  # await
                base_deci, quote_deci = self.utility.fetch_precision(ticker)
                current_ask = self.utility.adjust_precision(base_deci, quote_deci, Decimal(ticker['ask']), 'base')
                current_bid = self.utility.adjust_precision(base_deci, quote_deci, Decimal(ticker['bid']), 'base')
                limit_price = self.utility.adjust_precision(base_deci, quote_deci, Decimal(order['amount']), 'base')
                order_id = order['order_id']
                is_buy_order = order['side'].upper() == 'BUY'

                if is_buy_order and limit_price * Decimal('1.02') < current_ask:
                    self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order, order_id)  # await
                    stale_order_indices.append(index)
                    print(f"Cancelled stale buy order for {symbol} at {limit_price}. Current ask: {current_ask}")
                elif is_buy_order and (limit_price * Decimal('0.98')) > current_bid:
                    self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order, order_id)  # await
                    stale_order_indices.append(index)
                    print(f"Cancelled stale sell order for {symbol} at {limit_price}. Current bid: {current_bid}")

            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'update_ticker_cache: {error_details}')
                symbol = order['product_id'].replace('/', '-')
                self.log_manager.sighook_logger.error(f'order_manager: Error processing order {order_id} for {symbol}: {e}')
                continue
        # Drop stale orders from DataFrame outside the loop
        open_orders.drop(stale_order_indices, inplace=True)
        return open_orders

    def get_filled_orders(self, product_id, counter):  # async
        counter['processed'] += 1
        try:
            symbol = product_id.replace('-', '/')
            if symbol == 'USD/USD':
                return None, counter
            filled_orders = self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_closed_orders(  # await
                symbol=symbol))

            if filled_orders is None:
                raise ValueError('order_manager:: Received None from get_open_orders api(Coinbase Cloud) call')
            else:

                return filled_orders,  counter

        except Exception as e:
            self.log_manager.sighook_logger.error(f'order_manager:: get_filled_orders: Exception occurred during '
                                                  f'api(Coinbase Cloud) call: {e}')
            return None, counter

    def process_sell_order(self, product_id, current_price, holdings, trigger=None):  # async
        try:
            for holding in holdings:
                # Implement trailing stop logic
                # Check if current_price is below the stop price calculated from peak price
                if self.profit_helper.is_stop_triggered(holding, current_price):
                    sell_action, sell_pair, sell_limit, sell_order = self.trading_strategy.sell_signal(product_id,
                                                                                                       current_price,
                                                                                                       holding, trigger)
                    if sell_action:
                        self.webhook.send_webhook(sell_action, sell_pair, sell_limit, sell_order)  # await
                        # Update profit_data with realized gains after the sale
                        self.profit_helper.update_realized_gains(product_id, sell_order, current_price, holding['Balance'])

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'process_sell_order: {error_details}')
            self.log_manager.sighook_logger.error(f'process_sell_order: An error occurred: {e}')

    @staticmethod
    def format_open_orders(open_orders: list) -> pd.DataFrame:
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.

        Parameters:


        Returns:
        - list: A list of dictionaries containing the required data.
        """

        data_to_load = [{
            'order_id': order['id'],
            'product_id': order['info']['product_id'],
            'side': order['info']['side'],
            'size': order['amount'],
            'amount': order['price'],
            'filled': order['filled'],
            'remaining': order['remaining']
        } for order in open_orders]
        df = pd.DataFrame(data_to_load)
        return df

