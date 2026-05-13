"""
Trading algorithm constants and tunable parameters.

Constants defined here are algorithm defaults that can be overridden
via environment variables when needed for operational tuning.
"""
import os
from decimal import Decimal

# ============================================================================
# Technical Indicator Windows (Algorithm Constants)
# ============================================================================

ATR_WINDOW = 8
"""ATR calculation period"""

BB_WINDOW = 10
BB_STD = 2
BB_LOWER_BAND = 1.0
BB_UPPER_BAND = 1.1
"""Bollinger Band parameters"""

MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 5
"""MACD indicator periods"""

RSI_WINDOW = 7
RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25
"""RSI parameters"""

SMA_FAST = 10
SMA_SLOW = 30
SMA = 15
SMA_VOLATILITY = 10
"""Simple Moving Average periods"""

SWING_WINDOW = 10
ROC_WINDOW = 5
ROC_5MIN = 2
ROC_5MIN_BUY_THRESHOLD = 10.0
ROC_5MIN_SELL_THRESHOLD = 10.0
ROC_BUY_24H = 2
ROC_SELL_24H = 1
"""Swing and Rate of Change parameters"""

MAX_OHLCV_ROWS = int(os.getenv('MAX_OHLCV_ROWS', '2000'))
"""Maximum OHLCV rows to fetch"""

# ============================================================================
# Stop-Loss & Take-Profit (Tunable - env override supported)
# ============================================================================

STOP_MODE = os.getenv('STOP_MODE', 'atr')
"""Stop mode: 'atr' or 'fixed'"""

ATR_MULTIPLIER_STOP = float(os.getenv('ATR_MULTIPLIER_STOP', '1.8'))
"""ATR multiplier for stop-loss calculation"""

STOP_MIN_PCT = float(os.getenv('STOP_MIN_PCT', '0.012'))
"""Minimum stop-loss percentage (1.2%)"""

SPREAD_CUSHION_PCT = float(os.getenv('SPREAD_CUSHION_PCT', '0.0015'))
"""Extra cushion when spread unavailable (0.15%)"""

SPREAD_TO_FEE_MIN = float(os.getenv('SPREAD_TO_FEE_MIN', '2.0'))
"""Minimum spread-to-fee ratio"""

TP_MIN_TICKS = int(os.getenv('TP_MIN_TICKS', '3'))
"""Minimum ticks for take-profit"""

SL_LIMIT_OFFSET_TICKS = int(os.getenv('SL_LIMIT_OFFSET_TICKS', '2'))
"""Stop-loss limit order offset in ticks"""

STOP_LOSS = float(os.getenv('STOP_LOSS', '-0.01'))
TAKE_PROFIT = float(os.getenv('TAKE_PROFIT', '0.025'))
TRAILING_STOP = float(os.getenv('TRAILING_STOP', '0.02'))
TRAILING_PERCENTAGE = float(os.getenv('TRAILING_PERCENTAGE', '0.02'))
TRAILING_LIMIT = float(os.getenv('TRAILING_LIMIT', '0.02'))

# ============================================================================
# Position Sizing (Tunable)
# ============================================================================

ORDER_SIZE_FIAT = float(os.getenv('ORDER_SIZE_FIAT', '60'))
"""Default order size in fiat currency"""

MIN_ORDER_AMOUNT_FIAT = float(os.getenv('MIN_ORDER_AMOUNT_FIAT', '10'))
"""Minimum order amount in fiat"""

MIN_BUY_VALUE = float(os.getenv('MIN_BUY_VALUE', '10'))
MIN_SELL_VALUE = float(os.getenv('MIN_SELL_VALUE', '10'))
MAX_VALUE_TO_BUY = float(os.getenv('MAX_VALUE_TO_BUY', '250'))
MIN_VALUE_TO_MONITOR = float(os.getenv('MIN_VALUE_TO_MONITOR', '10'))

MIN_L1_NOTIONAL_USD = float(os.getenv('MIN_L1_NOTIONAL_USD', '250'))
"""Minimum level-1 notional value"""

PREBRACKET_SIGMA_RATIO = float(os.getenv('PREBRACKET_SIGMA_RATIO', '1'))

# ============================================================================
# Scoring Thresholds (Tunable)
# ============================================================================

