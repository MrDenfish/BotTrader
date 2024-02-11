
from decimal import Decimal, ROUND_DOWN
import asyncio
import pandas as pd
import datetime


class ProfitabilityManager:

    def __init__(self, api_wrapper, utility, order_manager, portfolio_manager, logmanager, config):
        self.exchange = api_wrapper.exchange
        self._stop_loss = Decimal(config.stop_loss)
        self._take_profit = Decimal(config.take_profit)
        self.api_wrapper = api_wrapper
        self.ledger_cache = None
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    def set_trade_parameters(self, start_time, session, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    async def check_profit_level(self, profit_data, holdings):
        tasks = []
        try:
            for item in holdings:
                if item['Currency'] == 'USD':
                    continue
                tasks.append(self.process_holding(item, holdings))

            # Run tasks concurrently and collect results
            rows_to_add = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out None values in case some tasks did not return a new row
            rows_to_add = [row for row in rows_to_add if row is not None]

            # Now, outside the loop, we update the DataFrame in one go
            if rows_to_add:
                new_data = pd.DataFrame(rows_to_add)
                profit_data = pd.concat([profit_data, new_data], ignore_index=True)
                profit_data = self.update_profit_data(profit_data, rows_to_add)
            return profit_data
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error in check_profit_level: {e}")
            return profit_data

    async def process_holding(self, item, holdings):
        """calculate profit/loss for each holding, weighted average"""
        counter = {'processed': 0}
        product_id = item['Currency']
        balance = item['Balance']
        # Skip if the product ID is USD
        filled_orders = None
        trigger = None
        if product_id == 'USD':
            return None
        try:
            # Retrieve the 'ask' price and symbol from ticker_cache for the current product_id
            current_symbol = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'symbol'].iloc[0]

            current_price = Decimal(self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'ask'].iloc[0])
            current_price = Decimal(current_price).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        except IndexError as e:
            # Log and skip this holding if the price or symbol is not found
            self.log_manager.sighook_logger.error(f"Price or symbol for {product_id} not found in ticker_cache. Error: {e}")
            return None
        # Fetch filled orders for the current symbol
        try:
            filled_orders = await self.portfolio_manager.get_my_trades(current_symbol)
            # Skip this holding if there are no filled orders
            if not filled_orders:
                return None

            # calculate the current holdings profit/loss
            profit_loss = await self.calculate_profit_loss(current_symbol, current_price, filled_orders, balance)
            unrealized_gains = profit_loss['unrealized_profit_loss']
            unrealized_gains = Decimal(unrealized_gains).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            unrealized_pct = profit_loss['unrealized_pct']
            total_cost = profit_loss['total_cost']
            cost_basis = Decimal(total_cost).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            current_value = profit_loss['current_value']
            current_value = Decimal(current_value).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

            # Calculate weighted_average based on filled orders and current price
            profit, purchase_decimal, diff_decimal = self.weighted_average(filled_orders, current_price, balance)
            # Skip this holding if profit calculation fails
            if profit_loss is None:
                return None
                # Process sell orders based on profit criteria
            if (-.1 > unrealized_pct > -.11) or unrealized_pct > self.take_profit or unrealized_pct < self.stop_loss:
                if unrealized_pct > self.take_profit:
                    trigger = 'Profit'
                elif unrealized_pct < self.stop_loss:
                    trigger = 'Stop-Loss'
                    quote = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'quote'].iloc[0]
                    self.log_manager.sighook_logger.info(f"{trigger} triggered for {product_id}. Unrealized gains:"
                                                         f"{unrealized_gains}{quote}")
                await self.order_manager.process_sell_order(current_symbol, current_price, holdings,
                                                            purchase_decimal, diff_decimal, trigger)

            # Return the new row to be added to the profit_data DataFrame
            return {
                'Symbol': product_id,
                'Unrealized PCT': unrealized_pct.quantize(Decimal('0.001'), rounding=ROUND_DOWN),  # pct
                'Profit/Loss': unrealized_gains.quantize(Decimal('0.01'), rounding=ROUND_DOWN),  # USD
                'Total Cost': cost_basis.quantize(Decimal('0.01'), rounding=ROUND_DOWN),  # USD
                'Current Value': current_value.quantize(Decimal('0.01'), rounding=ROUND_DOWN),  # USD
                'Balance': str(balance)
            }
        except RuntimeError as re:
            self.log_manager.sighook_logger.error(f"RuntimeError: {re}")
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error occurred: {e}")
            raise

    async def profits(self, start_time, portfolio_dir):  # called from main
        self.ledger_cache = await self.portfolio_manager.track_trades(portfolio_dir)
        if isinstance(self.ledger_cache, pd.DataFrame):
            try:
                grouped = self.ledger_cache.groupby('symbol')
                # Calculate Total profit, number of trades, and total fees for each symbol
                profitability = grouped.apply(lambda x: pd.Series({
                    'profit': (x['cost'].sum() - x['fee'].sum()),
                    'number_of_trades': x['type'].count(),
                    'total_fees': x['fee'].sum()
                })).reset_index()

                # Create a DataFrame for overall totals
                overall = pd.DataFrame({
                    'symbol': ['ALL'],
                    'profit': [profitability['profit'].sum()],
                    'number_of_trades': [profitability['number_of_trades'].sum()],
                    'total_fees': [profitability['total_fees'].sum()]
                })

                # Concatenate the total row at the bottom
                profitability = pd.concat([profitability, overall], ignore_index=True)

                return self.ledger_cache, profitability
            except Exception as e:
                print(f"Error calculating profits: {e}")
                return self.ledger_cache, None
        return self.ledger_cache, None

    def update_profit_data(self, profit_data, rows_to_add):
        try:
            if rows_to_add:
                # Convert rows_to_add to DataFrame
                new_data = pd.DataFrame(rows_to_add)

                # Merge existing profit_data with new_data on 'symbol', ensuring an outer join
                profit_data = pd.merge(profit_data, new_data, on='Symbol', how='outer', suffixes=('', '_new'))

                # Check and update for 'profit_new' and 'balance_new' columns
                if 'Profit/Loss_new' in profit_data.columns:
                    profit_data['Profit/Loss'] = profit_data['Profit/Loss_new'].combine_first(profit_data['Profit/Loss'])
                    profit_data.drop(columns=['Profit/Loss_new'], inplace=True)  # Drop 'profit_new' after update

                if 'Balance_new' in profit_data.columns:
                    profit_data['Balance'] = profit_data['Balance_new'].combine_first(profit_data['Balance'])
                    profit_data.drop(columns=['Balance_new'], inplace=True)  # Drop 'balance_new' after update

                if 'Unrealized PCT' in profit_data.columns:
                    profit_data['Unrealized PCT'] = (profit_data['Unrealized PCT_new'].
                                                     combine_first(profit_data['Unrealized PCT']))
                    profit_data.drop(columns=['Unrealized PCT_new'], inplace=True)  # Drop 'balance_new' after update

                if 'Total Cost' in profit_data.columns:
                    profit_data['Total Cost'] = profit_data['Total Cost_new'].combine_first(profit_data['Total Cost'])
                    profit_data.drop(columns=['Total Cost_new'], inplace=True)  # Drop 'balance_new' after update

                if 'Current Value' in profit_data.columns:
                    profit_data['Current Value'] = (profit_data['Current Value_new'].
                                                    combine_first(profit_data['Current Value']))
                    profit_data.drop(columns=['Current Value_new'], inplace=True)  # Drop 'balance_new' after update
                # Format the columns
                profit_data['Profit/Loss'] = profit_data['Profit/Loss'].map(lambda x: f"{x:>7}")
                profit_data['Total Cost'] = profit_data['Total Cost'].map(lambda x: f"{x:>7}")
                profit_data['Current Value'] = profit_data['Current Value'].map(lambda x: f"{x:>7}")
                profit_data['Unrealized PCT'] = profit_data['Unrealized PCT'].map(lambda x: f"{x:>7}")
                profit_data['Balance'] = profit_data['Balance'].map(lambda x: f"{x:>10}")
                profit_data['Balance'] = profit_data['Balance'].str.pad(width=4, side='left', fillchar=' ')
                profit_data['Symbol'] = profit_data['Symbol'].str.pad(width=3, side='left')

                # Reset index to maintain order
                profit_data.reset_index(drop=True, inplace=True)

                return profit_data
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in update_profit_data: {e}')
            return profit_data

    @staticmethod
    async def calculate_profit_loss(symbol, current_price, trades, balance):
        holdings_left = Decimal(str(balance))
        total_cost = Decimal('0')

        # Sort trades by date in descending order to work backwards
        trades.sort(key=lambda x: x['datetime'], reverse=True)

        for trade in trades:
            if holdings_left <= 0:
                break  # Stop if all current holdings are accounted for

            if trade['side'] == 'buy':
                amount = Decimal(str(trade['amount']))
                price = Decimal(str(trade['price']))
                fee = Decimal(str(trade.get('fee', {}).get('cost', '0')))

                # Determine how much of this purchase contributes to current holdings
                contrib_amount = min(amount, holdings_left)
                contrib_cost = (contrib_amount * price) + fee * (contrib_amount / amount)

                total_cost += contrib_cost
                holdings_left -= contrib_amount

        # Calculate current market value of holdings
        current_value = balance * current_price

        # Calculate unrealized profit or loss
        unrealized_pnl = current_value - total_cost  # in quote currency
        unrealized_pct = (unrealized_pnl / total_cost)  # percent

        return {
            'total_cost': total_cost,
            'current_value': current_value,
            'unrealized_profit_loss': unrealized_pnl,
            'unrealized_pct': unrealized_pct
        }

    def weighted_average(self, filled_orders, current_price, sell_quantity, order_type='buy'):
        # Filter orders by the specified type (buy or sell)

        filtered_orders = [order for order in filled_orders if order['side'].lower() == order_type.lower()]
        if not filtered_orders:
            return None, None, None
        # Calculate weighted average purchase price
        total_cost = Decimal('0.0')
        total_quantity = Decimal('0.0')
        for order in filtered_orders:
            if order['side'].lower() == 'buy':
                quantity = Decimal(order['amount'])
                total_cost += Decimal(str(order['cost'])) + Decimal(str(order.get('fee', {}).get('cost', '0')))
                total_quantity += quantity

        if total_quantity == 0:
            raise ValueError("No filled orders of the specified type")

        average_cost = total_cost / total_quantity

        # Calculate profit
        current_price_decimal = Decimal(current_price)
        profit = (current_price_decimal - average_cost) * Decimal(sell_quantity)

        # Format the numbers
        average_cost_decimal = average_cost.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        profit_decimal = profit.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        diff = self.utility.percentage_difference(current_price_decimal, average_cost)
        diff_decimal = Decimal(diff).quantize(Decimal('0.1'), rounding=ROUND_DOWN)

        return profit_decimal, average_cost_decimal, diff_decimal

    async def test_total_calculate_profit_loss(self, symbol, current_price, trades):
        """" Not currently in use may be modified at latter date to calculate profit/loss for each holding """

        holdings = []  # To keep track of purchased amounts and their costs
        total_sold = Decimal('0')
        profit_loss = Decimal('0')

        try:
            buy_count = 0
            sell_count = 0

            for trade in trades:
                amount = Decimal(str(trade['amount']))
                price = Decimal(str(trade['price']))
                fee = Decimal(str(trade.get('fee', {}).get('cost', '0')))
                trade_datetime = datetime.datetime.strptime(trade['datetime'], "%Y-%m-%dT%H:%M:%S.%fZ")

                if trade['side'] == 'buy':
                    buy_count += 1
                    cost = (amount * price) + fee
                    holdings.append({'amount': amount, 'cost': cost, 'date': trade_datetime})
                    self.log_manager.sighook_logger.info(
                        f"Buy: {amount} units at ${price} each on {trade_datetime}. Total cost: ${cost}")

                elif trade['side'] == 'sell':
                    sell_count += 1
                    earnings = amount * price - fee
                    self.log_manager.sighook_logger.info(
                        f"Preparing to sell: {amount} units at ${price} each on {trade_datetime}. "
                        f"Potential earnings: ${earnings}")

                    while amount > 0 and holdings:
                        oldest_holding = holdings[0]
                        if amount >= oldest_holding['amount']:
                            amount -= oldest_holding['amount']
                            realized_profit = oldest_holding['amount'] * (
                                        price - (oldest_holding['cost'] / oldest_holding['amount']))
                            profit_loss += realized_profit
                            self.log_manager.sighook_logger.info(
                                f"Sold: {oldest_holding['amount']} units from {oldest_holding['date']}. "
                                f"Realized profit: ${realized_profit}")
                            holdings.pop(0)  # Remove the oldest holding after it's fully sold
                        else:
                            oldest_holding['amount'] -= amount
                            partial_profit = amount * (price - (oldest_holding['cost'] / oldest_holding['amount']))
                            profit_loss += partial_profit
                            oldest_holding['cost'] -= amount * (oldest_holding['cost'] / oldest_holding['amount'])
                            self.log_manager.sighook_logger.info(
                                f"Sold: {amount} units from {oldest_holding['date']}. Realized profit: ${partial_profit}")
                            amount = Decimal('0')

            # Calculate Unsold Holdings and Average Cost
            unsold_amount = sum(holding['amount'] for holding in holdings)
            total_cost_unsold = sum(holding['cost'] for holding in holdings)

            # Determine Current Market Value
            current_value_unsold = unsold_amount * current_price

            # Calculate Profit/Loss for Unsold Holdings
            unsold_profit_loss = current_value_unsold - total_cost_unsold

            return {
                'total_profit_loss': profit_loss + unsold_profit_loss,
                'unsold_amount': unsold_amount,
                'unsold_average_cost': total_cost_unsold / unsold_amount if unsold_amount > 0 else Decimal('0'),
                'current_market_value_unsold': current_value_unsold,
                'unsold_profit_loss': unsold_profit_loss,
            }

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in calculate_profit_loss: {e}')
            return None
