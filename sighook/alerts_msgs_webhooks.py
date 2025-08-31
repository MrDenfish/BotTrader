import os
import json
import random
import asyncio
import aiohttp

from Shared_Utils.enum import ValidationCode
from Config.config_manager import CentralConfig



class SenderWebhook:
    _instance = None

    @classmethod
    def get_instance(cls, exchange, alerts, logger_manager, shared_utils_utility, web_url, shared_data_manager, shared_utils_color):
        if cls._instance is None:
            cls._instance = cls(exchange, alerts, logger_manager, shared_utils_utility,
                                web_url, shared_data_manager, shared_utils_color)
        return cls._instance

    def __init__(self, exchange, alerts, logger_manager, shared_utils_utility, web_url, shared_data_manager, shared_utils_color):
        self.config = CentralConfig()

        self._phone = self.config.phone
        self._email = self.config.email
        self._e_mailpass = self.config.e_mailpass
        self._my_email = self.config.my_email
        self._email_alerts_on = self.config.email_alerts
        self._order_size_fiat = self.config.order_size_fiat
        self._version = self.config.program_version
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_color = shared_utils_color
        self.logger = logger_manager  # 🙂
        self.shared_data_manager = shared_data_manager
        self.exchange = exchange
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.alerts = alerts
        self.lock = asyncio.Lock() # initialize the lock
        self.processed_uuids = set()
        self.cleanup_delay = 60 * 5  # Time in seconds after which UUIDs are removed
        self.http_session = None
        self.start_time = None
        self.web_url = None
        self.holdings = None
        self.web_url = web_url

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def order_size(self):
        return self._order_size_fiat

    @property
    def email_alerts_on(self):
        return self._email_alerts_on

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
        uuid = webhook_payload.get('order_id')  # Unique identifier for deduplication

        # Prevent duplicate webhooks
        async with self.lock:
            if uuid in self.processed_uuids:
                self.logger.info(f"🟡 Duplicate webhook ignored: {uuid}")
                return None
            self.processed_uuids.add(uuid)

        print(self.shared_utils_color.format(f" 🔹 Sending webhook: {webhook_payload}", self.shared_utils_color.MAGENTA))


        for attempt in range(1, retries + 1):
            try:
                self.logger.debug(f"➡️ Attempting webhook ({attempt}/{retries}): {webhook_payload}")
                token = (os.getenv("WEBHOOK_TOKEN") or "").strip()
                auth_header_name = os.getenv("WEBHOOK_AUTH_HEADER", "Authorization").strip()  # e.g. "Authorization" or "X-Auth-Token"
                auth_scheme = os.getenv("WEBHOOK_AUTH_SCHEME", "Bearer").strip()  # e.g. "Bearer" or ""

                headers = {"Content-Type": "application/json"}
                if not token:
                    self.logger.error("WEBHOOK_TOKEN is missing; server will reject the webhook")
                else:
                    if auth_header_name.lower() == "authorization":
                        headers["Authorization"] = f"{auth_scheme} {token}".strip()
                    else:
                        headers[auth_header_name] = token

                response = await http_session.post(
                    self.web_url,
                    json=webhook_payload,  # <— let aiohttp encode JSON
                    headers=headers,
                    timeout=45
                )

                # ✅ Success
                if response.status == 200:
                    if webhook_payload['side'] == 'buy':
                        self.logger.order_sent(f"✅ Alert webhook sent successfully: {uuid}")
                    return response

                # 🧼 Delegate structured error handling
                handled = await self.handle_webhook_error(response, webhook_payload)
                if handled:
                    return response

            except asyncio.TimeoutError:
                self.logger.error(f"‼️ Request timeout (attempt {attempt}/{retries}): {webhook_payload}")

            except aiohttp.ClientError as e:
                self.logger.error(f"‼️ Client error (attempt {attempt}/{retries}): {e}")

            # ⏳ Retry logic with exponential backoff
            if attempt < retries:
                sleep_time = delay + random.uniform(0, delay * 0.3)  # Add jitter
                self.logger.debug(f"🔁 Retrying in {sleep_time:.2f} seconds...")
                await asyncio.sleep(sleep_time)
                delay = min(delay * 2, max_delay)

        # ❌ Max retries reached
        self.logger.error(f"❌ Max retries reached for webhook: {uuid}")
        return None

    async def handle_webhook_error(self, response, webhook_payload) -> bool:
        """
        Handles known non-200 webhook response codes.
        Returns True if the error was handled and no retry is needed.
        """
        response_text = await response.text()
        try:
            parsed = json.loads(response_text)
        except Exception:
            parsed = {}

        status = response.status
        uuid = webhook_payload.get('order_id', 'unknown')

        # Convert Enum values to integers for comparison
        code = str(status)

        if code in {"403", "404"}:
            self.logger.error(f"‼️ Non-recoverable error {status}: {response_text}")
            return True

        if code in {
            ValidationCode.INSUFFICIENT_QUOTE.value,
            ValidationCode.INSUFFICIENT_BASE.value,
            "613",  # still raw unless you map this to an Enum later
        }:
            self.logger.warning(f"⚠️ Order blocked by balance or precision constraints ({status}): {parsed.get('message', response_text)}")
            return True

        if code == ValidationCode.SKIPPED_OPEN_ORDER.value:
            pair = parsed.get("details", {}).get("trading_pair", "unknown")
            side = parsed.get("details", {}).get("side", "unknown")
            size = parsed.get("details", {}).get("Order Size", "N/A")
            trigger_info = parsed.get("details", {}).get("trigger", {})
            trigger_source = trigger_info.get("trigger", "unknown")
            trigger_note = trigger_info.get("trigger_note", "")
            condition = parsed.get("condition", "Open order exists")

            self.logger.warning(
                f"⏸️ Skipping {side.upper()} order for {pair} — open order already exists ({status}).\n"
                f"Reason: {condition}\n"
                f"Trigger: {trigger_source} ({trigger_note}) | Requested size: {size}"
            )
            return True

        if code == "612":
            self.logger.warning(f"⚠️ Unable to adjust price for order ({status}): {parsed.get('message', response_text)}")
            return True

        if code == "613":
            self.logger.warning(f"⚠️ Crypto balance > $1.00 — order rejected ({status}): {parsed.get('message', response_text)}")
            return True

        if code in {"618", "622"}:
            self.logger.warning(f"⚠️ Order may be incomplete ({status}): {parsed.get('message', response_text)}")
            return True

        if status == 614 and 'Insufficient balance to sell' in response_text:
            self.logger.info(f"💸 Insufficient balance for {webhook_payload['pair']} {uuid}.")
            return True

        if code == "623":
            self.logger.warning(f"⚠️ Buy conditions not favorable ({status}): {parsed.get('message', response_text)}")
            return True
        if code in {"625"}:
            self.logger.warning(f"⚠️ Skipping order, conditions are not favorable: ({status}): {parsed.get('message', response_text)}")
            return True
        if code in {"429", "500", "503"}:
            short_summary = parsed.get('message') or response_text[:300]
            self.logger.warning(f"⚠️ Recoverable server error ({status}): {short_summary} (Retrying...)")
            return False  # retryable

        # Unknown/unhandled status
        raise Exception(f"Unhandled HTTP error ‼️ {status}: {response_text}")

