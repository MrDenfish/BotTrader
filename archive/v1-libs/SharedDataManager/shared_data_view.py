import time
import copy
from typing import Any, Dict, Optional

class DBSharedDataView:
    """
    Read-only facade that mimics the attributes of SharedDataManager,
    but pulls fresh data from the database (via DatabaseSessionManager).
    Safe for use in the 'sighook' container.
    """
    def __init__(self, database_session_manager, logger, ttl_seconds: float = 0.25):
        self._db = database_session_manager
        self._log = logger
        self._ttl = ttl_seconds
        self._cached_market_data: Dict[str, Any] = {}
        self._cached_order_mgmt: Dict[str, Any] = {}
        self._md_ts = 0.0
        self._om_ts = 0.0

    async def _refresh_if_stale(self):
        now = time.time()
        # Market data
        if now - self._md_ts > self._ttl:
            try:
                md_row = await self._db.fetch_market_data()
                md = {}
                if md_row and md_row.get("data"):
                    import json
                    from Utils.json_utils import CustomJSONDecoder  # adjust path if needed
                    md = json.loads(md_row["data"], cls=CustomJSONDecoder)
                self._cached_market_data = md or {}
                self._md_ts = now
            except Exception as e:
                self._log.error(f"DBSharedDataView: failed to refresh market_data: {e}", exc_info=True)

        # Order management (+ passive_orders merged)
        if now - self._om_ts > self._ttl:
            try:
                om_row = await self._db.fetch_order_management()
                om = {}
                if om_row and om_row.get("data"):
                    import json
                    from Utils.json_utils import CustomJSONDecoder  # adjust path if needed
                    om = json.loads(om_row["data"], cls=CustomJSONDecoder)
                # merge passive orders
                om["passive_orders"] = await self._db.fetch_passive_orders()
                self._cached_order_mgmt = om or {}
                self._om_ts = now
            except Exception as e:
                self._log.error(f"DBSharedDataView: failed to refresh order_management: {e}", exc_info=True)

    # ---- Properties that mirror SharedDataManager ----

    @property
    def market_data(self) -> Dict[str, Any]:
        # NOTE: properties can’t be async; callers that need fresh data should call ensure_fresh()
        return self._cached_market_data

    @property
    def order_management(self) -> Dict[str, Any]:
        return self._cached_order_mgmt

    # convenience properties used by your HoldingsProcessor
    @property
    def ticker_cache(self):
        return self._cached_market_data.get("ticker_cache")

    @property
    def bid_ask_spread(self):
        return self._cached_market_data.get("bid_ask_spread")

    @property
    def usd_pairs_cache(self):
        return self._cached_market_data.get("usd_pairs_cache")

    @property
    def filtered_vol(self):
        return self._cached_market_data.get("filtered_vol")

    @property
    def filtered_prices(self):
        return self._cached_market_data.get("filtered_prices")

    @property
    def spot_positions(self):
        return self._cached_market_data.get("spot_positions")

    async def ensure_fresh(self):
        """Call this once per loop/iteration to keep the view fresh."""
        await self._refresh_if_stale()

    # Optional helpers to mirror some SharedDataManager methods your code might call
    async def get_order_tracker(self) -> dict:
        await self._refresh_if_stale()
        om = self._cached_order_mgmt or {}
        ot = om.get("order_tracker")
        return {} if ot is None else copy.deepcopy(ot)