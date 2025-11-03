"""
Core system constants shared across all modules.

These define fundamental system behavior and rarely change.
Changes to these values affect the entire system.
"""
from decimal import Decimal

# ============================================================================
# Precision & Filtering
# ============================================================================

POSITION_DUST_THRESHOLD = Decimal('0.0001')
"""Ignore positions smaller than 0.0001 (exchange precision limit)"""

BREAKEVEN_EPSILON = 1e-9
"""Floating point tolerance for breakeven detection"""

# ============================================================================
# Time Definitions
# ============================================================================

FAST_ROUNDTRIP_MAX_SECONDS = 60
"""Trades closed within 60 seconds are classified as 'fast'"""

SECONDS_PER_DAY = 86400
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

# ============================================================================
# System Limits (Hard Limits)
# ============================================================================

ABSOLUTE_MAX_LOOKBACK_HOURS = 168
"""Hard limit: Cannot query more than 1 week of data"""

TRADE_QUERY_HARD_LIMIT = 10000
"""Hard limit: Never fetch more than 10k trades (memory protection)"""

DB_POOL_MAX_SIZE = 10
"""Maximum database connection pool size"""

# ============================================================================
# Display Defaults
# ============================================================================

DEFAULT_TOP_POSITIONS = 3
"""Default number of top positions to show in reports"""