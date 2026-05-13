import logging
import os
from logging.handlers import TimedRotatingFileHandler


class CustomLogger(logging.Logger):
    # Define custom logging levels
    BUY_LEVEL_NUM = 21
    SELL_LEVEL_NUM = 19
    ORDER_SENT_NUM = 23
    PROFIT_LEVEL_NUM = 17
    LOSS_LEVEL_NUM = 11
    STOP_LOSS_LEVEL_NUM = 15
    INSUFFICIENT_FUNDS = 13
    BAD_ORDER_NUM = 25

    logging.addLevelName(BUY_LEVEL_NUM, "BUY")
    logging.addLevelName(SELL_LEVEL_NUM, "SELL")
    logging.addLevelName(ORDER_SENT_NUM, "ORDER_SENT")
    logging.addLevelName(PROFIT_LEVEL_NUM, "TAKE_PROFIT")
    logging.addLevelName(LOSS_LEVEL_NUM, "TAKE_LOSS")
    logging.addLevelName(STOP_LOSS_LEVEL_NUM, "STOP_LOSS")
    logging.addLevelName(BAD_ORDER_NUM, "BAD_ORDER")
    logging.addLevelName(INSUFFICIENT_FUNDS, "INSUFFICIENT_FUNDS")

    def sell(self, message, *args, **kwargs):
        if self.isEnabledFor(self.SELL_LEVEL_NUM):
            self._log(self.SELL_LEVEL_NUM, f"SELL: {message}", args, **kwargs)

    def take_profit(self, message, *args, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, f"TAKE_PROFIT: {message}", args, **kwargs)

    def bad_order(self, message, *args, **kwargs):
        if self.isEnabledFor(self.BAD_ORDER_NUM):
            self._log(self.BAD_ORDER_NUM, f"BAD_ORDER: {message}", args, **kwargs)

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

    def order_sent(self, message, *args, **kwargs):
        if self.isEnabledFor(self.ORDER_SENT_NUM):
            self._log(self.ORDER_SENT_NUM, f"ORDER_SENT: {message}", args, **kwargs)


logging.setLoggerClass(CustomLogger)


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;21m"
    blue = "\x1b[34;21m"
    green = "\x1b[32;21m"
    yellow = "\x1b[33;21m"
    red = "\x1b[31;21m"
    magenta = "\x1b[35;21m"
    orange = "\x1b[38;5;214m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: grey + format + reset,
        logging.WARNING: orange + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset,
        CustomLogger.BUY_LEVEL_NUM: blue + format + reset,
        CustomLogger.ORDER_SENT_NUM: yellow + format + reset,
        CustomLogger.SELL_LEVEL_NUM: green + format + reset,
        CustomLogger.BAD_ORDER_NUM: magenta + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.format)
        formatter = logging.Formatter(log_fmt, "%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


class LoggerManager:
    _instance = None
    _is_initialized = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            print("Creating Logging instance")
            cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, config, log_dir=None):
        if not self._is_initialized:
            self._log_level = config.get('log_level', logging.INFO)
            self.log_dir = log_dir or "logs"
            self.loggers = {}
            self.setup_logging()
            self._is_initialized = True

    @property
    def log_level(self):
        return self._log_level

    def setup_logging(self):
        self.setup_logger('webhook_logger', 'webhook')
        self.setup_logger('sighook_logger', 'sighook')
        self.setup_logger('shared_logger', 'shared')

    def setup_logger(self, logger_name, subfolder):
        log_path = os.path.join(self.log_dir, subfolder)
        os.makedirs(log_path, exist_ok=True)

        log_file = os.path.join(log_path, f"{logger_name}.log")
        logger = CustomLogger(logger_name)
        logger.setLevel(logging.DEBUG)  # File will always capture everything

        if logger.hasHandlers():
            logger.handlers.clear()

        # ✅ Console Handler → Only shows INFO+ by default (DEBUG only if --verbose)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self._log_level)  # INFO normally, DEBUG with --verbose
        console_handler.setFormatter(CustomFormatter())
        logger.addHandler(console_handler)

        # ✅ File Handler → Always keep full DEBUG logs for postmortem analysis
        file_handler = TimedRotatingFileHandler(
            log_file, when="midnight", interval=1, backupCount=2
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        self.loggers[logger_name] = logger
        self.setup_sqlalchemy_logging(logging.WARNING)

    def get_logger(self, logger_name):
        return self.loggers.get(logger_name)

    @staticmethod
    def setup_sqlalchemy_logging(level=logging.WARNING):
        sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
        sqlalchemy_logger.setLevel(level)

        if sqlalchemy_logger.hasHandlers():
            sqlalchemy_logger.handlers.clear()

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(CustomFormatter())
        console_handler.setLevel(level)
        sqlalchemy_logger.addHandler(console_handler)

    @staticmethod
    def log_method_call(func):
        async def wrapper(*args, **kwargs):
            logger = logging.getLogger('webhook_logger')
            print(f"🪲 Calling {func.__name__} with args {args} and kwargs {kwargs}🪲 ") #DEBUG
            result = await func(*args, **kwargs)
            logger.debug(f"{func.__name__} returned {result}")
            return result
        return wrapper
