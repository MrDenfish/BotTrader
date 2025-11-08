"""
Structured Logging Configuration for BotTrader

This module provides centralized logging configuration with:
- JSON formatting for production environments
- Colored console output for development
- Size-based log rotation (50MB max)
- Context injection (trade_id, symbol, component)
- Custom log levels for trading operations
"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from Config.environment import get_environment


# Custom log levels for trading operations
TRADE_LOG_LEVELS = {
    'BUY': 21,
    'SELL': 19,
    'ORDER_SENT': 23,
    'TAKE_PROFIT': 17,
    'TAKE_LOSS': 11,
    'STOP_LOSS': 15,
    'INSUFFICIENT_FUNDS': 13,
    'BAD_ORDER': 25,
}

# Register custom log levels
for level_name, level_num in TRADE_LOG_LEVELS.items():
    logging.addLevelName(level_num, level_name)


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging in production.

    Outputs log records as JSON with consistent structure:
    {
        "timestamp": "2025-11-08T10:30:45.123Z",
        "level": "INFO",
        "logger": "webhook",
        "message": "Order processed",
        "context": {
            "trade_id": "12345",
            "symbol": "BTC-USD",
            "component": "webhook"
        },
        "extra": {...},
        "exc_info": "..."
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }

        # Add context if available
        if hasattr(record, 'context'):
            log_data['context'] = record.context

        # Add custom fields from extra
        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'message', 'pathname', 'process', 'processName',
                          'relativeCreated', 'thread', 'threadName', 'exc_info',
                          'exc_text', 'stack_info', 'context']:
                extra_fields[key] = value

        if extra_fields:
            log_data['extra'] = extra_fields

        # Add exception info if present
        if record.exc_info:
            log_data['exc_info'] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class ColoredConsoleFormatter(logging.Formatter):
    """
    Colored console formatter for development environments.
    Uses ANSI color codes for better readability.
    """

    # ANSI color codes
    COLORS = {
        'DEBUG': '\x1b[38;21m',      # Grey
        'INFO': '\x1b[38;21m',       # Grey
        'WARNING': '\x1b[38;5;214m', # Orange
        'ERROR': '\x1b[31;21m',      # Red
        'CRITICAL': '\x1b[31;1m',    # Bold Red
        'BUY': '\x1b[34;21m',        # Blue
        'SELL': '\x1b[32;21m',       # Green
        'ORDER_SENT': '\x1b[33;21m', # Yellow
        'BAD_ORDER': '\x1b[35;21m',  # Magenta
    }
    RESET = '\x1b[0m'

    def __init__(self, include_context: bool = True):
        """
        Initialize formatter.

        Args:
            include_context: Whether to include context fields in output
        """
        self.include_context = include_context
        fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
        super().__init__(fmt, datefmt='%Y-%m-%d %H:%M:%S')

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with color codes."""
        # Add color
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
            record.msg = f"{self.COLORS[levelname]}{record.msg}{self.RESET}"

        # Format base message
        formatted = super().format(record)

        # Restore original levelname
        record.levelname = levelname

        # Add context if available and enabled
        if self.include_context and hasattr(record, 'context') and record.context:
            context_str = ' | '.join(f"{k}={v}" for k, v in record.context.items())
            formatted += f" [{context_str}]"

        return formatted


