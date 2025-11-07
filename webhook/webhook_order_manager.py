
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Dict, Union, Optional

import os
import pandas as pd
from pandas.core.methods.describe import select_describe_func

from Config.config_manager import CentralConfig as config
from webhook.webhook_validate_orders import OrderData

# Define the TradeOrderManager class
"""This class  will manage the trade orders."""


class TradeOrderManager:
    _instance = None

    @classmethod
    def get_instance(cls, coinbase_api=None, exchange_client=None, shared_utils_precision=None, shared_utils_utility= None, validate=None,
                     logger_manager=None, alerts=None, ccxt_api=None, market_data_updater= None, order_book_manager=None, order_types=None,
                     websocket_helper=None, shared_data_manager=None, session=None, profit_manager=None):
        """
        Singleton method to ensure only one instance of TradeOrderManager exists.
        If already instantiated, returns the existing instance.
        """
        if cls._instance is None:
            cls._instance = cls(coinbase_api, exchange_client, shared_utils_precision,
                                shared_utils_utility, validate, logger_manager, alerts,
                                ccxt_api, market_data_updater, order_book_manager,
                                order_types, websocket_helper,shared_data_manager,
                                session, profit_manager)
        return cls._instance

    def __init__(self, coinbase_api, exchange_client, shared_utils_precision, shared_utils_utility, validate, logger_manager,
                 alerts, ccxt_api, market_data_updater, order_book_manager, order_types, websocket_helper, shared_data_manager, session,
                 profit_manager):
        """
        Initializes the TradeOrderManager.
        """
        self.config = config()
        self.coinbase_api = coinbase_api
        self.test_mode = self.config.test_mode
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_order_amount_fiat = self.config.min_order_amount_fiat
        self._min_sell_value = self.config.min_sell_value
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._order_size_fiat = self.config.order_size_fiat
        self._hodl = self.config.hodl
        self._default_maker_fee = self.config.maker_fee
        self._default_taker_fee = self.config.taker_fee
        # --- Guardrails (configurable) ---
        self.allow_buys_on_red_day = bool(self.config.allow_buys_on_red_day)

        self.logger = logger_manager  # 🙂

        self.validate= validate
        self.order_types = order_types
        self.order_book_manager = order_book_manager
        self.market_data_updater = market_data_updater
        self.websocket_helper = websocket_helper
        self._shared_data_manager = shared_data_manager

        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.profit_manager = profit_manager
        self.session = session


    @property
    def default_maker_fee(self):
        return self._default_maker_fee

    @property
    def fee_info(self):
        return self.shared_data_manager.market_data.get('fee_info', {})

    @property
    def default_taker_fee(self):
        return self._default_taker_fee

    @property
    def shared_data_manager(self):
        return self._shared_data_manager

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def spot_position(self):
        return self.shared_data_manager.market_data.get('spot_positions')


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
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get("order_tracker")

    @property
    def passive_orders(self):
        return self.shared_data_manager.order_management.get('passive_orders') or {}

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

    @property
    def min_order_amount(self):
        return self._min_order_amount_fiat

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self._max_value_of_crypto_to_buy_more

    @property
    def order_size(self):
        return float(self._order_size_fiat)

    @staticmethod
    def _get_env_pct(name: str, default: float) -> Decimal:
        try:
            return Decimal(os.getenv(name, str(default)))
        except Exception:
            return Decimal(str(default))

    def _fee_pct_for_side(self) -> Decimal:
        side = os.getenv("FEE_SIDE", "taker").lower()
        taker = self._get_env_pct("TAKER_FEE", 0.0055)
        maker = self._get_env_pct("MAKER_FEE", 0.0030)
        return taker if side == "taker" else maker

    def _read_stop_mode(self) -> str:
        m = os.getenv("STOP_MODE", "atr").lower()
        return m if m in ("atr", "fixed") else "atr"

    def _infer_spread_pct_from_orderbook(self, order_book: Optional[dict]) -> Decimal:
        """
        Expects {'bid': <Decimal/float/str>, 'ask': <...>}
        Returns spread% as a fraction (e.g., 0.0015 == 0.15%)
        """
        try:
            if not order_book:
                return self._get_env_pct("SPREAD_CUSHION_PCT", 0.0015)
            bid = Decimal(str(order_book.get("bid")))
            ask = Decimal(str(order_book.get("ask")))
            if ask > 0:
                return max((ask - bid) / ask, Decimal("0"))
        except Exception:
            pass
        return self._get_env_pct("SPREAD_CUSHION_PCT", 0.0015)

    def _compute_atr_pct_from_ohlcv(self, ohlcv: Optional[list], entry_price: Decimal, period: int = 14) -> Optional[Decimal]:
        """
        ohlcv rows: [ts, open, high, low, close, volume], newest last.
        Returns ATR/entry as a fraction (e.g., 0.007 == 0.7%).
        """
        if not ohlcv or len(ohlcv) < period + 1 or entry_price <= 0:
            return None
        trs = []
        prev_close = Decimal(str(ohlcv[0][4]))
        for row in ohlcv[1:]:
            high = Decimal(str(row[2]))
            low = Decimal(str(row[3]))
            close = Decimal(str(row[4]))
            tr = max(high - low, abs(high - prev_close), abs(prev_close - low))
            trs.append(tr)
            prev_close = close
            if len(trs) > period:
                trs.pop(0)
        if not trs:
            return None
        atr = sum(trs) / Decimal(len(trs))
        return atr / entry_price

    def _compute_stop_pct_long(self,entry_price: Decimal, ohlcv: Optional[list], order_book: Optional[dict]) -> Decimal:
        """
        Percent (fraction) below entry for a LONG stop, including cushions.
        """
        mode = self._read_stop_mode()
        fee_pct = self._fee_pct_for_side()
        spread_pct = self._infer_spread_pct_from_orderbook(order_book)

        if mode == "atr":
            atr_mult = self._get_env_pct("ATR_MULTIPLIER_STOP", 1.8)
            min_pct = self._get_env_pct("STOP_MIN_PCT", 0.012)
            atr_pct = self._compute_atr_pct_from_ohlcv(ohlcv, entry_price) or Decimal("0")
            base_pct = max(min_pct, atr_pct * atr_mult)
        else:
            # legacy fixed mode: STOP_LOSS might be negative in env; use abs()
            fixed = abs(self._get_env_pct("STOP_LOSS", 0.01))
            base_pct = fixed

        # cushions: spread + one side fee (likely taker)
        stop_pct = base_pct + spread_pct + fee_pct
        return max(Decimal("0"), stop_pct)

    def _compute_tp_price_long(self, entry_price: Decimal) -> Decimal:
        # existing env TAKE_PROFIT used as (1 + TAKE_PROFIT)
        tp = self._get_env_pct("TAKE_PROFIT", 0.025)
        return entry_price * (Decimal("1") + tp)


    async def build_order_data(
            self,
            source: str,
            trigger: Union[str, Dict[str, str]],
            asset: str,
            product_id: str,
            stop_price: Optional[Decimal] = None,
            order_type: Optional[str] = None,
            side: Optional[str] = None,
            test_mode: bool = False
    ) -> Optional[OrderData]:
        """
        Builds a fully prepared OrderData instance, including validation and test-mode overrides.

        Returns:
            Optional[OrderData]: Fully constructed OrderData or None if validation fails.
        """
        self.build_failure_reason = None  # Clear previous reason

        try:
            # ✅ Market data validation
            if not test_mode and self.market_data_updater.get_empty_keys(self.market_data):
                self.build_failure_reason = f"Market data incomplete — skipping {asset}"
                return None

            # ✅ Setup basic vars
            trading_pair = product_id.replace("/", "-")
            spot = self.spot_position.get(asset, {})
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(asset)
            quote_quantizer = Decimal("1").scaleb(-quote_deci)

            passive_order_data = self.passive_orders.get(asset, {})
            usd_data = self.spot_position.get("USD", {})
            usd_balance = Decimal(usd_data.get("total_balance_fiat", 0))
            usd_avail = self.shared_utils_precision.safe_quantize(
                Decimal(usd_data.get("available_to_trade_fiat", 0)), quote_quantizer
            )
            min_order_threshold = getattr(self, "min_order_threshold", Decimal("5.00"))

            # ✅ Bid/Ask & initial pricing
            bid_ask = self.bid_ask_spread.get(trading_pair, {})
            bid = Decimal(bid_ask.get("bid", 0))
            ask = Decimal(bid_ask.get("ask", 0))
            current_bid = self.shared_utils_precision.safe_quantize(bid, quote_quantizer)
            current_ask = self.shared_utils_precision.safe_quantize(ask, quote_quantizer)
            spread = Decimal(bid_ask.get("spread", 0)) # value not percentage
            spread = self.shared_utils_precision.safe_quantize(spread, quote_quantizer)
            price = (current_bid + current_ask) / 2 if (current_bid and current_ask) else Decimal("0")

            # --- Spread% ---
            spread_abs = (current_ask - current_bid) if (current_bid and current_ask) else Decimal("0")
            mid = ((current_bid + current_ask) / 2) if (current_bid and current_ask) else Decimal("0")
            spread_pct = (spread_abs / mid) if mid else Decimal("0")
            spread_pct_q = self.shared_utils_precision.safe_quantize(spread_pct, Decimal("1e-6"))

            # --- ATR% (if available from shared state) ---
            # Expect either an ATR in price terms + divide by price, or a precomputed pct (0.01 == 1%).
            atr_pct_val = None
            try:
                # Example 1: a direct pct cache
                atr_pct_cache = (self.shared_data_manager.market_data.get('atr_pct_cache') or {})
                atr_pct_val = atr_pct_cache.get(trading_pair)

                # Example 2: ATR in price units — convert to pct
                if atr_pct_val is None:
                    atr_price_cache = (self.shared_data_manager.market_data.get('atr_price_cache') or {})
                    atr_price = atr_price_cache.get(trading_pair)
                    if atr_price and price:
                        atr_pct_val = (Decimal(atr_price) / price)
            except Exception:
                atr_pct_val = None


            total_balance_crypto = Decimal(spot.get("total_balance_crypto", 0))
            available_to_trade = Decimal(spot.get("available_to_trade_crypto", 0))
            fiat_amt = min(usd_avail, self.order_size)
            crypto_amt = available_to_trade
            order_amount_fiat, order_amount_crypto = self.shared_utils_utility.initialize_order_amounts(
                side if side else "buy", fiat_amt, crypto_amt
            )

            # ✅ Centralized test mode overrides
            if test_mode:
                (usd_avail, usd_balance, spot, price,
                 order_amount_fiat, order_amount_crypto) = self.apply_test_mode_overrides(
                    asset=asset,
                    usd_avail=usd_avail,
                    usd_balance=usd_balance,
                    spot=spot,
                    price=price,
                    order_amount_fiat=order_amount_fiat,
                    order_amount_crypto=order_amount_crypto
                )

            # ✅ Handle new assets
            if not spot and not passive_order_data and not side:
                if usd_avail >= min_order_threshold or test_mode:
                    self.logger.info(f"💡 {'[TEST MODE] ' if test_mode else ''}Proceeding with buy for new asset {asset}")
                    spot = {}
                else:
                    self.build_failure_reason = f"Skipping {asset} — no wallet, no passive order, and USD < {min_order_threshold}"
                    return None

            if source == "passivemm" and not passive_order_data:
                if usd_avail >= min_order_threshold or test_mode:
                    self.logger.info(
                        f"💡 {'[TEST MODE] ' if test_mode else ''}passivemm initializing first-time quote for {asset}"
                    )
                    passive_order_data = {}
                else:
                    self.build_failure_reason = f"passivemm skipping {asset} — no passive data and insufficient USD."
                    return None
                self.shared_utils_utility.get_passive_order_data(passive_order_data)

            # ✅ Price validation
            if price == 0:
                self.build_failure_reason = f"Price is zero for {trading_pair}"
                return None

            # ✅ Side fallback logic
            if side is None:
                side = "buy" if usd_avail >= self.order_size or test_mode else "sell"

            # ✅ Skip bad momentum for buys
            # ✅ Skip bad momentum for buys (now honors allow_buys_on_red_day)
            if side == "buy" and not test_mode:
                try:
                    usd_pairs = self.usd_pairs.set_index("asset")
                    price_change_24h = usd_pairs.loc[asset, 'price_percentage_change_24h'] if asset in usd_pairs.index else None

                    # If we can't read the signal, be conservative but don't crash
                    if price_change_24h is None:
                        self.build_failure_reason = f"Skipping BUY for {asset} — no 24h price data"
                        return None

                    pc = Decimal(price_change_24h)

                    if not self.allow_buys_on_red_day:
                        # Original behavior: disallow on any red day
                        if pc <= 0:
                            self.build_failure_reason = f"Skipping BUY for {asset} — 24h change {pc}% and allow_buys_on_red_day=false"
                            return None
                    else:
                        # Softer rule when allowed: only block if VERY red (tunable)
                        red_floor = Decimal(str(getattr(self.config, "red_day_floor_pct", -2)))  # e.g. -2%
                        if pc <= red_floor:
                            self.build_failure_reason = f"Skipping BUY for {asset} — 24h change {pc}% below red_day_floor_pct={red_floor}%"
                            return None
                except Exception as e:
                    self.logger.warning(f"⚠️ 24h change check failed for {asset}: {e}")
                    # Fail closed or open? Keep current conservative behavior:
                    self.build_failure_reason = f"24h price change check failed for {asset}"
                    return None

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to check price change for {asset}: {e}")
                    self.build_failure_reason = f"24h price change check failed for {asset}"
                    return None

            # ✅ Fees
            maker_fee = Decimal(
                self.fee_info.get('fee_rates', {}).get('maker') or self.default_maker_fee) if self.fee_info else self.default_maker_fee
            taker_fee = Decimal(
                self.fee_info.get('fee_rates', {}).get('taker') or self.default_taker_fee) if self.fee_info else self.default_taker_fee

            # ✅ Trigger
            trigger_note = f"triggered by {trigger}" if isinstance(trigger, str) else trigger.get("trigger_note", "")
            trigger_dict = trigger if isinstance(trigger, dict) else self.build_trigger(trigger, trigger_note)

            # ✅ Final OrderData
            return OrderData(
                trading_pair=trading_pair,
                time_order_placed=None,
                type=order_type or "limit",
                order_id="UNKNOWN",
                side=side,
                order_amount_fiat=order_amount_fiat,
                order_amount_crypto=order_amount_crypto,
                filled_price=None,
                base_currency=asset,
                quote_currency="USD",
                usd_avail_balance=usd_avail,
                usd_balance=usd_balance,
                base_avail_balance=available_to_trade,
                total_balance_crypto=total_balance_crypto,
                available_to_trade_crypto=available_to_trade,
                base_decimal=base_deci,
                quote_decimal=quote_deci,
                quote_increment=quote_quantizer,
                highest_bid=current_bid,
                lowest_ask=current_ask,
                maker=maker_fee,
                taker=taker_fee,
                spread=spread,
                spread_pct=spread_pct_q,
                atr_pct=atr_pct_val,
                open_orders={},
                status="UNKNOWN",
                source=source,
                trigger=trigger_dict,
                price=price,
                cost_basis=Decimal("0"),
                limit_price=price,
                average_price=None,
                adjusted_price=None,
                adjusted_size=None,
                stop_loss_price=stop_price,
                take_profit_price=None,
                avg_quote_volume=self.avg_quote_volume,
                volume_24h=None
            )

        except Exception as e:
            self.logger.error(f"❌ Error in build_order_data for {asset} {trigger}: {e}", exc_info=True)
            self.build_failure_reason = f"Exception during order build: {e}"
            return None

    def apply_test_mode_overrides(
            self,
            asset: str,
            usd_avail: Decimal,
            usd_balance: Decimal,
            spot: dict,
            price: Optional[Decimal] = None,
            order_amount_fiat: Optional[Decimal] = None,
            order_amount_crypto: Optional[Decimal] = None
    ) -> tuple:
        """
        Centralized test mode overrides for balances, price, and order amounts.
        Ensures safe dummy values for testing without affecting live trading logic.

        Args:
            asset (str): The base asset being traded (e.g., 'ETH').
            usd_avail (Decimal): Available USD balance before overrides.
            usd_balance (Decimal): Total USD balance before overrides.
            spot (dict): Spot balance dict for the asset (crypto amounts).
            price (Decimal|None): Current calculated price (fallbacks to 1.00 if zero).
            order_amount_fiat (Decimal|None): Fiat order amount before overrides.
            order_amount_crypto (Decimal|None): Crypto order amount before overrides.

        Returns:
            tuple: (usd_avail, usd_balance, spot, price, order_amount_fiat, order_amount_crypto)
                   with test mode adjustments applied.
        """
        self.logger.warning(f"⚠️ [TEST MODE] Applying overrides for {asset}")

        # ✅ Ensure dummy USD balances (cap to configured order size)
        usd_avail = min(usd_avail, self.order_size)
        usd_balance = min(usd_balance, self.order_size)

        # ✅ Ensure dummy crypto balances (safe fallback for SELL or TP/SL logic)
        spot = {
            "total_balance_crypto": max(Decimal(spot.get("total_balance_crypto", 0)), Decimal("1.0")),
            "available_to_trade_crypto": max(Decimal(spot.get("available_to_trade_crypto", 0)), Decimal("1.0"))
        }

        # ✅ Force safe fallback price if missing or zero
        if price is not None and price <= 0:
            self.logger.warning(f"⚠️ [TEST MODE] Forcing dummy price for {asset}")
            price = Decimal("1.00")

        # ✅ Ensure reasonable order amounts for testing
        if order_amount_fiat is not None:
            order_amount_fiat = min(order_amount_fiat, usd_avail)
        if order_amount_crypto is not None:
            order_amount_crypto = max(order_amount_crypto, Decimal("0.01"))

        return usd_avail, usd_balance, spot, price, order_amount_fiat, order_amount_crypto

    def build_trigger(self,trigger_type: str, note: str = "") -> dict:
        return {
            "trigger": trigger_type.upper(),
            "trigger_note": note
        }

    async def place_order(self, raw_order_data: OrderData, precision_data=None) -> tuple[bool, dict]:
        try:
            self.shared_utils_utility.log_event_loop("place_order")
            trading_pair = raw_order_data.trading_pair
            all_open_orders = self.open_orders  # shared state
            has_open_order, open_order = self.shared_utils_utility.has_open_orders(trading_pair, all_open_orders)

            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()
            raw_order_data.open_orders = not open_orders.empty and raw_order_data.trading_pair in open_orders.symbol.values

            if not precision_data:
                precision_data = self.shared_utils_precision.fetch_precision(raw_order_data.trading_pair)
                base_deci, quote_deci, _, _ = precision_data
                raw_order_data.base_decimal = base_deci
                raw_order_data.quote_decimal = quote_deci

            # Step 1: Light validation
            validation_result = self.validate.validate_order_conditions(raw_order_data, open_orders)
            if not validation_result["is_valid"] or has_open_order:
                return False, validation_result

            # Step 2: Get order book
            order_book_details = self.bid_ask_spread.get(trading_pair)

            # Step 3: Full validation
            validation_result = self.validate.fetch_and_validate_rules(raw_order_data)
            if not validation_result["is_valid"]:
                return False, validation_result

            # Step 4: Construct final OrderData
            order_data = self.validate.build_order_data_from_validation_result(
                validation_result, order_book_details, precision_data
            )

            return await self.handle_order(order_data, order_book_details)

        except Exception as ex:
            self.logger.error(ex, exc_info=True)
            return False, {
                "success": False,
                "status": "rejected",
                "reason": "INVALID_LIMIT_PRICE_POST_ONLY",
                "trigger": raw_order_data.trigger,
                "source": raw_order_data.source,
                "symbol": raw_order_data.trading_pair,
                "side": raw_order_data.side.upper(),
                "price": str(raw_order_data.adjusted_price),
                "amount": str(raw_order_data.adjusted_size),
                "attempts": 0,
                "message": "Would match immediately",
                "note": "Post-only rule violation",
                "response": None,
            }

    async def handle_order(self, order_data: OrderData, order_book_details: dict) -> tuple[bool, dict]:
        """
        Handles a validated order: adjusts price and size, calculates TP/SL, and attempts order placement.

        Args:
            order_data (OrderData): Fully validated and normalized order details.

        Returns:
            Tuple[bool, dict]: Success flag and the order response (or error).
        """
        try:
            self.logger.debug(f"⚙️ Handling order for {order_data.trading_pair}")

            side = order_data.side.lower()
            type = order_data.type.lower()
            maker_fee = order_data.maker
            taker_fee = order_data.taker

            base_deci = order_data.base_decimal
            quote_deci = order_data.quote_decimal

            # Adjust price and size
            adjusted_price, adjusted_size_of_order_qty = self.shared_utils_precision.adjust_price_and_size(
                {
                    'side': side,
                    'type': type,
                    'maker_fee': maker_fee,
                    'taker_fee': taker_fee,
                    'base_avail_to_trade': order_data.base_avail_balance,
                    'sell_amount': order_data.base_avail_balance,
                    'order_amount_fiat': order_data.order_amount_fiat,
                    'quote_decimal': quote_deci,
                    'base_decimal': base_deci
                },
                {
                    'highest_bid': order_data.highest_bid,
                    'lowest_ask': order_data.lowest_ask
                }
            )

            if adjusted_price is None or adjusted_size_of_order_qty is None:
                return False, {
                    "success": False,
                    "status": "rejected",
                    "reason": "PRICE_OR_SIZE_ADJUSTMENT_FAILED",
                    "symbol": order_data.trading_pair,
                    "side": order_data.side.upper(),
                    "price": str(order_data.adjusted_price),
                    "amount": str(order_data.adjusted_size),
                    "trigger": order_data.trigger,
                    "message": "Failed to adjust price or size",
                    "note": "Adjustment returned None",
                    "source": order_data.source,
                    "response": {}
                }

            # Update OrderData basics; TP/SL will be computed later (profit_manager or local fallback)
            order_data.adjusted_price = adjusted_price
            order_data.adjusted_size = adjusted_size_of_order_qty

            # Choose order type
            order_type = self.order_type_to_use(side, order_data)

            self.logger.debug(f"🧠 Order Type: {order_type} | Adjusted Price: {adjusted_price} | Size: {adjusted_size_of_order_qty}")
            return await self.attempt_order_placement(order_data, order_type)

        except Exception as ex:
            self.logger.error(f"⚠️ Error in handle_order: {ex}", exc_info=True)
            return False, {
                "success": False,
                "status": "rejected",
                "reason": "HANDLE_ORDER_EXCEPTION",
                "symbol": order_data.trading_pair,
                "side": order_data.side.upper(),
                "price": str(order_data.adjusted_price),
                "amount": str(order_data.adjusted_size),
                "trigger": order_data.trigger,
                "message": str(ex),
                "note": "Exception caught in handle_order",
                "source": order_data.source,
                "response": {}
            }

    async def attempt_order_placement(self, order_data: OrderData, order_type: str, max_attempts: int = 3) -> tuple[bool, dict]:
        symbol = order_data.trading_pair
        side = order_data.side.lower()
        quote_deci = order_data.quote_decimal
        base_deci = order_data.base_decimal

        response = None
        await self.market_data_updater.run_single_refresh_market_data()

        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.debug(f"📤 Attempt #{attempt} to place {order_type} order for {symbol}...")

                # Refresh bid/ask
                order_book = self.bid_ask_spread.get(symbol)
                highest_bid = self.shared_utils_precision.safe_quantize(Decimal(order_book['bid']), Decimal(f"1e-{quote_deci}"))
                lowest_ask = self.shared_utils_precision.safe_quantize(Decimal(order_book['ask']), Decimal(f"1e-{quote_deci}"))
                spread = self.shared_utils_precision.safe_quantize(lowest_ask - highest_bid, Decimal(f"1e-{quote_deci}"))

                order_data.highest_bid = highest_bid
                order_data.lowest_ask = lowest_ask
                order_data.spread = spread

                # Post-only price adjustment
                order_data.adjusted_price = self.get_post_only_price(highest_bid, lowest_ask, order_data.quote_increment, side)
                order_data.adjusted_price = order_data.adjusted_price.quantize(Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP)

                if getattr(order_data, 'post_only', False):
                    if (side == 'buy' and order_data.adjusted_price >= lowest_ask) or \
                            (side == 'sell' and order_data.adjusted_price <= highest_bid):
                        return False, {
                            'success': False,
                            'status': 'rejected',
                            'reason': 'INVALID_LIMIT_PRICE_POST_ONLY',
                            'trigger': order_data.trigger,
                            'source': order_data.source,
                            'symbol': symbol,
                            'side': side.upper(),
                            'price': str(order_data.adjusted_price),
                            'amount': str(order_data.adjusted_size),
                            'attempts': attempt,
                            'message': 'Would match immediately',
                            'note': 'Post-only rule violation',
                        }

                # TP/SL calc (centralized)
                if order_type in ['tp_sl', 'limit', 'bracket']:
                    tp = sl = None

                    # Prefer centralized profit_manager if present
                    if self.profit_manager and hasattr(self.profit_manager, "calculate_tp_sl"):
                        try:

                            tp, sl = await self.profit_manager.calculate_tp_sl(order_data)
                        except Exception as e:
                            self.logger.warning(f"⚠️ profit_manager.calculate_tp_sl failed, falling back: {e}")

                    # Local ATR/fixed fallback
                    if tp is None or sl is None:
                        entry = order_data.adjusted_price
                        # We already refreshed order_book above
                        order_book = {'bid': highest_bid, 'ask': lowest_ask}

                        # optional: pull small OHLCV window if your updater exposes it
                        ohlcv = None
                        if hasattr(self.market_data_updater, "get_recent_ohlcv"):
                            try:
                                # symbol may be e.g. 'ZKC-USD' in trading_pair, or base in base_currency
                                base = order_data.base_currency
                                ohlcv = self.market_data_updater.get_recent_ohlcv(base, window=200)  # newest last
                            except Exception as e:
                                self.logger.debug(f"OHLCV fetch failed for {order_data.trading_pair}: {e}")

                        # Long-only here (if you support shorts, mirror the sign)
                        tp_price = self._compute_tp_price_long(entry)
                        stop_pct = self._compute_tp_price_long(entry, ohlcv, order_book)
                        sl_price = entry * (Decimal("1") - stop_pct)

                        # Precision
                        tp = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, tp_price, convert="quote")
                        sl = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, sl_price, convert="quote")

                        self.logger.info(
                            f"tp/sl calc {order_data.trading_pair} side={side} mode={self._read_stop_mode()} "
                            f"entry={entry} tp={tp} sl={sl} "
                            f"spread%={self._infer_spread_pct_from_orderbook(order_book):.5f} "
                            f"fee%={self._fee_pct_for_side():.5f}"
                        )

                    order_data.take_profit_price, order_data.stop_loss_price = tp, sl

                # Balance check
                if side == 'buy':
                    cost = order_data.adjusted_price * order_data.adjusted_size
                    if cost > order_data.usd_avail_balance:
                        if not self.test_mode:
                            return False, {
                                'success': False,
                                'status': 'rejected',
                                'reason': 'INSUFFICIENT_USD',
                                'trigger': order_data.trigger,
                                'source': order_data.source,
                                'symbol': symbol,
                                'side': side.upper(),
                                'price': str(order_data.adjusted_price),
                                'amount': str(order_data.adjusted_size),
                                'attempts': attempt,
                                'message': f"Need ${cost:.2f}, have ${order_data.usd_avail_balance:.2f}",
                                'note': 'Balance check failed'
                            }

                # Order submission
                if order_type == 'limit':
                    response = await self.order_types.place_limit_order(order_data.source, order_data)
                elif order_type == 'tp_sl':
                    response = await self.order_types.process_limit_and_tp_sl_orders(order_data.source, order_data, tp, sl)
                else:
                    return False, {
                        'success': False,
                        'status': 'rejected',
                        'reason': 'UNKNOWN_ORDER_TYPE',
                        'trigger': order_data.trigger,
                        'source': order_data.source,
                        'symbol': symbol,
                        'side': side.upper(),
                        'message': f"Order type {order_type} not recognized"
                    }

                # Success
                if response.get('success') and response.get('order_id'):
                    order_data.order_id = response.get('order_id')
                    return True, {
                        'success': True,
                        'status': 'placed',
                        'order_id': order_data.order_id,
                        'trigger': order_data.trigger,
                        'source': order_data.source,
                        'symbol': symbol,
                        'side': side.upper(),
                        'price': str(order_data.adjusted_price),
                        'amount': str(order_data.adjusted_size)
                    }

                # Failure — propagate standard failure details
                error_resp = response.get('response', {}).get('error_response', {})
                return False, {
                    'success': False,
                    'status': response.get('status', 'rejected'),
                    'trigger': order_data.trigger,
                    'source': order_data.source,
                    'symbol': symbol,
                    'side': side.upper(),
                    'price': str(order_data.adjusted_price),
                    'amount': str(order_data.adjusted_size),
                    'attempts': attempt,
                    'reason': error_resp.get('error', response.get('reason', 'unKnown')),
                    'message': error_resp.get('message', response.get('message', '')),
                    'response': response,
                    'note': f"Failed after {attempt} attempt(s) — check funds, size, or post-only rules"
                }

            except Exception as ex:
                self.logger.error(f"❌ Exception during {symbol} order attempt #{attempt}: {ex}", exc_info=True)
                return False, {
                    'success': False,
                    'status': 'error',
                    'trigger': order_data.trigger,
                    'source': order_data.source,
                    'symbol': symbol,
                    'side': side.upper(),
                    'price': str(order_data.adjusted_price),
                    'amount': str(order_data.adjusted_size),
                    'attempts': attempt,
                    'reason': 'EXCEPTION',
                    'message': str(ex),
                    'note': f"Exception raised on attempt #{attempt}"
                }

        # All attempts exhausted
        return False, {
            'success': False,
            'status': 'failed',
            'trigger': order_data.trigger,
            'source': order_data.source,
            'symbol': symbol,
            'side': side.upper(),
            'price': str(order_data.adjusted_price),
            'amount': str(order_data.adjusted_size),
            'attempts': max_attempts,
            'reason': 'MAX_ATTEMPTS_REACHED',
            'message': "All retry attempts failed.",
            'note': "Order placement failed after retries."
        }

    def order_type_to_use(self, side, order_data):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.

        if order_data.trigger and order_data.trigger.get("trigger") == "passive_buy":
            validation_result = 'limit'
            return validation_result
        if side == 'buy':
            validation_result = 'tp_sl'
            return validation_result
        elif side == 'sell':
            validation_result = 'limit'
            return validation_result

    def get_post_only_price(self, highest_bid, lowest_ask, quote_increment, side):
        adjustment = quote_increment * 2  # or 1 if tighter spacing is acceptable
        if side == 'buy':
            return (lowest_ask - adjustment).quantize(quote_increment, rounding=ROUND_HALF_UP)
        else:
            return (highest_bid + adjustment).quantize(quote_increment, rounding=ROUND_HALF_UP)



