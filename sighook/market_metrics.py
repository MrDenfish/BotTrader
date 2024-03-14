import requests
import numpy as np


class CoinMarketAPI:
    def __init__(self, api_key, api_url, logmanager):
        self.log_manager = logmanager
        self.api_key = api_key
        self.api_url = api_url
        self.headers = {
            'Accepts': 'application/json',
            'X-CMC_PRO_API_KEY': api_key,
        }

        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    def total_supply(self, df):
        # Assuming 'base_currency' column has the coin IDs compatible with the data platform
        try:
            df['total_supply'] = df['base_currency'].apply(lambda symbol: self.get_market_data(symbol))
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {e}')
        return df

    def get_market_data(self, symbol):
        """
        Fetch market data for a given cryptocurrency symbol.
        Returns market capitalization and trading volume.
        """
        response = None
        data_str = None
        parameters = {
            'symbol': symbol,
            'convert': 'USD'  # You can change the conversion currency as needed
        }
        try:
            response = requests.get(self.api_url, headers=self.headers, params=parameters)
            data = response.json()
            data_str = str(data)
        # Check if 'data' key exists and the symbol is present in the response
            if 'data' in data_str and symbol in data_str:
                self.log_manager.sighook_logger.debug(f' get_market_data:  Response :{data_str}')
                return data['data'][symbol].get('total_supply', 0)
            else:
                # Handle the case where 'data' or the symbol is not found
                return 0  # or any default value you deem appropriate
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in get_market_data: {e}. Response :{data_str}')
