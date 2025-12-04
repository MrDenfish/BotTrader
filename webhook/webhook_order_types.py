
import os
import json
import uuid
from pathlib import Path
from statistics import pstdev
from cachetools import TTLCache
from typing import Optional, Union
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from datetime import datetime, timedelta, timezone
from webhook.webhook_validate_orders import OrderData
from Config.config_manager import CentralConfig as Config
from Shared_Utils.logger import get_logger


# Define the OrderTypeManager class
"""This class  will manage the order types 
    -Limit 
    -Market 
    -Bracket.
"""


class OrderTypeManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color,
                     shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                     websocket_helper, session):
        """
        Singleton method to ensure only one instance of OrderTypeManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color,
                                shared_data_manager, validate, logger_manager, alerts, ccxt_api, order_book_manager,
                                websocket_helper, session)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, shared_utils_color, shared_data_manager,
                 validate, logger_manager, alerts, ccxt_api, order_book_manager, websocket_helper, session):
        self.config = Config()
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        # self.base_url = self.config._api_url
        self.logger = logger_manager  # 🙂
        self.structured_logger = get_logger('webhook', context={'component': 'order_types'})

        self.validate = validate
        self.order_book_manager = order_book_manager
        self.websocket_helper = websocket_helper
        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_color = shared_utils_color
        self.shared_data_manager = shared_data_manager
        self.session = session  # Store the session as an attribute
        self.start_time = self.ticker_cache = self.non_zero_balances = self.market_data = None
        self.order_tracker = self.market_cache_usd = self.market_cache_vol = self.order_management = None
        # ✅ Tracks recent orders to prevent duplicate placements
        self.recent_orders = TTLCache(maxsize=1000, ttl=10)  # Stores recent orders for 10 seconds

        # trade parameters
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._sell_ratio = Decimal(self.config.sell_ratio)
        self._buy_ratio = Decimal(self.config.buy_ratio)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._trailing_limit = Decimal(self.config.trailing_limit)
        self._min_sell_value = Decimal(self.config.min_sell_value)
        self.spread_to_fee_min = Decimal(self.config.spread_to_fee_min)
        self.tp_min_ticks = int(self.config.tp_min_ticks)
        self.sl_limit_offset_ticks = int(self.config.sl_limit_offset_ticks)
        self.min_l1_notional_usd = self.config.min_l1_notional_usd
        self.pre_bracket_sigma_ratio = Decimal(self.config.pre_bracket_sigma_ratio)
        self._hodl = Config.hodl


    @property
    def hodl(self):
        return self._hodl

    @property
    def fee_info(self):
        return self.shared_data_manager.market_data.get('fee_info', {})
    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get("bid_ask_spread", {})

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def sell_ratio(self):
        return self._sell_ratio

    @property
    def buy_ratio(self):
        return self._buy_ratio

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def trailing_stop(self):
        return self._trailing_stop

    @property
    def trailing_limit(self):
        return self._trailing_limit

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def get_orderbook_snapshot(
            self,
            product_id: str,
            quote_deci: int,
            fetch_missing_sizes: bool = True
    ) -> Optional[dict]:
        """
        Returns a merged L1 snapshot for `product_id`:
          {
            "bid": Decimal, "ask": Decimal, "spread": Decimal,
            "bid_size_1": Decimal|None, "ask_size_1": Decimal|None,
            "mid": Decimal
          }
        Uses self.bid_ask_spread (fast path). If sizes are missing and
        fetch_missing_sizes=True, fetches /product_book(limit=1) and merges.
        """
        qquant = Decimal("1").scaleb(-int(quote_deci))

        # --- 1) Fast path: read what we already have
        book = (self.bid_ask_spread or {}).get(product_id) or {}
        bid_raw = book.get("bid")
        ask_raw = book.get("ask")
        spread_raw = book.get("spread")

        # If we don't even have prices, bail early
        if bid_raw is None or ask_raw is None:
            return None

        # Quantize to quote precision
        bid = self.shared_utils_precision.safe_quantize(Decimal(str(bid_raw)), qquant)
        ask = self.shared_utils_precision.safe_quantize(Decimal(str(ask_raw)), qquant)
        spread = self.shared_utils_precision.safe_quantize(Decimal(str(spread_raw)) if spread_raw is not None else (ask - bid), qquant)
        mid = (bid + ask) / Decimal("2")

        # Sizes may already be present if you enriched bid_ask_spread in step B
        bid_sz = book.get("bid_size_1")
        ask_sz = book.get("ask_size_1")
        try:
            bid_sz = Decimal(str(bid_sz)) if bid_sz is not None else None
            ask_sz = Decimal(str(ask_sz)) if ask_sz is not None else None
        except Exception:
            bid_sz = ask_sz = None

        # --- 2) If sizes missing and allowed, fetch product_book(limit=1)
        if fetch_missing_sizes and (bid_sz is None or ask_sz is None):
            try:
                pb = await self.coinbase_api.get_product_book(product_id, limit=1)
                if pb and pb.get("bids") and pb.get("asks"):
                    b1 = pb["bids"][0]
                    a1 = pb["asks"][0]
                    # Validate price alignment (rarely, fast market might shift)
                    # If price moved beyond 1 tick from our bid/ask, we still keep fast prices,
                    # but accept sizes anyway—they’re close enough for risk checks.
                    bid_sz = b1.get("size") if bid_sz is None else bid_sz
                    ask_sz = a1.get("size") if ask_sz is None else ask_sz
            except Exception as e:
                self.logger.debug(f"product_book fetch failed for {product_id}: {e}")

        # Normalize result
        return {
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "mid": mid,
            "bid_size_1": bid_sz,
            "ask_size_1": ask_sz,
        }

    def pre_bracket_filter(
            self,
            *,
            side: str,
            entry_price: Decimal,
            tp_price: Decimal,
            sl_price: Decimal,
            order_book: dict,  # expects: {"bid": Decimal, "ask": Decimal}
            fees: dict,  # {"maker": Decimal, "taker": Decimal}
            tick_size: Decimal,  # quote tick increment
            recent_mid_series: list[Decimal] | None = None,
            depth_top: dict | None = None  # {"bid_1": Decimal, "ask_1": Decimal} optional
    ) -> tuple[bool, dict]:
        """
        Returns (ok_to_place, info). If False, caller should delay/bracket-modify.
        Adds:
          - L1 far-side notional guard (requires depth_top).
          - Stronger micro-volatility vs. TP distance gate (configurable).
        """

        reasons: list[str] = []
        risk = 0

        # --- config / tunables (env or attributes) --------------------------------
        # spread / fee threshold (you already set self.spread_to_fee_min upstream)
        try:
            spread_to_fee_min = Decimal(str(getattr(self, "spread_to_fee_min", "2.0")))
        except Exception:
            spread_to_fee_min = Decimal("2.0")

        # how strict the micro-vol vs TP check is; 1.0 = sigma must be <= TP distance
        try:
            sigma_vs_tp_ratio = Decimal(str(getattr(self, "sigma_vs_tp_ratio",str(self.pre_bracket_sigma_ratio))))
        except Exception:
            sigma_vs_tp_ratio = Decimal("1.0")

        # minimum notional on the far side L1 (ask for buys, bid for sells)
        try:
            min_l1_notional_usd = Decimal(str(getattr(self, "min_l1_notional_usd",str(self.min_l1_notional_usd))))
        except Exception:
            min_l1_notional_usd = Decimal("250")

        # --------------------------------------------------------------------------

        bid = Decimal(str(order_book.get("bid", "0")))
        ask = Decimal(str(order_book.get("ask", "0")))
        if bid <= 0 or ask <= 0 or entry_price <= 0:
            return False, {"risk": 10, "reasons": ["invalid_book_or_entry"]}

        mid = (bid + ask) / Decimal("2")

        # 1) Spread-to-fee screen
        spread_pct = (ask - bid) / mid
        maker = Decimal(str(fees.get("maker", "0")))
        taker = Decimal(str(fees.get("taker", "0")))
        fee_both_pct = maker + taker
        spread_to_fee = (spread_pct / fee_both_pct) if fee_both_pct > 0 else Decimal("9999")
        if spread_to_fee < spread_to_fee_min:
            risk += 1
            reasons.append(f"spread_to_fee_low={float(spread_to_fee):.3f}")

        # 2) Marketable/near-touch TP/SL screen (within 1 tick)
        one_tick = tick_size
        if side.lower() == "buy":
            if tp_price <= ask + one_tick:
                risk += 1;
                reasons.append("tp_near_or_inside_ask")
            if sl_price >= bid - one_tick:
                risk += 1;
                reasons.append("sl_near_or_inside_bid")
        else:  # sell
            if tp_price >= bid - one_tick:
                risk += 1;
                reasons.append("tp_near_or_inside_bid")
            if sl_price <= ask + one_tick:
                risk += 1;
                reasons.append("sl_near_or_inside_ask")

        # 3) Micro-volatility vs TP distance (10–30s) — stricter using sigma_vs_tp_ratio
        if recent_mid_series and len(recent_mid_series) >= 10:
            try:
                mids = [Decimal(str(x)) for x in recent_mid_series[-30:]]
                sigma_abs = Decimal(str(pstdev([float(x) for x in mids])))
                sigma_pct = sigma_abs / mid if mid > 0 else Decimal("0")
                tp_dist_pct = abs(tp_price - entry_price) / entry_price
                # If recent volatility approaches TP distance, likely chop → raise risk
                if tp_dist_pct > 0 and sigma_pct > (tp_dist_pct * sigma_vs_tp_ratio):
                    risk += 1;
                    reasons.append("microvol_rel_high")
            except Exception:
                pass

        # 4) Depth imbalance (optional)
        l1_imbalance = None
        if depth_top:
            bid1 = Decimal(str(depth_top.get("bid_1", "0")))
            ask1 = Decimal(str(depth_top.get("ask_1", "0")))
            if bid1 > 0 and ask1 > 0:
                l1_imbalance = (bid1 / ask1) if ask1 != 0 else Decimal("9999")
                if l1_imbalance < Decimal("0.7") or l1_imbalance > Decimal("1.5"):
                    risk += 1;
                    reasons.append(f"depth_imbalance={float(l1_imbalance):.2f}")

            # 5) NEW: far-side L1 notional guard (helps prevent “instant TP/SL swipes”)
            try:
                if side.lower() == "buy" and ask1 > 0:
                    far_side_notional = ask1 * ask  # ask size * ask px
                    if far_side_notional < min_l1_notional_usd:
                        risk += 1;
                        reasons.append(f"thin_far_side_ask_notional={float(far_side_notional):.2f}")
                elif side.lower() == "sell" and bid1 > 0:
                    far_side_notional = bid1 * bid  # bid size * bid px
                    if far_side_notional < min_l1_notional_usd:
                        risk += 1;
                        reasons.append(f"thin_far_side_bid_notional={float(far_side_notional):.2f}")
            except Exception:
                pass

        ok = (risk < 2)
        info = {
            "risk": int(risk),
            "reasons": reasons,
            "spread_to_fee": float(spread_to_fee),
        }
        if l1_imbalance is not None:
            info["l1_imbalance"] = float(l1_imbalance)
        return ok, info

    def adjust_targets_for_resting(
        self,
        *,
        side: str,
        tp_price: Decimal,
        sl_price: Decimal,
        order_book: dict,
        tick_size: Decimal,
        min_tp_ticks: int = 2,
        sl_limit_offset_ticks: int = 1,
    ) -> dict:
        """
        Enforce 'resting' TP and a stop-limit with small offset to avoid instant swipes.
        Returns dict with 'tp_price','sl_stop','sl_limit','post_only':True
        """
        bid = Decimal(str(order_book["bid"])); ask = Decimal(str(order_book["ask"]))
        one = tick_size
        # push TP at least N ticks outside touch
        if side.lower() == "buy":
            min_tp = ask + (one * min_tp_ticks)
            tp_price = max(tp_price, min_tp)
            sl_stop = sl_price
            sl_limit = sl_price - (one * sl_limit_offset_ticks)
        else:
            min_tp = bid - (one * min_tp_ticks)
            tp_price = min(tp_price, min_tp)
            sl_stop = sl_price
            sl_limit = sl_price + (one * sl_limit_offset_ticks)

        return {
            "tp_price": tp_price,
            "sl_stop": sl_stop,
            "sl_limit": sl_limit,
            "post_only": True,
        }

    async def process_limit_and_tp_sl_orders(
            self,
            source: str,
            order_data: OrderData,
            take_profit: Optional[Decimal] = None,
            stop_loss: Optional[Decimal] = None
    ) -> Union[dict, None]:
        """
        Places a limit order with attached TP and SL (TP/SL-first behavior).
        Assumes upstream validation & precision adjustment already done.

        Args:
            source: Origin of the request (e.g., 'sighook', 'webhook').
            order_data: Validated and normalized order data.
            take_profit: Optional TP override (Decimal).
            stop_loss: Optional SL override (Decimal).

        Returns:
            dict: Coinbase API response enriched with metadata, or None on failure.
        """
        try:
            trading_pair = order_data.trading_pair.replace("/", "-")
            asset = order_data.base_currency

            print_order_data = self.shared_utils_utility.pretty_summary(order_data)
            self.structured_logger.info(
                "Processing TP/SL Order",
                extra={
                    'source': source,
                    'trading_pair': trading_pair,
                    'order_summary': print_order_data
                }
            )

            # ✅ Avoid duplicate open orders
            has_open_order, open_order = self.shared_utils_utility.has_open_orders(
                trading_pair, self.open_orders
            )
            if has_open_order:
                return {
                    "error": "open_order",
                    "code": 611,
                    "message": f"⚠️ Order Blocked - Existing Open Order for {trading_pair}"
                }

            # ✅ Validation Recheck (failsafe)
            validation_result = self.validate.fetch_and_validate_rules(order_data)
            if not validation_result.get("is_valid"):
                condition = validation_result.details.get("condition", validation_result.get("error"))
                return {
                    "error": "order_not_valid",
                    "code": validation_result.get("code"),
                    "message": f"⚠️ Order Blocked {asset} - Trading Rules Violation: {condition}"
                }

            # ✅ Precision-Safe Adjustments
            base_quant = Decimal(f"1e-{order_data.base_decimal}")
            quote_quant = Decimal(f"1e-{order_data.quote_decimal}")

            # ✅ Precision-Safe Adjustments (robust to None/str)
            def _D(x):
                try:
                    return Decimal(str(x))
                except Exception:
                    return None

            base_quant = Decimal("1").scaleb(-int(order_data.base_decimal))
            quote_quant = Decimal("1").scaleb(-int(order_data.quote_decimal))

            px = _D(getattr(order_data, "adjusted_price", None) or getattr(order_data, "price", None))
            sz = _D(getattr(order_data, "adjusted_size", None) or getattr(order_data, "size", None))
            notional = _D(getattr(order_data, "order_amount_fiat", None))

            # Derive size from notional/price if needed
            if (sz is None or sz <= 0) and px is not None and px > 0 and notional is not None and notional > 0:
                sz = (notional / px)

            if px is None or px <= 0 or sz is None or sz <= 0:
                self.logger.error(f"❌ SIZE/PRICE missing or invalid: px={px} sz={sz} notional={notional}")
                return {
                    "success": False,
                    "status": "rejected",
                    "reason": "PRICE_OR_SIZE_INVALID",
                    "message": f"Invalid price/size: px={px} sz={sz} notional={notional}",
                    "order_id": None,
                    "error_response": {"message": "Invalid price/size"}
                }

            adjusted_price = px.quantize(quote_quant, rounding=ROUND_DOWN)
            adjusted_size = sz.quantize(base_quant, rounding=ROUND_DOWN)

            # ✅ TP/SL from upstream (or override if passed explicitly)
            tp_price = _D(take_profit or getattr(order_data, "take_profit_price", None) or 0) or Decimal("0")
            sl_price = _D(stop_loss or getattr(order_data, "stop_loss_price", None) or 0) or Decimal("0")
            tp_price = tp_price.quantize(quote_quant, rounding=ROUND_DOWN)
            sl_price = sl_price.quantize(quote_quant, rounding=ROUND_DOWN)

            # ✅ Basic balance checks (especially for BUYs)
            if order_data.side.upper() == "BUY":
                maker_fee = _D(getattr(order_data, "maker", 0)) or Decimal("0")
                usd_bal = _D(getattr(order_data, "usd_balance", 0)) or Decimal("0")
                usd_required = adjusted_size * adjusted_price * (Decimal("1") + maker_fee)
                if usd_required > usd_bal:
                    return {
                        "success": False,
                        "code": 402,
                        "error": "Insufficient_USD",
                        "message": (
                            f"⚠️ Order Blocked - Insufficient USD (${usd_bal}) "
                            f"for {asset} BUY. Required: ${usd_required}"
                        ),
                        "order_id": None
                    }
                if adjusted_size <= 0:
                    return {
                        "success": False,
                        "error": "Zero_Size",
                        "code": 700,
                        "message": f"⚠️ Order Blocked - Zero Size for {asset} BUY.",
                        "order_id": None
                    }
            elif order_data.side.upper() == "SELL":
                avail = _D(getattr(order_data, "available_to_trade_crypto", 0)) or Decimal("0")
                if adjusted_size > avail:
                    return {
                        "success": False,
                        "error": "Insufficient_Crypto",
                        "code": 614,
                        "message": f"⚠️ Order Blocked - Insufficient Crypto to sell {asset}.",
                        "order_id": None
                    }

            # ✅ Coinbase Order Payload (TP/SL attached)
            client_order_id = str(uuid.uuid4())

            # === Rapid-fire pre-bracket screen ===
            base_deci, quote_deci, base_increment, quote_increment = self.shared_utils_precision.fetch_precision(asset)
            if not isinstance(quote_increment, Decimal):
                quote_increment = Decimal(str(quote_increment)) if quote_increment is not None else Decimal("0")
            quote_quantizer = Decimal("1").scaleb(-quote_deci)

            bid_ask = self.bid_ask_spread.get(trading_pair, {})
            spread = Decimal(bid_ask.get("spread", 0))  # value not percentage
            spread = self.shared_utils_precision.safe_quantize(spread, quote_quantizer)
            maker_fee = Decimal(str(self.fee_info.get(trading_pair, {}).get("maker", "0")))
            taker_fee = Decimal(str(self.fee_info.get(trading_pair, {}).get("taker", "0")))
            ob = await self.get_orderbook_snapshot(trading_pair, quote_deci, fetch_missing_sizes=True)
            if not ob:
                return {
                    "success": False,
                    "status": "rejected",
                    "reason": "ORDERBOOK_UNAVAILABLE",
                    "message": f"No orderbook available for {trading_pair}",
                    "order_id": None,
                    "error_response": {"message": "Orderbook unavailable"}
                }

            best_bid = ob["bid"]
            best_ask = ob["ask"]
            bid_sz_1 = ob["bid_size_1"]
            ask_sz_1 = ob["ask_size_1"]

            # recent mids (deque) if available
            tp_id = trading_pair
            hist_deque = getattr(self, "mid_history", {}).get(tp_id) if hasattr(self, "mid_history") else None
            recent_mids_last_30 = list(hist_deque)[-30:] if hist_deque else None

            ok, info = self.pre_bracket_filter(
                side=order_data.side,
                entry_price=adjusted_price,
                tp_price=tp_price,
                sl_price=sl_price,
                order_book={"bid": best_bid, "ask": best_ask},
                fees={"maker": maker_fee, "taker": taker_fee},
                tick_size=(quote_increment if quote_increment and quote_increment > 0 else Decimal("1").scaleb(-quote_deci)),
                recent_mid_series=recent_mids_last_30,
                depth_top=({"bid_1": bid_sz_1, "ask_1": ask_sz_1} if (bid_sz_1 and ask_sz_1) else None),
            )

            if not ok:
                self.logger.info(
                    f"🛡 Bracket deferred for {order_data.trading_pair}: "
                    f"risk={info['risk']} reasons={info['reasons']}"
                )
                # Mitigation B: force TP to rest ≥ 2 ticks and use stop-limit SL
                adj = self.adjust_targets_for_resting(
                    side=order_data.side,
                    tp_price=tp_price,
                    sl_price=sl_price,
                    order_book={"bid": best_bid, "ask": best_ask},
                    tick_size=quote_increment,
                    min_tp_ticks=self.tp_min_ticks,
                    sl_limit_offset_ticks=self.sl_limit_offset_ticks,
                )
                tp_price = self.shared_utils_precision.safe_quantize(adj["tp_price"], quote_quantizer)
                sl_price = self.shared_utils_precision.safe_quantize(adj["sl_stop"], quote_quantizer)
                sl_limit = self.shared_utils_precision.safe_quantize(adj["sl_limit"], quote_quantizer)
                tp_post_only = adj["post_only"]

            order_payload = {
                "client_order_id": client_order_id,
                "product_id": trading_pair,
                "side": order_data.side.upper(),
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": str(adjusted_size),
                        "limit_price": str(adjusted_price)
                    }
                },
                "attached_order_configuration": {
                    "trigger_bracket_gtc": {
                        "limit_price": str(tp_price),
                        "stop_trigger_price": str(sl_price)
                    }
                }
            }

            self.logger.debug(f"📤 Submitting TP/SL Order → {order_payload}")

            order_data.time_order_placed = datetime.now()

            # ✅ Submit to Coinbase API
            response = await self.coinbase_api.create_order(order_payload)

            if isinstance(response, dict) and response.get("success") and response.get("success_response", {}).get("order_id"):
                order_id = response["success_response"]["order_id"]
                self.structured_logger.order_sent(
                    "TP/SL Order Placed Successfully",
                    extra={'order_id': order_id, 'trading_pair': trading_pair}
                )

                # ✅ Task 1: Store bracket order tracking for coordination
                # Initialize bracket_orders dict if it doesn't exist
                if 'bracket_orders' not in self.shared_data_manager.order_management:
                    self.shared_data_manager.order_management['bracket_orders'] = {}

                # Store bracket metadata for position monitor coordination
                self.shared_data_manager.order_management['bracket_orders'][trading_pair] = {
                    'entry_order_id': order_id,
                    'stop_order_id': None,  # Will be populated from websocket fills
                    'tp_order_id': None,    # Will be populated from websocket fills
                    'stop_price': float(sl_price),
                    'tp_price': float(tp_price),
                    'entry_price': float(adjusted_price),
                    'entry_time': datetime.now(timezone.utc),
                    'side': order_data.side.upper(),
                    'status': 'active',
                    'source': order_data.source
                }

                self.logger.debug(
                    f"[BRACKET_TRACK] {trading_pair} bracket stored: "
                    f"entry={adjusted_price:.4f}, TP={tp_price:.4f}, SL={sl_price:.4f}"
                )

                return {
                    **response,
                    "status": "placed",
                    "trigger": order_data.trigger,
                    "source": order_data.source,
                    "order_id": order_id,
                    "tp": float(tp_price),
                    "sl": float(sl_price)
                }

            # Ensure we always return a dict
            if not isinstance(response, dict):
                self.logger.error("❌ create_order returned non-dict/None; coercing to failure")
                return {
                    "success": False,
                    "status": "rejected",
                    "reason": "ADAPTER_RETURNED_NONE",
                    "message": "Adapter returned None/invalid",
                    "order_id": None,
                    "error_response": {"message": "Adapter returned None/invalid"}
                }
            self.structured_logger.warning(
                "TP/SL Order Rejected",
                extra={
                    'trading_pair': trading_pair,
                    'error_message': response.get('error_response', {}).get('message')
                }
            )
            response.setdefault("success", False)
            response.setdefault("order_id", None)
            return response

        except Exception as e:
            self.logger.error(f"❌ Error in process_limit_and_tp_sl_orders: {e}", exc_info=True)
            return {
                "success": False,
                "status": "rejected",
                "reason": "PROCESS_LIMIT_TP_SL_EXCEPTION",
                "message": str(e),
                "order_id": None,
                "error_response": {"message": str(e)}
            }

    async def place_limit_order(self, source, order_data: OrderData):
        """
        Places a post-only limit order with retries and dynamic buffer adjustment to avoid rejections.
        Returns structured metadata, but does not record the trade until it's filled.
        """

        def is_post_only_rejection(resp: dict) -> bool:
            msg = (resp.get('message') or "").lower()
            reason = (resp.get('reason') or "").lower()
            return any(k in msg for k in ["post-only", "priced below", "match existing"]) or \
                any(k in reason for k in ["post-only", "invalid_limit_price"])

        try:  # ✅ Outer try block added here

            symbol = order_data.trading_pair.replace('/', '-')
            asset = symbol.split('-')[0]
            side = order_data.side.upper()

            amount = self.shared_utils_precision.safe_convert(order_data.adjusted_size, order_data.base_decimal)
            price = self.shared_utils_precision.safe_convert(
                order_data.highest_bid if side == 'SELL' else order_data.lowest_ask,
                order_data.quote_decimal
            )
            available_crypto = self.shared_utils_precision.safe_convert(order_data.available_to_trade_crypto, order_data.base_decimal)
            usd_available = self.shared_utils_precision.safe_convert(order_data.usd_balance, order_data.quote_decimal)

            attempts = 0
            price_buffer_pct = Decimal('0.001')
            min_buffer = Decimal('0.0000001')
            max_buffer = Decimal('0.01')

            while attempts < 3:
                attempts += 1

                required_fields = ['trading_pair', 'side', 'adjusted_size', 'highest_bid', 'lowest_ask']
                missing = [f for f in required_fields if getattr(order_data, f) is None]
                if missing:
                    return {
                        'success': False,
                        'status': 'rejected',
                        'reason': 'MISSING_FIELDS',
                        'message': f"Missing fields: {missing}",
                        'trigger': order_data.trigger,
                        'source': order_data.source
                    }

                validation_result = self.validate.fetch_and_validate_rules(order_data)
                if not validation_result.get('is_valid'):
                    return {
                        'success': False,
                        'status': 'rejected',
                        'reason': 'TRADING_RULES_VIOLATION',
                        'message': validation_result.details.get("condition"),
                        'trigger': order_data.trigger,
                        'source': order_data.source
                    }

                if side == 'BUY':
                    usd_required = amount * price * (1 + order_data.maker)
                    if usd_required > usd_available:
                        return {
                            'success': False,
                            'status': 'rejected',
                            'reason': 'INSUFFICIENT_USD',
                            'message': f"Not enough USD: need ${usd_required}, have ${usd_available}",
                            'trigger': order_data.trigger,
                            'source': order_data.source
                        }
                else:
                    filled_size = await self.shared_data_manager.trade_recorder.find_latest_filled_size(symbol, side='buy')
                    # Handle case where filled_size is None (precision error or no history)
                    if filled_size is not None and amount > filled_size:
                        if attempts == 1:
                            amount = self.shared_utils_precision.compute_safe_base_size(
                                order_data.available_to_trade_crypto,
                                order_data.base_decimal,
                                filled_size
                            )
                            order_data.trigger["trigger_note"] += f" | clipped to filled size {filled_size}"
                        else:
                            return {
                                'success': False,
                                'status': 'rejected',
                                'reason': 'INSUFFICIENT_CRYPTO',
                                'message': f"Trying to sell {amount}, but only {available_crypto} available.",
                                'trigger': order_data.trigger,
                                'source': order_data.source
                            }

                ob = self.bid_ask_spread.get(order_data.trading_pair, {})
                latest_ask = self.shared_utils_precision.safe_convert(ob.get('ask', price), order_data.quote_decimal)
                latest_bid = self.shared_utils_precision.safe_convert(ob.get('bid', price), order_data.quote_decimal)

                if side == 'BUY':
                    price = min(latest_ask * (1 - price_buffer_pct), latest_ask - min_buffer)
                else:
                    price = max(latest_bid * (1 + price_buffer_pct), latest_bid + min_buffer)

                price = price.quantize(Decimal(f'1e-{order_data.quote_decimal}'), rounding=ROUND_DOWN if side == 'BUY' else ROUND_UP)

                formatted_price = f"{price:.{order_data.quote_decimal}f}"
                formatted_amount = f"{amount:.{order_data.base_decimal}f}"
                payload = {
                    "client_order_id": f"{order_data.source}-{uuid.uuid4().hex[:8]}",
                    "product_id": symbol,
                    "side": side,
                    "order_configuration": {
                        "limit_limit_gtc": {
                            "base_size": formatted_amount,
                            "limit_price": formatted_price,
                            "post_only": True
                        }
                    }
                }

                response = await self.coinbase_api.create_order(payload)

                self.structured_logger.info(
                    f"{order_data.source.upper()} ORDER",
                    extra={
                        'source': order_data.source,
                        'trigger': order_data.trigger.get('trigger'),
                        'symbol': symbol,
                        'response': response
                    }
                )

                if response.get("success"):
                    order_id = response['success_response'].get('order_id')
                    return {
                        'success': True,
                        'status': 'placed',
                        'order_id': order_id,
                        'trigger': order_data.trigger,
                        'source': order_data.source,
                        'symbol': symbol,
                        'side': side,
                        'price': str(price),
                        'amount': str(amount),
                        'attempts': attempts,
                        'response': response
                    }

                if is_post_only_rejection(response):
                    self.logger.warning(f"🔁 Post-only rejection on attempt {attempts}: {response.get('message')}")
                    price_buffer_pct = min(price_buffer_pct + Decimal('0.0005'), max_buffer)
                    continue

                return {
                    'success': False,
                    'status': 'rejected',
                    'trigger': order_data.trigger,
                    'source': order_data.source,
                    'symbol': symbol,
                    'side': side,
                    'price': str(price),
                    'amount': str(amount),
                    'attempts': attempts,
                    'reason': response.get("error_response", {}).get("preview_failure_reason") if response else "Unknown",
                    'message': response.get("error_response", {}).get("message") if response else "No response received",
                    'response': response,
                    'note': f"Failed after {attempts} attempt(s) — check funds, size, or post-only rules"
                }

        except Exception as ex:
            self.logger.error(f"❌ Error in place_limit_order: {ex}", exc_info=True)
            return {
                'success': False,
                'status': 'failed',
                'error': str(ex),
                'trigger': order_data.trigger,
                'source': order_data.source,
                'message': str(ex)
            }

    async def place_trailing_stop_order(self, order_book, order_data, market_price):
        """
        Places a trailing stop order. Returns the API response as a dictionary.
        """
        try:
            client_order_id = str(uuid.uuid4())
            trailing_percentage = Decimal(self.trailing_percentage)  # Now Decimal for consistency
            market_price = Decimal(market_price)
            symbol = order_data['trading_pair']
            asset = symbol.split('/')[0]

            # ✅ Use Available Balance Instead of Total Balance
            spot_position = self.market_data.get('spot_positions', {})
            available_balance = Decimal(spot_position.get(asset, {}).get('available_to_trade_crypto', 0))
            order_value = market_price * available_balance

            # ✅ Ensure order size is valid and remove bad orders
            if order_value < Decimal(1.0) and order_data.get('side').lower() == 'buy':
                self.logger.bad_order(f"There is a balance of {available_balance} for {symbol}and the buy order will not be placed")
                return None
            elif order_value < Decimal(1.0) and order_data.get('side').lower() == 'sell':
                self.logger.bad_order(f"The min value of  this order is less than the $1.00 threshold and will not be placed"
                                      f" {symbol}: {available_balance} ~${order_value}")
                return None
            # ✅ Adjust for Fee Deduction
            maker_fee = Decimal(self.maker_fee)
            base_size = available_balance - (available_balance * maker_fee)

            # ✅ Ensure correct decimal precision
            base_size = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], base_size, convert='base'
            )

            # ✅ Fetch latest price
            endpoint = 'public'
            ticker_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, endpoint, symbol)
            current_price = Decimal(ticker_data['last'])

            # ✅ Prevent invalid price calculations
            if market_price is None:
                raise ValueError("Could not retrieve the latest trade price.")

            # ✅ Calculate the trailing stop price (Fixed for BUY orders)
            if order_data['side'].upper() == 'buy':
                trailing_stop_price = market_price * (Decimal('1.0') + trailing_percentage / Decimal('100'))
            else:
                trailing_stop_price = market_price * (Decimal('1.0') - trailing_percentage / Decimal('100'))

            # ✅ Adjust stop price calculation
            if order_data['side'].upper() == 'buy':
                stop_price = max(trailing_stop_price, current_price * Decimal('1.002'))  # Ensure stop is higher for BUY
            else:
                stop_price = min(trailing_stop_price, current_price * Decimal('0.998'))  # Ensure stop is lower for SELL

            # ✅ Adjust limit price calculation
            if order_data['side'].upper() == 'buy':
                limit_price = stop_price * (
                            Decimal('1.003') + maker_fee)  # Buy limit price must be slightly above stop price
            else:
                limit_price = stop_price * (
                            Decimal('0.997') - maker_fee)  # Sell limit price must be slightly below stop price

            # ✅ Adjust prices for precision
            stop_price = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], stop_price, convert='quote'
            )
            limit_price = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], limit_price, convert='quote'
            )

            base_balance = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], order_data['base_avail_balance'], convert='base'
            )

            adjusted_size = self.shared_utils_precision.adjust_precision(
                order_data['base_decimal'], order_data['quote_decimal'], order_data['available_to_trade_crypto'], convert='quote'
            )

            # ✅ Format Trading Pair for Coinbase API
            product_id = order_data['trading_pair'].replace('/', '-').upper()

            # ✅ Set up the payload
            payload = {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": "sell" if order_data['side'].upper() == 'sell' else "buy",
                "order_configuration": {
                    "stop_limit_stop_limit_gtd": {
                        "base_size": str(adjusted_size) if adjusted_size > 0 else str(base_balance),
                        "stop_price": str(stop_price),
                        "limit_price": str(limit_price),
                        "end_time": (datetime.now(timezone.utc) + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "stop_direction": "STOP_DIRECTION_STOP_DOWN" if order_data['side'].upper() == 'sell' \
                            else "STOP_DIRECTION_STOP_UP"
                    }
                }
            }

            # ✅ Debugging
            self.structured_logger.debug(
                "Payload before sending",
                extra={'payload': payload, 'product_id': product_id}
            )

            # ✅ Send order request to Coinbase API
            response = await self.coinbase_api.create_order(payload)

            if response is None:
                self.logger.error(f"Received None as the response from create_order.")
                return None
            elif 'Insufficient balance in source account' in response:
                self.structured_logger.debug(
                    "Insufficient balance for trailing stop",
                    extra={
                        'available_balance': float(available_balance),
                        'base_size': float(base_size),
                        'trading_pair': order_data['trading_pair']
                    }
                )
                self.logger.info(f"Insufficient funds for trailing stop order: {order_data['trading_pair']}")
                return None

            self.structured_logger.info(
                "Trailing stop order placed",
                extra={'trading_pair': order_data["trading_pair"], 'response_status': response.get('status', 'unknown')}
            )
            response['trigger'] = order_data.trigger
            response['status'] = 'placed'
            response['source'] = order_data.source
            return response, market_price, trailing_stop_price

        except Exception as ex:
            self.logger.error(f" ❌ Error in place_trailing_stop_order: {str(ex)}", exc_info=True)
            return None

    @staticmethod
    async def update_order_payload(order_id, symbol, trailing_stop_price, limit_price, amount):
        return{
            "order_id":order_id,
            "price":str(limit_price),
            "size":str(amount)
        }
