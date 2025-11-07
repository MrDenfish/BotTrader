# Config/validators.py
"""
Configuration validation system for BotTrader.

Validates all trading constants from Config/constants_trading.py with:
- Type correctness (int, float, Decimal, str, bool)
- Range constraints (min/max values)
- Relationship constraints (e.g., MACD_FAST < MACD_SLOW)
- Business logic (e.g., fees reasonable, thresholds sensible)

Usage:
    from Config.validators import validate_all_config

    # At startup
    validate_all_config()  # Raises ConfigError if invalid

    # Or get a validation report
    issues = validate_all_config(raise_on_error=False)
    if issues:
        for issue in issues:
            print(f"WARNING: {issue}")
"""

from __future__ import annotations
from decimal import Decimal
from typing import Any, Optional, List, Tuple, Callable
import os

from .exceptions import (
    ConfigError,
    ConfigValidationError,
    ConfigRangeError,
    ConfigTypeError,
    ConfigRelationshipError,
)


# ============================================================================
# Validation Rule System
# ============================================================================

class ValidationRule:
    """Base class for validation rules."""

    def __init__(self, key: str, description: str):
        self.key = key
        self.description = description

    def validate(self, value: Any) -> Optional[str]:
        """
        Validate a value.

        Returns:
            None if valid
            Error message string if invalid
        """
        raise NotImplementedError


class TypeRule(ValidationRule):
    """Validates value is correct type."""

    def __init__(self, key: str, expected_type: type, description: str = ""):
        super().__init__(key, description or f"Must be {expected_type.__name__}")
        self.expected_type = expected_type

    def validate(self, value: Any) -> Optional[str]:
        # Handle numeric types flexibly (int can be float, etc.)
        if self.expected_type in (int, float, Decimal):
            if not isinstance(value, (int, float, Decimal)):
                return f"Expected numeric type, got {type(value).__name__}"
            if self.expected_type == int and isinstance(value, float) and not value.is_integer():
                return f"Expected integer, got float with decimals: {value}"
        elif not isinstance(value, self.expected_type):
            return f"Expected {self.expected_type.__name__}, got {type(value).__name__}"
        return None


class RangeRule(ValidationRule):
    """Validates numeric value is within range."""

    def __init__(
            self,
            key: str,
            min_val: Optional[float] = None,
            max_val: Optional[float] = None,
            min_inclusive: bool = True,
            max_inclusive: bool = True,
            description: str = "",
    ):
        self.min_val = min_val
        self.max_val = max_val
        self.min_inclusive = min_inclusive
        self.max_inclusive = max_inclusive

        # Auto-generate description
        if not description:
            parts = []
            if min_val is not None:
                op = ">=" if min_inclusive else ">"
                parts.append(f"{op} {min_val}")
            if max_val is not None:
                op = "<=" if max_inclusive else "<"
                parts.append(f"{op} {max_val}")
            description = " and ".join(parts) if parts else "no range constraint"

        super().__init__(key, description)

    def validate(self, value: Any) -> Optional[str]:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return f"Cannot convert to number: {value!r}"

        if self.min_val is not None:
            if self.min_inclusive and num < self.min_val:
                return f"Must be >= {self.min_val}, got {num}"
            elif not self.min_inclusive and num <= self.min_val:
                return f"Must be > {self.min_val}, got {num}"

        if self.max_val is not None:
            if self.max_inclusive and num > self.max_val:
                return f"Must be <= {self.max_val}, got {num}"
            elif not self.max_inclusive and num >= self.max_val:
                return f"Must be < {self.max_val}, got {num}"

        return None


class ChoiceRule(ValidationRule):
    """Validates value is one of allowed choices."""

    def __init__(self, key: str, choices: List[Any], description: str = ""):
        self.choices = choices
        desc = description or f"Must be one of: {', '.join(str(c) for c in choices)}"
        super().__init__(key, desc)

    def validate(self, value: Any) -> Optional[str]:
        if value not in self.choices:
            return f"Must be one of {self.choices}, got {value!r}"
        return None


class RelationshipRule(ValidationRule):
    """Validates relationship between two config values."""

    def __init__(
            self,
            key1: str,
            key2: str,
            relationship: Callable[[Any, Any], bool],
            description: str,
    ):
        self.key1 = key1
        self.key2 = key2
        self.relationship = relationship
        super().__init__(f"{key1}/{key2}", description)

    def validate(self, val1: Any, val2: Any) -> Optional[str]:
        """Note: Takes two values, not one."""
        try:
            if not self.relationship(val1, val2):
                return self.description
        except Exception as e:
            return f"Error checking relationship: {e}"
        return None


