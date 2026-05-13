import ccxt


class ExchangeManager:
    _instance = None

    @classmethod
    def get_instance(cls, config=None):
        if cls._instance is None:
            if config is None:
                raise ValueError("ExchangeManager requires config on first call")
            print("� ExchangeManager: Creating singleton instance")
            cls._instance = cls(config)
        else:
            print("✅ ExchangeManager: Returning existing singleton instance")
        return cls._instance

    def __init__(self, config):
        if ExchangeManager._instance is not None:
            raise Exception("Use get_instance() instead of instantiating directly.")

        api_key = config.get('name')
        secret = config.get('privateKey')

        self.exchange = ccxt.coinbase({
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'verbose': False,
        })

    def get_exchange(self):
        return self.exchange
