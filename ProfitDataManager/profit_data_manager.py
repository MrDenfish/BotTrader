
from decimal import Decimal
from inspect import stack  # debugging

import pandas as pd

from Config.config_manager import CentralConfig as config
from webhook.webhook_validate_orders import OrderData


class ProfitDataManager:
    _instance = None
    @classmethod
    def get_instance(cls, shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager):
        """
        Singleton method to ensure only one instance of ProfitDataManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager)
        return cls._instance

    def __init__(self, shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager):
        self.config = config()
        self._hodl = self.config.hodl
        self._stop_loss = Decimal(self.config.stop_loss)
        self._take_profit = Decimal(self.config.take_profit)
        self.market_cache = None
        self.last_ticker_update = None
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.shared_data_manager = shared_data_manager
        self.shared_utils_print_data = shared_utils_print_data
        self.shared_utils_precision = shared_utils_precision
        self.start_time = None

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def market_cache_usd(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def current_prices(self):
        return self.shared_data_manager.market_data.get('current_prices')

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get("order_tracker")


    @property
    def avg_quote_volume(self):
        return Decimal(self.shared_data_manager.market_data['avg_quote_volume'])

    @property
    def hodl(self):
        return self._hodl

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit


    async def calculate_profitability(self, symbol, required_prices, current_prices, usd_pairs):
        """
        Calculate profitability for a given asset using current balances and market prices.
        Returns a consolidated profit data dictionary.
        """
        try:
            # ✅ Normalize Symbol Format
            ticker = symbol.replace('-', '/') if '-' in symbol else symbol
            if '/' not in ticker:
                ticker = f"{ticker}/USD"
            asset = ticker.split('/')[0]

            # ✅ Get Market Price
            current_price = Decimal(current_prices.get(ticker, 0))
            if current_price == 0 and ticker not in ['USD', 'USDC']:
                print(f"Current price for {ticker} not available.")
                profit_data = {
                    'asset': asset,
                    'balance': round(Decimal(required_prices.get('asset_balance', 0)), 2),
                    'price': round(current_price, 2),
                    'value': 1,
                    'cost_basis': 1,
                    'avg_price': 1,
                    'profit': 1,
                    'profit percent': f'0%',
                    'status': 'na'
                }
                return profit_data

            # ✅ Fetch Precision Once
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(ticker)



            # ✅ Convert Values Once
            asset_balance = Decimal(required_prices.get('asset_balance', 0))
            avg_price = Decimal(required_prices.get('avg_price', 0))
            cost_basis = Decimal(required_prices.get('cost_basis', 0))

            # ✅ Guard Against Cost Basis Errors
            per_unit_cost_basis = cost_basis / asset_balance if asset_balance > 0 else Decimal(0)

            # ✅ Calculate Profit
            current_value = asset_balance * current_price
            profit = current_value - cost_basis
            profit_percentage = (profit / cost_basis) * 100 if cost_basis > 0 else Decimal(0)

            # ✅ Rounding to Precision
            profit_percentage = round(profit_percentage, 4)
            current_value = round(current_value, quote_deci)
            profit = round(profit, quote_deci)

            # ✅ Construct Profit Data
            profit_data = {
                'asset': asset,
                'balance': round(asset_balance, base_deci),
                'price': round(current_price, quote_deci),
                'value': current_value,
                'cost_basis': round(cost_basis, quote_deci),
                'avg_price': round(avg_price, quote_deci),
                'profit': profit,
                'profit percent': f'{profit_percentage}%',
                'status': required_prices.get('status', 'HDLG')
            }

            return profit_data

        except Exception as e:
            self.logger.error(f"❌ Error calculating profitability for {symbol}: {e}", exc_info=True)
            return None

    def consolidate_profit_data(self, profit_data_list):
        """
        Converts a list of profit data dictionaries into a structured DataFrame.
        """
        try:
            if not profit_data_list:
                print("No profitability data available.")
                return None

            # Convert list of dictionaries to DataFrame
            profit_df = pd.DataFrame(profit_data_list)

            # Sort by profit percentage
            profit_df = profit_df.sort_values(by=['profit percent'], ascending=False)
            # Set Asset as index for cleaner display
            profit_df.set_index('asset')
            caller_function_name = stack()[0].function  # debugging
            # Print DataFrame


            return profit_df

        except Exception as e:
            print(f"❌ Error consolidating profit data: {e}")
            return None

    async def calculate_tp_sl(self, order_data: OrderData):
        """
        Calculate Take Profit (TP) and Stop Loss (SL) prices with proper precision.

        Uses Option A strategy: applies fee after calculating price target,
        avoiding over-tight stop losses.

        Args:
            order_data (OrderData): Contains order parameters like adjusted_price, fee, and precision info.

        Returns:
            tuple: (adjusted_take_profit, adjusted_stop_loss)
        """
        try:
            # Base price to start from
            entry_price = order_data.adjusted_price
            fee = order_data.taker

            # --- Take Profit ---
            # Target a % above entry price, then account for fee
            tp = entry_price * (Decimal("1.0") + self.take_profit)
            tp += tp * fee  # Add fee on top

            # --- Stop Loss ---
            # Target a % below entry price, then account for fee
            sl = entry_price * (Decimal("1.0") + self.stop_loss)  # stop_loss is negative by default
            sl -= sl * fee  # Deduct fee (optionally leave as sl = sl if you want to avoid extra tightening)

            # Round to appropriate precision
            adjusted_tp = self.shared_utils_precision.adjust_precision(
                order_data.base_decimal,
                order_data.quote_decimal,
                tp,
                convert='quote'
            )
            adjusted_sl = self.shared_utils_precision.adjust_precision(
                order_data.base_decimal,
                order_data.quote_decimal,
                sl,
                convert='quote'
            )

            return adjusted_tp, adjusted_sl

        except Exception as e:
            self.logger.error(f"❌️ Error in calculate_tp_sl: {e}", exc_info=True)
            return None, None

    def should_place_sell_order(self, holding, current_price):
        """ PART VI: Profitability Analysis and Order Generation used in Sighook operates directly on a holding object (an instance from
        the Holdings table) and the current_market_price,
        making decisions based on the latest available data.  unrealized profit and its percentage are calculated
        dynamically within the function, ensuring decisions are based on real-time data."""
        try:
            if not holding or not current_price:
                return False
            unrealized_profit_pct = holding.get('unrealized_profit_pct', 0)

            # Decide to sell based on the calculated unrealized profit percentage
            return unrealized_profit_pct > self._take_profit or unrealized_profit_pct < self._stop_loss
        except Exception as e:
            self.logger.error(f"❌ Error in should_place_sell_order: {e}", exc_info=True)
            return False
