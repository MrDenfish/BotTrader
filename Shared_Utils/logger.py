"""
Enhanced Structured Logger for BotTrader

Provides:
- Context injection (trade_id, symbol, component)
- Performance tracking decorators
- Custom trading log levels (BUY, SELL, ORDER_SENT, etc.)
- Thread-safe context management
- Backward compatibility with existing LoggerManager

Usage:
    # Basic logging
    logger = get_logger('webhook')
    logger.info('Processing order')

    # With context
    logger = get_logger('webhook', context={'trade_id': '12345', 'symbol': 'BTC-USD'})
    logger.info('Order placed')

    # Performance tracking
    @log_performance('webhook')
    async def process_order(order):
        ...

    # Custom trading levels
    logger.buy('BTC-USD buy order executed')
    logger.sell('BTC-USD sell order executed')
"""

import asyncio
import functools
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional, Union

from Config.logging_config import LoggingConfig, get_logging_config, TRADE_LOG_LEVELS


# Thread-safe context storage
_log_context: ContextVar[Dict[str, Any]] = ContextVar('log_context', default={})


class StructuredLogger(logging.LoggerAdapter):
    """
    Enhanced logger adapter with context injection and custom methods.

    This adapter wraps standard Python loggers to add:
    - Automatic context injection
    - Custom trading log levels
    - Performance tracking
    - Structured data support
    """

    def __init__(self, logger: logging.Logger, extra: Optional[Dict[str, Any]] = None):
        """
        Initialize structured logger.

        Args:
            logger: Base Python logger
            extra: Additional context data
        """
        super().__init__(logger, extra or {})
        self._component = extra.get('component') if extra else None

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        """
        Process log message to inject context.

        Args:
            msg: Log message
            kwargs: Log keyword arguments

        Returns:
            Tuple of (message, kwargs) with context injected
        """
        # Merge context from various sources
        context = {}

        # 1. Thread-local context (set via set_context)
        context.update(_log_context.get())

        # 2. Logger's default extra
        if self.extra:
            context.update(self.extra)

        # 3. Per-call extra
        if 'extra' in kwargs:
            extra = kwargs.pop('extra')
            context.update(extra)

        # Inject merged context
        if context:
            kwargs['extra'] = {'context': context}

        return msg, kwargs

    # ========================================================================
    # Custom Trading Log Levels
    # ========================================================================

    def buy(self, msg: str, *args, **kwargs) -> None:
        """Log BUY level message."""
        self.log(TRADE_LOG_LEVELS['BUY'], msg, *args, **kwargs)

    def sell(self, msg: str, *args, **kwargs) -> None:
        """Log SELL level message."""
        self.log(TRADE_LOG_LEVELS['SELL'], msg, *args, **kwargs)

    def order_sent(self, msg: str, *args, **kwargs) -> None:
        """Log ORDER_SENT level message."""
        self.log(TRADE_LOG_LEVELS['ORDER_SENT'], msg, *args, **kwargs)

    def take_profit(self, msg: str, *args, **kwargs) -> None:
        """Log TAKE_PROFIT level message."""
        self.log(TRADE_LOG_LEVELS['TAKE_PROFIT'], msg, *args, **kwargs)

    def take_loss(self, msg: str, *args, **kwargs) -> None:
        """Log TAKE_LOSS level message."""
        self.log(TRADE_LOG_LEVELS['TAKE_LOSS'], msg, *args, **kwargs)

    def stop_loss(self, msg: str, *args, **kwargs) -> None:
        """Log STOP_LOSS level message."""
        self.log(TRADE_LOG_LEVELS['STOP_LOSS'], msg, *args, **kwargs)

    def insufficient_funds(self, msg: str, *args, **kwargs) -> None:
        """Log INSUFFICIENT_FUNDS level message."""
        self.log(TRADE_LOG_LEVELS['INSUFFICIENT_FUNDS'], msg, *args, **kwargs)

    def bad_order(self, msg: str, *args, **kwargs) -> None:
        """Log BAD_ORDER level message."""
        self.log(TRADE_LOG_LEVELS['BAD_ORDER'], msg, *args, **kwargs)


# ============================================================================
# Logger Factory
# ============================================================================

_loggers: Dict[str, StructuredLogger] = {}


def get_logger(
    name: str,
    context: Optional[Dict[str, Any]] = None,
    config: Optional[LoggingConfig] = None,
) -> StructuredLogger:
    """
    Get or create a structured logger.

    Args:
        name: Logger name (e.g., 'webhook', 'sighook', 'shared')
        context: Optional default context (trade_id, symbol, component, etc.)
        config: Optional custom logging config (uses default if not provided)

    Returns:
        StructuredLogger instance

    Examples:
        >>> logger = get_logger('webhook')
        >>> logger.info('Order received')

        >>> logger = get_logger('webhook', context={'component': 'order_processor'})
        >>> logger.info('Processing order', extra={'trade_id': '12345'})
    """
    cache_key = f"{name}:{id(context)}"

    if cache_key not in _loggers:
        # Get or use default config
        log_config = config or get_logging_config()

        # Configure base logger
        base_logger = log_config.configure_logger(
            logger_name=name,
            log_file=f"{name}.log",
            component=context.get('component') if context else None,
        )

        # Wrap in StructuredLogger
        _loggers[cache_key] = StructuredLogger(base_logger, extra=context)

    return _loggers[cache_key]


