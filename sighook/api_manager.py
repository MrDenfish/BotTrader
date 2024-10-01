
import asyncio
import random
import time
from inspect import stack
import traceback
from contextlib import asynccontextmanager
from ccxt.base.errors import RequestTimeout, BadSymbol, RateLimitExceeded, ExchangeError


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

        # Refill tokens based on elapsed time
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)

        if self.tokens < 1:
            # Wait for enough tokens to accumulate
            await asyncio.sleep((1 - self.tokens) / self.rate)
            self.tokens = 0
        else:
            self.tokens -= 1


class ApiCallContext:
    def __init__(self, api_exceptions):
        self.api_exceptions = api_exceptions

    @asynccontextmanager
    async def limit(self, endpoint_type):
        semaphore = self.api_exceptions.get_semaphore(endpoint_type)
        async with semaphore:
            yield


class ApiExceptions:

    def __init__(self, logmanager, alerts):
        self.log_manager = logmanager
        self.alerts = alerts
        self.rate_limiter = ApiRateLimiter(burst=10, rate=1)  # Adjust burst and rate as needed
        self.request_count = 0
        self.semaphores = {
            'public': asyncio.Semaphore(9),
            'private': asyncio.Semaphore(14),
            'fills': asyncio.Semaphore(9),
            'default': asyncio.Semaphore(1)  # Fallback to a default semaphore
        }
        self.circuit_breaker_open = False
        self.circuit_breaker_reset_time = 0
        self.consecutive_failures = 0
        self.max_failures_before_tripping = 3


    def get_semaphore(self, endpoint_type):
        return self.semaphores.get(endpoint_type, asyncio.Semaphore(1))  # Fallback to a default semaphore

    async def retry_with_backoff(self, delay, retries, max_delay, reason):
        """Helper function for retry logic with exponential backoff."""
        for attempt in range(retries):
            await self.exponential_backoff(delay, max_delay, attempt)
            self.log_manager.warning(f"Retrying due to {reason}... attempt {attempt + 1}")
            delay = min(max_delay, delay * 2)  # Exponential backoff logic

    async def handle_circuit_breaker(self):
        """Handles the circuit breaker logic."""
        if self.circuit_breaker_open:
            if time.time() > self.circuit_breaker_reset_time:
                self.circuit_breaker_open = False
                self.consecutive_failures = 0  # Reset failure count only after cooldown
                self.log_manager.info("Circuit breaker reset, resuming API calls.")
            else:
                self.log_manager.error("Circuit breaker open, pausing API calls.")
                await asyncio.sleep(300)  # 5-minute cool down

    @staticmethod
    async def exponential_backoff(base_delay, max_delay, attempt):
        """Applies exponential backoff with jitter."""
        delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, 1)
        await asyncio.sleep(delay)

    async def ccxt_api_call(self, func, endpoint_type, *args, **kwargs):  # async
        """
        Wrapper function for CCXT api(Coinbase Cloud) calls.

        Parameters:
        - func (callable): The CCXT api(Coinbase Cloud) function to call.
        - endpoint_type (str): The type of endpoint ('public', 'private', 'fills').
        - *args: Positional arguments to pass to func.
        - **kwargs: Keyword arguments to pass to func.

        Returns:
        - The result of the api(Coinbase Cloud) call, or None if an exception was caught.
        """

        # Check circuit breaker early
        await self.handle_circuit_breaker()
        if self.circuit_breaker_open:
            return None

        if self.semaphores is None:
            self.log_manager.error(f"Unknown endpoint type: {endpoint_type}")
            return None

        retries = 3
        initial_delay = 1
        max_delay = 60
        delay = initial_delay  # seconds

        async with ApiCallContext(self).limit(endpoint_type):
            try:
                response = None
                for attempt in range(retries):
                    try:
                        caller = stack()[1]
                        caller_function_name = caller.function

                        self.log_manager.debug(f"Attempt {attempt + 1} for {func.__name__}, waiting {delay} seconds")
                        self.log_manager.debug(f"Calling {func.__name__} from {caller_function_name}")

                        await self.rate_limiter.wait()  # Use rate limiter to control the rate of API calls

                        if asyncio.iscoroutinefunction(func):
                            self.log_manager.debug(f"Function {func.__name__} is a coroutine")
                            response = await func(*args, **kwargs)
                        else:
                            self.log_manager.debug(f"Function {func.__name__} is not a coroutine")
                            loop = asyncio.get_event_loop()
                            response = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

                        self.log_manager.debug(f"Received response for {caller_function_name}: {response}")
                        self.request_count += 1  # Increment the request counter
                        self.log_manager.debug(f"Total requests made: {self.request_count}")

                        return response

                    except asyncio.TimeoutError:
                        self.log_manager.error(f"TimeoutError on attempt {attempt + 1} for {func.__name__}", exc_info=True)
                        if attempt == retries - 1:
                            self.log_manager.error(f"Request timed out after {retries} attempts", exc_info=True)
                            break  # Exit the loop if this was the last attempt
                        await self.retry_with_backoff(delay, retries=5, max_delay=max_delay, reason="TimeoutError")

                    except RateLimitExceeded as ex:
                        if attempt < retries - 1:
                            self.log_manager.warning(f"Rate limit exceeded on attempt {attempt + 1}: {caller_function_name}")
                            await self.retry_with_backoff(delay, retries, max_delay, reason="Rate limit exceeded")
                        else:
                            self.log_manager.error(f"Rate limit exceeded after {retries} attempts.")
                            raise

                    except (BadSymbol, RequestTimeout) as ex:
                        if 'does not have market symbol' in str(ex):
                            self.log_manager.info(f"Bad symbol: {ex}")
                            break  # Exit the loop if symbol is not traded
                        elif attempt < retries - 1:
                            await self.retry_with_backoff(delay, retries, max_delay, reason=str(ex))
                        else:
                            raise

                    except ExchangeError as ex:
                        error_message = str(ex)
                        if 'Rate limit exceeded' in error_message:
                            await self.retry_with_backoff(delay, retries, max_delay, reason="Rate limit exceeded")
                        elif 'timeout' in error_message.lower():
                            await self.retry_with_backoff(delay, retries=5, max_delay=max_delay, reason="TimeoutError")
                        elif 'service unavailable' in error_message.lower():
                            self.consecutive_failures += 1
                            if self.consecutive_failures >= self.max_failures_before_tripping:
                                self.circuit_breaker_open = True
                                self.circuit_breaker_reset_time = time.time() + 300  # 5-minute cool down
                                self.log_manager.warning(
                                    f"Circuit breaker triggered after {self.consecutive_failures} consecutive failures.")
                            return None
                        elif 'Insufficient funds' in error_message:
                            self.log_manager.info(f'Exchange error: {error_message}', exc_info=True)
                        else:
                            self.log_manager.error(f'Exchange error: {ex}\nDetails:', exc_info=True)
                            raise

                    except Exception as ex:
                        if 'symbol' in kwargs:
                            self.log_manager.error(
                                f"Unexpected error:{caller_function_name}, {kwargs['symbol']}, {ex}\nDetails: {traceback.format_exc()}")
                        else:
                            self.log_manager.error(
                                f"Unexpected error:{caller_function_name}, {ex}\nDetails: {traceback.format_exc()}",
                                exc_info=True)
                        raise

            except Exception as e:
                self.log_manager.error(f"Error in ccxt_api_call: {e}", exc_info=True)
                raise
