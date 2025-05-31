import asyncio
from decimal import Decimal

import pandas as pd
import decimal
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
    async def get_instance(cls, config, shared_utils_debugger, shared_utils_print, logger_manager, rest_client, portfolio_uuid, exchange, ccxt_api,
                           shared_data_manager, shared_utils_precision):
        """Ensures only one instance of TickerManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:  # Double-check after acquiring the lock
                    cls._instance = cls(config, shared_utils_debugger, shared_utils_print, logger_manager, rest_client, portfolio_uuid, exchange,
                                        ccxt_api, shared_data_manager, shared_utils_precision)
        return cls._instance

    def __init__(self, config, shared_utils_debugger, shared_utils_print, logger_manager, rest_client, portfolio_uuid, exchange, ccxt_api,
                 shared_data_manager, shared_utils_precision):
        if TickerManager._instance is not None:
            raise Exception("TickerManager is a singleton and has already been initialized!")
        self.bot_config = config
        self.exchange = exchange
        self.rest_client = rest_client
        self.portfolio_uuid = portfolio_uuid
        self.min_volume = None
        self.last_ticker_update = None
        self.shill_coins = self.bot_config._shill_coins
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.ccxt_api = ccxt_api
        self.shared_data_manager = shared_data_manager
        self.shared_utils_print = shared_utils_print
        self.shared_utils_debugger = shared_utils_debugger
        self.shared_utils_precision = shared_utils_precision
        self.start_time = None

    # Potentially for future use
    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def ticker_cache(self):
        return self.market_data.get("ticker_cache", {})

    # <><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><>

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
                self.logger.error("Market data retrieval failed, returning empty dataset.")
                return {}, {}

            if not non_zero_balances:
                self.logger.warning("No non-zero balances found, returning empty dataset.")

            # Process market data
            filtered_vol_data, usd_pairs, avg_volume = self.filter_volume_for_market_data(market_data)
            supported_vol_markets, supported_usd_markets = await self.filter_markets_by_criteria(
                filtered_vol_data, usd_pairs
            )

            # Prepare DataFrame caches
            tickers_cache = self.prepare_dataframe(supported_vol_markets, non_zero_balances)
            usd_pairs_cache = self.prepare_dataframe(supported_usd_markets, non_zero_balances)

            if tickers_cache.empty:
                self.logger.error("Ticker cache is empty.")
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
            self.logger.error(f"❌ Error in update_ticker_cache: {e}", exc_info=True)
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
            self.logger.error(f"❌ Error in fetch_and_filter_balances: {e}", exc_info=True)
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
            usd_precision = Decimal('0.01')

            processed_positions = {}

            for asset, data in non_zero_balances.items():
                # Retrieve precision from tickers_cache
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)
                precision = {'amount': base_deci, 'price': quote_deci}
                # ticker_precision_map.get(asset, {'amount': None, 'price': None}))

                # Use vars() to get attributes of the custom object as a dictionary
                data_dict = vars(data) if hasattr(data, '__dict__') else data

                # Initialize the processed position with precision values
                processed_position = {"precision": precision}

                for key, value in data_dict.items():

                    if isinstance(value, (float, int, Decimal)) and not isinstance(value, bool):
                        value_str = str(value)
                        try:
                            decimal_value = Decimal(value_str)

                            if key in ['total_balance_crypto', 'available_to_trade_crypto', 'available_to_transfer_crypto']:
                                precision = Decimal(f'1e-{base_deci}')
                            elif key in ['total_balance_fiat', 'available_to_trade_fiat', 'available_to_transfer_fiat']:
                                precision = Decimal(f'1e-{quote_deci}')
                            else:
                                # Use a conservative default precision for any other numerics
                                precision = Decimal('1.0') if decimal_value == decimal_value.to_integral() else Decimal(f'1e-{quote_deci}')

                            processed_position[key] = self.shared_utils_precision.safe_quantize(decimal_value, precision)

                        except (decimal.InvalidOperation, ValueError) as e:
                            self.logger.warning(f"⚠️ Failed to quantize {key}={value_str} with precision={precision}: {e}")
                            processed_position[key] = Decimal("0")

                # Add the processed position to the final dictionary
                processed_positions[asset] = processed_position

            return processed_positions

        except Exception as e:
            self.logger.error(f"❌ Error in process_spot_positions: {e}", exc_info=True)
            return {}

    def safe_float(self, val, default=0.0):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def filter_volume_for_market_data(self, market_data):
        """
        Filters market data based on volume criteria.
        - First pass: calculate average quote volume
        - Second pass: filter data based on that average
        """
        try:
            filtered_data = []
            usd_pairs = []
            valid_volumes = []

            # First pass: Gather valid volumes for averaging
            for item in market_data:
                info = item.get('info', {})
                if info.get('new') and info.get('quote') == 'USD':
                    print(f"\n< -------- New market found: {item.get('symbol')} -------- >\n")
                    # TODO: Insert/remove from 'new_markets' DB table as needed

                quote_volume = self.safe_float(info.get('approximate_quote_24h_volume'))
                if quote_volume > 0:
                    valid_volumes.append(quote_volume)

            if not valid_volumes:
                self.logger.debug("No valid quote volumes found for averaging.")
                return [], [], None

            average_quote_volume = sum(valid_volumes) / len(valid_volumes)

            # Second pass: Build filtered datasets
            for item in market_data:
                try:
                    info = item.get('info', {})
                    quote_volume = self.safe_float(info.get('approximate_quote_24h_volume'))
                    volume_24h = self.safe_float(info.get('volume_24h'))
                    price = self.safe_float(info.get('price'))
                    price_change = self.safe_float(info.get('price_percentage_change_24h'))

                    if item.get('quote') != 'USD' or not item.get('active') or item.get('type') != 'spot':
                        continue

                    common_entry = {
                        'asset': item.get('base'),
                        'quote': item.get('quote'),
                        'symbol': item.get('symbol'),
                        'precision': item.get('precision'),
                        'info': {
                            'product_id': info.get('product_id'),
                            'type': item.get('type'),
                            'price': price,
                            'volume_24h': volume_24h,
                            '24h_quote_volume': quote_volume,
                            'price_percentage_change_24h': price_change,
                            'average_min_vol': average_quote_volume  # ✅ Added here
                        }
                    }

                    usd_pairs.append(common_entry)

                    if quote_volume >= average_quote_volume:
                        filtered_data.append(common_entry)

                except Exception as e:
                    self.logger.debug(f"Skipping market due to error: {e}, item: {item}")
                    continue

            return filtered_data, usd_pairs, average_quote_volume

        except Exception as e:
            self.logger.error(f"❌ Error in filter_market_data: {e}")
            return [], [], 0

    async def filter_markets_by_criteria(self, minimum_volume_market_data, usd_pairs):
        """
        Filter markets based on 24-hour quote volume and USD quote only.
        Filter out undesirable coins

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
            # filter out sketchy coins
            supported_markets_usd = [
                market for market in supported_markets_usd if market.get('asset') not in self.shill_coins
            ]
            return supported_markets_vol, supported_markets_usd
        except Exception as e:
            self.logger.error(f"❌ Error filtering markets: {e}", exc_info=True)
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
            self.logger.error(f"❌ Error in prepare_dataframe: {e}", exc_info=True)
            return pd.DataFrame()

    async def parallel_fetch_and_update(self, usd_pairs, df, update_type='current_price'):
        """PART I: Data Gathering and Database Loading
            PART VI: Profitability Analysis and Order Generation """
        current_prices = {}
        try:
            tickers = await self.fetch_bids_asks()
            if not tickers:
                self.logger.error("Failed to fetch bids and asks.")
                return df, current_prices
            if not callable(self.logger.info):
                self.logger.error("log_manager.info is not callable, check for possible overwriting.")
            #for symbol in df['symbol'].tolist():
            for symbol in usd_pairs['symbol'].tolist():
                try:
                    ticker = tickers.get(symbol)
                    if ticker:
                        bid = ticker.get('bid')
                        ask = ticker.get('ask')
                        if bid is None or ask is None:
                            self.logger.debug(f"Missing data for symbol {symbol}, skipping")
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
                        self.logger.info(f"No ticker data for symbol: {symbol}")

                except BadSymbol as bs:
                    self.logger.error(f"❌ Bad symbol: {bs}")
                    continue
                except Exception as e:
                    self.logger.error(f"❌ Error processing symbol {symbol}: {e}", exc_info=True)
                    continue
            return df, current_prices
        except Exception as e:
            self.logger.error(f'❌ Error in parallel_fetch_and_update: {e}', exc_info=True)
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
                self.logger.error(f"❌ Attempt {attempt + 1} failed: {e}", exc_info=True)
                if attempt == max_retries - 1:
                    self.logger.error("Max retries reached for get_portfolio_breakdown.")
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
                self.logger.info("fetch_bids_asks: Received empty tickers list.")
                return None

            return tickers
        except Exception as e:
            self.logger.error(f"❌ Error fetching bids and asks: {e}", exc_info=True)
            return {}

