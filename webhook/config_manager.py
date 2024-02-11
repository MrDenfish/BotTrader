# config_manager.py
"""The BotConfig class has been outlined with methods for loading configuration
from environment variables and providing getters for accessing these configurations.
This class  encapsulates the configuration management for the trading bot."""
import os
import json

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
            env_path = os.path.join(os.path.dirname(__file__), '..', 'config', '.env_webhook')
            load_dotenv(env_path)  # Load environment variables
            self._version = os.getenv('VERSION')
            self._api_key = os.getenv('API_KEY')
            self._api_secret = os.getenv('API_SECRET')
            self._passphrase = os.getenv('API_PASS')
            self._api_url = os.getenv('API_URL')
            self._docker_staticip = os.getenv('DOCKER_STATICIP')
            self._tv_whitelist = os.getenv('TV_WHITELIST')
            self._coin_whitelist = os.getenv('COIN_WHITELIST')
            self._account_sid = os.getenv('ACCOUNT_SID')
            self._auth_token = os.getenv('AUTH_TOKEN')
            self._account_phone = os.getenv('ACCOUNT_PHONE')
            self._web_url = os.getenv('WEB_URL')
            self._json_config = None
            self.machine_type = None
            self.port = int(os.getenv('WEBHOOK_PORT', 80))  # Default to 80 if not set
            self.log_dir = None
            self.flask_log_dir = None
            self.sql_log_dir = None
            self.active_trade_dir = None
            self.portfolio_dir = None
            self.profit_dir = None

            self.load_json_config()
            self._is_loaded = True

    def load_json_config(self):
        self.machine_type = self.determine_machine_type()
        current_dir = os.path.dirname(os.path.realpath(__file__))
        config_path = os.path.join(current_dir, 'config.json')

        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            self._json_config = config
            if self.machine_type in config:
                machine_path = config[self.machine_type]
                # Load machine-specific settings
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
        print(f'Machine type: {machine_type}')
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
    def program_version(self):
        return self._version

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

    def reload_config(self):
        # Force reload of configuration
        self._is_loaded = False
        self.__init__()
