"""
Report-specific constants and configuration.
"""
import os
from decimal import Decimal

# Import shared constants
from Config.constants_core import (
    DEFAULT_TOP_POSITIONS as CORE_DEFAULT_TOP_POSITIONS,
    ABSOLUTE_MAX_LOOKBACK_HOURS,
)

# ============================================================================
# Report Display Defaults
# ============================================================================

DEFAULT_TOP_POSITIONS = int(os.getenv('TOP_POSITIONS_DISPLAY', str(CORE_DEFAULT_TOP_POSITIONS)))
"""Number of top positions to show in email"""

DEFAULT_LOOKBACK_HOURS = int(os.getenv('REPORT_LOOKBACK_HOURS', '24'))
"""Default report window in hours"""

DEFAULT_LOOKBACK_MINUTES = int(os.getenv('REPORT_LOOKBACK_MINUTES', '1440'))
"""Default report window in minutes (24h)"""

MAX_LOOKBACK_HOURS = ABSOLUTE_MAX_LOOKBACK_HOURS
"""Maximum lookback window (inherited from core)"""

MIN_LOOKBACK_HOURS = 1
"""Minimum lookback window"""

# ============================================================================
# Report Table Names (Environment-specific)
# ============================================================================

REPORT_PNL_TABLE = os.getenv('REPORT_PNL_TABLE', 'public.trade_records')
REPORT_EXECUTIONS_TABLE = os.getenv('REPORT_EXECUTIONS_TABLE', 'public.trade_records')
REPORT_PRICE_TABLE = os.getenv('REPORT_PRICE_TABLE', 'public.report_prices')
REPORT_POSITIONS_TABLE = os.getenv('REPORT_POSITIONS_TABLE', 'public.report_positions')
REPORT_TRADES_TABLE = os.getenv('REPORT_TRADES_TABLE', 'public.report_trades')
REPORT_WINRATE_TABLE = os.getenv('REPORT_WINRATE_TABLE', 'public.trade_records')
REPORT_BALANCES_TABLE = os.getenv('REPORT_BALANCES_TABLE', 'public.report_balances')

# ============================================================================
# Report Column Mappings (Schema flexibility)
# ============================================================================

REPORT_COL_SYMBOL = os.getenv('REPORT_COL_SYMBOL', 'symbol')
REPORT_COL_SIDE = os.getenv('REPORT_COL_SIDE', 'side')
REPORT_COL_PRICE = os.getenv('REPORT_COL_PRICE', 'price')
REPORT_COL_SIZE = os.getenv('REPORT_COL_SIZE', 'size')
REPORT_COL_TIME = os.getenv('REPORT_COL_TIME', 'order_time')
REPORT_COL_POS_QTY = os.getenv('REPORT_COL_POS_QTY', 'position_qty')
REPORT_COL_PNL = os.getenv('REPORT_COL_PNL', 'pnl_usd')

REPORT_PRICE_COL = os.getenv('REPORT_PRICE_COL', 'price')
REPORT_PRICE_TIME_COL = os.getenv('REPORT_PRICE_TIME_COL', 'order_time')
REPORT_PRICE_SYM_COL = os.getenv('REPORT_PRICE_SYM_COL', 'symbol')

REPORT_CASH_SYM_COL = os.getenv('REPORT_CASH_SYM_COL', 'symbol')
REPORT_CASH_AMT_COL = os.getenv('REPORT_CASH_AMT_COL', 'balance')
REPORT_CASH_SYMBOLS = [s.strip().upper() for s in os.getenv('REPORT_CASH_SYMBOLS', 'USD,USDC,USDT').split(',') if s.strip()]

# ============================================================================
# Report Behavior
# ============================================================================

REPORT_SHOW_DETAILS = os.getenv('REPORT_SHOW_DETAILS', '0') == '1'
"""Whether to include detailed position/trade tables"""

REPORT_DEBUG = os.getenv('REPORT_DEBUG', '0') == '1'
"""Enable debug output in reports"""

REPORT_USE_PT_DAY = os.getenv('REPORT_USE_PT_DAY', '0') in {'1', 'true', 'TRUE', 'yes', 'Yes'}
"""Use Pacific Time day instead of rolling hours"""

# ============================================================================
# Fee Configuration (for break-even calculations)
# ============================================================================

STARTING_EQUITY_USD = float(os.getenv('STARTING_EQUITY_USD', '3000'))
"""Starting equity for drawdown calculations"""

# Import from trading for consistency
from Config.constants_trading import TAKER_FEE, MAKER_FEE