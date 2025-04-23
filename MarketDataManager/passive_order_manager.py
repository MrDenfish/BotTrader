import copy
import time
from decimal import Decimal


class PassiveOrderManager:
    def __init__(self, trade_order_manager, logger, min_spread_pct=Decimal("0.005")):
        """
        Args:
            trade_order_manager: Instance of TradeOrderManager
            logger: LoggerManager or compatible logger
            min_spread_pct: Minimum spread % required to place passive orders (e.g., 0.005 = 0.5%)
        """
        self.trade_order_manager = trade_order_manager
        self.logger = logger
        self.min_spread_pct = min_spread_pct
        self.passive_order_tracker = {}  # {symbol: {'buy': order_id, 'sell': order_id, 'timestamp': float}}

    async def place_passive_orders(self, asset: str, product_id: str):
        try:
            trading_pair = product_id.replace("-", "/")

            # Step 1: Build once
            order_data = await self.trade_order_manager.build_order_data(
                source="PassiveOrderManager",
                trigger="low_vol",
                asset=asset,
                product_id=product_id
            )

            if not order_data:
                self.logger.warning(f"⚠️ Failed to build OrderData for {trading_pair}. Skipping.")
                return

            highest_bid = order_data.highest_bid
            lowest_ask = order_data.lowest_ask
            usd_avail = order_data.usd_avail_balance

            if highest_bid == 0 or lowest_ask == 0:
                self.logger.warning(f"❌ Missing bid/ask for {trading_pair}.")
                return

            if usd_avail < Decimal("25"):
                self.logger.debug(f"� Not enough USD to trade {trading_pair}. Skipping.")
                return

            spread = lowest_ask - highest_bid
            mid_price = (lowest_ask + highest_bid) / 2
            spread_pct = spread / mid_price

            if spread_pct < self.min_spread_pct:
                self.logger.debug(f"⛔ Spread too narrow for {trading_pair}: {spread_pct:.4%}")
                return

            # Step 2: Clone for buy and sell
            adjustment = Decimal("0.0001")

            for side in ["buy", "sell"]:
                cloned = copy.deepcopy(order_data)  # Make a clean clone for safety
                cloned.post_only = True
                cloned.type = "limit"
                cloned.side = side

                if side == "buy":
                    cloned.adjusted_price = (highest_bid - adjustment).quantize(Decimal(f"1e-{cloned.quote_decimal}"))
                else:
                    cloned.adjusted_price = (lowest_ask + adjustment).quantize(Decimal(f"1e-{cloned.quote_decimal}"))

                cloned.trigger = f"passive_{side}@{cloned.adjusted_price}"

                success, result = await self.trade_order_manager.place_order(cloned)

                if success:
                    self.logger.info(f"✅ Passive {side.upper()} order placed for {trading_pair} @ {cloned.adjusted_price}")
                    self.passive_order_tracker[trading_pair] = {
                        **self.passive_order_tracker.get(trading_pair, {}),
                        side: result.get("details", {}).get("order_id"),
                        "timestamp": time.time()
                    }
                else:
                    self.logger.warning(f"⚠️ Passive {side.upper()} order failed for {trading_pair}: {result.get('message')}")

        except Exception as e:
            self.logger.error(f"❌ Error in place_passive_orders() for {product_id}: {e}", exc_info=True)
