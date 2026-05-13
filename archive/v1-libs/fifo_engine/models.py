"""
Data models for FIFO Allocation Engine.

Defines core data structures used throughout the allocation system.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, List


@dataclass
class FifoAllocation:
    """
    Represents a single FIFO allocation (how much of a sell matched to a buy).

    This is the core allocation record that gets stored in fifo_allocations table.
    """

    # The match
    sell_order_id: str
    buy_order_id: Optional[str]  # None for unmatched sells
    symbol: str

    # Size
    allocated_size: Decimal

    # Prices (per unit)
    buy_price: Optional[Decimal]
    sell_price: Decimal
    buy_fees_per_unit: Optional[Decimal]
    sell_fees_per_unit: Decimal

    # Computed values (USD)
    cost_basis_usd: Optional[Decimal]
    proceeds_usd: Decimal
    net_proceeds_usd: Decimal
    pnl_usd: Optional[Decimal]

    # Timestamps
    buy_time: Optional[datetime]
    sell_time: datetime

    # Allocation metadata
    allocation_version: int
    allocation_batch_id: uuid.UUID
    notes: Optional[str] = None

    @property
    def is_matched(self) -> bool:
        """Check if this allocation is matched to a buy."""
        return self.buy_order_id is not None

    @property
    def is_unmatched(self) -> bool:
        """Check if this allocation is unmatched (no buy found)."""
        return self.buy_order_id is None

    def __str__(self) -> str:
        """Human-readable representation."""
        if self.is_matched:
            return (
                f"Allocation({self.symbol}: {self.allocated_size} @ "
                f"${self.sell_price} → PnL: ${self.pnl_usd})"
            )
        else:
            return (
                f"Allocation({self.symbol}: {self.allocated_size} UNMATCHED @ "
                f"${self.sell_price})"
            )


@dataclass
class ComputationResult:
    """
    Result of a FIFO allocation computation.

    Contains statistics, timing, and error information.
    """

    success: bool
    version: int
    batch_id: uuid.UUID

    # Statistics
    symbols_processed: List[str] = None
    buys_processed: int = 0
    sells_processed: int = 0
    allocations_created: int = 0

    # PnL
    total_pnl: Optional[Decimal] = None

    # Timing
    duration_ms: Optional[int] = None

    # Error info (if success=False)
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None

    def __post_init__(self):
        """Initialize default values."""
        if self.symbols_processed is None:
            self.symbols_processed = []

    @property
    def has_errors(self) -> bool:
        """Check if computation had errors."""
        return not self.success or self.error_message is not None

    def __str__(self) -> str:
        """Human-readable representation."""
        if self.success:
            return (
                f"ComputationResult(✅ Version {self.version}: "
                f"{self.allocations_created} allocations, "
                f"PnL: ${self.total_pnl or 0:,.2f}, "
                f"{self.duration_ms or 0}ms)"
            )
        else:
            return (
                f"ComputationResult(❌ Version {self.version}: "
                f"FAILED - {self.error_message})"
            )


@dataclass
class ValidationResult:
    """
    Result of allocation validation.

    Contains validation checks and any discrepancies found.
    """

    is_valid: bool
    version: int

    # Validation checks
    total_allocations: int = 0
    total_sells: int = 0
    total_buys: int = 0

    # Discrepancies
    unmatched_sells: int = 0
    under_allocated_sells: int = 0
    over_allocated_sells: int = 0
    duplicate_allocations: int = 0

    # PnL checks
    total_pnl_computed: Optional[Decimal] = None
    expected_pnl: Optional[Decimal] = None
    pnl_discrepancy: Optional[Decimal] = None

    # Error details
    error_messages: List[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        """Initialize default values."""
        if self.error_messages is None:
            self.error_messages = []
        if self.warnings is None:
            self.warnings = []

    @property
    def has_errors(self) -> bool:
        """Check if validation found errors."""
        return not self.is_valid or len(self.error_messages) > 0

    @property
    def has_warnings(self) -> bool:
        """Check if validation found warnings."""
        return len(self.warnings) > 0

    @property
    def has_discrepancies(self) -> bool:
        """Check if validation found any discrepancies."""
        return (
            self.unmatched_sells > 0 or
            self.under_allocated_sells > 0 or
            self.over_allocated_sells > 0 or
            self.duplicate_allocations > 0
        )

    def add_error(self, message: str):
        """Add an error message."""
        self.error_messages.append(message)
        self.is_valid = False

    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)

    def __str__(self) -> str:
        """Human-readable representation."""
        if self.is_valid:
            status = "✅ VALID"
        else:
            status = "❌ INVALID"

        parts = [
            f"ValidationResult({status} Version {self.version})",
            f"  Allocations: {self.total_allocations}",
            f"  Sells: {self.total_sells} (Unmatched: {self.unmatched_sells})",
        ]

        if self.has_discrepancies:
            parts.append(f"  ⚠️  Discrepancies found:")
            if self.under_allocated_sells > 0:
                parts.append(f"    - Under-allocated sells: {self.under_allocated_sells}")
            if self.over_allocated_sells > 0:
                parts.append(f"    - Over-allocated sells: {self.over_allocated_sells}")
            if self.duplicate_allocations > 0:
                parts.append(f"    - Duplicate allocations: {self.duplicate_allocations}")

        if self.pnl_discrepancy:
            parts.append(f"  PnL Discrepancy: ${self.pnl_discrepancy:,.2f}")

        if self.has_errors:
            parts.append(f"  ❌ Errors: {len(self.error_messages)}")
            for err in self.error_messages[:3]:  # Show first 3
                parts.append(f"    - {err}")

        if self.has_warnings:
            parts.append(f"  ⚠️  Warnings: {len(self.warnings)}")
            for warn in self.warnings[:3]:  # Show first 3
                parts.append(f"    - {warn}")

        return "\n".join(parts)


@dataclass
class InventorySnapshot:
    """
    Represents inventory state at a point in time.

    Used for incremental computation and debugging.
    """

    symbol: str
    buy_order_id: str
    remaining_size: Decimal
    snapshot_time: datetime
    allocation_version: int

    def __str__(self) -> str:
        """Human-readable representation."""
        return (
            f"InventorySnapshot({self.symbol}: {self.buy_order_id} → "
            f"{self.remaining_size} remaining, v{self.allocation_version})"
        )


@dataclass
class ManualReviewItem:
    """
    Represents an item in the manual review queue.

    Used to track trades requiring human investigation.
    """

    order_id: str
    issue_type: str  # 'unmatched_sell', 'allocation_error', etc.
    severity: str  # 'low', 'medium', 'high', 'critical'
    status: str  # 'pending', 'in_progress', 'resolved', 'dismissed'

    description: str
    resolution: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        """Check if item is resolved."""
        return self.status in ('resolved', 'dismissed')

    @property
    def is_pending(self) -> bool:
        """Check if item is pending review."""
        return self.status == 'pending'

    @property
    def is_critical(self) -> bool:
        """Check if item is critical severity."""
        return self.severity == 'critical'

    def __str__(self) -> str:
        """Human-readable representation."""
        severity_emoji = {
            'low': 'ℹ️',
            'medium': '⚠️',
            'high': '❗',
            'critical': '🚨'
        }
        emoji = severity_emoji.get(self.severity, '❓')

        return (
            f"ManualReviewItem({emoji} {self.issue_type}: "
            f"{self.order_id} [{self.status}])"
        )