# ============================================================================
# Trading Constants Validation Rules
# ============================================================================

TRADING_RULES = [
    # ========================================================================
    # Technical Indicator Windows
    # ========================================================================

    # ATR
    TypeRule("ATR_WINDOW", int, "ATR calculation period"),
    RangeRule("ATR_WINDOW", min_val=3, max_val=100, description="3-100 bars typical"),

    # Bollinger Bands
    TypeRule("BB_WINDOW", int, "Bollinger Band window"),
    RangeRule("BB_WINDOW", min_val=5, max_val=50, description="5-50 bars typical"),

    TypeRule("BB_STD", (int, float), "Bollinger Band standard deviations"),
    RangeRule("BB_STD", min_val=1.0, max_val=4.0, description="1-4 std devs typical"),

    TypeRule("BB_LOWER_BAND", float, "BB lower band multiplier"),
    RangeRule("BB_LOWER_BAND", min_val=0.5, max_val=2.0),

    TypeRule("BB_UPPER_BAND", float, "BB upper band multiplier"),
    RangeRule("BB_UPPER_BAND", min_val=0.5, max_val=2.0),

    # MACD
    TypeRule("MACD_FAST", int, "MACD fast period (bars)"),
    RangeRule("MACD_FAST", min_val=3, max_val=50, description="3-50 bars typical"),

    TypeRule("MACD_SLOW", int, "MACD slow period (bars)"),
    RangeRule("MACD_SLOW", min_val=5, max_val=100, description="5-100 bars typical"),

    TypeRule("MACD_SIGNAL", int, "MACD signal period (bars)"),
    RangeRule("MACD_SIGNAL", min_val=3, max_val=30, description="3-30 bars typical"),

    # RSI
    TypeRule("RSI_WINDOW", int, "RSI lookback window"),
    RangeRule("RSI_WINDOW", min_val=3, max_val=50, description="3-50 bars typical"),

    TypeRule("RSI_OVERBOUGHT", (int, float), "RSI overbought threshold"),
    RangeRule("RSI_OVERBOUGHT", min_val=50, max_val=100, description="50-100"),

    TypeRule("RSI_OVERSOLD", (int, float), "RSI oversold threshold"),
    RangeRule("RSI_OVERSOLD", min_val=0, max_val=50, description="0-50"),

    # SMA
    TypeRule("SMA_FAST", int, "Fast SMA period"),
    RangeRule("SMA_FAST", min_val=3, max_val=50),

    TypeRule("SMA_SLOW", int, "Slow SMA period"),
    RangeRule("SMA_SLOW", min_val=5, max_val=200),

    TypeRule("SMA", int, "SMA period"),
    RangeRule("SMA", min_val=3, max_val=100),

    TypeRule("SMA_VOLATILITY", int, "Volatility SMA period"),
    RangeRule("SMA_VOLATILITY", min_val=3, max_val=50),

    # Swing & ROC
    TypeRule("SWING_WINDOW", int, "Swing detection window"),
    RangeRule("SWING_WINDOW", min_val=3, max_val=50),

    TypeRule("ROC_WINDOW", int, "Rate of Change window"),
    RangeRule("ROC_WINDOW", min_val=1, max_val=50),

    TypeRule("ROC_5MIN", int, "5-minute ROC period"),
    RangeRule("ROC_5MIN", min_val=1, max_val=20),

    TypeRule("ROC_BUY_24H", (int, float), "24h ROC buy threshold"),
    RangeRule("ROC_BUY_24H", min_val=0.1, max_val=50),

    TypeRule("ROC_SELL_24H", (int, float), "24h ROC sell threshold"),
    RangeRule("ROC_SELL_24H", min_val=0.1, max_val=50),

    TypeRule("MAX_OHLCV_ROWS", int, "Max OHLCV rows to fetch"),
    RangeRule("MAX_OHLCV_ROWS", min_val=100, max_val=10000),

    # ========================================================================
    # Stop-Loss & Take-Profit
    # ========================================================================

    TypeRule("STOP_MODE", str, "Stop mode: 'atr' or 'fixed'"),
    ChoiceRule("STOP_MODE", ["atr", "fixed"], "Must be 'atr' or 'fixed'"),

    TypeRule("ATR_MULTIPLIER_STOP", float, "ATR multiplier for stop-loss"),
    RangeRule("ATR_MULTIPLIER_STOP", min_val=0.5, max_val=10.0),

    # Note: ATR_MULTIPLIER_TARGET removed - not in constants_trading.py
    # Instead, TAKE_PROFIT is used as a percentage

    TypeRule("STOP_MIN_PCT", float, "Minimum stop-loss percentage"),
    RangeRule("STOP_MIN_PCT", min_val=0.001, max_val=0.10, description="0.1% to 10%"),

    TypeRule("SPREAD_CUSHION_PCT", float, "Spread cushion percentage"),
    RangeRule("SPREAD_CUSHION_PCT", min_val=0.0001, max_val=0.01, description="0.01% to 1%"),

    TypeRule("SPREAD_TO_FEE_MIN", float, "Minimum spread-to-fee ratio"),
    RangeRule("SPREAD_TO_FEE_MIN", min_val=0.5, max_val=10.0),

    TypeRule("TP_MIN_TICKS", int, "Minimum ticks for take-profit"),
    RangeRule("TP_MIN_TICKS", min_val=1, max_val=100),

    TypeRule("SL_LIMIT_OFFSET_TICKS", int, "Stop-loss limit order offset"),
    RangeRule("SL_LIMIT_OFFSET_TICKS", min_val=1, max_val=50),

    TypeRule("STOP_LOSS", float, "Stop loss percentage"),
    RangeRule("STOP_LOSS", min_val=-0.50, max_val=0.0, description="-50% to 0%"),

    TypeRule("TAKE_PROFIT", float, "Take profit percentage"),
    RangeRule("TAKE_PROFIT", min_val=0.001, max_val=1.0, description="0.1% to 100%"),

    TypeRule("TRAILING_STOP", float, "Trailing stop percentage"),
    RangeRule("TRAILING_STOP", min_val=0.001, max_val=0.50, description="0.1% to 50%"),

    TypeRule("TRAILING_PERCENTAGE", float, "Trailing percentage"),
    RangeRule("TRAILING_PERCENTAGE", min_val=0.001, max_val=0.50),

    TypeRule("TRAILING_LIMIT", float, "Trailing limit"),
    RangeRule("TRAILING_LIMIT", min_val=0.001, max_val=0.50),

    # ========================================================================
    # Position Sizing
    # ========================================================================

    TypeRule("ORDER_SIZE_FIAT", float, "Default order size in fiat"),
    RangeRule("ORDER_SIZE_FIAT", min_val=1.0, max_val=100000.0),

    TypeRule("MIN_ORDER_AMOUNT_FIAT", float, "Minimum order amount"),
    RangeRule("MIN_ORDER_AMOUNT_FIAT", min_val=1.0, max_val=1000.0),

    TypeRule("MIN_BUY_VALUE", float, "Minimum buy value"),
    RangeRule("MIN_BUY_VALUE", min_val=1.0, max_val=1000.0),

    TypeRule("MIN_SELL_VALUE", float, "Minimum sell value"),
    RangeRule("MIN_SELL_VALUE", min_val=1.0, max_val=1000.0),

    TypeRule("MAX_VALUE_TO_BUY", float, "Maximum value to buy"),
    RangeRule("MAX_VALUE_TO_BUY", min_val=10.0, max_val=1000000.0),

    TypeRule("MIN_VALUE_TO_MONITOR", float, "Minimum value to monitor"),
    RangeRule("MIN_VALUE_TO_MONITOR", min_val=1.0, max_val=1000.0),

    TypeRule("MIN_L1_NOTIONAL_USD", float, "Minimum level-1 notional"),
    RangeRule("MIN_L1_NOTIONAL_USD", min_val=10.0, max_val=10000.0),

    TypeRule("PREBRACKET_SIGMA_RATIO", float, "Pre-bracket sigma ratio"),
    RangeRule("PREBRACKET_SIGMA_RATIO", min_val=0.1, max_val=5.0),

    # ========================================================================
    # Scoring Thresholds
    # ========================================================================

    TypeRule("SCORE_BUY_TARGET", float, "Target score for buy signals"),
    RangeRule("SCORE_BUY_TARGET", min_val=0.0, max_val=20.0),

    TypeRule("SCORE_SELL_TARGET", float, "Target score for sell signals"),
    RangeRule("SCORE_SELL_TARGET", min_val=0.0, max_val=20.0),

    # ========================================================================
    # Risk Management & Guardrails
    # ========================================================================

    TypeRule("COOLDOWN_BARS", int, "Bars to wait after position flip"),
    RangeRule("COOLDOWN_BARS", min_val=0, max_val=100),

    TypeRule("FLIP_HYSTERESIS_PCT", float, "Flip position threshold percentage"),
    RangeRule("FLIP_HYSTERESIS_PCT", min_val=0.01, max_val=1.0, description="1% to 100%"),

    TypeRule("ALLOW_BUYS_ON_RED_DAY", bool, "Allow buys on red days"),

    TypeRule("MIN_COOLDOWN", int, "Minimum cooldown in seconds"),
    RangeRule("MIN_COOLDOWN", min_val=0, max_val=3600, description="0-3600 seconds (1 hour)"),

    TypeRule("SLEEP", int, "Main loop sleep interval in seconds"),
    RangeRule("SLEEP", min_val=1, max_val=3600, description="1-3600 seconds"),

    # ========================================================================
    # Market Filters
    # ========================================================================

    TypeRule("MIN_QUOTE_VOLUME", float, "Minimum 24h quote volume"),
    RangeRule("MIN_QUOTE_VOLUME", min_val=1000.0, max_val=1000000000.0),

    TypeRule("QUOTE_CURRENCY", str, "Quote currency (e.g., USD, USDT)"),

    # ========================================================================
    # Fee Configuration
    # ========================================================================

    TypeRule("FEE_SIDE", str, "Fee side: 'taker' or 'maker'"),
    ChoiceRule("FEE_SIDE", ["taker", "maker"], "Must be 'taker' or 'maker'"),

    TypeRule("TAKER_FEE", float, "Taker fee as decimal (e.g., 0.0055 = 0.55%)"),
    RangeRule("TAKER_FEE", min_val=0.0, max_val=0.02, description="0% to 2%"),

    TypeRule("MAKER_FEE", float, "Maker fee as decimal"),
    RangeRule("MAKER_FEE", min_val=0.0, max_val=0.02, description="0% to 2%"),

    # ========================================================================
    # Order Cancellation Thresholds
    # ========================================================================

    TypeRule("CXL_BUY", float, "Buy cancellation threshold"),
    RangeRule("CXL_BUY", min_val=0.0, max_val=0.50),

    TypeRule("CXL_SELL", float, "Sell cancellation threshold"),
    RangeRule("CXL_SELL", min_val=0.0, max_val=0.50),

    # ========================================================================
    # Ratios
    # ========================================================================

    TypeRule("BUY_RATIO", float, "Buy ratio multiplier"),
    RangeRule("BUY_RATIO", min_val=0.5, max_val=2.0),

    TypeRule("SELL_RATIO", float, "Sell ratio multiplier"),
    RangeRule("SELL_RATIO", min_val=0.5, max_val=2.0),

    # ========================================================================
    # Passive Order Parameters
    # ========================================================================

    TypeRule("EDGE_BUFFER_PCT", float, "Edge buffer percentage"),
    RangeRule("EDGE_BUFFER_PCT", min_val=0.00001, max_val=0.01),

    TypeRule("MIN_SPREAD_PCT", float, "Minimum spread percentage"),
    RangeRule("MIN_SPREAD_PCT", min_val=0.0001, max_val=0.05),

    TypeRule("PASSIVE_IGNORE_FEES_FOR_SPREAD", bool, "Ignore fees for spread calc"),

    TypeRule("MAX_LIFETIME", int, "Max order lifetime in seconds"),
    RangeRule("MAX_LIFETIME", min_val=1, max_val=86400, description="1s to 24h"),

    TypeRule("INVENTORY_BIAS_FACTOR", float, "Inventory bias factor"),
    RangeRule("INVENTORY_BIAS_FACTOR", min_val=0.0, max_val=1.0),

    # ========================================================================
    # Enrichment
    # ========================================================================

    TypeRule("ENRICH_LIMIT", int, "Enrichment limit"),
    RangeRule("ENRICH_LIMIT", min_val=1, max_val=1000),
]

