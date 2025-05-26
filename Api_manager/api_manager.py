import asyncio
import time
from contextlib import asynccontextmanager
from http.client import RemoteDisconnected
from inspect import stack

from aiohttp import ClientConnectionError
from ccxt.base.errors import RequestTimeout, BadSymbol, RateLimitExceeded, ExchangeError, InvalidOrder


class ApiRateLimiter:
    def __init__(self, burst, rate):
        self.tokens = burst
        self.burst = burst
        self.rate = rate
        self.last_time = asyncio.get_event_loop().time()

    async def wait(self):
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self.last_time
        self.last_time = current_time

        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if self.tokens < 1:
            await asyncio.sleep((1 - self.tokens) / self.rate)
            self.tokens = 0
        else:
            self.tokens -= 1


class ApiCallContext:
    def __init__(self, api_manager):
        self.api_manager = api_manager

    @asynccontextmanager
    async def limit(self, endpoint_type):
        semaphore = self.api_manager.get_semaphore(endpoint_type)
        async with semaphore:
            yield


class ApiManager:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, exchange_client, logger_manager, alert_system, burst=10, rate=1):
        """Ensures only one instance exists."""
        if cls._instance is None:
            cls._instance = cls(exchange_client, logger_manager, alert_system, burst, rate)
        return cls._instance

    def __init__(self, exchange_client, logger_manager, alert_system, burst=10, rate=1):
        if ApiManager._instance is not None:
            raise Exception("ApiManager is a singleton and has already been initialized!")

        self.exchange = exchange_client
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.alert_system = alert_system
        self.rate_limiter = ApiRateLimiter(burst, rate)
        self.semaphores = {
            'public': asyncio.Semaphore(9),
            'private': asyncio.Semaphore(14),
            'ohlcv': asyncio.Semaphore(5),
            'orders': asyncio.Semaphore(10),
            'fills': asyncio.Semaphore(9),
            'default': asyncio.Semaphore(2)
        }
        self.circuit_breaker_open = False
        self.circuit_breaker_reset_time = 0
        self.consecutive_failures = 0
        self.max_failures_before_tripping = 3

    def get_semaphore(self, endpoint_type):
        return self.semaphores.get(endpoint_type, asyncio.Semaphore(1))

    async def handle_circuit_breaker(self):
        if self.circuit_breaker_open and time.time() > self.circuit_breaker_reset_time:
            self.circuit_breaker_open = False
            self.consecutive_failures = 0
            self.logger.info("Circuit breaker reset; API calls resumed.")
        elif self.circuit_breaker_open:
            self.logger.error("Circuit breaker open, pausing API calls.")
            await asyncio.sleep(300)

    async def ccxt_api_call(self, func, endpoint_type, *args, currency=None,  **kwargs):
        await self.handle_circuit_breaker()
        if self.circuit_breaker_open:
            return None

        retries = 3
        initial_delay = 1
        max_delay = 60
        delay = initial_delay

        async with ApiCallContext(self).limit(endpoint_type):
            await self.rate_limiter.wait()
            for attempt in range(1, retries + 1):
                try:
                    caller_function_name = stack()[1].function
                    if caller_function_name == 'fetch_bids_asks':
                        caller_function_name = stack()[2].function
                    self.logger.debug(f"Attempt {attempt} for {func.__name__} from {caller_function_name}")
                    # Add delay for public endpoints
                    if endpoint_type == 'public':
                        await asyncio.sleep(0.1)  # Enforce 10 requests/second
                    #print(f"‼️ Verbose: *args: {args}, **kwargs: {kwargs}")
                    response = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
                    self.consecutive_failures = 0 # reset consecutive failures
                    if response is None:
                        self.logger.error(
                            f"� CCXT API returned None for {func.__name__} | Args: {args} | Kwargs: {kwargs}")

                    return response

                except (ClientConnectionError, RemoteDisconnected) as e:
                    print(f'{caller_function_name}')
                    wait_time = min(delay * (2 ** attempt), max_delay)
                    self.logger.error(f"Network error on attempt {attempt}: {e}, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)

                except (RequestTimeout, RateLimitExceeded, BadSymbol) as e:
                    self.logger.error(f"{func.__name__} raised an API call error {type(e).__name__} on attempt"
                                           f" {attempt}: {e}", exc_info=True)
                    if isinstance(e, RateLimitExceeded):
                        await self._handle_rate_limit_exceeded(func.__name__,e)

                except IndexError as e:
                    self.logger.error(f"{func.__name__}: IndexError - {e}", exc_info=True)
                    return None
                except InvalidOrder as e:
                    self.logger.error(f"{func.__name__}: Invalid order - {e}", exc_info=True)
                    return None
                except ExchangeError as e:
                    error_message = str(e)
                    if 'could not find account id' in error_message and currency:
                        return False
                    elif 'coinbase does not have currency code' in error_message:
                        return False
                    elif 'could not find account id for' in error_message:
                        return False
                    elif self._is_rate_limit_exceeded(e):
                        await self._handle_rate_limit_exceeded(func.__name__,e)
                    elif "circuit breaker open" in error_message.lower():
                        self._trigger_circuit_breaker()
                        return None
                    elif 'internal_server_error' in error_message:
                        # retry with exponential backoff
                        await self._handle_rate_limit_exceeded(func.__name__,e)
                        continue  # Retry after delay
                    elif 'Insufficient balance in source account' in error_message:
                        # **Gracefully handle insufficient balance**
                        self.logger.info(f"Insufficient balance for {func.__name__}. Cannot place order.",
                                              exc_info=False)

                        # Optional: Send an alert if necessary
                        print(f"⚠️ Insufficient balance detected for {func.__name__}. Order could not be placed.")

                        # Return a meaningful response
                        return {'status': 'failed', 'reason': 'insufficient_balance'}
                    elif 'coinbase cancelOrders() has failed' in error_message:
                        self.logger.error(f"Coinbase cancelOrders() has failed: {e}", exc_info=True)
                        return None
                    else:
                        self.logger.error(f"⚠️ Post-only limit buys must be priced below the lowest sell price"
                                                   f"{args}  {func.__name__} from {caller_function_name}",exc_info=True)
                        break
                except asyncio.TimeoutError:
                    print(f'{caller_function_name}')
                    if attempt == retries:
                        self.logger.error("TimeoutError after max retries")
                        break
                    await asyncio.sleep(delay * (2 ** attempt))
                except Exception as e:
                    if attempt == retries:
                        print(f'an exception was raised when the api was called from {caller_function_name}')
                        self.logger.error(f"Unexpected error: {e}", exc_info=True)
                        raise
            self.logger.info(f"API call failed after all retries. {func.__name__}", exc_info=True)
            return None

    def _is_rate_limit_exceeded(self, error):
        return "Rate limit exceeded" in str(error)

    async def _handle_rate_limit_exceeded(self, calling_func, error, attempt=1, delay=1, max_delay=60):
        self.consecutive_failures += 1
        wait_time = min((2 ** attempt) * delay, max_delay)
        self.logger.info(f"Rate-limit error in {calling_func}. Backing off for {wait_time}s.")
        if self.consecutive_failures >= self.max_failures_before_tripping:
            self.alert_system.send_alert(
                f"Repeated rate-limit errors in {calling_func}. Circuit breaker engaged. {error}"
            )
            self._trigger_circuit_breaker()
        else:
            await asyncio.sleep(wait_time)


    def _trigger_circuit_breaker(self):
        self.circuit_breaker_open = True
        self.circuit_breaker_reset_time = time.time() + 300
        self.logger.warning(f"Circuit breaker triggered.", exc_info=True)
