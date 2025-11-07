# Config/exceptions.py
"""
Custom exceptions for configuration validation.
These provide clear, actionable error messages when config is invalid.
"""

from typing import Optional, Any


class ConfigError(Exception):
    """Base exception for all configuration errors."""
    pass


class ConfigValidationError(ConfigError):
    """Raised when a config value fails validation."""

    def __init__(self, key: str, value: Any, reason: str, suggestion: Optional[str] = None):
        self.key = key
        self.value = value
        self.reason = reason
        self.suggestion = suggestion

        msg = f"Invalid config: {key}={value!r}\n  Reason: {reason}"
        if suggestion:
            msg += f"\n  Suggestion: {suggestion}"
        super().__init__(msg)


class ConfigRangeError(ConfigValidationError):
    """Raised when a numeric value is outside allowed range."""

    def __init__(self, key: str, value: Any, min_val: Optional[float], max_val: Optional[float]):
        range_str = []
        if min_val is not None:
            range_str.append(f">= {min_val}")
        if max_val is not None:
            range_str.append(f"<= {max_val}")

        reason = f"Value must be {' and '.join(range_str)}"
        suggestion = None

        # Provide helpful suggestions for common mistakes
        if min_val is not None and value < min_val:
            suggestion = f"Try setting {key}={min_val} or higher"
        elif max_val is not None and value > max_val:
            suggestion = f"Try setting {key}={max_val} or lower"

        super().__init__(key, value, reason, suggestion)


class ConfigTypeError(ConfigValidationError):
    """Raised when a value has the wrong type."""

    def __init__(self, key: str, value: Any, expected_type: type):
        actual_type = type(value).__name__
        expected_name = expected_type.__name__

        reason = f"Expected {expected_name}, got {actual_type}"
        suggestion = f"Convert to {expected_name}: e.g., {key}={expected_type.__name__}({value!r})"

        super().__init__(key, value, reason, suggestion)


class ConfigRelationshipError(ConfigValidationError):
    """Raised when relationships between config values are invalid."""

    def __init__(self, key1: str, val1: Any, key2: str, val2: Any, relationship: str):
        msg = f"Invalid relationship: {key1}={val1!r} vs {key2}={val2!r}\n  Reason: {relationship}"
        super().__init__(f"{key1}/{key2}", f"{val1}/{val2}", relationship)


class ConfigMissingError(ConfigError):
    """Raised when a required config value is missing."""

    def __init__(self, key: str, location: str):
        msg = f"Required config missing: {key}\n  Expected in: {location}"
        super().__init__(msg)
        self.key = key
        self.location = location