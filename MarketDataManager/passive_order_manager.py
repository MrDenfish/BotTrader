
from __future__ import annotations

"""Passive market‑making helper.

This module contains a self‑contained `PassiveOrderManager` class that
implements a simple maker‑only strategy:
  • Places resting bid/ask quotes just inside the spread when the spread
    is wide enough to cover fees.
  • Sizes quotes dynamically and respects balance / inventory limits.
  • Cancels / refreshes quotes after a configurable time‑to‑live so they
    do not get picked off when the market moves.

It expects the surrounding code‑base to provide:
  • `trade_order_manager` with an async `build_order_data()` and
    `place_order()` that operate on your existing `OrderData` dataclass.
  • `logger` implementing the stdlib `logging.Logger` interface.
  • A `fee_cache` or similar object exposing `maker` and `taker` rates.

Drop‑in defaults are provided for things like `min_spread_pct`, but tune
these at runtime based on your exchange tier and risk appetite.
"""
import os
import asyncio
import copy
import time
import  json
import pandas as pd
from collections.abc import Mapping
from Shared_Utils.enum import ExitCondition
from typing import Any, Tuple, Optional, Dict
from datetime import datetime, timedelta, timezone
from webhook.webhook_validate_orders import OrderData
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from Shared_Utils.logger import get_logger



# ---------------------------------------------------------------------------
# Type aliases – keep it loose here, real project will import your dataclass
# ---------------------------------------------------------------------------

