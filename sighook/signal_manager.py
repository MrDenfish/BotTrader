
from decimal import Decimal, ROUND_HALF_UP
from Config.config_manager import CentralConfig as config
from sighook.indicators import Indicators
from typing import Optional, Tuple, Dict, Any
import pandas as pd

class SignalManager:
    """
    Manages dynamic and static buy/sell signal thresholds, computes scoring,
    and evaluates TP/SL conditions based on trade history.
    """

    def __init__(self, logger, shared_utils_precision, trade_recorder):
        from Config.config_manager import CentralConfig  # Ensure CentralConfig is used
        from sighook.indicators import Indicators

        self.config = CentralConfig()
        self.logger = logger
        self.indicators = Indicators(logger)
        self.shared_utils_precision = shared_utils_precision
        self.trade_recorder = trade_recorder

        # ✅ TP/SL thresholds (ensure Decimal types)
        self.tp_threshold = Decimal(str(self.config.take_profit or 3.0))
        self.sl_threshold = Decimal(str(self.config.stop_loss or -2.0))

        # ✅ Buy/Sell Scoring Thresholds (as Decimals)
        self.roc_buy_threshold = Decimal(str(self.config.roc_buy_24h or 3.0))
        self.roc_sell_threshold = Decimal(str(self.config.roc_sell_24h or -2.0))
        self.rsi_buy = Decimal(str(self.config.rsi_buy or 30))
        self.rsi_sell = Decimal(str(self.config.rsi_sell or 70))
        self.buy_target = float(self.config.buy_ratio or 0.0)
        self.sell_target = float(self.config.sell_ratio or 0.0)

        # ✅ Strategy Weights
        self.strategy_weights = self.indicators.strategy_weights or {
            'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
            'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
            'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5,
            'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
        }

    # =========================================================
    # ✅ Core Buy/Sell Scoring
    # =========================================================
    def buy_sell_scoring(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        try:
            action = 'hold'
            last_row = ohlcv_df.iloc[-1]

            # ✅ ROC priority overrides
            roc_value = last_row.get('ROC', None)
            roc_diff_value = last_row.get('ROC_Diff', 0.0)
            rsi_value = last_row.get('RSI', None)

            if roc_value is not None:
                buy_signal_roc = (
                    roc_value > self.roc_buy_threshold and
                    abs(roc_diff_value) > 0.3 and
                    rsi_value is not None and rsi_value < self.rsi_buy
                )
                sell_signal_roc = (
                    roc_value < self.roc_sell_threshold and
                    abs(roc_diff_value) > 0.3 and
                    rsi_value is not None and rsi_value > self.rsi_sell
                )
                if buy_signal_roc:
                    return {
                        'action': 'buy',
                        'trigger': 'roc_buy',
                        'type': 'tp_sl',
                        'Buy Signal': (1, float(roc_value), float(self.roc_buy_threshold)),
                        'Sell Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }
                if sell_signal_roc:
                    return {
                        'action': 'sell',
                        'trigger': 'roc_sell',
                        'type': 'tp_sl',
                        'Sell Signal': (1, float(roc_value), float(self.roc_sell_threshold)),
                        'Buy Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }

            # ✅ Weighted scoring
            buy_score, sell_score = 0.0, 0.0
            for indicator, weight in self.strategy_weights.items():
                value = last_row.get(indicator)
                if isinstance(value, tuple) and len(value) == 3:
                    decision = int(value[0])
                    if indicator.startswith("Buy"):
                        buy_score += decision * weight
                    elif indicator.startswith("Sell"):
                        sell_score += decision * weight

            buy_signal = (
                (1, round(buy_score, 3), self.buy_target)
                if buy_score >= self.buy_target else (0, round(buy_score, 3), self.buy_target)
            )
            sell_signal = (
                (1, round(sell_score, 3), self.sell_target)
                if sell_score >= self.sell_target else (0, round(sell_score, 3), self.sell_target)
            )

            # ✅ Conflict resolution
            if buy_signal[0] == 1 and sell_signal[0] == 0:
                action = 'buy'
            elif sell_signal[0] == 1 and buy_signal[0] == 0:
                action = 'sell'
            elif buy_signal[0] == 1 and sell_signal[0] == 1:
                action = 'buy' if buy_score > sell_score else 'sell'

            return {
                'action': action,
                'trigger': 'score',
                'type': 'limit',
                'Buy Signal': buy_signal,
                'Sell Signal': sell_signal,
                'Score': {'Buy Score': buy_score, 'Sell Score': sell_score}
            }

        except Exception as e:
            self.logger.error(f"❌ Error in buy_sell_scoring() for {symbol}: {e}", exc_info=True)
            return {'action': None, 'trigger': None, 'Sell Signal': None, 'Buy Signal': (0, None, None),
                    'Score': {'Buy Score': None, 'Sell Score': None}}

    # =========================================================
    # ✅ TP/SL Evaluation
    # =========================================================
    async def evaluate_tp_sl_conditions(self, symbol: str, current_price: float) -> Optional[str]:
        try:
            trade_records = await self.fetch_trade_records_for_tp_sl(symbol)
            if not trade_records:
                return None

            avg_cost = sum(float(t["cost_basis_usd"]) for t in trade_records) / len(trade_records)
            if avg_cost != 0:
                profit_pct = ((current_price - avg_cost) / avg_cost) * 100
            else:
                return None

            if profit_pct >= float(self.tp_threshold):
                return 'profit'
            elif profit_pct <= float(self.sl_threshold):
                return 'loss'
            return None
        except Exception as e:
            self.logger.error(f"❌ Error evaluating TP/SL for {symbol}: {e}", exc_info=True)
            return None

    async def fetch_trade_records_for_tp_sl(self, symbol: str) -> list:
        try:
            trades = await self.trade_recorder.fetch_all_trades()
            return [
                {
                    "symbol": t.symbol,
                    "cost_basis_usd": float(t.cost_basis_usd or 0),
                    "remaining_size": float(t.remaining_size or 0)
                }
                for t in trades
                if t.symbol == symbol and t.remaining_size and t.remaining_size > 0
            ]
        except Exception as e:
            self.logger.error(f"❌ Error fetching trade records for TP/SL for {symbol}: {e}", exc_info=True)
            return []

    # =========================================================
    # ✅ Buy/Sell Matrix
    # =========================================================
    def update_indicator_matrix(self, asset: str, ohlcv_df: pd.DataFrame, buy_sell_matrix: pd.DataFrame):
        try:
            last_row = ohlcv_df.iloc[-1]
            for col in buy_sell_matrix.columns:
                if col in ohlcv_df.columns:
                    raw_tuple = last_row[col] if isinstance(last_row[col], tuple) else (0, 0.0, None)
                    decision = int(raw_tuple[0])
                    value = float(raw_tuple[1] or 0.0)
                    threshold = float(raw_tuple[2]) if raw_tuple[2] is not None else None
                    buy_sell_matrix.at[asset, col] = (decision, value, threshold)
        except Exception as e:
            self.logger.error(f"❌ Error updating buy_sell_matrix for {asset}: {e}", exc_info=True)

    def evaluate_signals(self, asset: str, buy_sell_matrix: pd.DataFrame) -> Tuple[Tuple[int, float, float], Tuple[int, float, float]]:
        try:
            row = buy_sell_matrix.loc[asset]
            buy_score = sum(row[ind][0] * self.strategy_weights.get(ind, 1.0)
                            for ind in row.index if ind.startswith("Buy"))
            sell_score = sum(row[ind][0] * self.strategy_weights.get(ind, 1.0)
                             for ind in row.index if ind.startswith("Sell"))

            buy_signal = (1, round(buy_score, 3), self.buy_target) if buy_score >= self.buy_target else (0, round(buy_score, 3), self.buy_target)
            sell_signal = (1, round(sell_score, 3), self.sell_target) if sell_score >= self.sell_target else (0, round(sell_score, 3), self.sell_target)
            return buy_signal, sell_signal
        except Exception as e:
            self.logger.error(f"❌ Error evaluating matrix signals for {asset}: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)



