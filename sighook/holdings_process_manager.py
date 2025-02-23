
from decimal import ROUND_DOWN
from decimal import Decimal
from Shared_Utils.precision import PrecisionUtils
import pandas as pd

class HoldingsProcessor:
    _instance = None

    @classmethod
    def get_instance(cls, log_manager, profit_data_manager, *args, **kwargs):
        """ Ensures only one instance of HoldingsProcessor is created. """
        if cls._instance is None:
            cls._instance = cls(log_manager, profit_data_manager, *args, **kwargs)
        return cls._instance

    def __init__(self, log_manager, profit_data_manager, *args, **kwargs):
        """ Initialize HoldingsProcessor. """
        if HoldingsProcessor._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.log_manager = log_manager
        self.profit_data_manager = profit_data_manager
        self.shared_utils_precision = PrecisionUtils.get_instance(log_manager)

        self.start_time = self.market_data = self.ticker_cache = self.current_prices = None
        self.usd_pairs = self.market_cache_vol = self.filtered_balances = self.holdings_list = None



    def set_trade_parameters(self, start_time, market_data, order_management):
        self.start_time = start_time
        self.market_data = market_data
        self.ticker_cache = market_data['ticker_cache']
        self.current_prices = market_data['current_prices']
        self.usd_pairs = market_data.get('usd_pairs_cache', {})  # usd pairs
        self.market_cache_vol = market_data['filtered_vol']  # usd pairs with min volume
        self.filtered_balances = order_management['non_zero_balances']
        self.holdings_list = market_data['spot_positions']


    def _truncate_decimal(self, value, decimal_places=8):
        """Truncates a Decimal value to a maximum number of decimal places.
        Handles string values that may include a percentage sign '%'.
        """
        if isinstance(value, str):
            value = value.strip().replace('%', '')  # Remove '%' if present
            try:
                value = Decimal(value)
            except ValueError:
                raise ValueError(f"Invalid numeric value: {value}")

        if not isinstance(value, Decimal):
            value = Decimal(value)

        return value.quantize(Decimal(f'1.{"0" * decimal_places}'), rounding=ROUND_DOWN)

    async def _calculate_derived_metrics(self, holding, processed_pairs, trailing_stop_orders):
        """Calculate derived metrics for a single holding using _calculate_profitability()."""
        asset = holding['asset']
        balance = self._truncate_decimal(holding['total'])
        price = self._truncate_decimal(holding['price'])

        pair_data = processed_pairs.get(asset, {})
        cost_basis = self._truncate_decimal(pair_data.get('cost_basis', 0))

        # Prepare required prices dictionary
        required_prices = {
            'avg_price': self._truncate_decimal(pair_data.get('average_price', 0)),
            'cost_basis': cost_basis,
            'balance': balance,
            'current_price': price,
            'profit': None,
            'profit_percentage': None,
        }

        # Calculate profitability
        profitability = await self.profit_data_manager._calculate_profitability(asset, required_prices,
                                                                                self.current_prices, self.usd_pairs)

        trailing_stop = (
            trailing_stop_orders[trailing_stop_orders['product_id'] == holding['symbol']]
            .to_dict(orient='records') if not trailing_stop_orders.empty else None
        )

        return {
            'symbol': holding['symbol'],#✅
            'quote': holding['quote'],
            'asset': asset,#✅
            'amount': self._truncate_decimal(holding['free']),#✅
            'current_price': price,#✅
            'weighted_average_price': required_prices['avg_price'],
            'initial_investment': cost_basis,
            'unrealized_profit_loss': self._truncate_decimal(profitability.get('profit', 0)),#✅
            'unrealized_profit_pct': self._truncate_decimal(profitability.get('   profit percent', 0))/100,#✅
            'trailing_stop': trailing_stop,
            'current_value': self._truncate_decimal(balance * price),
        }

    async def process_holdings(self, open_orders, holdings_list):
        """Processes holdings data and returns an aggregated DataFrame."""
        try:
            self.log_manager.info("Processing holdings data...")

            # Prepare trailing stop orders
            trailing_stop_orders = (
                open_orders[open_orders['trigger_status'] == 'STOP_PENDING']
                if open_orders is not None and not open_orders.empty else pd.DataFrame()
            )

            # Pre-process filtered_pairs for easier lookup
            processed_pairs = {
                asset: {
                    'average_price': Decimal(data.get('average_entry_price', {}).get('value', 0)),
                    'cost_basis': Decimal(data.get('cost_basis', {}).get('value', 0)),
                    'unrealized_pnl': Decimal(data.get('unrealized_pnl', 0))
                }
                for asset, data in self.holdings_list.items()
            }

            # Generate aggregated data
            aggregated_data = [
                await self._calculate_derived_metrics(holding, processed_pairs, trailing_stop_orders)
                for holding in holdings_list
            ]
            aggregated_df = pd.DataFrame(aggregated_data)

            # Ensure expected columns are present
            expected_columns = [
                'symbol', 'quote', 'asset', 'amount', 'current_price',
                'weighted_average_price', 'initial_investment', 'unrealized_profit_loss',
                'unrealized_profit_pct', 'trailing_stop', 'current_value'
            ]
            aggregated_df = aggregated_df.reindex(columns=expected_columns)

            return aggregated_df

        except Exception as e:
            self.log_manager.error(f"Failed to process holdings data: {e}", exc_info=True)
            raise

