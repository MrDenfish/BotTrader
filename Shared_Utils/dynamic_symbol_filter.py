"""
Dynamic Symbol Filter - Data-Driven Symbol Exclusion System

This module automatically excludes/includes symbols based on rolling performance metrics.
Symbols are evaluated daily and dynamically added/removed from the exclusion list based on:
- Win rate (% of profitable trades)
- Average P&L per trade
- Total net P&L over lookback period
- Average spread (bid-ask)
- Trade frequency (minimum trades required for statistical significance)

Permanent exclusions (HODL, SHILL_COINS, manually blacklisted) are preserved.
"""

import os
import time
import asyncio
from typing import Set, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict

from Shared_Utils.logger import get_logger


class DynamicSymbolFilter:
    """
    Manages dynamic symbol exclusion based on performance metrics.

    Auto-excludes symbols that:
    - Win rate < threshold (default 30%)
    - Average P&L < threshold (default -$5)
    - Total net P&L < threshold (default -$50)
    - Average spread > threshold (default 2%)
    - Low volatility (passive MM specific)

    Auto-includes symbols when performance improves above thresholds.
    """

    def __init__(self, shared_data_manager, config, logger_manager=None):
        """
        Initialize the dynamic symbol filter.

        Args:
            shared_data_manager: Access to database and market data
            config: Bot configuration (BotConfig instance)
            logger_manager: Optional logger manager
        """
        self.shared_data_manager = shared_data_manager
        self.config = config
        self.logger = get_logger('dynamic_symbol_filter', context={'component': 'dynamic_symbol_filter'})

        # Cache for excluded symbols
        self._excluded_cache: Set[str] = set()
        self._cache_timestamp: float = 0
        self._cache_ttl: int = 3600  # 1 hour cache

        # Performance thresholds (configurable via .env)
        self.min_win_rate = Decimal(os.getenv('DYNAMIC_FILTER_MIN_WIN_RATE', '0.30'))  # 30%
        self.min_avg_pnl = Decimal(os.getenv('DYNAMIC_FILTER_MIN_AVG_PNL', '-5.0'))  # -$5
        self.min_total_pnl = Decimal(os.getenv('DYNAMIC_FILTER_MIN_TOTAL_PNL', '-50.0'))  # -$50
        self.max_avg_spread_pct = Decimal(os.getenv('DYNAMIC_FILTER_MAX_SPREAD_PCT', '0.02'))  # 2%
        self.min_trades_required = int(os.getenv('DYNAMIC_FILTER_MIN_TRADES', '5'))  # Minimum 5 trades
        self.lookback_days = int(os.getenv('DYNAMIC_FILTER_LOOKBACK_DAYS', '30'))  # 30 days

        # Permanent exclusions (manual override)
        permanent_str = os.getenv('PERMANENT_EXCLUSIONS', '')
        self.permanent_exclusions: Set[str] = {s.strip() for s in permanent_str.split(',') if s.strip()}

        # Enable/disable dynamic filtering
        self.enabled = os.getenv('DYNAMIC_FILTER_ENABLED', 'true').lower() in ('true', '1', 'yes')

        self.logger.info(
            f"Dynamic Symbol Filter initialized: enabled={self.enabled}, "
            f"min_win_rate={self.min_win_rate:.1%}, min_avg_pnl=${self.min_avg_pnl}, "
            f"min_total_pnl=${self.min_total_pnl}, lookback={self.lookback_days}d, "
            f"permanent_exclusions={len(self.permanent_exclusions)}"
        )

    async def get_excluded_symbols(self, force_refresh: bool = False) -> Set[str]:
        """
        Get the current list of excluded symbols (cached).

        Args:
            force_refresh: If True, bypass cache and recompute

        Returns:
            Set of symbol strings to exclude (e.g., {'TNSR-USD', 'A8-USD'})
        """
        now = time.time()

        # Return cached result if still valid
        if not force_refresh and (now - self._cache_timestamp) < self._cache_ttl:
            return self._excluded_cache

        # Recompute exclusion list
        excluded = await self._compute_excluded_symbols()

        # Update cache
        self._excluded_cache = excluded
        self._cache_timestamp = now

        return excluded

    async def _compute_excluded_symbols(self) -> Set[str]:
        """
        Compute the full list of excluded symbols based on performance and manual rules.

        Returns:
            Set of symbols to exclude
        """
        if not self.enabled:
            self.logger.info("Dynamic filtering disabled, using permanent exclusions only")
            return self.permanent_exclusions.copy()

        excluded = set()

        # 1. Get performance-based exclusions
        try:
            performance_excluded = await self._get_performance_excluded_symbols()
            excluded.update(performance_excluded)
            self.logger.info(f"Performance-based exclusions: {len(performance_excluded)} symbols")
        except Exception as e:
            self.logger.error(f"Error computing performance exclusions: {e}", exc_info=True)

        # 2. Get spread-based exclusions
        try:
            spread_excluded = await self._get_spread_excluded_symbols()
            excluded.update(spread_excluded)
            self.logger.info(f"Spread-based exclusions: {len(spread_excluded)} symbols")
        except Exception as e:
            self.logger.error(f"Error computing spread exclusions: {e}", exc_info=True)

        # 3. Add permanent exclusions (manual override)
        excluded.update(self.permanent_exclusions)

        # 4. Log changes from previous cache
        if hasattr(self, '_excluded_cache'):
            newly_excluded = excluded - self._excluded_cache
            newly_included = self._excluded_cache - excluded

            if newly_excluded:
                self.logger.warning(f"🚫 Newly excluded symbols: {sorted(newly_excluded)}")
            if newly_included:
                self.logger.info(f"✅ Newly included symbols: {sorted(newly_included)}")

        self.logger.info(f"Total excluded symbols: {len(excluded)}")

        return excluded

    async def _get_performance_excluded_symbols(self) -> Set[str]:
        """
        Query database for symbols with poor performance metrics.

        Excludes symbols where:
        - Win rate < min_win_rate
        - Average P&L < min_avg_pnl
        - Total P&L < min_total_pnl

        Returns:
            Set of symbols to exclude based on performance
        """
        excluded = set()

        query = """
        SELECT
            symbol,
            COUNT(*) as trade_count,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) as win_rate,
            AVG(pnl_usd) as avg_pnl,
            SUM(pnl_usd) as total_pnl,
            MIN(order_time) as first_trade,
            MAX(order_time) as last_trade
        FROM trade_records
        WHERE order_time >= NOW() - INTERVAL '%s days'
          AND pnl_usd IS NOT NULL
          AND symbol NOT IN (%s)  -- Exclude permanent exclusions from analysis
        GROUP BY symbol
        HAVING COUNT(*) >= %s  -- Minimum trades for statistical significance
        ORDER BY total_pnl ASC
        """

        # Build permanent exclusions list for SQL
        if self.permanent_exclusions:
            perm_list = ','.join([f"'{s}'" for s in self.permanent_exclusions])
        else:
            perm_list = "''"

        try:
            async with self.shared_data_manager.db_session_manager.session() as session:
                result = await session.execute(
                    query % (self.lookback_days, perm_list, self.min_trades_required)
                )
                rows = result.fetchall()
        except Exception as e:
            self.logger.error(f"Database query failed for performance exclusions: {e}", exc_info=True)
            return excluded

        for row in rows:
            symbol = row[0]
            trade_count = row[1]
            win_rate = Decimal(str(row[2])) if row[2] is not None else Decimal('0')
            avg_pnl = Decimal(str(row[3])) if row[3] is not None else Decimal('0')
            total_pnl = Decimal(str(row[4])) if row[4] is not None else Decimal('0')

            reasons = []

            # Check win rate
            if win_rate < self.min_win_rate:
                reasons.append(f"win_rate={win_rate:.1%}<{self.min_win_rate:.1%}")

            # Check average P&L
            if avg_pnl < self.min_avg_pnl:
                reasons.append(f"avg_pnl=${avg_pnl:.2f}<${self.min_avg_pnl}")

            # Check total P&L
            if total_pnl < self.min_total_pnl:
                reasons.append(f"total_pnl=${total_pnl:.2f}<${self.min_total_pnl}")

            # If any threshold violated, exclude
            if reasons:
                excluded.add(symbol)
                self.logger.info(
                    f"⛔ Excluding {symbol}: trades={trade_count}, {', '.join(reasons)}"
                )

        return excluded

    async def _get_spread_excluded_symbols(self) -> Set[str]:
        """
        Get symbols with consistently wide spreads (> max threshold).

        Returns:
            Set of symbols to exclude based on spread
        """
        excluded = set()

        try:
            # Get current bid-ask spreads from market data
            market_data = self.shared_data_manager.market_data
            bid_ask_spread = market_data.get('bid_ask_spread', {})

            for symbol, spread_data in bid_ask_spread.items():
                bid = spread_data.get('bid')
                ask = spread_data.get('ask')

                if bid and ask and bid > 0:
                    mid = (Decimal(str(bid)) + Decimal(str(ask))) / Decimal('2')
                    spread_pct = (Decimal(str(ask)) - Decimal(str(bid))) / mid

                    if spread_pct > self.max_avg_spread_pct:
                        excluded.add(symbol)
                        self.logger.debug(
                            f"⛔ Excluding {symbol} due to wide spread: {spread_pct:.3%} > {self.max_avg_spread_pct:.3%}"
                        )
        except Exception as e:
            self.logger.error(f"Error checking spreads: {e}", exc_info=True)

        return excluded

    async def get_symbol_performance(self, symbol: str) -> Optional[Dict]:
        """
        Get detailed performance metrics for a specific symbol.

        Args:
            symbol: Symbol to analyze (e.g., 'TNSR-USD')

        Returns:
            Dict with performance metrics or None if insufficient data
        """
        query = """
        SELECT
            COUNT(*) as trade_count,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) as win_rate,
            AVG(pnl_usd) as avg_pnl,
            SUM(pnl_usd) as total_pnl,
            STDDEV(pnl_usd) as pnl_stddev,
            MIN(order_time) as first_trade,
            MAX(order_time) as last_trade,
            COUNT(DISTINCT DATE(order_time)) as active_days
        FROM trade_records
        WHERE symbol = %s
          AND order_time >= NOW() - INTERVAL '%s days'
          AND pnl_usd IS NOT NULL
        """

        try:
            async with self.shared_data_manager.db_session_manager.session() as session:
                result = await session.execute(query, (symbol, self.lookback_days))
                row = result.fetchone()

                if not row or row[0] < self.min_trades_required:
                    return None

                return {
                    'symbol': symbol,
                    'trade_count': row[0],
                    'win_rate': float(row[1]) if row[1] else 0.0,
                    'avg_pnl': float(row[2]) if row[2] else 0.0,
                    'total_pnl': float(row[3]) if row[3] else 0.0,
                    'pnl_stddev': float(row[4]) if row[4] else 0.0,
                    'first_trade': row[5],
                    'last_trade': row[6],
                    'active_days': row[7],
                    'is_excluded': symbol in await self.get_excluded_symbols()
                }
        except Exception as e:
            self.logger.error(f"Error fetching performance for {symbol}: {e}", exc_info=True)
            return None

    async def get_exclusion_report(self) -> Dict[str, List[str]]:
        """
        Generate a detailed report of exclusions by category.

        Returns:
            Dict with categorized exclusions:
            {
                'performance': [...],
                'spread': [...],
                'permanent': [...],
                'total': [...]
            }
        """
        performance_excluded = await self._get_performance_excluded_symbols()
        spread_excluded = await self._get_spread_excluded_symbols()
        total_excluded = await self.get_excluded_symbols(force_refresh=True)

        return {
            'performance': sorted(performance_excluded),
            'spread': sorted(spread_excluded),
            'permanent': sorted(self.permanent_exclusions),
            'total': sorted(total_excluded)
        }

    async def force_refresh(self) -> Set[str]:
        """
        Force immediate refresh of exclusion list (bypass cache).

        Returns:
            Updated set of excluded symbols
        """
        self.logger.info("Forcing refresh of excluded symbols...")
        return await self.get_excluded_symbols(force_refresh=True)

    def is_excluded(self, symbol: str, cached: bool = True) -> bool:
        """
        Synchronous check if a symbol is excluded (uses cached data).

        Args:
            symbol: Symbol to check
            cached: If True, use cached exclusions; if False, may return stale data

        Returns:
            True if symbol is excluded, False otherwise
        """
        # Use cached exclusions for synchronous check
        return symbol in self._excluded_cache
