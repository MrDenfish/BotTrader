import json
import os
from decimal import Decimal
from dotenv import load_dotenv
from urllib.parse import urljoin
from typing import Any, Optional
from coinbase import rest as coinbase
from pandas.core.methods.describe import select_describe_func
from Shared_Utils.runtime_env import running_in_docker as running_in_docker
from Shared_Utils.url_helper import build_asyncpg_url_from_env

class CentralConfig:
    """Centralized configuration manager shared across all modules."""
    _instance = None  # Singleton instance
    _is_loaded = False

    def __new__(cls, is_docker=None):
        if cls._instance is None:
            print("Creating Config Manager instance")
            cls._instance = super(CentralConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self, is_docker=None):
        if not self._is_loaded:
            if is_docker is None:
                machine_type, webhook_port = self.determine_machine_type()
                is_docker = (machine_type == "docker")

            self.is_docker = is_docker
            self._initialize_default_values()
            self._load_configuration()
            self.initialize_rest_client()
            self._is_loaded = True  # ✅ Mark it done here
            self.test_mode = False  # ✅ Default to False for production

    def _initialize_default_values(self):
        """Set default values for all configuration attributes."""
        self.db_url = self.db_user = self.db_password = self.db_host = None
        self.db_port = self.db_name = self._api_url = self._json_config = None
        self._phone = self._report_sender = self._smtp_password = self._report_recipients = self._email_alerts = None
        self._order_size_fiat = self._version = self._max_ohlcv_rows = self._async_mode = None
        self._bb_window = self._bb_std = self._bb_lower_band = self._bb_upper_band = None
        self._macd_fast = self._macd_slow = self._macd_signal = None
        self._rsi_window = self._atr_window = self._rsi_buy = self._max_value_of_crypto_to_buy_more = None
        self._rsi_sell = self._sma_fast = self._sma_slow = self._sma = None
        self._buy_ratio = self._sell_ratio = self._sma_volatility = self._hodl = None
        self._cxl_buy = self._cxl_sell = self._take_profit = self._shill_coins = None
        self._stop_loss = self._csv_dir = self._pc_url = self._docker_url = self._sleep_time = None
        self._docker_staticip = self._tv_whitelist = self._coin_whitelist = None
        self._trailing_stop = self._min_sell_value = self._min_buy_value = None
        self._trailing_limit = self._db_pool_size = self._db_max_overflow = None
        self._sighook_api_key_path = self._websocket_api_key_path = self._swing_window = None
        self._webhook_api_key_path = self._api_key = self._api_secret = None
        self._passphrase = self._currency_pairs_ignored = self._log_level = None
        self._assets_ignored = self._buy_target = self._sell_target = self._min_value_to_monitor = None
        self._quote_currency = self._trailing_percentage = self._min_quote_volume = self._min_cooldown = None
        self._roc_5min = self._roc_buy_24h = self._roc_sell_24h = self._roc_window = None
        self._min_spread_pct = self._maker_fee = self._taker_fee = self._min_order_amount_fiat = None
        self._edge_buffer_pct = self._max_lifetime = self._inventory_bias_factor = self._spread_to_fee_min =None
        self._tp_min_ticks = self._sl_limit_offset_ticks = self._score_buy_target = self._score_sell_target =  None
        self._allow_buys_on_red_day = self._flip_hysteresis_pct = self._cooldown_bars = self._enrich_limit =  None
        self._sl_limit_offset_ticks = self._min_l1_notional_usd = self._pre_bracket_sigma_ratio = None
        self._aws_region = self._score_jsonl_path = self._tp_sl_log_path = self._report_lookback_minutes = None
        self.exchange: Optional[Any] = None

        # Default values
        self._json_config = {}
        self._log_level = "INFO"
        self._currency_pairs_ignored = []
        self.is_docker = os.getenv("IN_DOCKER", "false").lower() == "true"

    def _load_configuration(self):
        """Load configuration from environment variables and JSON files."""
        self.load_dotenv_settings()
        self.machine_type, self.webhook_port = self.determine_machine_type()  # self.sighook_port
        self._load_environment_variables()
        self._load_json_config()  # Ensure paths like _sighook_api_key_path are set

        self._generate_database_url()
        self._is_loaded = True  # Mark the configuration as loaded

    @staticmethod
    def load_dotenv_settings():
        from pathlib import Path
        if os.getenv("IN_DOCKER", "false").lower() == "true":
            # Don’t load local .env inside containers
            return
        env_path = Path(__file__).resolve().parent.parent / '.env_tradebot'
        print(f"🔹 Loading local .env from {env_path}")
        load_dotenv(dotenv_path=env_path)

    def _load_environment_variables(self):
        env_vars = {
            "_in_docker": "IN_DOCKER",
            "_aws_region": "AWS_REGION",
            "db_host": "DB_HOST",
            "db_port": "DB_PORT",
            "db_name": "DB_NAME",
            "db_user": "DB_USER",
            "_db_monitor_interval": "DB_MONITOR_INTERVAL",
            "_db_connection_threshold": "DB_CONNECTION_THRESHOLD",
            "db_password": "DB_PASSWORD",
            "_email_alerts": "EMAIL_ALERTS",
            "_report_recipients": "REPORT_RECIPIENTS",
            "_report_sender": "REPORT_SENDER",
            "_report_lookback_minutes": "REPORT_LOOKBACK_MINUTES",
            "_email_password": "SMTP_PASSWORD",
            "_log_level": "LOG_LEVEL",
            "_quote_currency": "QUOTE_CURRENCY",
            "_order_size_fiat": "ORDER_SIZE_FIAT", # in USD
            "_trailing_percentage": "TRAILING_PERCENTAGE",
            "_min_cooldown": "MIN_COOLDOWN", # in minutes
            "_min_order_amount_fiat":"MIN_ORDER_AMOUNT_FIAT", # in USD
            "_min_value_to_monitor": "MIN_VALUE_TO_MONITOR", # in USD
            "_min_buy_value": "MIN_BUY_VALUE",
            "_min_sell_value": "MIN_SELL_VALUE",
            "_max_value_of_crypto_to_buy_more":"MAX_VALUE_TO_BUY", # max value of crypto in USD in order to buy more
            "_min_quote_volume": "MIN_QUOTE_VOLUME", # min daily volume strategies use to evaluate ovhlc data
            "_max_ohlcv_rows": "MAX_OHLCV_ROWS",
            "_hodl": "HODL",
            "_take_profit": "TAKE_PROFIT",
            "_stop_loss": "STOP_LOSS",
            "_cxl_buy": "CXL_BUY",
            "_cxl_sell": "CXL_SELL",
            "_roc_buy_24h": "ROC_BUY_24H",
            "_roc_sell_24h": "ROC_SELL_24H",
            "_roc_window": "ROC_WINDOW",
            "_roc_5min":"ROC_5MIN",
            "_min_spread_pct":"MIN_SPREAD_PCT",
            "_spread_to_fee_min":"SPREAD_TO_FEE_MIN",
            "_tp_min_ticks":"TP_MIN_TICKS",
            "_sl_limit_offset_ticks":"SL_LIMIT_OFFSET_TICKS",
            "_min_l1_notional_usd":"MIN_L1_NOTIONAL_USD",
            "_pre_bracket_sigma_ratio":"PREBRACKET_SIGMA_RATIO",
            "_edge_buffer_pct":"EDGE_BUFFER_PCT",
            "_max_lifetime":"MAX_LIFETIME",
            "_inventory_bias_factor":"INVENTORY_BIAS_FACTOR",
            "_coin_whitelist": "COIN_WHITELIST",
            "_score_buy_target": "SCORE_BUY_TARGET",
            "_score_sell_target": "SCORE_SELL_TARGET",
            "_allow_buys_on_red_day": "ALLOW_BUYS_ON_RED_DAY",
            "_flip_hysteresis_pct": "FLIP_HYSTERESIS_PCT",
            "_cooldown_bars": "COOLDOWN_BARS",
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
            "_swing_window": "SWING_WINDOW",
            "_sma_volatility": "SMA_VOLATILITY",
            "_trailing_stop": "TRAILING_STOP",
            "_trailing_limit": "TRAILING_LIMIT",
            "_enrich_limit": "ENRICH_LIMIT",
            "_currency_pairs_ignored": "CURRENCY_PAIRS_IGNORED",
            "_shill_coins": "SHILL_COINS",
            "_sleep_time": "SLEEP",
            "_tp_sl_log_path": "TP_SL_LOG_PATH",
            "_score_jsonl_path": "SCORE_JSONL_PATH",
            # "_pc_url": 'PC_URL',
            "_maker_fee": "MAKER_FEE",
            "_taker_fee": "TAKER_FEE",

        }

        for attr, env_var in env_vars.items():
            value = os.getenv(env_var)

            if value is not None:
                setattr(self, attr, Decimal(value) if attr.startswith("_") and "percentage" in attr.lower() else value)
            else:
                pass
        print(f"Configuration loaded successfully.")

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
        try:
            self.db_url = build_asyncpg_url_from_env()
            masked = self.db_url.replace(self.db_url.split("://", 1)[1].split("@", 1)[0], "****:****")
            print(f"Configured PostgreSQL database at: {masked}")
            print(f" ❇️  web_url: {self.web_url}  ❇️ ")
        except Exception as e:
            print(f"Error configuring database URL: {e}")
            raise

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
        whitelist = coin_whitelist_list
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

            self.rest_client = coinbase.RESTClient(api_key=api_key, api_secret=api_secret)
            self.portfolio_uuid = portfolio_uuid
            print("REST client successfully initialized.")
        except Exception as e:
            print(f"Error initializing REST client: {e}")
            raise

    def load_channels(self):
        """Load and return WebSocket API channels as a list of channel names."""
        try:
            websocket_config = self.load_websocket_api_key()  # ✅ Load the correct JSON config

            # ✅ Extract market and user channels (only the keys, not values)
            market_channels = list(websocket_config.get("market_channels", {}).keys())
            user_channels = list(websocket_config.get("user_channels", {}).keys())

            return market_channels, user_channels
        except Exception as e:
            print(f"Error loading channels: {e}")
            return [], []  # Return empty lists in case of failure

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

    def determine_machine_type(self) -> tuple:
        cwd_parts = os.getcwd().split('/')
        print(f"🍀🍀🍀 {cwd_parts} 🍀🍀🍀")
        if 'app' in cwd_parts:
            print(f"🍀 Machine type: docker 🍀")
            return 'docker', int(os.getenv('WEBHOOK_PORT', 5003))
        elif len(cwd_parts) > 2:

            if cwd_parts[2] == 'jack':
                print(f"🍀 Machine type: Laptop 🍀")
                return cwd_parts[2], int(os.getenv('WEBHOOK_PORT', 5003))
            else:
                print(f"🍀 Machine type: Desktop 🍀")
                return cwd_parts[2], int(os.getenv('WEBHOOK_PORT', 5003))
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
    def report_recipients(self):
        return self._report_recipients
    @property
    def report_lookback_minutes(self):
        return self._report_lookback_minutes

    @property
    def score_jsonl_path(self):
        return self._score_jsonl_path
    @property
    def tp_sl_log_path(self):
        return self._tp_sl_log_path

    @property
    def aws_region(self):
        return self._aws_region

    @property
    def in_docker(self):
        return self.in_docker

    @property
    def webhook_api_key_path(self):
        return self._webhook_api_key_path

    @property
    def docker_staticip(self):
        return self._docker_staticip

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
    def db_monitor_interval(self):
        return self._db_monitor_interval

    @property
    def db_connection_threshold(self):
        return self._db_connection_threshold

    @property
    def log_level(self):
        return self._log_level

    @property
    def hodl(self):
        return self._hodl

    @property
    def shill_coins(self):
        return self._shill_coins

    @property
    def currency_pairs_ignored(self):
        return self._currency_pairs_ignored

    @property
    def assets_ignored(self):
        return self._assets_ignored

    @property
    def min_order_amount_fiat(self):
        return Decimal(self._min_order_amount_fiat)

    @property
    def min_value_to_monitor(self):
        return Decimal(self._min_value_to_monitor)

    @property
    def min_sell_value(self):
        return Decimal(self._min_sell_value)

    @property
    def min_buy_value(self):
        return Decimal(self._min_buy_value)

    @property
    def max_value_of_crypto_to_buy_more(self):
        return Decimal(self._max_value_of_crypto_to_buy_more)

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
    def maker_fee(self):
        return self._maker_fee

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def min_quote_volume(self):
        return self._min_quote_volume

    @property
    def min_cooldown(self):
        return float(self._min_cooldown)

    @property
    def database_url(self):

        return self.db_url

    @property
    def trailing_stop(self):
        return self._trailing_stop

    @property
    def trailing_limit(self):
        return self._trailing_limit

    @property
    def enrich_limit(self):
        return int(self._enrich_limit)

    @property
    def phone(self):
        return self._phone

    @property
    def report_sender(self):
        return self._report_sender

    @property
    def smtp_password(self):
        return self._smtp_password

    @property
    def report_recipient(self):
        return self._report_recipients

    @property
    def email_alerts(self):
        return self._email_alerts

    @property
    def order_size_fiat(self):
        return self._order_size_fiat

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
    def swing_window(self):
        return self._swing_window
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
    def roc_buy_24h(self):
        return int(self._roc_buy_24h)

    @property
    def roc_sell_24h(self):
        return int(self._roc_sell_24h)

    @property
    def roc_window(self):
        return int(self._roc_window)

    @property
    def roc_5min(self):
        return int(self._roc_5min)

    @property
    def min_spread_pct(self):
        return Decimal(self._min_spread_pct)

    @property
    def spread_to_fee_min(self):
        return Decimal(self._spread_to_fee_min)

    @property
    def tp_min_ticks(self):
        return Decimal(self._tp_min_ticks)

    @property
    def sl_limit_offset_ticks(self):
        return Decimal(self._sl_limit_offset_ticks)

    @property
    def min_l1_notional_usd(self):
        return Decimal(self._min_l1_notional_usd)

    @property
    def pre_bracket_sigma_ratio(self):
        return Decimal(self._pre_bracket_sigma_ratio)

    @property
    def edge_buffer_pct(self):
        return Decimal(self._edge_buffer_pct)

    @property
    def max_lifetime(self):
        return int(self._max_lifetime)

    @property
    def inventory_bias_factor(self):
        return self._inventory_bias_factor

    @property
    def csv_dir(self):
        return self._csv_dir

    @property
    def is_loaded(self):
        return self._is_loaded

    @property
    def score_buy_target(self) -> float:
        try:
            return float(self._score_buy_target) if self._score_buy_target is not None else 5.5
        except Exception:
            return 5.5

    @property
    def score_sell_target(self) -> float:
        try:
            return float(self._score_sell_target) if self._score_sell_target is not None else 5.5
        except Exception:
            return 5.5

    @property
    def allow_buys_on_red_day(self) -> bool:
        v = self._allow_buys_on_red_day
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        return str(v).strip().lower() in ("1","true","yes","y","on")

    @property
    def flip_hysteresis_pct(self) -> float:
        try:
            return float(self._flip_hysteresis_pct) if self._flip_hysteresis_pct is not None else 0.10
        except Exception:
            return 0.10

    @property
    def cooldown_bars(self) -> int:
        try:
            return int(self._cooldown_bars) if self._cooldown_bars is not None else 7
        except Exception:
            return 7


    def _strip_path(self, url: str) -> str:
        from urllib.parse import urlparse
        try:
            p = urlparse(url)
            if p.scheme and p.netloc:
                return f"{p.scheme}://{p.netloc}"
        except Exception:
            pass
        return url.rsplit("/", 1)[0]

    @property
    def web_url(self) -> str:
        # base: scheme://host[:port] only (no path)
        base = (os.getenv("WEBHOOK_BASE_URL") or self._default_base_url()).rstrip("/")
        # path: default to /webhook; accept env override
        path = (os.getenv("WEBHOOK_PATH") or "/webhook").strip() or "/webhook"
        if not path.startswith("/"):
            path = "/" + path
        final = urljoin(base + "/", path.lstrip("/"))
        self._log_url("computed", final, base=base, path=path)
        print("Webhook URL resolved to:", final)
        return final

    def _default_base_url(self) -> str:
        # legacy fallbacks if users still set them
        in_docker = running_in_docker()
        if in_docker is None:
            pc_url = getattr(self, "_pc_url", None) or os.getenv("PC_URL") or ""
            return self._strip_path(pc_url)
        else:
            docker_url = os.getenv("WEBHOOK_BASE_URL") or ""
            return docker_url





        # In Docker
        if in_docker:
            # split mode (webhook/sighook): prefer service DNS
            return "http://webhook:5003"

        # single-container in Docker
        if docker_url:
            return self._strip_path(docker_url)
        return "http://127.0.0.1:5003"

    def _log_url(self, source: str, url: str, **kw):
        print(f"❇️ web_url ({source}): {url}  {(' ' + str(kw)) if kw else ''}")

    @property
    def sleep_time(self):
        return self._sleep_time



