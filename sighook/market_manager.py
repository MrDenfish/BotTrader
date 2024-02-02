
import pandas as pd

import asyncio


class MarketManager:
    def __init__(self, api_wrapper, trading_strategy, logmanager, ticker_manager, utility):
        assert api_wrapper is not None, "api_wrapper must not be None"
        self.open_orders = []
        self.api_wrapper = api_wrapper
        self.exchange = api_wrapper.exchange
        self.trading_strategy = trading_strategy
        self.ticker_manager = ticker_manager
        self.utility = utility
        self.log_manager = logmanager
        self.results = pd.DataFrame(columns=['symbol', 'action', 'price', 'band_ratio'])
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache,  hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.current_holdings = hist_holdings

    async def new_fetch_ohlcv(self, old_portfolio, usd_pairs, avg_dollar_vol_total, buy_sell_matrix):
        try:
            if avg_dollar_vol_total is None:
                self.log_manager.sighook_logger.info('average volume missing')
                return None, None, self.results

            self.open_orders = await self.api_wrapper.get_open_orders(old_portfolio, usd_pairs)
            counter = {'processed': 0}
            # Prepare asynchronous tasks for processing rows
            tasks = [self.trading_strategy.process_row_async(row, old_portfolio, buy_sell_matrix, counter)
                     for _, row in self.ticker_manager.ticker_cache.iterrows()]

            results = await asyncio.gather(*tasks)
            print(f" {counter['processed']} coins have been evaluated for buy sell conditions")
            # Process the results
            all_bollinger_dfs = []  # List of all bollinger_df DataFrames
            all_updates = []  # List to collect all updates from threads
            new_entries = []  # List to collect all new entries from threads
            signals_generated = []  # List to collect all signals generated from threads

            for result in results:
                if result.get('symbol'):
                    signals_generated.append(result['symbol'])
                    new_entries.append(result['symbol'])
                if result.get('action'):
                    signals_generated.append(result['action'])
                    new_entries.append(result['action'])
                if result.get('band_ratio'):
                    new_entries.append(result['band_ratio'])
                if result.get('price'):
                    signals_generated.append(result['price'])
                if result.get('action_data'):
                    new_entries.append(result['action_data'])
                if 'bollinger_df' in result and result['bollinger_df'] is not None and not result['bollinger_df'].empty:
                    all_bollinger_dfs.append(result['bollinger_df'])
                if 'roc' in result and result['roc'] is not None and not result['roc'].empty:
                    new_entries.append(result['roc'])
                if 'rsi' in result and result['rsi'] is not None and not result['rsi'].empty:
                    new_entries.append(result['rsi'])
                if result.get('updates'):
                    all_updates.append(result['updates'])

            for update in all_updates:
                if update is not None:  # Check if update is not None
                    for coin, values in update.items():
                        for col, value in values.items():
                            if col in buy_sell_matrix.columns:
                                buy_sell_matrix.loc[buy_sell_matrix['coin'] == coin, col] = value

            # Combine all bollinger_df DataFrames into one
            combined_bollinger_df = pd.concat(all_bollinger_dfs, ignore_index=True)
            self.utility.print_elapsed_time(self.start_time, 'fetch_ohlcv')
            return self.open_orders, combined_bollinger_df
        except ValueError as ve:
            self.log_manager.sighook_logger.error(f"ValueError: {ve}")
        except RuntimeError as re:
            self.log_manager.sighook_logger.error(f"RuntimeError: {re}")
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error occurred: {e}")
            raise
        finally:
            # Close the exchange connection here, ensuring it's always executed
            await self.exchange.close()