SCORE_BUY_TARGET = float(os.getenv('SCORE_BUY_TARGET', '5.5'))
"""Target score threshold for buy signals"""

SCORE_SELL_TARGET = float(os.getenv('SCORE_SELL_TARGET', '5.5'))
"""Target score threshold for sell signals"""

# ============================================================================
# Risk Management & Guardrails (Tunable)
# ============================================================================

COOLDOWN_BARS = int(os.getenv('COOLDOWN_BARS', '7'))
"""Bars to wait after position flip"""

FLIP_HYSTERESIS_PCT = float(os.getenv('FLIP_HYSTERESIS_PCT', '0.10'))
"""Percentage threshold to flip position direction (10%)"""

ALLOW_BUYS_ON_RED_DAY = os.getenv('ALLOW_BUYS_ON_RED_DAY', 'true').lower() == 'true'
"""Whether to allow buys when 24h is red"""

MIN_COOLDOWN = int(os.getenv('MIN_COOLDOWN', '120'))
"""Minimum cooldown in seconds between trades"""

SLEEP = int(os.getenv('SLEEP', '300'))
"""Main loop sleep interval in seconds"""

# ============================================================================
# Market Filters
# ============================================================================

MIN_QUOTE_VOLUME = float(os.getenv('MIN_QUOTE_VOLUME', '2000000'))
"""Minimum 24h quote volume to consider trading"""

# Currency pairs to ignore (from env)
_ignored = os.getenv('CURRENCY_PAIRS_IGNORED', '')
CURRENCY_PAIRS_IGNORED = [s.strip() for s in _ignored.split(',') if s.strip()]

# Special coin lists
_shill = os.getenv('SHILL_COINS', '')
SHILL_COINS = [s.strip() for s in _shill.split(',') if s.strip()]

_hodl = os.getenv('HODL', '')
HODL_COINS = [s.strip() for s in _hodl.split(',') if s.strip()]

QUOTE_CURRENCY = os.getenv('QUOTE_CURRENCY', 'USD')

# ============================================================================
# Fee Configuration (Environment-specific)
# ============================================================================

FEE_SIDE = os.getenv('FEE_SIDE', 'taker')
TAKER_FEE = float(os.getenv('TAKER_FEE', '0.0055'))
MAKER_FEE = float(os.getenv('MAKER_FEE', '0.003'))

# ============================================================================
# Order Cancellation Thresholds
# ============================================================================

CXL_BUY = float(os.getenv('CXL_BUY', '0.01'))
CXL_SELL = float(os.getenv('CXL_SELL', '0.01'))

# ============================================================================
# Ratios
# ============================================================================

BUY_RATIO = float(os.getenv('BUY_RATIO', '1.05'))
SELL_RATIO = float(os.getenv('SELL_RATIO', '0.95'))

# ============================================================================
# Passive Order Parameters
# ============================================================================

EDGE_BUFFER_PCT = float(os.getenv('EDGE_BUFFER_PCT', '0.00005'))
MIN_SPREAD_PCT = float(os.getenv('MIN_SPREAD_PCT', '0.00025'))
PASSIVE_IGNORE_FEES_FOR_SPREAD = os.getenv('PASSIVE_IGNORE_FEES_FOR_SPREAD', 'false').lower() == 'true'
MAX_LIFETIME = int(os.getenv('MAX_LIFETIME', '600'))
INVENTORY_BIAS_FACTOR = float(os.getenv('INVENTORY_BIAS_FACTOR', '0.10'))

# ============================================================================
# Enrichment
# ============================================================================

ENRICH_LIMIT = int(os.getenv('ENRICH_LIMIT', '20'))

# ============================================================================
# Validation on Import (Optional)
# ============================================================================

# To enable validation on import, set environment variable:
#   export CONFIG_VALIDATE_ON_IMPORT=1
#
# This will validate all constants when this module is first imported,
# raising an error if any values are invalid.

if os.getenv("CONFIG_VALIDATE_ON_IMPORT", "").lower() in {"1", "true", "yes"}:
    from .validators import validate_trading_constants
    result = validate_trading_constants()
    if not result.is_valid:
        raise ValueError(f"Trading constants validation failed:\n{result.format_report()}")