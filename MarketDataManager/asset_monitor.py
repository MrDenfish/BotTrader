import asyncio
import re
import datetime as dt
import collections
from types import SimpleNamespace
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta, timezone
from webhook.webhook_validate_orders import OrderData
from Shared_Utils.logger import get_logger
from MarketDataManager.position_monitor import PositionMonitor

# === Config knobs (put near other module-level constants or __init__) ===
POSITIONS_EXIT_SWEEP_INTERVAL_SEC = 3       # how often you'll call this (see B)
POSITIONS_EXIT_OCO_GRACE_SEC      = 10      # grace after open before we (re)arm
POSITIONS_EXIT_REARM              = False   # DISABLED: position_monitor now handles exits with P&L thresholds
BRACKET_ADJUST_GRACE_SEC = 10  # do not cancel/adjust bracket legs in the first N seconds
LIMIT_ADJUST_GRACE_SEC   = 10  # do not cancel/adjust plain limit sells in the first N seconds
PASSIVE_EXIT_MODE = "market"        # "market" | "limit_at_bid"
PASSIVE_EXIT_BID_BUFFER_BPS = 5     # for limit_at_bid: place at bid * (1 - 0.0005)
PASSIVE_EXIT_MIN_NOTIONAL = Decimal("1.00")  # skip dust exits
# ── Momentum guard config ──
ADX_LOOKBACK      = 14            # Wilder classic
ADX_STRONG_MIN    = 25            # ≥25 considered strong trend
DI_DOMINANCE_MIN  = 2             # +DI must exceed −DI by at least this many points
TP_MOMENTUM_MODE  = "hold"        # "hold" | "ratchet"
TP_RATCHET_BPS    = 100           # if ratchet: raise TP ~ +1.00% (100 bps) above current price
ADX_CACHE_SEC     = 5             # don’t recompute more than once per symbol per 5s

_adx_cache_local = {}  # {(symbol): (timestamp, (adx, plus_di, minus_di))}

