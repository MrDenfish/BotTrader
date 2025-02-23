
from decimal import Decimal
from Shared_Utils.config_manager import CentralConfig as config
import pandas as pd
from inspect import stack # debugging


class ProfitDataManager:
    _instance = None
    @classmethod
    def get_instance(cls, shared_utils_precision, shared_utils_print_data, log_manager):
        """
        Singleton method to ensure only one instance of ProfitDataManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(shared_utils_precision, shared_utils_print_data, log_manager)
        return cls._instance

    def __init__(self, shared_utils_precision, shared_utils_print_data, log_manager):
        self.config = config()
        self._hodl = self.config.hodl
        self.ticker_cache = None
        self.market_cache = None
        self.min_volume = None
        self.last_ticker_update = None
        self.log_manager = log_manager
        self.shared_utils_print_data = shared_utils_print_data
        self.shared_utils_precision = shared_utils_precision
        self.start_time = None


    @property
    def hodl(self):
        return self._hodl

    def set_trade_parameters(self, market_data, order_management, start_time=None):
        try:
            self.start_time = start_time
            # Safely access keys in market_data
            self.ticker_cache = market_data.get('ticker_cache', None)
            self.non_zero_balances = order_management.get('non_zero_balances', {})
            self.order_tracker = order_management.get('order_tracker', {})
            self.current_prices = market_data.get('current_prices', {})
            self.market_cache_vol = market_data.get('filtered_vol', None)

            avg_quote_volume = market_data.get('avg_quote_volume', None)
            self.min_volume = Decimal(avg_quote_volume) if avg_quote_volume else Decimal('0')

            if self.ticker_cache.empty: # list is empty
                self.log_manager.warning("Ticker cache is empty. Defaulting to empty list.")
            if not self.market_cache_vol: # list is empty
                self.log_manager.warning("Market cache volume is empty. Defaulting to empty list.")
            if not avg_quote_volume:
                self.log_manager.warning("Average quote volume is missing. Defaulting to 0.")

            self.log_manager.info("Trade parameters set successfully.")

        except Exception as e:
            self.log_manager.error(f"Error setting trade parameters: {e}", exc_info=True)
            raise

    async def _calculate_profitability(self, symbol, required_prices, current_prices, usd_pairs):
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
                return None

            # ✅ Fetch Precision Once
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(ticker, usd_pairs)

            # ✅ Convert Values Once
            balance = Decimal(required_prices.get('balance', 0))
            avg_price = Decimal(required_prices.get('avg_price', 0))
            cost_basis = Decimal(required_prices.get('cost_basis', 0))

            # ✅ Guard Against Cost Basis Errors
            per_unit_cost_basis = cost_basis / balance if balance > 0 else Decimal(0)

            # ✅ Calculate Profit
            current_value = balance * current_price
            profit = current_value - cost_basis
            profit_percentage = (profit / cost_basis) * 100 if cost_basis > 0 else Decimal(0)

            # ✅ Rounding to Precision
            profit_percentage = round(profit_percentage, 4)
            current_value = round(current_value, quote_deci)
            profit = round(profit, quote_deci)

            # ✅ Construct Profit Data
            profit_data = {
                'asset': asset,
                '   balance': round(balance, base_deci),
                '   price': round(current_price, quote_deci),
                '   value': current_value,
                'cost_basis': round(cost_basis, quote_deci),
                'avg_price': round(avg_price, quote_deci),
                'profit': profit,
                '   profit percent': f'{profit_percentage}%',
                'status': required_prices.get('status', 'HDLG')
            }

            return profit_data

        except Exception as e:
            self.log_manager.error(f"Error calculating profitability for {symbol}: {e}", exc_info=True)
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
            profit_df = profit_df.sort_values(by=['   profit percent'], ascending=False)
            # Set Asset as index for cleaner display
            profit_df.set_index('asset')
            caller_function_name = stack()[0].function  # debugging
            # Print DataFrame


            return profit_df

        except Exception as e:
            print(f"Error consolidating profit data: {e}")
            return None

