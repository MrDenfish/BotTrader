import os
from dotenv import load_dotenv
import json
from decimal import Decimal


class AppConfig:
    _instance = None
    _is_loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        self.db_type, self.port, self.machine_type, self.profit_dir, self.portfolio_dir  = None, None, None, None, None
        self.active_trade_dir, self._min_sell_value, self.sqlite_db_path, self.sqlite_db_file = None, None, None, None
        self.log_dir, self._json_config, self._take_profit, self._stop_loss, self._sleep_time = None, None, None, None, None
        self._my_email, self._e_mailpass, self._email, self._phone, self._web_url = None, None, None, None, None
        self._account_phone, self._async_mode, self._auth_token, self._account_sid = None, None, None, None
        self._coin_whitelist, self._tv_whitelist, self._docker_staticip, self._manny_database_path = None, None, None, None
        self._digital_ocean_database_path, self._cmc_api_url, self._cmc_api_key, self._api_url = None, None, None, None
        self._passphrase, self._api_secret, self._api_key, self._version, self._log_level = None, None, None, None, None
        self._echo_sql, self._db_pool_size, self._db_max_overflow, self._db_echo = None, None, None, None
        self._ccxt_verbose = None
        self._database_dir = None
        self._hodl = []
        if self._is_loaded:
            return

        self.load_dotenv_settings()
        self.machine_type, self.port = self.determine_machine_type()
        self.load_json_config()
        self.load_environment_variables()
        self._is_loaded = True

    @staticmethod
    def load_dotenv_settings():
        env_path = ".env" if os.getenv('RUNNING_IN_DOCKER') else os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                                                              '.env_tradebot')
        load_dotenv(env_path)

    def load_environment_variables(self):
        self.db_type = os.getenv('DB_TYPE', 'sqlite')
        self._database_dir = self.get_database_dir()
        self.db_name = os.getenv('DB_NAME', 'tradebot')
        self.db_file = os.getenv('DB_FILE', 'trades.db')  # Default SQLite file name
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
        self._min_sell_value = Decimal(os.getenv('MIN_SELL_VALUE'))
        self._hodl = os.getenv('HODL')
        self._stop_loss = os.getenv('STOP_LOSS')
        self._take_profit = os.getenv('TAKE_PROFIT')
        self._log_level = os.getenv('LOG_LEVEL_SIGHOOK')
        if self.machine_type in ['moe', 'Manny', 'docker']:
            self.port = os.getenv('SIGHOOK_PORT')  # Example usage

    def generate_database_url(self):
        db_path = os.path.join(self.get_database_dir(), self.db_file)
        return f"sqlite+aiosqlite:///{db_path}"

    def load_json_config(self):
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'config.json')
            with open(config_path, 'r') as f:
                self._json_config = json.load(f)
            # Process loaded configuration here...
        except Exception as e:
            print(f"Error loading JSON configuration: {e}")
            exit(1)

    def process_config_paths(self, config):
        base_dir = os.getenv('BASE_DIR_' + self.machine_type.upper(), '.')
        for key in ['SENDER_ERROR_LOG_DIR', 'DATABASE_DIR', 'ACTIVE_TRADE_DIR', 'PORTFOLIO_DIR', 'PROFIT_DIR']:
            path = os.path.join(base_dir, config[self.machine_type][key])
            setattr(self, key.lower(), path)
            if not os.path.exists(path):
                os.makedirs(path)

    def get_database_dir(self):
        return os.path.join(os.getenv('BASE_DIR_' + self.machine_type.upper(), '.'),
                            self._json_config.get(self.machine_type, {}).get('DATABASE_DIR', ''))

    def get_directory_paths(self, path):
        base_dir = os.getenv('BASE_DIR_' + self.machine_type.upper(), '.')
        self.log_dir = os.path.join(base_dir, self._json_config[self.machine_type]['SENDER_ERROR_LOG_DIR'])
        # self.database_dir = os.path.join(base_dir, self._json_config[self.machine_type]['DATABASE_DIR'])
        self.sqlite_db_file = self._json_config[self.machine_type]['DATABASE_FILE']
        self.sqlite_db_path = os.path.join(self.database_dir, self.sqlite_db_file)
        self.active_trade_dir = os.path.join(base_dir, self._json_config[self.machine_type]['ACTIVE_TRADE_DIR'])
        self.portfolio_dir = os.path.join(base_dir, self._json_config[self.machine_type]['PORTFOLIO_DIR'])
        self.profit_dir = os.path.join(base_dir, self._json_config[self.machine_type]['PROFIT_DIR'])

        # Create the directories if they don't exist
        for dir_path in [self.active_trade_dir, self.portfolio_dir, self.profit_dir]:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

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

    @property
    def database_dir(self):
        return self._database_dir

    @property
    def database_url(self):
        if self.db_type == 'sqlite':
            return f"sqlite+aiosqlite:///{os.path.join(self.database_dir, self.db_file)}"
        # Additional database types can be handled here
        return None

    @property
    def db_echo(self):
        return self._db_echo

    @property
    def db_pool_size(self):
        return self._db_pool_size

    @property
    def db_max_overflow(self):
        return self._db_max_overflow

    @property
    def echo_sql(self):
        return self._echo_sql

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value

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