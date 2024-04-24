
import asyncio
import inspect
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
        self.semaphores = {
            'public': asyncio.Semaphore(10),
            'private': asyncio.Semaphore(15),
            'fills': asyncio.Semaphore(10)
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

        retries = 5
        backoff_factor = 0.2
        rate_limit_wait = 1  # seconds

        async with ApiCallContext(self).limit(endpoint_type):
            try:
                for attempt in range(1, retries + 1):  # Start counting attempts from 1
                    try:
                        try:
                            # Check if 'func' is a coroutine function and await it directly
                            if asyncio.iscoroutinefunction(func):
                                return await func(*args, **kwargs)
                            else:
                                # If 'func' is not a coroutine, run it in the executor
                                loop = asyncio.get_event_loop()
                                return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

                        except asyncio.TimeoutError:
                            if attempt == retries:
                                self.log_manager.sighook_logger.error(f"Request timed out after {retries} attempts")
                                break  # Exit the loop if this was the last attempt
                            await asyncio.sleep(backoff_factor * 2 ** (attempt - 1))

                        except BadSymbol as ex:
                            # Handle the case where the symbol is not recognized by the exchange
                            self.log_manager.sighook_logger.info(f'Invalid Symbol: {ex}')
                            return None

                        except RateLimitExceeded as ex:
                            wait_time = min(rate_limit_wait * (2 ** attempt), 60)
                            self.log_manager.sighook_logger.info(f'Rate limit exceeded: Retrying in {wait_time} seconds...')
                            await asyncio.sleep(wait_time)

                        except ExchangeError as ex:
                            if 'Rate limit exceeded' in str(ex):
                                # Dynamic wait time based on the attempt number, considering both standard and burst rates
                                burst_multiplier = 1.5 if attempt <= 3 else 1  # Use burst rate for the first few attempts
                                wait_time = min((2 ** (attempt - 1)) * backoff_factor * burst_multiplier, 60)
                                # Get the name of the calling function using inspect.stack()
                                caller = inspect.stack()[1]
                                caller_function_name = caller.function
                                self.log_manager.sighook_logger.info(f'Rate limit exceeded {caller_function_name}, args: '
                                                                     f'{args}: Retrying in {wait_time} seconds...')
                                await asyncio.sleep(wait_time)
                            elif 'Insufficient funds' in str(ex):
                                self.log_manager.sighook_logger.info(f'Exchange error: {ex}', exc_info=True)
                            elif 'USD/USD' in str(ex):
                                self.log_manager.sighook_logger.debug(f'USD/USD: {ex}', exc_info=True)
                            else:
                                if 'coinbase does not have market symbol' in str(ex):
                                    self.log_manager.sighook_logger.error(f'Exchange error: {ex}')
                                    return None
                                else:
                                    self.log_manager.sighook_logger.error(f'Exchange error: {ex}\nDetails:', exc_info=True)
                                    break  # Break out of the loop for non-rate limit related errors
                            await asyncio.sleep(wait_time)  # await   # Use the calculated wait_time here
                    except IndexError as e:
                        error_details = traceback.format_exc()
                        if args is not None:
                            return None
                        else:
                            self.log_manager.sighook_logger.error(f'IndexError: {args}\nDetails: {e},   {error_details}')
                            self.log_manager.sighook_logger.debug(f'Index error Trading is unavailable for {args}: {e}')

                    except RequestTimeout as timeout_error:
                        max_wait_time = 60  # Maximum wait time of 60 seconds
                        wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                        error_details = traceback.format_exc()
                        self.log_manager.sighook_logger.error(f'Request timeout error: {timeout_error}\nDetails: '
                                                              f'{error_details}')
                        await asyncio.sleep(wait_time)  # await Use the calculated wait_time

                    except Exception as e:
                        max_wait_time = 60  # Maximum wait time of 60 seconds
                        wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                        error_details = traceback.format_exc()
                        self.log_manager.sighook_logger.error(f"Error in API call: {e}\nDetails: {error_details}")
                        await asyncio.sleep(wait_time)  # await   # Use the calculated wait_time here

                return None
            finally:
                # print(f'API call completed for {func.__name__}') # debug
                pass
