import os
import logging
from logging.handlers import TimedRotatingFileHandler
import platform
from datetime import datetime


class CustomLogger(logging.Logger):
    # Define custom logging levels
    BUY_LEVEL_NUM = 21
    SELL_LEVEL_NUM = 19
    PROFIT_LEVEL_NUM = 17
    LOSS_LEVEL_NUM = 16
    STOP_LOSS_LEVEL_NUM = 15
    INSUFFICIENT_FUNDS = 13

    logging.addLevelName(BUY_LEVEL_NUM, "BUY")
    logging.addLevelName(SELL_LEVEL_NUM, "SELL")
    logging.addLevelName(PROFIT_LEVEL_NUM, "TAKE_PROFIT")
    logging.addLevelName(LOSS_LEVEL_NUM, "TAKE_LOSS")
    logging.addLevelName(STOP_LOSS_LEVEL_NUM, "STOP_LOSS")
    logging.addLevelName(INSUFFICIENT_FUNDS, "INSUFFICIENT_FUNDS")

    def sell(self, message, *args, **kwargs):
        if self.isEnabledFor(self.SELL_LEVEL_NUM):
            self._log(self.SELL_LEVEL_NUM, f"SELL: {message}", args, **kwargs)

    def take_profit(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"TAKE_PROFIT: {message}", args, **kwargs)

    def insufficient_funds(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"INSUFFICIENT_FUNDS: {message}", args, **kwargs)

    def take_loss(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"TAKE_LOSS: {message}", args, **kwargs)

    def stop_loss(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"STOP_LOSS: {message}", args, **kwargs)

    def buy(self, message, *args, **kwargs):
        if self.isEnabledFor(self.BUY_LEVEL_NUM):
            self._log(self.BUY_LEVEL_NUM, f"BUY: {message}", args, **kwargs)

logging.setLoggerClass(CustomLogger)

class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;21m"
    blue = "\x1b[34;21m"
    green = "\x1b[32;21m"
    yellow = "\x1b[33;21m"
    red = "\x1b[31;21m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: grey + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset,
        CustomLogger.BUY_LEVEL_NUM: blue + format + reset,
        CustomLogger.SELL_LEVEL_NUM: green + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.format)
        formatter = logging.Formatter(log_fmt, "%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

class LoggerManager:
    """ Shared logging manager that supports multiple log directories. """

    _instance = None
    _is_initialized = False

    # singleton pattern
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, config, log_dir=None):
        if not self._is_initialized:
            self._log_level = config.get('log_level', logging.INFO)
            self.log_dir = log_dir if log_dir else "logs"
            self.loggers = {}
            self.setup_logging()
            self._is_initialized = True

    @property
    def log_level(self):
        return self._log_level

    def setup_logging(self):
        """Setup logging for both 'webhook_logger' and 'sighook_logger'."""
        self.setup_logger('webhook_logger', 'logs/listener_logs')
        self.setup_logger('sighook_logger', 'logs/signal_logs')

    def setup_logger(self, logger_name, log_subdir):
        """Setup individual logger with TimedRotatingFileHandler."""
        current_date = datetime.now().strftime('%Y-%m-%d')
        current_platform = platform.system()

        # Construct log directory
        log_path = os.path.join(self.log_dir, log_subdir)
        if not os.path.exists(log_path):
            os.makedirs(log_path)

        # File paths for rotating logs
        log_file_path = os.path.join(log_path, f"{logger_name}_{current_platform}_{current_date}.log")
        constant_log_file_path = os.path.join(log_path, f"{logger_name}.log")

        logger = logging.getLogger(logger_name)
        logger.setLevel(self._log_level)

        if not logger.handlers:
            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(CustomFormatter())
            logger.addHandler(console_handler)

            # File handler (rotates logs every midnight and keeps 2 days of logs)
            timed_file_handler = TimedRotatingFileHandler(
                log_file_path, when="midnight", interval=1, backupCount=2  # Keeps logs for 2 days
            )
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)")
            timed_file_handler.setFormatter(file_formatter)
            logger.addHandler(timed_file_handler)

        self.loggers[logger_name] = logger
        # Set up SQLAlchemy logging (if needed)
        self.setup_sqlalchemy_logging(logging.WARNING)



    def get_logger(self, logger_name):
        """Return the requested logger instance."""
        return self.loggers.get(logger_name, None)

    @staticmethod
    def setup_sqlalchemy_logging(level=logging.WARNING):
        """ Configure SQLAlchemy logging. """
        sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
        sqlalchemy_logger.setLevel(level)

        # Add a console handler or any other handler if needed
        if not sqlalchemy_logger.hasHandlers():
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(CustomFormatter())
            console_handler.setLevel(level)
            sqlalchemy_logger.addHandler(console_handler)

    @staticmethod
    def log_method_call(func):
        async def wrapper(*args, **kwargs):
            logger = logging.getLogger('webhook_logger')
            logger.debug(f"Calling {func.__name__} with args {args} and kwargs {kwargs}")
            result = await func(*args, **kwargs)
            logger.debug(f"{func.__name__} returned {result}")
            return result

        return wrapper
