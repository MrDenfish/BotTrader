import logging
import os
from logging.handlers import TimedRotatingFileHandler
import platform
from datetime import datetime


class CustomLogger(logging.Logger):
    # Define custom logging levels
    BUY_LEVEL_NUM = 21
    SELL_LEVEL_NUM = 19

    logging.addLevelName(BUY_LEVEL_NUM, "BUY")
    logging.addLevelName(SELL_LEVEL_NUM, "SELL")

    def sell(self, message, *args, **kwargs):
        if self.isEnabledFor(self.SELL_LEVEL_NUM):
            self._log(self.SELL_LEVEL_NUM, f"SELL: {message}", args, **kwargs)

    def take_profit(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"TAKE_PROFIT: {message}", args, **kwargs)

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
    """ This class handles the logging of errors and messages.  It is used by the other classes to log errors and
    messages to the console and to log files."""
    _instance_count = 0
    _instance = None
    _is_initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, log_dir=None):
        # self.id = LoggerManager._instance_count
        # LoggerManager._instance_count += 1
        # print(f"LoggerManager Instance ID: {self.id}")

        if not self._is_initialized:
            self.webhook_logger = None
            self.flask_logger = None
            self.log_dir = log_dir if log_dir else os.getenv('WEBHOOK_ERROR_LOG_DIR', 'logs')
            self.setup_logging()
            self._is_initialized = True

    def setup_logging(self):
        """ This method sets up the logging for the TradeBot.  It creates the log directory if it does not exist and
        creates the log files.  It also sets up the logging for the Flask server."""

        logging.setLoggerClass(CustomLogger)

        current_date = datetime.now().strftime('%Y-%m-%d')
        current_platform = platform.system()

        # Directories for listener and Flask logs
        webhook_log_dir = os.path.join(self.log_dir, 'listener_logs')
        flask_log_dir = os.path.join(self.log_dir, 'flask_logs')

        # Create directories if they don't exist
        for directory in [webhook_log_dir, flask_log_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)

        # Listener log setup
        listener_log_filename = f"webhook.log.{current_platform}.{current_date}"
        listener_log_file_path = os.path.join(webhook_log_dir, listener_log_filename)
        listener_constant_log_file_path = os.path.join(webhook_log_dir, "webhook.log")
        self.webhook_logger = self.get_logger('webhook_logger', listener_log_file_path, listener_constant_log_file_path)

        # Flask log setup
        flask_log_filename = f"flask.log.{current_platform}.{current_date}"
        flask_log_file_path = os.path.join(flask_log_dir, flask_log_filename)
        flask_constant_log_file_path = os.path.join(flask_log_dir, "flask.log")
        self.flask_logger = self.get_logger('flask_logger', flask_log_file_path, flask_constant_log_file_path)

    @staticmethod
    def get_logger(name, log_file_path, constant_log_file_path):
        log_level_str = os.getenv('LOG_LEVEL', 'INFO')
        log_level = getattr(logging, log_level_str.upper(), logging.INFO)

        logger = logging.getLogger(name)
        logger.setLevel(log_level)

        if not logger.handlers:
            # Handler for console output
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(CustomFormatter())
            console_handler.setLevel(log_level)  # Use the dynamic log level
            logger.addHandler(console_handler)

            # File handler for rotating logs
            timed_file_handler = TimedRotatingFileHandler(log_file_path, when="midnight", interval=1, backupCount=2)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)")
            timed_file_handler.setFormatter(file_formatter)
            timed_file_handler.setLevel(log_level)  # Use the dynamic log level
            logger.addHandler(timed_file_handler)

            # File handler for constant log file
            constant_file_handler = logging.FileHandler(constant_log_file_path)
            constant_file_handler.setFormatter(file_formatter)
            constant_file_handler.setLevel(log_level)  # Use the dynamic log level
            logger.addHandler(constant_file_handler)

        return logger

    @staticmethod
    def log_method_call(func):
        def wrapper(*args, **kwargs):
            logger = logging.getLogger('webhook_logger')
            logger.debug(f"Calling {func.__name__} with args {args} and kwargs {kwargs}")
            result = func(*args, **kwargs)
            logger.debug(f"{func.__name__} returned {result}")
            return result

        return wrapper