class PassiveOrderManager:
    def __init__(self, config, ccxt_api, coinbase_api, exchange, ohlcv_manager, shared_data_manager, shared_utils_color, shared_utils_utility,
                 shared_utils_precision, trade_order_manager, order_manager, logger_manager, edge_buffer_pct, min_spread_pct,
                 max_lifetime, inventory_bias_factor, fee_cache: Dict[str, Decimal]) -> None:
        self.config = config
        self.tom = trade_order_manager  # shorthand inside class
        self.order_manager = order_manager
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_color = shared_utils_color
        self.shared_data_manager = shared_data_manager
        self.logger_manager = logger_manager  # Keep for backward compatibility
        self.logger = get_logger('passive_order_manager', context={'component': 'passive_order_manager'})
        self.fee = fee_cache  # expects {'maker': Decimal, 'taker': Decimal}



        self.ccxt_api = ccxt_api
        self.exchange = exchange
        self.coinbase_api = coinbase_api

        # Trading parameters
        self._stop_loss = Decimal(config.stop_loss)
        self._min_quote_volume = Decimal(config.min_quote_volume)
        self._take_profit = Decimal(config.take_profit)
        self._trailing_percentage = Decimal(config.trailing_percentage)
        self._trailing_stop = Decimal(config.trailing_stop)
        self._min_order_amount_fiat = Decimal(config.min_order_amount_fiat)
        self._min_buy_value = Decimal(config.min_buy_value)



        def _as_dec(v: Any) -> Decimal:
            return v if isinstance(v, Decimal) else Decimal(str(v))

        # Passive order parameters
        self._min_spread_pct = config.min_spread_pct # Decimal
        self._edge_buffer_pct = config.edge_buffer_pct # Decimal
        self._max_lifetime   = int(config.max_lifetime) # int (cast to be safe)
        self._inventory_bias_factor = Decimal(config.inventory_bias_factor)  # str -> Decimal

        # {symbol: {"buy": orde_id, "sell": order_id, "timestamp": float}}
        self.passive_order_tracker: Dict[str, Dict[str, Any]] = {}

        # Data managers
        self.ohlcv_manager = ohlcv_manager

        # ✅ Cache structure (class-level or init-level)
        self._profitable_symbols_cache = {
            "last_update": 0,
            "symbols": set()
        }

        # launch watchdog
        asyncio.create_task(self._watchdog())
        asyncio.create_task(self.live_performance_tracker(interval=300, lookback_days=7))
    _fee_lock: asyncio.Lock = asyncio.Lock()

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread', {})

    async def update_fee_cache(self, new_fee: Dict[str, Decimal]) -> None:
        """Hot-swap maker/taker fees atomically."""
        async with self._fee_lock:
            self.fee = new_fee
    # ------------------------------------------------------------------
    # Public entry – call this once per symbol you want to quote
    # ------------------------------------------------------------------

    async def monitor_passive_position(self, symbol: str, od: OrderData):
        """
        Monitors a passive BUY order. Dynamically adjusts SL, TP, and TSL thresholds based on volatility.
        """
        try:
            quote_deci = od.quote_decimal
            base_deci = od.base_decimal

            # Get current price
            ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, 'public', symbol)
            current_price = Decimal(ticker_data['last'])
            current_price = self.shared_utils_precision.safe_quantize(
                current_price, Decimal("1").scaleb(-quote_deci)
            )

            entry = self.passive_order_tracker.get(symbol, {})
            peak_price = entry.get("peak_price", od.limit_price)

            # ✅ Time-based stale position check (NEW)
            hold_time = time.time() - entry.get("timestamp", time.time())
            if hold_time > self._max_lifetime:
                # Position held too long - evaluate exit
                be_maker, be_taker = self._break_even_prices(od.limit_price)

                if current_price >= be_maker:
                    # Can exit at break-even or profit
                    profit_pct = ((current_price - od.limit_price) / od.limit_price) * Decimal("100")
                    self.logger.warning(
                        f"⏰ Max lifetime ({self._max_lifetime}s) reached for {symbol}, "
                        f"exiting at/above break-even @ {current_price} (Profit: {profit_pct:.2f}%)"
                    )
                    await self._submit_passive_sell(
                        symbol, od, current_price, reason="max_lifetime",
                        note=f"HoldTime:{hold_time:.0f}s,BE:{be_maker:.4f},Profit:{profit_pct:.2f}%"
                    )
                else:
                    # Below break-even - take small loss rather than hold forever
                    loss_pct = ((current_price - od.limit_price) / od.limit_price) * Decimal("100")
                    self.logger.error(
                        f"⏰ Max lifetime + underwater for {symbol}, forced exit @ {current_price} "
                        f"(Loss: {loss_pct:.2f}%)"
                    )
                    await self._submit_passive_sell(
                        symbol, od, current_price, reason="timeout_loss",
                        note=f"HoldTime:{hold_time:.0f}s,BE:{be_maker:.4f},Loss:{loss_pct:.2f}%"
                    )
                return

            # ✅ NEW: Volatility factor (spread as proxy, can later replace w/ ATR)
            normalized_spread_pct = (
                od.spread / od.limit_price if od.limit_price and od.spread else Decimal("0")
            )
            volatility_multiplier = max(Decimal("1.0"), normalized_spread_pct * Decimal("10"))

            self.logger.debug("Passive active order monitoring",
                            extra={'symbol': symbol, 'current_price': str(current_price), 'entry': str(entry),
                                   'peak_price': str(peak_price), 'spread_pct': f"{normalized_spread_pct:.4%}"})

            # Update peak price (for TSL tracking)
            if current_price > peak_price:
                entry["peak_price"] = current_price
                peak_price = current_price

            # ✅ Dynamic STOP-LOSS (SL)
            dynamic_sl_pct = max(
                self.fee["taker"] * Decimal(2),
                normalized_spread_pct * Decimal("1.5") * volatility_multiplier
            )
            stop_price = od.limit_price * (Decimal("1.0") - dynamic_sl_pct)
            stop_price = self.shared_utils_precision.safe_quantize(
                stop_price, Decimal("1").scaleb(-quote_deci)
            )

            if current_price <= stop_price:
                self.logger.warning(
                    f"🔻 Stop-loss triggered for {symbol} @ {current_price} (SL: {stop_price})"
                )
                await self._submit_passive_sell(
                    symbol, od, current_price, reason="stop_loss", note=f"SL:{stop_price}"
                )
                return

            # ✅ Break-Even Exit Logic (NEW)
            # Calculate fee-aware break-even prices
            be_maker, be_taker = self._break_even_prices(od.limit_price)
            # Require 0.2% buffer above maker break-even to avoid marginal exits
            min_profit_buffer = od.limit_price * Decimal("0.002")
            profitable_exit_price = be_maker + min_profit_buffer

            if current_price >= profitable_exit_price:
                profit_pct = ((current_price - od.limit_price) / od.limit_price) * Decimal("100")
                self.logger.info(
                    f"💰 Break-even+ exit for {symbol} @ {current_price} "
                    f"(Entry: {od.limit_price}, BE: {be_maker:.4f}, Profit: {profit_pct:.2f}%)"
                )
                await self._submit_passive_sell(
                    symbol, od, current_price, reason="break_even_plus",
                    note=f"Entry:{od.limit_price},BE:{be_maker:.4f},Profit:{profit_pct:.2f}%"
                )
                return

            # ✅ Dynamic TAKE-PROFIT (TP)
            take_profit_price = od.limit_price * (
                    Decimal("1.0") + (self._take_profit * volatility_multiplier)
            )
            if current_price >= take_profit_price:
                self.logger.info(
                    f"📈 Take-profit triggered for {symbol} @ {current_price} (TP: {take_profit_price})"
                )
                await self._submit_passive_sell(
                    symbol, od, current_price, reason="take_profit", note=f"TP:{take_profit_price}"
                )
                return

            # ✅ Dynamic TRAILING STOP-LOSS (TSL)
            trailing_stop_price = peak_price * (
                    Decimal("1.0") - (self._trailing_percentage * volatility_multiplier)
            )
            if current_price <= trailing_stop_price:
                self.logger.warning(
                    f"🔻 Trailing SL triggered for {symbol} @ {current_price} "
                    f"(Peak:{peak_price}, TSL:{trailing_stop_price})"
                )
                await self._submit_passive_sell(
                    symbol, od, current_price, reason="trailing_stop",
                    note=f"Peak:{peak_price},TSL:{trailing_stop_price}"
                )
                return

        except Exception as e:
            self.logger.error(f"❌ Error in monitor_passive_position() for {symbol}: {e}", exc_info=True)

    async def evaluate_exit_conditions(self, od: OrderData) -> Optional[dict]:

        symbol = od.trading_pair
        side = od.side.upper()
        filled_price = od.filled_price
        available_qty = od.available_to_trade_crypto
        current_price = od.lowest_ask if side == "SELL" else od.highest_bid

        if current_price <= 0 or available_qty <= 0:
            self.logger.warning(f"Skipping {symbol}: invalid current_price or quantity.")
            return None

        # --- Fee-aware break-evens ---
        be_maker_exit, be_taker_exit = self._break_even_prices(Decimal(filled_price))

        # Assume take-profit exits passively (maker), stop-loss exits aggressively (taker)
        tp_edge_over_entry = ((Decimal(current_price) - be_maker_exit) / Decimal(filled_price)) * 100
        sl_edge_over_entry = ((Decimal(current_price) - be_taker_exit) / Decimal(filled_price)) * 100

        min_profit_pct = Decimal(self.tom.config.get("min_profit_pct", "1.0"))
        max_loss_pct   = Decimal(self.tom.config.get("max_loss_pct", "-5.0"))

        if tp_edge_over_entry >= min_profit_pct:
            return {
                "trigger": {
                    "trigger": ExitCondition.TAKE_PROFIT.value,
                    "trigger_note": f"Net≥TP {tp_edge_over_entry:.2f}% >= {min_profit_pct}% (fee-aware)"
                }
            }

        if sl_edge_over_entry <= max_loss_pct:
            return {
                "trigger": {
                    "trigger": ExitCondition.STOP_LOSS.value,
                    "trigger_note": f"Net≤SL {sl_edge_over_entry:.2f}% <= {max_loss_pct}% (fee-aware)"
                }
            }

        return None

    async def place_passive_orders(self, asset: str, product_id: str) -> None:
        """
        Passive MM:
          1) Profitability/liquidity gate
          2) active_symbols gate
          3) Build OrderData
          4) Spread/edge checks
          5) BUY via quote notional (order_amount_fiat); SELL via base_avail_balance
          6) Robust result normalization + 'validated-only' detection
        """


        trading_pair = product_id

        base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(asset)
        quote_quantizer = Decimal("1").scaleb(-quote_deci)
        bid_ask = self.bid_ask_spread.get(trading_pair, {})

        bid = bid_ask.get('bid')
        ask = bid_ask.get('ask')

        current_bid = self.shared_utils_precision.safe_quantize(bid, quote_quantizer)
        current_ask = self.shared_utils_precision.safe_quantize(ask, quote_quantizer)


        self.logger.info(f"🧭 PassiveMM:start {product_id} asset={asset}") # debug


        # ---------- helpers ----------
        def _to_step(value: Decimal, step: Decimal, rounding=ROUND_DOWN) -> Decimal:
            return value.quantize(step, rounding=rounding)

        def _order_result(res: Any) -> Tuple[bool, Optional[str], Any]:
            """
            Normalize to (ok, order_id, raw). Special-cased for (bool, dict) path.
            """
            if res is None:
                return False, None, None
            # Primary TOM path: (bool, dict)
            if isinstance(res, (tuple, list)) and len(res) == 2 and isinstance(res[0], bool):
                ok = res[0]
                payload = res[1]
                oid = None
                if isinstance(payload, Mapping):
                    oid = payload.get("order_id") or payload.get("id") or payload.get("client_order_id") \
                          or payload.get("clientOrderId") or payload.get("Order Id")
                    # common nestings
                    if not oid and isinstance(payload.get("details"), Mapping):
                        det = payload["details"]
                        oid = det.get("order_id") or det.get("id") or det.get("Order Id")
                    if not oid and isinstance(payload.get("response"), Mapping):
                        rsp = payload["response"]
                        oid = rsp.get("order_id") or rsp.get("id") or rsp.get("client_order_id") or rsp.get("clientOrderId")
                return ok, str(oid) if oid else None, res
            # Dict fallback
            if isinstance(res, Mapping):
                ok_val = res.get("ok")
                if not isinstance(ok_val, bool):
                    ok_val = res.get("success")
                    if isinstance(ok_val, str):
                        ok_val = ok_val.lower() in {"true", "1", "yes", "ok"}
                oid = res.get("order_id") or res.get("id") or res.get("client_order_id") \
                      or res.get("clientOrderId") or res.get("Order Id")
                if not oid and isinstance(res.get("details"), Mapping):
                    det = res["details"]
                    oid = det.get("order_id") or det.get("id") or det.get("Order Id")
                if not oid and isinstance(res.get("response"), Mapping):
                    rsp = res["response"]
                    oid = rsp.get("order_id") or rsp.get("id") or rsp.get("client_order_id") or rsp.get("clientOrderId")
                return bool(ok_val), str(oid) if oid else None, res
            # Last resort
            return False, None, res

        def _validated_only(raw: Any) -> Tuple[bool, Optional[str]]:
            """
            Detect (False, {'is_valid': True, 'code': '200', 'message': ...}) shape.
            """
            if isinstance(raw, (tuple, list)) and len(raw) == 2 and raw[0] is False and isinstance(raw[1], Mapping):
                d = raw[1]
                if d.get("is_valid") is True and str(d.get("code")) == "200":
                    return True, d.get("message") or "Validation OK; not submitted"
            return False, None

        async def _balances(trading_pair: str) -> dict:
            """
            Snapshot balances; replace these with your real cached calls if you have them.
            Return {'usd': Decimal, 'base': Decimal}
            """
            usd = Decimal("0")
            base = Decimal("0")
            try:
                usd = await self.shared_data_manager.fetch_usd_available()
                base = await self.shared_data_manager.fetch_base_available(trading_pair)
            except Exception:
                pass
            # coerce
            try:
                usd = Decimal(str(usd))
            except Exception:
                usd = Decimal("0")
            try:
                base = Decimal(str(base))
            except Exception:
                base = Decimal("0")
            return {"usd": usd, "base": base}

        try:
            # -------- 0) guards --------
            if not self.shared_data_manager:
                self.logger.debug("⛔ No shared_data_manager; skipping passive orders.")
                return

            # Load HODL and SHILL_COINS lists from environment
            hodl_list = os.getenv('HODL', '').split(',')
            hodl_assets = {a.strip().upper() for a in hodl_list if a.strip()}

            shill_list = os.getenv('SHILL_COINS', '').split(',')
            shill_assets = {a.strip().upper() for a in shill_list if a.strip()}

            # Check if this asset is in SHILL_COINS (only allow sells, no buys)
            if asset.upper() in shill_assets:
                self.logger.debug(
                    f"⛔ PassiveMM:shill_coin {asset} - only sells allowed, skipping passive BUY placement"
                )
                # Note: We still allow through to potentially place SELL orders
                # but we'll block BUY orders below

            # -------- 1) profitability/liquidity gate --------
            profitable = await self.shared_data_manager.fetch_profitable_symbols(
                min_trades=2, min_pnl_usd=Decimal("0.0"), lookback_days=7,
                source_filter=None, min_quote_volume=Decimal(self._min_quote_volume), refresh_interval=60
            )

            if trading_pair in profitable: # Breakpoint set here for debugging
                self.logger.info("PassiveMM profitable check passed",
                               extra={'product_id': product_id, 'trading_pair': trading_pair,
                                      'min_quote_volume': str(self._min_quote_volume)})
            else:
                self.logger.debug("Skipping non-profitable/illiquid symbol",
                                extra={'trading_pair': trading_pair, 'min_quote_volume': str(self._min_quote_volume)})
                return

            # -------- 2) active_symbols gate --------
            try:
                active_syms = await self.shared_data_manager.fetch_active_symbols(as_of_max_age_sec=6 * 3600)
            except Exception as e:

                self.logger.error(f"⚠️ fetch_active_symbols failed; skipping {trading_pair}: {e}", exc_info=True)
                return
            if trading_pair not in active_syms:
                self.logger.debug(f"⛔ Skipping {trading_pair} — not in active_symbols (leaderboard filter)")
                return
            self.logger.info(f"🧭 PassiveMM:active? {product_id}={trading_pair in active_syms} (fresh<=6h)")


            # -------- 3) build OrderData --------
            try:
                od = await self.tom.build_order_data(
                    source="passivemm", trigger="market_making", asset=asset, product_id=product_id
                )
            except Exception as exc:

                self.logger.error(f"❌ build_order_data failed for {trading_pair}: {exc}", exc_info=True)
                return
            if not od or not od.highest_bid or not od.lowest_ask:
                self.logger.debug(f"⛔ No viable book for {trading_pair}")
                return
            self.logger.info(f"🧭 PassiveMM:book {product_id} bid={od.highest_bid} ask={od.lowest_ask}")

            # -------- 4) spread/edge checks --------
            best_bid = Decimal(od.highest_bid)
            best_ask = Decimal(od.lowest_ask)
            if best_ask <= best_bid:
                self.logger.info(f"⛔ PassiveMM:crossed_or_locked {trading_pair} bid={best_bid} ask={best_ask}")
                return
            mid = (best_bid + best_ask) / Decimal("2")
            spread_pct = (best_ask - best_bid) / mid

            # -------- 5) steps / mins --------
            q_dec = int(od.quote_decimal)
            b_dec = int(od.base_decimal)
            price_step = Decimal("1").scaleb(-q_dec)
            size_step = Decimal("1").scaleb(-b_dec)

            min_base_size = Decimal(str(getattr(od, "min_base_size", "0") or "0"))
            min_quote_notional = Decimal(str(getattr(od, "min_quote_notional", "0") or "0"))

            # Adaptive requirement: max(global floor, 2*maker fee + buffer, 2*tick width)
            ignore_fees = os.getenv("PASSIVE_IGNORE_FEES_FOR_SPREAD", "false").lower() in ("1", "true", "yes")

            maker_fee = Decimal(str(getattr(od, "maker", "0") or "0"))
            tick_spread = (Decimal("2") * price_step) / mid
            fee_spread = (maker_fee * Decimal("2")) + self._edge_buffer_pct
            floor_spread = self._min_spread_pct
            min_spread_req = max(floor_spread, tick_spread) if ignore_fees else max(floor_spread, fee_spread, tick_spread)

            self.logger.info(
                f"🧭 passivemm:spread_req {product_id} floor={(floor_spread * 100):.3f}% "
                f"fees={(fee_spread * 100):.3f}% ticks={(tick_spread * 100):.3f}% → req={(min_spread_req * 100):.3f}%"
            )
            # Always show the computed spread and requirement
            if spread_pct < min_spread_req:
                self.logger.info(
                    f"⛔ passivemm:spread_too_tight {trading_pair} "
                    f"spread={(spread_pct * 100):.3f}% req={(min_spread_req * 100):.3f}% "
                    f"(bid={best_bid}, ask={best_ask})"
                )

                return
            self.logger.info(f"🧭 PassiveMM:spread_ok {product_id} {(spread_pct * 100):.3f}% >= req {(min_spread_req * 100):.3f}%")

            # -------- 5b) Pre-entry volatility check (NEW) --------
            # Check recent price movement to avoid trading flat/low-volatility symbols
            try:
                ohlcv = await self.ohlcv_manager.fetch_last_5min_ohlcv(trading_pair)
                if ohlcv and len(ohlcv) >= 5:
                    recent_candles = ohlcv[-5:]  # Last 5 candles (25 minutes)
                    recent_high = max([float(c[2]) for c in recent_candles])
                    recent_low = min([float(c[3]) for c in recent_candles])
                    recent_mid = (recent_high + recent_low) / 2
                    recent_range_pct = Decimal(str((recent_high - recent_low) / recent_mid))

                    # Require volatility >= spread requirement (ensures price actually moves)
                    if recent_range_pct < min_spread_req:
                        self.logger.info(
                            f"⛔ passivemm:insufficient_volatility {trading_pair} "
                            f"recent_range={(recent_range_pct * 100):.3f}% < required={(min_spread_req * 100):.3f}% "
                            f"(last 25min: high={recent_high:.4f}, low={recent_low:.4f})"
                        )
                        return
                    else:
                        self.logger.info(
                            f"✅ passivemm:volatility_ok {trading_pair} "
                            f"recent_range={(recent_range_pct * 100):.3f}% >= required={(min_spread_req * 100):.3f}%"
                        )
            except Exception as e:
                self.logger.debug(f"⚠️ Could not fetch OHLCV for volatility check on {trading_pair}: {e}")
                # Continue without volatility check if OHLCV unavailable

            # -------- 6) compute maker quotes --------
            buy_px = _to_step(best_bid + price_step, price_step, ROUND_DOWN)
            sell_px = _to_step(best_ask - price_step, price_step, ROUND_DOWN)
            realized_spread = (sell_px - buy_px) / mid
            if realized_spread < min_spread_req:
                self.logger.info(
                    f"⛔ PassiveMM:realized_spread_too_tight {product_id} "
                    f"realized={(realized_spread * 100):.3f}% req={(min_spread_req * 100):.3f}% "
                    f"(buy_px={buy_px}, sell_px={sell_px})"
                )
                return

            # Base sizes implied by your min fiat floor (used for initial suggestion only)
            min_fiat = Decimal(self._min_order_amount_fiat + 5)  # e.g., $10 or $60 add 5 for debugging passive order placement
            implied_buy_base = _to_step(min_fiat / buy_px, size_step, ROUND_DOWN)
            implied_sell_base = _to_step(min_fiat / sell_px, size_step, ROUND_DOWN)

            # -------- 7) balances + clamp SELL --------
            bal = await _balances(trading_pair)
            usd_bal = bal["usd"]
            base_bal = bal["base"]

            sell_sz = min(implied_sell_base, base_bal)
            sell_sz = _to_step(sell_sz, size_step, ROUND_DOWN)
            if min_base_size and sell_sz < min_base_size:
                self.logger.info(f"⛔ PassiveMM:sell_below_min {trading_pair} size={sell_sz} min={min_base_size}")
                sell_sz = Decimal("0")
            self.logger.info(
                f"🧭 PassiveMM:sizing {trading_pair} "
                f"min_fiat=${min_fiat} usd_bal=${usd_bal} "
                f"→ BUY=${(min(min_fiat, usd_bal * Decimal('0.95'))).quantize(Decimal('0.01'), rounding=ROUND_DOWN)}; "
                f"SELL={sell_sz} base (avail={base_bal})")

            # -------- 8) BUY notional (Option A via order_amount_fiat) --------
            # leave 5% buffer to avoid insuff-funds on fees/rounding
            buy_notional = min(min_fiat, usd_bal * Decimal("0.95"))
            # honor min notional if present
            if min_quote_notional and buy_notional < min_quote_notional:
                self.logger.info(f"⛔ PassiveMM:buy_below_min_notional {trading_pair} notional=${buy_notional} min=${min_quote_notional}")
                buy_notional = Decimal("0")
            buy_notional = buy_notional.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

            place_buy = buy_notional > 0
            place_sell = sell_sz > 0
            if not place_buy and not place_sell:
                self.logger.info(
                    f"⛔ PassiveMM:both_legs_blocked {trading_pair} "
                    f"buy_notional=${buy_notional} "
                    f"sell_sz={sell_sz} "
                    f"(usd_bal=${usd_bal}, base_bal={base_bal}, "
                    f"min_quote_notional=${min_quote_notional}, min_base_size={min_base_size})"
                    )
                return
            self.logger.info(f"🧭 PassiveMM:place? {product_id} BUY={place_buy} (${buy_notional}) SELL={place_sell} ({sell_sz})")

            # -------- 9) submit BUY first (via order_amount_fiat) --------
            if place_buy:
                # Block BUY for SHILL_COINS
                if asset.upper() in shill_assets:
                    self.logger.info(
                        f"⛔ PassiveMM:blocking_buy {trading_pair} - {asset} is in SHILL_COINS (only sells allowed)"
                    )
                    place_buy = False

            if place_buy:
                try:
                    buy_od = copy.deepcopy(od)
                    buy_od.side = "buy"
                    buy_od.type = "limit"
                    buy_od.price = buy_px  # adapter expects strings fine
                    buy_od.order_amount_fiat = buy_notional  # <-- key for  handle_order()
                    # make sure these exist for the adjust functions:
                    buy_od.base_avail_balance = getattr(buy_od, "base_avail_balance", Decimal("0"))

                    buy_od.post_only = True
                    buy_od.time_in_force = "GTC"
                    buy_od.strategy_tag = "passivemm"
                    buy_od.source = "passivemm"

                    self.logger.info("PassiveMM BUY order submitting",
                                   extra={'product_id': product_id, 'notional_usd': str(buy_notional), 'price': str(buy_px)})
                    res_buy = await self.tom.place_order(buy_od)
                    self.logger.debug("PassiveMM BUY order response",
                                    extra={'product_id': product_id, 'response': str(res_buy)})
                    ok_buy, order_id_buy, raw_buy = _order_result(res_buy)

                    v_only, reason = _validated_only(raw_buy)

                    if v_only:
                        self.logger.info(
                            f"ℹ️ PASSIVE BUY validated-only {trading_pair}: {reason}; "
                            f"order_amount_fiat=${buy_notional} @ {buy_px}"
                        )
                    elif ok_buy and order_id_buy:
                        await self.shared_data_manager.save_passive_order(
                            order_id=order_id_buy, symbol=trading_pair, side="buy",
                            order_data=getattr(buy_od, "to_dict", lambda: {
                                "price": str(buy_px), "order_amount_fiat": str(buy_notional),
                                "post_only": True, "tif": "GTC", "source": "passivemm"
                            })()
                        )
                        self.logger.info(f"✅ Placed PASSIVE BUY {trading_pair} ${buy_notional} @ {buy_px} (order_id={order_id_buy})")
                    else:
                        self.logger.error(
                            f"❌ Failed to place PASSIVE BUY for {trading_pair} — "
                            f"normalized=(ok={ok_buy}, id={order_id_buy}) raw={raw_buy!r}"
                        )
                except Exception as e:
                    self.logger.error(f"❌ Exception placing PASSIVE BUY for {trading_pair}: {e}", exc_info=True)

            # -------- 10) submit SELL (via base_avail_balance) --------
            if place_sell:
                # Block SELL for HODL assets
                if asset.upper() in hodl_assets:
                    self.logger.info(
                        f"⛔ PassiveMM:blocking_sell {trading_pair} - {asset} is in HODL (only buys allowed)"
                    )
                    place_sell = False

            if place_sell:
                try:
                    sell_od = copy.deepcopy(od)
                    sell_od.side = "sell"
                    sell_od.type = "limit"
                    sell_od.price = sell_px
                    # drive sizing through base_avail_balance for handle_order()
                    sell_od.base_avail_balance = sell_sz
                    # if your adjust functions also consult order_amount_fiat on SELL, keep it empty/zero
                    sell_od.order_amount_fiat = Decimal("0")

                    sell_od.post_only = True
                    sell_od.time_in_force = "GTC"
                    sell_od.strategy_tag = "passivemm"
                    sell_od.source = "passivemm"

                    self.logger.info("PassiveMM SELL order submitting",
                                   extra={'product_id': product_id, 'size': str(sell_sz), 'price': str(sell_px)})

                    res_sell = await self.tom.place_order(sell_od)
                    ok_sell, order_id_sell, raw_sell = _order_result(res_sell)
                    v_only, reason = _validated_only(raw_sell)

                    if v_only:
                        self.logger.info(
                            f"ℹ️ PASSIVE SELL validated-only {trading_pair}: {reason}; "
                            f"size={sell_sz} @ {sell_px}"
                        )
                    elif ok_sell and order_id_sell:
                        await self.shared_data_manager.save_passive_order(
                            order_id=order_id_sell, symbol=trading_pair, side="sell",
                            order_data=getattr(sell_od, "to_dict", lambda: {
                                "price": str(sell_px), "size": str(sell_sz),
                                "post_only": True, "tif": "GTC", "source": "passivemm"
                            })()
                        )
                        self.logger.info("Placed PASSIVE SELL order",
                                       extra={'trading_pair': trading_pair, 'size': str(sell_sz),
                                              'price': str(sell_px), 'order_id': order_id_sell})
                    else:
                        self.logger.error(
                            f"❌ Failed to place PASSIVE SELL for {trading_pair} — "
                            f"normalized=(ok={ok_sell}, id={order_id_sell}) raw={raw_sell!r}"
                        )
                except Exception as e:
                    self.logger.error(f"❌ Exception placing PASSIVE SELL for {trading_pair}: {e}", exc_info=True)

        except asyncio.CancelledError:
            self.logger.warning(f"🛑 place_passive_orders cancelled for {product_id}")
            raise
        except Exception as e:
            self.logger.error(f"❌ place_passive_orders fatal error for {product_id}: {e}", exc_info=True)

    async def _submit_passive_sell(self, symbol: str, od: OrderData, price: Decimal, reason: str, note: str = ""):
        try:
            buy_id = self.passive_order_tracker.get(symbol, {}).get("buy")
            if buy_id:
                await self.order_manager.cancel_order(buy_id, symbol)

            sell_od = self._clone_order_data(od, side="sell", trigger=f"passive_{reason}", source="passivemm")
            sell_od.type = "limit"
            sell_od.adjusted_price = price.quantize(Decimal(f'1e-{od.quote_decimal}'))
            # Use total_balance_crypto to sell entire position (no dust left behind)
            sell_od.adjusted_size = od.total_balance_crypto
            sell_od.cost_basis = (sell_od.adjusted_price * sell_od.adjusted_size).quantize(
                Decimal(f'1e-{od.quote_decimal}'), rounding=ROUND_HALF_UP
            )
            sell_od.trigger = {"trigger": f"passive_{reason}", "trigger_note": note}

            if not self._passes_balance_check(sell_od):
                self.logger.warning(f"⚠️ Insufficient balance to place {reason} SELL for {symbol}")
                return

            if reason in self.passive_order_tracker.get(symbol, {}):
                return

            ok, res = await self.tom.place_order(sell_od)
            if ok:
                sell_id = res.get('details', {}).get('order_id')
                self.logger.info(f"✅ {reason.upper()} SELL placed for {symbol} @ {sell_od.adjusted_price}")
                await self._track_passive_order(symbol, reason, sell_id, sell_od)

            else:
                self.logger.error(f"❌ Failed to place {reason.upper()} SELL for {symbol}: {res}")
        except Exception as e:
            self.logger.error(f"❌ Failed to submit {reason.upper()} SELL for {symbol}: {e}", exc_info=True)

    async def reload_persisted_passive_orders(self):
        if not self.shared_data_manager:
            return

        rows = await self.shared_data_manager.load_all_passive_orders()
        for symbol, side, od_dict in rows:
            try:
                if isinstance(od_dict, str):
                    od_dict = json.loads(od_dict)  # ✅ Convert JSON string to dict

                od = OrderData.from_dict(od_dict)
                entry = self.passive_order_tracker.setdefault(symbol, {})
                entry[side] = od.order_id
                entry["order_data"] = od
                entry["timestamp"] = time.time()
                self.logger.info(f"🔁 Restored passive order: {symbol} {side}")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to restore passive order for {symbol}/{side}: {e}", exc_info=True)

    async def _quote_passive_buy(self, od: OrderData, trading_pair: str, tick: Decimal):
        """
        Quotes a passive BUY order with size scaled by spread quality.
        """
        try:
            quote_od = self._clone_order_data(od, side="buy", post_only=True)

            # ✅ Step 3: Dynamic size scaling based on spread quality
            min_required_spread = max(self.fee["maker"] * Decimal("2.0"), self._min_spread_pct)
            spread_factor = max(Decimal("1.0"), (od.spread / (od.limit_price * min_required_spread)))

            # Cap size increase to avoid overexposure (e.g., max 3x baseline)
            spread_factor = min(spread_factor, Decimal("3.0"))

            target_buy_value = (self._min_buy_value * spread_factor).quantize(Decimal("0.01"))
            quote_od.order_amount_fiat = target_buy_value

            total_value = quote_od.price * quote_od.total_balance_crypto

            # Original logic preserved, but with adjusted price placement
            if total_value <= self._min_buy_value:
                target_px = min(od.highest_bid, od.lowest_ask - tick)
                quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)

            await self._finalize_passive_order(quote_od, trading_pair)
        except Exception as e:
            self.logger.error(f"❌ Error in _quote_passive_buy for {trading_pair}: {e}", exc_info=True)

    async def _quote_passive_sell(
            self, od: OrderData, trading_pair: str, tick: Decimal, average_close: Decimal | None
    ):
        """
        Quotes a passive SELL order with size scaled by spread quality.
        """
        try:
            quote_od = self._clone_order_data(od, side="sell", post_only=True)

            total_value = quote_od.price * quote_od.total_balance_crypto
            if total_value <= self._min_order_amount_fiat:
                return

            # ✅ Step 3: Dynamic size scaling based on spread quality
            min_required_spread = max(self.fee["maker"] * Decimal("2.0"), self._min_spread_pct)
            spread_factor = max(Decimal("1.0"), (od.spread / (od.limit_price * min_required_spread)))
            spread_factor = min(spread_factor, Decimal("3.0"))

            target_sell_value = (self._min_order_amount_fiat * spread_factor).quantize(Decimal("0.01"))
            quote_od.order_amount_fiat = target_sell_value

            min_profit_pct = self._take_profit + self.fee["maker"]
            min_sell_price = self._break_even_prices(Decimal(od.filled_price))[0] * (Decimal('1') + self._take_profit)
            current_price = od.lowest_ask

            if average_close and average_close < current_price:
                self.logger.info(
                    f"📉 Skipping passive SELL for {trading_pair} — average_close ({average_close}) < current_price ({current_price})"
                )
                return

            target_px = max(min_sell_price, od.highest_bid + tick)
            quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)

            await self._finalize_passive_order(quote_od, trading_pair)
        except Exception as e:
            self.logger.error(f"❌ Error in _quote_passive_sell for {trading_pair}: {e}", exc_info=True)

    async def _finalize_passive_order(self, quote_od: OrderData, trading_pair: str):
        # Set price/size/cost basis
        price = quote_od.adjusted_price
        if not price:
            price = quote_od.limit_price
        fiat = quote_od.order_amount_fiat
        base_deci = quote_od.base_decimal
        quote_deci = quote_od.quote_decimal
        base_quantizer = Decimal("1").scaleb(-base_deci)
        quote_quantizer = Decimal("1").scaleb(-quote_deci)

        quote_od.adjusted_size_fiat = fiat
        quote_od.adjusted_size = self.shared_utils_precision.safe_quantize((fiat / price), base_quantizer)
        quote_od.cost_basis = self.shared_utils_precision.safe_quantize(price * quote_od.adjusted_size,quote_quantizer)
        spread = Decimal(quote_od.spread)
        quote_od.spread = self.shared_utils_precision.safe_quantize(spread, quote_quantizer)
        quote_od.trigger = {"trigger": f"passive_{quote_od.side}", "trigger_note": f"price:{price}"}
        quote_od.source = 'passivemm'

        if not self._passes_balance_check(quote_od):
            return

        self.logger.info("Placing passive order",
                        extra={'source': quote_od.source, 'trading_pair': quote_od.trading_pair,
                               'side': quote_od.side.upper(), 'size': str(quote_od.adjusted_size),
                               'price': str(price), 'spread': str(quote_od.spread)})

        ok, res = await self.tom.place_order(quote_od)

        if ok:
            order_id = res.get("order_id") or res.get("details", {}).get("order_id")
            if order_id:
                quote_od.open_orders = True
                quote_od.order_id = order_id
                self.logger.info("Saving passive order",
                               extra={'side': quote_od.side.upper(), 'trading_pair': trading_pair,
                                      'price': str(price), 'order_id': order_id})
                await self._track_passive_order(trading_pair, quote_od.side, order_id, quote_od)
            else:
                self.logger.warning(f"⚠️ No order_id returned in order placement response: {res}")
            self.logger.info(f"✅ Passive {quote_od.side.upper()} {trading_pair} @ {price}")
        else:
            # New unified structure for failed responses
            reason = res.get("reason", "UNKNOWN")
            msg = res.get("message", "No message")
            attempts = res.get("attempts", "N/A")

            self.logger.warning("Passive order attempt failed",
                              extra={'side': quote_od.side.upper(), 'trading_pair': trading_pair,
                                     'attempts': str(attempts), 'reason': reason, 'message': msg})

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _break_even_prices(self, entry_price: Decimal) -> tuple[Decimal, Decimal]:
        """Return (maker_exit_be, taker_exit_be) prices that net to ~zero after entry+exit fees.
        maker_exit_be: paid maker on entry, will pay maker on exit (post‑only TP)
        taker_exit_be: paid maker on entry, will pay taker on exit (market/urgent SL)
        """
        maker = self.fee["maker"]
        taker = self.fee["taker"]
        one = Decimal("1")
        be_maker_exit = entry_price * (one + maker) * (one + maker)
        be_taker_exit = entry_price * (one + maker) * (one + taker)
        return (be_maker_exit, be_taker_exit)

    def _compute_inventory_bias(
        self, *, asset_value: Decimal, usd_value: Decimal, spread: Decimal
    ) -> Decimal:
        """Return a ± bias (in price units) to skew quotes."""
        total = asset_value + usd_value
        if total == 0:
            return Decimal("0")
        imbalance = (usd_value - asset_value) / total  # +ve => long USD
        return imbalance * self._inventory_bias_factor * spread

    def _passes_balance_check(self, od: OrderData) -> bool:
        """Ensure affordability of the order about to placed."""
        maker_fee = self.fee["maker"]
        if od.cost_basis < self._min_order_amount_fiat :
            return False
        if od.side == "buy":
            fee_multiplier = Decimal(1) + maker_fee
            cost = od.cost_basis * fee_multiplier
            if od.usd_avail_balance < cost:
                return False
        else:  # sell
            # Determine intended order size in base units
            if getattr(od, 'order_amount_crypto', None):
                intended_qty = od.order_amount_crypto
            elif getattr(od, 'order_amount_fiat', None) and getattr(od, 'limit_price', None):
                intended_qty = od.order_amount_fiat / od.limit_price
            else:
                intended_qty = Decimal('0')
            if od.base_avail_balance < intended_qty:
                return False
        return True

    def _clone_order_data(self, od: OrderData, **overrides) -> OrderData:
        """Deep‑copy `od` and update provided attributes."""
        cloned = copy.deepcopy(od)
        for k, v in overrides.items():
            setattr(cloned, k, v)
        return cloned

    async def _track_passive_order(self, symbol: str, side: str, order_id: str, od: OrderData = None) -> None:
        entry = self.passive_order_tracker.setdefault(symbol, {})
        entry[side] = order_id
        if od:
            entry["order_data"] = od
            entry["peak_price"] = od.filled_price or od.limit_price  # Initialize to purchase price
        entry["timestamp"] = time.time()

        if od:
            await self.shared_data_manager.save_passive_order(order_id, symbol, side, od.to_dict())

    # ------------------------------------------------------------------
    # Housekeeping – cancel and refresh stale quotes
    # ------------------------------------------------------------------
    async def _watchdog(self) -> None:
        """
        Optimized background coroutine:
        ✅ Runs lightweight cleanup every 30s.
        ✅ Spawns per-symbol async tasks for active buy order monitoring.
        """
        counter = 0

        while True:
            try:
                await asyncio.sleep(30)  # Reduced frequency for global cleanup
                now = time.time()

                # 🔁 Periodic cleanup of expired orders
                for symbol, entry in list(self.passive_order_tracker.items()):
                    if now - entry.get("timestamp", 0) >= self._max_lifetime:
                        # Cancel & cleanup expired orders
                        for side in ("buy", "sell"):
                            old_id = entry.get(side)
                            if isinstance(old_id, dict):
                                old_id = old_id.get("order_id")

                            if old_id:
                                try:
                                    await self.order_manager.cancel_order(old_id, symbol)
                                except Exception as exc:
                                    self.logger.warning(
                                        f"⚠️ Failed to cancel expired {side} {symbol} (ID:{old_id}): {exc}",
                                        exc_info=True
                                    )
                                try:
                                    await self.shared_data_manager.remove_passive_order(old_id)
                                except Exception as exc:
                                    self.logger.warning(
                                        f"⚠️ Failed to remove passive order {old_id}: {exc}",
                                        exc_info=True
                                    )
                            else:
                                self.logger.info(
                                    f"ℹ️ No order_id for expired {side} {symbol}, skipping cancel."
                                )

                        self.passive_order_tracker.pop(symbol, None)
                        self.logger.info("Cleaned expired passive order", extra={'symbol': symbol})

                    else:
                        # ✅ Ensure active buys are monitored by a dedicated task
                        if "buy" in entry and not entry.get("monitor_task"):
                            entry["monitor_task"] = asyncio.create_task(
                                self._monitor_active_symbol(symbol, entry)
                            )

                # 🔁 Reconcile DB every ~5 min (10 cycles at 30s each)
                counter += 1
                if counter % 10 == 0:
                    await self.shared_data_manager.reconcile_passive_orders()

            except Exception as ex:
                self.logger.error(f"⚠️ Watchdog error {ex}", exc_info=True)

    async def _monitor_active_symbol(self, symbol: str, entry: dict):
        """
        Per-symbol monitoring loop for active buy orders.
        Runs frequently (every 5s) and stops automatically when order is gone.
        """
        self.logger.info(f"▶️ Starting monitor for active order: {symbol}")

        try:
            while symbol in self.passive_order_tracker and "buy" in entry:
                od = entry.get("order_data")
                if isinstance(od, OrderData):
                    await self.monitor_passive_position(symbol, od)

                await asyncio.sleep(5)

            self.logger.info(f"⏹️ Monitor stopped for {symbol}")

        except asyncio.CancelledError:
            self.logger.info(f"⏹️ Monitor cancelled for {symbol}")
        except Exception as e:
            self.logger.error(f"❌ Error in _monitor_active_symbol for {symbol}: {e}", exc_info=True)
        finally:
            entry["monitor_task"] = None

    async def live_performance_tracker(self, interval: int = 300, lookback_days: int = 7):
        """
        Logs live PassiveMM performance every `interval` seconds by linking SELLs to PassiveMM BUYs.
        """
        await asyncio.sleep(5)
        while True:
            try:
                cutoff_time = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                trades = await self.shared_data_manager.trade_recorder.fetch_recent_trades()  # fetch all recent

                if not trades:
                    self.logger.info("📉 No recent trades found for live tracker.")
                    await asyncio.sleep(interval)
                    continue

                df = pd.DataFrame([t.__dict__ for t in trades])
                df['order_time'] = pd.to_datetime(df['order_time'])
                df = df[df['order_time'] >= cutoff_time].copy()

                # ✅ Step 1: Get all PassiveMM BUY order IDs
                passive_buy_ids = set(df[(df['side'] == 'buy') & (df['source'] == "passivemm")]['order_id'])

                # ✅ Step 2: Filter SELLs whose parent_ids contain any PassiveMM BUY order_id
                def is_passive_sell(row):
                    parents = row.get('parent_ids') or []
                    if isinstance(parents, str):
                        parents = parents.strip('{}').split(',')
                    return any(p.strip() in passive_buy_ids for p in parents)

                passive_sells = df[(df['side'] == 'sell') & df.apply(is_passive_sell, axis=1)].copy()

                # ✅ Step 3: Calculate stats using PnL (not realized_profit to avoid cumulative issues)
                total_trades = len(passive_sells)
                profitable_trades = passive_sells[passive_sells['pnl_usd'] > 0]
                win_rate = (len(profitable_trades) / total_trades * 100) if total_trades else 0
                total_pnl = passive_sells['pnl_usd'].sum()
                avg_pnl = passive_sells['pnl_usd'].mean() if total_trades else 0

                top_symbols = (
                    passive_sells.groupby('symbol')['pnl_usd']
                    .sum()
                    .sort_values(ascending=False)
                    .head(5)
                    .to_dict()
                )
                if total_trades >0:
                    self.logger.info("PassiveMM live performance tracker",
                                   extra={'lookback_days': lookback_days, 'total_trades': total_trades,
                                          'win_rate': f"{win_rate:.2f}%", 'total_pnl_usd': f"{total_pnl:+.2f}",
                                          'avg_pnl_per_trade': f"{avg_pnl:+.2f}", 'top_symbols': str(top_symbols)})

            except Exception as e:
                self.logger.error(f"❌ Live performance tracker error: {e}", exc_info=True)

            await asyncio.sleep(interval)