# Relationship rules (things that must be true between multiple values)
TRADING_RELATIONSHIP_RULES = [
    RelationshipRule(
        "MACD_FAST", "MACD_SLOW",
        lambda fast, slow: fast < slow,
        "MACD_FAST must be < MACD_SLOW"
    ),
    RelationshipRule(
        "RSI_OVERSOLD", "RSI_OVERBOUGHT",
        lambda oversold, overbought: oversold < overbought,
        "RSI_OVERSOLD must be < RSI_OVERBOUGHT"
    ),
    RelationshipRule(
        "SMA_FAST", "SMA_SLOW",
        lambda fast, slow: fast < slow,
        "SMA_FAST must be < SMA_SLOW"
    ),
    RelationshipRule(
        "MAKER_FEE", "TAKER_FEE",
        lambda maker, taker: maker <= taker,
        "MAKER_FEE should be <= TAKER_FEE (usually maker is cheaper)"
    ),
    RelationshipRule(
        "MIN_ORDER_AMOUNT_FIAT", "ORDER_SIZE_FIAT",
        lambda min_amt, order_size: min_amt <= order_size,
        "MIN_ORDER_AMOUNT_FIAT should be <= ORDER_SIZE_FIAT"
    ),
    RelationshipRule(
        "MIN_BUY_VALUE", "MAX_VALUE_TO_BUY",
        lambda min_buy, max_buy: min_buy <= max_buy,
        "MIN_BUY_VALUE must be <= MAX_VALUE_TO_BUY"
    ),
    RelationshipRule(
        "BB_LOWER_BAND", "BB_UPPER_BAND",
        lambda lower, upper: lower <= upper,
        "BB_LOWER_BAND should be <= BB_UPPER_BAND"
    ),
    RelationshipRule(
        "STOP_LOSS", "TAKE_PROFIT",
        lambda stop, profit: stop < 0 and profit > 0,
        "STOP_LOSS should be negative and TAKE_PROFIT positive"
    ),
]

