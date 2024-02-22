# Define the AlertSystem class

import smtplib
import socket
import asyncio
import random
import traceback

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

    def get_my_ip_address(self):  # rarely used except when exception occurs for debugging
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        self.log_manager.sighook_logger.info(f"Hostname: {hostname}")
        return ip_address

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
                self._smtp_server.quit()  # Close the connection


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
        self.session = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, session, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    async def send_webhook(self, send_action, send_pair, lim_price, send_order, order_size=None, retries=3, initial_delay=1,
                           max_delay=60):
        delay = initial_delay
        response = None
        # Define payload outside of the retry loop to avoid redundant operations
        lim_price = str(lim_price)
        send_pair = send_pair.replace('/', '')
        order_size = str(order_size) if order_size is not None else '100'  # Default to '100' if None
        payload = {
            'action': send_action,
            'pair': send_pair,
            'limit_price': lim_price,
            'origin': "signal_generator"
        }
        if send_action == 'close_at_limit':
            payload['order_type'] = send_order
        else:
            payload['order_size'] = order_size

        for attempt in range(1, retries + 1):
            try:
                self.log_manager.sighook_logger.debug(f"Attempt {attempt}: Sending webhook payload: {payload}")

                # async with aiohttp.ClientSession() as session:
                response = await self.session.post(self.web_url, json=payload, headers={'Content-Type': 'application/json'},
                                                   timeout=20)
                if response.text is not None:
                    response_text = await response.text()
                else:
                    response_text = None
                self.log_manager.sighook_logger.debug(f"Webhook sent, awaiting response...")
                if response.status == 200:
                    self.log_manager.sighook_logger.debug(f"Webhook successfully sent: {payload}")
                    return  # Success, exit function

                # Handle specific status codes that warrant a retry or log an error
                if response.status in [429, 500]:  # Rate limit exceeded or server error
                    self.log_manager.sighook_logger.error(f"Error {response.status}: {response_text}")
                else:
                    raise Exception(f"Unhandled status code {response.status}: {response_text}")

            except asyncio.TimeoutError as eto:
                self.log_manager.sighook_logger.error(f"Request timed out: {eto} ")

            except Exception as e:
                tb_str = traceback.format_exc()  # get complete traceback as a string
                self.log_manager.sighook_logger.error(f'Error in sending webhook(): {e}\nTraceback: {tb_str}')
                raise  # Reraise the exception to handle it outside or log it

            # Apply exponential backoff with jitter only if not on last attempt
            if attempt < retries:
                sleep_time = int(delay + random.uniform(0, delay * 0.2))
                await asyncio.sleep(sleep_time)
                delay = min(delay * 2, max_delay)

        self.log_manager.sighook_logger.error("Max retries reached, giving up.")
        return None  # Indicate failure after all retries
