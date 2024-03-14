
from decimal import Decimal, ROUND_DOWN
import asyncio
from sqlalchemy import func
import traceback
from database_manager import Trade, Holding, SymbolUpdate
from dateutil import parser
from datetime import datetime

class ProfitHelper:

    def __init__(self, utility, portfolio_manager, ticker_manager, logmanager, config):
        self.portfolio_manager = portfolio_manager
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self.utility = utility
        self.ticker_manager = ticker_manager
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

    def update_holdings_from_list(self, session, holdings):

        """take holdings list and update the database with the latest information.  This function is designed to be used
        in conjunction with the fetch_holdings method in the portfolio_manager class.  The holdings list is a list of"""
        try:
            for item in holdings:
                symbol = item['symbol']

                # Use the aggregate_trade_data_for_symbol function to get aggregated trade data
                aggregated_data = self.aggregate_trade_data_for_symbol(session, symbol)

                if aggregated_data:
                    # Check for an existing holding and update or create as needed
                    holding = session.query(Holding).filter_by(currency=symbol).first()
                    if holding:
                        # Update existing holding with aggregated data
                        holding.purchase_date = aggregated_data['earliest_trade_time']
                        holding.purchase_price = aggregated_data['purchase_price']  # Set purchase price
                        holding.purchase_amount = aggregated_data['total_amount']
                        holding.balance = item['Balance']  # Assuming you want to update the balance
                        holding.average_cost = aggregated_data['average_cost']
                        holding.total_cost = aggregated_data['total_cost']
                    else:
                        # Create a new holding record if one doesn't exist for this symbol
                        new_holding = Holding(
                            currency=symbol,
                            purchase_date=aggregated_data['earliest_trade_time'],
                            purchase_price=aggregated_data['purchase_price'],
                            purchase_amount=aggregated_data['total_amount'],
                            balance=item['Balance'],
                            average_cost=aggregated_data['average_cost'],
                            total_cost=aggregated_data['total_cost'],
                        )
                        session.add(new_holding)

        except Exception as e:
            error_details = traceback.format_exc()
            session.rollback()  # Roll back the session in case of error
            self.log_manager.sighook_logger.error(f"Error updating holdings from list: {error_details}, {e}")

    def aggregate_trade_data_for_symbol(self, session, symbol):
        # Aggregate trade data for the given symbol, considering only 'buy' trades for purchase data
        aggregation = session.query(
            func.min(Trade.trade_time).label('earliest_trade_time'),
            func.sum(Trade.amount).label('total_amount'),
            func.sum(Trade.cost).label('total_cost'),
        ).filter(
            Trade.symbol == symbol,
            Trade.side == 'buy'  # Consider only buy trades for calculating purchase details
        ).group_by(Trade.symbol).one_or_none()

        if aggregation and aggregation.total_amount > 0:
            # Calculate weighted average price (total cost / total amount)
            weighted_average_price = aggregation.total_cost / aggregation.total_amount
            return {
                'earliest_trade_time': aggregation.earliest_trade_time,
                'total_amount': aggregation.total_amount,
                'total_cost': aggregation.total_cost,
                'average_cost': weighted_average_price,  # This now also represents the purchase price
                'purchase_price': weighted_average_price,  # Explicitly stating it as purchase_price for clarity
            }
        else:
            # Handle the case where there are no 'buy' trades for the given symbol
            return None

    def calculate_unrealized_profit_loss(self, session):
        """
           Calculate and update unrealized profit/loss for each holding.

           :param session: The SQLAlchemy session.
           :param current_prices: A dictionary mapping currencies to their current market prices.
           """
        try:
            _, current_prices = self.ticker_manager.parallel_fetch_and_update(self.ticker_cache)
            holdings = session.query(Holding).all()

            for holding in holdings:
                # Fetch the current market price for the holding's currency
                current_price = Decimal(current_prices.get(holding.currency, 0))
                holding.current_price = current_price

                if current_price:
                    # Calculate the original total cost of the holding
                    total_cost = holding.average_cost * holding.balance

                    # Calculate the market value of the remaining balance at the current price
                    market_value = holding.balance * current_price

                    # Calculate unrealized profit or loss in absolute terms
                    unrealized_p_l = market_value - total_cost

                    # Update the holding with the calculated unrealized profit/loss
                    holding.unrealized_profit_loss = unrealized_p_l.quantize(Decimal('.01'))

                    # Calculate and update unrealized profit or loss as a percentage
                    if total_cost != 0:  # To avoid division by zero
                        percentage_change = ((unrealized_p_l / total_cost) * 100).quantize(Decimal('.01'))
                        holding.unrealized_pct_change = percentage_change
                    else:
                        holding.unrealized_pct_change = Decimal(0)
            return current_prices
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f"Error calculating profit/loss: {error_details},  {e}")
            session.rollback()
            return None

    def should_place_sell_order(self, holding, current_price):
        """operates directly on a holding object (an instance from the Holdings table) and the current_market_price,
        making decisions based on the latest available data.  unrealized profit and its percentage are calculated
        dynamically within the function, ensuring decisions are based on real-time data."""

        if not holding or not current_price:
            return False

        # Calculate current value and unrealized profit percentage
        current_value = holding.balance * Decimal(current_price)
        unrealized_profit = current_value - (holding.balance * holding.average_cost)
        unrealized_profit_pct = ((unrealized_profit / (
                    holding.average_cost * holding.balance)) * 100) if holding.average_cost > 0 else Decimal('0')

        # Decide to sell based on the calculated unrealized profit percentage
        return unrealized_profit_pct > self._take_profit or unrealized_profit_pct < self._stop_loss

    def process_sell_order_fifo(self, session, symbol, sell_amount, sell_price):
        try:
            buy_trades = session.query(Trade).filter(Trade.symbol == symbol, Trade.side == 'buy').order_by(
                Trade.trade_time.asc()).all()

            remaining_sell_amount = sell_amount
            total_realized_profit = Decimal('0')

            for buy_trade in buy_trades:
                if remaining_sell_amount <= 0:
                    break  # All sold

                available_for_sale = min(buy_trade.amount, remaining_sell_amount)
                realized_profit = (sell_price - buy_trade.price) * available_for_sale - buy_trade.fee
                total_realized_profit += realized_profit

                # Update the holding quantity
                buy_trade.amount -= available_for_sale
                remaining_sell_amount -= available_for_sale

                # Log realized profit
                #  new_realized_profit = RealizedProfit(symbol=symbol, profit_loss=realized_profit, ...)
                # session.add(new_realized_profit)

            return total_realized_profit
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f"Error processing sell order FIFO for {symbol}: {error_details}, {e}")
            session.rollback()
            return None

    #  <><><><><><><><><><><><><><><><><><><>><><><><><><><><><><><><><><><><><><><>><>><><><><><><><><><><><><><><><><><><><>

    def update_holding_from_buy(self, session, symbol, trade):
        try:
            # Fetch the existing holding record, if it exists
            holding = session.query(Holding).filter_by(currency=symbol).first()

            # Calculate the total cost of the new trade, including fees
            # Assuming 'fee' is part of the trade dictionary and expressed in the same currency as the purchase
            trade_cost = (trade['price'] * trade['amount']) + trade.get('fee', 0)

            if holding:
                # Update existing holding
                holding.quantity += trade['amount']
                holding.total_cost += trade_cost
                holding.average_cost = holding.total_cost / holding.quantity
            else:
                # Create a new holding if it doesn't exist
                new_holding = Holding(
                    currency=symbol,
                    purchase_date=parser.parse(trade['timestamp']),
                    balance=trade['amount'],
                    average_cost=trade_cost / trade['amount'],
                    total_cost=trade_cost
                )
                session.add(new_holding)
        except Exception as e:
            session.rollback()
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'update_holding_from_buy: {error_details}, {e}')

    def update_unrealized_profits(self, session, market_prices):
        """dynamically calculate and update unrealized profits in the database"""
        holdings = session.query(Holding).all()
        for holding in holdings:
            current_market_price = market_prices[holding.symbol]
            current_value = holding.quantity * current_market_price
            holding.unrealized_profit = current_value - (holding.quantity * holding.average_cost)
        session.commit()

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

    def fetch_current_market_prices(self, symbols):  # async
        market_prices = {}
        for symbol in symbols:
            _, bid, _ = self.ticker_manager.fetch_ticker_data(symbol)  # await  fetch_ticker_data returns symbol,bid,ask
            if bid:
                market_prices[symbol] = bid
        return market_prices



    def get_last_update_time_for_symbol(self, session, symbol):
        """Retrieve the last update time for a symbol from the database."""
        symbol_update = session.query(SymbolUpdate).filter_by(symbol=symbol).first()

        if symbol_update:
            return symbol_update.last_update_time
        else:
            # Return a default time if there's no record for the symbol
            # This could be the time when your trading application started, or an earlier date
            return datetime(2017, 12, 1)  # Example default date

    from datetime import datetime

    def set_last_update_time(self, session, symbol, last_update_timestamp):
        """Updates or sets the last update time for a given trading symbol in the database."""

        # Attempt to find an existing record for the symbol
        symbol_update = session.query(SymbolUpdate).filter_by(symbol=symbol).first()

        if symbol_update:
            # If a record exists, update the last update time
            symbol_update.last_update_time = last_update_timestamp
        else:
            # If no record exists, create a new one with the last update time
            new_symbol_update = SymbolUpdate(
                symbol=symbol,
                last_update_time=last_update_timestamp
            )
            session.add(new_symbol_update)

        try:
            # Attempt to commit changes to the database
            session.commit()
        except Exception as e:
            # Rollback in case of error
            session.rollback()
            # Log the error or raise an exception as per your error handling policy
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f"Error setting last update time for {symbol}: {error_details}, {e}")
            raise

    def process_trade_data(self, trade):
        # Extract relevant information from the raw trade object
        # Convert data types if necessary, e.g., timestamps to datetime objects
        try:
            # Initialize fee_cost
            fee_cost = None
            # Check if 'fee' exists in the trade and handle both possible formats
            if 'fee' in trade:
                fee = trade['fee']
                if isinstance(fee, dict) and 'cost' in fee:
                    # If 'fee' is a dictionary with a 'cost' key, extract the cost
                    fee_cost = fee['cost']
                elif isinstance(fee, (Decimal, float, int)):
                    # If 'fee' is a numeric type (Decimal, float, int), use it directly
                    fee_cost = fee
                    # Handle the 'timestamp' field correctly based on its type
            timestamp = trade.get('timestamp')
            if isinstance(timestamp, (int, float)):
                # If 'timestamp' is a numeric value, convert it from Unix time to datetime
                timestamp = datetime.utcfromtimestamp(timestamp / 1000.0)
            elif not isinstance(timestamp, datetime):
                # If 'timestamp' is neither numeric nor datetime, log an error or convert it as needed
                self.log_manager.sighook_logger.error(f'Unexpected timestamp format: {timestamp}')
                timestamp = None  # Set to None or handle as needed
            processed_trade = {
                'trade_time': trade.get('timestamp'),
                'id': trade.get('trade_id') or trade.get('id'),  # Handling different key names for trade ID
                'order_id': trade.get('order'),
                'symbol': trade.get('symbol'),
                'price': Decimal(trade['price']).quantize(Decimal('0.01'), ROUND_DOWN),  # Convert to Decimal for precise
                # financial calculations
                'amount': Decimal(trade['amount']).quantize(Decimal('0.00000001'), ROUND_DOWN),
                'cost': Decimal(trade['cost']).quantize(Decimal('0.01'), ROUND_DOWN),
                'side': trade['side'].lower(),  # Normalize to lowercase for consistency
                'fee': Decimal(str(fee_cost)).quantize(Decimal('0.01'), ROUND_DOWN) if fee_cost is not None else None,
                'timestamp': timestamp
                }

            return processed_trade
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'process_trade_data: {error_details}, {e}')
            return None
        # Perform any necessary validation or transformation on the extracted data
        # For example, you might want to ensure that 'side' is either 'buy' or 'sell'

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
