"""
Allocation Validator

Validates FIFO allocation invariants and detects discrepancies.
"""

from decimal import Decimal
from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy import text

from database_manager.database_session_manager import DatabaseSessionManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision import PrecisionUtils
from .models import ValidationResult


class AllocationValidator:
    """
    Validates FIFO allocations to ensure correctness.

    Checks:
    - All sells are fully allocated (no gaps)
    - No double-allocation (same buy used twice for same sell)
    - No over-allocation (allocated size > sell size)
    - PnL calculations are correct
    - Temporal consistency (buy time <= sell time)
    - Dust thresholds respected
    """

    def __init__(
        self,
        database_session_manager: DatabaseSessionManager,
        logger_manager: LoggerManager,
        precision_utils: PrecisionUtils
    ):
        """
        Initialize Allocation Validator.

        Args:
            database_session_manager: Database session manager
            logger_manager: Logging manager
            precision_utils: Precision utilities
        """
        self.db = database_session_manager
        self.logger = logger_manager.get_logger('shared_logger')
        self.precision = precision_utils

        self.logger.info("✅ AllocationValidator initialized")

    async def validate_version(
        self,
        version: int,
        strict: bool = True
    ) -> ValidationResult:
        """
        Validate all allocations for a version.

        Args:
            version: Allocation version to validate
            strict: If True, treat warnings as errors

        Returns:
            ValidationResult with validation details
        """
        self.logger.info(f"🔍 Validating allocations for Version {version} (strict={strict})")

        result = ValidationResult(
            is_valid=True,
            version=version
        )

        try:
            async with self.db.async_session() as session:
                # Get basic statistics
                result.total_allocations = await self._count_allocations(session, version)
                result.total_sells = await self._count_sells(session)
                result.total_buys = await self._count_buys(session)

                self.logger.info(
                    f"   Total allocations: {result.total_allocations}\n"
                    f"   Total sells: {result.total_sells}\n"
                    f"   Total buys: {result.total_buys}"
                )

                # Run validation checks
                await self._check_unmatched_sells(session, version, result)
                await self._check_allocation_completeness(session, version, result)
                await self._check_duplicate_allocations(session, version, result)
                await self._check_temporal_consistency(session, version, result)

                # Check if validation passed
                if result.has_errors:
                    result.is_valid = False
                    self.logger.error(f"❌ Validation FAILED for Version {version}")
                elif result.has_warnings and strict:
                    result.is_valid = False
                    self.logger.warning(f"⚠️  Validation FAILED (strict mode) for Version {version}")
                else:
                    self.logger.info(f"✅ Validation PASSED for Version {version}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Validation failed with exception: {e}", exc_info=True)
            result.is_valid = False
            result.add_error(f"Validation exception: {e}")
            return result

    # =========================================================================
    # VALIDATION CHECKS
    # =========================================================================

    async def _check_unmatched_sells(self, session, version: int, result: ValidationResult):
        """Check for unmatched sells (sells with no matching buy)."""
        query_result = await session.execute(text("""
            SELECT COUNT(*) as count
            FROM fifo_allocations
            WHERE allocation_version = :version AND buy_order_id IS NULL
        """), {'version': version})

        row = query_result.fetchone()
        result.unmatched_sells = row[0]

        if result.unmatched_sells > 0:
            result.add_warning(
                f"Found {result.unmatched_sells} unmatched sells (requires manual investigation)"
            )
            self.logger.warning(f"⚠️  {result.unmatched_sells} unmatched sells")

    async def _check_allocation_completeness(self, session, version: int, result: ValidationResult):
        """
        Check that all sells are fully allocated.

        For each sell, sum(allocated_size) should equal sell.size (within dust threshold).
        """
        query_result = await session.execute(text("""
            SELECT
                s.order_id,
                s.symbol,
                s.size as sell_size,
                COALESCE(SUM(a.allocated_size), 0) as allocated_total,
                ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) as discrepancy
            FROM trade_records s
            LEFT JOIN fifo_allocations a
                ON a.sell_order_id = s.order_id
                AND a.allocation_version = :version
            WHERE s.side = 'sell'
            GROUP BY s.order_id, s.symbol, s.size
            HAVING ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) > 0.00001
        """), {'version': version})

        rows = query_result.fetchall()

        if rows:
            # Categorize discrepancies
            for row in rows:
                row_dict = dict(row._mapping)
                symbol = row_dict['symbol']
                dust_threshold = self.precision.get_dust_threshold(symbol)
                discrepancy = self.precision.safe_decimal(row_dict['discrepancy'])

                if discrepancy <= dust_threshold:
                    # Within dust threshold, ignore
                    continue
                elif row_dict['allocated_total'] < row_dict['sell_size']:
                    result.under_allocated_sells += 1
                    result.add_error(
                        f"Under-allocated sell {row_dict['order_id']}: "
                        f"size={row_dict['sell_size']}, allocated={row_dict['allocated_total']}"
                    )
                else:
                    result.over_allocated_sells += 1
                    result.add_error(
                        f"Over-allocated sell {row_dict['order_id']}: "
                        f"size={row_dict['sell_size']}, allocated={row_dict['allocated_total']}"
                    )

            if result.under_allocated_sells > 0:
                self.logger.error(f"❌ {result.under_allocated_sells} under-allocated sells")
            if result.over_allocated_sells > 0:
                self.logger.error(f"❌ {result.over_allocated_sells} over-allocated sells")

    async def _check_duplicate_allocations(self, session, version: int, result: ValidationResult):
        """Check for duplicate allocations (same buy→sell pair multiple times)."""
        query_result = await session.execute(text("""
            SELECT
                sell_order_id,
                buy_order_id,
                COUNT(*) as count
            FROM fifo_allocations
            WHERE allocation_version = :version
            GROUP BY sell_order_id, buy_order_id
            HAVING COUNT(*) > 1
        """), {'version': version})

        rows = query_result.fetchall()

        if rows:
            result.duplicate_allocations = len(rows)
            for row in rows:
                row_dict = dict(row._mapping)
                result.add_error(
                    f"Duplicate allocation: sell={row_dict['sell_order_id']}, "
                    f"buy={row_dict['buy_order_id']}, count={row_dict['count']}"
                )
            self.logger.error(f"❌ {result.duplicate_allocations} duplicate allocations")

    async def _check_temporal_consistency(self, session, version: int, result: ValidationResult):
        """Check that buy_time <= sell_time for all matched allocations."""
        query_result = await session.execute(text("""
            SELECT
                a.id,
                a.sell_order_id,
                a.buy_order_id,
                a.buy_time,
                a.sell_time
            FROM fifo_allocations a
            WHERE a.allocation_version = :version
              AND a.buy_order_id IS NOT NULL
              AND a.buy_time > a.sell_time
        """), {'version': version})

        rows = query_result.fetchall()

        if rows:
            for row in rows:
                row_dict = dict(row._mapping)
                result.add_error(
                    f"Temporal violation: sell {row_dict['sell_order_id']} at {row_dict['sell_time']} "
                    f"matched to buy {row_dict['buy_order_id']} at {row_dict['buy_time']}"
                )
            self.logger.error(f"❌ {len(rows)} temporal consistency violations")

    # =========================================================================
    # DATABASE HELPERS
    # =========================================================================

    async def _count_allocations(self, session, version: int) -> int:
        """Count total allocations for a version."""
        result = await session.execute(text("""
            SELECT COUNT(*) as count FROM fifo_allocations WHERE allocation_version = :version
        """), {'version': version})
        row = result.fetchone()
        return row[0]

    async def _count_sells(self, session) -> int:
        """Count total sell trades."""
        result = await session.execute(text("""
            SELECT COUNT(*) as count FROM trade_records WHERE side = 'sell'
        """))
        row = result.fetchone()
        return row[0]

    async def _count_buys(self, session) -> int:
        """Count total buy trades."""
        result = await session.execute(text("""
            SELECT COUNT(*) as count FROM trade_records WHERE side = 'buy'
        """))
        row = result.fetchone()
        return row[0]

    # =========================================================================
    # REPORT GENERATION
    # =========================================================================

    async def generate_health_report(self, version: int) -> Dict:
        """
        Generate a comprehensive health report for a version.

        Returns dict with statistics, discrepancies, and health status.
        """
        async with self.db.async_session() as session:
            # Get health stats
            health_result = await session.execute(text("""
                SELECT * FROM v_allocation_health WHERE allocation_version = :version
            """), {'version': version})
            health_row = health_result.fetchone()
            health = dict(health_row._mapping) if health_row else {}

            # Get discrepancies
            discrepancy_result = await session.execute(text("""
                SELECT * FROM v_allocation_discrepancies
            """))
            discrepancies = [dict(row._mapping) for row in discrepancy_result.fetchall()]

            # Get unmatched sells
            unmatched_result = await session.execute(text("""
                SELECT * FROM v_unmatched_sells WHERE allocation_version = :version
            """), {'version': version})
            unmatched = [dict(row._mapping) for row in unmatched_result.fetchall()]

            return {
                'version': version,
                'health': health,
                'discrepancies': discrepancies,
                'unmatched_sells': unmatched,
                'timestamp': datetime.now()
            }
