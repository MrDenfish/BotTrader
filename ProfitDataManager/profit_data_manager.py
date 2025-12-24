
import re
import os
import pandas as pd
from inspect import stack  # debugging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from webhook.webhook_validate_orders import OrderData
from Config.config_manager import CentralConfig as config
from Shared_Utils.logger import get_logger

def _env_pct(name: str, default: float) -> Decimal:
    try:
        return Decimal(os.getenv(name, str(default)))
    except Exception:
        return Decimal(str(default))

def _stop_mode() -> str:
    m = os.getenv("STOP_MODE", "atr").lower()
    return m if m in ("atr", "fixed") else "atr"

def _fee_for_side(order_data) -> Decimal:
    # if you prefer maker in some venues, switch here based on order type/flags
    side = os.getenv("FEE_SIDE", "taker").lower()
    return order_data.taker if side == "taker" else order_data.maker

def _spread_pct(order_book: dict | None) -> Decimal:
    try:
        if not order_book:
            return _env_pct("SPREAD_CUSHION_PCT", 0.0015)  # 0.15%
        bid = Decimal(str(order_book["bid"]))
        ask = Decimal(str(order_book["ask"]))
        return max((ask - bid) / ask, Decimal("0")) if ask > 0 else _env_pct("SPREAD_CUSHION_PCT", 0.0015)
    except Exception:
        return _env_pct("SPREAD_CUSHION_PCT", 0.0015)

def _atr_pct_from_ohlcv(ohlcv: list | None, entry_price: Decimal, period: int = 14) -> Decimal | None:
    # ohlcv rows: [ts, open, high, low, close, volume], newest last
    if not ohlcv or len(ohlcv) < period + 1 or entry_price <= 0:
        return None
    trs = []
    prev_close = Decimal(str(ohlcv[0][4]))
    for row in ohlcv[1:]:
        high = Decimal(str(row[2])); low = Decimal(str(row[3])); close = Decimal(str(row[4]))
        tr = max(high - low, abs(high - prev_close), abs(prev_close - low))
        trs.append(tr)
        prev_close = close
        if len(trs) > period:
            trs.pop(0)
    if not trs:
        return None
    atr = sum(trs) / Decimal(len(trs))
    return atr / entry_price

