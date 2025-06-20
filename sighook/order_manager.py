
import asyncio
import time
import uuid
from decimal import Decimal

import aiohttp
import pandas as pd

from Config.config_manager import CentralConfig


class OrderManager:
    _instance = None

    @classmethod
    def get_instance(cls, trading_strategy, ticker_manager, exchange, webhook, alerts, logger_manager, coinbase_api, ccxt_api,
                     shared_utils_precision, shared_data_manager, web_url, max_concurrent_tasks=10):
        if cls._instance is None:
            cls._instance = cls(trading_strategy, ticker_manager, exchange, webhook, alerts,
                                logger_manager, coinbase_api, ccxt_api, shared_utils_precision,
                                shared_data_manager, web_url, max_concurrent_tasks)
        return cls._instance

    def __init__(self, trading_strategy, ticker_manager, exchange, webhook, alerts, logger_manager, coinbase_api, ccxt_api,
                 shared_utils_precision, shared_data_manager, web_url, max_concurrent_tasks=10):
        self.config = CentralConfig()
        self.shared_data_manager = shared_data_manager
        self.trading_strategy = trading_strategy
        self.exchange = exchange
        self.webhook = webhook
        self.ticker_manager = ticker_manager
        self.shared_utils_precision = shared_utils_precision
        self.alerts = alerts
        self.logger = logger_manager  # 🙂
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self._version = self.config.program_version
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self._order_size_fiat = Decimal(self.config.order_size_fiat)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)  # Default trailing stop at 0.5%
        self._hodl = self.config.hodl
        self._take_profit = Decimal(self.config.take_profit)
        self._cxl_buy = self.config.cxl_buy
        self._cxl_sell = self.config.cxl_sell
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self._assets_ignored = self.config.assets_ignored
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.http_session, self.start_time, self.web_url  = None, None, None
        self.web_url = web_url

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def filtered_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})

    @property
    def min_volume(self):
        return Decimal(self.shared_data_manager.market_data['avg_quote_volume'])


    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def order_size_fiat(self):
        return self._order_size_fiat

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

    @property
    def assets_ignored(self):
        return self._assets_ignored

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
                self.logger.error(f"❌ Error in throttled_send: {e}", exc_info=True)
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
            self.logger.error(f'❌ get_open_orders: {gooe}', exc_info=True)
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
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)



            merged_orders['price'] = merged_orders['price'].apply(Decimal)
            merged_orders['ask'] = merged_orders['ask'].apply(Decimal)
            merged_orders['bid'] = merged_orders['bid'].apply(Decimal)
            merged_orders['price'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'price'], 'quote')
            merged_orders['ask'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'ask'], 'base')
            merged_orders['bid'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, merged_orders[
                'bid'], 'base')

            if merged_orders['active (mins)'].dtype == 'object':
                merged_orders['active (mins)'] = merged_orders['active (mins)'].str.replace(' minutes', '')
            merged_orders['active (mins)'] = pd.to_numeric(merged_orders['active (mins)'],
                                                                   errors='coerce').fillna(0).astype(int)
            merged_orders['active > 5 mins '] = merged_orders['active (mins)'] > 5

            merged_orders['is_stale'] = (
                    ((merged_orders['side'].str.upper() == 'BUY') &
                     (merged_orders['price'] < merged_orders['ask'] * (1 - Decimal(self.cxl_buy))) &
                     (merged_orders['active > 5 mins '])) |
                    ((merged_orders['side'].str.upper() == 'SELL') & (merged_orders['type'].str.upper() == 'LIMIT') &
                     (merged_orders['price'] > merged_orders['ask'] * (1 + Decimal(self.cxl_sell))) &
                     (merged_orders['active > 5 mins ']))
            )

            stale_orders = merged_orders[merged_orders['is_stale']]
            cancel_tasks = [self.cancel_order(order_id, product_id) for order_id, product_id in
                            zip(stale_orders['order_id'], stale_orders['product_id'])]

            await asyncio.gather(*cancel_tasks)
            non_stale_orders = merged_orders[~merged_orders['is_stale']].drop(columns=['is_stale', 'symbol', 'ask', 'bid'])
            return non_stale_orders

        except Exception as e:
            self.logger.error(f'❌ Error cancelling stale orders: {e}', exc_info=True)
            return None

    async def cancel_order(self, order_id, product_id):
        """PART III: Trading Strategies """
        try:
            if order_id is not None:
                print(f'Cancelling order {product_id}:{order_id}')
                response = await self.coinbase_api.cancel_order([order_id])
                if response:
                    print(f"  🟪🟨  open order canceled  🟨🟪  ")  # debug
                    return
                else:
                    print(f'‼️ Order {product_id}:{order_id}  was not cancelled')
                    return
        except Exception as e:
            self.logger.error(f'❌Error cancelling order {product_id}:{order_id}: {e}', exc_info=True)

    async def adjust_merged_orders_prices(self, merged_orders):
        """
        Adjust the price of each order in the merged_orders DataFrame to align with the precision defined for the product_id.
        """
        try:
            for index, row in merged_orders.iterrows():
                # Fetch the precision for the symbol (product_id)
                product_id = row['product_id']
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(product_id)



                # Adjust the price using the quote precision
                adjusted_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, row['price'], 'quote')

                # Update the price in the DataFrame
                merged_orders.at[index, 'price'] = float(adjusted_price)

            return merged_orders

        except Exception as e:
            self.logger.error(f"❌Error adjusting prices in merged_orders: {e}", exc_info=True)
            return merged_orders

    async def format_open_orders_from_dict(self, open_orders_dict: dict) -> pd.DataFrame:
        """
        Format the open orders data stored in a dictionary structure.

        Args:
            open_orders_dict (dict): Dictionary of open orders keyed by order ID.

        Returns:
            pd.DataFrame: A DataFrame containing formatted open order data.
        """

        def parse_float_safe(value):
            try:
                return float(value) if value not in ("", None) else None
            except ValueError:
                return None

        try:
            data_to_load = []

            for order_id, order in open_orders_dict.items():
                order_type = order.get('type', '').upper()
                # if order_type =='TAKE_PROFIT_STOP_LOSS':

                info = order.get('info', {})
                common_fields = {
                    'order_id': order_id,
                    'parent_id':None if info.get("side", "").lower() == "buy" else info.get("originating_order_id"),
                    'product_id': info.get('product_id',order.get('symbol')),
                    'side': info.get('side',order.get('side')),
                    'filled': order.get('filled'),
                    'remaining': order.get('remaining'),
                    'type': order_type,
                    'time active': info.get('created_time')
                }

                if order_type == 'LIMIT':
                    common_fields.update(
                        {
                            'size': order.get('amount'),
                            'price': round(order.get('price', 0), 8) if order.get('price') is not None else None,
                            'trigger_price': order.get('triggerPrice'),
                            'stop_price': order.get('stopPrice')
                        }
                    )

                elif order_type == 'TAKE_PROFIT_STOP_LOSS':
                    trigger_config = info.get('order_configuration', {}).get('trigger_bracket_gtc', {})
                    common_fields.update(
                        {
                            'size': parse_float_safe(trigger_config.get('base_size')),
                            'price': parse_float_safe(trigger_config.get('limit_price')),
                            'trigger_price': parse_float_safe(trigger_config.get('stop_trigger_price')),
                            'stop_price': parse_float_safe(trigger_config.get('stop_loss_price')),
                        }
                    )

                else:
                    self.logger.warning(f"⚠️ Skipping unsupported order type: {order_type}")
                    continue

                data_to_load.append(common_fields)

            df = pd.DataFrame(data_to_load)

            if not df.empty:
                df['time active'] = pd.to_datetime(df['time active'], errors='coerce')
                current_time = pd.Timestamp.utcnow()
                df['active (mins)'] = df['time active'].apply(
                    lambda x: (current_time - x).total_seconds() / 60 if pd.notnull(x) else None
                )
                df['time_temp'] = pd.to_numeric(df['active (mins)'], errors='coerce')
                df['active > 5 mins '] = df['time_temp'] > 5
                df.drop(columns=['time_temp'], inplace=True)

            return df

        except Exception as e:
            self.logger.error(f"❌ Error in format_open_orders_from_dict: {e}", exc_info=True)
            return pd.DataFrame()

    async def execute_actions(self, strategy_orders, holdings):
        """
        Executes strategy and profit-driven buy/sell actions.
        """
        try:
            execution_tasks = []

            for order in strategy_orders:
                if order.get('action') not in ['buy', 'sell']:
                    continue

                execution_tasks.append(self.handle_actions(order, holdings))

            execution_results = await asyncio.gather(*execution_tasks, return_exceptions=True)

            processed_orders = [
                {
                    'symbol': order.get('symbol'),
                    'type': order.get('type'),
                    'action': order.get('action'),
                    'trigger': order.get('trigger'),
                    'score': order.get('score')
                }
                for order in execution_results if isinstance(order, dict)
            ]

            return pd.DataFrame(processed_orders, columns=['symbol', 'action', 'trigger', 'score'])

        except Exception as e:
            self.logger.error(f"❌ Error executing actions: {e}", exc_info=True)
            return None

    async def handle_actions(self, order, holdings):
        """Process buy, sell, and trailing stop conditions based on the order action."""
        await self.open_http_session()  # Ensure session is open
        try:
            asset = order['asset']
            symbol = order['symbol']
            action_type = order.get('action')

            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)



            price = self.shared_utils_precision.float_to_decimal(order['price'], quote_deci)
            base_avail_to_trade = Decimal(self.filtered_balances.get(asset, {})['available_to_trade_crypto'])
            base_avail_to_trade = self.shared_utils_precision.adjust_precision(base_deci,quote_deci,base_avail_to_trade,convert='base')
            quote_avail_balance = Decimal(self.filtered_balances.get('USD', {})['available_to_trade_fiat'])
            quote_avail_balance = self.shared_utils_precision.adjust_precision(base_deci,quote_deci,quote_avail_balance,convert='quote')

            action_methods = {
                'buy': self.handle_buy_action,
                'sell': self.handle_sell_action,
                # 'hold': self.handle_trailing_stop
            }

            if action_type in action_methods:
                return await action_methods[action_type](holdings, symbol, base_avail_to_trade, quote_avail_balance, price, order)
            else:
                self.logger.warning(f"Unknown action type: {action_type}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Error handling action {order}: {e}", exc_info=True)
            return None

    async def handle_buy_action(self, holdings, symbol, base_avail_to_trade, quote_avail_balance, price, order):
        """Handles buy actions for market and limit orders."""
        try:
            usd_balance = quote_avail_balance
            coin_balance_value = base_avail_to_trade * price
            coin = symbol.split('/')[0]
            trigger = order.get('trigger')
            score = order.get('score')

            if ((usd_balance > 100 and coin_balance_value < self.min_sell_value) or
                    (usd_balance > 50 and coin not in self.hodl and coin not in [self.currency_pairs_ignored, self.assets_ignored])):
                buy_order = 'market' if trigger == 'market_buy' else 'limit'
                webhook_payload = self.build_webhook_payload(symbol, 'buy', buy_order, price, trigger, score,
                                                             base_avail_to_trade, quote_avail_balance)

                if webhook_payload.get('verified') == 'valid':
                    response = await self.throttled_send(webhook_payload)
                    if response and response.status in [403, 429, 500]:
                        await self.close_http_session()
                        return []

                    self.logger.buy(f'✅ {symbol} buy signal triggered @ {buy_order} price {price}, USD balance: ${usd_balance}')
                    return {'buy_action': 'open_at_limit', 'buy_pair': symbol, 'buy_limit': price, 'curr_band_ratio': order.get('band_ratio'),
                            'trigger': order.get('trigger')}
                return[]

            self.logger.info(
                f'Insufficient funds ${usd_balance} to buy {symbol}' if usd_balance <= 100 else
                f'Currently holding {symbol}. Buy signal will not be processed.'
                )
            return None

        except Exception as e:
            self.logger.error(f'❌ handle_buy_action: Error processing order {symbol}: {e}', exc_info=True)
            return None

    async def handle_sell_action(self, holdings, symbol, base_avail_to_trade, quote_amount,price, order):
        """Handles sell actions for market, limit, and bracket orders."""
        try:
            coin = symbol.split('/')[0]
            type = order.get('type')
            trigger = order.get('trigger')
            sell_cond = order.get('sell_cond')
            score = order.get('score')
            if type not in ['tp_sl']:  # Process market & limit orders
                sell_action, sell_symbol, sell_limit, sell_order = self.trading_strategy.sell_signal_from_indicators(
                    symbol, price, trigger, type, holdings
                    )

                if sell_action and coin not in self.hodl:  # Skip coins marked for accumulation
                    webhook_payload = self.build_webhook_payload(symbol, 'sell', sell_order, price, trigger,
                                                                 score, base_avail_to_trade, quote_amount)
                    if webhook_payload.get('verified') == 'valid':
                        await self.throttled_send(webhook_payload)
                        self.logger.sell(f'{symbol} sell signal triggered from {trigger} @ {sell_action} price {sell_limit}')
                        return {'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit, 'sell_cond': sell_cond,
                                'trigger': trigger}
                    return[]
            else:
                await self.execute_tp_sl_order(symbol, price, base_avail_to_trade, quote_amount,
                                               trigger, score, coin)  # this may not be correct in how variables are being passed.
            return None

        except Exception as e:
            self.logger.error(f'❌ handle_sell_action: Error processing order for {symbol}: {e}', exc_info=True)
            return None

    # async def handle_trailing_stop(self, holdings, symbol, base_avail_to_trade, quote_avail_balance, price, order):
    #     """Handles trailing stop sell orders when available."""
    #     return await self.handle_sell_action(holdings, symbol, base_avail_to_trade, quote_avail_balance,
    #                                          price, order)

    async def execute_tp_sl_order(self, symbol, price, base_avail_to_trade, quote_avail_balance, trigger, score, coin):
        """Submits a tp_sl order (take profit/loss) when applicable."""
        webhook_payload = self.build_webhook_payload(symbol, 'sell', 'tp_sl', price, trigger, score,
                                                     base_avail_to_trade, quote_avail_balance)
        if webhook_payload.get('verified') == 'valid':
            await self.throttled_send(webhook_payload)
            if trigger == 'profit' and coin not in self.hodl:
                self.logger.take_profit(f'{symbol} sell signal triggered {trigger} @ sell price {price}')
            elif trigger == 'loss' and coin not in self.hodl:
                self.logger.take_loss(f'{symbol} sell signal triggered {trigger} @ sell price {price}')

    def build_webhook_payload(self, symbol, side, order_type, price, trigger, score, base_avail_to_trade=0, quote_avail_balance=0):
        """Constructs the webhook payload for sending orders."""

        # Convert Decimal values to float
        price = float(price) if isinstance(price, Decimal) else price
        base_avail_to_trade = float(base_avail_to_trade) if isinstance(base_avail_to_trade, Decimal) else float(base_avail_to_trade)
        quote_avail_balance = float(quote_avail_balance) if isinstance(quote_avail_balance, Decimal) else float(quote_avail_balance)
        valid_order = 'valid'
        if base_avail_to_trade * price < self.min_sell_value and side == 'sell':
            valid_order = 'invalid'
        elif quote_avail_balance < self._order_size_fiat and side == 'buy':
            valid_order = 'invalid'
        else:
            if quote_avail_balance < self._order_size_fiat and side == 'buy':
                valid_order = 'invalid'

        # Construct payload
        payload = {
            'timestamp': int(time.time() * 1000),
            'pair': symbol,
            'order_id': str(uuid.uuid4()),  # Generate unique order ID
            'action': 'close_at_limit' if side == 'sell' and order_type == 'bracket' else side.lower(),
            'order_type': order_type,
            'order_amount_fiat': float(self.order_size_fiat) if side == 'buy' else base_avail_to_trade,
            'side': side.lower(),
            'quote_avail_balance': quote_avail_balance,
            'base_avail_to_trade': base_avail_to_trade,
            'limit_price': price,
            'stop_loss': None,
            'take_profit': None,
            'origin': "SIGHOOK",
            'trigger': trigger,
            'score': score,
            'verified': valid_order
        }

        # Remove `None` values
        return {k: v for k, v in payload.items() if v is not None}
