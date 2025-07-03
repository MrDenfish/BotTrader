
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

import asyncio
import copy
import time
import  json
from webhook.webhook_validate_orders import OrderData
from Shared_Utils.enum import ExitCondition
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Any, Optional

# ---------------------------------------------------------------------------
# Type aliases – keep it loose here, real project will import your dataclass
# ---------------------------------------------------------------------------

class PassiveOrderManager:
    """A lightweight maker‑side quoting engine."""

    #: How wide the spread must be *before* we even attempt to quote.
    DEFAULT_MIN_SPREAD_PCT = Decimal("0.0025")  # 0.25%  # 0.20 %

    #: Cancel & refresh resting orders after this many seconds.
    DEFAULT_MAX_LIFETIME = 600 # 10 minutes

    #: How aggressively to bias quotes when inventory is skewed.
    INVENTORY_BIAS_FACTOR = Decimal("0.10")  # ≤ 25 % of current spread Lower inventory skew

    def __init__(self, config, ccxt_api, coinbase_api, exchange, ohlcv_manager, shared_data_manager, shared_utils_color, shared_utils_utility, shared_utils_precision,
                 trade_order_manager, order_manager, logger, min_spread_pct, fee_cache: Dict[str, Decimal], *, max_lifetime: int | None = None) -> None:
        self.config = config
        self.tom = trade_order_manager  # shorthand inside class
        self.order_manager = order_manager
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_color = shared_utils_color
        self.shared_data_manager = shared_data_manager
        self.logger = logger
        self.fee = fee_cache  # expects {'maker': Decimal, 'taker': Decimal}

        self.min_spread_pct = min_spread_pct or self.DEFAULT_MIN_SPREAD_PCT
        self.max_lifetime = max_lifetime or self.DEFAULT_MAX_LIFETIME

        # {symbol: {"buy": order_id, "sell": order_id, "timestamp": float}}
        self.passive_order_tracker: Dict[str, Dict[str, Any]] = {}

        self.ccxt_api = ccxt_api
        self.exchange = exchange
        self.coinbase_api = coinbase_api

        # Trading parameters
        self._stop_loss = Decimal(config.stop_loss)
        self._take_profit = Decimal(config.take_profit)
        self._trailing_percentage = Decimal(config.trailing_percentage)
        self._trailing_stop = Decimal(config.trailing_stop)
        self._min_order_amount_fiat = Decimal(config.min_order_amount_fiat)
        self._min_buy_value = Decimal(config.min_buy_value)

        # Data managers
        self.ohlcv_manager = ohlcv_manager

        # launch watchdog
        asyncio.create_task(self._watchdog())

    _fee_lock: asyncio.Lock = asyncio.Lock()
    async def update_fee_cache(self, new_fee: Dict[str, Decimal]) -> None:
        """Hot-swap maker/taker fees atomically."""
        async with self._fee_lock:
            self.fee = new_fee
    # ------------------------------------------------------------------
    # Public entry – call this once per symbol you want to quote
    # ------------------------------------------------------------------

    async def monitor_passive_position(self, symbol: str, od: OrderData):
        """
        Monitors a passive BUY order. If price moves unfavorably or favorably:
        - Triggers a stop-loss (SL)
        - Triggers a take-profit (TP)
        - Triggers a trailing stop-loss (TSL)
        """
        try:
            quote_deci = od.quote_decimal
            base_deci = od.base_decimal

            # Evaluate exit conditions
            decision = await self.evaluate_exit_conditions(od)
            if decision:
                self.logger.info(f"✅ Exit condition met: {decision['trigger']}")
            ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, 'public', symbol)
            # reformat deci size:
            quote_quantizer = Decimal("1").scaleb(-quote_deci)
            base_quantizer = Decimal("1").scaleb(-base_deci)
            current_price = Decimal(ticker_data['last'])
            current_price = self.shared_utils_precision.safe_quantize(current_price, quote_quantizer)
            # spread =  self.shared_utils_precision.safe_quantize(od.spread, quote_quantizer)

            entry = self.passive_order_tracker.get(symbol, {})
            peak_price = entry.get("peak_price", od.limit_price)

            print(f" 🔷 Passive Active Order: {symbol} Current price:{current_price}  Entry:{entry}  peak price:{peak_price}  🔷")
            # Update peak price for TSL tracking
            if current_price > peak_price:
                entry["peak_price"] = current_price
                peak_price = current_price

            # ----- STOP-LOSS (SL) -----
            normalized_spread_pct = (
                od.spread / od.limit_price if od.limit_price and od.spread else Decimal("0")
            )
            dynamic_sl_pct = max(self.fee["taker"] * Decimal(2), normalized_spread_pct * Decimal("1.25"))
            stop_price = od.limit_price * (Decimal("1.0") - dynamic_sl_pct)
            stop_price = self.shared_utils_precision.safe_quantize(stop_price, quote_quantizer)


            if current_price <= stop_price:
                self.logger.warning(f"🔻 Stop-loss triggered for {symbol} @ {current_price} (SL threshold: {stop_price})")
                await self._submit_passive_sell(
                    symbol,
                    od,
                    current_price,
                    reason="stop_loss",
                    note=f"SL threshold: {stop_price}"
                )
                return

            # ----- TAKE-PROFIT (TP) -----
            take_profit_price = od.limit_price * (Decimal("1.0") + self._take_profit)
            if current_price >= take_profit_price:
                self.logger.info(f"📈 Take-profit triggered for {symbol} @ {current_price} (TP threshold: {take_profit_price})")
                await self._submit_passive_sell(
                    symbol,
                    od,
                    current_price,
                    reason="take_profit",
                    note=f"TP threshold: {take_profit_price}"
                )
                return

            # ----- TRAILING STOP-LOSS (TSL) -----
            trailing_stop_price = peak_price * (Decimal("1.0") - self._trailing_percentage)
            if current_price <= trailing_stop_price:
                self.logger.warning(
                    f"🔻 Trailing SL triggered for {symbol} @ {current_price} (peak: {peak_price}, trailing stop: {trailing_stop_price})")
                await self._submit_passive_sell(
                    symbol,
                    od,
                    current_price,
                    reason="trailing_stop",
                    note=f"Peak: {peak_price}, trailing stop: {trailing_stop_price}"
                )
                return

        except Exception as e:
            self.logger.error(f"❌ Error in monitor_passive_position() for {symbol}: {e}", exc_info=True)

    async def evaluate_exit_conditions(self, od: OrderData) -> Optional[dict]:
        """
        Evaluate stop-loss, take-profit, and return trigger info if any conditions are met.
        """
        symbol = od.trading_pair
        side = od.side.upper()
        base = od.base_currency
        filled_price = od.filled_price
        available_qty = od.available_to_trade_crypto
        current_price = od.lowest_ask if side == "SELL" else od.highest_bid

        if current_price <= 0 or available_qty <= 0:
            self.logger.warning(f"Skipping {symbol}: invalid current_price or quantity.")
            return None

        estimated_value = available_qty * current_price
        original_cost = available_qty * filled_price
        raw_profit = estimated_value - original_cost
        profit_pct = (raw_profit / original_cost) * 100 if original_cost > 0 else Decimal(0)

        min_profit_pct = Decimal(self.tom.config.get("min_profit_pct", "1.0"))
        max_loss_pct = Decimal(self.tom.config.get("max_loss_pct", "-5.0"))

        if profit_pct >= min_profit_pct:
            return {
                "trigger": {
                    "trigger": ExitCondition.TAKE_PROFIT.value,
                    "trigger_note": f"Profit +{profit_pct:.2f}% >= {min_profit_pct}%",
                }
            }

        if profit_pct <= max_loss_pct:
            return {
                "trigger": {
                    "trigger": ExitCondition.STOP_LOSS.value,
                    "trigger_note": f"Loss {profit_pct:.2f}% <= {max_loss_pct}%",
                }
            }

        return None

    async def place_passive_orders(self, asset: str, product_id: str) -> None:
        """
        Attempt to quote both sides of the book for *product_id*.
        Will return silently if market conditions aren't favorable.
        """
        trading_pair = product_id.replace("/", "-")

        try:
            od: OrderData | None = await self.tom.build_order_data(
                source="PassiveMM",
                trigger="market_making",
                asset=asset,
                product_id=product_id,
            )
        except Exception as exc:
            self.logger.error(f"❌ build_order_data failed for {trading_pair}: {exc}", exc_info=True)
            return

        if not od or od.highest_bid == 0 or od.lowest_ask == 0:
            return  # No usable order book

        spread = od.lowest_ask - od.highest_bid
        od.spread = spread

        mid_price = (od.lowest_ask + od.highest_bid) / 2
        spread_pct = spread / mid_price
        price = od.limit_price or od.price

        required_edge = self.fee["maker"] * Decimal("2.5")
        min_required_spread = max(required_edge, self.min_spread_pct)
        if spread_pct < min_required_spread:
            print(
                f"⛔ Skipping {trading_pair} — Spread {spread_pct:.4%} < threshold {min_required_spread:.4%}"
            )
            return

        try:
            tick = self.shared_utils_precision.safe_convert(od.quote_increment, od.quote_decimal)
            pct_nudge = Decimal("0.3") / Decimal("100")

            # Fetch OHLCV once for use in SELL side
            try:
                oldest_close, latest_close, average_close = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id)
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ Failed to fetch OHLCV for {trading_pair}, skipping SELL: {exc}", exc_info=True
                )
                oldest_close = latest_close = average_close = None

            await self._quote_passive_buy(od, trading_pair, tick)
            await self._quote_passive_sell(od, trading_pair, tick, average_close)

        except Exception as exc:
            self.logger.error(f"❌ Error in passive MM for {trading_pair}: {exc}", exc_info=True)

    async def _submit_passive_sell(self, symbol: str, od: OrderData, price: Decimal, reason: str, note: str = ""):
        try:
            buy_id = self.passive_order_tracker.get(symbol, {}).get("buy")
            if buy_id:
                await self.order_manager.cancel_order(buy_id, symbol)

            sell_od = self._clone_order_data(od, side="sell", trigger=f"passive_{reason}", source="PassiveMM")
            sell_od.type = "limit"
            sell_od.adjusted_price = price.quantize(Decimal(f'1e-{od.quote_decimal}'))
            sell_od.adjusted_size = od.available_to_trade_crypto
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
        quote_od = self._clone_order_data(od, side="buy", post_only=True)
        total_value = quote_od.price * quote_od.total_balance_crypto
        if total_value <= self._min_buy_value:
            target_px = min(od.highest_bid, od.lowest_ask - tick)
            quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)
            await self._finalize_passive_order(quote_od, trading_pair)

    async def _quote_passive_sell(
            self, od: OrderData, trading_pair: str, tick: Decimal, average_close: Decimal | None
    ):
        quote_od = self._clone_order_data(od, side="sell", post_only=True)
        total_value = quote_od.price * quote_od.total_balance_crypto
        if total_value <= self._min_order_amount_fiat:
            return

        min_profit_pct = self._take_profit + self.fee["maker"]
        min_sell_price = od.price * (Decimal("1.0") + min_profit_pct)
        current_price = od.lowest_ask

        if average_close and average_close < current_price:
            self.logger.info(
                f"📉 Skipping passive SELL for {trading_pair} — average_close ({average_close}) < current_price ({current_price})"
            )
            return

        target_px = max(min_sell_price, od.highest_bid + tick)
        quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)
        await self._finalize_passive_order(quote_od, trading_pair)

    async def _finalize_passive_order(self, quote_od: OrderData, trading_pair: str):
        # Set price/size/cost basis
        price = quote_od.adjusted_price
        fiat = quote_od.order_amount_fiat
        base_deci = quote_od.base_decimal
        quote_deci = quote_od.quote_decimal

        quote_od.adjusted_size_fiat = fiat
        quote_od.adjusted_size = (fiat / price).quantize(
            Decimal(f'1e-{base_deci}'), rounding=ROUND_DOWN
        )
        quote_od.cost_basis = (price * quote_od.adjusted_size).quantize(
            Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP
        )
        quote_quantizer = Decimal("1").scaleb(-quote_deci)
        spread = Decimal(quote_od.spread)
        quote_od.spread = self.shared_utils_precision.safe_quantize(spread, quote_quantizer)
        quote_od.trigger = {"trigger": f"passive_{quote_od.side}", "trigger_note": f"price:{price}"}
        quote_od.source = 'PassiveMM'

        if not self._passes_balance_check(quote_od):
            return

        print(f"📈 Placing Passive order: {quote_od}")

        ok, res = await self.tom.place_order(quote_od)

        if ok:
            order_id = res.get("order_id") or res.get("details", {}).get("order_id")
            if order_id:
                quote_od.open_orders = True
                quote_od.order_id = order_id
                print(f"✅ Saving Passive order: {quote_od.side.upper()} {trading_pair} @ {price}")
                await self._track_passive_order(trading_pair, quote_od.side, order_id, quote_od)
            else:
                self.logger.warning(f"⚠️ No order_id returned in order placement response: {res}")
            self.logger.info(f"✅ Passive {quote_od.side.upper()} {trading_pair} @ {price}")
        else:
            # New unified structure for failed responses
            reason = res.get("reason", "UNKNOWN")
            msg = res.get("message", "No message")
            attempts = res.get("attempts", "N/A")

            print(f"⚠️ Passive {quote_od.side.upper()} {trading_pair} attempt {attempts} failed — Reason: {reason} | Msg: {msg}")
            self.logger.warning(f"⚠️ Passive {quote_od.side.upper()} failed for {trading_pair}: {msg}", exc_info=True)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _compute_inventory_bias(
        self, *, asset_value: Decimal, usd_value: Decimal, spread: Decimal
    ) -> Decimal:
        """Return a ± bias (in price units) to skew quotes."""
        total = asset_value + usd_value
        if total == 0:
            return Decimal("0")
        imbalance = (usd_value - asset_value) / total  # +ve => long USD
        return imbalance * self.INVENTORY_BIAS_FACTOR * spread

    def _passes_balance_check(self, od: OrderData) -> bool:
        """Ensure affordability of the order about to placed."""
        maker_fee = self.fee["maker"]
        if od.cost_basis < self._min_order_amount_fiat :
            return False
        if od.side == "buy":
            fee_multiplier = Decimal(1) + self.fee["maker"]
            cost = od.cost_basis * fee_multiplier
            if od.usd_avail_balance < cost:
                return False
        else:  # sell
            if od.base_avail_balance  <= od.total_balance_crypto:
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
        """Background coroutine that clears expired resting orders and reconciles DB."""
        counter = 0  # to track periodic execution

        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()

                # Passive order cleanup logic
                if len(self.passive_order_tracker)>0:
                    print(self.shared_utils_color.format(f" {self.passive_order_tracker}",self.shared_utils_color.MAGENTA))

                for symbol, entry in list(self.passive_order_tracker.items()):
                    if now - entry.get("timestamp", 0) < self.max_lifetime:
                        # Monitor active BUYs
                        if "buy" in entry:
                            od = entry.get("order_data")
                            if isinstance(od, OrderData):
                                await self.monitor_passive_position(symbol, od)
                        continue

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
                                    f"⚠️ Failed to cancel expired {side} {symbol} (ID: {old_id}): {exc}", exc_info=True
                                )
                            try:
                                await self.shared_data_manager.remove_passive_order(old_id)
                            except Exception as exc:
                                self.logger.warning(
                                    f"⚠️ Failed to remove passive order {old_id} from DB: {exc}", exc_info=True
                                )
                        else:
                            self.logger.info(
                                f"ℹ️ No order_id for expired {side} {symbol} — skipping cancel, attempting DB cleanup"
                            )

                    self.passive_order_tracker.pop(symbol, None)
                    print(f"🔍 Monitoring: {list(self.passive_order_tracker.keys())}")

                # 🔁 Reconcile DB every 12 cycles (~1 min)
                counter += 1
                if counter % 12 == 0:
                    await self.shared_data_manager.reconcile_passive_orders()


            except Exception as ex:
                self.logger.error(f"Watchdog error {ex}", exc_info=True)

