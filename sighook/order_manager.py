import pandas as pd
from decimal import Decimal
import asyncio
import aiohttp
import time
from Shared_Utils.config_manager import CentralConfig

class OrderManager:
    _instance = None

    @classmethod
    def get_instance(cls, trading_strategy, ticker_manager, exchange, webhook, alerts, logmanager, ccxt_api,
                        profit_helper, shared_utils_precision, max_concurrent_tasks=10):
        if cls._instance is None:
            cls._instance = cls(trading_strategy, ticker_manager, exchange, webhook, alerts, logmanager, ccxt_api,
                                profit_helper, shared_utils_precision, max_concurrent_tasks)
        return cls._instance

    def __init__(self, trading_strategy, ticker_manager, exchange, webhook, alerts, logmanager, ccxt_api,
                 profit_helper, shared_utils_precision, max_concurrent_tasks=10):
        self.config = CentralConfig()
        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.ticker_manager = ticker_manager
        self.shared_utils_precision = shared_utils_precision
        self.alerts = alerts
        self.log_manager = logmanager
        self.ccxt_api = ccxt_api
        self.profit_helper = profit_helper
        self._version = self.config.program_version
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)  # Default trailing stop at 0.5%
        self._hodl = self.config.hodl
        self._take_profit = Decimal(self.config.take_profit)
        self._cxl_buy = self.config.cxl_buy
        self._cxl_sell = self.config.cxl_sell
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.market_cache_vol, self.ticker_cache, self.filtered_balances, self.min_volume = None, None, None, None
        self.http_session, self.start_time, self.web_url  = None, None, None

    def set_trade_parameters(self, start_time, market_data,  order_management, web_url):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache']
        self.usd_pairs = market_data['usd_pairs_cache']
        self.market_cache_vol = market_data['filtered_vol']
        self.current_prices = market_data['current_prices']
        self.filtered_balances = order_management['non_zero_balances']
        self.open_orders = order_management['order_tracker']
        self.min_volume = Decimal(market_data['avg_quote_volume'])
        self.web_url = web_url

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def cxl_buy(self):
        return self._cxl_buy

    @property
    def cxl_sell(self):
        return self._cxl_sell

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def currency_pairs_ignored(self):
        return self._currency_pairs_ignored

    async def open_http_session(self):
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()

    async def close_http_session(self):
        if self.http_session:
            await self.http_session.close()
            self.http_session = None

    async def throttled_send(self, webhook_payload):
        """PART V:
        Throttle the send_webhook() function to limit concurrent requests.
        Args:
            webhook_payload (dict): The webhook payload to be sent.
        Returns:
            Response or None: The response from send_webhook() or None if it fails.
        """
        await self.open_http_session()  # Ensure the HTTP session is open
        async with self.semaphore:  # Acquire semaphore to limit concurrency
            try:
                response = await self.webhook.send_webhook(self.http_session, webhook_payload)
                return response
            except Exception as e:
                self.log_manager.error(f"Error in throttled_send: {e}", exc_info=True)
                return None


    async def get_open_orders(self):  # async
        """PART III: Trading Strategies"""
        """ Fetch open orders for ALL USD paired coins  and process the data to determine if the order should be
        cancelled."""
        try:
            all_open_orders = await self.format_open_orders_from_dict(self.open_orders)
            if not all_open_orders.empty:
                open_orders = await self.cancel_stale_orders(all_open_orders)
                return open_orders
            else:
                return None
        except Exception as gooe:
            self.log_manager.error(f'get_open_orders: {gooe}', exc_info=True)
            return None

    async def cancel_stale_orders(self, open_orders):
        """PART III: Trading Strategies """
        """Cancel stale BUY  orders based on pre-fetched ticker data."""
        ticker_data = []
        try:
            symbols = set(open_orders['product_id'].str.replace('/', '-'))
            asset = symbols.pop().split('-')[0]
            ticker_tasks = [self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, 'public', symbol) for symbol in symbols]
            ticker_data = await asyncio.gather(*ticker_tasks)
            ticker_df = pd.DataFrame(
                [(symbol, Decimal(ticker['ask']), Decimal(ticker['bid'])) for symbol, ticker in zip(symbols, ticker_data) if
                 ticker],
                columns=['symbol', 'ask', 'bid'])

            merged_orders = pd.merge(open_orders, ticker_df, left_on=open_orders['product_id'].str.replace('/', '-'),
                                     right_on='symbol', how='left')

            merged_orders = await self.adjust_merged_orders_prices(merged_orders)
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, self.usd_pairs)

            merged_orders['price'] = merged_orders['price'].apply(Decimal)
            merged_orders['ask'] = merged_orders['ask'].apply(Decimal)
            merged_orders['bid'] = merged_orders['bid'].apply(Decimal)
            merged_orders['price'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'price'], 'quote')
            merged_orders['ask'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'ask'], 'base')
            merged_orders['bid'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'bid'], 'base')

            if merged_orders['time active (minutes)'].dtype == 'object':
                merged_orders['time active (minutes)'] = merged_orders['time active (minutes)'].str.replace(' minutes', '')
            merged_orders['time active (minutes)'] = pd.to_numeric(merged_orders['time active (minutes)'],
                                                                   errors='coerce').fillna(0).astype(int)
            merged_orders['time active > 5 minutes'] = merged_orders['time active (minutes)'] > 5

            merged_orders['is_stale'] = (
                    ((merged_orders['side'] == 'buy') &
                    ((merged_orders['price'] < merged_orders['ask'] * Decimal(1 - Decimal(self.cxl_buy))) |
                    (merged_orders['price']   > merged_orders['bid'])) &
                    (merged_orders['time active > 5 minutes'])) |
                    ((merged_orders['side'] == 'sell') &
                    (merged_orders['price'] < merged_orders['ask'] * Decimal(1 - Decimal(self.cxl_sell))))
            )

            stale_orders = merged_orders[merged_orders['is_stale']]
            cancel_tasks = [self.cancel_order(order_id, product_id) for order_id, product_id in
                            zip(stale_orders['order_id'], stale_orders['product_id'])]

            await asyncio.gather(*cancel_tasks)
            non_stale_orders = merged_orders[~merged_orders['is_stale']].drop(columns=['is_stale', 'symbol', 'ask', 'bid'])
            return non_stale_orders

        except Exception as e:
            self.log_manager.error(f'Error cancelling stale orders: {e}', exc_info=True)
            return None

    async def cancel_order(self, order_id, product_id):
        """PART III: Trading Strategies """
        try:
            if order_id is not None:
                print(f'Cancelling order {product_id}:{order_id}')
                product_id = product_id.replace('-', '/')
                endpoint = 'private'
                async with self.ccxt_api.get_semaphore(endpoint):
                    await self.ccxt_api.ccxt_api_call(self.exchange.cancel_order,endpoint,order_id)
                    return
            print(f'‼️ Order {product_id}:{order_id}  was not cancelled')
            return
        except Exception as e:
            self.log_manager.error(f'Error cancelling order {product_id}:{order_id}: {e}', exc_info=True)

    async def adjust_merged_orders_prices(self, merged_orders):
        """
        Adjust the price of each order in the merged_orders DataFrame to align with the precision defined for the product_id.
        """
        try:
            for index, row in merged_orders.iterrows():
                # Fetch the precision for the symbol (product_id)
                product_id = row['product_id']
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(product_id, self.usd_pairs)

                # Adjust the price using the quote precision
                adjusted_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, row['price'], 'quote')

                # Update the price in the DataFrame
                merged_orders.at[index, 'price'] = float(adjusted_price)

            return merged_orders

        except Exception as e:
            self.log_manager.error(f"Error adjusting prices in merged_orders: {e}", exc_info=True)
            return merged_orders

    async def format_open_orders_from_dict(self, open_orders_dict: dict) -> pd.DataFrame:
        """
        Format the open orders data stored in a dictionary structure.

        Args:
            open_orders_dict (dict): Dictionary of open orders keyed by order ID.

        Returns:
            pd.DataFrame: A DataFrame containing formatted open order data.
        """
        try:
            # Convert the dictionary to a list of dictionaries
            data_to_load = [
                {
                    'order_id': order_id,
                    'product_id': order['info']['product_id'],
                    'side': order['info']['side'],
                    'size': order['amount'],
                    'price': round(order['price'],8),
                    'trigger_status': order['info']['trigger_status'],
                    'trigger_price': order.get('triggerPrice'),  # Use .get() to handle missing keys
                    'stop_price': order.get('stopPrice'),
                    'filled': order['filled'],
                    'remaining': order['remaining'],
                    'time active': order['info']['created_time']
                }
                for order_id, order in open_orders_dict.items()
            ]

            # Create a DataFrame from the list
            df = pd.DataFrame(data_to_load)

            # Ensure `time active` is parsed as datetime
            df['time active'] = pd.to_datetime(df['time active'], errors='coerce')

            # Calculate the time active in minutes
            current_time = pd.Timestamp.utcnow()
            df['time active (minutes)'] = df['time active'].apply(
                lambda x: (current_time - x).total_seconds() / 60 if pd.notnull(x) else None)

            # Convert to numeric type and check if time active exceeds 5 minutes
            df['time_temp'] = pd.to_numeric(df['time active (minutes)'], errors='coerce')
            df['time active > 5 minutes'] = df['time_temp'] > 5

            # Drop temporary column if not needed
            df.drop(columns=['time_temp'])

            return df
        except Exception as e:
            # Handle exceptions and log the error
            print(f"Error in format_open_orders_from_dict: {e}")
            return pd.DataFrame()

    async def execute_actions(self, strategy_results, holdings):
        """ PART V:
        Executes buy/sell actions based on strategy results.
        """
        try:
            execution_tasks = []

            for result in strategy_results:
                # Skip invalid or non-actionable strategy_results
                if result.get('action') not in ['buy', 'sell']:
                    continue

                execution_tasks.append(self.handle_actions(result, holdings))

            # Execute all tasks concurrently
            execution_results = await asyncio.gather(*execution_tasks, return_exceptions=True)

            # Filter and process successful orders
            processed_orders = [
                {
                    'symbol': order.get('buy_pair') if order.get('buy_action') else order.get('sell_symbol'),
                    'action': 'buy' if order.get('buy_action') else 'sell',
                    'trigger': order.get('trigger')
                }
                for order in execution_results if isinstance(order, dict)
            ]

            return pd.DataFrame(processed_orders, columns=['symbol', 'action', 'trigger'])

        except Exception as e:
            self.log_manager.error(f"Error executing actions: {e}", exc_info=True)
            return None

    async def handle_actions(self, order, holdings):
        """Process buy, sell, and trailing stop conditions based on the order action."""
        await self.open_http_session()  # Ensure session is open
        try:
            asset = order['asset']
            symbol = order['symbol']
            action_type = order.get('action')

            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, self.usd_pairs)

            price = self.shared_utils_precision.float_to_decimal(order['price'], quote_deci)
            volume = order.get('volume', 0)
            base_amount = Decimal(self.filtered_balances.get(asset, {}).get('available_to_trade_crypto', 0))
            base_amount = self.shared_utils_precision.adjust_precision(base_deci,quote_deci,base_amount,convert='base')
            quote_amount = Decimal(self.filtered_balances.get('USD', {}).get('available_to_trade_fiat', 0))
            quote_amount = self.shared_utils_precision.adjust_precision(base_deci,quote_deci,quote_amount,convert='quote')

            action_methods = {
                'buy': self.handle_buy_action,
                'sell': self.handle_sell_action,
                'hold': self.handle_trailing_stop
            }

            if action_type in action_methods:
                return await action_methods[action_type](holdings, symbol, base_amount, quote_amount,price, order)
            else:
                self.log_manager.warning(f"Unknown action type: {action_type}")
                return None

        except Exception as e:
            self.log_manager.error(f"Error handling action {order}: {e}", exc_info=True)
            return None

    async def handle_buy_action(self, holdings, symbol, base_amount, quote_amount, price, order):
        """Handles buy actions for market and limit orders."""
        try:
            usd_balance = quote_amount
            coin_balance_value = base_amount * price
            coin = symbol.split('/')[0]

            if ((usd_balance > 100 and coin_balance_value < self.min_sell_value) or
                    (usd_balance > 50 and coin in self.hodl and coin not in self.currency_pairs_ignored)):  # Accumulate BTC, ETH, etc.

                buy_order = 'market' if order.get('trigger') == 'market_buy' else 'limit'
                webhook_payload = self.build_webhook_payload(symbol, 'buy', buy_order, price, base_amount, quote_amount)

                response = await self.throttled_send(webhook_payload)
                if response and response.status in [403, 429, 500]:
                    await self.close_http_session()
                    return []

                self.log_manager.buy(f'✅ {symbol} buy signal triggered @ {buy_order} price {price}, USD balance: ${usd_balance}')
                return {'buy_action': 'open_at_limit', 'buy_pair': symbol, 'buy_limit': price, 'curr_band_ratio': order.get('band_ratio'),
                        'trigger': order.get('trigger')}

            self.log_manager.info(
                f'Insufficient funds ${usd_balance} to buy {symbol}' if usd_balance <= 100 else f'Currently holding {symbol}. Buy signal will not be processed.'
                )
            return None

        except Exception as e:
            self.log_manager.error(f'handle_buy_action: Error processing order {symbol}: {e}', exc_info=True)
            return None

    async def handle_sell_action(self, holdings, symbol, base_amount, quote_amount,price, order):
        """Handles sell actions for market, limit, and bracket orders."""
        try:
            coin = symbol.split('/')[0]
            trigger = order.get('trigger')
            sell_cond = order.get('sell_cond')

            if trigger not in ['bracket_profit', 'bracket_loss']:  # Process market & limit orders
                sell_action, sell_symbol, sell_limit, sell_order = self.trading_strategy.sell_signal_from_indicators(
                    symbol, price, trigger, holdings
                    )

                if sell_action and coin not in self.hodl:  # Skip coins marked for accumulation
                    webhook_payload = self.build_webhook_payload(symbol, 'sell', sell_order, price, base_amount, quote_amount)
                    await self.throttled_send(webhook_payload)
                    self.log_manager.sell(f'{symbol} sell signal triggered from {trigger} @ {sell_action} price {sell_limit}')
                    return {'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit, 'sell_cond': sell_cond,
                            'trigger': trigger}
            else:
                await self.execute_bracket_order(symbol, price, base_amount, quote_amount, trigger, coin)

            return None

        except Exception as e:
            self.log_manager.error(f'handle_sell_action: Error processing order for {symbol}: {e}', exc_info=True)
            return None

    async def handle_trailing_stop(self, holdings, symbol, base_amount, quote_amount,price, order):
        """Handles trailing stop sell orders when available."""
        return await self.handle_sell_action(holdings, symbol, base_amount, quote_amount,price, order)

    async def execute_bracket_order(self, symbol, price, base_amount, quote_amount, trigger, coin):
        """Executes a bracket order (take profit/loss) when applicable."""
        webhook_payload = self.build_webhook_payload(symbol, 'sell', 'bracket', price, base_amount, quote_amount)
        await self.throttled_send(webhook_payload)

        if trigger == 'profit' and coin not in self.hodl:
            self.log_manager.take_profit(f'{symbol} sell signal triggered {trigger} @ sell price {price}')
        elif trigger == 'loss' and coin not in self.hodl:
            self.log_manager.take_loss(f'{symbol} sell signal triggered {trigger} @ sell price {price}')

    def build_webhook_payload(self, symbol, side, order_type, price, base_amount=0, quote_amount=0):
        """Constructs the webhook payload for sending orders."""
        return {
            'timestamp': int(time.time() * 1000),
            'pair': symbol,
            'order_id': None,
            'action': 'close_at_limit' if side == 'sell' and order_type == 'bracket' else side.lower(),
            'order_type': order_type,
            'side': side,
            'quote_amount':quote_amount,
            'base_amount': base_amount,
            'limit_price': price,
            'stop_loss': None,
            'take_profit': None,
            'origin': "SIGHOOK",
            'verified': "valid or not valid"
        }

    # async def handle_actions(self, order, holdings):
    #     """ PART V:
    #     Handle buy conditions with limit and market orders.
    #     Handle  sell conditions with market ('sro' trigger) limit('bro' trigger) and bracket ('profit'/'loss' trigger)
    #     Handle trailing stop orders when they become available
    #
    #     """
    #     await self.open_http_session()  # Ensure session is open
    #     try:
    #         asset = order['asset']
    #         symbol = order['symbol']
    #         action_type = order.get('action')
    #
    #         # Extract price, volume, and other order details
    #         base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, self.usd_pairs)
    #
    #         price =self.shared_utils_precision.float_to_decimal(order['price'], quote_deci) # quote_deci
    #         volume = order.get('volume', 0)
    #         amount = self.filtered_balances.get(asset, {}).get('available_to_trade_crypto', Decimal('0'))
    #         result = None
    #         if action_type == 'buy':
    #             coin_balance = self.filtered_balances.get(asset, {}).get('available_to_trade_crypto', Decimal('0'))
    #             usd_balance = Decimal(self.filtered_balances.get('USD', {}).get('available_to_trade_fiat', Decimal('0')))
    #             band_ratio = order.get('band_ratio')
    #             trigger = order.get('trigger')
    #             result = await self.handle_buy_action(symbol, price, coin_balance, usd_balance, band_ratio,  trigger
    #             )
    #         elif action_type == 'sell':
    #             if amount > self.min_sell_value:
    #                 result = await self.handle_sell_action(
    #                     holdings, symbol, amount, price, order.get('trigger'), order.get('sell_cond')
    #                 )
    #         elif action_type == 'hold':
    #             result = self.handle_trailing_stop(
    #                 holdings, symbol, amount, price, order.get('trigger'), order.get('sell_cond')
    #             )
    #         else:
    #             self.log_manager.warning(f"Unknown action type: {action_type}")
    #             return result
    #
    #         return result
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error handling action {order}: {e}", exc_info=True)
    #         return None
    #
    # async def handle_buy_action(self, symbol, price, coin_balance, usd_balance, band_ratio, trigger):
    #     """PART V: Order Execution"""
    #     try:
    #         usd_balance = usd_balance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    #         coin_balance_value = Decimal(coin_balance) * Decimal(price)
    #         coin = symbol.split('/')[0]
    #
    #         if ((usd_balance > 100 and coin_balance_value < self.min_sell_value)
    #                 or (usd_balance > 50 and (coin in self.hodl)) and coin  not in self.currency_pairs_ignored):  # accumulate BTC, ETH etc
    #             # Prepare buy action data
    #             buy_action = 'open_at_limit'
    #             buy_order = 'limit'
    #             if trigger == 'market_buy':
    #                 buy_order = 'market'
    #
    #             time_millisecs = int(time.time() * 1000)
    #             webhook_payload = {
    #                 'pair': symbol.replace('/', ''),
    #                 'side': 'buy',
    #                 'timestamp': time_millisecs,
    #                 'quote_amount':0,
    #                 'base_amount': 0,
    #                 'action': buy_action,
    #                 'origin': "SIGHOOK",
    #                 'uuid':None,
    #                 'order_type': buy_order,
    #                 'verified': "valid or not valid "
    #                 #'limit_price': 0
    #                 # 'stop_loss': 0,
    #                 # 'take_profit': 0,
    #             }
    #             # Use throttled_send to limit concurrent webhook requests
    #             response = await self.throttled_send(webhook_payload)
    #
    #             if response:
    #                 if response.status in [403, 429, 500]:  #
    #                     await self.close_http_session()
    #                     return []
    #             else:
    #                 await self.close_http_session()
    #                 return []
    #             self.log_manager.buy(f'✅ {symbol} buy signal triggered @ {buy_action} price'
    #                                                 f' {price}, USD balance: ${usd_balance}')
    #             return ({'buy_action': buy_action, 'buy_pair': symbol, 'buy_limit': price, 'curr_band_ratio':
    #                     band_ratio, 'sell_action': None, 'sell_symbol': None, 'sell_limit': None, 'sell_cond': None,
    #                      'trigger': trigger})
    #         elif usd_balance <= 100:
    #             print(f'Insufficient funds ${usd_balance} to buy {symbol}')
    #             return None
    #         else:
    #             print(f'Currently holding {symbol}.Buy signal will not be processed.')
    #             return None
    #     except Exception as e:
    #         self.log_manager.error(f'handle_buy_action: Error processing order  {symbol}: {e}', exc_info=True)
    #         return None
    #
    # async def handle_sell_action(self, holdings, symbol, amount, price, trigger, sell_cond):
    #     """PART V: Order Execution"""
    #     try:
    #         # Prepare sell action data
    #         coin = symbol.split('/')[0]
    #         if trigger not in ['bracket_profit', 'bracket_loss']: # process market & limit orders
    #             sell_action, sell_symbol, sell_limit, sell_order = (self.trading_strategy.sell_signal_from_indicators(
    #                 symbol, price, trigger, holdings))
    #             if sell_action and (coin not in self.hodl):   # Hold specified (.env) coins for accumulation.
    #                 time_millisecs = int(time.time() * 1000)
    #                 webhook_payload = {
    #                     'timestamp': time_millisecs,
    #                     'pair': symbol.replace('/', ''),
    #                     'order_id': None,
    #                     'action': sell_action,
    #                     'order_type': sell_order,
    #                     'side': 'sell,
    #                     'amount': amount, # the amount allowed to be sold, the actual amount is slightly larger for fees
    #                     'limit_price': price,
    #                     'stop_loss': None,
    #                     'take_profit': None,
    #                     'origin': "SIGHOOK",
    #                     'verified': "valid or not valid "  # this will be used to verify the order
    #                 }
    #                 # Use throttled_send to limit concurrent webhook requests
    #                 await self.throttled_send(webhook_payload)
    #
    #                 self.log_manager.sell(f'{symbol} sell signal triggered from {trigger} @'
    #                                                      f' {sell_action} price' f' {sell_limit}')
    #
    #                 return ({'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
    #                         'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
    #                          'sell_cond': sell_cond, 'trigger': trigger})
    #         else:
    #             time_millisecs = int(time.time() * 1000)
    #             webhook_payload = {
    #                 'timestamp': time_millisecs,
    #                 'pair': symbol.replace('/', ''),
    #                 'order_id': None,
    #                 'action': 'close_at_limit',
    #                 'order_type': 'bracket',
    #                 'side': 'sell,
    #                 'amount': amount, # the amount allowed to be sold, the actual amount is slightly larger for fees
    #                 'limit_price': price,
    #                 'stop_loss': None, # will be computed in webhook place_bracket_order()
    #                 'take_profit': None,
    #                 'origin': "SIGHOOK",
    #                 'verified': "valid or not valid "  # this will be used to verify the order
    #             }
    #             # Use throttled_send to limit concurrent webhook requests
    #             await self.throttled_send(webhook_payload)  # await
    #
    #             if trigger == 'profit' and coin not in self.hodl:
    #                 self.log_manager.take_profit(f'{symbol} sell signal triggered  {trigger} @ sell price'
    #                                                             f' {price}')
    #             elif trigger == 'loss' and coin not in self.hodl:
    #                 self.log_manager.take_loss(f'{symbol} sell signal triggered  {trigger} @ sell price'
    #                                                           f' {price}')
    #             return None
    #     except Exception as e:
    #         self.log_manager.error(f'handle_sell_action: Error processing order for {symbol}: {e}',
    #                                               exc_info=True)
    #         return None
    #
    # async def handle_trailing_stop(self, holdings, symbol, amount, price, trigger, sell_cond):
    #     """PART V: Order Execution"""
    #     try:
    #         # Prepare sell action data
    #         coin = symbol.split('/')[0]
    #         if trigger not in ['profit', 'loss']:
    #             sell_action, sell_symbol, sell_limit, sell_order = (self.trading_strategy.sell_signal_from_indicators(
    #                 symbol, price, trigger, holdings))
    #             if sell_action and (coin not in self.hodl):   # Hold specified (.env) coins for accumulation.
    #                 time_millisecs = int(time.time() * 1000)
    #                 webhook_payload = {
    #                     'timestamp': time_millisecs,
    #                     'pair': symbol.replace('/', ''),
    #                     'order_id': None,
    #                     'action': sell_action,
    #                     'order_type': sell_order,
    #                     'side': 'sell,
    #                     'order_size': amount, # the amount allowed to be sold, the actual amount is slightly larger for fees
    #                     'limit_price': price,
    #                     'stop_loss': None,
    #                     'take_profit': None,
    #                     'origin': "SIGHOOK",
    #                     'verified': "valid or not valid "  # this will be used to verify the order
    #                 }
    #
    #                 # Use throttled_send to limit concurrent webhook requests
    #                 await self.throttled_send(webhook_payload)
    #
    #                 self.log_manager.sell(f'{symbol} sell signal triggered from {trigger} @'
    #                                                      f' {sell_action} price' f' {sell_limit}')
    #
    #                 return ({'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
    #                         'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
    #                          'sell_cond': sell_cond, 'trigger': trigger})
    #         else:
    #             time_millisecs = int(time.time() * 1000)
    #             webhook_payload = {
    #                 'timestamp': time_millisecs,
    #                 'pair': symbol.replace('/', ''),
    #                 'order_id': None,
    #                 'action': 'close_at_limit',
    #                 'order_type': 'limit',
    #                 'side': 'sell,
    #                 'amount': amount, # the amount allowed to be sold, the actual amount is slightly larger for fees
    #                 'limit_price': price,
    #                 'stop_loss': None,
    #                 'take_profit': None,
    #                 'origin': "SIGHOOK",
    #                 'verified': "valid or not valid "  # this will be used to verify the order
    #             }
    #             # Use throttled_send to limit concurrent webhook requests
    #             await self.throttled_send(webhook_payload)
    #
    #             if trigger == 'profit' and coin not in self.hodl:
    #                 self.log_manager.take_profit(f'{symbol} sell signal triggered  {trigger} @ sell price'
    #                                                             f' {price}')
    #             elif trigger == 'loss' and coin not in self.hodl:
    #                 self.log_manager.take_loss(f'{symbol} sell signal triggered  {trigger} @ sell price'
    #                                                           f' {price}')
    #             return None
    #     except Exception as e:
    #         self.log_manager.error(f'handle_sell_action: Error processing order for {symbol}: {e}',
    #                                               exc_info=True)
    #         return None
