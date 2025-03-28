import asyncio
from decimal import Decimal

import pandas as pd
from ccxt.base.errors import BadSymbol


class PortfolioPosition:
    def __init__(self, asset, available_to_trade, total_balance):
        self.asset = asset
        self.available_to_trade = Decimal(available_to_trade)
        self.total_balance = Decimal(total_balance)

    def __repr__(self):
        return f"PortfolioPosition(asset={self.asset}, available_to_trade={self.available_to_trade}, total_balance={self.total_balance})"

class TickerManager:
    _instance = None
    _lock = asyncio.Lock()  # Ensures thread-safety in an async environment

    @classmethod
    async def get_instance(cls, shared_utils_debugger, shared_utils_print, log_manager, rest_client, portfolio_uuid, exchange, ccxt_api):
        """Ensures only one instance of TickerManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:  # Double-check after acquiring the lock
                    cls._instance = cls(shared_utils_debugger, shared_utils_print, log_manager, rest_client, portfolio_uuid, exchange, ccxt_api)
        return cls._instance

    def __init__(self, shared_utils_debugger, shared_utils_print, log_manager, rest_client, portfolio_uuid, exchange, ccxt_api):
        if TickerManager._instance is not None:
            raise Exception("TickerManager is a singleton and has already been initialized!")

        self.exchange = exchange
        self.rest_client = rest_client
        self.portfolio_uuid = portfolio_uuid
        self.ticker_cache = None
        self.market_cache = None
        self.min_volume = None
        self.last_ticker_update = None
        self.log_manager = log_manager
        self.ccxt_api = ccxt_api
        self.shared_utils_print = shared_utils_print
        self.shared_utils_debugger = shared_utils_debugger
        self.shared_utils_precision = None
        self.start_time = None

    async def update_ticker_cache(self, open_orders=None, start_time=None) -> tuple:
        """
        Update market_data dictionary with market and portfolio data.

        Args:
            open_orders (list): List of open orders (optional).
            start_time (float): Start time for market updates (optional).

        Returns:
            tuple: Updated ticker cache and additional metadata.
        """
        try:
            # Fetch market data and portfolio balances concurrently
            market_data_task = self.ccxt_api.ccxt_api_call(
                self.exchange.fetch_markets,
                'public',
                params={'paginate': True, 'limit': 1000}
            )
            balances_task = self.fetch_and_filter_balances(self.portfolio_uuid)
            market_data, non_zero_balances = await asyncio.gather(market_data_task, balances_task)
            if not market_data:
                self.log_manager.error("Market data retrieval failed, returning empty dataset.")
                return {}, {}

            if not non_zero_balances:
                self.log_manager.warning("No non-zero balances found, returning empty dataset.")

            # Process market data
            filtered_vol_data, usd_pairs, avg_volume = self.filter_volume_for_market_data(market_data)
            supported_vol_markets, supported_usd_markets = await self.filter_markets_by_criteria(
                filtered_vol_data, usd_pairs
            )

            # Prepare DataFrame caches
            tickers_cache = self.prepare_dataframe(supported_vol_markets, non_zero_balances)
            usd_pairs_cache = self.prepare_dataframe(supported_usd_markets, non_zero_balances)

            if tickers_cache.empty:
                self.log_manager.error("Ticker cache is empty.")
                return {}, {}

            # Fetch current prices and update ticker cache
            tickers_cache, current_prices = await self.parallel_fetch_and_update(usd_pairs_cache, tickers_cache)

            # Process spot positions and include precision data
            spot_positions = self.process_spot_positions(non_zero_balances, tickers_cache)

            return {
                "ticker_cache": tickers_cache,
                "filtered_vol": supported_vol_markets,
                "usd_pairs_cache": usd_pairs_cache,
                "current_prices": current_prices,
                "avg_quote_volume": Decimal(avg_volume).quantize(Decimal('0')),
                "spot_positions": spot_positions
            }, {"non_zero_balances": non_zero_balances, 'order_tracker': {}}

        except Exception as e:
            self.log_manager.error(f"Error in update_ticker_cache: {e}", exc_info=True)
            return {}, {}


    async def fetch_and_filter_balances(self, portfolio_uuid: str) -> dict:
        """
        Fetch portfolio breakdown and filter non-zero balances.

        Args:
            portfolio_uuid (str): The UUID of the portfolio.

        Returns:
            dict: Non-zero balances filtered by wallet account type.
        """
        try:
            portfolio_data = await self.get_portfolio_breakdown(portfolio_uuid)
            if not portfolio_data:
                raise ValueError("Portfolio breakdown data is empty or invalid.")

            # Extract non-zero balances
            spot_positions = portfolio_data.breakdown.spot_positions
            non_zero_balances = {
                pos["asset"]: pos
                for pos in spot_positions
                if Decimal(pos["total_balance_crypto"]) > 0
            }
            return non_zero_balances
        except Exception as e:
            self.log_manager.error(f"Error in fetch_and_filter_balances: {e}", exc_info=True)
            return {}

    def process_spot_positions(self, non_zero_balances: dict, tickers_cache: pd.DataFrame) -> dict:
        """
        Process spot positions, round numeric values, and merge precision data for custom objects.

        Args:
            non_zero_balances (dict): Dictionary of custom objects for non-zero balances.
            tickers_cache (pd.DataFrame): DataFrame containing ticker information.

        Returns:
            dict: Processed spot positions with rounded values and added precision.
        """
        try:
            # Convert tickers_cache to a dictionary for quick lookup
            ticker_precision_map = tickers_cache.set_index('asset')['precision'].to_dict()

            # Define precision for rounding
            rounding_precision = Decimal('0.00000001')

            processed_positions = {}

            for asset, data in non_zero_balances.items():
                # Retrieve precision from tickers_cache
                precision = ticker_precision_map.get(asset, {'amount': None, 'price': None})

                # Use vars() to get attributes of the custom object as a dictionary
                data_dict = vars(data) if hasattr(data, '__dict__') else data

                # Initialize the processed position with precision values
                processed_position = {"precision": precision}

                for key, value in data_dict.items():
                    if isinstance(value, (float, int, Decimal)):  # Process numeric values
                        processed_position[key] = Decimal(value).quantize(rounding_precision)
                    else:
                        # Retain non-numeric values as-is
                        processed_position[key] = value

                # Add the processed position to the final dictionary
                processed_positions[asset] = processed_position

            return processed_positions

        except Exception as e:
            self.log_manager.error(f"Error in process_spot_positions: {e}", exc_info=True)
            return {}


    def filter_volume_for_market_data(self, market_data):
        """
        PART I: Data Gathering and Database Loading.
        Purpose: Filters market data based on volume criteria.

        """
        try:
            filtered_data = []
            usd_pairs = []  # List without the volume filter
            total_volume = 0
            valid_volumes = []

            # First pass: Calculate total quote volume for averaging
            for item in market_data:
                try:
                    info = item.get('info', {})
                    approximate_quote_volume = info.get('approximate_quote_24h_volume', "0")
                    is_new = info.get('new', False)
                    if is_new and info.get('quote') == 'USD':
                        print(f'')
                        print(f"< -------- --------  New market found: {item['symbol']} -------- -------- >")
                        print(f'')
                        pass
                        # save to the database table 'new_markets
                    #elif item['symbol'] is in the new_markets table:
                        #pass
                        # remove from the table, no longer new

                    # Check if `approximate_quote_24h_volume` is numeric
                    if not approximate_quote_volume.replace('.', '', 1).isdigit():
                        continue

                    quote_volume = float(approximate_quote_volume)
                    valid_volumes.append(quote_volume)

                except (KeyError, ValueError):
                    continue

            # Calculate average quote volume
            if valid_volumes:
                average_quote_volume = sum(valid_volumes) / len(valid_volumes)
            else:
                self.log_manager.debug("No valid quote volumes found for averaging.")
                return filtered_data, usd_pairs , None # Return empty lists if no valid volumes

            # Second pass: Filter based on average quote volume
            for item in market_data:
                try:
                    # Ensure required keys exist
                    info = item.get('info', {})
                    id = item.get('id')
                    product_id = info.get('product_id')
                    price = float(info.get('price', 0))
                    volume_24h = float(info.get('volume_24h', 0))
                    price_change = float(info.get('price_percentage_change_24h', 0))
                    approximate_quote_volume = info.get('approximate_quote_24h_volume', "0")

                    # Check if `approximate_quote_24h_volume` is numeric
                    if not approximate_quote_volume.replace('.', '', 1).isdigit():
                        continue

                    quote_volume = float(approximate_quote_volume)
                    total_volume += quote_volume

                    # Add to the list without volume filtering
                    if item.get('quote') == 'USD' and item.get('active', False) and item.get('type') == 'spot':
                        usd_pairs.append({
                            'asset': item.get('base'),
                            'quote': item.get('quote'),
                            'symbol': item.get('symbol'),
                            'precision': item.get('precision'),
                            'info': {
                                'product_id': product_id,
                                'type': item.get('type'),
                                'price': price,
                                'volume_24h': volume_24h,
                                '24h_quote_volume': approximate_quote_volume,
                                'price_percentage_change_24h': price_change
                            }
                        })

                    # Add to the list with volume filtering
                    if (
                            item.get('quote') == 'USD' and  # Filter for USD quote
                            item.get('active', False) and  # Ensure market is active
                            item.get('type') == 'spot' and  # Filter for spot markets
                            quote_volume >= average_quote_volume  # Compare to average volume
                    ):
                        filtered_data.append({
                            'asset': item.get('base'),
                            'quote': item.get('quote'),
                            'symbol': item.get('symbol'),
                            'precision': item.get('precision'),
                            'info': {
                                'product_id': product_id,
                                'type': item.get('type'),
                                'price': price,
                                'volume_24h': volume_24h,
                                '24h_quote_volume': approximate_quote_volume,
                                'price_percentage_change_24h': price_change
                            }
                        })
                except (KeyError, ValueError) as e:
                    # Log any issues with specific market entries
                    self.log_manager.debug(f"Skipping market due to error: {e}, item: {item}")
                    continue


            return filtered_data, usd_pairs, average_quote_volume
        except Exception as e:
            self.log_manager.error(f"Error in filter_market_data: {e}")
            return [], [], 0

    async def filter_markets_by_criteria(self, minimum_volume_market_data, usd_pairs):
        """
        Filter markets based on 24-hour quote volume and USD quote only.

        Args:
            minimum_volume_market_data (list): Markets filtered by volume.
            usd_pairs (list): Markets with USD as the quote currency.

        Returns:
            tuple: Filtered markets based on volume and USD pairs.
        """
        try:
            # msg = self.shared_utils_debugger.debug_code(os.path.abspath(__file__),stack()) #debugging
            # Filter markets by 24-hour quote volume
            supported_markets_vol = [
                market for market in minimum_volume_market_data
                if float(market['info'].get('24h_quote_volume', 0)) > 0
            ]

            # Filter markets by USD quote
            supported_markets_usd = [
                market for market in usd_pairs if market.get('quote') == 'USD'
            ]

            return supported_markets_vol, supported_markets_usd
        except Exception as e:
            self.log_manager.error(f"Error filtering markets: {e}", exc_info=True)
            return [], []

    def prepare_dataframe(self, tickers_dict, balances):
        """
        Prepare a DataFrame of tickers and balances.

        Args:
            tickers_dict (list): List of market data dictionaries containing asset info.
            balances (dict): Dictionary of balances, where each value is a PortfolioPosition object.

        Returns:
            pd.DataFrame: A DataFrame containing combined ticker and balance data.
        """
        try:
            # Extract balance data directly from PortfolioPosition objects in the balances dictionary
            avail_qty = {asset: details.available_to_trade_crypto for asset, details in balances.items()}
            total_qty = {asset: details.total_balance_crypto for asset, details in balances.items()}

            # Transform tickers_dict into a DataFrame
            if tickers_dict:
                df = pd.DataFrame(tickers_dict)

                # Flatten the 'info' column into separate columns
                info_df = pd.json_normalize(df['info'])
                df = pd.concat([df.drop(columns=['info']), info_df], axis=1)

                # Map balances to the DataFrame
                df['free'] = df['asset'].map(avail_qty).fillna(Decimal(0))  # Add free column
                df['total'] = df['asset'].map(total_qty).fillna(Decimal(0))  # Add total column

                # Add USD balance as a row if available
                usd_position = balances.get('USD')
                if usd_position:
                    usd_row = {
                        'symbol': 'USD/USD',
                        'asset': 'USD',
                        'free': Decimal(usd_position.available_to_trade_fiat).quantize(Decimal('0.01')),
                        'total': Decimal(usd_position.total_balance_fiat).quantize(Decimal('0.01')),
                        'volume_24h': 0
                    }
                    df = pd.concat([df, pd.DataFrame([usd_row])], ignore_index=True)

            else:
                df = pd.DataFrame()

            return df
        except Exception as e:
            self.log_manager.error(f"Error in prepare_dataframe: {e}", exc_info=True)
            return pd.DataFrame()

    async def parallel_fetch_and_update(self, usd_pairs, df, update_type='current_price'):
        """PART I: Data Gathering and Database Loading
            PART VI: Profitability Analysis and Order Generation """
        current_prices = {}
        try:
            tickers = await self.fetch_bids_asks()
            if not tickers:
                self.log_manager.error("Failed to fetch bids and asks.")
                return df, current_prices
            if not callable(self.log_manager.info):
                self.log_manager.error("log_manager.info is not callable, check for possible overwriting.")
            #for symbol in df['symbol'].tolist():
            for symbol in usd_pairs['symbol'].tolist():
                try:
                    ticker = tickers.get(symbol)
                    if ticker:
                        bid = ticker.get('bid')
                        ask = ticker.get('ask')
                        if bid is None or ask is None:
                            self.log_manager.debug(f"Missing data for symbol {symbol}, skipping")
                            continue

                        if symbol in df['symbol'].values:
                            if update_type == 'bid_ask':
                                df.loc[df['symbol'] == symbol, ['bid', 'ask']] = [bid, ask]
                            elif update_type == 'current_price':
                                df.loc[df['symbol'] == symbol, 'current_price'] = float(ask)
                        current_prices[symbol] = float(ask)

                    elif symbol in ['USD/USD', 'USD']:
                        continue
                    else:
                        self.log_manager.info(f"No ticker data for symbol: {symbol}")

                except BadSymbol as bs:
                    self.log_manager.error(f"Bad symbol: {bs}")
                    continue
                except Exception as e:
                    self.log_manager.error(f"Error processing symbol {symbol}: {e}", exc_info=True)
                    continue
            return df, current_prices
        except Exception as e:
            self.log_manager.error(f'Error in parallel_fetch_and_update: {e}', exc_info=True)
            return df, current_prices

    async def get_portfolio_breakdown(self, portfolio_uuid: str, currency: str = "USD") -> object:
        """
        Fetch the portfolio breakdown using the REST client.

        Args:
            portfolio_uuid (str): The portfolio UUID.
            currency (str): Currency symbol for monetary values (default: "USD").

        Returns:
            object: Portfolio breakdown response object.
        """
        max_retries = 3
        retry_delay = 2  # Seconds

        for attempt in range(max_retries):
            try:
                response = self.rest_client.get_portfolio_breakdown(portfolio_uuid, currency)
                if not response or not hasattr(response, "breakdown"):
                    raise ValueError("Invalid response structure from portfolio breakdown API.")

                return response  # Return valid response
            except Exception as e:
                self.log_manager.error(f"Attempt {attempt + 1} failed: {e}",exc_info=True)
                if attempt == max_retries - 1:
                    self.log_manager.error("Max retries reached for get_portfolio_breakdown.")
                    return None
                await asyncio.sleep(retry_delay * (2 ** attempt))

    async def fetch_bids_asks(self):
        try:
            endpoint = 'public'
            params = {
                'paginate': True,
                'paginationCalls': 10,
                'limit': 300
            }
            tickers = await self.ccxt_api.ccxt_api_call(self.exchange.fetchBidsAsks, endpoint, params=params)
            if not tickers:
                self.log_manager.info("fetch_bids_asks: Received empty tickers list.")
                return None

            return tickers
        except Exception as e:
            self.log_manager.error(f"Error fetching bids and asks: {e}", exc_info=True)
            return {}