# ============================================================================
# Report Config Validation Rules
# ============================================================================

REPORT_RULES = [
    TypeRule("lookback_hours", int, "Report lookback window"),
    RangeRule("lookback_hours", min_val=1, max_val=168, description="1 hour to 1 week"),

    TypeRule("taker_fee", float, "Taker fee for break-even calc"),
    RangeRule("taker_fee", min_val=0.0, max_val=0.02),

    TypeRule("maker_fee", float, "Maker fee for break-even calc"),
    RangeRule("maker_fee", min_val=0.0, max_val=0.02),

    TypeRule("starting_equity", float, "Starting equity for drawdown calc"),
    RangeRule("starting_equity", min_val=100, max_val=10_000_000, description="$100 to $10M"),
]


# ============================================================================
# Validation Engine
# ============================================================================

class ValidationResult:
    """Result of validation check."""

    def __init__(self):
        self.errors: List[Tuple[str, str]] = []  # (key, error_message)
        self.warnings: List[Tuple[str, str]] = []  # (key, warning_message)

    def add_error(self, key: str, message: str):
        self.errors.append((key, message))

    def add_warning(self, key: str, message: str):
        self.warnings.append((key, message))

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def __bool__(self) -> bool:
        return self.is_valid

    def format_report(self, include_warnings: bool = True) -> str:
        """Format validation result as human-readable report."""
        lines = []

        if self.errors:
            lines.append("VALIDATION ERRORS:")
            for key, msg in self.errors:
                lines.append(f"  ❌ {key}: {msg}")

        if include_warnings and self.warnings:
            if lines:
                lines.append("")
            lines.append("VALIDATION WARNINGS:")
            for key, msg in self.warnings:
                lines.append(f"  ⚠️  {key}: {msg}")

        if not self.errors and not self.warnings:
            lines.append("✅ All validation checks passed!")

        return "\n".join(lines)


