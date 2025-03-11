from smtplib import SMTP_SSL
from Shared_Utils.config_manager import CentralConfig
import aiohttp
import asyncio
import random
import uuid
import json


class AlertSystem:
    _instance = None

    @classmethod
    def get_instance(cls, logmanager):
        """
        Singleton method to ensure only one instance of AlertSystem exists.
        """
        if cls._instance is None:
            cls._instance = cls(logmanager)
        return cls._instance

    def __init__(self, logmanager):
        """
        Initializes the AlertSystem.
        """
        if AlertSystem._instance is not None:
            raise Exception("This class is a singleton! Use get_instance().")

        self.config = CentralConfig()
        self._smtp_server = SMTP_SSL('smtp.gmail.com', 465)
        self._phone = self.config.phone
        self._email = self.config.email
        self._e_mailpass = self.config.e_mailpass
        self._my_email = self.config.my_email
        self._order_size = self.config.order_size
        self._smtp_host = 'smtp.gmail.com'
        self._smtp_port = 465
        self.log_manager = logmanager

        self.semaphore = asyncio.Semaphore(10)

        # Set the instance
        AlertSystem._instance = self



class SenderWebhook:
    _instance = None

    @classmethod
    def get_instance(cls, exchange, alerts, logmanager, shared_utils_utility):
        if cls._instance is None:
            cls._instance = cls(exchange, alerts, logmanager, shared_utils_utility)
        return cls._instance

    def __init__(self, exchange, alerts, logmanager, shared_utils_utility):
        self.config = CentralConfig()
        self._smtp_server = SMTP_SSL('smtp.gmail.com', 465)
        self._phone = self.config.phone
        self._email = self.config.email
        self._e_mailpass = self.config.e_mailpass
        self._my_email = self.config.my_email
        self._order_size = self.config.order_size
        self._version = self.config.program_version
        self.shared_utils_utility = shared_utils_utility
        self.log_manager = logmanager
        self.exchange = exchange
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.alerts = alerts
        self.lock = asyncio.Lock() # initialize the lock
        self.processed_uuids = set()
        self.cleanup_delay = 60 * 5  # Time in seconds after which UUIDs are removed
        self.http_session = None
        self.ticker_cache = None
        self.market_cache_vol= None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, market_data, web_url):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache']
        self.market_cache_vol = market_data['filtered_vol']
        self.web_url = web_url

    @property
    def order_size(self):
        return self._order_size

    async def send_webhook(self, http_session, webhook_payload, retries=3, initial_delay=1, max_delay=60):
        """
        Sends a webhook with retry logic, exponential backoff, and cleaner error handling.

        Args:
            http_session: aiohttp.ClientSession for making HTTP requests.
            webhook_payload: dict containing webhook data to send.
            retries: Number of retry attempts for recoverable errors.
            initial_delay: Initial backoff delay (seconds).
            max_delay: Maximum backoff delay (seconds).
        """
        delay = initial_delay
        webhook_payload['origin'] = "SIGHOOK"
        webhook_payload['verified'] = "valid or not valid"
        webhook_payload['uuid'] = webhook_payload.get('uuid', str(uuid.uuid4()))  # Unique identifier for deduplication

        # Prevent duplicate webhooks
        async with self.lock:
            if webhook_payload['uuid'] in self.processed_uuids:
                self.log_manager.info(f"� Duplicate webhook ignored: {webhook_payload['uuid']}")
                return None
            self.processed_uuids.add(webhook_payload['uuid'])

        # Schedule UUID cleanup after delay
        asyncio.get_event_loop().call_later(self.cleanup_delay, lambda: self.remove_uuid(webhook_payload['uuid']))

        for attempt in range(1, retries + 1):
            try:
                # Attempt webhook send
                self.log_manager.debug(f"� Attempting webhook ({attempt}/{retries}): {webhook_payload}")
                response = await http_session.post(
                    self.web_url,
                    data=json.dumps(webhook_payload, default=self.shared_utils_utility.string_default),
                    headers={'Content-Type': 'application/json'},
                    timeout=45
                )
                response_text = await response.text()

                # ✅ Successful request
                if response.status == 200:
                    if webhook_payload['side'] == 'buy':
                        self.log_manager.order_sent(f"✅ Alert webhook sent successfully: {webhook_payload['uuid']}")
                    return response

                # ❌ Handle non-recoverable errors
                if response.status in [403, 404]:
                    self.log_manager.error(f"� Non-recoverable error {response.status}: {response_text}")
                    return response

                # ⚠️ Handle recoverable errors with clean logging
                if response.status in [429, 500, 503]:
                    error_summary = (response_text[:300] + "...") if len(response_text) > 300 else response_text
                    self.log_manager.warning(f"⚠️ Recoverable {response.status} error: {error_summary} (Retrying...)")

                elif response.status == 400 and 'Insufficient balance to sell' in response_text:
                    self.log_manager.info(f"� Insufficient balance for {webhook_payload['pair']} {webhook_payload['uuid']}")
                    return response

                else:
                    raise Exception(f"Unhandled HTTP {response.status}: {response_text}")

            except asyncio.TimeoutError:
                self.log_manager.error(f"⏳ Request timeout (attempt {attempt}/{retries}): {webhook_payload}")

            except aiohttp.ClientError as e:
                self.log_manager.error(f"� Client error (attempt {attempt}/{retries}): {e}")

            # ⏳ Retry logic with exponential backoff
            if attempt < retries:
                sleep_time = delay + random.uniform(0, delay * 0.3)  # Add jitter
                self.log_manager.debug(f"� Retrying in {sleep_time:.2f} seconds...")
                await asyncio.sleep(sleep_time)
                delay = min(delay * 2, max_delay)  # Exponential backoff

        # ❌ Max retries reached
        self.log_manager.error(f"❌ Max retries reached for webhook: {webhook_payload['uuid']}")
        return None

    def remove_uuid(self, uuid):
        """Safely remove a UUID from the processed set."""
        try:
            self.processed_uuids.remove(uuid)
            self.log_manager.debug(f"UUID {uuid} removed from processed set after {self.cleanup_delay} seconds.")
        except KeyError:
            # UUID might already be removed or not found
            self.log_manager.debug(f"Attempted to remove nonexistent UUID {uuid}. Ignoring.")
