from decimal import Decimal, ROUND_DOWN
import asyncio
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from sqlalchemy.future import select
import traceback
import pandas as pd
import numpy as np

from old_database_manager import Trade, Holding, SymbolUpdate, RealizedProfit
from dateutil import parser
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
            df['current_price'] = df['symbol'].map(current_prices).fillna(df['current_price'])

            # Calculate unrealized profit or loss
            df['total_cost'] = df['average_cost'] * df['Balance']
            df['market_value'] = df['Balance'] * df['current_price']
            df['unrealized_profit_loss'] = (df['market_value'] - df['total_cost']).apply(
                lambda x: Decimal(x).quantize(Decimal('.01')))

            # Calculate unrealized percent change
            df['unrealized_pct_change'] = (
                (df['unrealized_profit_loss'] / df['total_cost'] * 100).replace([pd.NA, pd.NaT, np.inf, -np.inf], 0))
            df['unrealized_pct_change'] = df['unrealized_pct_change'].apply(
                lambda x: Decimal(x).quantize(Decimal('.01')) if not pd.isna(x) else Decimal(0))

            return df  # Return the updated DataFrame with all calculations
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error calculating unrealized profit/loss: {e}", exc_info=True)
            raise

    def should_place_sell_order(self, holding, current_price):
        """ PART VI: Profitability Analysis and Order Generation  operates directly on a holding object (an instance from
        the Holdings table) and the current_market_price,
        making decisions based on the latest available data.  unrealized profit and its percentage are calculated
        dynamically within the function, ensuring decisions are based on real-time data."""

        if not holding or not current_price:
            return False

        # Calculate current value and unrealized profit percentage
        current_value = holding['Balance'] * Decimal(current_price)
        unrealized_profit = current_value - (holding['Balance'] * holding['average_cost'])
        unrealized_profit_pct = ((unrealized_profit / (
                    holding['average_cost'] * holding['Balance'])) * 100) if holding['average_cost'] > 0 else Decimal('0')

        # Decide to sell based on the calculated unrealized profit percentage
        return unrealized_profit_pct > self._take_profit or unrealized_profit_pct < self._stop_loss

    async def fetch_current_market_prices(self, symbols):  # async
        """ PART VI: Profitability Analysis and Order Generation"""

        market_prices = {}
        for symbol in symbols:
            _, bid, _ = await self.ticker_manager.fetch_ticker_data(symbol)  # await
            # bid,ask
            if bid:
                market_prices[symbol] = bid
        return market_prices



    def process_trade_data(self, trade):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Initialize fee_cost
            fee_cost = None

            # Extract the trade time, handling the case where 'info' may or may not be present
            if 'info' in trade and 'trade_time' in trade['info']:
                trade_time_str = trade['info']['trade_time']
                # Truncate the string to limit the number of decimal places to 6
                trade_time_str = trade_time_str.split('.')[0] + '.' + trade_time_str.split('.')[1][:6]
                trade_time = datetime.fromisoformat(trade_time_str.rstrip("Z"))  # Assuming ISO format string
            elif 'trade_time' in trade and isinstance(trade['trade_time'], datetime):
                trade_time = trade['trade_time']  # Directly use the datetime object
            else:
                self.log_manager.sighook_logger.error(
                    f"Unexpected or missing 'trade_time' in trade data for {trade.get('symbol')}")
                trade_time = None  # Handle the unexpected format or missing 'trade_time'

            # Handle the 'fee' field, which can be a dictionary or a direct numeric value
            if 'fee' in trade:
                fee = trade['fee']
                if isinstance(fee, dict) and 'cost' in fee:
                    fee_cost = fee['cost']
                elif isinstance(fee, (Decimal, float, int)):
                    fee_cost = fee
                else:
                    self.log_manager.sighook_logger.error(f"Unexpected 'fee' format in trade data for {trade.get('symbol')}")

            # Construct the processed trade dictionary
            processed_trade = {
                'trade_time': trade_time,
                'id': trade.get('id'),
                'order_id': trade.get('order_id'),
                'symbol': trade.get('symbol'),
                'price': Decimal(trade['price']).quantize(Decimal('0.01'), ROUND_DOWN) if 'price' in trade else None,
                'amount': Decimal(trade['amount']).quantize(Decimal('0.00000001'),
                                                            ROUND_DOWN) if 'amount' in trade else None,
                'cost': Decimal(trade['cost']).quantize(Decimal('0.01'), ROUND_DOWN) if 'cost' in trade else None,
                'side': trade.get('side').lower() if 'side' in trade else None,
                'fee': Decimal(str(fee_cost)).quantize(Decimal('0.01'), ROUND_DOWN) if fee_cost is not None else None,
            }

            return processed_trade
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'process_trade_data: {error_details}, {e}')
            return None
        # Perform any necessary validation or transformation on the extracted data
        # For example, you might want to ensure that 'side' is either 'buy' or 'sell'


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
            'Symbol': product_id,
            'Unrealized PCT': profit_loss['unrealized_pct'].quantize(Decimal('0.001'), ROUND_DOWN),
            'Profit/Loss': profit_loss['unrealized_profit_loss'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Total Cost': profit_loss['total_cost'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Current Value': profit_loss['current_value'].quantize(Decimal('0.01'), ROUND_DOWN),
            'Balance': str(balance)
        }

    def is_stop_triggered(self, holding, current_price):
        pass

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