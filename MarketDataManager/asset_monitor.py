import asyncio
import time
import re
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from webhook.webhook_validate_orders import OrderData


class AssetMonitor:
    def __init__(self, *,listener, logger, config, shared_data_manager, trade_order_manager, order_manager, trade_recorder, profit_data_manager,
                 order_book_manager, shared_utils_precision, shared_utils_color, shared_utils_date_time):

        self.logger = logger
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
        await self.monitor_active_orders()
        await self.monitor_untracked_assets()

    async def monitor_active_orders(self):
        """
        Monitor active open orders, calculate profitability, and handle active TP/SL or limit orders.
        Now fully standardized for both limit and TP/SL orders.
        """
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
                            precision
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
            print(f"Profit Data Open Orders:\n{df.to_string(index=True)}")

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

                if not await self._passes_holding_cooldown(symbol):
                    continue

                await self._handle_tp_sl_decision(symbol, asset, current_price, qty, avg_entry,
                                                  profit, profit_pct, precision_data)
            except Exception as e:
                self.logger.error(f"❌ Error analyzing {asset}: {e}", exc_info=True)

        self.logger.info("✅ monitor_untracked_assets completed")

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

    async def _passes_holding_cooldown(self, symbol: str) -> bool:
        try:
            order_id = await self.trade_recorder.find_latest_unlinked_buy(symbol)
            if not order_id:
                return True

            trade = await self.trade_recorder.fetch_trade_by_order_id(order_id)
            if not trade or not trade.order_time:
                return True

            now = datetime.now(timezone.utc)
            trade_time = trade.order_time.astimezone(timezone.utc)
            held_for = now - trade_time

            if held_for.total_seconds() < 0:
                self.logger.warning(
                    f"⚠️ Time anomaly detected for {symbol}: negative hold duration {held_for} "
                    f"(now={now}, trade_time={trade_time})"
                )
                return True

            return held_for >= timedelta(minutes=self.min_cooldown)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not evaluate cooldown for {symbol}: {e}", exc_info=True)
            return True

    async def _handle_tp_sl_decision(self, symbol, asset, current_price, qty, avg_entry, profit, profit_pct, precision):
        try:
            if asset in self.hodl:
                return

            open_order = next((o for o in self.open_orders.values() if o.get("symbol") == symbol), None)
            info = open_order.get("info", {}) if open_order else {}
            order_price = Decimal(info.get("average_filled_price", "0") or "0")

            if profit_pct >= self.take_profit:
                trigger = self.trade_order_manager.build_trigger("TP", f"profit_pct={profit_pct:.2%} ≥ take_profit={self.take_profit:.2%}")
                if open_order and current_price > order_price:
                    await self.order_manager.cancel_order(info.get("order_id"), symbol)
                    open_order = None
                if not open_order:
                    await self._place_order("websocket", trigger, asset, symbol, precision)

            elif profit_pct <= self.stop_loss:
                trigger = self.trade_order_manager.build_trigger("SL", f"profit_pct={profit_pct:.2%} < stop_loss={self.stop_loss:.2%}")
                if open_order and current_price < order_price:
                    await self.order_manager.cancel_order(info.get("order_id"), symbol)
                    open_order = None
                if not open_order:
                    await self._place_order("websocket", trigger, asset, symbol, precision)

        except Exception as e:
            self.logger.error(f"❌ Error handling TP/SL for {symbol}: {e}", exc_info=True)

    async def _place_order(self, source, trigger, asset, symbol, precision):
        order_data = await self.trade_order_manager.build_order_data(source=source, trigger=trigger, asset=asset, product_id=symbol, side='sell')
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

    async def _handle_active_tp_sl_decision(
            self,
            order_data: OrderData,
            full_order: dict,
            symbol: str,
            asset: str,
            current_price: Decimal,
            avg_price: Decimal,
            precision_data: tuple,
            profit_data: dict,  # new
    ):
        try:
            quote_deci = precision_data[1]
            current_price = current_price.quantize(Decimal('1.' + '0' * quote_deci))
            avg_price = avg_price.quantize(Decimal('1.' + '0' * quote_deci))

            profit_pct = Decimal(profit_data["profit percent"].strip('%')) / 100

            trigger_config = full_order['info']['order_configuration']['trigger_bracket_gtc']
            old_limit_price = Decimal(trigger_config.get('limit_price', '0')).quantize(Decimal('1.' + '0' * quote_deci))

            # Determine if price change justifies update
            if current_price > old_limit_price:
                trigger = self.trade_order_manager.build_trigger(
                    "TP",
                    f"profit_pct={profit_pct:.2%} → price rose above TP ({current_price} > {old_limit_price})"
                )
            elif current_price < old_limit_price:
                trigger = self.trade_order_manager.build_trigger(
                    "SL",
                    f"profit_pct={profit_pct:.2%} → price fell below SL ({current_price} < {old_limit_price})"
                )
            else:
                return  # No update needed

            await self.listener.order_manager.cancel_order(order_data.order_id, symbol)

            new_order_data = await self.trade_order_manager.build_order_data(
                source='websocket',
                trigger=trigger,
                asset=asset,
                product_id=symbol,
                side='sell',
            )

            if new_order_data:
                success, response = await self.trade_order_manager.place_order(new_order_data, precision_data)
                log_method = self.logger.info if success else self.logger.warning
                log_method(f"{'✅' if success else '⚠️'} Updated SL/TP for {symbol} at {current_price}: {response}")

        except Exception as e:
            self.logger.error(f"❌ Error in _handle_active_tp_sl_decision for {symbol}: {e}", exc_info=True)


    async def _handle_limit_sell(self, order_data, symbol, asset, precision, order_duration, avg_price, current_price):
        order_book = await self.order_book_manager.get_order_book(order_data, symbol)
        highest_bid = order_book["highest_bid"] if order_book and order_book.get("highest_bid") else current_price


        # Adjust trailing stop logic
        if order_data.price < min(current_price, highest_bid) and order_duration > 5:
            await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
            new_order_data = await self.trade_order_manager.build_order_data('websocket', 'trailing_stop', asset, symbol, order_data.price, None)
            await self.listener.handle_order_fill(new_order_data)
            return

        # NEW: adjust limit sell if price is creeping upward from loss
        if order_data.price < avg_price and current_price > order_data.price and order_duration > 5:
            trigger = self.trade_order_manager.build_trigger(
                "limit_sell_adjusted",
                f"Recovering price: old={order_data.price}, current={current_price}, avg={avg_price}"
            )
            await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
            new_order_data = await self.trade_order_manager.build_order_data('websocket', trigger, asset, symbol, None, 'limit', 'sell')
            if new_order_data:
                success, response = await self.trade_order_manager.place_order(new_order_data, precision)
                log = self.logger.info if success else self.logger.warning
                log(f"{'✅' if success else '⚠️'} Adjusted limit SELL for {symbol}: {response}")
