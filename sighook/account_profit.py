
from decimal import Decimal, ROUND_DOWN
import asyncio
import pandas as pd


class ProfitabilityManager:

    def __init__(self, api_wrapper, utility, order_manager, portfolio_manager, logmanager):
        self.exchange = api_wrapper.exchange
        self.api_wrapper = api_wrapper
        self.ledger_cache = None
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.log_manager = logmanager
        self.utility = utility
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

    async def check_profit_level(self, profit_data, current_holdings):
        tasks = []
        for item in current_holdings:
            if item['Currency'] == 'USD':
                continue
            tasks.append(self.process_holding(item, current_holdings))

        # Run tasks concurrently and collect results
        rows_to_add = await asyncio.gather(*tasks)

        # Filter out None values in case some tasks did not return a new row
        rows_to_add = [row for row in rows_to_add if row is not None]

        # Now, outside the loop, we update the DataFrame in one go
        if rows_to_add:
            new_data = pd.DataFrame(rows_to_add)
            profit_data = pd.concat([profit_data, new_data], ignore_index=True)
            profit_data = self.update_profit_data_format(profit_data)
        return profit_data

    async def process_holding(self, item, current_holdings):
        counter = {'processed': 0}
        product_id = item['Currency']
        sell_quantity = item['Balance']
        # Skip if the product ID is USD
        if product_id == 'USD':
            return None
        try:
            # Retrieve the 'ask' price and symbol from ticker_cache for the current product_id
            current_symbol = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'symbol'].iloc[0]
            current_price = Decimal(self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'ask'].iloc[0])
        except IndexError as e:
            # Log and skip this holding if the price or symbol is not found
            self.log_manager.sighook_logger.error(f"Price or symbol for {product_id} not found in ticker_cache. Error: {e}")
            return None
        # Fetch filled orders for the current symbol
        filled_orders, counter = await self.api_wrapper.get_filled_orders(current_symbol, counter)

        # Skip this holding if there are no filled orders
        if not filled_orders:
            return None

        # Calculate profit based on filled orders and current price
        profit, purchase_decimal, diff_decimal = self.calculate_profit(filled_orders, current_price, sell_quantity)
        # Skip this holding if profit calculation fails
        if profit is None:
            return None
            # Process sell orders based on profit criteria
        if (-10.0 > profit > -11.0) or profit > 5.0:
            await self.order_manager.process_sell_order(current_symbol, current_price, current_holdings,
                                                        purchase_decimal, diff_decimal)

        # Return the new row to be added to the profit_data DataFrame
        return {
            'symbol': product_id,
            'profit': profit.quantize(Decimal('0.01'), rounding=ROUND_DOWN),
            'balance': str(sell_quantity)
        }

    @staticmethod
    def update_profit_data_format(profit_data):
        profit_data['profit'] = profit_data['profit'].map(lambda x: f"{x:>7}")
        profit_data['balance'] = profit_data['balance'].map(lambda x: f"{x:>10}")
        profit_data['balance'] = profit_data['balance'].str.pad(width=4, side='left', fillchar=' ')
        profit_data['symbol'] = profit_data['symbol'].str.pad(width=3, side='left')
        profit_data.reset_index(drop=True, inplace=True)
        return profit_data

    async def calculate_profits(self, start_time, portfolio_dir):
        self.ledger_cache = await self.portfolio_manager.track_trades(start_time, portfolio_dir)
        if isinstance(self.ledger_cache, pd.DataFrame):
            try:
                grouped = self.ledger_cache.groupby('symbol')
                # Calculate profit, number of trades, and total fees for each symbol
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

    def update_profit_data(self, profit_data, rows_to_add, filled_order_list):
        try:
            if rows_to_add:
                # Convert rows_to_add to DataFrame
                new_data = pd.DataFrame(rows_to_add)

                # Merge existing profit_data with new_data on 'symbol', ensuring an outer join
                profit_data = pd.merge(profit_data, new_data, on='symbol', how='outer', suffixes=('', '_new'))

                # Check and update for 'profit_new' and 'balance_new' columns
                if 'profit_new' in profit_data.columns:
                    profit_data['profit'] = profit_data['profit_new'].combine_first(profit_data['profit'])
                    profit_data.drop(columns=['profit_new'], inplace=True)  # Drop 'profit_new' after update

                if 'balance_new' in profit_data.columns:
                    profit_data['balance'] = profit_data['balance_new'].combine_first(profit_data['balance'])
                    profit_data.drop(columns=['balance_new'], inplace=True)  # Drop 'balance_new' after update

                # Format the columns (this part remains unchanged)
                profit_data['profit'] = profit_data['profit'].map(lambda x: f"{x:>7}")
                profit_data['balance'] = profit_data['balance'].map(lambda x: f"{x:>10}")
                profit_data['balance'] = profit_data['balance'].str.pad(width=4, side='left', fillchar=' ')
                profit_data['symbol'] = profit_data['symbol'].str.pad(width=3, side='left')

                # Reset index to maintain order
                profit_data.reset_index(drop=True, inplace=True)

            if filled_order_list:
                self.log_manager.sighook_logger.debug(
                    f'order_manager:: check_filled_orders: Crypto currently held: {filled_order_list}')
                return profit_data
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in update_profit_data: {e}')
            return profit_data

    def calculate_profit(self, filled_orders, current_price, sell_quantity, order_type='buy'):
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

    # profit_data.drop_duplicates(subset=['symbol'], keep='last', inplace=True)
    # async def old_check_profit_level(self, profit_data, current_holdings):
    #     """Determine if each coin  in  portfolio has > 15% PROFIT MARGIN. If so, create a sell order
    #     send a webhook to sell the coin."""
    #
    #     tasks = []
    #     filled_order_list = []
    #     rows_to_add = []
    #     counter = {'processed': 0}
    #     for item in current_holdings:
    #         product_id = item['Currency']
    #         sell_quantity = item['Balance']
    #         if product_id == 'USD':
    #             continue
    #             # Retrieve the 'ask' price from ticker_cache for the current product_id
    #         try:
    #             current_symbol = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'symbol'].iloc[0]
    #             current_price = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'ask'].iloc[0]
    #             current_price = decimal.Decimal(current_price)
    #         except IndexError:
    #             # Handle cases where the product_id is not found in ticker_cache
    #             self.log_manager.sighook_logger.error(f"Price for {product_id} not found in ticker_cache.")
    #             continue
    #         filled_orders, counter = await self.api_wrapper.get_filled_orders(current_symbol, counter)
    #         print(f'filled_orders: {filled_orders}  processed: {counter["processed"]}')
    #         # has_sell_orders = any(order['side'].lower() == 'sell' for order in filled_orders) if filled_orders else False
    #         if not filled_orders:
    #             continue
    #         else:
    #             print(f'product_id: {product_id} has  filled orders or has sell orders active')
    #         filled_order_list.append(product_id)
    #
    #         profit, purchase_decimal, diff_decimal = self.calculate_profit(filled_orders, current_price, sell_quantity)
    #         if profit is None:
    #             continue
    #         #  Stop Loss
    #         if -10.0 > profit > -11.0:  # temporary until all loosses < -11 are sold
    #             await self.order_manager.process_sell_order(current_symbol, current_price, current_holdings,
    #                                                         purchase_decimal, diff_decimal)
    #
    #         if profit > 5.0:
    #             await self.order_manager.process_sell_order(current_symbol, current_price, current_holdings,
    #                                                         purchase_decimal, diff_decimal)
    #
    #         new_row = {'symbol': product_id, 'profit': profit.quantize(Decimal('0.01'), rounding=ROUND_DOWN),
    #                    'balance': item['Balance']}
    #         rows_to_add.append(new_row)
    #     print(f" {counter['processed']} coins have been evaluated for buy sell conditions")
    #     return self.update_profit_data(profit_data, rows_to_add, filled_order_list)