def validate_config_dict(
        config: dict,
        rules: List[ValidationRule],
        relationship_rules: List[RelationshipRule] = None,
) -> ValidationResult:
    """
    Validate a config dictionary against a set of rules.

    Args:
        config: Dictionary of config values
        rules: List of validation rules
        relationship_rules: Optional list of relationship rules

    Returns:
        ValidationResult with errors/warnings
    """
    result = ValidationResult()

    # Check individual rules
    for rule in rules:
        if rule.key not in config:
            result.add_warning(rule.key, "Not found in config (using default)")
            continue

        value = config[rule.key]
        error = rule.validate(value)
        if error:
            result.add_error(rule.key, error)

    # Check relationship rules
    if relationship_rules:
        for rule in relationship_rules:
            if rule.key1 not in config or rule.key2 not in config:
                continue  # Already warned about missing keys above

            val1 = config[rule.key1]
            val2 = config[rule.key2]
            error = rule.validate(val1, val2)
            if error:
                result.add_error(rule.key, error)

    return result


def validate_trading_constants() -> ValidationResult:
    """
    Validate trading constants from Config.constants_trading.

    This can be imported safely because we're not importing at module level.
    """
    try:
        from . import constants_trading as ct
    except ImportError:
        result = ValidationResult()
        result.add_error("import", "Cannot import Config.constants_trading")
        return result

    # Build config dict from module - ALL constants
    config = {
        # Technical Indicators
        "ATR_WINDOW": ct.ATR_WINDOW,
        "BB_WINDOW": ct.BB_WINDOW,
        "BB_STD": ct.BB_STD,
        "BB_LOWER_BAND": ct.BB_LOWER_BAND,
        "BB_UPPER_BAND": ct.BB_UPPER_BAND,
        "MACD_FAST": ct.MACD_FAST,
        "MACD_SLOW": ct.MACD_SLOW,
        "MACD_SIGNAL": ct.MACD_SIGNAL,
        "RSI_WINDOW": ct.RSI_WINDOW,
        "RSI_OVERBOUGHT": ct.RSI_OVERBOUGHT,
        "RSI_OVERSOLD": ct.RSI_OVERSOLD,
        "SMA_FAST": ct.SMA_FAST,
        "SMA_SLOW": ct.SMA_SLOW,
        "SMA": ct.SMA,
        "SMA_VOLATILITY": ct.SMA_VOLATILITY,
        "SWING_WINDOW": ct.SWING_WINDOW,
        "ROC_WINDOW": ct.ROC_WINDOW,
        "ROC_5MIN": ct.ROC_5MIN,
        "ROC_BUY_24H": ct.ROC_BUY_24H,
        "ROC_SELL_24H": ct.ROC_SELL_24H,
        "MAX_OHLCV_ROWS": ct.MAX_OHLCV_ROWS,

        # Stop-Loss & Take-Profit
        "STOP_MODE": ct.STOP_MODE,
        "ATR_MULTIPLIER_STOP": ct.ATR_MULTIPLIER_STOP,
        "STOP_MIN_PCT": ct.STOP_MIN_PCT,
        "SPREAD_CUSHION_PCT": ct.SPREAD_CUSHION_PCT,
        "SPREAD_TO_FEE_MIN": ct.SPREAD_TO_FEE_MIN,
        "TP_MIN_TICKS": ct.TP_MIN_TICKS,
        "SL_LIMIT_OFFSET_TICKS": ct.SL_LIMIT_OFFSET_TICKS,
        "STOP_LOSS": ct.STOP_LOSS,
        "TAKE_PROFIT": ct.TAKE_PROFIT,
        "TRAILING_STOP": ct.TRAILING_STOP,
        "TRAILING_PERCENTAGE": ct.TRAILING_PERCENTAGE,
        "TRAILING_LIMIT": ct.TRAILING_LIMIT,

        # Position Sizing
        "ORDER_SIZE_FIAT": ct.ORDER_SIZE_FIAT,
        "MIN_ORDER_AMOUNT_FIAT": ct.MIN_ORDER_AMOUNT_FIAT,
        "MIN_BUY_VALUE": ct.MIN_BUY_VALUE,
        "MIN_SELL_VALUE": ct.MIN_SELL_VALUE,
        "MAX_VALUE_TO_BUY": ct.MAX_VALUE_TO_BUY,
        "MIN_VALUE_TO_MONITOR": ct.MIN_VALUE_TO_MONITOR,
        "MIN_L1_NOTIONAL_USD": ct.MIN_L1_NOTIONAL_USD,
        "PREBRACKET_SIGMA_RATIO": ct.PREBRACKET_SIGMA_RATIO,

        # Scoring
        "SCORE_BUY_TARGET": ct.SCORE_BUY_TARGET,
        "SCORE_SELL_TARGET": ct.SCORE_SELL_TARGET,

        # Risk Management
        "COOLDOWN_BARS": ct.COOLDOWN_BARS,
        "FLIP_HYSTERESIS_PCT": ct.FLIP_HYSTERESIS_PCT,
        "ALLOW_BUYS_ON_RED_DAY": ct.ALLOW_BUYS_ON_RED_DAY,
        "MIN_COOLDOWN": ct.MIN_COOLDOWN,
        "SLEEP": ct.SLEEP,

        # Market Filters
        "MIN_QUOTE_VOLUME": ct.MIN_QUOTE_VOLUME,
        "QUOTE_CURRENCY": ct.QUOTE_CURRENCY,

        # Fees
        "FEE_SIDE": ct.FEE_SIDE,
        "TAKER_FEE": ct.TAKER_FEE,
        "MAKER_FEE": ct.MAKER_FEE,

        # Cancellation
        "CXL_BUY": ct.CXL_BUY,
        "CXL_SELL": ct.CXL_SELL,

        # Ratios
        "BUY_RATIO": ct.BUY_RATIO,
        "SELL_RATIO": ct.SELL_RATIO,

        # Passive Orders
        "EDGE_BUFFER_PCT": ct.EDGE_BUFFER_PCT,
        "MIN_SPREAD_PCT": ct.MIN_SPREAD_PCT,
        "PASSIVE_IGNORE_FEES_FOR_SPREAD": ct.PASSIVE_IGNORE_FEES_FOR_SPREAD,
        "MAX_LIFETIME": ct.MAX_LIFETIME,
        "INVENTORY_BIAS_FACTOR": ct.INVENTORY_BIAS_FACTOR,

        # Enrichment
        "ENRICH_LIMIT": ct.ENRICH_LIMIT,
    }

    return validate_config_dict(config, TRADING_RULES, TRADING_RELATIONSHIP_RULES)