class AssetMonitor:
    def __init__(self, *,listener, logger, config, shared_data_manager, trade_order_manager, order_manager, trade_recorder, profit_data_manager,
                 order_book_manager, shared_utils_precision, shared_utils_color, shared_utils_date_time):

        self.logger = get_logger('asset_monitor', context={'component': 'asset_monitor'})
        self.listener = listener
        self.shared_data_manager = shared_data_manager
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_color = shared_utils_color
        self.shared_utils_date_time = shared_utils_date_time
        self.trade_order_manager = trade_order_manager
        self.order_manager = order_manager
        self.trade_recorder = trade_recorder
        self.order_book_manager = order_book_manager
        self.profit_data_manager = profit_data_manager

        self.take_profit = Decimal(config.take_profit)
        self.stop_loss = Decimal(config.stop_loss)
        self.min_cooldown = float(config._min_cooldown)
        self.hodl = config.hodl

        self.order_tracker_lock = asyncio.Lock()

        # Initialize position monitor for smart LIMIT exits
        self.position_monitor = PositionMonitor(
            shared_data_manager=shared_data_manager,
            trade_order_manager=trade_order_manager,
            shared_utils_precision=shared_utils_precision,
            logger=self.logger
        )
        self.logger.info(f"[ASSET_MONITOR] Position monitor initialized successfully: {type(self.position_monitor).__name__}")

    @property
    def non_zero_balances(self):
        return self.shared_data_manager.order_management.get("non_zero_balances", {})

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get("order_tracker", {})

    @property
    def passive_orders(self):
        return self.shared_data_manager.order_management.get("passive_orders") or {}

    @property
    def spot_positions(self):
        return self.shared_data_manager.market_data.get("spot_positions", {})

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get("bid_ask_spread", {})

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get("usd_pairs_cache", {})

    async def monitor_all_orders(self):
        await self.monitor_orders_and_assets()
        await self.monitor_untracked_assets()

    # ==================== > Helpers < =================#

    def _sym_lock(self, symbol: str):
        """Return a stable per-symbol asyncio.Lock (created on first use)."""
        if not hasattr(self, "_symbol_locks"):
            self._symbol_locks = collections.defaultdict(asyncio.Lock)
        return self._symbol_locks[symbol]

    def _is_live_order_status(self, status: str | None) -> bool:
        if not status:
            return False
        s = status.upper()
        return s in {"NEW", "OPEN", "PARTIALLY_FILLED", "ACTIVE", "PENDING"}

    def _find_live_bracket_child(self, tracker_snapshot: dict, symbol: str):
        """
        From a normalized order tracker snapshot, return a raw order dict for a live
        bracket child (TP/SL) for `symbol`, or None.
        """
        if not tracker_snapshot:
            return None
        for _oid, raw in tracker_snapshot.items():
            try:
                sym = raw.get("symbol") or raw.get("product_id")
                if sym != symbol:
                    continue
                # Reuse your bracket detection
                od = OrderData.from_dict(raw)
                if not self._order_is_bracket_child(od, raw):
                    continue
                if self._is_live_order_status(raw.get("status")):
                    return raw
            except Exception:
                continue
        return None

    async def _get_open_orders_snapshot(self, *, allow_rest_refresh: bool = True) -> dict:
        """
        Returns a normalized snapshot of open orders keyed by order_id.
        Prefers in-memory tracker; optionally refreshes from REST if stale/empty.
        """
        # 1) In-memory tracker (under short lock)
        async with self.order_tracker_lock:
            tracker = self.shared_data_manager.order_management.get("order_tracker", {}) or {}
            # normalize once (your existing method)
            snap = self._normalize_order_tracker_snapshot(self.shared_data_manager.order_management)

        # Heuristic: if empty or clearly stale, try REST (optional)
        if (not snap or len(snap) == 0) and allow_rest_refresh:
            try:
                # Don’t block on global lock while doing I/O
                rest_orders = await self.listener.coinbase_api.fetch_open_orders()
                # Convert the REST list → tracker-like dict keyed by id
                rest_snap = {}
                for o in rest_orders or []:
                    oid = o.get("id") or o.get("order_id")
                    if not oid:
                        continue
                    # fit minimal fields you use elsewhere
                    rest_snap[oid] = {
                        "order_id": oid,
                        "product_id": o.get("symbol") or o.get("product_id"),
                        "symbol": o.get("symbol") or o.get("product_id"),
                        "status": o.get("status") or "OPEN",
                        "info": o,  # keep full payload for bracket detection
                    }
                if rest_snap:
                    return rest_snap
            except Exception as e:
                self.logger.warning(f"[ORDERS-SNAPSHOT] REST refresh failed; using in-memory: {e}")

        return snap or {}


    async def _place_passive_exit_for_position(
            self,
            symbol: str,
            asset: str,
            qty: Decimal,
            avg_cost: Decimal,
    ) -> tuple[bool, dict | str]:
        """
        Flatten position without relying on an OCO.
        Places a MARKET sell (default) or a LIMIT-at-bid sell with a small buffer.
        Returns (success, response_or_reason).
        """
        limit_px = None
        try:
            # --- Precision & size guards ---
            base_dec, quote_dec, *_ = self.shared_utils_precision.fetch_precision(symbol)
            if qty is None:
                return False, "qty_none"
            # Round DOWN size to exch precision to avoid rejections
            qfmt = Decimal("1." + "0" * base_dec)
            size_rounded = qty.quantize(qfmt, rounding=ROUND_DOWN)
            if size_rounded <= 0:
                return False, "qty_zero_after_round"
            # Skip dust exits if configured
            est_notional = (avg_cost or Decimal("0")) * size_rounded
            if est_notional < PASSIVE_EXIT_MIN_NOTIONAL:
                self.logger.debug(f"[PASSIVE-EXIT] skip {symbol} dust notional={est_notional}")
                return False, "dust_notional"

            # --- Price discovery (for limit mode & for logging) ---
            current_price = None
            try:
                ob = await self.order_book_manager.get_order_book(None, symbol)
                if ob and ob.get("highest_bid"):
                    current_price = Decimal(str(ob["highest_bid"]))
            except Exception:
                current_price = None

            if current_price is None:
                # fallback to your bid_ask_spread cache
                current_price = self.bid_ask_spread.get(symbol, None)
                if isinstance(current_price, dict):
                    current_price = current_price.get("bid")
                current_price = Decimal(str(current_price or "0"))

            # --- Build order data ---
            if PASSIVE_EXIT_MODE == "limit_at_bid":
                # place at bid*(1 - buffer) to favor fast fill
                if current_price is None or current_price <= 0:
                    # if we can’t price, fallback to market
                    mode = "market"
                else:
                    buffer = (Decimal(PASSIVE_EXIT_BID_BUFFER_BPS) / Decimal(10_000))
                    limit_px = current_price * (Decimal("1") - buffer)
                    px_fmt = Decimal("1." + "0" * quote_dec)
                    limit_px = limit_px.quantize(px_fmt, rounding=ROUND_DOWN)
                    mode = "limit"
            else:
                mode = "market"

            trigger = self.trade_order_manager.build_trigger(
                "passive_exit",
                f"mode={mode} size={size_rounded} avg={avg_cost}"
            )

            order_kwargs = dict(
                source="websocket",
                trigger=trigger,
                asset=asset,
                product_id=symbol,
                side="sell",
                size=size_rounded,  # if your builder uses size
            )
            if mode == "limit":
                order_kwargs.update(order_type="limit", price=limit_px)
            else:
                order_kwargs.update(order_type="market", price=None)

            new_order = await self.trade_order_manager.build_order_data(**order_kwargs)
            if not new_order:
                return False, "build_order_data_failed"

            # --- Place with EXIT intent (bypass cooldown/gates downstream) ---
            success, resp = await self.trade_order_manager.place_order(
                new_order,
                (base_dec, quote_dec),
                intent="EXIT"
            )
            if success:
                self.logger.info(
                    f"🛑 PASSIVE EXIT placed for {symbol}: mode={mode} size={size_rounded} "
                    f"price={limit_px if mode == 'limit' else 'market'}"
                )
                return True, resp

            self.logger.warning(f"[PASSIVE-EXIT] place failed for {symbol}: {resp}")
            return False, resp

        except Exception as e:
            self.logger.error(f"❌ passive exit error for {symbol}: {e}", exc_info=True)
            return False, str(e)

    async def _manage_untracked_position_exit(
            self,
            symbol: str,
            asset: str,
            qty: Decimal,
            avg_entry: Decimal,
            profit_pct: Decimal,
            current_price: Decimal,
            precision_data: tuple
    ):
        """
        Position-centric safety net:
          - If a live bracket child exists → delegate to _handle_active_tp_sl_decision.
          - Else → (re)arm a fresh protective bracket (no cancels).
        """
        # 1) Try to locate a live child order for this symbol
        order_mgmt = self.shared_data_manager.order_management
        tracked = self._normalize_order_tracker_snapshot(order_mgmt)
        live_child = None
        for _oid, raw in tracked.items():
            try:
                if raw.get("symbol") == symbol or raw.get("product_id") == symbol:
                    info = raw.get("info", {}) or {}
                    ocfg = (info.get("order_configuration") or {})
                    if "trigger_bracket_gtc" in ocfg and (raw.get("status") in {"open", "OPEN", "new", "NEW", "partially_filled"}):
                        live_child = raw
                        break
            except Exception:
                continue

        # 2) If found → adapt minimal order_data/full_order and delegate
        if live_child:
            try:
                od = OrderData.from_dict(live_child)  # same constructor used elsewhere
            except Exception:
                od = None

            if od:
                # current_price/avg_entry/precision already computed by caller
                profit_stub = {"profit percent": f"{(profit_pct * 100):.4f}%"}  # matches your existing parsing
                await self._handle_active_tp_sl_decision(
                    order_data=od,
                    full_order=live_child,
                    symbol=symbol,
                    asset=asset,
                    current_price=current_price,
                    avg_price=avg_entry,
                    precision_data=precision_data,
                    profit_data=profit_stub,
                )
                return

        # 3) No live child → (re)arm protection (treat as EXIT; bypass entry gates/cooldowns)

        # NEW: Check for orphaned non-OCO orders and cancel them first
        orphaned_orders = []
        for oid, raw in tracked.items():
            try:
                if raw.get("symbol") == symbol or raw.get("product_id") == symbol:
                    # Check if this is NOT an OCO order
                    info = raw.get("info", {}) or {}
                    ocfg = (info.get("order_configuration") or {})
                    is_oco = "trigger_bracket_gtc" in ocfg

                    if not is_oco and raw.get("status") in {"open", "OPEN", "new", "NEW"}:
                        orphaned_orders.append((oid, raw))
            except Exception as e:
                self.logger.debug(f"Error checking order {oid}: {e}")
                continue

        # Cancel orphaned orders that are blocking OCO placement
        if orphaned_orders:
            self.logger.warning(
                f"[UNTRACKED] Found {len(orphaned_orders)} orphaned non-OCO order(s) for {symbol}. "
                f"Canceling to place protective OCO..."
            )
            for oid, order_info in orphaned_orders:
                try:
                    # Cancel the order on Coinbase using cancel_order (takes a list)
                    cancel_resp = await self.trade_order_manager.coinbase_api.cancel_order([oid])

                    # Check if cancellation was successful
                    results = (cancel_resp or {}).get("results") or []
                    entry = next((r for r in results if str(r.get("order_id")) == str(oid)), None)

                    if entry and entry.get("success"):
                        self.logger.info(f"[UNTRACKED] Canceled orphaned order {oid} for {symbol}")

                        # Remove from order_tracker
                        if oid in self.shared_data_manager.order_management.get('order_tracker', {}):
                            del self.shared_data_manager.order_management['order_tracker'][oid]
                            self.logger.info(f"[UNTRACKED] Removed order {oid} from order_tracker")
                    else:
                        failure_reason = entry.get("failure_reason") if entry else "No response entry"
                        self.logger.warning(f"[UNTRACKED] Failed to cancel orphaned order {oid}: {failure_reason}")
                except Exception as e:
                    self.logger.error(f"[UNTRACKED] Failed to cancel orphaned order {oid}: {e}", exc_info=True)

        trigger = self.trade_order_manager.build_trigger(
            "rearm_oco_missing",
            f"arming protection for naked position qty={qty} avg={avg_entry}"
        )
        new_order = await self.trade_order_manager.build_order_data(
            source="websocket",
            trigger=trigger,
            asset=asset,
            product_id=symbol,
            side="sell",
            # Leave price None; your builder should compute TP/SL legs from config & avg
        )
        if not new_order:
            self.logger.warning(f"[UNTRACKED] Failed to build protective OCO for {symbol}; leaving as-is (still monitored)")
            return

        success, resp = await self.trade_order_manager.place_order(new_order, precision_data)
        log = self.logger.info if success else self.logger.warning
        log(f"{'🛡️' if success else '⚠️'} Rearmed protection for {symbol} (untracked): {resp}")


    def _extract_order_id(self, place_response: dict | None) -> str | None:
        """
        Try to pull a new order_id out of the place_order response.
        Handles a few common shapes:
          { "order_id": "..." } or { "success": True, "order_id": "..." }
          { "success": True, "result": {"order_id": "..."} }
        Returns None if not found.
        """
        if not place_response or not isinstance(place_response, dict):
            return None
        if "order_id" in place_response and place_response["order_id"]:
            return str(place_response["order_id"])
        result = place_response.get("result") if isinstance(place_response.get("result"), dict) else None
        if result and result.get("order_id"):
            return str(result["order_id"])
        return None

    async def _handle_active_tp_sl_decision(
            self,
            order_data: "OrderData",
            full_order: dict,
            symbol: str,
            asset: str,
            current_price: Decimal,
            avg_price: Decimal,
            precision_data: tuple,
            profit_data: dict | None = None,  # ← optional
    ):
        try:
            adx, pdi, mdi = None, None, None  # for logging later
            # Expect bracket role; guards from #1
            if not self._order_is_bracket_child(order_data, full_order):
                self.logger.debug(f"[GUARD] _handle_active_tp_sl_decision skip for {symbol} — not a bracket child")
                return

            # Grace guard (from #1)
            info = full_order.get("info", {}) if isinstance(full_order, dict) else {}
            created_iso = None
            try:
                created_iso = info.get("created_time") or full_order.get("datetime")
            except Exception:
                created_iso = None

            order_age = 0.0
            if created_iso:
                try:
                    s = created_iso.replace("Z", "+00:00") if isinstance(created_iso, str) and created_iso.endswith("Z") else created_iso
                    created_dt = dt.datetime.fromisoformat(s) if isinstance(s, str) else None
                    if created_dt:
                        order_age = (dt.datetime.now(dt.timezone.utc) - created_dt.astimezone(dt.timezone.utc)).total_seconds()
                except Exception:
                    order_age = 0.0

            if order_age < BRACKET_ADJUST_GRACE_SEC:
                self.logger.debug(
                    f"[GUARD] _handle_active_tp_sl_decision grace-skip for {symbol} — age={order_age:.2f}s < {BRACKET_ADJUST_GRACE_SEC}s"
                )
                return

            # --- Quantize prices to precision ---
            quote_deci = precision_data[1]
            qfmt = Decimal('1.' + '0' * quote_deci)

            current_price = current_price.quantize(qfmt)
            avg_price = avg_price.quantize(qfmt) if avg_price is not None else Decimal("0").quantize(qfmt)

            # --- profit_pct: prefer provided profit_data; otherwise compute fallback ---
            profit_pct = Decimal("0")
            try:
                if profit_data and "profit percent" in profit_data:
                    profit_pct = Decimal(str(profit_data["profit percent"]).replace("%", "")) / Decimal("100")
                elif avg_price > 0:
                    profit_pct = (current_price - avg_price) / avg_price
            except Exception:
                # keep profit_pct=0 if parsing fails
                pass

            # --- Read bracket config (TP/SL) and decide which leg we’re amending ---
            trigger_cfg = (info.get('order_configuration') or {}).get('trigger_bracket_gtc', {}) if isinstance(info, dict) else {}
            # Your existing code used a single 'limit_price' — keep that behavior for now
            old_limit_price = Decimal(str(trigger_cfg.get('limit_price', '0') or '0')).quantize(qfmt)

            # Decide SL vs TP first (unchanged logic)
            tp_event = current_price > old_limit_price
            sl_event = current_price < old_limit_price

            if not (tp_event or sl_event):
                return  # no update

            # 🔒 Never delay SL
            if sl_event:
                trigger = self.trade_order_manager.build_trigger(
                    "SL",
                    f"profit_pct={profit_pct:.2%} → price fell below SL ({current_price} < {old_limit_price})"
                )
            else:
                # tp_event: consider momentum gating
                momentum = await self._get_adx_di(symbol, ADX_LOOKBACK)
                if momentum:
                    adx, pdi, mdi = momentum
                    strong_up = (adx >= Decimal(ADX_STRONG_MIN)) and ((pdi - mdi) >= Decimal(DI_DOMINANCE_MIN))
                else:
                    strong_up = False  # no data → don't gate

                if strong_up:
                    if TP_MOMENTUM_MODE == "hold":
                        # Skip TP update to let trend run; keep existing SL mechanics
                        self.logger.debug(
                            f"[TP-MOMENTUM] HOLD {symbol}: ADX={adx:.2f}, +DI={pdi:.2f} > -DI={mdi:.2f}; "
                            f"deferring TP update at {current_price}"
                        )
                        return
                    elif TP_MOMENTUM_MODE == "ratchet":
                        # Raise TP above current price by TP_RATCHET_BPS; SL unchanged elsewhere
                        bps = Decimal(TP_RATCHET_BPS) / Decimal(10_000)
                        new_tp = (current_price * (Decimal(1) + bps)).quantize(qfmt)
                        trigger = self.trade_order_manager.build_trigger(
                            "TP_RATCHET",
                            f"ADX={adx:.2f}, +DI={pdi:.2f} > -DI={mdi:.2f}; "
                            f"raising TP from {old_limit_price} → {new_tp}"
                        )
                        # You may want to carry new_tp to builder; see below
                        # NOTE: If your builder accepts explicit TP price, pass it via trigger or kwargs
                        # and have build_order_data interpret it. Example:
                        # new_order_data = await self.trade_order_manager.build_order_data(..., tp_price=new_tp)
                    else:
                        # Unknown mode → default to hold
                        self.logger.debug(f"[TP-MOMENTUM] Unknown mode '{TP_MOMENTUM_MODE}', holding TP for {symbol}")
                        return
                else:
                    # Not a strong uptrend → proceed with normal TP replace
                    trigger = self.trade_order_manager.build_trigger(
                        "TP",
                        f"profit_pct={profit_pct:.2%} → price rose above TP ({current_price} > {old_limit_price})"
                    )

            # --- SAFE REPLACE: place new protective order first ---
            new_order_data = await self.trade_order_manager.build_order_data(
                source='websocket',
                trigger=trigger,
                asset=asset,
                product_id=symbol,
                side='sell',
            )
            if not new_order_data:
                self.logger.warning(f"[SAFE-REPLACE] build failed for {symbol} (TP/SL adjust); keeping existing")
                return

            self.logger.debug("Asset monitor placing new order",
                            extra={'symbol': symbol, 'trigger': str(trigger)})
            success, resp = await self.trade_order_manager.place_order(new_order_data, precision_data)
            if not success:
                self.logger.warning(f"[SAFE-REPLACE] place failed for {symbol} (TP/SL adjust); keeping existing: {resp}")
                return

            new_oid = self._extract_order_id(resp)
            if not new_oid:
                self.logger.warning(f"[SAFE-REPLACE] no order_id in place response for {symbol} (TP/SL adjust); keeping existing")
                return

            # Only now cancel the previous child
            await self.order_manager.cancel_order(order_data.order_id, symbol, cancel_tag="tp_sl_adjust_safe_replace")
            self.logger.info(f"✅ Updated SL/TP for {symbol}: old={order_data.order_id} → new={new_oid}")

        except Exception as e:
            self.logger.error(f"❌ Error in _handle_active_tp_sl_decision for {symbol}: {e}", exc_info=True)

    def _order_is_bracket_child(self, order_data: "OrderData", full_order: dict | None = None) -> bool:
        """
        Returns True if this order is clearly part of a TP/SL bracket.
        Tries multiple hints to be robust across sources.
        """
        try:
            # 1) Your existing hint on OrderData.trigger
            if getattr(order_data, "trigger", None) and isinstance(order_data.trigger, dict):
                if order_data.trigger.get("tp_sl_flag") or order_data.trigger.get("type") in {"TP", "SL"}:
                    return True

            # 2) Full order payload has Coinbase trigger structure
            if full_order and isinstance(full_order, dict):
                info = full_order.get("info", {}) or {}
                ocfg = info.get("order_configuration", {}) or {}
                if "trigger_bracket_gtc" in ocfg:
                    return True

            # 3) Fallback: names that often indicate bracket roles
            if getattr(order_data, "type", "") in {"take_profit", "stop_loss", "trailing_stop"}:
                return True
        except Exception:
            pass
        return False

    def _get_usd_prices(self):
        if self.usd_pairs.empty:
            return {}
        return self.usd_pairs.set_index("symbol")["price"].to_dict()

    async def _analyze_position(self, asset, position, usd_prices):
        symbol = f"{asset}-USD"
        if symbol == "USD-USD" or symbol in self.passive_orders:
            return None

        pos = position.to_dict() if hasattr(position, "to_dict") else position
        current_price = usd_prices.get(symbol)
        if not current_price:
            return None

        precision = self.shared_utils_precision.fetch_precision(symbol)
        base_deci, quote_deci = precision[:2]
        base_q = Decimal("1").scaleb(-base_deci)
        quote_q = Decimal("1").scaleb(-quote_deci)

        avg_entry = self.shared_utils_precision.safe_quantize(
            Decimal(pos.get("average_entry_price", {}).get("value", "0")), quote_q
        )
        cost_basis = self.shared_utils_precision.safe_quantize(
            Decimal(pos.get("cost_basis", {}).get("value", "0")), quote_q
        )
        qty = self.shared_utils_precision.safe_quantize(
            Decimal(pos.get("available_to_trade_crypto", "0")), base_q
        )

        if qty <= Decimal("0.0001") or avg_entry <= 0:
            return None

        # ✅ Call calculate_profitability()
        required_prices = {
            "avg_price": avg_entry,
            "cost_basis": cost_basis,
            "asset_balance": qty,
            "current_price": Decimal(current_price),
            "usd_avail": self._get_usd_available(),
            "status_of_order": "UNTRACKED"
        }

        profit_data = await self.profit_data_manager.calculate_profitability(
            symbol, required_prices, self.bid_ask_spread, self.usd_pairs
        )

        if not profit_data:
            return None

        try:
            profit_pct = Decimal(profit_data["profit percent"].strip('%')) / 100
        except Exception:
            profit_pct = (Decimal(current_price) - avg_entry) / avg_entry

        profit = Decimal(profit_data["profit"])

        return symbol, asset, Decimal(current_price), qty, avg_entry, profit, profit_pct, precision

    async def _passes_holding_cooldown(self, symbol: str, *, intent: str = "ENTRY") -> bool:
        """
        Checks whether the minimum holding cooldown has passed for the oldest unlinked BUY.

        Returns:
            True  → Safe to place a new order
            False → Still within the cooldown period
        """
        try:
            # ✅ Fetch unlinked BUYs (FIFO order)
            trades = await self.trade_recorder.find_unlinked_buys(symbol)
            if not trades:
                return True  # No active BUYs → always safe

            # ✅ Oldest unlinked buy (FIFO logic)
            oldest_trade = trades[0]

            # Validate order_time
            if not oldest_trade or not oldest_trade.order_time:
                self.logger.warning(f"⚠️ No valid order_time for cooldown check: {symbol}")
                return True

            now = datetime.now(timezone.utc)
            trade_time = oldest_trade.order_time.astimezone(timezone.utc)
            held_for = now - trade_time

            # ✅ Sanity check: handle any weird clock drift
            if held_for.total_seconds() < 0:
                self.logger.warning(
                    f"⚠️ Time anomaly detected for {symbol}: negative hold duration {held_for} "
                    f"(now={now}, trade_time={trade_time})"
                )
                return True

            # ✅ Check if minimum cooldown period passed
            if held_for >= timedelta(minutes=self.min_cooldown):
                self.logger.debug(
                    f"⏩ Cooldown passed for {symbol}: held for {held_for.total_seconds() / 60:.1f} min "
                    f"(required={self.min_cooldown} min)"
                )
                return True
            else:
                self.logger.debug(
                    f"⏳ Cooldown active for {symbol}: held for {held_for.total_seconds() / 60:.1f} min "
                    f"(required={self.min_cooldown} min)"
                )
                return False

        except Exception as e:
            self.logger.warning(f"⚠️ Could not evaluate cooldown for {symbol}: {e}", exc_info=True)
            return True  # Fail-safe: allow order if something goes wrong


    async def _place_order(self, source, trigger, asset, symbol, precision):
        order_data = await self.trade_order_manager.build_order_data(source=source, trigger=trigger, asset=asset, product_id=symbol,
                                                                     side='sell')
        if not order_data:
            return
        order_data.trigger = trigger
        success, response = await self.trade_order_manager.place_order(order_data, precision)
        log = self.logger.info if success else self.logger.warning
        log(f"{'✅' if success else '⚠️'} Order for {symbol}: {response}")

    def _normalize_order_tracker_snapshot(self, order_mgmt: dict) -> dict:
        tracker = order_mgmt.get("order_tracker", {})
        normalized_tracker = {}

        for order_id, raw in tracker.items():
            order_type = raw.get("type")

            if order_type == "TAKE_PROFIT_STOP_LOSS":
                trigger_cfg = (
                    raw.get("info", {})
                    .get("order_configuration", {})
                    .get("trigger_bracket_gtc", {})
                )

                normalized_tracker[order_id] = {
                    **raw,
                    "type": "limit",  # Treat as limit for consistency
                    "tp_sl_flag": True,
                    "amount": Decimal(trigger_cfg.get("base_size", "0")),
                    "price": Decimal(trigger_cfg.get("limit_price", "0")),
                    "stop_price": Decimal(trigger_cfg.get("stop_trigger_price", "0")),
                    "parent_order_id": raw.get("info", {}).get("originating_order_id"),
                }
            else:
                normalized_tracker[order_id] = raw

        return normalized_tracker

    def _get_usd_available(self):
        usd_data = self.usd_pairs.set_index('asset').to_dict(orient='index')
        return usd_data.get('USD', {}).get('free', Decimal('0'))

    def _get_asset_details(self, snapshot, asset, precision):
        try:
            quote_deci = precision[1]
            base_deci = precision[0]
            base_quantizer = Decimal("1").scaleb(-base_deci)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)

            # Pull from non_zero_balances first
            balance_data = snapshot.get('non_zero_balances', {}).get(asset, {})

            avg_price = Decimal(str(balance_data['average_entry_price'].get('value', '0')))
            avg_price = self.shared_utils_precision.safe_quantize(avg_price, quote_quantizer)

            cost_basis = Decimal(str(balance_data['cost_basis'].get('value', '0')))
            cost_basis = self.shared_utils_precision.safe_quantize(cost_basis, quote_quantizer)

            # ✅ Fallback to spot_positions if non_zero_balances is missing or empty
            asset_balance = Decimal(
                self.spot_positions.get(asset, {}).get('total_balance_crypto', 0)
            )
            asset_balance = self.shared_utils_precision.safe_quantize(asset_balance, base_quantizer)

            # ✅ Recompute cost_basis if not present but we have avg_price & balance
            if cost_basis == 0 and asset_balance > 0 and avg_price > 0:
                cost_basis = (asset_balance * avg_price).quantize(quote_quantizer)

            return asset_balance, avg_price, cost_basis

        except Exception as e:
            self.logger.error(f"❌ Error getting asset details for {asset}: {e}", exc_info=True)
            return Decimal('0'), Decimal('0'), Decimal('0')

    def _compute_order_duration(self, order_time_str):
        try:
            # Strip 'Z' and replace with timezone-aware UTC if needed
            if isinstance(order_time_str, str):
                if order_time_str.endswith('Z'):
                    order_time_str = order_time_str.replace('Z', '+00:00')
                order_time = datetime.fromisoformat(order_time_str)
            else:
                return 0

            now = datetime.now(timezone.utc).replace(tzinfo=order_time.tzinfo)
            return int((now - order_time).total_seconds() // 60)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"⚠️ Failed to compute order duration for time {order_time_str}: {e}")
            return 0

    async def _handle_limit_sell(self, order_data, symbol, asset, precision, order_duration, avg_price, current_price):
        """
        Role + grace guards should already be at the top (from step #1).
        This version performs SAFE REPLACE for any adjustment:
          1) place the new protective order,
          2) verify acceptance,
          3) only then cancel the previous order.
        """
        # --- Role guard: do NOT touch bracket children here ---
        if self._order_is_bracket_child(order_data):
            self.logger.debug(f"[GUARD] _handle_limit_sell skip for {symbol} — bracket child detected")
            return

        # --- Grace guard for brand-new orders ---
        if order_duration is not None and order_duration < LIMIT_ADJUST_GRACE_SEC:
            self.logger.debug(
                f"[GUARD] _handle_limit_sell grace-skip for {symbol} — age={order_duration:.2f}s < {LIMIT_ADJUST_GRACE_SEC}s"
            )
            return

        order_book = await self.order_book_manager.get_order_book(order_data, symbol)
        highest_bid = order_book["highest_bid"] if order_book and order_book.get("highest_bid") else current_price

        # ① Trailing stop “upgrade” condition
        if order_data.price < min(current_price, highest_bid) and order_duration > 5:
            trigger = 'trailing_stop'
            # Build & place new order FIRST
            new_order_data = await self.trade_order_manager.build_order_data(
                source='websocket',
                trigger=trigger,
                asset=asset,
                product_id=symbol,
                price=order_data.price,  # your original code used order_data.price as reference
                order_type=None,  # trailing stop specifics handled by build_order_data
                side='sell'
            )
            if not new_order_data:
                self.logger.warning(f"[SAFE-REPLACE] build failed for {symbol} ({trigger}); keeping existing order")
                return

            success, resp = await self.trade_order_manager.place_order(new_order_data, precision)
            if not success:
                self.logger.warning(f"[SAFE-REPLACE] place failed for {symbol} ({trigger}); keeping existing order: {resp}")
                return

            new_oid = self._extract_order_id(resp)
            if not new_oid:
                self.logger.warning(f"[SAFE-REPLACE] no order_id in place response for {symbol}; keeping existing")
                return

            # Now it’s safe to cancel the previous order
            await self.order_manager.cancel_order(order_data.order_id, symbol)
            self.logger.info(f"✅ Trailing stop armed for {symbol}; old={order_data.order_id} → new={new_oid}")
            return

        # ② Adjust limit sell if recovering from loss
        if order_data.price < avg_price and current_price > order_data.price and order_duration > 5:
            trigger = self.trade_order_manager.build_trigger(
                "limit_sell_adjusted",
                f"Recovering price: old={order_data.price}, current={current_price}, avg={avg_price}"
            )

            # Build & place new order FIRST
            new_order_data = await self.trade_order_manager.build_order_data(
                source='websocket',
                trigger=trigger,
                asset=asset,
                product_id=symbol,
                price=None,  # let builder compute limit; matches your prior call
                order_type='limit',
                side='sell'
            )
            if not new_order_data:
                self.logger.warning(f"[SAFE-REPLACE] build failed for {symbol} (limit_sell_adjusted); keeping existing")
                return

            success, resp = await self.trade_order_manager.place_order(new_order_data, precision)
            if not success:
                self.logger.warning(f"[SAFE-REPLACE] place failed for {symbol} (limit_sell_adjusted); keeping existing: {resp}")
                return

            new_oid = self._extract_order_id(resp)
            if not new_oid:
                self.logger.warning(f"[SAFE-REPLACE] no order_id in place response for {symbol} (limit_sell_adjusted); keeping existing")
                return

            # Now it’s safe to cancel the previous order
            await self.order_manager.cancel_order(order_data.order_id, symbol)
            self.logger.info(f"✅ Adjusted limit SELL for {symbol}: old={order_data.order_id} → new={new_oid}")

    async def _fetch_open_positions_snapshot(self):
        """
        Returns a list of position-like objects with fields:
            symbol: str
            size: Decimal
            avg_cost: Decimal
            opened_at: datetime (best-effort; uses a recent timestamp if unknown)

        Prefers shared_data_manager.positions_dao if available.
        Falls back to synthesizing from order_management.spot_positions
        or non_zero_balances.
        """
        now = self.shared_data_manager.now() if hasattr(self.shared_data_manager, "now") else dt.datetime.now(dt.timezone.utc)

        # 1) Preferred: positions_dao if it exists
        positions_dao = getattr(self.shared_data_manager, "positions_dao", None)
        db_sess_mgr = getattr(self.shared_data_manager, "database_session_manager", None)

        if positions_dao and db_sess_mgr:
            try:
                async with db_sess_mgr.async_session() as s:
                    return await positions_dao.fetch_open_positions(s)
            except Exception as e:
                self.logger.warning(f"[POS-SNAPSHOT] positions_dao failed, falling back: {e}")

        # 2) Fallback: synthesize from order_management
        positions: list = []
        om = getattr(self.shared_data_manager, "order_management", {}) or {}

        # 2a) Try spot_positions (if present)
        spot_positions = (om.get("spot_positions") or {}) if isinstance(om, dict) else {}
        for symbol, rec in spot_positions.items():
            try:
                # Typical keys seen in your profit printout: asset_balance, avg_price
                size = Decimal(str(rec.get("asset_balance") or rec.get("qty") or 0))
                if size <= 0:
                    continue
                avg_cost = Decimal(str(rec.get("avg_price") or rec.get("avg_cost") or 0))
                opened_at = rec.get("opened_at")
                if isinstance(opened_at, str):
                    try:
                        opened_at = dt.datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    except Exception:
                        opened_at = None
                if opened_at is None:
                    # choose a conservative timestamp so grace window won’t block forever
                    opened_at = now - dt.timedelta(minutes=15)

                positions.append(SimpleNamespace(symbol=symbol, size=size, avg_cost=avg_cost, opened_at=opened_at))
            except Exception:
                continue

        # 2b) If nothing yet, try non_zero_balances (asset → balance)
        if not positions:
            nzb = om.get("non_zero_balances", {}) if isinstance(om, dict) else {}
            for asset, bal in (nzb or {}).items():
                try:
                    size = Decimal(str(bal.get("total") if isinstance(bal, dict) else bal))
                    if size <= 0:
                        continue
                    # Try to construct a symbol like "ASSET-USD" if you have usd_pairs / map
                    symbol = None
                    if hasattr(self, "usd_pairs") and isinstance(self.usd_pairs, dict):
                        symbol = self.usd_pairs.get(asset)  # often "ASSET-USD"
                    if not symbol:
                        symbol = f"{asset}-USD"  # best-effort guess

                    # avg_cost not known from balances; fall back to last known avg or 0
                    avg_cost = Decimal("0")
                    # If you keep a cost basis cache, wire it here; otherwise leave 0
                    opened_at = now - dt.timedelta(minutes=15)
                    positions.append(SimpleNamespace(symbol=symbol, size=size, avg_cost=avg_cost, opened_at=opened_at))
                except Exception:
                    continue

        return positions


    async def _get_adx_di(self, symbol: str, lookback: int = ADX_LOOKBACK):
        """
        Returns (adx, plus_di, minus_di) as Decimals, or None if not available.
        Prefers DB OHLCV via existing fetch function; falls back to live OHLCV via ohlcv_manager.
        Reuses precomputed indicators if present on the DataFrame; else computes Wilder ADX.
        """
        now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
        cached = _adx_cache_local.get(symbol)
        if cached and (now_ts - cached[0]) <= ADX_CACHE_SEC:
            return cached[1]

        ohlcv_df = None

        # 1) Try your existing DB fetch (preferred)
        fetch_db = None
        # Locate an existing fetch_ohlcv_data_from_db implementation (listener / manager)
        if hasattr(self.listener, "fetch_ohlcv_data_from_db"):
            fetch_db = self.listener.fetch_ohlcv_data_from_db
        elif hasattr(self.listener, "market_data_updater") and hasattr(self.listener.market_data_updater, "fetch_ohlcv_data_from_db"):
            fetch_db = self.listener.market_data_updater.fetch_ohlcv_data_from_db
        elif hasattr(self, "fetch_ohlcv_data_from_db"):
            fetch_db = self.fetch_ohlcv_data_from_db  # if AssetMonitor exposes it

        if fetch_db:
            try:
                ohlcv_df = await fetch_db(symbol)
            except Exception as e:
                self.logger.debug(f"[ADX] DB fetch failed for {symbol}: {e}")

        # 2) Fallback: live OHLCV via ohlcv_manager
        if (ohlcv_df is None) or getattr(ohlcv_df, "empty", True):
            try:
                candles = await self.listener.ohlcv_manager.fetch_recent_ohlcv(
                    symbol, limit=max(lookback * 3, 100)
                )
                # normalize to a minimal DataFrame-like structure if possible
                if candles:
                    # Try to import pandas only if needed
                    import pandas as pd
                    rows = []
                    for c in candles:
                        if isinstance(c, dict):
                            rows.append({
                                "time": c.get("time") or c.get("timestamp"),
                                "open": c.get("open"),
                                "high": c.get("high"),
                                "low": c.get("low"),
                                "close": c.get("close"),
                                "volume": c.get("volume"),
                            })
                        else:
                            # assume tuple/list [ts, open, high, low, close, vol]
                            ts, op, hi, lo, cl, vol = c[:6]
                            rows.append({"time": ts, "open": op, "high": hi, "low": lo, "close": cl, "volume": vol})
                    if rows:
                        ohlcv_df = pd.DataFrame(rows)
            except Exception as e:
                self.logger.debug(f"[ADX] Live OHLCV fetch failed for {symbol}: {e}")

        # If still nothing usable, bail
        if (ohlcv_df is None) or getattr(ohlcv_df, "empty", True):
            return None

        # Ensure sort by time ascending and pick the most recent N rows
        try:
            if "time" in ohlcv_df.columns:
                ohlcv_df = ohlcv_df.sort_values("time", ascending=True).reset_index(drop=True)
            if len(ohlcv_df) < lookback + 2:
                return None
            # Work on a tail window to speed up
            window = max(lookback * 3, 100)
            if len(ohlcv_df) > window:
                ohlcv_df = ohlcv_df.iloc[-window:].reset_index(drop=True)
        except Exception:
            pass

        # 3) If indicators already computed, reuse them
        # Your pipeline’s `indicators.calculate_indicators()` often creates ADX/+DI/-DI columns.
        # Try common names; adjust if your exact column names differ.
        for adx_col, pdi_col, mdi_col in [
            ("ADX", "PLUS_DI", "MINUS_DI"),
            ("adx", "plus_di", "minus_di"),
            ("ADX_14", "+DI_14", "-DI_14"),
        ]:
            if all(col in ohlcv_df.columns for col in (adx_col, pdi_col, mdi_col)):
                last = ohlcv_df.iloc[-1]
                try:
                    adx_val = Decimal(str(last[adx_col]))
                    pdi_val = Decimal(str(last[pdi_col]))
                    mdi_val = Decimal(str(last[mdi_col]))
                    _adx_cache_local[symbol] = (now_ts, (adx_val, pdi_val, mdi_val))
                    return adx_val, pdi_val, mdi_val
                except Exception:
                    break  # fall through to manual compute

        # 4) Compute Wilder ADX/+DI/-DI manually from H/L/C (if not already present)
        try:
            import numpy as np  # speed up; optional
            H = ohlcv_df["high"].astype(float).to_numpy()
            L = ohlcv_df["low"].astype(float).to_numpy()
            C = ohlcv_df["close"].astype(float).to_numpy()
            n = len(C)
            if n < lookback + 2:
                return None

            TR = np.zeros(n)
            DMp = np.zeros(n)
            DMm = np.zeros(n)
            for i in range(1, n):
                up = H[i] - H[i - 1]
                down = L[i - 1] - L[i]
                TR[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
                DMp[i] = up if (up > 0 and up > down) else 0.0
                DMm[i] = down if (down > 0 and down > up) else 0.0

            # Wilder smoothing
            def wilder(series, period):
                sm = np.zeros(n)
                sm[period] = series[1:period + 1].sum()
                for i in range(period + 1, n):
                    sm[i] = sm[i - 1] - (sm[i - 1] / period) + series[i]
                return sm

            TRs = wilder(TR, lookback)
            DMps = wilder(DMp, lookback)
            DMms = wilder(DMm, lookback)

            plus_di = np.zeros(n)
            minus_di = np.zeros(n)
            dx = np.zeros(n)

            for i in range(lookback, n):
                if TRs[i] == 0:
                    continue
                plus_di[i] = (DMps[i] / TRs[i]) * 100.0
                minus_di[i] = (DMms[i] / TRs[i]) * 100.0
                denom = plus_di[i] + minus_di[i]
                if denom > 0:
                    dx[i] = (abs(plus_di[i] - minus_di[i]) / denom) * 100.0

            # ADX: Wilder smoothing of DX
            ADX = np.zeros(n)
            if n >= lookback * 2:
                seed = dx[lookback:lookback * 2].mean()
                ADX[lookback * 2 - 1] = seed
                for i in range(lookback * 2, n):
                    ADX[i] = ((ADX[i - 1] * (lookback - 1)) + dx[i]) / lookback
                adx_val = ADX[-1]
            else:
                adx_val = dx[-1]

            pdi_val = plus_di[-1]
            mdi_val = minus_di[-1]

            adx_d = Decimal(str(round(float(adx_val), 6)))
            pdi_d = Decimal(str(round(float(pdi_val), 6)))
            mdi_d = Decimal(str(round(float(mdi_val), 6)))

            _adx_cache_local[symbol] = (now_ts, (adx_d, pdi_d, mdi_d))
            return adx_d, pdi_d, mdi_d

        except Exception as e:
            self.logger.debug(f"[ADX] compute failed for {symbol}: {e}")
            return None

    # ==================== > Main Monitors < =================#
    async def run_positions_exit_sentinel(self, interval_sec: int = 3):
        while True:
            try:
                await self.sweep_positions_for_exits()
            except Exception:
                self.logger.exception("positions_exit_sentinel error")
            await asyncio.sleep(interval_sec)

    async def monitor_orders_and_assets(self):
        """
        Monitor active open orders, calculate profitability, and handle active TP/SL or limit orders.
        Now fully standardized for both limit and TP/SL orders.
        """
        #Fast, positions-first exit sweep (bypasses gates)
        await self.sweep_positions_for_exits()

        profit_data_list = []
        usd_avail = self._get_usd_available()
        order_mgmt = self.shared_data_manager.order_management

        async with self.order_tracker_lock:

            order_tracker = self._normalize_order_tracker_snapshot(order_mgmt)

            for order_id, raw_order in order_tracker.items():
                try:
                    order_data = OrderData.from_dict(raw_order)
                    symbol = order_data.trading_pair
                    asset = re.split(r'[-/]', symbol)[0]

                    # ✅ Skip if asset not in non-zero balances (not held)
                    if asset not in order_mgmt.get("non_zero_balances", {}):
                        continue

                    # ✅ Get precision and asset details (wallet + staked funds included)
                    precision = self.shared_utils_precision.fetch_precision(symbol)
                    order_data.base_decimal, order_data.quote_decimal = precision[:2]
                    order_data.product_id = symbol

                    info = raw_order.get("info", {})
                    order_duration = self._compute_order_duration(
                        info.get("created_time", raw_order.get("datetime", ""))
                    )

                    current_price = self.bid_ask_spread.get(symbol, Decimal("0"))
                    asset_balance, avg_price, cost_basis = self._get_asset_details(order_mgmt, asset, precision)

                    # ✅ Handle active orders (limit and TP/SL now unified)
                    if order_data.side == "sell":
                        # Optional debug for TP/SL orders
                        if order_data.trigger.get("tp_sl_flag"):
                            self.logger.debug(f"TP/SL order treated as standard limit sell: {symbol}")

                        await self._handle_limit_sell(
                            order_data,
                            symbol,
                            asset,
                            precision,
                            order_duration,
                            avg_price,
                            current_price
                        )

                    elif order_data.side == "buy":
                        await self._handle_active_tp_sl_decision(
                            order_data,
                            raw_order,
                            symbol,
                            asset,
                            current_price,
                            avg_price,
                            precision,
                        )

                    # ✅ Prepare profitability calculation
                    required_prices = {
                        "avg_price": avg_price,
                        "cost_basis": cost_basis,
                        "asset_balance": asset_balance,
                        "current_price": current_price,
                        "usd_avail": usd_avail,
                        "status_of_order": order_data.status,
                    }

                    profit = await self.profit_data_manager.calculate_profitability(
                        symbol, required_prices, self.bid_ask_spread, self.usd_pairs
                    )

                    if profit:
                        # Re-run handling with profitability info if needed
                        if order_data.side == "sell":
                            await self._handle_limit_sell(
                                order_data,
                                symbol,
                                asset,
                                precision,
                                order_duration,
                                avg_price,
                                current_price
                            )
                        elif order_data.side == "buy":
                            await self._handle_active_tp_sl_decision(
                                order_data,
                                raw_order,
                                symbol,
                                asset,
                                current_price,
                                avg_price,
                                precision,
                                profit
                            )

                        profit_data_list.append(profit)

                except Exception as e:
                    self.logger.error(f"❌ Error handling tracked order {order_id}: {e}", exc_info=True)

        if profit_data_list:
            df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            self.logger.info("Profit data for open orders",
                           extra={'profit_data': df.to_dict(orient='records') if hasattr(df, 'to_dict') else str(df)})

    async def monitor_untracked_assets(self):
        self.logger.info("📱 Starting monitor_untracked_assets")
        usd_prices = self._get_usd_prices()
        if not usd_prices or not self.non_zero_balances:
            self.logger.warning("⚠️ Skipping due to missing prices or balances")
            return

        for asset, position in self.non_zero_balances.items():
            try:
                result = await self._analyze_position(asset, position, usd_prices)
                if not result:
                    continue

                symbol, asset, current_price, qty, avg_entry, profit, profit_pct, precision_data = result

                if not await self._passes_holding_cooldown(symbol, intent="EXIT"):
                    continue

                await self._manage_untracked_position_exit(
                    symbol=symbol,
                    asset=asset,
                    qty=qty,
                    profit_pct=profit_pct,
                    avg_entry=avg_entry,
                    current_price=current_price,
                    precision_data=precision_data
                )
            except Exception as e:
                self.logger.error(f"❌ Error analyzing {asset}: {e}", exc_info=True)

        self.logger.info("✅ monitor_untracked_assets completed")

    async def sweep_positions_for_exits(self) -> None:
        """
        Positions-first safety sweep with two-section locking and DAO fallback.
        """
        try:
            # NEW: Run position monitor first (independent of position fetch)
            self.logger.debug(f"[ASSET_MONITOR] About to call position_monitor.check_positions(), monitor object: {self.position_monitor}")
            await self.position_monitor.check_positions()

            # ── Section 1: short snapshot of the in-memory order tracker ──
            async with self.order_tracker_lock:
                tracker_snapshot = self._normalize_order_tracker_snapshot(
                    self.shared_data_manager.order_management
                )

            # Fetch positions using resilient helper (DAO or fallback)
            positions = await self._fetch_open_positions_snapshot()

            now = self.shared_data_manager.now() if hasattr(self.shared_data_manager, "now") else dt.datetime.now(dt.timezone.utc)
            rearmed, with_oco, naked, skipped_grace = 0, 0, 0, 0
            precision_cache: dict[str, tuple] = {}

            # ── Compute candidates (no locks held) ──
            candidates: list[tuple] = []
            for p in positions:
                symbol = getattr(p, "symbol", None)
                if not symbol:
                    continue
                asset = symbol.split("-")[0] if "-" in symbol else symbol
                qty = getattr(p, "size", None)
                avg_cost = getattr(p, "avg_cost", None)
                opened_at = getattr(p, "opened_at", None)
                if qty is None or avg_cost is None:
                    continue
                age = (now - opened_at).total_seconds() if isinstance(opened_at, dt.datetime) else 9999.0

                # Is there already a live bracket child in our snapshot?
                child = self._find_live_bracket_child(tracker_snapshot, symbol)
                if child:
                    with_oco += 1
                    continue

                naked += 1
                candidates.append((symbol, asset, qty, avg_cost, age))

            # ── Act per symbol with per-symbol lock and double-check ──
            for (symbol, asset, qty, avg_cost, age) in candidates:
                if age < POSITIONS_EXIT_OCO_GRACE_SEC:
                    skipped_grace += 1
                    self.logger.debug(
                        f"[POS-EXIT] grace-skip {symbol} age={age:.2f}s<{POSITIONS_EXIT_OCO_GRACE_SEC}s"
                    )
                    continue

                async with self._sym_lock(symbol):
                    # Double-check under short global lock
                    async with self.order_tracker_lock:
                        tracker_now = self._normalize_order_tracker_snapshot(
                            self.shared_data_manager.order_management
                        )
                    if self._find_live_bracket_child(tracker_now, symbol):
                        self.logger.debug(f"[POS-EXIT] {symbol} gained protection; skip action")
                        continue

                    # Precision cache
                    if symbol not in precision_cache:
                        precision_cache[symbol] = self.shared_utils_precision.fetch_precision(symbol)
                    precision = precision_cache[symbol]

                    if POSITIONS_EXIT_REARM:
                        trigger = self.trade_order_manager.build_trigger(
                            "rearm_from_position_sweep",
                            f"arming protection qty={qty} avg={avg_cost}"
                        )
                        new_order = await self.trade_order_manager.build_order_data(
                            source="websocket",
                            trigger=trigger,
                            asset=asset,
                            product_id=symbol,
                            side="sell",
                        )
                        if not new_order:
                            self.logger.warning(f"[POS-EXIT] build OCO failed for {symbol}; will retry later")
                            continue

                        success, resp = await self.trade_order_manager.place_order(
                            new_order,
                            precision,
                            intent="EXIT"
                        )
                        if success:
                            rearmed += 1
                            self.logger.info(f"🛡️ Rearmed OCO for {symbol} from positions sweep: {resp}")
                        else:
                            self.logger.warning(f"[POS-EXIT] OCO place failed for {symbol}: {resp}")
                    else:
                        ok, resp = await self._place_passive_exit_for_position(symbol, asset, qty, avg_cost)
                        if ok:
                            rearmed += 1
                        else:
                            self.logger.warning(f"[POS-EXIT] passive exit failed for {symbol}: {resp}")

            self.logger.debug(
                f"[LIVENESS] pos={len(positions)} with_oco={with_oco} naked={naked} "
                f"skipped_grace={skipped_grace} rearmed={rearmed}"
            )

        except Exception:
            self.logger.exception("sweep_positions_for_exits failed")


