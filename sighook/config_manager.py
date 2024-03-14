import os
import json

from dotenv import load_dotenv


"""The AppConfig class has been outlined with methods for loading configuration
from environment variables and providing getters for accessing these configurations.
This class  encapsulates the configuration management for the trading bot."""


class AppConfig:
    _instance = None
    _is_loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        self.port = None
        self.machine_type = None
        self.profit_dir = None
        self.portfolio_dir = None
        self.active_trade_dir = None
        self.log_dir = None
        self.database_dir = None
        self.sqlite_db_path = None
        self.sqlite_db_file = None
        self._json_config = None
        self._take_profit = None
        self._stop_loss = None
        self._sleep_time = None
        self._my_email = None
        self._e_mailpass = None
        self._email = None
        self._phone = None
        self._web_url = None
        self._account_phone = None
        self._async_mode = None
        self._auth_token = None
        self._account_sid = None
        self._coin_whitelist = None
        self._tv_whitelist = None
        self._docker_staticip = None
        self._manny_database_path = None
        self._digital_ocean_database_path = None
        self._cmc_api_url = None
        self._cmc_api_key = None
        self._api_url = None
        self._passphrase = None
        self._api_secret = None
        self._api_key = None
        self._version = None
        self._log_level = None
        if not self._is_loaded:
            # Check if running inside Docker by looking for a specific environment variable
            if os.getenv('RUNNING_IN_DOCKER'):
                env_path = ".env"  # Docker environment
            else:
                # Local environment
                env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env_tradebot')
            load_dotenv(env_path)
            self.load_environment_variables()
            self.load_json_config()
            self._is_loaded = True

    def load_environment_variables(self):
        self._version = os.getenv('VERSION')
        self._async_mode = os.getenv('ASYNC_MODE')
        self._api_key = os.getenv('API_KEY')
        self._api_secret = os.getenv('API_SECRET')
        self._passphrase = os.getenv('API_PASS')
        self._api_url = os.getenv('API_URL')
        self._cmc_api_key = os.getenv('CMC_API_KEY')  # PLACEHOLDER NOT USABLE CODE
        self._cmc_api_url = os.getenv('CMC_API_URL')  # PLACEHOLDER NOT USABLE CODE
        self._docker_staticip = os.getenv('DOCKER_STATICIP')
        self._manny_database_path = os.getenv('MANNY_DATABASE_PATH')
        self._digital_ocean_database_path = os.getenv('DIGITAL_OCEAN_DATABASE_PATH')
        self._tv_whitelist = os.getenv('TV_WHITELIST')
        self._coin_whitelist = os.getenv('COIN_WHITELIST')
        self._account_sid = os.getenv('ACCOUNT_SID')
        self._auth_token = os.getenv('AUTH_TOKEN')
        self._account_phone = os.getenv('ACCOUNT_PHONE')
        self._web_url = os.getenv('WEB_URL')
        self._phone = os.getenv('PHONE')
        self._email = os.getenv('EMAIL')
        self._e_mailpass = os.getenv('E_MAILPASS')
        self._my_email = os.getenv('MY_EMAIL')
        self._sleep_time = os.getenv('SLEEP')
        self._stop_loss = os.getenv('STOP_LOSS')
        self._take_profit = os.getenv('TAKE_PROFIT')
        self._log_level = os.getenv('LOG_LEVEL_SIGHOOK')
        self.machine_type, self.port = self.determine_machine_type()
        if self.machine_type in ['moe', 'Manny', 'docker']:
            self.port = os.getenv('SIGHOOK_PORT')  # Example usage

    def load_json_config(self):
        current_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(current_dir, 'config.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            self._json_config = config
            if self.machine_type in config:
                machine_path = config[self.machine_type]
                self.get_directory_paths(machine_path)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON configuration: {e}")
            exit(1)

    @staticmethod
    def determine_machine_type():
        machine_type = os.getcwd().split('/')
        # print(f'Machine type: {machine_type}')  # debug statement
        if 'app' in machine_type:
            machine_type = 'docker'
            print(f'Machine type: {machine_type}')
        elif len(machine_type) > 1:
            machine_type = machine_type[2]
            print(f'Machine type: {machine_type}')
        else:
            print(f"Invalid path {os.getcwd()}")

        if machine_type in ['moe', 'Manny', 'docker']:
            port = os.getenv('SIGHOOK_PORT')  # Get the port from the environment variable

        else:
            print('Error: Could not determine machine type')
            exit(1)
        return machine_type, port

    def get_directory_paths(self, path):
        base_dir = os.getenv('BASE_DIR_' + self.machine_type.upper(), '.')
        self.log_dir = os.path.join(base_dir, self._json_config[self.machine_type]['SENDER_ERROR_LOG_DIR'])
        self.database_dir = os.path.join(base_dir, self._json_config[self.machine_type]['DATABASE_DIR'])
        self.sqlite_db_file = self._json_config[self.machine_type]['DATABASE_FILE']
        self.sqlite_db_path = os.path.join(self.database_dir, self.sqlite_db_file)
        self.active_trade_dir = os.path.join(base_dir, self._json_config[self.machine_type]['ACTIVE_TRADE_DIR'])
        self.portfolio_dir = os.path.join(base_dir, self._json_config[self.machine_type]['PORTFOLIO_DIR'])
        self.profit_dir = os.path.join(base_dir, self._json_config[self.machine_type]['PROFIT_DIR'])

        # Create the directories if they don't exist
        for dir_path in [self.active_trade_dir, self.portfolio_dir, self.profit_dir]:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

    # @staticmethod  # Toggleable Function
    # def run_in_mode(sync_func, async_func, *args, **kwargs):
    #     if async_mode:
    #         return async_func(*args, **kwargs)  # Remember to await this when calling
    #     else:
    #         return sync_func(*args, **kwargs)
    #
    # # Usage
    # result = run_in_mode(get_data_sync, get_data_async, arg1, arg2)

    @property
    def program_version(self):
        return self._version

    @property
    def async_mode(self):
        return self._async_mode

    @property
    def log_level(self):
        return self._log_level

    @property
    def sleep_time(self):
        return self._sleep_time

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    @property
    def cmc_api_key(self):
        return self._cmc_api_key

    @property
    def passphrase(self):
        return self._passphrase

    @property
    def api_url(self):
        return self._api_url

    @property
    def cmc_api_url(self):
        return self._cmc_api_url

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
    def is_loaded(self):
        return self._is_loaded

    @property
    def web_url(self):
        return self._web_url

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

    def reload_config(self):
        # Force reload of configuration
        self._is_loaded = False
        self.__init__()
