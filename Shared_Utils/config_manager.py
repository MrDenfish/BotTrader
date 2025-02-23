import os
import json
from coinbase.rest import RESTClient
from dotenv import load_dotenv
from decimal import Decimal


class CentralConfig:
    """Centralized configuration manager shared across all modules."""
    _instance = None # Singleton instance
    _is_loaded = False

    def __new__(cls):
        if cls._instance is None:
            print("Creating Config Manager instance")
            cls._instance = super(CentralConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if  not self._is_loaded:
            self._initialize_default_values()
            self._load_configuration()
            self.initialize_rest_client()

    def _initialize_default_values(self):
        """Set default values for all configuration attributes."""
        self.db_url = self.db_user = self.db_password = self.db_host = None
        self.db_port = self._api_url = self._json_config = None
        self._phone = self._email = self._e_mailpass = self._my_email = None
        self._order_size = self._version = self._max_ohlcv_rows = self._async_mode = None
        self._bb_window = self._bb_std = self._bb_lower_band = self._bb_upper_band = None
        self._macd_fast = self._macd_slow = self._macd_signal = None
        self._rsi_window = self._atr_window = self._rsi_buy = None
        self._rsi_sell = self._sma_fast = self._sma_slow = self._sma = None
        self._buy_ratio = self._sell_ratio = self._sma_volatility = self._hodl = None
        self._cxl_buy = self._cxl_sell = self._take_profit = self._roc_24hr = None
        self._stop_loss = self._csv_dir = self._web_url = self._sleep_time = None
        self._docker_staticip = self._tv_whitelist = self._coin_whitelist = None
        self._taker_fee = self._maker_fee = self._trailing_stop = None
        self._trailing_limit = self._db_pool_size = self._db_max_overflow = None
        self._sighook_api_key_path = self._websocket_api_key_path = None
        self._webhook_api_key_path = self._api_key = self._api_secret = None
        self._passphrase = self._currency_pairs_ignored = self._log_level = None
        self._assets_ignored = self._buy_target = self._sell_target = None
        self._quote_currency = self._trailing_percentage = self._min_volume = None


        # Default values
        self._json_config = {}
        self._log_level = "INFO"
        self._trailing_percentage = Decimal("0.5")
        self._min_sell_value = Decimal("0.01")
        self._currency_pairs_ignored = []
        self.is_docker = os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true"

    def _load_configuration(self):
        """Load configuration from environment variables and JSON files."""
        self._initialize_default_values()
        self.load_dotenv_settings()
        self.machine_type, self.webhook_port, self.sighook_port = self.determine_machine_type()
        self._load_environment_variables()
        self._load_json_config()  # Ensure paths like _sighook_api_key_path are set
        self._generate_database_url()
        self._is_loaded = True  # Mark the configuration as loaded

    @staticmethod
    def load_dotenv_settings():
        env_path = ".env" if os.getenv('RUNNING_IN_DOCKER') else os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                                                              '.env_tradebot')
        load_dotenv(env_path)

    def _load_environment_variables(self):
        env_vars = {
            "db_host": "DB_HOST",
            "db_port": "DB_PORT",
            "db_name": "DB_NAME",
            "db_user": "DB_USER",
            "db_password": "DB_PASSWORD",
            "_log_level": "LOG_LEVEL",
            "_async_mode": "ASYNC_MODE",
            "_quote_currency": "QUOTE_CURRENCY",
            "_order_size": "ORDER_SIZE",
            "_trailing_percentage": "TRAILING_PERCENTAGE",
            "_min_sell_value": "MIN_SELL_VALUE",
            "_min_volume": "MIN_VOLUME",
            "_max_ohlcv_rows": "MAX_OHLCV_ROWS",
            "_hodl": "HODL",
            "_buy_target": "BUY_TARGET",
            "_sell_target": "SELL_TARGET",
            "_take_profit": "TAKE_PROFIT",
            "_stop_loss": "STOP_LOSS",
            "_cxl_buy": "CXL_BUY",
            "_cxl_sell": "CXL_SELL",
            "_roc_24hr": "ROC_24HR",
            "_docker_staticip": "DOCKER_STATICIP",
            "_tv_whitelist": "TV_WHITELIST",
            "_coin_whitelist": "COIN_WHITELIST",
            "_version": "VERSION",
            "_bb_window": "BB_WINDOW",
            "_bb_std": "BB_STD",
            "_bb_lower_band": "BB_LOWER_BAND",
            "_bb_upper_band": "BB_UPPER_BAND",
            "_macd_fast": "MACD_FAST",
            "_macd_slow": "MACD_SLOW",
            "_macd_signal": "MACD_SIGNAL",
            "_rsi_window": "RSI_WINDOW",
            "_atr_window": "ATR_WINDOW",
            "_rsi_buy": "RSI_OVERSOLD",
            "_rsi_sell": "RSI_OVERBOUGHT",
            "_buy_ratio": "BUY_RATIO",
            "_sell_ratio": "SELL_RATIO",
            "_sma_fast": "SMA_FAST",
            "_sma_slow": "SMA_SLOW",
            "_sma": "SMA",
            "_sma_volatility": "SMA_VOLATILITY",
            "_api_url": "API_URL",
            "_pagekite_whitelist": "PAGEKITE_WHITELIST",
            "_taker_fee": "TAKER_FEE",
            "_maker_fee": "MAKER_FEE",
            "_trailing_stop": "TRAILING_STOP",
            "_trailing_limit": "TRAILING_LIMIT",
            "_api_key": "API_KEY",
            "_api_secret": "API_SECRET",
            "_passphrase": "PASSPHRASE",
            "_currency_pairs_ignored": "CURRENCY_PAIRS_IGNORED",
            "_assets_ignored": "ASSETS_IGNORED",
            "_sleep_time": 'SLEEP',
            "_web_url": 'WEB_URL',
        }

        for attr, env_var in env_vars.items():
            value = os.getenv(env_var)
            if value is not None:
                setattr(self, attr, Decimal(value) if attr.startswith("_") and "percentage" in attr.lower() else value)


    def _load_json_config(self):
        """Load and merge JSON configuration files from Shared_Utils."""
        try:
            shared_utils_dir = os.path.dirname(os.path.realpath(__file__))
            config_files = [
                "webhook_config.json", "webhook_tb_api_key.json",
                "webhook_api_key.json", "websocket_api_info.json",
                "sighook_config.json", "sighook_api_key.json"
            ]
            for file_name in config_files:
                config_path = os.path.join(shared_utils_dir, file_name)
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        self._merge_config_data(json.load(f))

            # Ensure machine type exists in configuration
            if self.machine_type not in self._json_config:  # Fix is here
                raise ValueError(f"Machine type '{self.machine_type}' not found in JSON configurations.")

            # Assign machine-specific directories
            machine_config = self._json_config[self.machine_type]  # Fix is here
            self._csv_dir = machine_config.get("CHUNKS_DIR")
            self.database_dir = machine_config.get("DATABASE_DIR", "data")
            self.database_file = machine_config.get("DATABASE_FILE", "trades.db")

            # Initialize the API key path
            self._sighook_api_key_path = os.path.join(shared_utils_dir, "sighook_api_key.json")
            self._webhook_api_key_path = os.path.join(shared_utils_dir, 'webhook_api_key.json')
            self._tb_api_key_path = os.path.join(shared_utils_dir, 'tb_api_key.json')
            self._websocket_api_key_path = os.path.join(shared_utils_dir, 'websocket_api_info.json')
            self.websocket_api = self.load_websocket_api_key()

            print(f"Configuration loaded successfully for machine type '{self.machine_type}'.")
        except Exception as e:
            print(f"Error loading JSON configuration: {e}")
            exit(1)

    def _merge_config_data(self, new_config):
        """Merge new configuration data into the existing configuration."""
        for key, value in new_config.items():
            if key in self._json_config:
                if isinstance(self._json_config[key], dict) and isinstance(value, dict):
                    self._json_config[key].update(value)  # Merge nested dictionaries
                else:
                    self._json_config[key] = value  # Overwrite existing value
            else:
                self._json_config[key] = value

    def _generate_database_url(self):
        """Generate the database URL."""
        try:
            self.db_url = f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
            print(f"Configured PostgreSQL database at: {self.db_url}")
        except Exception as e:
            print(f"Error configuring database URL: {e}")

    def load_sighook_api_key(self):
        """Load the Sighook API key from a JSON file."""
        try:
            with open(self._sighook_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading Sighook API key JSON: {e}")
            return None

    def get_whitelist(self):
        """
        Combine all whitelist information into one list, ensuring strings are split into lists.
        """
        # Convert each whitelist string to a list, splitting by ','.
        tv_whitelist_list = self._tv_whitelist.split(',') if isinstance(self._tv_whitelist, str) else self._tv_whitelist
        coin_whitelist_list = self._coin_whitelist.split(',') if isinstance(self._coin_whitelist,
                                                                            str) else self._coin_whitelist
        docker_staticip_list = self._docker_staticip.split(',') if isinstance(self._docker_staticip,
                                                                              str) else self._docker_staticip


        # Combine all whitelist information into a single list, excluding empty values
        whitelist = tv_whitelist_list + coin_whitelist_list + docker_staticip_list
        return [item for item in whitelist if item]  # Exclude empty strings or None values

    def initialize_rest_client(self):
        """
        Initialize the REST client with the API key, secret, and UUID.
        """
        try:
            api_key = self.load_webhook_api_key().get('name')
            api_secret = self.load_webhook_api_key().get('privateKey')
            portfolio_uuid = self.load_webhook_api_key().get('uuid')
            if not api_key or not api_secret or not portfolio_uuid:
                raise ValueError("API key, secret, and UUID are required to initialize the REST client.")

            self.rest_client = RESTClient(api_key=api_key, api_secret=api_secret)
            self.portfolio_uuid = portfolio_uuid
            print("REST client successfully initialized.")
        except Exception as e:
            print(f"Error initializing REST client: {e}")
            raise

    def load_channels(self):
        """Load and return the WebSocket API channels as a list of channel names."""
        try:
            websocket_config = self.websocket_api  # Fetch the websocket config
            return list(websocket_config.get("channel_names", {}).values())
        except Exception as e:
            print(f"Error loading channels: {e}")
            return []

    def get_database_dir(self):
        # Always use config.json for database dir
        base_dir = self._json_config.get(self.machine_type, {}).get('BASE_DIR', '.')
        database_dir = self._json_config[self.machine_type].get('DATABASE_DIR', 'data')
        return os.path.join(base_dir, database_dir)

    def load_webhook_api_key(self):
        try:
            with open(self._webhook_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading webhook CDP API key JSON: {e}")
            exit(1)

    def load_websocket_api_key(self):
        try:
            with open(self._websocket_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading websocket CDP API key JSON: {e}")
            exit(1)

    def reload_config(self):
        # Force reload of configuration
        self._is_loaded = False
        self.__init__()

    # def get_directory_paths(self, path):
    #     base_dir = self.get_database_dir()  # Always use the database directory handler

    @staticmethod
    def determine_machine_type() -> tuple:
        cwd_parts = os.getcwd().split('/')
        if 'app' in cwd_parts:
            return 'docker', os.getenv('SIGHOOK_PORT', '8000')
        elif len(cwd_parts) > 2:
            webhook_port = int(os.getenv('WEBHOOK_PORT', 80))
            sighook_port = int(os.getenv('SIGHOOK_PORT', 5000))
            return cwd_parts[2], webhook_port, sighook_port
        else:
            raise ValueError(f"Invalid path {os.getcwd()}, unable to determine machine type.")

    def load_tb_api_key(self):
        try:
            with open(self._tb_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading tb CDP API key JSON: {e}")
            exit(1)
    @property
    def buy_target(self):
        return self._buy_target

    @property
    def sell_target(self):
        return self._sell_target

    @property
    def webhook_api_key_path(self):
        return self._webhook_api_key_path

    @property
    def docker_staticip(self):
        return self._docker_staticip

    @property
    def tv_whitelist(self):
        return self._tv_whitelist

    #
    @property
    def coin_whitelist(self):
        return self._coin_whitelist

    @property
    def json_config(self):
        return self._json_config

    #
    @property
    def sighook_api_key_path(self):
        return self._sighook_api_key_path

    @property
    def db_pool_size(self):
        return self._db_pool_size

    @property
    def db_max_overflow(self):
        return self._db_max_overflow

    @property
    def log_level(self):
        return self._log_level

    @property
    def async_mode(self):
        return self._async_mode

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    @property
    def passphrase(self):
        return self._passphrase

    @property
    def hodl(self):
        return self._hodl

    @property
    def currency_pairs_ignored(self):
        return self._currency_pairs_ignored

    @property
    def assets_ignored(self):
        return self._assets_ignored

    @property
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def quote_currency(self):
        return self._quote_currency

    @property
    def trailing_percentage(self):
        return self._trailing_percentage


    @property
    def min_volume(self):
        return self._min_volume

    @property
    def database_url(self):
        return self.db_url

    @property
    def api_url(self):
        return self._api_url

    @property
    def trailing_stop(self):
        return self._trailing_stop

    @property
    def trailing_limit(self):
        return self._trailing_limit

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def maker_fee(self):
        return self._maker_fee

    @property
    def phone(self):
        return self._phone

    @property
    def email(self):
        return self._email

    @property
    def e_mailpass(self):
        return self._e_mailpass

    @property
    def my_email(self):
        return self._my_email

    @property
    def order_size(self):
        return self._order_size

    @property
    def program_version(self):
        return self._version
#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    @property
    def bb_window(self):
        return self._bb_window

    @property
    def bb_std(self):
        return self._bb_std
    @property
    def bb_lower_band(self):
        return self._bb_lower_band

    @property
    def bb_upper_band(self):
        return self._bb_upper_band

    @property
    def macd_fast(self):
        return self._macd_fast

    @property
    def macd_slow(self):
        return self._macd_slow

    @property
    def macd_signal(self):
        return self._macd_signal

    @property
    def buy_ratio(self):
        return self._buy_ratio

    @property
    def sell_ratio(self):
        return self._sell_ratio

    @property
    def rsi_window(self):
        return self._rsi_window

    @property
    def atr_window(self):
        return self._atr_window

    @property
    def rsi_buy(self):
        return self._rsi_buy

    @property
    def rsi_sell(self):
        return self._rsi_sell

    @property
    def sma_fast(self):
        return self._sma_fast

    @property
    def sma_slow(self):
        return self._sma_slow

    @property
    def sma(self):
        return self._sma

    @property
    def sma_volatility(self):
        return self._sma_volatility

    @property
    def max_ohlcv_rows(self):
        return self._max_ohlcv_rows

    @property
    def cxl_buy(self):
        return self._cxl_buy

    @property
    def cxl_sell(self):
        return self._cxl_sell

    @property
    def roc_24hr(self):
        return int(self._roc_24hr)

    @property
    def csv_dir(self):
        return self._csv_dir

    @property
    def is_loaded(self):
        return self._is_loaded

    @property
    def web_url(self):
        return self._web_url

    @property
    def sleep_time(self):
        return self._sleep_time



