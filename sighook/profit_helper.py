from decimal import Decimal, ROUND_DOWN
import traceback
import pandas as pd
import numpy as np
from datetime import datetime


class ProfitHelper:

    def __init__(self, utility, portfolio_manager, ticker_manager, database_manager, logmanager, config):
        self.portfolio_manager = portfolio_manager
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self.utility = utility
        self.ticker_manager = ticker_manager
        self.database_manager = database_manager
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    import pandas as pd
    from decimal import Decimal

    async def calculate_unrealized_profit_loss(self, aggregated_df):
        """PART VI: Profitability Analysis and Order Generation
        Asynchronously calculate and update unrealized profit/loss for each holding using a DataFrame.
        """
        try:
            # Fetch current prices
            update_type = 'current_price'
            df, current_prices = await self.ticker_manager.parallel_fetch_and_update(aggregated_df, update_type)

            # Update the DataFrame with current prices
            df['current_price'] = df['asset'].map(current_prices).fillna(df['current_price'])
            # Convert and calculate using Decimal for precision
            df['market_value'] = df['Balance'].apply(float) * df['current_price'].apply(float)
            df['unrealized_profit_loss'] = abs(df['market_value']) - abs(df['initial_investment'])
            df['unrealized_profit_loss'] = df['unrealized_profit_loss'].apply(lambda x: round(float(x), 2))

            # Calculate unrealized percent change, handle NaN/inf, and ensure Decimal precision
            df['unrealized_pct_change'] = ((df['market_value']-abs(df['initial_investment']))/((df[
                                            'market_value'] + abs(df['initial_investment']))/2)).fillna(0)
            # Handle NaNs and infinities more robustly
            df['unrealized_pct_change'] = df['unrealized_pct_change'].apply(
                lambda x: round(float(x), 2) if not pd.isna(x) else 0.0)

            return df  # Return the updated DataFrame with all calculations
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error calculating unrealized profit/loss: {e}", exc_info=True)
            raise

    def should_place_sell_order(self, asset, holding, current_price):
        """ PART VI: Profitability Analysis and Order Generation  operates directly on a holding object (an instance from
        the Holdings table) and the current_market_price,
        making decisions based on the latest available data.  unrealized profit and its percentage are calculated
        dynamically within the function, ensuring decisions are based on real-time data."""

        if not holding or not current_price:
            return False

        # Calculate current value and unrealized profit percentage
        current_value = holding['Balance'] * float(current_price)
        v1 = holding['market_value'] - holding['initial_investment']
        v2 = (holding['market_value'] + holding['initial_investment'])/2
        unrealized_profit_pct = v1/v2
        # print(f"Unrealized profit percentage for {asset}: {unrealized_profit_pct}") # debug
        # Decide to sell based on the calculated unrealized profit percentage
        return unrealized_profit_pct > self._take_profit or unrealized_profit_pct < self._stop_loss

    async def fetch_current_market_prices(self, symbols):  # async
        """ PART VI: Profitability Analysis and Order Generation"""

        market_prices = {}
        for symbol in symbols:
            _, bid, _ = await self.ticker_manager.fetch_ticker_data(symbol['symbol'])  # Accessing symbol
            if bid:
                market_prices[symbol['asset']] = bid
        return market_prices



    #  <><><><><><><><><><><><><><><><><><><>><><><><><><><><><><><><><><><><><><><>><>><><><><><><><><><><><><><><><><><><><>

    @staticmethod
    def update_realized_gains(symbol, trades, sell_price, profit_loss_data, sell_amount):
        # Assuming cost_basis_per_unit can be derived from profit_loss_data

        cost_basis_per_unit = profit_loss_data['total_cost'] / profit_loss_data['current_value']  # placeholder until
        # resolved.

        # Calculate realized gain
        realized_gain = (sell_price * sell_amount) - (cost_basis_per_unit * sell_amount)

        return realized_gain

    @staticmethod
    def construct_profit_data_row(product_id, profit_loss, balance):
        # Simplified method signature by removing 'balance' as it's already part of 'profit_loss'
        return {
            'Asset': product_id,
            'Unrealized PCT': profit_loss['unrealized_pct'].quantize(Decimal('0.001'), ROUND_DOWN),
            'Profit/Loss': profit_loss['unrealized_profit_loss'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Total Cost': profit_loss['total_cost'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Current Value': profit_loss['current_value'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Balance': str(balance)
        }

    def is_stop_triggered(self, holding, current_price):
        pass  # debug

    @staticmethod
    def update_trailing_stop(current_price, trailing_stop_percentage, peak_price=None, stop_price=None):
        """
        Update the trailing stop based on the current price.

        :param current_price: The current market price of the asset.
        :param trailing_stop_percentage: The percentage from the peak price at which the stop is set.
        :param peak_price: The highest price reached since the position was opened.
        :param stop_price: The current stop price.
        :return: Updated peak price and stop price.
        """

        # If peak_price is not defined or current_price is higher, update peak_price
        if peak_price is None or current_price > peak_price:
            peak_price = current_price
            # Calculate new stop price using the updated peak price
            stop_price = peak_price * (1 - trailing_stop_percentage / 100)

        # If current price is dropping but hasn't reached the new stop price, the stop price remains unchanged
        #  return the current peak_price and stop_price
        return peak_price, stop_price