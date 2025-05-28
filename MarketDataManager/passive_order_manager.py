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
from webhook.webhook_validate_orders import OrderData
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Any, Tuple

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

    MIN_ORDER_COST_BASIS = Decimal("25.00")

    def __init__(self, config, ccxt_api, coinbase_api, exchange, trade_order_manager, order_manager, logger, min_spread_pct, fee_cache: Dict[str, Decimal], *,
                  max_lifetime: int | None = None,) -> None:
        self.config = config
        self.tom = trade_order_manager  # shorthand inside class
        self.order_manager = order_manager
        self.logger = logger
        self.fee = fee_cache  # expects {'maker': Decimal, 'taker': Decimal}

        self.min_spread_pct = min_spread_pct or self.DEFAULT_MIN_SPREAD_PCT
        self.min_order_cost_basis = self.MIN_ORDER_COST_BASIS
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
        """Continuously monitor a passive BUY order and simulate stop-limit exit if needed."""
        try:
            # Retrieve the latest market price
            ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, 'public', symbol)
            current_price = Decimal(ticker_data['last'])

            # Dynamically calculate SL threshold (e.g., 0.75%–1.5% based on spread)
            dynamic_sl_pct = max(self.fee["taker"] * Decimal(2), od.spread * Decimal(1.25))  # e.g., 1–1.5%
            stop_price = od.limit_price * (Decimal("1.0") - dynamic_sl_pct)

            if current_price <= stop_price:
                self.logger.warning(f"🔻 {symbol} dropped below dynamic SL threshold. Initiating simulated SL sell...")

                # Cancel the passive BUY
                order_id = self.passive_order_tracker.get(symbol, {}).get("buy")
                if order_id:
                    await self.order_manager.cancel_order(order_id, symbol)

                # Construct new SELL OrderData
                sl_od = self._clone_order_data(od, side="sell", trigger="simulated_sl", source="PassiveMM")
                sl_od.type = "limit"
                sl_od.adjusted_price = current_price.quantize(Decimal(f'1e-{od.quote_decimal}'))
                sl_od.adjusted_size = od.available_to_trade_crypto
                sl_od.cost_basis = (sl_od.adjusted_price * sl_od.adjusted_size).quantize(
                    Decimal(f'1e-{od.quote_decimal}'), rounding=ROUND_HALF_UP
                )

                if not self._passes_balance_check(sl_od):
                    self.logger.warning(f"⚠️ Not enough balance to place SL SELL for {symbol}")
                    return

                # Place SL order
                if "sl" in self.passive_order_tracker.get(symbol, {}):
                    return
                ok, res = await self.tom.place_order(sl_od)
                if ok:
                    sl_order_id = res.get('details', {}).get('order_id')
                    self.logger.info(f"✅ SL SELL placed for {symbol} @ {sl_od.adjusted_price}")
                    # Track the SL order
                    self._track_passive_order(symbol, "sl", sl_order_id)
                else:
                    self.logger.error(f"❌ Failed to place SL SELL for {symbol}: {res}")
        except Exception as e:
            self.logger.error(f"❌ Error in monitor_passive_position() for {symbol}: {e}", exc_info=True)


    async def place_passive_orders(self, asset: str, product_id: str) -> None:
        """Attempt to quote both sides of the book for *product_id*.

        Will silently return if conditions are not favourable (spread too
        tight, insufficient balances, inventory skew too high, etc.).
        """
        trading_pair = product_id.replace("-", "/")

        # ------------------------------------------------------------------
        # Build initial OrderData snapshot (prices, balances, precision …)
        # ------------------------------------------------------------------
        try:
            od: OrderData | None = await self.tom.build_order_data(
                source="PassiveMM",
                trigger="market_making",
                asset=asset,
                product_id=product_id,
            )
        except Exception as exc:
            self.logger.error(
                f"❌ build_order_data failed for {trading_pair}: {exc}",
                exc_info=True,
            )
            return

        if not od or od.highest_bid == 0 or od.lowest_ask == 0:
            return  # nothing to do – no order book yet

        # ------------------------------------------------------------------
        # Basic market sanity checks (spread vs fees)
        # ------------------------------------------------------------------
        spread: Decimal = od.lowest_ask - od.highest_bid
        mid_price: Decimal = (od.lowest_ask + od.highest_bid) / 2
        spread_pct: Decimal = spread / mid_price

        # Edge must beat round‑trip maker fees + cushion
        required_edge = self.fee["maker"] * Decimal(2.5)  # in, out, +slippage cushion
        if spread_pct < max(required_edge, self.min_spread_pct):
            print(
                f"⛔ Skipping {trading_pair} — Spread {spread_pct:.4%} < threshold {max(required_edge, self.min_spread_pct):.4%}"
            )
            return

        # ------------------------------------------------------------------
        # Compute tick‑scaled nudge just inside the current spread
        # ------------------------------------------------------------------
        try:
            tick = Decimal(str(od.quote_increment))
            pct_nudge = Decimal("0.3") / Decimal("100")  # 0.15 % of mid
            adjustment = max(tick, (mid_price * pct_nudge).quantize(tick))

            # ------------------------------------------------------------------
            # Bias quotes if inventory drifts away from 50 : 50 USD/CRYPTO
            # ------------------------------------------------------------------

            bias = self._compute_inventory_bias(
                asset_value=od.limit_price * od.base_avail_balance, usd_value=od.usd_avail_balance, spread=spread
            )

            # ------------------------------------------------------------------
            # Generate & submit both sides
            # ------------------------------------------------------------------
            for side in ("buy", "sell"):
                quote_od = self._clone_order_data(od, side=side, post_only=True)

                if side == "buy":
                    if (quote_od.adjusted_price * quote_od.total_balance_crypto) <= self._min_buy_value:
                        # One tick below the lowest ask
                        target_px = min(od.highest_bid, od.lowest_ask - tick)
                        quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)
                else:  # sell
                    if (quote_od.adjusted_price * quote_od.total_balance_crypto) > self._min_order_amount_fiat:
                        # Minimum sell price for profit: entry * (1 + TP - SL buffer)
                        min_profit_pct = self._take_profit + self.fee["maker"]

                        min_sell_price = od.limit_price * (Decimal("1.0") + min_profit_pct)

                        # Price tick one above bid
                        target_px = max(min_sell_price, od.highest_bid + tick)

                        quote_od.adjusted_price = target_px.quantize(tick, rounding=ROUND_DOWN)
                    break
                quote_od.adjusted_size_fiat = quote_od.order_amount_fiat  # if not already set
                quote_od.adjusted_size = (quote_od.order_amount_fiat / quote_od.adjusted_price).quantize(
                    Decimal(f'1e-{quote_od.base_decimal}'),
                    rounding=ROUND_DOWN,
                )

                quote_od.cost_basis = (quote_od.adjusted_price * quote_od.adjusted_size).quantize(
                    Decimal(f'1e-{quote_od.quote_decimal}'),
                    rounding=ROUND_HALF_UP,
                )

                quote_od.trigger = f"passive_{side}@{quote_od.adjusted_price}"

                # per‑side balance sanity check
                if not self._passes_balance_check(quote_od):
                    continue

                ok, res = await self.tom.place_order(quote_od)
                if ok:
                    order_id = res['details']['order_id']
                    if order_id:
                        self._track_passive_order(trading_pair, side, order_id, quote_od)
                    self.logger.info(
                        f"✅ Passive {side.upper()} {trading_pair} @ {quote_od.adjusted_price}"
                    )
                elif res.get('code') == '411':
                    print(
                        f"⚠️ Passive {side.upper()} failed for {trading_pair}: {res.get('error')}"
                    )
                elif res.get('code') == '415':
                    print(
                        f"⚠️ Passive {side.upper()} failed for {trading_pair}: {res.get('error')}"
                    )
                elif res.get('code') == '500':
                    print(
                        f"⚠️ Passive {side.upper()} failed for {trading_pair}: {res.get('message')}"
                    )

                else:
                    self.logger.warning(
                        f"⚠️ Passive {side.upper()} failed for {trading_pair}: {res.get('message')}",exc_info=True
                    )
        except Exception as exc:
            self.logger.error(f"❌ build_order_data failed for {trading_pair}: {exc}", exc_info=True,)
            return
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
        if od.cost_basis < self.min_order_cost_basis :
            return False
        if od.side == "buy":
            fee_multiplier = Decimal(1) + self.fee["maker"]
            cost = od.cost_basis * fee_multiplier
            if od.usd_avail_balance < cost:
                return False
        else:  # sell
            if od.order_amount_fiat < od.base_avail_balance:
                return False
        return True

    def _clone_order_data(self, od: OrderData, **overrides) -> OrderData:
        """Deep‑copy `od` and update provided attributes."""
        cloned = copy.deepcopy(od)
        for k, v in overrides.items():
            setattr(cloned, k, v)
        return cloned

    def _track_passive_order(self, symbol: str, side: str, order_id: str, od: OrderData = None) -> None:
        entry = self.passive_order_tracker.setdefault(symbol, {})
        entry[side] = order_id
        if od:
            entry["order_data"] = od
        entry["timestamp"] = time.time()

    # ------------------------------------------------------------------
    # Housekeeping – cancel and refresh stale quotes
    # ------------------------------------------------------------------
    async def _watchdog(self) -> None:
        """Background coroutine that clears expired resting orders."""
        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()
                for symbol, entry in list(self.passive_order_tracker.items()):
                    if now - entry.get("timestamp", 0) < self.max_lifetime:
                        # Only monitor active BUYs
                        if "buy" in entry:
                            od = entry.get("order_data")
                            if isinstance(od, OrderData):
                                await self.monitor_passive_position(symbol, od)
                        continue
                    # cancel both sides and purge tracker
                    for side in ("buy", "sell"):
                        oid = entry.get(side)
                        if isinstance(oid, dict):  # accidental structure
                            oid = oid.get("order_id")
                        if oid:
                            try:
                                await self.order_manager.cancel_order(oid, symbol)
                            except Exception as exc:  # noqa: BLE001 (broad ok here)
                                self.logger.warning(
                                    f"⚠️ Failed to cancel expired {side} {symbol} (ID: {oid}): {exc}", exc_info=True
                                )
                    self.passive_order_tracker.pop(symbol, None)
                    print(f"🔍 Monitoring: {list(self.passive_order_tracker.keys())}")

            except Exception as exc:  # watchdog must never die silently
                self.logger.error("Watchdog error", exc_info=True)

