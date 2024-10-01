
from decimal import Decimal


class ProfitabilityManager:
    def __init__(self, exchange, ccxt_api, utility, portfolio_manager, database_session_mngr, database_ops_mngr,
                 order_manager, trading_strategy, profit_helper, profit_extras, logmanager, app_config):

        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(app_config.take_profit)
        self._stop_loss = Decimal(app_config.stop_loss)
        self.database_dir = app_config.get_database_dir
        self.ledger_cache = None
        self.utility = utility
        self.database_manager = database_session_mngr
        self.database_ops = database_ops_mngr
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.trading_strategy = trading_strategy
        self.profit_helper = profit_helper
        self.profit_extras = profit_extras
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    async def check_profit_level(self, holding_list, holdings_df, current_prices, open_orders):  # async
        """PART VI: Profitability Analysis and Order Generation """
        # await self.database_session_mngr.process_holding_db(holdings, self.start_time)
        try:
            # Update and process holdings
            aggregated_df = await self.update_and_process_holdings(holding_list, holdings_df, current_prices, open_orders)
            profit_data = await self.database_manager.create_performance_snapshot()  # Create a snapshot
            # of current portfolio performance

            return aggregated_df, profit_data
        except Exception as e:
            self.log_manager.error(f'check_profit_level: {e} e', exc_info=True)

    async def update_and_process_holdings(self, holding_list, holdings_df, current_prices, open_orders):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Load or update holdings

            aggregated_df = await self.database_manager.process_holding_db(holding_list, holdings_df, current_prices,
                                                                           open_orders)
            holdings_df = await self.portfolio_manager.fetch_wallets()
            updated_holdings_df = await self.profit_helper.calculate_unrealized_profit_loss(aggregated_df)
            merged_df = updated_holdings_df.merge(holdings_df[['asset', 'total']], on='asset', how='left')
            await self.check_and_execute_sell_orders(merged_df, current_prices, open_orders)  # await

            # # # Fetch new trades for all currencies in holdings

            #  need to process further
            # symbols = updated_holdings_df['symbol'].tolist()
            # all_new_trades = await self.database_manager.fetch_new_trades_for_symbols(symbols)  # await

        except Exception as e:
            self.log_manager.error(f'update_and_process_holdings: {e}', exc_info=True)

    async def check_and_execute_sell_orders(self, updated_holdings_df, current_prices, open_orders):
        """PART VI: Profitability Analysis and Order Generation"""
        try:
            realized_profit = 0
            sell_orders = []

            updated_holdings_list = updated_holdings_df.to_dict('records')  # Convert DataFrame to list of dictionaries

            for holding in updated_holdings_list:
                asset = holding['symbol'].split('/')[0]
                current_market_price = holding['current_price']
                if self.profit_helper.should_place_sell_order(holding, current_market_price):
                    sell_amount = holding['balance']
                    sell_price = Decimal(current_market_price)
                    sell_orders.append((asset, sell_amount, sell_price, holding))

                    trigger = 'profit' if realized_profit > 0 else 'loss'
                    order = {
                        'asset': holding['asset'],
                        'symbol': holding['symbol'],
                        'action': 'sell',
                        'price': sell_price,
                        'trigger': trigger,
                        'bollinger_df': None,  # If applicable
                        'action_data': {
                            'action': 'sell',
                            'trigger': trigger,
                            'updates': {
                                holding['quote']: {
                                    'Sell Signal': trigger
                                }
                            },
                            'sell_cond': trigger
                        },
                        'value': holding['total'] * sell_price # Calculate the value of the order
                    }

                    # Here, handle_actions needs to accept order and holdings_list
                    await self.order_manager.handle_actions(order, updated_holdings_list)
                    # Process all sell orders in a single operation
            if sell_orders:
                realized_profit = await self.database_manager.process_sell_orders_fifo(self.market_cache, sell_orders,
                                                                                       updated_holdings_list,
                                                                                       updated_holdings_df, current_prices)

            if updated_holdings_list:
                await self.database_manager.batch_update_holdings(updated_holdings_list, current_prices, open_orders)

            return realized_profit
        except Exception as e:
            self.log_manager.error(f'check_and_execute_sell_orders:  {e}', exc_info=True)
            raise