def validate_report_config(report_cfg) -> ValidationResult:
    """Validate a ReportConfig dataclass instance."""
    config = {
        "lookback_hours": report_cfg.lookback_hours,
        "taker_fee": report_cfg.taker_fee,
        "maker_fee": report_cfg.maker_fee,
        "starting_equity": report_cfg.starting_equity,
    }

    return validate_config_dict(config, REPORT_RULES)


def validate_all_config(raise_on_error: bool = True, verbose: bool = False) -> Optional[ValidationResult]:
    """
    Validate all configuration.

    Args:
        raise_on_error: If True, raise ConfigError on validation failure
        verbose: If True, print validation report even if successful

    Returns:
        ValidationResult if raise_on_error=False
        None if raise_on_error=True (raises on error instead)

    Raises:
        ConfigError: If validation fails and raise_on_error=True
    """
    # Validate trading constants
    result = validate_trading_constants()

    # Validate report config (if imported)
    try:
        from .config import load_report_config
        report_cfg = load_report_config()
        report_result = validate_report_config(report_cfg)

        # Merge results
        result.errors.extend(report_result.errors)
        result.warnings.extend(report_result.warnings)
    except ImportError:
        pass  # Report config not available, skip

    # Print or raise
    if verbose or not result.is_valid:
        print("\n" + "=" * 60)
        print("CONFIG VALIDATION REPORT")
        print("=" * 60)
        print(result.format_report())
        print("=" * 60 + "\n")

    if not result.is_valid and raise_on_error:
        raise ConfigError(f"Config validation failed:\n{result.format_report(include_warnings=False)}")

    return result if not raise_on_error else None


# ============================================================================
# Quick Validation Helpers
# ============================================================================

def validate_on_import():
    """
    Validate config when module is imported.

    Use this in Config/__init__.py:
        from .validators import validate_on_import
        validate_on_import()
    """
    # Only validate if explicitly requested via env var
    if os.getenv("CONFIG_VALIDATE_ON_IMPORT", "").lower() in {"1", "true", "yes"}:
        validate_all_config(raise_on_error=True, verbose=True)


# ============================================================================
# CLI for manual validation
# ============================================================================

if __name__ == "__main__":
    """Run validation from command line: python -m Config.validators"""
    import sys

    print("Validating BotTrader configuration...")
    print()

    result = validate_all_config(raise_on_error=False, verbose=True)

    sys.exit(0 if result.is_valid else 1)