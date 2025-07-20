from decimal import Decimal, ROUND_DOWN, getcontext, InvalidOperation

import pandas as pd


class HoldingsProcessor:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, profit_data_manager, shared_utils_precision, shared_data_manager, *args, **kwargs):
        """ Ensures only one instance of HoldingsProcessor is created. """
        if cls._instance is None:
            cls._instance = cls(logger_manager, profit_data_manager, shared_utils_precision, shared_data_manager, *args, **kwargs)
        return cls._instance

    def __init__(self, logger_manager, profit_data_manager, shared_utils_precision, shared_data_manager, *args, **kwargs):
        """ Initialize HoldingsProcessor. """
        if HoldingsProcessor._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.logger = logger_manager  # 🙂
        self.profit_data_manager = profit_data_manager
        self.shared_utils_precision = shared_utils_precision
        self.shared_data_manager = shared_data_manager
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
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def filtered_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances')

    @property
    def precision(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def market_cache_prices(self):
        return self.shared_data_manager.market_data.get('filtered_prices')


    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def holdings_list(self):
        return self.shared_data_manager.market_data.get('spot_positions')

    def _truncate_decimal(self, value, decimal_places=8):
        """
        Truncates a Decimal value to a maximum number of decimal places.
        Handles string values that may include a percentage sign '%'.
        """
        try:
            # Set decimal context precision
            getcontext().prec = 28  # set precision to default value 28
            getcontext().traps[InvalidOperation] = False

            # Handle string inputs
            if isinstance(value, str):
                value = value.strip().replace('%', '')
                value = Decimal(value)

            # Convert float to Decimal via string to preserve precision
            if isinstance(value, float):
                value = self.shared_utils_precision.safe_convert(value, decimal_places)

            # Ensure value is a Decimal
            if not isinstance(value, Decimal):
                value = Decimal(value)

            # Quantize to the specified number of decimal places
            quantize_str = '1.' + '0' * decimal_places
            return value.quantize(Decimal(quantize_str), rounding=ROUND_DOWN)

        except Exception as e:
            self.logger.error(f"Error truncating decimal value {value}: {e}", exc_info=True)
            return Decimal('0')

    async def _calculate_derived_metrics(self, holding, processed_pairs, trailing_stop_orders):
        """Calculate derived metrics for a single holding using calculate_profitability()."""
        try:

            asset = holding['asset']
            symbol = asset + "/USD"
            base_deci,quote_deci,_,_ = self.shared_utils_precision.fetch_precision(symbol)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)
            base_quantizer = Decimal("1").scaleb(-base_deci)
            price = Decimal(self.market_data.get('bid_ask_spread', {}).get(symbol))
            price = self.shared_utils_precision.safe_quantize(price, quote_quantizer)
            asset_balance = self._truncate_decimal(holding['total_balance_crypto'])

            pair_data = processed_pairs.get(asset, {})
            cost_basis = self._truncate_decimal(pair_data.get('cost_basis', 0))

            # Prepare required prices dictionary
            required_prices = {
                'avg_price': self._truncate_decimal(pair_data.get('average_price', 0)),
                'cost_basis': cost_basis,
                'asset_balance': asset_balance,
                'current_price': price,
                'profit': None,
                'profit_percentage': None,
                'status_of_order': None
            }

            # Calculate profitability
            if asset == 'USD':
                pass
            profitability = await self.profit_data_manager.calculate_profitability(symbol, required_prices,
                                                                            self.bid_ask_spread, self.usd_pairs)

            trailing_stop = (
                trailing_stop_orders[trailing_stop_orders['product_id'] == holding['symbol']]
                .to_dict(orient='records') if not trailing_stop_orders.empty else None
            )

            return {
                'symbol': holding['asset']+"/USD",#✅
                'quote': price,
                'asset': asset,#✅
                'amount': self._truncate_decimal(holding['available_to_trade_crypto']),#✅
                'current_price': price,#✅
                'weighted_average_price': required_prices['avg_price'],
                'initial_investment': cost_basis,
                'unrealized_profit_loss': self._truncate_decimal(profitability.get('profit', 0)),#✅
                'unrealized_profit_pct': self._truncate_decimal(profitability.get('profit percent', 0)) / 100,  # ✅
                'current_value': self._truncate_decimal(asset_balance * price),
            }
        except Exception as e:
            self.logger.error(f"Error calculating derived metrics for holding {holding}: {e}", exc_info=True)
            return {}

    async def process_holdings(self, open_orders):
        """Processes holdings data and returns an aggregated DataFrame."""
        try:
            self.logger.info("Processing holdings data...")

            # Prepare trailing stop orders
            trailing_stop_orders = (
                open_orders[open_orders['type'] == 'STOP_PENDING']
                if open_orders is not None and not open_orders.empty else pd.DataFrame()
            )

            # Pre-process filtered_pairs for easier lookup
            processed_pairs = {
                asset: {
                    'average_price': Decimal(data['average_entry_price']['value']),
                    'cost_basis': Decimal(data['cost_basis']['value']),
                    'unrealized_pnl': Decimal(data['unrealized_pnl'])
                }
                for asset, data in self.filtered_balances.items()
            }

            # Generate aggregated data
            aggregated_data = [
                await self._calculate_derived_metrics(holding, processed_pairs, trailing_stop_orders)
                for holding in self.filtered_balances.values()
                if holding['asset'] != 'USD'
                   and holding['asset'] +'/USD' in self.market_data.get('bid_ask_spread', {})
            ]

            aggregated_df = pd.DataFrame(aggregated_data)

            # Ensure expected columns are present
            expected_columns = [
                'symbol', 'quote', 'asset', 'amount', 'current_price',
                'weighted_average_price', 'initial_investment', 'unrealized_profit_loss',
                'unrealized_profit_pct', 'current_value'
            ]
            aggregated_df = aggregated_df.reindex(columns=expected_columns)

            return aggregated_df

        except Exception as e:
            self.logger.error(f"❌Failed to process holdings data: {e}", exc_info=True)
            raise

