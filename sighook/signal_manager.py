from logging.handlers import TimedRotatingFileHandler
from decimal import Decimal, ROUND_HALF_UP

from pandas.core.methods.describe import select_describe_func

from Config.config_manager import CentralConfig
from Shared_Utils.paths import resolve_runtime_paths
from Shared_Utils.runtime_env import running_in_docker
from sighook.indicators import Indicators
from typing import Optional, Tuple, Dict, Any
from pathlib import Path
import logging
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
        # 5-minute ROC thresholds for momentum strategy
        self.roc_5min_buy_threshold = Decimal(str(self.config.roc_5min_buy_threshold or 10.0))
        # ROC_5MIN_SELL_THRESHOLD is stored as positive in config, negate for sell threshold
        self.roc_5min_sell_threshold = -Decimal(str(self.config.roc_5min_sell_threshold or 10.0))
        self.rsi_buy = Decimal(str(self.config.rsi_buy or 20))  # ← Tightened from 30 (more selective)
        self.rsi_sell = Decimal(str(self.config.rsi_sell or 80))  # ← Tightened from 70 (more selective)
        self.buy_target = float(self.config.buy_ratio or 0.0)
        self.sell_target = float(self.config.sell_ratio or 0.0)

        # # --- Score log output (CSV). Override via env SCORE_LOG_PATH if you like.
        # self.score_log_path = os.getenv("SCORE_LOG_PATH", os.path.join("logs", "score_log.csv"))
        # os.makedirs(os.path.dirname(self.score_log_path), exist_ok=True)
        # self._score_log_header_written = os.path.exists(self.score_log_path)

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
            'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 1.5,  # ← Reduced from 2.5
            'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
            'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 1.5,  # ← Reduced from 2.5
            'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
        }

        # ✅ Multi-Indicator Confirmation (NEW)
        self.min_indicators_required = int(self.config.min_indicators_required or 2)

        # --- Runtime directories (single source of truth) -----------------

        # Prefer dirs exposed by SharedDataManager (set by CentralConfig)
        data_dir = getattr(self.shared_data_manager, "data_dir", None)
        cache_dir = getattr(self.shared_data_manager, "cache_dir", None)
        log_dir = getattr(self.shared_data_manager, "log_dir", None)

        # If not provided, resolve via shared helpers
        try:
            from Shared_Utils.paths import resolve_runtime_paths
            from Shared_Utils.runtime_env import running_in_docker
            if any(d is None for d in (data_dir, cache_dir, log_dir)):
                r_data, r_cache, r_logs = resolve_runtime_paths(running_in_docker())
                data_dir = data_dir or r_data
                cache_dir = cache_dir or r_cache
                log_dir = log_dir or r_logs
        except Exception:
            # Conservative fallbacks (rarely used)
            if any(d is None for d in (data_dir, cache_dir, log_dir)):
                if os.getenv("IN_DOCKER", "false").strip().lower() in {"1", "true", "yes", "on"}:
                    data_dir = Path("/app/data") if data_dir is None else Path(data_dir)
                    cache_dir = Path("/app/cache") if cache_dir is None else Path(cache_dir)
                    log_dir = Path("/app/logs") if log_dir is None else Path(log_dir)
                else:
                    base = Path.cwd() / ".bottrader"
                    data_dir = base if data_dir is None else Path(data_dir)
                    cache_dir = (base / "cache") if cache_dir is None else Path(cache_dir)
                    log_dir = (base / "logs") if log_dir is None else Path(log_dir)

        # Normalize and ensure they exist
        self.data_dir = Path(str(data_dir))
        self.cache_dir = Path(str(cache_dir))
        self.log_dir = Path(str(log_dir))
        for d in (self.data_dir, self.cache_dir, self.log_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.warning(f"Could not create runtime dir {d}: {e}")

        # --- Score JSONL path (env-overrideable) --------------------------
        # Default to logs dir; allow SCORE_JSONL_PATH and SCORE_JSONL_FILENAME overrides
        score_filename = os.getenv("SCORE_JSONL_FILENAME", "score_log.jsonl")
        score_jsonl = os.getenv("SCORE_JSONL_PATH", str(self.log_dir / score_filename))
        self.score_jsonl_path = str(Path(score_jsonl))
        Path(self.score_jsonl_path).parent.mkdir(parents=True, exist_ok=True)

        # --- Dedicated JSONL logger --------------------------------------
        self.score_logger = logging.getLogger("score_jsonl")
        self.score_logger.propagate = False
        if not self.score_logger.handlers:
            handler = TimedRotatingFileHandler(
                filename=self.score_jsonl_path,
                when="midnight",
                interval=1,
                backupCount=int(os.getenv("SCORE_BACKUP_COUNT", "7")),
                utc=True
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.score_logger.addHandler(handler)
            self.score_logger.setLevel(logging.INFO)

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
            self._append_score_jsonl(payload)
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

    def _append_score_jsonl(self, payload: dict):
        try:
            self.score_logger.info(json.dumps(payload, default=str))
        except Exception:
            pass

    # =========================================================
    # ✅ Core Buy/Sell Scoring
    # =========================================================
    def buy_sell_scoring(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        try:
            guardrail_note = None
            action = 'hold'
            last_row = ohlcv_df.iloc[-1]

            # ✅ 24-HOUR MOMENTUM ROC STRATEGY
            # Changed from 5-minute ROC to 24-hour momentum runners
            # Goal: Catch markets that surge 10%+ in 24h and keep rising

            rsi_value = last_row.get('RSI', None)

            # Get 24-hour price change from usd_pairs (live ticker data)
            roc_24h_value = None
            if self.usd_pairs is not None:
                try:
                    usd_pairs_df = self.usd_pairs.set_index("asset")
                    # Extract symbol name (e.g., "BTC-USD" -> "BTC")
                    asset_name = symbol.split('-')[0] if '-' in symbol else symbol
                    if asset_name in usd_pairs_df.index:
                        roc_24h_value = float(usd_pairs_df.loc[asset_name, 'price_percentage_change_24h'])
                except Exception as e:
                    self.logger.debug(f"Could not fetch 24h ROC for {symbol}: {e}")

            if roc_24h_value is not None and rsi_value is not None:
                # 24-hour momentum thresholds (optimized for momentum runners)
                roc_24h_buy_threshold = 10.0   # 10% gain in 24 hours
                roc_24h_sell_threshold = -5.0  # -5% drop in 24 hours

                # RSI gate: Only trade in neutral zone (avoid overextended conditions)
                # Tightened to 45-55 to avoid chasing pumps that are already overheated
                buy_signal_roc = (
                    (roc_24h_value > roc_24h_buy_threshold) and
                    (45.0 <= rsi_value <= 55.0)
                )
                sell_signal_roc = (
                    (roc_24h_value < roc_24h_sell_threshold) and
                    (45.0 <= rsi_value <= 55.0)
                )

                if buy_signal_roc:
                    # compute full components so we can see the context even on overrides
                    bs, ss, comps = self._compute_score_components(last_row)
                    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='buy', trigger='roc_momo_24h')

                    return {
                        'action': 'buy', 'trigger': 'roc_momo', 'type': 'limit',
                        'Buy Signal': (1, float(roc_24h_value), float(roc_24h_buy_threshold)),
                        'Sell Signal': (0, None, None),
                        'Score': {'Buy Score': bs, 'Sell Score': ss}
                    }
                if sell_signal_roc:
                    # compute full components so we can see the context even on overrides
                    bs, ss, comps = self._compute_score_components(last_row)
                    self._log_score_snapshot(symbol, ohlcv_df, bs, ss, comps, action='sell', trigger='roc_momo_24h')

                    return {
                        'action': 'sell', 'trigger': 'roc_momo', 'type': 'limit',
                        'Sell Signal': (1, float(roc_24h_value), float(roc_24h_sell_threshold)),
                        'Buy Signal': (0, None, None),
                        'Score': {'Buy Score': bs, 'Sell Score': ss}
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

            # ✅ Multi-Indicator Confirmation (NEW)
            # Count how many indicators actually fired (decision == 1)
            buy_indicators_fired = sum(
                1 for indicator in self.strategy_weights.keys()
                if indicator.startswith("Buy") and last_row.get(indicator, (0,))[0] == 1
            )
            sell_indicators_fired = sum(
                1 for indicator in self.strategy_weights.keys()
                if indicator.startswith("Sell") and last_row.get(indicator, (0,))[0] == 1
            )

            # Suppress signal if insufficient indicators
            if buy_signal[0] == 1 and buy_indicators_fired < self.min_indicators_required:
                guardrail_note = f"buy_suppressed_insufficient_indicators_{buy_indicators_fired}_of_{self.min_indicators_required}"
                buy_signal = (0, buy_signal[1], buy_signal[2])

            if sell_signal[0] == 1 and sell_indicators_fired < self.min_indicators_required:
                guardrail_note = f"sell_suppressed_insufficient_indicators_{sell_indicators_fired}_of_{self.min_indicators_required}"
                sell_signal = (0, sell_signal[1], sell_signal[2])

            # --- Guardrails: hysteresis & cooldown (per symbol) ---
            # Use the DataFrame index as a bar counter for cooldown
            bar_idx = int(getattr(last_row, 'name', len(ohlcv_df) - 1))

            # Hysteresis: require +x% over target to flip away from current side
            # Example: if last_side == "long", require SELL score >= target * (1 + hyst)
            hyst = 1.0 + max(0.0, self.flip_hysteresis_pct)

            last_side = self._last_side.get(symbol)
            if last_side == "long" and sell_signal[0] == 1 and sell_score < (self.score_sell_target * hyst):
                guardrail_note = "sell_suppressed_by_hysteresis"
                sell_signal = (0, sell_signal[1], sell_signal[2])  # suppress marginal flip
            elif last_side == "short" and buy_signal[0] == 1 and buy_score < (self.score_buy_target * hyst):
                guardrail_note = "buy_suppressed_by_hysteresis"
                buy_signal = (0, buy_signal[1], buy_signal[2])

            # Cooldown: after a flip, ignore the opposite side until a future bar index
            cd_until = self._cooldown_until.get(symbol, -1)
            if bar_idx < cd_until:
                # If in cooldown, suppress the opposite of last_side
                if last_side == "long":
                    guardrail_note = "sell_suppressed_by_cooldown"
                    sell_signal = (0, sell_signal[1], sell_signal[2])
                elif last_side == "short":
                    guardrail_note = "buy_suppressed_by_cooldown"
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
                                     action=action, trigger=guardrail_note or 'score')

            # 🔹 Trigger signal-based ETH accumulation on strong buy signals
            if (action == 'buy' and buy_score >= 4.0 and
                hasattr(self.shared_data_manager, 'accumulation_manager') and
                self.shared_data_manager.accumulation_manager is not None):
                try:
                    import asyncio
                    asyncio.create_task(
                        self.shared_data_manager.accumulation_manager.accumulate_on_signal(signal=True),
                        name=f"Accumulation-{symbol}"
                    )
                except Exception as e:
                    self.logger.debug(f"[Accumulation] Could not trigger signal-based accumulation: {e}")

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
        """
        Fetch active trade records for a specific symbol (optimized for TP/SL).

        Previously used fetch_all_trades() which loaded 7000+ records (1GB) causing timeouts.
        Now uses fetch_active_trades_for_symbol() which queries only the needed records.

        Performance: ~1000x faster (1 GB → ~1 KB)
        """
        try:
            # Use optimized symbol-specific query instead of fetching ALL trades
            trades = await self.trade_recorder.fetch_active_trades_for_symbol(symbol)
            return [
                {
                    "symbol": t.symbol,
                    "cost_basis_usd": float(t.cost_basis_usd or 0),
                    "remaining_size": float(t.remaining_size or 0)
                }
                for t in trades
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






