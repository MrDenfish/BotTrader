# Define the AlertSystem class

import os
import smtplib
from datetime import datetime
import asyncio
import aiohttp
from aiohttp import ClientSession

from dotenv import load_dotenv

""" This class handles the sending of alert messages, such as SMS or emails."""
#


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

    def _connect_smtp_server(self):
        # Establish a new SMTP connection for each email sent
        self._smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        self._smtp_server.login(self._email, self._e_mailpass)

    @property
    def smtp_server(self):
        return self._smtp_server

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

    def callhome(self, subject, message):
        try:
            self._connect_smtp_server()  # Ensure a fresh connection is established
            to = f'{self._phone}@txt.att.net'
            email_text = f'Subject: {subject}\n\n{message}'
            self._smtp_server.sendmail(self._my_email, to, email_text)
        except Exception as e:
            print(f'Error sending SMS alert: {e}')
            self.log_manager.sighook_logger.error(f'Error sending SMS alert: {e}')
        finally:
            if self._smtp_server:
                self._smtp_server.quit()  # Close the connection in the finally block


class SenderWebhook:
    _instance_count = 0

    def __init__(self, exchange, utility, logmanager, config):
        self._smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        self._phone = config.phone
        self._email = config.email
        self._e_mailpass = config.e_mailpass
        self._my_email = config.my_email
        self.log_manager = logmanager
        self.exchange = exchange
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.current_holdings = hist_holdings

    async def send_webhook(self, send_action, send_pair, lim_price, send_order, order_size=None):
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
        async with ClientSession() as session:
            try:

                async with session.post(self.web_url, json=payload, timeout=20) as response:
                    # Handle the response here, and raise an exception if rate-limited
                    if response.status == 200:
                        self.log_manager.sighook_logger.debug(f'send_webhook 200: {payload} placed at {formatted_time}')
                    elif response.status == 429:
                        raise Exception('429: Rate limit exceeded')
                    elif response.status == 400:
                        raise Exception('400: Invalid request format.')
                    elif response.status == 403:
                        my_ip = self.utility.get_my_ip_address()  # debug when running on the laptop
                        self.log_manager.sighook_logger.debug(
                            f'send_webhook: Exception occurred during API call from IP {my_ip}: {response}')
                        raise Exception('403: IP Not Whitelisted')
                    elif response.status == 404:
                        raise Exception('404: Not found, check the webhook URL')
                    elif response.status == 405:
                        raise Exception('405: Method Not Allowed')
                    elif response.status == 500:
                        raise Exception('500: Internal Server error - Coinbase issue')
                    elif response.status == 502:
                        raise Exception('502: Bad Gateway - Coinbase issue')
                    elif response.status == 401:
                        raise Exception('401: Invalid api(Coinbase Cloud) Key')
                    else:
                        raise Exception(f'Unhandled status code: {response.status}')

            except asyncio.TimeoutError as eto:  # Corrected exception for timeout
                self.log_manager.sighook_logger.error(f'send_webhook: Request Timed out at {formatted_time}: {eto}')
            except aiohttp.ClientError as e:  # General aiohttp client errors
                self.log_manager.sighook_logger.error(f'send_webhook: Aiohttp client error occurred: {e}')
            except Exception as e:  # Catch-all for any other exceptions
                self.log_manager.sighook_logger.error(f'send_webhook: Unknown error occurred: {e}')

            return None
