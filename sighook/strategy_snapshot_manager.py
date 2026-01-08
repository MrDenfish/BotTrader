"""
Strategy Snapshot Manager

Tracks bot configuration changes and correlates them with performance metrics.
Enables A/B testing and historical performance analysis.

Usage:
    # On bot startup or config change
    snapshot_mgr = StrategySnapshotManager(db, logger)
    await snapshot_mgr.save_current_config(config, notes="Reduced RSI weight to 1.5")

    # Link each trade to current strategy
    await snapshot_mgr.link_trade_to_strategy(order_id, signal_data)

    # Daily performance summary (run via cron/scheduler)
    await snapshot_mgr.compute_daily_summary(date)

    # Compare strategies
    results = await snapshot_mgr.compare_strategies(limit=10)
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional
from uuid import UUID

from sqlalchemy import text, select
from sqlalchemy.dialects.postgresql import insert

from database_manager.database_session_manager import DatabaseSessionManager


class StrategySnapshotManager:
    """Manages strategy configuration snapshots for performance tracking."""

    def __init__(self, db: DatabaseSessionManager, logger):
        self.db = db
        self.logger = logger
        self._current_snapshot_id: Optional[UUID] = None

    def _compute_config_hash(self, config_dict: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of configuration for deduplication."""
        # Sort keys for consistent hashing
        config_json = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()

    async def save_current_config(
        self,
        config: Any,  # CentralConfig instance
        notes: Optional[str] = None
    ) -> UUID:
        """
        Save current bot configuration as a snapshot.
        If config unchanged, returns existing snapshot_id.
        If config changed, archives old snapshot and creates new one.

        Returns:
            snapshot_id (UUID): ID of current active snapshot
        """
        try:
            # Extract configuration from CentralConfig
            config_dict = {
                "score_buy_target": float(getattr(config, 'score_buy_target', 3.0)),
                "score_sell_target": float(getattr(config, 'score_sell_target', 3.0)),
                "indicator_weights": getattr(config, 'strategy_weights', {}),
                "rsi_buy_threshold": float(getattr(config, 'rsi_buy', 25.0)),
                "rsi_sell_threshold": float(getattr(config, 'rsi_sell', 75.0)),
                "roc_buy_threshold": float(getattr(config, 'roc_buy_threshold', 2.0)),
                "roc_sell_threshold": float(getattr(config, 'roc_sell_threshold', -1.0)),
                "macd_signal_threshold": float(getattr(config, 'macd_signal_threshold', 0.0)),
                "tp_threshold": float(getattr(config, 'tp_threshold', 3.0)),
                "sl_threshold": float(getattr(config, 'sl_threshold', -2.0)),
                "cooldown_bars": int(getattr(config, 'cooldown_bars', 3)),
                "flip_hysteresis_pct": float(getattr(config, 'flip_hysteresis_pct', 0.0)),
                "min_indicators_required": int(getattr(config, 'min_indicators_required', 0)),
                "excluded_symbols": getattr(config, 'excluded_symbols', []),
                "max_spread_pct": float(getattr(config, 'max_spread_pct', 1.0)),
            }

            config_hash = self._compute_config_hash(config_dict)

            async with self.db.async_session() as session:
                # Check if config already exists and is active
                check_query = text("""
                    SELECT snapshot_id FROM strategy_snapshots
                    WHERE config_hash = :hash AND active_until IS NULL
                """)
                result = await session.execute(check_query, {"hash": config_hash})
                existing = result.fetchone()

                if existing:
                    self._current_snapshot_id = existing[0]
                    self.logger.info(f"Using existing strategy snapshot: {self._current_snapshot_id}")
                    return self._current_snapshot_id

                # Archive previous active snapshot
                archive_query = text("""
                    UPDATE strategy_snapshots
                    SET active_until = NOW()
                    WHERE active_until IS NULL
                """)
                await session.execute(archive_query)

                # Insert new snapshot
                insert_query = text("""
                    INSERT INTO strategy_snapshots (
                        active_from, score_buy_target, score_sell_target,
                        indicator_weights, rsi_buy_threshold, rsi_sell_threshold,
                        roc_buy_threshold, roc_sell_threshold, macd_signal_threshold,
                        tp_threshold, sl_threshold, cooldown_bars, flip_hysteresis_pct,
                        min_indicators_required, excluded_symbols, max_spread_pct,
                        config_hash, notes, created_by
                    )
                    VALUES (
                        NOW(), :score_buy, :score_sell, CAST(:weights AS JSONB),
                        :rsi_buy, :rsi_sell, :roc_buy, :roc_sell, :macd_threshold,
                        :tp, :sl, :cooldown, :hysteresis, :min_indicators,
                        CAST(:excluded AS TEXT[]), :max_spread, :hash, :notes, 'system'
                    )
                    RETURNING snapshot_id
                """)

                # For asyncpg with raw SQL, JSONB needs to be a JSON string
                result = await session.execute(insert_query, {
                    "score_buy": config_dict["score_buy_target"],
                    "score_sell": config_dict["score_sell_target"],
                    "weights": json.dumps(config_dict["indicator_weights"]),
                    "rsi_buy": config_dict["rsi_buy_threshold"],
                    "rsi_sell": config_dict["rsi_sell_threshold"],
                    "roc_buy": config_dict["roc_buy_threshold"],
                    "roc_sell": config_dict["roc_sell_threshold"],
                    "macd_threshold": config_dict["macd_signal_threshold"],
                    "tp": config_dict["tp_threshold"],
                    "sl": config_dict["sl_threshold"],
                    "cooldown": config_dict["cooldown_bars"],
                    "hysteresis": config_dict["flip_hysteresis_pct"],
                    "min_indicators": config_dict["min_indicators_required"],
                    "excluded": config_dict["excluded_symbols"],
                    "max_spread": config_dict["max_spread_pct"],
                    "hash": config_hash,
                    "notes": notes
                })

                snapshot_id = result.fetchone()[0]
                await session.commit()

                self._current_snapshot_id = snapshot_id
                self.logger.info(f"✅ Created new strategy snapshot: {snapshot_id}")
                if notes:
                    self.logger.info(f"   Notes: {notes}")

                return snapshot_id

        except Exception as e:
            self.logger.error(f"❌ Error saving strategy snapshot: {e}", exc_info=True)
            raise

    async def link_trade_to_strategy(
        self,
        order_id: str,
        signal_data: Dict[str, Any]
    ) -> None:
        """
        Link a trade to the current strategy snapshot.

        Args:
            order_id: Trade order ID from Coinbase
            signal_data: Signal details from buy_sell_scoring()
        """
        try:
            if not self._current_snapshot_id:
                # Load current snapshot if not cached
                async with self.db.async_session() as session:
                    query = text("""
                        SELECT snapshot_id FROM strategy_snapshots
                        WHERE active_until IS NULL
                        ORDER BY active_from DESC LIMIT 1
                    """)
                    result = await session.execute(query)
                    row = result.fetchone()
                    if row:
                        self._current_snapshot_id = row[0]

            if not self._current_snapshot_id:
                self.logger.warning(f"⚠️ No active strategy snapshot found for trade {order_id}")
                return

            buy_score = signal_data.get('Score', {}).get('Buy Score')
            sell_score = signal_data.get('Score', {}).get('Sell Score')
            trigger = signal_data.get('trigger', 'unknown')

            # Count indicators that fired
            # This requires access to the last row data - will be passed in signal_data
            indicator_breakdown = signal_data.get('indicator_breakdown', {})
            indicators_fired = len([v for v in indicator_breakdown.values() if v > 0])

            async with self.db.async_session() as session:
                insert_query = text("""
                    INSERT INTO trade_strategy_link (
                        order_id, snapshot_id, buy_score, sell_score,
                        trigger_type, indicators_fired, indicator_breakdown
                    )
                    VALUES (
                        :order_id, :snapshot_id, :buy_score, :sell_score,
                        :trigger, :indicators_fired, :breakdown::jsonb
                    )
                    ON CONFLICT (order_id) DO NOTHING
                """)

                await session.execute(insert_query, {
                    "order_id": order_id,
                    "snapshot_id": str(self._current_snapshot_id),
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "trigger": trigger,
                    "indicators_fired": indicators_fired,
                    "breakdown": json.dumps(indicator_breakdown)
                })

                await session.commit()

        except Exception as e:
            self.logger.error(f"❌ Error linking trade {order_id} to strategy: {e}", exc_info=True)

    async def compute_daily_summary(self, summary_date: Optional[date] = None) -> None:
        """
        Compute daily performance summary for all active strategies.
        Should be run once per day via scheduler.

        Args:
            summary_date: Date to compute summary for (defaults to yesterday)
        """
        if summary_date is None:
            summary_date = date.today()

        try:
            async with self.db.async_session() as session:
                query = text("""
                    WITH daily_trades AS (
                        SELECT
                            tsl.snapshot_id,
                            tr.order_id,
                            tr.side,
                            tr.order_time::date as trade_date,
                            COALESCE(SUM(fa.pnl_usd), 0) as pnl_usd,
                            EXTRACT(EPOCH FROM (
                                MAX(CASE WHEN tr.side = 'sell' THEN tr.order_time END) -
                                MIN(CASE WHEN tr.side = 'buy' THEN tr.order_time END)
                            )) as hold_seconds
                        FROM trade_strategy_link tsl
                        JOIN trade_records tr ON tr.order_id = tsl.order_id
                        LEFT JOIN fifo_allocations fa ON fa.sell_order_id = tr.order_id
                            AND fa.allocation_version = 2
                        WHERE tr.order_time::date = :date
                        GROUP BY tsl.snapshot_id, tr.order_id, tr.side, tr.order_time::date
                    ),
                    aggregated AS (
                        SELECT
                            snapshot_id,
                            COUNT(*) FILTER (WHERE side = 'sell') as total_trades,
                            COUNT(*) FILTER (WHERE side = 'sell' AND pnl_usd > 0.01) as winning_trades,
                            COUNT(*) FILTER (WHERE side = 'sell' AND pnl_usd < -0.01) as losing_trades,
                            COUNT(*) FILTER (WHERE side = 'sell' AND pnl_usd BETWEEN -0.01 AND 0.01) as breakeven_trades,
                            SUM(pnl_usd) as total_pnl,
                            AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
                            AVG(CASE WHEN pnl_usd < 0 THEN pnl_usd END) as avg_loss,
                            MAX(pnl_usd) as largest_win,
                            MIN(pnl_usd) as largest_loss,
                            AVG(hold_seconds) as avg_hold_seconds,
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY hold_seconds) as median_hold_seconds,
                            COUNT(*) FILTER (WHERE hold_seconds < 60) as fast_exits,
                            SUM(CASE WHEN hold_seconds < 60 THEN pnl_usd ELSE 0 END) as fast_exits_pnl
                        FROM daily_trades
                        WHERE side = 'sell'
                        GROUP BY snapshot_id
                    )
                    INSERT INTO strategy_performance_summary (
                        snapshot_id, date, total_trades, winning_trades, losing_trades,
                        breakeven_trades, total_pnl_usd, avg_win_usd, avg_loss_usd,
                        largest_win_usd, largest_loss_usd, win_rate, profit_factor,
                        expectancy_usd, avg_hold_time_seconds, median_hold_time_seconds,
                        fast_exits_count, fast_exits_pnl
                    )
                    SELECT
                        snapshot_id, :date, total_trades, winning_trades, losing_trades,
                        breakeven_trades, total_pnl, avg_win, avg_loss,
                        largest_win, largest_loss,
                        CASE WHEN total_trades > 0
                            THEN (winning_trades::float / total_trades * 100)
                            ELSE 0 END as win_rate,
                        CASE WHEN avg_loss < 0 AND avg_loss IS NOT NULL
                            THEN (avg_win / ABS(avg_loss))
                            ELSE 0 END as profit_factor,
                        CASE WHEN total_trades > 0
                            THEN (total_pnl / total_trades)
                            ELSE 0 END as expectancy,
                        avg_hold_seconds, median_hold_seconds,
                        fast_exits, fast_exits_pnl
                    FROM aggregated
                    ON CONFLICT (snapshot_id, date) DO UPDATE SET
                        total_trades = EXCLUDED.total_trades,
                        winning_trades = EXCLUDED.winning_trades,
                        losing_trades = EXCLUDED.losing_trades,
                        total_pnl_usd = EXCLUDED.total_pnl_usd,
                        win_rate = EXCLUDED.win_rate,
                        profit_factor = EXCLUDED.profit_factor,
                        updated_at = NOW()
                """)

                await session.execute(query, {"date": summary_date})
                await session.commit()

                self.logger.info(f"✅ Computed daily strategy summary for {summary_date}")

        except Exception as e:
            self.logger.error(f"❌ Error computing daily summary: {e}", exc_info=True)

    async def compare_strategies(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Compare performance across different strategy configurations.

        Args:
            limit: Max number of strategies to compare

        Returns:
            List of strategy comparison results
        """
        try:
            async with self.db.async_session() as session:
                query = text("""
                    SELECT * FROM strategy_comparison
                    ORDER BY active_from DESC
                    LIMIT :limit
                """)

                result = await session.execute(query, {"limit": limit})
                rows = result.fetchall()

                comparisons = []
                for row in rows:
                    comparisons.append({
                        "snapshot_id": row.snapshot_id,
                        "active_from": row.active_from,
                        "active_until": row.active_until,
                        "days_active": row.days_active,
                        "total_trades": row.total_trades,
                        "avg_win_rate": float(row.avg_win_rate) if row.avg_win_rate else 0.0,
                        "total_pnl": float(row.total_pnl) if row.total_pnl else 0.0,
                        "avg_profit_factor": float(row.avg_profit_factor) if row.avg_profit_factor else 0.0,
                        "avg_expectancy": float(row.avg_expectancy) if row.avg_expectancy else 0.0,
                        "notes": row.notes
                    })

                return comparisons

        except Exception as e:
            self.logger.error(f"❌ Error comparing strategies: {e}", exc_info=True)
            return []
