import pandas as pd
from decimal import Decimal, ROUND_HALF_UP
import asyncio
import aiohttp


class OrderManager:
    def __init__(self, trading_strategy, ticker_manager, exchange, webhook, utility, alerts, logmanager, ccxt_api,
                 profit_helper, config, max_concurrent_tasks=10):

        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.ticker_manager = ticker_manager
        self.alerts = alerts
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.profit_helper = profit_helper
        self._version = config.program_version
        self._min_sell_value = Decimal(config.min_sell_value)
        self._hodl = config.hodl
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.ticker_cache = None
        self.http_session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache,  web_url):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def open_http_session(self):
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()

    async def close_http_session(self):
        if self.http_session:
            await self.http_session.close()
            self.http_session = None

    async def get_open_orders(self, holdings, usd_pairs, fetch_all=True):  # async
        """PART III: Trading Strategies"""
        """ Fetch open orders for ALL USD paired coins  and process the data to determine if the order should be
        cancelled."""
        endpoint = 'private'  # for rate limiting
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
            all_open_orders = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_open_orders, endpoint,
                                                                       params=params)

            if len(all_open_orders) == 0:  # no open orders for coins in portfolio
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: No open orders found.')
                return None
            else:  # open orders exist
                self.log_manager.sighook_logger.debug(f'order_manager: get_open_orders: Found {len(all_open_orders)}'
                                                      f' open orders.')
                all_open_orders = self.format_open_orders(all_open_orders)
                open_orders = await self.cancel_stale_orders(all_open_orders)  # await
                return open_orders
        except Exception as gooe:
            self.log_manager.sighook_logger.error(f'get_open_orders: {gooe}', exc_info=True)
            return None

    async def cancel_stale_orders(self, open_orders):
        """PART III: Trading Strategies """
        """Cancel stale orders based on pre-fetched ticker data."""
        try:
            # Fetch ticker data for unique symbols in open_orders
            symbols = set(open_orders['product_id'].str.replace('/', '-'))
            ticker_tasks = [self.fetch_ticker_data(symbol) for symbol in symbols]
            ticker_data = await asyncio.gather(*ticker_tasks)

            # Create a DataFrame from the fetched ticker data
            ticker_df = pd.DataFrame(
                [(symbol, Decimal(ticker['ask']), Decimal(ticker['bid'])) for symbol, ticker in ticker_data if ticker],
                columns=['symbol', 'ask', 'bid'])

            # Merge open_orders with ticker_df
            merged_orders = pd.merge(open_orders, ticker_df, left_on=open_orders['product_id'].str.replace('/', '-'),
                                     right_on='symbol', how='left')

            # Ensure all relevant columns are converted to Decimal
            merged_orders['amount'] = merged_orders['amount'].apply(Decimal)
            merged_orders['ask'] = merged_orders['ask'].apply(Decimal)
            merged_orders['bid'] = merged_orders['bid'].apply(Decimal)

            # Calculate 'is_stale' using vectorized operations
            merged_orders['is_stale'] = (((merged_orders['side'].str.upper() == 'BUY') &
                                          ((merged_orders['amount'] * Decimal('1.02') < merged_orders['ask']) |
                                           (merged_orders['amount'] * Decimal('0.98') > merged_orders['bid']))) |
                                         ((merged_orders['side'].str.upper() == 'SELL') &
                                          (merged_orders['amount'] < merged_orders['ask'] * Decimal('0.98'))))

            # Filter stale orders
            stale_orders = merged_orders[merged_orders['is_stale']]

            # Concurrently cancel stale orders
            cancel_tasks = [self.cancel_order(order_id) for order_id in stale_orders['order_id']]
            await asyncio.gather(*cancel_tasks)

            # Return non-stale orders
            non_stale_orders = merged_orders[~merged_orders['is_stale']].drop(columns=['is_stale', 'symbol', 'ask', 'bid'])
            return non_stale_orders

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error cancelling stale orders: {e}', exc_info=True)
            return None

    async def fetch_ticker_data(self, symbol):
        """PART III: Order cancellation and Data Collection """
        """Fetch ticker data for a symbol."""
        try:
            endpoint = 'public'  # for rate limiting
            ticker = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, endpoint, symbol)
            return symbol, ticker
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error fetching ticker for {symbol}: {e}', exc_info=True)
            return symbol, None

    async def cancel_order(self, order_id):
        """PART III: Trading Strategies """
        endpoint = 'private'  # for rate limiting
        await self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order, endpoint, order_id)

    def format_open_orders(self, open_orders: list) -> pd.DataFrame:
        """PART III: Trading Strategies """
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.

        Parameters:

        Returns:
        - list: A list of dictionaries containing the required data.
        """
        try:
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
            base_deci, quote_deci = self.utility.fetch_precision(df['product_id'])
            df['size'] = df.apply(lambda row: self.utility.adjust_precision(base_deci, quote_deci, Decimal(row['size']),
                                                                            'base'), axis=1)
            df['amount'] = df.apply(lambda row: self.utility.adjust_precision(base_deci, quote_deci, Decimal(row['amount']),
                                                                              'base'), axis=1)
            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error formatting open orders: {e}', exc_info=True)

    async def execute_actions(self, results, holdings):
        """PART V: Order Execution"""
        execution_tasks = []
        try:
            # Initialize an empty DataFrame for orders
            orders_df = pd.DataFrame(columns=['symbol', 'action', 'trigger'])
            for result in results:
                if 'order_info' in result and result['order_info']['action'] in ['buy', 'sell']:
                    execution_tasks.append(self.handle_actions(result['order_info'], holdings))

            if execution_tasks:  # Check if there are any tasks to execute
                execution_results = await asyncio.gather(*execution_tasks, return_exceptions=True)
            else:
                execution_results = []
            # Check each item if it's not empty and the first element is not None
            filtered_orders = []
            for item in execution_results:
                if isinstance(item, Exception):
                    self.log_manager.sighook_logger.error(f"Error executing actions: {item}", exc_info=True)
                elif item and item[0] is not None:
                    filtered_orders.append(item[0])

            # Processed orders with the desired structure
            processed_orders = [
                {
                    'symbol': order['buy_pair'] if order['buy_action'] else order['sell_symbol'],
                    'action': 'buy' if order['buy_action'] else 'sell',
                    'trigger': order['trigger']
                }
                for order in filtered_orders if order['buy_action'] or order['sell_action']
            ]
            return pd.DataFrame(processed_orders, columns=['symbol', 'action', 'trigger'])

        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error executing actions: {e}", exc_info=True)
            return None

    async def handle_actions(self, order, holdings):
        """PART V: Order Execution
           PART VI: Profitability Analysis and Order Generation """
        await self.open_http_session()  # Ensure the session is open before handling actions
        try:
            _, quote_deci = self.utility.fetch_precision(order['symbol'])
            symbol = order['symbol']
            action_type = order['action']
            price = order['price']
            price = self.utility.float_to_decimal(price, quote_deci)
            value = order.get('value', 0)  # Default value if not present
            bollinger_data = order.get('bollinger_df', {})  # Safe access with default
            action_data = order['action_data']  # Assuming 'action_data' must exist

            results = []

            for coin in action_data['updates'].keys():  # key is the coin symbol
                balances = await self.ticker_manager.fetch_balance_and_filter()

                action_type = action_data.get('action')
                trigger = action_data.get('trigger')
                band_ratio = action_data.get('band_ratio', None)
                sell_cond = action_data.get('sell_cond', None)
                if coin not in balances['filtered']:
                    coin_balance = Decimal('0')
                else:
                    coin_balance = Decimal(balances['filtered'][coin]['free'])
                usd_balance = Decimal(balances['filtered']['USD']['free'])
                if action_type == 'buy':
                    result = await self.handle_buy_action(symbol, price, coin_balance, usd_balance, band_ratio, trigger)
                    results.append(result)
                elif action_type == 'sell' and value > self.min_sell_value:
                    result = await self.handle_sell_action(holdings, symbol, price, trigger, sell_cond)
                    results.append(result)

            return results  # Process results as needed
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error fetching symbols from actions: {e}")
            return []

    async def handle_buy_action(self, symbol, price, coin_balance, usd_balance, band_ratio, trigger):
        """PART V: Order Execution"""
        try:
            usd_balance = usd_balance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            coin_balance_value = coin_balance * Decimal(price)
            coin = symbol.split('/')[0]
            if ((usd_balance > 100 and coin_balance_value < self.min_sell_value)
                    or (usd_balance > 50 and (coin in self.hodl))):  # accumulate BTC, ETH etc
                # Prepare buy action data
                buy_action = 'open_at_limit'
                buy_pair = symbol
                buy_limit = price
                buy_order = 'limit'
                response = await self.webhook.send_webhook(self.http_session, buy_action, buy_pair, buy_limit, buy_order)
                if response.status in [403, 429, 500]:  #
                    await self.close_http_session()
                    return []
                # await
                self.log_manager.sighook_logger.buy(f'{symbol} buy signal triggered @ {buy_action} price'
                                                    f' {buy_limit}, USD balance: ${usd_balance}')
                return ({'buy_action': buy_action, 'buy_pair': buy_pair, 'buy_limit': buy_limit, 'curr_band_ratio':
                        band_ratio, 'sell_action': None, 'sell_symbol': None, 'sell_limit': None, 'sell_cond': None,
                         'trigger': trigger})
            elif usd_balance <= 100:
                print(f'Insufficient funds ${usd_balance} to buy {symbol}')
                return None
            else:
                print(f'Currently holding {symbol}.Buy signal will not be processed.')
                return None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'handle_buy_action: Error processing order  {symbol}: {e}', exc_info=True)
            return None

    async def handle_sell_action(self, holdings, symbol, price, trigger, sell_cond):
        """PART V: Order Execution"""
        try:
            # Prepare sell action data
            coin = symbol.split('/')[0]
            if trigger not in ['profit', 'loss']:
                sell_action, sell_symbol, sell_limit, sell_order = (self.trading_strategy.sell_signal_from_indicators(
                    symbol, price, trigger, holdings))
                if sell_action and (coin not in self.hodl):   # Hold specified (.env) coins for accumulation.
                    await self.webhook.send_webhook(self.http_session, sell_action, sell_symbol, sell_limit, sell_order)
                    # await
                    self.log_manager.sighook_logger.sell(f'{symbol} sell signal triggered from {trigger} @'
                                                         f' {sell_action} price' f' {sell_limit}')

                    return ({'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
                            'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
                             'sell_cond': sell_cond, 'trigger': trigger})
            else:
                sell_order = 'limit'
                sell_action = 'close_at_limit'
                await self.webhook.send_webhook(self.http_session, sell_action, symbol, price, sell_order)  # await
                if trigger == 'profit' and coin not in self.hodl:
                    self.log_manager.sighook_logger.take_profit(f'{symbol} sell signal triggered  {trigger} @ sell price'
                                                                f' {price}')
                elif trigger == 'loss' and coin not in self.hodl:
                    self.log_manager.sighook_logger.take_loss(f'{symbol} sell signal triggered  {trigger} @ sell price'
                                                              f' {price}')
                return None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'handle_sell_action: Error processing order for {symbol}: {e}',
                                                  exc_info=True)
            return None
