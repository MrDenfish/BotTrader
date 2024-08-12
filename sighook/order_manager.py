import pandas as pd
from decimal import Decimal, ROUND_HALF_UP
import asyncio
import aiohttp
import os
import time


class OrderManager:
    def __init__(self, trading_strategy, ticker_manager, exchange, webhook, utility, alerts, logmanager, ccxt_api,
                 profit_helper, config, max_concurrent_tasks=10):

        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.ticker_manager = ticker_manager
        self.alerts = alerts
        self.log_manager = logmanager
        self.ccxt_api = ccxt_api
        self.utility = utility
        self.profit_helper = profit_helper
        self._version = config.program_version
        self._min_sell_value = Decimal(config.min_sell_value)
        self._trailing_percentage = Decimal(config.trailing_percentage)  # Default trailing stop at 0.5%
        self._hodl = config.hodl
        self._take_profit = Decimal(config.take_profit)
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

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    async def open_http_session(self):
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()

    async def close_http_session(self):
        if self.http_session:
            await self.http_session.close()
            self.http_session = None

    async def check_prepare_trailing_stop_orders(self, open_orders, current_prices):
        """
        Check and update trailing stop orders.
        """
        trailing_stop_pending = open_orders[open_orders['trigger_status'] == 'STOP_PENDING']   # Active Status
        trailing_stop_triggered = open_orders[open_orders['trigger_status'] == 'STOP_TRIGGERED']

        # Handle STOP_PENDING orders
        for _, order in trailing_stop_pending.iterrows():
            symbol = order['product_id']
            ticker = symbol.replace('-', '/')
            _, quote_deci = self.utility.fetch_precision(ticker)
            new_limit_price = self.utility.float_to_decimal(current_prices.get(ticker), quote_deci)
            trigger_price = order['trigger_price']
            stop_price = order['stop_price']
            if new_limit_price:
                if new_limit_price > trigger_price:  # price is rising increase stop_loss value
                    stop_loss = self.calculate_stop_limit_price(new_limit_price, self.trailing_percentage)
                    # edit order
                    webhook_payload = await self.prepare_trailing_stop_webhook_payload(order, stop_loss, new_limit_price)
                elif trigger_price > new_limit_price > stop_price:  # price is falling
                    webhook_payload = await self.prepare_trailing_stop_webhook_payload(order, stop_price, new_limit_price)
                elif new_limit_price <= trigger_price <= stop_price: # sell at a loss
                    webhook_payload = await self.prepare_trailing_stop_webhook_payload(order, stop_loss, new_limit_price)
                    await self.alerts.send_webhook(self.http_session, webhook_payload)

        # Optionally, handle STOP_TRIGGERED orders if needed
        # In most cases, STOP_TRIGGERED do not nee to be handled, but here's an example if you do
        for _, order in trailing_stop_triggered.iterrows():
            # Log or handle STOP_TRIGGERED orders if necessary
            self.log_manager.sighook_logger.info(f"Trailing stop order {order['order_id']} is in STOP_TRIGGERED state.")

    async def prepare_trailing_stop_webhook_payload(self, order, new_stop_loss, new_limit_price):
        """
        Update the trailing stop order with the new limit price.
        """
        symbol = order['product_id'].replace('-', '')

        webhook_payload = {
            'timestamp': time.time(),
            'pair': symbol,
            'order_id': order['order_id'],
            'action': 'edit_order',
            'side': 'SELL',
            'amount': order['size'],
            'limit_price': str(new_limit_price),
            'stop_loss': str(new_stop_loss),
            'take_profit': str((1 + self.take_profit) * new_limit_price),
            'origin': "SIGHOOK",
            'verified': "valid or not valid "  # this will be used to verify the order
        }
        return webhook_payload

    @staticmethod
    def calculate_stop_limit_price(new_limit_price, trailing_stop_percentage):
        """
        Calculate the new limit price based on the current price and trailing stop percentage.
        """
        stop_loss = new_limit_price * (1 - trailing_stop_percentage / 100)
        return stop_loss

    async def get_open_orders(self):  # async
        """PART III: Trading Strategies"""
        """ Fetch open orders for ALL USD paired coins  and process the data to determine if the order should be
        cancelled."""
        endpoint = 'private'
        try:
            # symbols_to_check = usd_pairs if fetch_all else [symbol['id'].replace('-', '/') for symbol in holdings if
            #                                                 symbol['id']]
            params = {'paginate': True, 'paginationCalls': 10}

            all_open_orders = await self.ccxt_api.ccxt_api_call(
                self.exchange.fetch_open_orders, endpoint, None, params=params)

            if not all_open_orders:
                self.log_manager.sighook_logger.debug('order_manager: get_open_orders: No open orders found.')
                return None

            self.log_manager.sighook_logger.debug(
                f'order_manager: get_open_orders: Found {len(all_open_orders)} open orders.')
            all_open_orders = self.format_open_orders(all_open_orders)
            open_orders = await self.cancel_stale_orders(all_open_orders)
            return open_orders
        except Exception as gooe:
            self.log_manager.sighook_logger.error(f'get_open_orders: {gooe}', exc_info=True)
            return None

    async def cancel_stale_orders(self, open_orders):
        """PART III: Trading Strategies """
        """Cancel stale BUY  orders based on pre-fetched ticker data."""
        ticker_data = []
        try:
            symbols = set(open_orders['product_id'].str.replace('/', '-'))
            ticker_tasks = [self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, 'public', symbol) for symbol in symbols]
            ticker_data = await asyncio.gather(*ticker_tasks)
            ticker_df = pd.DataFrame(
                [(symbol, Decimal(ticker['ask']), Decimal(ticker['bid'])) for symbol, ticker in zip(symbols, ticker_data) if
                 ticker],
                columns=['symbol', 'ask', 'bid'])

            merged_orders = pd.merge(open_orders, ticker_df, left_on=open_orders['product_id'].str.replace('/', '-'),
                                     right_on='symbol', how='left')

            merged_orders['price'] = merged_orders['price'].apply(Decimal)
            merged_orders['ask'] = merged_orders['ask'].apply(Decimal)
            merged_orders['bid'] = merged_orders['bid'].apply(Decimal)
            merged_orders['time active (minutes)'] = merged_orders['time active (minutes)'].str.replace(' minutes',
                                                                                                        '').astype(int)
            merged_orders['time active > 5 minutes'] = merged_orders['time active (minutes)'] > 5

            merged_orders['is_stale'] = (
                    ((merged_orders['side'] == 'BUY') &
                     ((merged_orders['price'] * Decimal('1.02') < merged_orders['ask']) |
                      (merged_orders['price'] * Decimal('0.98') > merged_orders['bid'])) &
                     (merged_orders['time active > 5 minutes'])) |
                    ((merged_orders['side'] == 'SELL') &
                     (merged_orders['price'] < merged_orders['ask'] * Decimal('0.98')))
            )

            stale_orders = merged_orders[merged_orders['is_stale']]
            cancel_tasks = [self.cancel_order(order_id) for order_id in stale_orders['order_id']]
            await asyncio.gather(*cancel_tasks)

            non_stale_orders = merged_orders[~merged_orders['is_stale']].drop(columns=['is_stale', 'symbol', 'ask', 'bid'])
            return non_stale_orders

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error cancelling stale orders: {e}', exc_info=True)
            return None

    async def cancel_order(self, order_id):
        """PART III: Trading Strategies """
        endpoint = 'private'
        async with self.ccxt_api.get_semaphore(endpoint):
            await self.ccxt_api.ccxt_api_call(self.exchange.cancel_order, endpoint, order_id)

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
                'price': order['price'],
                'trigger_status': order['info']['trigger_status'],
                'trigger_price': order['triggerPrice'],
                'stop_price': order['stopPrice'],
                'filled': order['filled'],
                'remaining': order['remaining'],
                'time active': order['info']['created_time']
            } for order in open_orders]
            df = pd.DataFrame(data_to_load)
            base_deci, quote_deci = self.utility.fetch_precision(df['product_id'])

            df['size'] = df.apply(
                lambda row: self.utility.adjust_precision(base_deci, quote_deci, Decimal(row['size']), 'base'), axis=1)
            df['price'] = df.apply(
                lambda row: self.utility.adjust_precision(base_deci, quote_deci, Decimal(row['price']), 'base'), axis=1)
            df['time active (minutes)'] = df['time active'].apply(lambda x: self.utility.calculate_time_difference(x))
            df['time_temp'] = pd.to_numeric(df['time active (minutes)'], errors='coerce')
            df['time active > 5 minutes'] = df['time_temp'] > 5
            df.drop(columns=['time_temp'], inplace=True)

            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error formatting open orders: {e}', exc_info=True)

    async def execute_actions(self, results, holdings):
        """PART V: Order Execution"""
        execution_tasks = []
        try:
            for result in results:
                if result is None or isinstance(result, Exception) or "error" in result:
                    continue  # Skip this result and move on to the next
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
                    'symbol': order.get('buy_pair') if order.get('buy_action') else order.get('sell_symbol'),
                    'action': 'buy' if order.get('buy_action') else 'sell',
                    'trigger': order.get('trigger')
                }
                for order in filtered_orders if order.get('buy_action') or order.get('sell_action')
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
            asset = order['symbol'].split('/')[0]
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
                elif action_type == 'trailing_stop':
                    result = self.handle_trailing_stop(holdings, symbol, price, trigger, sell_cond)
                    results.append(result)

            return results  # Process results as needed
        except Exception as e:
            if "No market found" in str(e):
                self.log_manager.sighook_logger.info(f"No market found: {e}")
            else:
                self.log_manager.sighook_logger.error(f"Error handling actions: {e}", exc_info=True)
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

                buy_order = 'limit'
                webhook_payload = {
                    'timestamp': time.time(),
                    'pair': symbol.replace('/', ''),
                    'order_id': None,
                    'action': buy_action,
                    'order_type': buy_order,
                    'side': 'BUY',
                    'order_size': None,
                    'limit_price': price,
                    'stop_loss': None,
                    'take_profit': None,
                    'origin': "SIGHOOK",
                    'verified': "valid or not valid "  # this will be used to verify the order
                }

                response = await self.webhook.send_webhook(self.http_session, webhook_payload)
                if response:
                    if response.status in [403, 429, 500]:  #
                        await self.close_http_session()
                        return []
                else:
                    await self.close_http_session()
                    return []
                # await
                self.log_manager.sighook_logger.buy(f'{symbol} buy signal triggered @ {buy_action} price'
                                                    f' {price}, USD balance: ${usd_balance}')
                return ({'buy_action': buy_action, 'buy_pair': symbol, 'buy_limit': price, 'curr_band_ratio':
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
                    webhook_payload = {
                        'timestamp': time.time(),
                        'pair': symbol.replace('/', ''),
                        'order_id': None,
                        'action': sell_action,
                        'order_type': sell_order,
                        'side': 'SELL',
                        'amount': None,
                        'limit_price': price,
                        'stop_loss': None,
                        'take_profit': None,
                        'origin': "SIGHOOK",
                        'verified': "valid or not valid "  # this will be used to verify the order
                    }

                    await self.webhook.send_webhook(self.http_session, webhook_payload)
                    # await
                    self.log_manager.sighook_logger.sell(f'{symbol} sell signal triggered from {trigger} @'
                                                         f' {sell_action} price' f' {sell_limit}')

                    return ({'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
                            'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
                             'sell_cond': sell_cond, 'trigger': trigger})
            else:
                webhook_payload = {
                    'timestamp': time.time(),
                    'pair': symbol.replace('/', ''),
                    'order_id': None,
                    'action': 'close_at_limit',
                    'order_type': 'limit',
                    'side': 'SELL',
                    'amount': None,
                    'limit_price': price,
                    'stop_loss': None,
                    'take_profit': None,
                    'origin': "SIGHOOK",
                    'verified': "valid or not valid "  # this will be used to verify the order
                }
                await self.webhook.send_webhook(self.http_session, webhook_payload)  # await
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

    async def handle_trailing_stop(self, holdings, symbol, price, trigger, sell_cond):
        """PART V: Order Execution"""
        try:
            # Prepare sell action data
            coin = symbol.split('/')[0]
            if trigger not in ['profit', 'loss']:
                sell_action, sell_symbol, sell_limit, sell_order = (self.trading_strategy.sell_signal_from_indicators(
                    symbol, price, trigger, holdings))
                if sell_action and (coin not in self.hodl):   # Hold specified (.env) coins for accumulation.
                    webhook_payload = {
                        'timestamp': time.time(),
                        'pair': symbol.replace('/', ''),
                        'order_id': None,
                        'action': sell_action,
                        'order_type': sell_order,
                        'side': 'SELL',
                        'order_size': None,
                        'limit_price': price,
                        'stop_loss': None,
                        'take_profit': None,
                        'origin': "SIGHOOK",
                        'verified': "valid or not valid "  # this will be used to verify the order
                    }

                    await self.webhook.send_webhook(self.http_session, webhook_payload)
                    # await
                    self.log_manager.sighook_logger.sell(f'{symbol} sell signal triggered from {trigger} @'
                                                         f' {sell_action} price' f' {sell_limit}')

                    return ({'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
                            'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
                             'sell_cond': sell_cond, 'trigger': trigger})
            else:

                webhook_payload = {
                    'timestamp': time.time(),
                    'pair': symbol.replace('/', ''),
                    'order_id': None,
                    'action': 'close_at_limit',
                    'order_type': 'limit',
                    'side': 'SELL',
                    'amount': None,
                    'limit_price': price,
                    'stop_loss': None,
                    'take_profit': None,
                    'origin': "SIGHOOK",
                    'verified': "valid or not valid "  # this will be used to verify the order
                }
                await self.webhook.send_webhook(self.http_session, webhook_payload)  # await
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
