
from datetime import datetime

import requests

class SenderWebhook:
    _instance_count = 0

    def __init__(self, exchange, utility, logmanager):
        # self.id = SenderWebhook._instance_count
        # SenderWebhook._instance_count += 1
        # print(f"SenderWebhook Instance ID: {self.id}")
        self.exchange = exchange
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_cache = None
        self.start_time = None
        self.web_url = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.web_url = web_url
        self.current_holdings = hist_holdings

    def send_webhook(self, send_action, send_pair, lim_price, send_order, order_size=None):
        """"""
        lim_price = str(lim_price)
        send_pair = send_pair.replace('/', '')
        payload = {}
        if send_action == 'open_at_limit':
            order_size = str(100.00)
            payload = {
                'action': send_action,  # open_at_limit: buy, close_at_limit: sell
                'pair': send_pair,  # trading pair (BTCUSD)
                'order_size': order_size,  # order size
                'limit_price': lim_price,  # price
                'origin': "signal_generator"  # where the signal came from
            }
        elif send_action == 'close_at_limit':
            payload = {
                'action': send_action,  # open_at_limit: buy, close_at_limit: sell
                'pair': send_pair,  # trading pair (BTCUSD)
                'limit_price': lim_price,  # price
                'order_type': send_order,  # order type (market, limit, stop, stop_limit)
                'origin': "signal_generator"  # where the signal came from
            }

        current_time = datetime.now()
        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            response_request = requests.post(self.web_url, json=payload, timeout=20)
            # Handle the response here, and raise an exception if rate-limited
            if response_request.status_code == 429:  # 429 is the typical status code for rate limits
                raise Exception('429: Rate limit exceeded')
            if response_request.status_code == 400:
                raise Exception('400: Invalid request format.')
            if response_request.status_code == 401:
                raise Exception('401: Invalid api(Coinbase Cloud) Key')
            if ' 403: IP Not Whitelisted' in str(response_request.status_code):
                my_ip = self.utility.get_my_ip_address()
                self.log_manager.sighook_logger.debug(
                    f'send_webhook: Exception occurred during API call from IP {my_ip}: {response_request}')
                raise Exception('403: IP Not Whitelisted')
            if response_request.status_code == 403:
                my_ip = self.utility.get_my_ip_address()  # debug when running on the laptop
                self.log_manager.sighook_logger.debug(
                    f'send_webhook: Exception occurred during API call from IP {my_ip}: {response_request}')
                raise Exception('403: IP Not Whitelisted')
            if response_request.status_code == 404:
                raise Exception('404: Not found, check the webhook URL')
            if response_request.status_code == 405:
                raise Exception('405: Method Not Allowed')
            if response_request.status_code == 500:
                raise Exception('500: Internal Server error - Coinbase issue')
            if response_request.status_code == 502:
                raise Exception('502: Bad Gateway - Coinbase issue')
            if response_request.status_code == 200:
                self.log_manager.sighook_logger.debug(f'send_webhook 200: {payload} placed at {formatted_time} ')
        except requests.exceptions.Timeout as eto:
            self.log_manager.sighook_logger.error(f'webhook: send_webhook: Request Timed out at {formatted_time} : {eto}')
        except Exception as e:
            self.log_manager.sighook_logger.error(f'webhook: send_webhook:unknown error occurred: {e}')
        return None
