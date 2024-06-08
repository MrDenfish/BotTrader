"""The BotConfig class has been outlined with methods for loading configuration
from environment variables and providing getters for accessing these configurations.
This class  encapsulates the configuration management for the trading bot."""
import os
import json
from decimal import Decimal
from dotenv import load_dotenv


class BotConfig:
    _instance = None
    _is_loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._is_loaded:
            self._version, self._api_key, self._api_secret, self._passphrase = None, None, None, None
            self._api_url, self._json_config, self._docker_staticip, self._tv_whitelist = None, None, None, None
            self._coin_whitelist, self._pagekite_whitelist, self._account_sid, self._auth_token = None, None, None, None
            self._account_phone, self._web_url, self._log_level, self._hodl = None, None, None, []
            self._min_sell_value, self.port, self.machine_type, self.log_dir, self.sql_log_dir = None, None, None, None, None
            self.flask_log_dir, self.active_trade_dir, self.portfolio_dir, self.profit_dir = None, None, None, None
            self._stop_loss, self._take_profit, self._coinbase_api_key, self._coinbase_secret = None, None, None, None
            self._coinbase_passphrase, self._cdp_api_key_path = None, None

            if not self._is_loaded:
                # Check if running inside Docker by looking for a specific environment variable
                if os.getenv('RUNNING_IN_DOCKER'):
                    env_path = ".env"  # Docker environment
                    self.port = int(os.getenv('WEBHOOK_PORT', 80))
                else:
                    # Local environment
                    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env_tradebot')
                    self.port = int(os.getenv('WEBHOOK_PORT', 80))
                load_dotenv(env_path)
                self.load_environment_variables()
                self.load_json_config()
                self._is_loaded = True

    def load_environment_variables(self):
        self._version = os.getenv('VERSION')
        self._api_key = os.getenv('API_KEY')
        self._coinbase_api_key = os.getenv('COINBASE_API_KEY')
        self._coinbase_secret = os.getenv('COINBASE_API_SECRET')
        self._coinbase_passphrase = os.getenv('COINBASE_PASSPHRASE')
        self._api_secret = os.getenv('API_SECRET')
        self._passphrase = os.getenv('API_PASS')
        self._api_url = os.getenv('API_URL')
        self._docker_staticip = os.getenv('DOCKER_STATICIP')
        self._tv_whitelist = os.getenv('TV_WHITELIST')
        self._coin_whitelist = os.getenv('COIN_WHITELIST')
        self._pagekite_whitelist = os.getenv('PAGEKITE_WHITELIST')
        self._account_sid = os.getenv('ACCOUNT_SID')
        self._auth_token = os.getenv('AUTH_TOKEN')
        self._account_phone = os.getenv('ACCOUNT_PHONE')
        self._web_url = os.getenv('WEB_URL')
        self._log_level = os.getenv('LOG_LEVEL_WEBHOOK')
        self._stop_loss = os.getenv('STOP_LOSS')
        self._take_profit = os.getenv('TAKE_PROFIT')
        self._min_sell_value = Decimal(os.getenv('MIN_SELL_VALUE'))
        self._hodl = os.getenv('HODL')
        self.machine_type = self.determine_machine_type()

    def load_json_config(self):
        current_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(current_dir, 'config.json')
        self._cdp_api_key_path = os.path.join(current_dir, 'cdp_api_key.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                self._json_config = config
            if self.machine_type in config:
                self.get_directory_paths()
            else:
                print(f"Error: Machine type '{self.machine_type}' not found in config")
                exit(1)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON configuration: {e}")
            exit(1)

    @staticmethod
    def determine_machine_type():
        machine_type = os.getcwd().split('/')
        if 'app' in machine_type:
            machine_type = 'docker'
            print(f'Machine type: {machine_type}')
        elif len(machine_type) > 1:
            machine_type = machine_type[2]
            print(f'Machine type: {machine_type}')
        else:
            print(f"Invalid path {os.getcwd()}")

        if machine_type in ['moe', 'Manny']:  # laptop, desktop
            # For 'moe' and 'manny', use the specified port (default 80)

            print(f'webhook: determine_machine_type: Machine type: {machine_type}')
        elif machine_type == 'docker':  # container
            # For Docker containers, also use the specified port (default 80)
            pass

        else:
            print('Error: Could not determine machine type')
            exit(1)
        return machine_type

    def get_directory_paths(self):
        base_dir = os.getenv('BASE_DIR_' + self.machine_type.upper(), '.')
        self.log_dir = os.path.join(base_dir, self._json_config[self.machine_type]['TRADERBOT_ERROR_LOG_DIR'])
        self.flask_log_dir = os.path.join(base_dir, self._json_config[self.machine_type]['FLASK_ERROR_LOG_DIR'])

        # Create the directories if they don't exist
        for dir_path in [self.log_dir, self.flask_log_dir]:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

    @property
    def hodl(self):
        return self._hodl

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
    def program_version(self):
        return self._version

    @property
    def log_level(self):
        return self._log_level

    @property  # read only
    def api_key(self):
        return self._api_key

    # Define the rest of the getters
    @property
    def api_secret(self):
        return self._api_secret

    @property
    def passphrase(self):
        return self._passphrase

    @property
    def coinbase_api_key(self):
        return self._coinbase_api_key

    @property
    def coinbase_secret(self):
        return self._coinbase_secret

    @property
    def coinbase_passphrase(self):
        return self._coinbase_passphrase

    @property
    def api_url(self):
        return self._api_url

    @property
    def docker_staticip(self):
        return self._docker_staticip

    @property
    def tv_whitelist(self):
        return self._tv_whitelist

    @property
    def coin_whitelist(self):
        return self._coin_whitelist

    @property
    def pagekite_whitelist(self):
        return self._pagekite_whitelist

    @property
    def account_sid(self):
        return self._account_sid

    @property
    def auth_token(self):
        return self._auth_token

    @property
    def account_phone(self):
        return self._account_phone

    @property
    def json_config(self):
        return self._json_config

    @property
    def cdp_api_key_path(self):
        return self._cdp_api_key_path

    @property
    def is_loaded(self):
        return self._is_loaded

    @property
    def web_url(self):
        return self._web_url

    def reload_config(self):
        # Force reload of configuration
        self._is_loaded = False
        self.__init__()
