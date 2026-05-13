"""
FIFO Allocation Engine

This module implements the FIFO (First-In-First-Out) allocation system for
computing trade PnL based on an immutable trade ledger.

Key Components:
- FifoAllocationEngine: Computes allocations for trades
- AllocationValidator: Validates allocation invariants
- Models: Data structures for allocations and computation logs

Architecture:
- Trade records are immutable facts (what happened)
- Allocations are computed separately (what it means)
- Allocations can be deleted and recomputed anytime
- Versioning enables parallel operation and A/B testing

Usage:
    from fifo_engine import FifoAllocationEngine

    engine = FifoAllocationEngine(db_manager, logger, precision_utils)
    result = await engine.compute_all_symbols(version=1)
"""

from .engine import FifoAllocationEngine
from .validator import AllocationValidator
from .models import FifoAllocation, ComputationResult, ValidationResult

__all__ = [
    'FifoAllocationEngine',
    'AllocationValidator',
    'FifoAllocation',
    'ComputationResult',
    'ValidationResult',
]

__version__ = '1.0.0'
