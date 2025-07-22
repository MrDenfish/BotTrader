import asyncio
from decimal import Decimal
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

class AccumulationManager:
    """
    Modular accumulation manager to build long-term holdings (e.g., ETH)
    ✅ Signal-Based Accumulation (implemented)
    ✅ Profit-Based Batched Accumulation (stub)
    ✅ Daily PnL-Based Accumulation (new)
    """

    def __init__(
        self,
        exchange,
        logger_manager,
        shared_data_manager,
        shutdown_event: asyncio.Event,
        accumulation_symbol: str = "ETH-USD",
        signal_based_enabled: bool = True,
        profit_based_enabled: bool = False,
        daily_pnl_based_enabled: bool = True,
        profit_allocation_pct: float = 0.5,  # % of trade profit for batched accumulation
        daily_allocation_pct: float = 1.0,   # 100% of daily PnL by default
        accumulation_threshold: float = 25.0,
        accumulation_amount_per_signal: float = 25.0
    ):
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.exchange = exchange
        self.shared_data_manager = shared_data_manager
        self.shutdown_event = shutdown_event

        # Configurable parameters
        self.accumulation_symbol = accumulation_symbol
        self.signal_based_enabled = signal_based_enabled
        self.profit_based_enabled = profit_based_enabled
        self.daily_pnl_based_enabled = daily_pnl_based_enabled
        self.profit_allocation_pct = Decimal(str(profit_allocation_pct))
        self.daily_allocation_pct = Decimal(str(daily_allocation_pct))
        self.accumulation_threshold = Decimal(str(accumulation_threshold))
        self.accumulation_amount_per_signal = Decimal(str(accumulation_amount_per_signal))

        # Internal trackers
        self.accumulated_profit_usd = Decimal("0.0")
        self.last_daily_accumulation_date = None  # Track last run date

        self._initialize_accumulation_ledger()

    async def start_daily_runner(self, run_time: dt_time = dt_time(hour=0, minute=5)):
        """
        Starts a daily scheduled runner to execute PnL-based accumulation at a specified UTC time.
        Default: 00:05 UTC.
        """
        self.logger.info(f"🕒 Daily accumulation runner started. Scheduled for {run_time} UTC daily.")

        while not self.shutdown_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                target_dt = datetime.combine(now.date(), run_time, tzinfo=timezone.utc)

                if now >= target_dt:
                    target_dt = target_dt + timedelta(days=1)

                wait_seconds = (target_dt - now).total_seconds()
                self.logger.debug(f"⏳ Next accumulation run in {wait_seconds / 3600:.2f} hours")

                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=wait_seconds)
                    break
                except asyncio.TimeoutError:
                    pass  # It’s time to run

                await self.accumulate_daily_from_realized_pnl()

            except Exception as e:
                self.logger.error(f"❌ Error in daily accumulation runner: {e}")
                await asyncio.sleep(3600)  # Wait an hour before retrying
    # ============================
    # 🔹 SIGNAL-BASED ACCUMULATION
    # ============================
    async def accumulate_on_signal(self, signal: bool):
        if not self.signal_based_enabled or not signal:
            return

        try:
            amount_usd = self.accumulation_amount_per_signal
            self.logger.info(f"📈 [Accumulation] Signal-based accumulation triggered. Buying ${amount_usd} of {self.accumulation_symbol}")

            order = await self._place_accumulation_order(amount_usd)
            if order:
                self._record_accumulation(order, source="signal_based")
                self.logger.info(f"✅ [Accumulation] Bought {order['filled_size']} {self.accumulation_symbol} @ ${order['avg_fill_price']}")
        except Exception as e:
            self.logger.error(f"❌ [Accumulation] Signal-based accumulation failed: {e}")

    # ============================
    # 🔹 PROFIT-BASED ACCUMULATION (stub)
    # ============================
    def allocate_from_profits(self, pnl_usd: float):
        if not self.profit_based_enabled or pnl_usd <= 0:
            return
        allocation = Decimal(str(pnl_usd)) * self.profit_allocation_pct
        self.accumulated_profit_usd += allocation
        self.logger.debug(f"💰 [Accumulation] Added ${allocation} to profit accumulation fund. Total: ${self.accumulated_profit_usd}")

    async def execute_batched_accumulation(self):
        if not self.profit_based_enabled:
            return
        if self.accumulated_profit_usd >= self.accumulation_threshold:
            try:
                amount_usd = self.accumulated_profit_usd
                self.logger.info(f"📈 [Accumulation] Executing batched profit-based accumulation: ${amount_usd}")
                order = await self._place_accumulation_order(amount_usd)
                if order:
                    self._record_accumulation(order, source="profit_based")
                    self.accumulated_profit_usd = Decimal("0.0")
                    self.logger.info(f"✅ [Accumulation] Bought {order['filled_size']} {self.accumulation_symbol} @ ${order['avg_fill_price']}")
            except Exception as e:
                self.logger.error(f"❌ [Accumulation] Batched accumulation failed: {e}")

    # ============================
    # 🔹 DAILY PNL-BASED ACCUMULATION (new)
    # ============================
    async def accumulate_daily_from_realized_pnl(self):
        """
        Runs once per day. Allocates daily realized profit to ETH accumulation.
        """
        if not self.daily_pnl_based_enabled:
            return

        today = datetime.utcnow().date()
        if self.last_daily_accumulation_date == today:
            return  # Already executed today

        try:
            # Fetch sells for yesterday (or today's completed trades if run after market close)
            date_to_use = today - timedelta(days=1)
            daily_sells = await self.shared_data_manager.trade_recorder.fetch_sells_by_date(date_to_use)

            # Sum only positive PnL
            daily_profit = sum(trade.pnl_usd for trade in daily_sells if trade.pnl_usd and trade.pnl_usd > 0)
            if daily_profit <= 0:
                self.logger.info(f"ℹ️ [Accumulation] No positive PnL for {date_to_use}. Skipping accumulation.")
                self.last_daily_accumulation_date = today
                return

            allocation = Decimal(str(daily_profit)) * self.daily_allocation_pct
            self.logger.info(f"📈 [Accumulation] Allocating ${allocation:.2f} to daily ETH accumulation from {date_to_use}'s profit")

            order = await self._place_accumulation_order(allocation)
            if order:
                self._record_accumulation(order, source="daily_pnl")
                self.logger.info(f"✅ [Accumulation] Bought {order['filled_size']} {self.accumulation_symbol} @ ${order['avg_fill_price']}")

            self.last_daily_accumulation_date = today

        except Exception as e:
            self.logger.error(f"❌ [Accumulation] Daily PnL-based accumulation failed: {e}")

    # ============================
    # 🔹 CORE METHODS
    # ============================
    async def _place_accumulation_order(self, amount_usd: Decimal) -> Optional[dict]:
        product_id = self.accumulation_symbol
        try:
            order = await self.exchange.place_market_order_usd(
                product_id=product_id,
                usd_amount=float(amount_usd),
                post_only=False
            )
            return order
        except Exception as e:
            self.logger.error(f"❌ [Accumulation] Order placement failed: {e}")
            return None

    def _record_accumulation(self, order: dict, source: str):
        ledger_entry = {
            "symbol": self.accumulation_symbol,
            "order_id": order.get("order_id"),
            "filled_size": float(order.get("filled_size", 0)),
            "avg_fill_price": float(order.get("avg_fill_price", 0)),
            "source": source,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.shared_data_manager.accumulated_assets.setdefault("ledger", []).append(ledger_entry)
        self.logger.debug(f"🗒️ [Accumulation] Ledger updated: {ledger_entry}")

    def _initialize_accumulation_ledger(self):
        if not hasattr(self.shared_data_manager, "accumulated_assets"):
            self.shared_data_manager.accumulated_assets = {"ledger": []}

