
from decimal import Decimal

from Config.config_manager import CentralConfig as Config


class ProfitabilityManager:
    _instance = None

    @classmethod
    def get_instance(cls, exchange, ccxt_api, portfolio_manager, holdings_processor, database_ops_mngr,
                     order_manager, trading_strategy, profit_data_manager, shared_data_manager, web_url, logger_manager):
        if cls._instance is None:
            cls._instance = cls(exchange, ccxt_api, portfolio_manager, holdings_processor, database_ops_mngr,
                                order_manager, trading_strategy, profit_data_manager, shared_data_manager, web_url, logger_manager)
        return cls._instance

    def __init__(self, exchange, ccxt_api, portfolio_manager, holdings_processor, database_ops_mngr,
                 order_manager, trading_strategy, profit_data_manager, shared_data_manager, web_url, logger_manager):
        self.config = Config()
        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._hodl = self.config.hodl
        self.database_dir = self.config.get_database_dir
        self.holdings_processor = holdings_processor
        self.database_ops = database_ops_mngr
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.shared_data_manager = shared_data_manager
        self.trading_strategy = trading_strategy
        self.profit_data_manager = profit_data_manager
        self.logger = logger_manager

        self.web_url = web_url

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache')

    @property
    def current_prices(self):
        return self.market_data.get('current_prices')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def hodl(self):
        return self._hodl

    async def update_and_process_holdings(self, start_time, open_orders):
        """PART VI:
        Analyze profitability and place sell orders using calculate_profitability."""
        try:
            # Process holdings and calculate profitability
            aggregated_df = await self.holdings_processor.process_holdings(open_orders)
            # Evaluate and execute sell orders
            await self.check_and_execute_sell_orders(start_time, aggregated_df, open_orders)

            return aggregated_df #updated_holdings_df
        except Exception as e:
            self.logger.error(f"❌ update_and_process_holdings: {e}", exc_info=True)
            raise

    async def check_and_execute_sell_orders(self, start_time, updated_holdings_df, open_orders):
        """
        PART VI: Profitability Analysis and Order Generation
        Evaluate holdings to determine if sell orders should be placed.
        """
        try:
            sell_orders = []
            updated_holdings_list = updated_holdings_df.to_dict('records')  # Convert DataFrame to list of dictionaries
            trigger = None


            for holding in updated_holdings_list:
                # Skip assets marked as "hodl"
                if holding['asset'] in self.hodl:
                    continue

                # Extract asset and current market price
                asset = holding['asset']
                current_market_price = Decimal(self.current_prices.get(holding['symbol'], 0))

                # Determine if a sell order should be placed
                if self.profit_data_manager.should_place_sell_order(holding, current_market_price):
                    trigger = 'profit'
                    sell_order = self.create_sell_order(holding, trigger, current_market_price)
                    sell_orders.append(sell_order)

            # Execute sell orders using OrderManager's handle_actions()
            if sell_orders:
                await self.order_manager.execute_actions(sell_orders, updated_holdings_list)

        except Exception as e:
            self.logger.error(f'❌ check_and_execute_sell_orders: {e}', exc_info=True)
            raise

    @staticmethod
    def create_sell_order(holding, trigger, current_market_price):
        """
        Create a standardized sell order dictionary for a given holding.
        Compatible with webhook payload structure.
        """
        unrealized_profit = holding['unrealized_profit_loss']
        type = 'tp_sl' if unrealized_profit > 0 else 'limit'

        return {
            'asset': holding['asset'],
            'symbol': holding['symbol'],
            'action': 'sell',
            'type': type,
            'price': current_market_price,
            'trigger': trigger,
            'score': None,  # strategy orders may provide this
            'volume': holding['amount'],  # Selling the entire balance
            'sell_cond': trigger,
            'value': Decimal(holding['current_value'])
        }