class ProfitDataManager:
    _instance = None
    @classmethod
    def get_instance(cls, shared_utils_utility, shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager, market_data_updater=None):
        """
        Singleton method to ensure only one instance of ProfitDataManager exists.
        """
        if cls._instance is None:
            cls._instance = cls( shared_utils_utility, shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager, market_data_updater)
        return cls._instance

    def __init__(self, shared_utils_utility, shared_utils_precision, shared_utils_print_data, shared_data_manager, logger_manager, market_data_updater=None):
        self.config = config()
        self._hodl = self.config.hodl
        self._stop_loss = Decimal(self.config.stop_loss)
        self._take_profit = Decimal(self.config.take_profit)
        self.market_cache = None
        self.last_ticker_update = None
        self.logger_manager = logger_manager  # Keep for backward compatibility
        self.logger = get_logger('profit_data_manager', context={'component': 'profit_data_manager'})
        self.shared_data_manager = shared_data_manager
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_print_data = shared_utils_print_data
        self.shared_utils_precision = shared_utils_precision
        self.market_data_updater = market_data_updater
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
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

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


    async def calculate_profitability(self, symbol, required_prices, bid_ask_spread, usd_pairs):
        """
        Calculate profitability for a given asset using current balances and market prices.
        Returns a consolidated profit data dictionary.
        """
        try:
            # ✅ Normalize Symbol Format
            # Normalize to slash format for internal use
            base, quote = re.split(r'[-/]', symbol)
            normalized_symbol = f"{base}/{quote}"
            alt_symbol = f"{base}-{quote}"  # alternative format for lookup

            # Try both formats in bid_ask_spread
            current_price_raw = bid_ask_spread.get(normalized_symbol) or bid_ask_spread.get(alt_symbol)
            # Extract just the price (if dict), or use 0 fallback
            if isinstance(current_price_raw, dict):
                current_price = Decimal(current_price_raw.get("bid") or 0)
            else:
                current_price = Decimal(current_price_raw or 0)

            # For asset label
            asset = base

            if current_price == 0 and quote not in ['USD', 'USDC']:
                self.logger.warning("Current price not available", extra={'symbol': normalized_symbol})
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
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)



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
            # Guard against invalid Decimal values before rounding
            safe_quote_deci_temp = quote_deci if quote_deci is not None and isinstance(quote_deci, int) else 2
            profit_percentage = round(profit_percentage, 4) if profit_percentage.is_finite() else Decimal(0)
            current_value = round(current_value, safe_quote_deci_temp) if current_value.is_finite() else Decimal(0)
            profit = round(profit, safe_quote_deci_temp) if profit.is_finite() else Decimal(0)

            # ✅ Construct Profit Data
            # Guard against invalid precision values
            safe_base_deci = base_deci if base_deci is not None and isinstance(base_deci, int) else 8
            safe_quote_deci = quote_deci if quote_deci is not None and isinstance(quote_deci, int) else 2

            # Helper function to safely round Decimal values
            def safe_round(value, precision):
                try:
                    if value.is_finite():
                        return round(value, precision)
                except (InvalidOperation, AttributeError):
                    pass
                return Decimal(0)

            profit_data = {
                'asset': asset,
                'balance': safe_round(asset_balance, safe_base_deci),
                'price': safe_round(current_price, safe_quote_deci),
                'value': safe_round(current_value, safe_quote_deci),
                'cost_basis': safe_round(cost_basis, safe_quote_deci),
                'avg_price': safe_round(avg_price, safe_quote_deci),
                'profit': safe_round(profit, safe_quote_deci),
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
                self.logger.info("No profitability data available")
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
            self.logger.error("Error consolidating profit data", extra={'error': str(e)}, exc_info=True)
            return None

    async def calculate_tp_sl(self, order_data: OrderData):
        """
        TP/SL with ATR-or-fixed stop + spread/fee cushions.
        TP uses your existing 'Option A' (apply fee on top).
        Supports trigger-specific multipliers for momentum trades (ROC_MOMO).
        """
        try:
            # --- email reports
            TP_SL_LOG_PATH = os.getenv("TP_SL_LOG_PATH", "/app/logs/tpsl.jsonl")

            # ---- Inputs
            entry = order_data.adjusted_price                  # Decimal
            fee_pct = _fee_for_side(order_data)               # fraction (e.g., 0.0055)
            tp_pct  = getattr(self, "take_profit", None)
            if tp_pct is None:
                tp_pct = _env_pct("TAKE_PROFIT", 0.025)       # 2.5% default
            else:
                tp_pct = Decimal(str(tp_pct))

            # Get trigger-specific multipliers for momentum trades
            trigger_dict = order_data.trigger if isinstance(order_data.trigger, dict) else {}
            trigger_type = trigger_dict.get("trigger", "score")
            tp_mult, sl_mult = self._get_trigger_multipliers(trigger_type)

            # Apply trigger multiplier to TP
            tp_pct = tp_pct * tp_mult

            # ---- TP (unchanged "Option A"): price target then add fee
            tp_raw = entry * (Decimal("1") + tp_pct)
            tp_raw += tp_raw * fee_pct  # add fee on top
            tp_adj = self.shared_utils_precision.adjust_precision(
                order_data.base_decimal, order_data.quote_decimal, tp_raw, convert="quote"
            )

            # ---- SL (ATR or Fixed) + cushions
            mode = _stop_mode()  # 'atr' | 'fixed'

            # pull a tiny OHLCV window if available (optional, safe if not)
            ohlcv = None
            if hasattr(self, "market_data_updater") and hasattr(self.market_data_updater, "get_recent_ohlcv"):
                try:
                    base = getattr(order_data, "base_currency", None) or order_data.trading_pair.split("-")[0]
                    ohlcv = self.market_data_updater.get_recent_ohlcv(base, window=200)  # newest last
                except Exception as e:
                    self.logger.debug(f"OHLCV fetch failed for {order_data.trading_pair}: {e}")

            # recent orderbook snapshot available in caller; if not, we still use cushion
            order_book = {"bid": order_data.highest_bid, "ask": order_data.lowest_ask} \
                if (getattr(order_data, "highest_bid", None) and getattr(order_data, "lowest_ask", None)) else None

            spread_pct = _spread_pct(order_book)

            if mode == "atr":
                atr_mult = _env_pct("ATR_MULTIPLIER_STOP", 1.8)
                min_pct  = _env_pct("STOP_MIN_PCT", 0.012)  # 1.2% floor
                atr_pct  = _atr_pct_from_ohlcv(ohlcv, entry) or Decimal("0")
                base_pct = max(min_pct, atr_pct * atr_mult)
            else:
                # legacy fixed (use abs in case config is negative)
                fixed = getattr(self, "stop_loss", None)
                fixed = Decimal(str(fixed)) if fixed is not None else _env_pct("STOP_LOSS", 0.01)
                base_pct = abs(fixed)
                atr_pct = Decimal("0")  # not using ATR in fixed mode

            # Apply trigger multiplier to SL (wider stops for momentum trades)
            base_pct = base_pct * sl_mult

            stop_pct = base_pct + spread_pct + fee_pct
            sl_raw = entry * (Decimal("1") - stop_pct)

            sl_adj = self.shared_utils_precision.adjust_precision(
                order_data.base_decimal, order_data.quote_decimal, sl_raw, convert="quote"
            )

            # ---- Breadcrumb for logs
            try:
                self.logger.info(
                    f"tp/sl {order_data.trading_pair} entry={entry} "
                    f"tp={tp_adj} sl={sl_adj} mode={mode} "
                    f"base_stop%={base_pct:.5f} spread%={spread_pct:.5f} fee%={fee_pct:.5f}"
                )
            except Exception:
                pass

            # Prepare logging metrics using calculated values
            fee_side = "taker"  # or detect maker/taker used in calc
            fee_pct_logged = float(order_data.taker) if fee_side == "taker" else float(order_data.maker)
            atr_pct_logged = float(atr_pct)  # use calculated local variable
            spr_pct_logged = float(spread_pct)  # use calculated local variable

            rr = float((tp_adj - entry) / max(Decimal("1e-12"), (entry - sl_adj)))  # avoid div-by-zero
            stop_pct_logged = float((entry - sl_adj) / entry) if entry else None
            tp_pct_logged = float((tp_adj - entry) / entry) if entry else None

            self.shared_utils_utility.write_jsonl(TP_SL_LOG_PATH, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": order_data.trading_pair,
                "entry": float(entry),
                "tp": float(tp_adj),
                "sl": float(sl_adj),
                "rr": rr,  # risk:reward at entry time
                "tp_pct": tp_pct_logged,  # +% target
                "stop_pct": stop_pct_logged,  # -% stop
                "stop_mode": mode,  # use calculated mode variable
                "atr_pct": atr_pct_logged,  # use calculated ATR value
                "cushion_spread": spr_pct_logged,  # use calculated spread value
                "cushion_fee": fee_pct_logged,
                "fee_side": fee_side,
                "trigger": trigger_type,  # trigger type for analysis
                "tp_mult": float(tp_mult),  # TP multiplier applied
                "sl_mult": float(sl_mult)   # SL multiplier applied
            })

            return tp_adj, sl_adj

        except Exception as e:
            self.logger.error(f"❌️ Error in calculate_tp_sl: {e}", exc_info=True)
            return None, None

    def _get_trigger_multipliers(self, trigger_type: str) -> tuple:
        """
        Get TP and SL multipliers based on trigger type.
        ROC momentum trades need wider TP/SL to let trades run.

        Args:
            trigger_type: The trigger type string (e.g., "roc_momo_override")

        Returns:
            tuple: (tp_multiplier, sl_multiplier) as Decimals
        """
        trigger_upper = (trigger_type or "").upper()

        # ROC momentum triggers need wider TP/SL
        if trigger_upper in ("ROC_MOMO", "ROC_MOMO_OVERRIDE", "ROC"):
            # Use env vars if set, otherwise defaults for momentum trades
            tp_mult = _env_pct("ROC_TP_MULTIPLIER", 3.0)  # 3x TP for momentum
            sl_mult = _env_pct("ROC_SL_MULTIPLIER", 2.0)  # 2x SL for momentum
            return tp_mult, sl_mult

        # Default: no multiplier
        return Decimal("1.0"), Decimal("1.0")

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
