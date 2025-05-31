
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Dict, Any
import pandas as pd

STRATEGY_WEIGHTS = {
        'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
        'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2, 'Sell Ratio': 1.2,
        'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5, 'Sell ROC': 2.0,
        'Sell MACD': 1.8, 'Sell Swing': 2.2
    }
class SignalManager:
    """
    Manages dynamic and static buy/sell signal thresholds and computes signal scoring.
    """

    def __init__(self, logger, shared_utils_precision, buy_target: float = 7.0, sell_target: float = 7.0):
        self.logger = logger
        self.shared_utils_precision= shared_utils_precision
        self.strategy_weights = STRATEGY_WEIGHTS
        if self.strategy_weights:
            self.update_targets(self.strategy_weights)
        else:
            self._buy_target = self.shared_utils_precision.safe_convert(buy_target, 1)
            self._sell_target = self.shared_utils_precision.safe_convert(sell_target, 1)

    def update_targets(self, strategy_weights: dict):
        """
        Dynamically calculate new buy/sell targets based on strategy weights.
        """
        try:
            total_buy_weight = sum(weight for key, weight in strategy_weights.items() if key.startswith("Buy"))
            total_sell_weight = sum(weight for key, weight in strategy_weights.items() if key.startswith("Sell"))

            self._buy_target = (self.shared_utils_precision.safe_convert(total_buy_weight, 1) * Decimal("0.7")).quantize(Decimal('0.01'),
                                                                                          rounding=ROUND_HALF_UP)
            self._sell_target = (self.shared_utils_precision.safe_convert(total_sell_weight, 1) * Decimal("0.7")).quantize(Decimal('0.01'),
                                                                                            rounding=ROUND_HALF_UP)

        except Exception as e:
            self.logger.error(f"❌ Error updating targets in SignalManager: {e}", exc_info=True)

    def buy_sell_scoring(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        Full buy/sell scoring logic including ROC priority overrides.
        """
        try:
            action = 'hold'
            last_ohlcv = ohlcv_df.iloc[-1]

            # Extract critical indicators
            roc_value = last_ohlcv.get('ROC', None)
            roc_diff_value = last_ohlcv.get('ROC_Diff', 0.0)
            rsi_value = last_ohlcv.get('RSI', None)

            # ✅ ROC-based priority (overrides scoring logic)
            if roc_value is not None:
                buy_signal_roc = roc_value > 5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value < 30
                sell_signal_roc = roc_value < -2.5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value > 70

                if buy_signal_roc:
                    return {
                        'action': 'buy',
                        'trigger': 'roc_buy',
                        'type': 'limit',
                        'Buy Signal': (1, float(roc_value), 5),
                        'Sell Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }

                if sell_signal_roc:
                    return {
                        'action': 'sell',
                        'trigger': 'roc_sell',
                        'type': 'limit',
                        'Sell Signal': (1, float(roc_value), -2.5),
                        'Buy Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }

            # ✅ Score-based evaluation
            buy_score = 0.0
            sell_score = 0.0

            for indicator, weight in self.strategy_weights.items():
                value = last_ohlcv.get(indicator)
                if isinstance(value, tuple) and len(value) == 3:
                    decision = value[0]
                    buy_score += decision * weight if indicator.startswith("Buy") else 0.0
                    sell_score += decision * weight if indicator.startswith("Sell") else 0.0

            buy_signal = (1, round(buy_score, 3), float(self._buy_target)) if buy_score >= self._buy_target else (0, round(buy_score, 3), float(self._buy_target))
            sell_signal = (1, round(sell_score, 3), float(self._sell_target)) if sell_score >= self._sell_target else (0, round(sell_score, 3), float(self._sell_target))

            # Resolve conflicts
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
            self.logger.error(f"❌ Error in buy_sell_scoring() for {symbol}: {e}",exc_info=True)
            return {
                'action': None,
                'trigger': None,
                'Sell Signal': None,
                'Buy Signal': (0, None, None),
                'Score': {'Buy Score': None, 'Sell Score': None}
            }

    def compute_signals(self, logger, buy_target, sell_target, buy_score, sell_score):
        """Determine final buy/sell signals based on weighted scores."""
        try:
            # Debugging logs
            logger.debug(f"Buy Score: {buy_score}, Buy Target: {buy_target}")
            logger.debug(f"Sell Score: {sell_score}, Sell Target: {sell_target}")

            buy_signal = (1, buy_score, buy_target) if buy_score >= buy_target else (0, buy_score, buy_target)
            sell_signal = (1, sell_score, sell_target) if sell_score >= sell_target else (0, sell_score, sell_target)
            # print(f'buy_signal:{buy_signal}') #debug
            # print(f'sell_signal:{sell_signal}') #debug
            # Resolve conflicting signals (if both are active)
            if buy_signal[0] == 1 and sell_signal[0] == 1:
                if buy_score > sell_score:
                    sell_signal = (0, sell_score, sell_target)
                else:
                    buy_signal = (0, buy_score, buy_target)
                # print(f'buy_signal:{buy_signal}') #debug
                # print(f'sell_signal:{sell_signal}') #debug
            return buy_signal, sell_signal

        except Exception as e:
            logger.error(f"❌ Error computing final buy/sell signals: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)

    def evaluate_signals(self, asset: str, matrix: pd.DataFrame) -> Tuple[
        Tuple[int, float, float], Tuple[int, float, float]]:
        """
        Update dynamic thresholds, compute weighted scores, and return buy/sell signals for a given asset.
        """
        try:
            self.update_dynamic_targets(asset, matrix)
            buy_score, sell_score = self.compute_weighted_scores(asset, matrix)
            buy_signal, sell_signal = self.compute_signals(self.logger, self.buy_target, self.sell_target,
                                                           buy_score, sell_score)

            return buy_signal, sell_signal
        except Exception as e:
            self.logger.error(f"❌ Error in evaluate_signals for {asset}: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)

    def update_dynamic_targets(self, asset: str, buy_sell_matrix: pd.DataFrame):
        """Update dynamic buy & sell targets based on strategy weights."""
        try:
            def safe_float(val):
                """Ensure the threshold is a valid float or default to 0."""
                try:
                    return float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            total_buy_weight = sum(self.strategy_weights[col] for col in self.strategy_weights if col.startswith("Buy"))
            # print(f'{total_buy_weight}') #debug
            total_sell_weight = sum(self.strategy_weights[col] for col in self.strategy_weights if col.startswith("Sell"))
            # print(f'{total_sell_weight}') #debug

            self.buy_target = total_buy_weight * 0.7  # Adjusted threshold
            self.sell_target = total_sell_weight * 0.7  # Adjusted threshold

            self.logger.debug(f"Dynamic Buy Target for {asset}: {self.buy_target}")
            self.logger.debug(f"Dynamic Sell Target for {asset}: {self.sell_target}")


        except Exception as e:
            self.logger.error(f"❌ Error updating dynamic targets: {e}", exc_info=True)

    def compute_weighted_scores(self, asset, buy_sell_matrix):
        """Compute weighted buy and sell scores based on strategy decisions and weights."""
        try:

            # **✅ Print the buy/sell matrix values for debugging**
            # print(f"\n� DEBUG - Asset: {asset}")
            # for col, value in buy_sell_matrix.loc[asset].items():
            #     if col.startswith("Buy") or col.startswith("Sell"):
            #         print(f"{col}: {value}")  # Check raw values

            # ✅ Calculate buy score
            buy_score = sum(
                min(value[0] * self.strategy_weights[col], 10)  # Cap max contribution to 10 per indicator
                if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Buy") and col in self.strategy_weights
            )

            # ✅ Calculate sell score
            sell_score = sum(
                (value[0] * self.strategy_weights[col]) if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Sell") and col in self.strategy_weights
            )

            return buy_score, sell_score
        except Exception as e:
            self.logger.error(f"❌ Error computing weighted scores: {e}", exc_info=True)
            return 0, 0

    def __str__(self):
        return f"SignalManager(buy_target={self._buy_target}, sell_target={self._sell_target})"

if __name__ == "__main__":
    sm = SignalManager()
    print(sm)



