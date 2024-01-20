
import decimal
from decimal import Decimal, ROUND_DOWN

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
        self.start_time = None
        self.web_url = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.web_url = web_url
        self.current_holdings = hist_holdings

    def calculate_profits(self, start_time, portfolio_dir):
        self.ledger_cache = self.portfolio_manager.track_trades(start_time, portfolio_dir)
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

    def check_profit_level(self, profit_data, old_portfolio):
        """Determine if each coin  in  portfolio has > 15% PROFIT MARGIN. If so, create a sell order
        send a webhook to sell the coin."""
        filled_order_list = []
        rows_to_add = []

        for item in old_portfolio:

            product_id = item['Currency']
            sell_quantity = item['Balance']
            if product_id == 'USD':
                continue
                # Retrieve the 'ask' price from ticker_cache for the current product_id
            try:
                current_symbol = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'symbol'].iloc[0]
                current_price = self.ticker_cache.loc[self.ticker_cache['base'] == product_id, 'ask'].iloc[0]
                current_price = decimal.Decimal(current_price)
            except IndexError:
                # Handle cases where the product_id is not found in ticker_cache
                self.log_manager.sighook_logger.error(f"Price for {product_id} not found in ticker_cache.")
                continue
            filled_orders = self.api_wrapper.get_filled_orders(current_symbol)
            if not filled_orders:
                continue

            filled_order_list.append(product_id)
            profit, purchase_decimal, diff_decimal = self.calculate_profit(filled_orders, current_price, sell_quantity)

            if profit > 5.0:
                self.order_manager.process_sell_order(current_symbol, current_price, old_portfolio, purchase_decimal,
                                                      diff_decimal)

            new_row = {'symbol': product_id, 'profit': profit.quantize(Decimal('0.01'), rounding=ROUND_DOWN),
                       'balance': item['Balance']}
            rows_to_add.append(new_row)

        return self.update_profit_data(profit_data, rows_to_add, filled_order_list)

    def update_profit_data(self, profit_data, rows_to_add, filled_order_list):
        if rows_to_add:
            new_data = pd.DataFrame(rows_to_add)
            profit_data = pd.concat([profit_data, new_data], ignore_index=True)
            profit_data.drop_duplicates(subset=['symbol'], keep='last', inplace=True)
            profit_data['profit'] = profit_data['profit'].map(lambda x: f"{x:>7}")
            profit_data['balance'] = profit_data['balance'].map(lambda x: f"{x:>10}")
            profit_data['balance'] = profit_data['balance'].str.pad(width=4, side='left', fillchar=' ')
            profit_data['symbol'] = profit_data['symbol'].str.pad(width=3, side='left')
            profit_data.reset_index(drop=True, inplace=True)

        if filled_order_list:
            self.log_manager.sighook_logger.debug(f'order_manager:: check_filled_orders: Crypto currently held: '
                                                  f'{filled_order_list}')

        return profit_data

    def calculate_profit(self, filled_orders, current_price, sell_quantity):
        most_recent_buy_price = filled_orders[-1]['price']
        purchase_price = decimal.Decimal(most_recent_buy_price)
        #  add def adjust_precision(self, num_to_adjust, convert): from webhook/accessories.py
        diff = self.utility.percentage_difference(current_price, purchase_price)
        profit = (current_price - purchase_price) * Decimal(sell_quantity)
        purchase_decimal = purchase_price.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        diff_decimal = Decimal(diff).quantize(Decimal('0.1'), rounding=ROUND_DOWN)
        return profit, purchase_decimal, diff_decimal