def get_component_logger(component: str, **extra_context) -> StructuredLogger:
    """
    Get a logger with component in context.

    Args:
        component: Component name (e.g., 'webhook', 'order_manager')
        **extra_context: Additional context fields

    Returns:
        StructuredLogger with component context

    Examples:
        >>> logger = get_component_logger('order_manager')
        >>> logger.info('Order validated')
    """
    context = {'component': component}
    context.update(extra_context)
    return get_logger(component, context=context)


# ============================================================================
# Context Management
# ============================================================================

def set_context(**context) -> None:
    """
    Set thread-local logging context.

    Args:
        **context: Context key-value pairs

    Examples:
        >>> set_context(trade_id='12345', symbol='BTC-USD')
        >>> logger.info('Order processed')  # Includes trade_id and symbol
    """
    current = _log_context.get().copy()
    current.update(context)
    _log_context.set(current)


def clear_context() -> None:
    """Clear thread-local logging context."""
    _log_context.set({})


def get_context() -> Dict[str, Any]:
    """
    Get current thread-local logging context.

    Returns:
        Current context dictionary
    """
    return _log_context.get().copy()


@contextmanager
def log_context(**context):
    """
    Context manager for temporary logging context.

    Args:
        **context: Context key-value pairs to set temporarily

    Examples:
        >>> with log_context(trade_id='12345', symbol='BTC-USD'):
        ...     logger.info('Processing order')  # Includes trade_id and symbol
        >>> logger.info('Done')  # No trade_id or symbol
    """
    # Save current context
    previous = get_context()

    # Set new context
    set_context(**context)

    try:
        yield
    finally:
        # Restore previous context
        _log_context.set(previous)


# ============================================================================
# Performance Tracking Decorators
# ============================================================================

def log_performance(
    logger_name: str,
    level: str = 'INFO',
    include_args: bool = False,
) -> Callable:
    """
    Decorator to log function execution time.

    Args:
        logger_name: Name of logger to use
        level: Log level (default: INFO)
        include_args: Whether to log function arguments

    Examples:
        >>> @log_performance('webhook')
        ... async def process_order(order):
        ...     await asyncio.sleep(1)
        ...     return order

        >>> @log_performance('webhook', level='DEBUG', include_args=True)
        ... def validate_order(order_id, symbol):
        ...     return True
    """
    def decorator(func: Callable) -> Callable:
        logger = get_logger(logger_name)
        log_level = getattr(logging, level.upper(), logging.INFO)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            func_name = func.__name__

            # Log function entry
            if include_args:
                logger.log(
                    log_level,
                    f"Calling {func_name}",
                    extra={'args': str(args), 'kwargs': str(kwargs)}
                )
            else:
                logger.log(log_level, f"Calling {func_name}")

            try:
                result = await func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time

                # Log successful completion
                logger.log(
                    log_level,
                    f"{func_name} completed",
                    extra={'duration_ms': round(elapsed * 1000, 2)}
                )

                return result

            except Exception as e:
                elapsed = time.perf_counter() - start_time
                logger.error(
                    f"{func_name} failed",
                    exc_info=True,
                    extra={'duration_ms': round(elapsed * 1000, 2)}
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            func_name = func.__name__

            # Log function entry
            if include_args:
                logger.log(
                    log_level,
                    f"Calling {func_name}",
                    extra={'args': str(args), 'kwargs': str(kwargs)}
                )
            else:
                logger.log(log_level, f"Calling {func_name}")

            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time

                # Log successful completion
                logger.log(
                    log_level,
                    f"{func_name} completed",
                    extra={'duration_ms': round(elapsed * 1000, 2)}
                )

                return result

            except Exception as e:
                elapsed = time.perf_counter() - start_time
                logger.error(
                    f"{func_name} failed",
                    exc_info=True,
                    extra={'duration_ms': round(elapsed * 1000, 2)}
                )
                raise

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def log_async_performance(logger_name: str, level: str = 'DEBUG') -> Callable:
    """
    Simplified decorator for async function performance logging.

    Args:
        logger_name: Name of logger to use
        level: Log level (default: DEBUG)

    Examples:
        >>> @log_async_performance('webhook')
        ... async def fetch_market_data(symbol):
        ...     await asyncio.sleep(0.5)
        ...     return {'symbol': symbol, 'price': 50000}
    """
    return log_performance(logger_name, level=level, include_args=False)


# ============================================================================
# Backward Compatibility Helpers
# ============================================================================

def setup_structured_logging(
    log_dir: Optional[str] = None,
    console_level: str = 'INFO',
    use_json: Optional[bool] = None,
) -> LoggingConfig:
    """
    Initialize structured logging system.

    Args:
        log_dir: Directory for log files
        console_level: Console log level
        use_json: Force JSON formatting

    Returns:
        LoggingConfig instance

    Examples:
        >>> config = setup_structured_logging(log_dir='logs', console_level='DEBUG')
        >>> logger = get_logger('webhook')
    """
    from Config.logging_config import set_logging_config

    config = LoggingConfig(
        log_dir=log_dir,
        console_level=console_level,
        use_json=use_json,
    )

    set_logging_config(config)
    return config


# ============================================================================
# Module-level exports
# ============================================================================

__all__ = [
    'StructuredLogger',
    'get_logger',
    'get_component_logger',
    'set_context',
    'clear_context',
    'get_context',
    'log_context',
    'log_performance',
    'log_async_performance',
    'setup_structured_logging',
]