class LoggingConfig:
    """
    Central logging configuration manager.

    Provides factory methods for creating configured loggers with:
    - Environment-aware formatting (JSON for production, colored for dev)
    - Size-based rotation (50MB default)
    - Consistent log levels and handlers
    """

    # Default settings
    DEFAULT_LOG_DIR = 'logs'
    DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50MB
    DEFAULT_BACKUP_COUNT = 5
    DEFAULT_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'

    def __init__(
        self,
        log_dir: Optional[str] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        console_level: Optional[str] = None,
        file_level: str = 'DEBUG',
        use_json: Optional[bool] = None,
    ):
        """
        Initialize logging configuration.

        Args:
            log_dir: Directory for log files (default: 'logs')
            max_bytes: Max size per log file before rotation (default: 50MB)
            backup_count: Number of backup files to keep (default: 5)
            console_level: Console log level (default: INFO, or DEBUG with --verbose)
            file_level: File log level (default: DEBUG)
            use_json: Force JSON formatting (default: auto-detect from environment)
        """
        self.log_dir = Path(log_dir or self.DEFAULT_LOG_DIR)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.console_level = console_level or os.getenv('LOG_LEVEL', 'INFO')
        self.file_level = file_level

        # Auto-detect environment if not specified
        if use_json is None:
            env = get_environment()
            self.use_json = env in ['production', 'staging', 'docker']
        else:
            self.use_json = use_json

        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def get_console_formatter(self) -> logging.Formatter:
        """Get appropriate console formatter based on environment."""
        if self.use_json:
            return JSONFormatter()
        return ColoredConsoleFormatter(include_context=True)

    def get_file_formatter(self) -> logging.Formatter:
        """Get file formatter (always JSON for structured logs)."""
        return JSONFormatter()

    def create_console_handler(self) -> logging.StreamHandler:
        """Create configured console handler."""
        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, self.console_level.upper()))
        handler.setFormatter(self.get_console_formatter())
        return handler

    def create_file_handler(self, log_file: str) -> RotatingFileHandler:
        """
        Create configured rotating file handler.

        Args:
            log_file: Name of the log file (e.g., 'webhook.log')

        Returns:
            Configured RotatingFileHandler
        """
        log_path = self.log_dir / log_file
        handler = RotatingFileHandler(
            log_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
        )
        handler.setLevel(getattr(logging, self.file_level.upper()))
        handler.setFormatter(self.get_file_formatter())
        return handler

    def configure_logger(
        self,
        logger_name: str,
        log_file: Optional[str] = None,
        component: Optional[str] = None,
    ) -> logging.Logger:
        """
        Configure a logger with console and file handlers.

        Args:
            logger_name: Name of the logger
            log_file: Log file name (default: {logger_name}.log)
            component: Component name for context injection

        Returns:
            Configured logger instance
        """
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)  # Capture all levels, handlers filter

        # Clear existing handlers to avoid duplicates
        if logger.hasHandlers():
            logger.handlers.clear()

        # Add console handler
        logger.addHandler(self.create_console_handler())

        # Add file handler if log_file specified
        if log_file:
            logger.addHandler(self.create_file_handler(log_file))

        # Prevent propagation to root logger
        logger.propagate = False

        return logger

    def setup_sqlalchemy_logging(self, level: str = 'WARNING') -> None:
        """
        Configure SQLAlchemy logging to reduce noise.

        Args:
            level: Log level for SQLAlchemy (default: WARNING)
        """
        sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
        sqlalchemy_logger.setLevel(getattr(logging, level.upper()))

        if sqlalchemy_logger.hasHandlers():
            sqlalchemy_logger.handlers.clear()

        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, level.upper()))
        handler.setFormatter(self.get_console_formatter())
        sqlalchemy_logger.addHandler(handler)

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'LoggingConfig':
        """
        Create LoggingConfig from dictionary.

        Args:
            config: Configuration dictionary

        Returns:
            LoggingConfig instance
        """
        return cls(
            log_dir=config.get('log_dir'),
            max_bytes=config.get('max_bytes', cls.DEFAULT_MAX_BYTES),
            backup_count=config.get('backup_count', cls.DEFAULT_BACKUP_COUNT),
            console_level=config.get('console_level'),
            file_level=config.get('file_level', 'DEBUG'),
            use_json=config.get('use_json'),
        )


# Singleton instance for easy access
_default_config: Optional[LoggingConfig] = None


def get_logging_config() -> LoggingConfig:
    """Get or create default logging configuration."""
    global _default_config
    if _default_config is None:
        _default_config = LoggingConfig()
    return _default_config


def set_logging_config(config: LoggingConfig) -> None:
    """Set default logging configuration."""
    global _default_config
    _default_config = config
