import os
import json
from coinbase.rest import RESTClient
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
            self.active_trade_dir, self.portfolio_dir, self.profit_dir, self._stop_loss = None, None, None, None
            self._take_profit, self._webhook_api_key_path, self._tb_api_key_path, self.rest_client = (None, None, None, None)
            self._websocket_api_key_path, self._trailing_percentage, self._roc_24hr, self.websocket_api = (None, None,
                                                                                                           None, None)

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
        self._roc_24hr = os.getenv('ROC_24HR')
        self._stop_loss = os.getenv('STOP_LOSS')
        self._take_profit = os.getenv('TAKE_PROFIT')
        self._trailing_percentage = Decimal(os.getenv('TRAILING_PERCENTAGE', '0.5'))  # Default trailing stop at 0.5%
        self._min_sell_value = Decimal(os.getenv('MIN_SELL_VALUE'))
        self._hodl = os.getenv('HODL')
        self._api_key = os.getenv('API_KEY')
        self._api_secret = os.getenv('API_SECRET')
        self._passphrase = os.getenv('PASSPHRASE')

        self.machine_type = self.determine_machine_type()

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
        pagekite_whitelist_list = self._pagekite_whitelist.split(',') if isinstance(self._pagekite_whitelist,
                                                                                    str) else self._pagekite_whitelist

        # Combine all whitelist information into a single list, excluding empty values
        whitelist = tv_whitelist_list + coin_whitelist_list + docker_staticip_list + pagekite_whitelist_list
        return [item for item in whitelist if item]  # Exclude empty strings or None values

    def load_json_config(self):
        current_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(current_dir, 'config.json')
        self._webhook_api_key_path = os.path.join(current_dir, 'webhook_api_key.json')
        self._tb_api_key_path = os.path.join(current_dir, 'tb_api_key.json')
        self._websocket_api_key_path = os.path.join(current_dir, 'websocket_api_key.json')
        self.websocket_api = self.load_websocket_api_key()
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
            print(f'webhook: determine_machine_type: Machine type: {machine_type}')
        elif machine_type == 'docker':  # container
            pass
        else:
            print('Error: Could not determine machine type')
            exit(1)
        return machine_type

    def get_directory_paths(self):
        base_dir = os.getenv('BASE_DIR_' + self.machine_type.upper(), '')
        self.log_dir = os.path.join(base_dir, self._json_config[self.machine_type]['LISTENER_ERROR_LOG_DIR'])

        # Create the directories if they don't exist
        for dir_path in [self.log_dir]:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

    def setup_rest_client(self, api_key, api_secret):
        self.rest_client = RESTClient(api_key=api_key, api_secret=api_secret)
        return self.rest_client

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
    def min_sell_value(self):
        return self._min_sell_value

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def program_version(self):
        return self._version

    @property
    def log_level(self):
        return self._log_level

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
    def webhook_api_key_path(self):
        return self._webhook_api_key_path

    @property
    def tb_api_key_path(self):
        return self._tb_api_key_path

    @property
    def websocket_api_key_path(self):
        return self._websocket_api_key_path

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

    def load_webhook_api_key(self):
        try:
            with open(self._webhook_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading webhook CDP API key JSON: {e}")
            exit(1)

    def load_tb_api_key(self):
        try:
            with open(self._tb_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading tb CDP API key JSON: {e}")
            exit(1)

    def load_websocket_api_key(self):
        try:
            with open(self._websocket_api_key_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading websocket CDP API key JSON: {e}")
            exit(1)
