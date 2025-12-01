"""
FIFO Allocation Engine

Computes trade allocations using First-In-First-Out matching logic.
Operates on an immutable trade ledger to produce versioned allocations.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
import asyncio
from sqlalchemy import text, select
from sqlalchemy.dialects.postgresql import insert

from database_manager.database_session_manager import DatabaseSessionManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision import PrecisionUtils


class FifoAllocationEngine:
    """
    FIFO Allocation Engine for computing trade PnL.

    Core Principles:
    - Trade records are immutable facts (what happened)
    - Allocations are computed separately (what it means)
    - Allocations can be deleted and recomputed anytime
    - Versioning enables parallel operation and A/B testing

    Usage:
        engine = FifoAllocationEngine(database_session_manager, logger_manager, precision_utils)
        result = await engine.compute_all_symbols(version=1)
    """

    def __init__(
        self,
        database_session_manager: DatabaseSessionManager,
        logger_manager: LoggerManager,
        precision_utils: PrecisionUtils = None
    ):
        """
        Initialize FIFO Allocation Engine.

        Args:
            database_session_manager: Database session manager
            logger_manager: Logging manager
            precision_utils: Precision and dust threshold utilities (optional, uses fallbacks if None)
        """
        self.db = database_session_manager
        self.logger = logger_manager.get_logger('shared_logger')
        self.precision = precision_utils

        # Use fallback precision if not provided
        if self.precision is None:
            self.logger.warning("⚠️  PrecisionUtils not provided, using fallback methods")
            self._use_fallback_precision = True
        else:
            self._use_fallback_precision = False

        self.logger.info("✅ FifoAllocationEngine initialized")

    def _safe_decimal(self, value) -> Decimal:
        """Fallback for safe_decimal if precision utils unavailable."""
        if self._use_fallback_precision:
            if isinstance(value, Decimal):
                return value
            try:
                return Decimal(str(value))
            except:
                return Decimal('0')
        return self.precision.safe_decimal(value)

    def _get_dust_threshold(self, symbol: str) -> Decimal:
        """Fallback for dust threshold if precision utils unavailable."""
        if self._use_fallback_precision:
            return Decimal('0.00000001')  # 1e-8 default
        return self.precision.get_dust_threshold(symbol)

    def _round_with_bankers(self, value, symbol: str, is_base: bool = False):
        """Fallback for banker's rounding if precision utils unavailable."""
        if self._use_fallback_precision:
            # Simple rounding to 8 decimal places
            return round(Decimal(str(value)), 8)
        return self.precision.round_with_bankers(value, symbol, is_base)

    async def compute_all_symbols(
        self,
        version: int,
        triggered_by: str = 'manual'
    ) -> 'ComputationResult':
        """
        Compute FIFO allocations for all symbols (full recomputation).

        This is used for:
        - Initial bootstrap (Version 1)
        - After algorithm changes
        - After historical data amendments
        - After bug fixes affecting history

        Args:
            version: Allocation version number to create
            triggered_by: What triggered this computation ('manual', 'scheduled', 'api')

        Returns:
            ComputationResult with statistics and errors
        """
        batch_id = uuid.uuid4()
        start_time = datetime.now(timezone.utc)

        self.logger.info(f"🚀 Starting FIFO computation (Version {version}, Batch {batch_id})")

        try:
            async with self.db.async_session() as session:
                async with session.begin():
                    # Log computation start
                    log_id = await self._log_computation_start(
                        session=session,
                        symbol=None,
                        version=version,
                        batch_id=batch_id,
                        start_time=start_time,
                        mode='full',
                        triggered_by=triggered_by
                    )

                    # Delete existing allocations for this version (if any)
                    await self._clear_allocations(session, version)

                    # Get all symbols that have trades
                    symbols = await self._get_all_symbols(session)
                    self.logger.info(f"📊 Found {len(symbols)} symbols to process")

                    # Process each symbol
                    total_allocations = 0
                    total_buys = 0
                    total_sells = 0
                    symbols_processed = []

                    for symbol in symbols:
                        self.logger.info(f"⚙️  Processing {symbol}...")

                        result = await self._compute_symbol_internal(
                            session=session,
                            symbol=symbol,
                            version=version,
                            batch_id=batch_id
                        )

                        total_allocations += result['allocations_created']
                        total_buys += result['buys_processed']
                        total_sells += result['sells_processed']
                        symbols_processed.append(symbol)

                        self.logger.info(
                            f"✅ {symbol}: {result['allocations_created']} allocations "
                            f"({result['buys_processed']} buys → {result['sells_processed']} sells)"
                        )

                    # Compute final statistics
                    end_time = datetime.now(timezone.utc)
                    duration_ms = int((end_time - start_time).total_seconds() * 1000)

                    total_pnl = await self._compute_total_pnl(session, version)

                    # Update computation log
                    await self._log_computation_complete(
                        session=session,
                        log_id=log_id,
                        end_time=end_time,
                        duration_ms=duration_ms,
                        buys_processed=total_buys,
                        sells_processed=total_sells,
                        allocations_created=total_allocations,
                        symbols_processed=symbols_processed,
                        total_pnl=total_pnl
                    )

            self.logger.info(
                f"🎉 FIFO computation complete!\n"
                f"   Version: {version}\n"
                f"   Symbols: {len(symbols_processed)}\n"
                f"   Allocations: {total_allocations}\n"
                f"   Total PnL: ${total_pnl:,.2f}\n"
                f"   Duration: {duration_ms:,}ms"
            )

            return ComputationResult(
                success=True,
                version=version,
                batch_id=batch_id,
                symbols_processed=symbols_processed,
                buys_processed=total_buys,
                sells_processed=total_sells,
                allocations_created=total_allocations,
                total_pnl=total_pnl,
                duration_ms=duration_ms
            )

        except Exception as e:
            self.logger.error(f"❌ FIFO computation failed: {e}", exc_info=True)

            # Log failure
            async with self.db.async_session() as session:
                async with session.begin():
                    await self._log_computation_failure(
                        session=session,
                        log_id=log_id,
                        error_message=str(e)
                    )

            return ComputationResult(
                success=False,
                version=version,
                batch_id=batch_id,
                error_message=str(e)
            )

    async def compute_symbol(
        self,
        symbol: str,
        version: int,
        batch_id: Optional[uuid.UUID] = None
    ) -> 'ComputationResult':
        """
        Compute FIFO allocations for a single symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC-USD')
            version: Allocation version number
            batch_id: Optional batch ID for grouping allocations

        Returns:
            ComputationResult with statistics
        """
        if batch_id is None:
            batch_id = uuid.uuid4()

        self.logger.info(f"⚙️  Computing allocations for {symbol} (Version {version})")

        try:
            async with self.db.async_session() as session:
                async with session.begin():
                    result = await self._compute_symbol_internal(
                        session=session,
                        symbol=symbol,
                        version=version,
                        batch_id=batch_id
                    )

            return ComputationResult(
                success=True,
                version=version,
                batch_id=batch_id,
                symbols_processed=[symbol],
                buys_processed=result['buys_processed'],
                sells_processed=result['sells_processed'],
                allocations_created=result['allocations_created']
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to compute allocations for {symbol}: {e}", exc_info=True)
            return ComputationResult(
                success=False,
                version=version,
                batch_id=batch_id,
                error_message=str(e)
            )

    async def _compute_symbol_internal(
        self,
        session,
        symbol: str,
        version: int,
        batch_id: uuid.UUID
    ) -> Dict:
        """Internal symbol computation (within transaction)."""
        # Fetch all buys for this symbol (FIFO order)
        buys = await self._fetch_buys(session, symbol)
        self.logger.debug(f"   Fetched {len(buys)} buys for {symbol}")

        # Fetch all sells for this symbol (chronological order)
        sells = await self._fetch_sells(session, symbol)
        self.logger.debug(f"   Fetched {len(sells)} sells for {symbol}")

        if not sells:
            self.logger.info(f"   No sells to process for {symbol}")
            return {
                'buys_processed': len(buys),
                'sells_processed': 0,
                'allocations_created': 0
            }

        # Initialize inventory (order_id → remaining_size)
        inventory = self._initialize_inventory(buys)

        # Process each sell using FIFO matching
        all_allocations = []
        for sell in sells:
            allocations = await self._allocate_sell_fifo(
                session=session,
                sell=sell,
                inventory=inventory,
                buys_dict={b['order_id']: b for b in buys},
                version=version,
                batch_id=batch_id
            )
            all_allocations.extend(allocations)

        # Save allocations to database
        if all_allocations:
            await self._save_allocations(session, all_allocations)
            self.logger.info(f"   Saved {len(all_allocations)} allocations for {symbol}")

        return {
            'buys_processed': len(buys),
            'sells_processed': len(sells),
            'allocations_created': len(all_allocations)
        }

    # =========================================================================
    # FIFO MATCHING ALGORITHM
    # =========================================================================

    async def _allocate_sell_fifo(
        self,
        session,
        sell: Dict,
        inventory: Dict[str, Decimal],
        buys_dict: Dict[str, Dict],
        version: int,
        batch_id: uuid.UUID
    ) -> List[Dict]:
        """
        Allocate a sell to buy(s) using FIFO logic.

        Args:
            session: Database session
            sell: Sell trade record
            inventory: Current inventory state (order_id → remaining_size)
            buys_dict: Dictionary of buy records by order_id
            version: Allocation version
            batch_id: Batch ID for this computation

        Returns:
            List of allocation dicts
        """
        allocations = []
        remaining_sell_size = self._safe_decimal(sell['size'])
        symbol = sell['symbol']

        # Get dust threshold for this symbol
        dust_threshold = self._get_dust_threshold(symbol)

        # Get sell time for temporal filtering
        sell_time = sell['order_time']

        # Get available buys in FIFO order (only buys that happened BEFORE this sell)
        available_buy_ids = sorted(
            [
                oid for oid, size in inventory.items()
                if size > dust_threshold and buys_dict[oid]['order_time'] <= sell_time
            ],
            key=lambda oid: (buys_dict[oid]['order_time'], oid)
        )

        for buy_order_id in available_buy_ids:
            if remaining_sell_size <= dust_threshold:
                # Remaining amount is dust, stop allocating
                break

            available_size = inventory[buy_order_id]

            if available_size <= dust_threshold:
                # This buy is exhausted or dust, skip
                continue

            # How much can we allocate from this buy?
            allocated_size = min(remaining_sell_size, available_size)

            # Create allocation
            buy = buys_dict[buy_order_id]
            allocation = self._create_allocation(
                sell=sell,
                buy=buy,
                allocated_size=allocated_size,
                version=version,
                batch_id=batch_id
            )
            allocations.append(allocation)

            # Update inventory
            inventory[buy_order_id] = available_size - allocated_size
            remaining_sell_size -= allocated_size

        # Check if sell is fully allocated
        if remaining_sell_size > dust_threshold:
            # Unmatched sell - create placeholder allocation
            self.logger.warning(
                f"⚠️  Unmatched sell: {sell['order_id']} ({symbol}) - "
                f"Remaining: {remaining_sell_size}"
            )

            unmatched_allocation = self._create_unmatched_allocation(
                sell=sell,
                unmatched_size=remaining_sell_size,
                version=version,
                batch_id=batch_id
            )
            allocations.append(unmatched_allocation)

            # Add to manual review queue
            await self._add_to_review_queue(
                session=session,
                order_id=sell['order_id'],
                issue_type='unmatched_sell',
                severity='medium',
                description=f"Sell has {remaining_sell_size} {symbol} with no matching buy"
            )

        return allocations

    def _create_allocation(
        self,
        sell: Dict,
        buy: Dict,
        allocated_size: Decimal,
        version: int,
        batch_id: uuid.UUID
    ) -> Dict:
        """Create a matched allocation record."""
        symbol = sell['symbol']

        # Prices
        buy_price = self._safe_decimal(buy['price'])
        sell_price = self._safe_decimal(sell['price'])

        # Fees per unit
        buy_total_fees = self._safe_decimal(buy.get('total_fees_usd', 0))
        sell_total_fees = self._safe_decimal(sell.get('total_fees_usd', 0))
        buy_size = self._safe_decimal(buy['size'])
        sell_size = self._safe_decimal(sell['size'])

        buy_fees_per_unit = buy_total_fees / buy_size if buy_size > 0 else Decimal('0')
        sell_fees_per_unit = sell_total_fees / sell_size if sell_size > 0 else Decimal('0')

        # PnL calculation
        cost_basis_usd = (buy_price + buy_fees_per_unit) * allocated_size
        proceeds_usd = sell_price * allocated_size
        net_proceeds_usd = proceeds_usd - (sell_fees_per_unit * allocated_size)
        pnl_usd = net_proceeds_usd - cost_basis_usd

        # Round using banker's rounding
        cost_basis_usd = self._round_with_bankers(cost_basis_usd, symbol, is_base=False)
        proceeds_usd = self._round_with_bankers(proceeds_usd, symbol, is_base=False)
        net_proceeds_usd = self._round_with_bankers(net_proceeds_usd, symbol, is_base=False)
        pnl_usd = self._round_with_bankers(pnl_usd, symbol, is_base=False)

        return {
            'sell_order_id': sell['order_id'],
            'buy_order_id': buy['order_id'],
            'symbol': symbol,
            'allocated_size': allocated_size,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'buy_fees_per_unit': buy_fees_per_unit,
            'sell_fees_per_unit': sell_fees_per_unit,
            'cost_basis_usd': cost_basis_usd,
            'proceeds_usd': proceeds_usd,
            'net_proceeds_usd': net_proceeds_usd,
            'pnl_usd': pnl_usd,
            'buy_time': buy['order_time'],
            'sell_time': sell['order_time'],
            'allocation_version': version,
            'allocation_batch_id': batch_id,
            'notes': None
        }

    def _create_unmatched_allocation(
        self,
        sell: Dict,
        unmatched_size: Decimal,
        version: int,
        batch_id: uuid.UUID
    ) -> Dict:
        """Create a placeholder allocation for unmatched sell."""
        symbol = sell['symbol']
        sell_price = self._safe_decimal(sell['price'])

        # Fees per unit
        sell_total_fees = self._safe_decimal(sell.get('total_fees_usd', 0))
        sell_size = self._safe_decimal(sell['size'])
        sell_fees_per_unit = sell_total_fees / sell_size if sell_size > 0 else Decimal('0')

        proceeds_usd = sell_price * unmatched_size
        net_proceeds_usd = proceeds_usd - (sell_fees_per_unit * unmatched_size)

        # Round using banker's rounding
        proceeds_usd = self._round_with_bankers(proceeds_usd, symbol, is_base=False)
        net_proceeds_usd = self._round_with_bankers(net_proceeds_usd, symbol, is_base=False)

        return {
            'sell_order_id': sell['order_id'],
            'buy_order_id': None,
            'symbol': symbol,
            'allocated_size': unmatched_size,
            'buy_price': None,
            'sell_price': sell_price,
            'buy_fees_per_unit': None,
            'sell_fees_per_unit': sell_fees_per_unit,
            'cost_basis_usd': None,
            'proceeds_usd': proceeds_usd,
            'net_proceeds_usd': net_proceeds_usd,
            'pnl_usd': None,
            'buy_time': None,
            'sell_time': sell['order_time'],
            'allocation_version': version,
            'allocation_batch_id': batch_id,
            'notes': f"UNMATCHED: No buy found for {unmatched_size} {symbol}"
        }

    # =========================================================================
    # DATABASE HELPERS
    # =========================================================================

    async def _fetch_buys(self, session, symbol: str) -> List[Dict]:
        """Fetch all buy trades for a symbol in FIFO order (oldest first)."""
        result = await session.execute(text("""
            SELECT order_id, symbol, side, size, price, total_fees_usd, order_time
            FROM trade_records
            WHERE symbol = :symbol AND side = 'buy'
            ORDER BY order_time ASC, order_id ASC
        """), {'symbol': symbol})

        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def _fetch_sells(self, session, symbol: str) -> List[Dict]:
        """Fetch all sell trades for a symbol in chronological order."""
        result = await session.execute(text("""
            SELECT order_id, symbol, side, size, price, total_fees_usd, order_time
            FROM trade_records
            WHERE symbol = :symbol AND side = 'sell'
            ORDER BY order_time ASC, order_id ASC
        """), {'symbol': symbol})

        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def _get_all_symbols(self, session) -> List[str]:
        """Get all unique symbols that have trades."""
        result = await session.execute(text("""
            SELECT DISTINCT symbol FROM trade_records ORDER BY symbol
        """))
        rows = result.fetchall()
        return [row[0] for row in rows]

    async def _clear_allocations(self, session, version: int):
        """Delete all allocations for a version."""
        await session.execute(text("""
            DELETE FROM fifo_allocations WHERE allocation_version = :version
        """), {'version': version})
        self.logger.info(f"   Cleared existing allocations for version {version}")

    async def _save_allocations(self, session, allocations: List[Dict]):
        """Save allocations to database in batch."""
        if not allocations:
            return

        # Build values for batch insert
        await session.execute(text("""
            INSERT INTO fifo_allocations (
                sell_order_id, buy_order_id, symbol, allocated_size,
                buy_price, sell_price, buy_fees_per_unit, sell_fees_per_unit,
                cost_basis_usd, proceeds_usd, net_proceeds_usd, pnl_usd,
                buy_time, sell_time, allocation_version, allocation_batch_id, notes
            ) VALUES (
                :sell_order_id, :buy_order_id, :symbol, :allocated_size,
                :buy_price, :sell_price, :buy_fees_per_unit, :sell_fees_per_unit,
                :cost_basis_usd, :proceeds_usd, :net_proceeds_usd, :pnl_usd,
                :buy_time, :sell_time, :allocation_version, :allocation_batch_id, :notes
            )
        """), allocations)

    async def _add_to_review_queue(
        self,
        session,
        order_id: str,
        issue_type: str,
        severity: str,
        description: str
    ):
        """Add an issue to the manual review queue."""
        await session.execute(text("""
            INSERT INTO manual_review_queue (order_id, issue_type, severity, description)
            VALUES (:order_id, :issue_type, :severity, :description)
            ON CONFLICT (order_id, issue_type) DO UPDATE
            SET updated_at = NOW(), description = EXCLUDED.description
        """), {
            'order_id': order_id,
            'issue_type': issue_type,
            'severity': severity,
            'description': description
        })

    # =========================================================================
    # INVENTORY HELPERS
    # =========================================================================

    def _initialize_inventory(self, buys: List[Dict]) -> Dict[str, Decimal]:
        """Initialize inventory from buy records."""
        inventory = {}
        for buy in buys:
            inventory[buy['order_id']] = self._safe_decimal(buy['size'])
        return inventory

    # =========================================================================
    # COMPUTATION LOGGING
    # =========================================================================

    async def _log_computation_start(
        self,
        session,
        symbol: Optional[str],
        version: int,
        batch_id: uuid.UUID,
        start_time: datetime,
        mode: str,
        triggered_by: str
    ) -> int:
        """Log the start of a computation."""
        result = await session.execute(text("""
            INSERT INTO fifo_computation_log (
                symbol, allocation_version, allocation_batch_id,
                computation_start, status, computation_mode, triggered_by
            )
            VALUES (:symbol, :version, :batch_id, :start_time, 'running', :mode, :triggered_by)
            RETURNING id
        """), {
            'symbol': symbol,
            'version': version,
            'batch_id': batch_id,
            'start_time': start_time,
            'mode': mode,
            'triggered_by': triggered_by
        })
        row = result.fetchone()
        return row[0]

    async def _log_computation_complete(
        self,
        session,
        log_id: int,
        end_time: datetime,
        duration_ms: int,
        buys_processed: int,
        sells_processed: int,
        allocations_created: int,
        symbols_processed: List[str],
        total_pnl: Decimal
    ):
        """Update computation log with completion details."""
        await session.execute(text("""
            UPDATE fifo_computation_log
            SET
                computation_end = :end_time,
                computation_duration_ms = :duration_ms,
                status = 'completed',
                buys_processed = :buys_processed,
                sells_processed = :sells_processed,
                allocations_created = :allocations_created,
                symbols_processed = :symbols_processed,
                total_pnl_computed = :total_pnl
            WHERE id = :log_id
        """), {
            'log_id': log_id,
            'end_time': end_time,
            'duration_ms': duration_ms,
            'buys_processed': buys_processed,
            'sells_processed': sells_processed,
            'allocations_created': allocations_created,
            'symbols_processed': symbols_processed,
            'total_pnl': total_pnl
        })

    async def _log_computation_failure(self, session, log_id: int, error_message: str):
        """Update computation log with failure details."""
        await session.execute(text("""
            UPDATE fifo_computation_log
            SET
                computation_end = NOW(),
                status = 'failed',
                error_message = :error_message
            WHERE id = :log_id
        """), {
            'log_id': log_id,
            'error_message': error_message
        })

    async def _compute_total_pnl(self, session, version: int) -> Decimal:
        """Compute total PnL for a version."""
        result = await session.execute(text("""
            SELECT COALESCE(SUM(pnl_usd), 0) as total_pnl
            FROM fifo_allocations
            WHERE allocation_version = :version
        """), {'version': version})
        row = result.fetchone()
        return self._safe_decimal(row[0])


# Import models at end to avoid circular imports
from .models import ComputationResult
