
from decimal import Decimal, ROUND_HALF_UP
from Config.config_manager import CentralConfig
from sighook.indicators import Indicators
from typing import Optional, Tuple, Dict, Any
import pandas as pd
import json
import csv
import os

class SignalManager:
    """
    Manages dynamic and static buy/sell signal thresholds, computes scoring,
    and evaluates TP/SL conditions based on trade history.
    """

    def __init__(self, logger,shared_data_manager, shared_utils_precision, trade_recorder):

        self.config = CentralConfig()
        self.logger = logger
        self.indicators = Indicators(logger)
        self.shared_utils_precision = shared_utils_precision
        self.shared_data_manager = shared_data_manager
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

        # --- Score log output (CSV). Override via env SCORE_LOG_PATH if you like.
        self.score_log_path = os.getenv("SCORE_LOG_PATH", os.path.join("logs", "score_log.csv"))
        os.makedirs(os.path.dirname(self.score_log_path), exist_ok=True)
        self._score_log_header_written = os.path.exists(self.score_log_path)

        # --- Score targets (separate from band-ratio thresholds) ---
        self.score_buy_target = float(self.config.score_buy_target or 5.5)
        self.score_sell_target = float(self.config.score_sell_target or 5.5)

        # --- Guardrails (configurable) ---
        self.allow_buys_on_red_day = bool(self.config.allow_buys_on_red_day)
        self.flip_hysteresis_pct = float(self.config.flip_hysteresis_pct or 0.10)
        self.cooldown_bars = int(self.config.cooldown_bars or 7)

        # --- Per-symbol state for hysteresis & cooldown ---
        self._last_side: dict[str, str] = {}  # {"SYMBOL": "long"|"short"}
        self._cooldown_until: dict[str, int] = {}  # {"SYMBOL": last_bar_index_allowed}

        # ✅ Strategy Weights
        self.strategy_weights = self.indicators.strategy_weights or {
            'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
            'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
            'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5,
            'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
        }

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    # -------- Score Logger Helpers --------
    def _compute_score_components(self, last_row) -> tuple[float, float, dict]:
        """
        Returns:
          buy_score, sell_score, components = {
              "buy": [{"indicator","decision","value","threshold","weight","contribution"}...],
              "sell": [ ... ]
          }
        """
        buy_score, sell_score = 0.0, 0.0
        buy_components, sell_components = [], []

        for indicator, weight in self.strategy_weights.items():
            raw = last_row.get(indicator)
            if isinstance(raw, tuple) and len(raw) == 3:
                decision = int(raw[0])
                value = float(raw[1] if raw[1] is not None else 0.0)
                threshold = float(raw[2] if raw[2] is not None else 0.0)
                contribution = decision * float(weight)

                row = {
                    "indicator": indicator,
                    "decision": decision,
                    "value": value,
                    "threshold": threshold,
                    "weight": float(weight),
                    "contribution": round(contribution, 6),
                }
                if indicator.startswith("Buy"):
                    buy_score += contribution
                    buy_components.append(row)
                elif indicator.startswith("Sell"):
                    sell_score += contribution
                    sell_components.append(row)

        components = {"buy": buy_components, "sell": sell_components}
        return round(buy_score, 6), round(sell_score, 6), components

    def _ensure_score_log_header(self):
        if self._score_log_header_written:
            return
        headers = [
            "ts","symbol","bar_idx","price","side","indicator",
            "decision","weight","contribution","value","threshold",
            "buy_score","sell_score","target_buy","target_sell",
            "action","trigger","last_side","cooldown_until",
            "ROC","RSI","MACD_Hist","upper","lower"
        ]
        with open(self.score_log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
        self._score_log_header_written = True

    def _append_score_log_rows(self, rows: list[dict]):
        """Append many component-rows to CSV (one component per row)."""
        if not rows:
            return
        self._ensure_score_log_header()
        headers = [
            "ts","symbol","bar_idx","price","side","indicator",
            "decision","weight","contribution","value","threshold",
            "buy_score","sell_score","target_buy","target_sell",
            "action","trigger","last_side","cooldown_until",
            "ROC","RSI","MACD_Hist","upper","lower"
        ]
        with open(self.score_log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            for r in rows:
                writer.writerow({k: r.get(k) for k in headers})

    def _log_score_snapshot(
        self,
        symbol: str,
        ohlcv_df: pd.DataFrame,
        buy_score: float,
        sell_score: float,
        components: dict,
        action: str,
        trigger: str
    ):
        """Emit a compact JSON line to the logger and (optionally) write a detailed CSV."""
        last_row = ohlcv_df.iloc[-1]
        # time/index/price context
        ts = last_row.get("time")
        if isinstance(ts, (float, int)):  # safety
            ts_str = str(ts)
        else:
            try:
                ts_str = ts.isoformat()
            except Exception:
                ts_str = str(ts)
        bar_idx = int(getattr(last_row, "name", len(ohlcv_df) - 1))
        price = float(last_row.get("close", 0.0))

        # quick commonly-used raw fields
        roc = last_row.get("ROC", None)
        rsi = last_row.get("RSI", None)
        macd_hist = last_row.get("MACD_Histogram", None)
        upper = last_row.get("upper", None)
        lower = last_row.get("lower", None)

        # sort and keep top contributors for the JSON log (keep CSV full detail)
        top_buy = sorted(components["buy"], key=lambda x: x["contribution"], reverse=True)[:5]
        top_sell = sorted(components["sell"], key=lambda x: x["contribution"], reverse=True)[:5]

        payload = {
            "ts": ts_str,
            "symbol": symbol,
            "bar_idx": bar_idx,
            "price": price,
            "action": action,
            "trigger": trigger,
            "buy_score": round(buy_score, 3),
            "sell_score": round(sell_score, 3),
            "target_buy": self.score_buy_target,
            "target_sell": self.score_sell_target,
            "last_side": self._last_side.get(symbol),
            "cooldown_until": self._cooldown_until.get(symbol, -1),
            "top_buy_components": top_buy,
            "top_sell_components": top_sell,
            "raw": {
                "ROC": None if roc is None else float(roc),
                "RSI": None if rsi is None else float(rsi),
                "MACD_Hist": None if macd_hist is None else float(macd_hist),
                "upper": None if upper is None else float(upper),
                "lower": None if lower is None else float(lower),
            }
        }

        # JSON line to your logger
        try:
            self.logger.info(f"📊 score_snapshot {json.dumps(payload, default=str)}")
        except Exception:
            # never break trading on logging
            pass

        # Build CSV rows (one per component), both sides
        csv_rows = []
        common = {
            "ts": ts_str,
            "symbol": symbol,
            "bar_idx": bar_idx,
            "price": price,
            "buy_score": round(buy_score, 6),
            "sell_score": round(sell_score, 6),
            "target_buy": self.score_buy_target,
            "target_sell": self.score_sell_target,
            "action": action,
            "trigger": trigger,
            "last_side": self._last_side.get(symbol),
            "cooldown_until": self._cooldown_until.get(symbol, -1),
            "ROC": None if roc is None else float(roc),
            "RSI": None if rsi is None else float(rsi),
            "MACD_Hist": None if macd_hist is None else float(macd_hist),
            "upper": None if upper is None else float(upper),
            "lower": None if lower is None else float(lower),
        }

        for comp in components["buy"]:
            csv_rows.append({
                **common, "side": "buy",
                "indicator": comp["indicator"],
                "decision": comp["decision"],
                "weight": comp["weight"],
                "contribution": comp["contribution"],
                "value": comp["value"],
                "threshold": comp["threshold"],
            })
        for comp in components["sell"]:
            csv_rows.append({
                **common, "side": "sell",
                "indicator": comp["indicator"],
                "decision": comp["decision"],
                "weight": comp["weight"],
                "contribution": comp["contribution"],
                "value": comp["value"],
                "threshold": comp["threshold"],
            })

        try:
            self._append_score_log_rows(csv_rows)
        except Exception:
            # don't interrupt trading if the file is locked or path is invalid
            pass



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

            if roc_value is not None and rsi_value is not None:
                roc_thr_buy = float(self.roc_buy_threshold)  # e.g., +0.5% to +1.0% (config)
                roc_thr_sell = float(self.roc_sell_threshold)  # e.g., -0.5% to -1.0% (config)
                # Adaptive acceleration gate (fallback to 0.3 if not available)
                roc_diff_std = float(last_row.get('ROC_Diff_STD20', 0.3))
                accel_ok = abs(roc_diff_value) > max(0.3, 0.5 * roc_diff_std)

                buy_signal_roc = (roc_value > roc_thr_buy) and accel_ok and (rsi_value >= max(50.0, float(self.rsi_buy)))
                sell_signal_roc = (roc_value < roc_thr_sell) and accel_ok and (rsi_value <= min(50.0, float(self.rsi_sell)))

                if buy_signal_roc:
                    # compute full components so we can see the context even on overrides
                    bs, ss, comps = self._compute_score_components(last_row)
                    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_override')

                    return {
                        'action': 'buy', 'trigger': 'roc_momo', 'type': 'limit',
                        'Buy Signal': (1, float(roc_value), float(roc_thr_buy)),
                        'Sell Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }
                if sell_signal_roc:
                    # compute full components so we can see the context even on overrides
                    bs, ss, comps = self._compute_score_components(last_row)
                    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='sell', trigger='roc_momo_override')

                    return {
                        'action': 'sell', 'trigger': 'roc_momo', 'type': 'limit',
                        'Sell Signal': (1, float(roc_value), float(roc_thr_sell)),
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
                (1, round(buy_score, 3), self.score_buy_target)
                if buy_score >= self.score_buy_target else
                (0, round(buy_score, 3), self.score_buy_target)
            )
            sell_signal = (
                (1, round(sell_score, 3), self.score_sell_target)
                if sell_score >= self.score_sell_target else
                (0, round(sell_score, 3), self.score_sell_target)
            )

            # --- Guardrails: hysteresis & cooldown (per symbol) ---
            # Use the DataFrame index as a bar counter for cooldown
            bar_idx = int(getattr(last_row, 'name', len(ohlcv_df) - 1))

            # Hysteresis: require +x% over target to flip away from current side
            # Example: if last_side == "long", require SELL score >= target * (1 + hyst)
            hyst = 1.0 + max(0.0, self.flip_hysteresis_pct)

            last_side = self._last_side.get(symbol)
            if last_side == "long" and sell_signal[0] == 1 and sell_score < (self.score_sell_target * hyst):
                sell_signal = (0, sell_signal[1], sell_signal[2])  # suppress marginal flip
            elif last_side == "short" and buy_signal[0] == 1 and buy_score < (self.score_buy_target * hyst):
                buy_signal = (0, buy_signal[1], buy_signal[2])

            # Cooldown: after a flip, ignore the opposite side until a future bar index
            cd_until = self._cooldown_until.get(symbol, -1)
            if bar_idx < cd_until:
                # If in cooldown, suppress the opposite of last_side
                if last_side == "long":
                    sell_signal = (0, sell_signal[1], sell_signal[2])
                elif last_side == "short":
                    buy_signal = (0, buy_signal[1], buy_signal[2])

            # ✅ Conflict resolution with guardrails applied
            if buy_signal[0] == 1 and sell_signal[0] == 0:
                action = 'buy'
            elif sell_signal[0] == 1 and buy_signal[0] == 0:
                action = 'sell'
            elif buy_signal[0] == 1 and sell_signal[0] == 1:
                action = 'buy' if buy_score > sell_score else 'sell'
            else:
                action = 'hold'

            # Update state + cooldown on flips
            prev_side = self._last_side.get(symbol)
            curr_side = 'long' if action == 'buy' else ('short' if action == 'sell' else prev_side)
            if prev_side != curr_side and action in ('buy', 'sell'):
                self._last_side[symbol] = curr_side
                # start cooldown for the *opposite* side
                self._cooldown_until[symbol] = bar_idx + max(0, int(self.cooldown_bars))

            _, _, comps = self._compute_score_components(last_row)
            self._log_score_snapshot(symbol, ohlcv_df, buy_score, sell_score, comps,
                                     action=action, trigger='score')
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

    def evaluate_signals(self, asset: str, buy_sell_matrix: pd.DataFrame) -> Tuple[Tuple[int, float, float, str], Tuple[int, float, float, str]]:
        try:
            usd_pairs = self.usd_pairs.set_index("asset")
            price_change_24h = usd_pairs.loc[asset, 'price_percentage_change_24h'] if asset in usd_pairs.index else None

            row = buy_sell_matrix.loc[asset]

            buy_score = sum(
                row[ind][0] * self.strategy_weights.get(ind, 1.0)
                for ind in row.index if ind.startswith("Buy")
            )
            sell_score = sum(
                row[ind][0] * self.strategy_weights.get(ind, 1.0)
                for ind in row.index if ind.startswith("Sell")
            )

            buy_reason = "ok"
            sell_reason = "ok"

            if buy_score >= self.score_buy_target:
                if price_change_24h is None:
                    buy_signal = (0, round(buy_score, 3), self.score_buy_target, "blocked: no 24h price data")
                elif (price_change_24h < 0) and (not self.allow_buys_on_red_day):
                    buy_signal = (0, round(buy_score, 3), self.score_buy_target,
                                  f"blocked: 24h price down ({price_change_24h:.2f}%)")
                else:
                    buy_signal = (1, round(buy_score, 3), self.score_buy_target, "ok")
            else:
                buy_signal = (0, round(buy_score, 3), self.score_buy_target, "below threshold")

            if sell_score >= self.score_sell_target:
                sell_signal = (1, round(sell_score, 3), self.score_sell_target, "ok")
            else:
                sell_signal = (0, round(sell_score, 3), self.score_sell_target, "below threshold")
            return buy_signal, sell_signal

        except Exception as e:
            self.logger.error(f"❌ Error evaluating matrix signals for {asset}: {e}", exc_info=True)
            return (0, 0.0, 0.0, "error"), (0, 0.0, 0.0, "error")






