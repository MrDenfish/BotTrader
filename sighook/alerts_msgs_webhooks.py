
from async_functions import AsyncFunctions
import smtplib
import aiohttp
import socket
import asyncio
import random
import time

class AlertSystem:
    _instance = None
    _is_loaded = False

    def __new__(cls, config, logmanager):
        if cls._instance is None:
            cls._instance = super(AlertSystem, cls).__new__(cls)
        return cls._instance

    def __init__(self, config, logmanager):
        self._smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        self._phone = config.phone
        self._email = config.email
        self._e_mailpass = config.e_mailpass
        self._my_email = config.my_email
        # self._smtp_host = 'smtp.gmail.com'
        # self._smtp_port = 465  # Us
        self.log_manager = logmanager


class SenderWebhook:
    _instance_count = 0

    def __init__(self, exchange, alerts, logmanager, config):
        self._smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        self._phone = config.phone
        self._email = config.email
        self._e_mailpass = config.e_mailpass
        self._my_email = config.my_email
        self._version = config.program_version
        self.log_manager = logmanager
        self.exchange = exchange
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.log_manager = logmanager
        self.alerts = alerts
        self.http_session = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url

    async def send_webhook(self, http_session, send_action, send_pair, lim_price, send_order, order_size=None, retries=3,
                           initial_delay=1,
                           max_delay=60):  # async

        delay = initial_delay

        # Define payload outside of the retry loop to avoid redundant operations
        payload = {
            'timestamp': time.time(),
            'action': send_action,
            'pair': send_pair.replace('/', ''),
            'limit_price': str(lim_price),
            'origin': "SIGHOOK",
            'order_size': str(order_size) if order_size is not None else '100',
            'verified': "valid or not valid " # this will be used to verify the order
        }
        if send_action == 'close_at_limit':
            payload['order_type'] = send_order

        for attempt in range(1, retries + 1):
            try:
                response = await http_session.post(self.web_url,
                                                   json=payload,
                                                   headers={'Content-Type': 'application/json'},
                                                   timeout=20)
                response_text = await response.text()

                if response.status == 200:
                    return response

                # Handle specific status codes
                if response.status in [403, 429, 500]:  # Rate limit exceeded or server error
                    if response.status == 403:
                        self.log_manager.sighook_logger.error(f"There may be an issue with NGROK or LocalTunnel check "
                                                              f"monthly limits: {response.status}")
                    else:
                        self.log_manager.sighook_logger.error(f"Error {response.status}: check webhook listener is listening")
                    return response
                elif response.status == 502:  # Not found
                    self.log_manager.sighook_logger.error(f"Error:  Check Listener is listening {response.status}")

                else:
                    raise Exception(f"Unhandled status code {response.status}: {response_text}")

            except asyncio.TimeoutError as eto:

                self.log_manager.sighook_logger.error(f'Request timed out:  {eto}', exc_info=True)
            except aiohttp.ClientError as e:

                self.log_manager.sighook_logger.error(f'Error in sending webhook():  {e}', exc_info=True)

            if attempt < retries:
                sleep_time = delay + random.uniform(0, delay * 0.2)
                await asyncio.sleep(sleep_time)
                delay = min(delay * 2, max_delay)

        self.log_manager.sighook_logger.error("Max retries reached, giving up.")
        return None
