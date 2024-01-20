
from queue import Queue

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd


class MarketManager:
    def __init__(self, api_wrapper, trading_strategy, logmanager, ticker_manager):
        assert api_wrapper is not None, "api_wrapper must not be None"
        self.open_orders = []
        self.api_wrapper = api_wrapper
        self.exchange = api_wrapper.exchange
        self.trading_strategy = trading_strategy
        self.ticker_manager = ticker_manager
        self.log_manager = logmanager
        self.results = pd.DataFrame(columns=['symbol', 'action', 'price', 'band_ratio'])
        self.ticker_cache = None
        self.start_time = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.current_holdings = hist_holdings

    def fetch_ohlcv(self,old_portfolio, usd_pairs, avg_dollar_vol_total, high_total_vol):
        """ Fetch OHLCV data using "process_row" then determine if bollinger band ratio qualifies buy/sell order
        this is done for each coin in the ticker_cache DataFrame """
        try:
            # dollar volume will be the amount to compare. Intersting if greater than , not interesting if less than
            if avg_dollar_vol_total is None:
                self.log_manager.sighook_logger.info('average volume missing')
                return None, None, self.results

            coin_bal, self.open_orders = self.api_wrapper.get_open_orders(old_portfolio, usd_pairs)
            """ load open orders to open_orders using multithreading and process each row of the DataFrame ticker_cache by
             calling the self.process_row method. The processing is done concurrently using a maximum of 8 coinbasepro
             limit is 10 threads, and a dictionary of futures is created to keep track of the computations.
             Use a list to collect results from threads """
            # thread_results = []
            signal_queue = Queue()  # Thread-safe queue for collecting results
            # Process each row and update high_total_vol

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(self.trading_strategy.process_row, row, self.exchange, old_portfolio,
                                    high_total_vol): row for _, row in self.ticker_manager.ticker_cache.iterrows()}

                # Process futures as they complete
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            signal_queue.put(result)  # Store result in the queue
                    except Exception as ex:
                        self.log_manager.sighook_logger.error(f"Error occurred: {ex}")
                        # Initialize a list to hold all bollinger_df DataFrames
            all_bollinger_dfs = []
            all_updates = []  # List to collect all updates from threads
            new_entries = []  # List to collect all new entries from threads
            signals_generated = []  # List to collect all signals generated from threads
            # Dequeue and process each item
            while not signal_queue.empty():
                result = signal_queue.get()
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
                if 'bollinger_df' in result and not result['bollinger_df'].empty:
                    all_bollinger_dfs.append(result['bollinger_df'])

                # if result.get('bollinger_df'):
                #     all_bollinger_dfs.append(result['bollinger_df'])
                if result.get('updates'):
                    all_updates.append(result['updates'])
                # Bulk update the 'high_total_vol' DataFrame

                for update in all_updates:
                    if update is not None:  # Check if update is not None
                        for coin, values in update.items():
                            for col, value in values.items():
                                high_total_vol.loc[high_total_vol['coin'] == coin, col] = value

                # Update the 'ob' DataFrame
                print(f'New Entries: {new_entries}')
                # for new_entry in new_entries:
                #     ob = pd.concat([ob, pd.DataFrame([new_entry])], ignore_index=True)
                # ob = self.api_wrapper.update_order_book(old_portfolio,usd_pairs)

                # Combine all bollinger_df DataFrames into one
                combined_bollinger_df = pd.concat(all_bollinger_dfs, ignore_index=True)
                return self.open_orders, self.results, combined_bollinger_df
        except ValueError as ve:
            self.log_manager.sighook_logger.error(f"ValueError: {ve}")
        except RuntimeError as re:
            self.log_manager.sighook_logger.error(f"RuntimeError: {re}")
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error occurred: {e}")
            # Handle the exception or re-raise it
            raise


