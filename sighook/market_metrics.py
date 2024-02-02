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
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.current_holdings = hist_holdings

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
        parameters = {
            'symbol': symbol,
            'convert': 'USD'  # You can change the conversion currency as needed
        }
        try:
            response = requests.get(self.api_url, headers=self.headers, params=parameters)
            data = response.json()
        # Check if 'data' key exists and the symbol is present in the response
            if 'data' in data and symbol in data['data']:
                self.log_manager.sighook_logger.debug(f' get_market_data:  Response :{data}')
                return data['data'][symbol].get('total_supply', 0)
            else:
                # Handle the case where 'data' or the symbol is not found
                return 0  # or any default value you deem appropriate
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in get_market_data: {e}. Response :{response}')

    @staticmethod
    def analyze_price_trends(df, period=30):
        """
        Analyze price trends over a given period.
        Returns a simple moving average or other trend indicators.
        """
        # df['price'] = df['close'].apply(lambda x: x.get('price', None))
        df['trend'] = df['close'].rolling(window=period).mean()  # simple moving average
        df['volatility'] = df['close'].rolling(window=period).std()
        return df

    @staticmethod
    def calculate_volatility(crypto_symbol, period):
        """
        Calculate the volatility of a cryptocurrency over a specified period.
        Volatility is often measured as the standard deviation of price changes.
        """
        # Replace with actual API call
        api_url = f"https://api.example.com/price_data?symbol={crypto_symbol}&period={period}"
        response = requests.get(api_url)
        prices = response.json()['prices']
        volatility = np.std(prices)
        return volatility

    # Example usage
    # crypto = "BTC"  # Bitcoin symbol
    # market_cap, trading_volume = get_market_data(crypto)
    # sma = analyze_price_trends(crypto, 30)  # 30-day period
    # volatility = calculate_volatility(crypto, 30)
    # print(f"Market Cap: {market_cap}, Trading Volume: {trading_volume}, SMA: {sma}, Volatility: {volatility}")
