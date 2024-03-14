import logging
import os
from logging.handlers import TimedRotatingFileHandler
import platform
from datetime import datetime


class CustomLogger(logging.Logger):
    # Define custom logging levels
    BUY_LEVEL_NUM = 21
    SELL_LEVEL_NUM = 19
    PROFIT_LEVEL_NUM = 17
    STOP_LOSS_LEVEL_NUM = 15

    logging.addLevelName(BUY_LEVEL_NUM, "BUY")
    logging.addLevelName(SELL_LEVEL_NUM, "SELL")

    def sell(self, message, *args, **kwargs):
        if self.isEnabledFor(self.SELL_LEVEL_NUM):
            self._log(self.SELL_LEVEL_NUM, f"SELL: {message}", args, **kwargs)

    def take_profit(self, message, *args, **kwargs):
        if self.isEnabledFor(self.PROFIT_LEVEL_NUM):
            self._log(logging.INFO, f"TAKE_PROFIT: {message}", args, **kwargs)

    def stop_loss(self, message, *args, **kwargs):
        if self.isEnabledFor(self.STOP_LOSS_LEVEL_NUM):
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
        CustomLogger.SELL_LEVEL_NUM: green + format + reset,
        CustomLogger.PROFIT_LEVEL_NUM: green + format + reset,
        CustomLogger.STOP_LOSS_LEVEL_NUM: red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.format)
        formatter = logging.Formatter(log_fmt, "%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


class LoggerManager:
    """ This class handles the logging of errors and messages.  It is used by the other classes to log errors and
    messages to the console and to log files."""

    _instance = None
    _is_initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, config, log_dir=None):
        if not self._is_initialized:
            self._log_level = config.log_level
            self.sighook_logger = None
            self.log_dir = log_dir if log_dir else os.getenv('SENDER_ERROR_LOG_DIR', 'logs')
            self.setup_logging()
            self._is_initialized = True

    @property
    def log_level(self):
        return self._log_level

    def setup_logging(self):
        """ This method sets up the logging for the TradeBot.  It creates the log directory if it does not exist and
        creates the log files.  It also sets up the logging for the Flask server."""
        # Ensure CustomLogger is set as the logger class
        logging.setLoggerClass(CustomLogger)

        current_date = datetime.now().strftime('%Y-%m-%d')
        current_platform = platform.system()

        sighook_log_dir = os.path.join(self.log_dir)
        if not os.path.exists(sighook_log_dir):
            os.makedirs(sighook_log_dir)

        sighook_log_filename = f"sighook.log.{current_platform}.{current_date}"
        log_file_path = os.path.join(sighook_log_dir, sighook_log_filename)
        constant_log_file_path = os.path.join(sighook_log_dir, "sighook.log")

        self.sighook_logger = self.get_logger('sighook_logger', log_file_path, constant_log_file_path)

    def get_logger(self, name, log_file_path, constant_log_file_path):

        logger = logging.getLogger(name)
        logger.setLevel(self.log_level)

        if not logger.handlers:
            # Handler for console output
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(CustomFormatter())
            console_handler.setLevel(self.log_level)  # Use the dynamic log level
            logger.addHandler(console_handler)

            # File handler for rotating logs
            timed_file_handler = TimedRotatingFileHandler(log_file_path, when="midnight", interval=1, backupCount=2)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)")
            timed_file_handler.setFormatter(file_formatter)
            timed_file_handler.setLevel(self.log_level)  # Use the dynamic log level
            logger.addHandler(timed_file_handler)

            # File handler for constant log file
            constant_file_handler = logging.FileHandler(constant_log_file_path)
            constant_file_handler.setFormatter(file_formatter)
            constant_file_handler.setLevel(self.log_level)  # Use the dynamic log level
            logger.addHandler(constant_file_handler)

        return logger
