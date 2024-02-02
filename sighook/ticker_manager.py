
import asyncio
import datetime
from decimal import Decimal

import pandas as pd


class TickerManager:
    def __init__(self, utility, logmanager, exchange, ccxt_api):
        self.exchange = exchange
        self.ticker_cache = None
        self.market_cache = None
        self.last_ticker_update = None
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.start_time = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.current_holdings = hist_holdings

    async def update_ticker_cache(self, start_time=None):
        market_data = None
        try:
            now = datetime.datetime.utcnow()
            if self.start_time is None:
                market_data = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
                if market_data is None:
                    return None, None
            else:
                start_time_datetime = datetime.datetime.utcfromtimestamp(self.start_time)
                temp_time = now - start_time_datetime
                if temp_time.seconds >= 1440:
                    market_data = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
                    if market_data is None:
                        return None, None
                else:

                    return self.ticker_cache, None  # Exit if the ticker cache is already up to date

            # Filter for symbols that end with '/USD'
            tickers = [market for market in market_data if market['symbol'].endswith('/USD')]
            if not tickers:
                return None, None  # Exit if no USD tickers are found

            tickers_dict = {market['symbol']: market for market in tickers}
            balance = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance)
            if balance is None:
                return None, None

            avail_qty = balance.get('free', {})

            df = pd.DataFrame.from_dict(tickers_dict, orient='index')
            df['base_currency'] = df['symbol'].str.split('/').str[0]
            df['free'] = df['base_currency'].map(avail_qty).fillna(0)
            df['volume_24h'] = df['info'].apply(lambda x: x.get('volume_24h', 0))
            self.utility.print_elapsed_time(start_time, 'update_ticker_cache')  # debug statement
            await self.parallel_fetch_and_update(df)
            self.utility.print_elapsed_time(start_time, 'update_ticker_cache')  # debug statement
            return df, market_data

        except Exception as e:
            self.ccxt_exceptions.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {e}')

    async def parallel_fetch_and_update(self, df):
        #  removed ThreadPoolExecutor
        try:
            symbols = df['symbol'].tolist()
            tasks = [self.fetch_ticker_data(symbol) for symbol in symbols]
            results = await asyncio.gather(*tasks)
            for result in results:
                if not result or len(result) < 3:
                    self.log_manager.sighook_logger.info(f"Invalid result: {result}")
                    continue
                symbol, bid, ask = result
                if bid is None or ask is None:
                    self.log_manager.sighook_logger.debug(f"Missing data for symbol {symbol}, skipping")
                    continue
                if symbol in df.index:
                    df.loc[symbol, ['bid', 'ask']] = bid, ask
                else:
                    self.log_manager.sighook_logger.info(f"Symbol not found in DataFrame: {symbol}")
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in parallel_fetch_and_update: {e}')

    async def fetch_ticker_data(self, symbol):
        try:
            individual_ticker = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, symbol)
            if individual_ticker:
                bid = individual_ticker.get('bid')
                ask = individual_ticker.get('ask')
                return symbol, bid, ask
            else:
                self.log_manager.sighook_logger.debug(f'No data available for ticker {symbol}')
                return symbol, None, None
        except IndexError:
            self.log_manager.sighook_logger.error(
                f'Index error for symbol {symbol}: Data format may be incorrect or incomplete.')
            return symbol, None, None
        except Exception as e:
            if "object has no attribute 'get'" in str(e):
                self.log_manager.sighook_logger.info(f'TickerManager:fetch_ticker_data:{symbol} Trading Market Disabled')
                return symbol, None, None
            self.log_manager.sighook_logger.error(
                f'TickerManager:fetch_ticker_data:Error while fetching ticker data for {symbol}: {e}')
            return symbol, None, None

    async def get_ticker_balance(self, coin):
        """Get the balance of a coin in the exchange account."""
        try:
            balance = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_balance())

            coin_balance = Decimal(balance[coin]['total']) if coin in balance else Decimal('0.0')
            usd_balance = Decimal(balance['USD']['total']) if 'USD' in balance else Decimal('0.0')

        except Exception as e:
            self.log_manager.sighook_logger.error(f'SenderUtils get_balance: Exception occurred during  {e}')
            coin_balance = Decimal('0.0')
            usd_balance = Decimal('0.0')

        return coin_balance, usd_balance
