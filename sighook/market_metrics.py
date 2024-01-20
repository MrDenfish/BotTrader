import requests
import numpy as np

class MarketMetrics:
    def __init__(self):
        pass

    def get_market_data(self,crypto_symbol):
        """
        Fetch market data for a given cryptocurrency symbol.
        Returns market capitalization and trading volume.
        """
        # Replace with actual API call
        api_url = f"https://api.example.com/data?symbol={crypto_symbol}"
        response = requests.get(api_url)
        data = response.json()
        market_cap = data['market_cap']
        trading_volume = data['trading_volume']
        return market_cap, trading_volume

    def analyze_price_trends(self,crypto_symbol, period):
        """
        Analyze price trends over a given period.
        Returns a simple moving average or other trend indicators.
        """
        # Replace with actual API call
        api_url = f"https://api.example.com/price_data?symbol={crypto_symbol}&period={period}"
        response = requests.get(api_url)
        prices = response.json()['prices']
        # Example: Calculating simple moving average
        sma = np.mean(prices)
        return sma

    def calculate_volatility(self,crypto_symbol, period):
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
    crypto = "BTC"  # Bitcoin symbol
    market_cap, trading_volume = get_market_data(crypto)
    sma = analyze_price_trends(crypto, 30)  # 30-day period
    volatility = calculate_volatility(crypto, 30)
    print(f"Market Cap: {market_cap}, Trading Volume: {trading_volume}, SMA: {sma}, Volatility: {volatility}")
