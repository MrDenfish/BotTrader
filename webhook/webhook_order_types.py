
from coinbase.rest import RESTClient
from decimal import Decimal
import traceback

# Define the OrderTypeManager class
"""This class  will manage the order types 
    -Limit 
    -Market 
    -Bracket.
"""


class OrderTypeManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        if cls._instance is None:
            cls._instance = cls(config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book)
        return cls._instance

    def __init__(self, config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self._min_sell_value = Decimal(config.min_sell_value)
        self._hodl = config.hodl
        self.exchange = exchange_client
        self.base_url = config.api_url
        self.log_manager = logmanager
        self.validate = validate
        self.order_book = order_book
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts
        self.utils = utility

        # Initialize the REST client using credentials from the config
        self.client = RESTClient(key_file=config.cdp_api_key_path, verbose=True)

    @property
    def hodl(self):
        return self._hodl

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def place_limit_order(self, order_data):
        """
        Attempts to place a limit order and returns the response.
        If the order fails, it logs the error and returns None.
        """
        trading_pair = order_data['trading_pair']

        try:

            side = order_data['side']
            adjusted_size = order_data['adjusted_size']
            adjusted_price = order_data['adjusted_price']
            endpoint = 'private'
            response = await self.ccxt_exceptions.ccxt_api_call(self.exchange.create_limit_order, endpoint, trading_pair,
                                                                side, adjusted_size, adjusted_price, {'post_only': True})
            if response:
                if response == 'amend':
                    return 'amend'  # Order needs amendment
                elif response == 'insufficient base balance':
                    return 'insufficient base balance'
                elif response == 'order_size_too_small':
                    return 'order_size_too_small'
                elif response['id']:
                    return response  # order placed successfully
            else:
                return 'amaend'
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'place_limit_order: {error_details}')
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{trading_pair}')
                return 'amend'
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
                return 'amend'
        return None  # Return None indicating the order was not successfully placed

    def place_market_order(self, trading_pair, side, adjusted_size, adjusted_price):
        """
               This function coordinates the process. It calculates the order parameters, attempts to place
               an order, checks if the order is accepted, and retries if necessary."""
        response = None
        try:
            endpoint = 'private'
            response = self.ccxt_exceptions.ccxt_api_call(self.exchange.create_market_order(trading_pair, side,
                                                          adjusted_size, adjusted_price), endpoint, trading_pair)
            if response:
                return response
        except Exception as ex:
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{trading_pair}')
                return response
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')

        return None  # Return None indicating the order was not successfully pla

    # <><><><><><><><><><><><><><><>NOT IMPLIMENTED YET 04/04/2024 <>><><><><><><><><><><><><><><><><><><><><><><>

    #   async def place_sell_bracket_order(self, order_data, self_trade_prevention_id=None, leverage=None, margin_type=None,
    #                                      retail_portfolio_id=None):
    #     try:
    #         client_order_id = f'client_{int(time.time() * 1000)}'
    #         trading_pair = order_data['trading_pair'].replace('/', '-')
    #         side = order_data['side'].lower()
    #         adjusted_size = str(order_data['adjusted_size'])
    #         adjusted_price = str(order_data['adjusted_price'])
    #         stop_loss_price = str(order_data['stop_loss_price'])
    #
    #         # Log the request parameters
    #         self.log_manager.webhook_logger.info(f'Placing trigger bracket order with params: '
    #                                               f'client_order_id={client_order_id}, '
    #                                               f'product_id={trading_pair}, '
    #                                               f'side={side}, '
    #                                               f'base_size={adjusted_size}, '
    #                                               f'limit_price={adjusted_price}, '
    #                                               f'stop_trigger_price={stop_loss_price}')
    #
    #         response = await self.client.trigger_bracket_order_gtc(
    #             client_order_id=client_order_id,
    #             product_id=trading_pair,
    #             side=side,
    #             base_size=adjusted_size,
    #             limit_price=adjusted_price,
    #             stop_trigger_price=stop_loss_price,
    #             self_trade_prevention_id=self_trade_prevention_id,
    #             leverage=leverage,
    #             margin_type=margin_type,
    #             retail_portfolio_id=retail_portfolio_id
    #         )
    #         return response
    #     except Exception as ex:
    #         self.log_manager.webhook_logger.debug(f'Error placing trigger bracket order: {ex}', exc_info=True)
    #         return None
    #
    #         if response.status_code == 200:
    #             return response.json()
    #         else:
    #             print(f'Error: {response.status_code}, {response.text}')
    #             return None
    #     except Exception as ex:
    #         error_details = traceback.format_exc()
    #         self.log_manager.webhook_logger.error(f'place_bracket_order: {error_details}')
    #         self.log_manager.webhook_logger.error(f'Error placing bracket order: {ex}', exc_info=True)
    #         return None
