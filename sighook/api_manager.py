
import asyncio
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

    def get_semaphore(self, endpoint_type):
        return self.semaphores.get(endpoint_type, asyncio.Semaphore(1))  # Fallback to a default semaphore

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

        if self.semaphores is None:
            self.log_manager.sighook_logger.error(f"Unknown endpoint type: {endpoint_type}")
            return None

        retries = 3
        initial_delay = 1
        max_delay = 60
        delay = initial_delay  # seconds

        async with ApiCallContext(self).limit(endpoint_type):
            try:
                response = None
                for attempt in range(retries):  # Start counting attempts from 1
                    try:
                        caller = stack()[1]
                        caller_function_name = caller.function
                        self.log_manager.sighook_logger.debug(
                            f"Attempt {attempt + 1} for {func.__name__}, waiting {delay} seconds")
                        self.log_manager.sighook_logger.debug(f"Calling {func.__name__} from {caller_function_name}")

                        await self.rate_limiter.wait()  # Use rate limiter to control the rate of API calls

                        if asyncio.iscoroutinefunction(func):
                            self.log_manager.sighook_logger.debug(f"Function {func.__name__} is a coroutine")

                            response = await func(*args, **kwargs)
                            self.log_manager.sighook_logger.debug(
                                f"Received response for {caller_function_name}: {response}")
                            if response:
                                self.log_manager.sighook_logger.debug(f"Response is not None for {caller_function_name}")
                            elif 'symbol' in kwargs and kwargs['symbol'] is None:
                                self.log_manager.sighook_logger.error(f"Response is None for {caller_function_name} "
                                                                      f"**kwargs: {kwargs})")
                                break  # Break out of the loop if the symbol is None

                        else:
                            self.log_manager.sighook_logger.debug(f"Function {func.__name__} is not a coroutine")
                            loop = asyncio.get_event_loop()
                            response = await loop.run_in_executor(None, lambda: func(*args, **kwargs))
                            self.log_manager.sighook_logger.debug(
                                f"Received response for {caller_function_name}: {response}")
                        self.request_count += 1  # Increment the request counter
                        self.log_manager.sighook_logger.debug(
                            f"Total requests made: {self.request_count}")  # Log the counter
                        return response
                    except asyncio.TimeoutError:
                        self.log_manager.sighook_logger.error(f"TimeoutError on attempt {attempt} for {func.__name__}",
                                                              exc_info=True)
                        if attempt == retries:
                            self.log_manager.sighook_logger.error(f"Request timed out after {retries} attempts",
                                                                  exc_info=True)
                            break  # Exit the loop if this was the last attempt
                        self.log_manager.sighook_logger.warning(
                            f"Timeout error, retrying in {delay} seconds")
                        await asyncio.sleep(delay)

                    except RateLimitExceeded as ex:
                        if attempt < retries - 1:
                            self.log_manager.sighook_logger.warning(
                                f'Rate limit exceeded on attempt # {attempt + 1}: {caller_function_name}, semaphore:'
                                f' {self.semaphores} Retrying in'
                                f' {delay} seconds...{ex}', exc_info=True)
                            self.log_manager.verbose_logger.verbose(
                                f"Rate limit exceeded at attempt {attempt}. Function: {func.__name__} in "
                                f"{caller_function_name}, Args: {args}, Kwargs: {kwargs}", exc_info=True)
                            await asyncio.sleep(delay)
                            delay = min(max_delay, delay + 1)
                        else:
                            self.log_manager.sighook_logger.error(f"Rate limit exceeded after {retries} attempts.")
                            raise

                    except (BadSymbol, RequestTimeout) as ex:
                        self.log_manager.sighook_logger.error(f"Exception {ex} on attempt {attempt + 1} for {func.__name__}",
                                                              exc_info=True)
                        if 'does not have market symbol' in str(ex):
                            self.log_manager.sighook_logger.info(f"Bad symbol: {ex}", exc_info=True)
                            break  # Exit the loop if symbol is not traded

                        elif attempt < retries - 1:
                            await asyncio.sleep(delay)
                            delay = min(max_delay, delay + 1)
                        else:
                            raise

                    except ExchangeError as ex:
                        error_message = str(ex)
                        if 'Rate limit exceeded' in error_message:
                            if attempt < retries - 1:
                                self.log_manager.sighook_logger.error(
                                    f"ExchangeError on attempt {attempt} Function: {func.__name__} in "
                                    f"{caller_function_name},Args: {args}, Kwargs: {kwargs}: {error_message}", exc_info=True)
                                delay = min(max_delay, delay + 1)
                        if 'circuit breaker' in error_message or '503' in error_message:
                            wait_time = 600  # 10 minutes to cool off
                            self.log_manager.sighook_logger.warning(
                                f"Service unavailable (circuit breaker), retrying in {wait_time} seconds...", exc_info=True)
                        elif 'Rate limit exceeded' in error_message:
                            print(f'{args}, {kwargs}')
                            if attempt < retries - 1:
                                self.log_manager.sighook_logger.info(
                                    f"ExchangeError: {func.__name__} rate limit exceeded, retrying in {delay} "
                                    f"seconds...{ex}")
                                await asyncio.sleep(delay)
                                delay = min(max_delay, delay + 1)
                            else:
                                self.log_manager.sighook_logger.error(
                                    f"ExchangeError rate limit exceeded after {retries} attempts.")
                                raise
                        elif 'Insufficient funds' in error_message:
                            self.log_manager.sighook_logger.info(f'Exchange error: {error_message}', exc_info=True)
                        elif 'USD/USD' in error_message:
                            self.log_manager.sighook_logger.debug(f'USD/USD: {error_message}', exc_info=True)
                        else:
                            self.log_manager.sighook_logger.error(f'Exchange error: {ex}\nDetails:', exc_info=True)
                            raise
                        await asyncio.sleep(wait_time)

                    except Exception as ex:
                        if 'symbol' in kwargs:
                            self.log_manager.sighook_logger.error(f"Unexpected error:{caller_function_name},  "
                                                                  f"{kwargs['symbol']}, {ex}\nDetails: "
                                                                  f"{traceback.format_exc()}")
                        else:
                            self.log_manager.sighook_logger.error(f"Unexpected error:{caller_function_name}, {ex}\nDetails:"
                                                                  f" {traceback.format_exc()}", exc_info=True)

                        raise

            except Exception as e:
                self.log_manager.sighook_logger.error(f"Error in ccxt_api_call: {e}", exc_info=True)
                raise
