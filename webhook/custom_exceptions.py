import time
import functools
import inspect
import traceback
import logging
from ccxt.base.errors import RequestTimeout, BadSymbol, RateLimitExceeded, ExchangeError
import asyncio
from contextlib import asynccontextmanager
from contextlib import contextmanager


# Dummy implementation for missing classes and imports
class LoggerManager:
    def __init__(self, config, log_dir):
        self.webhook_logger = logging.getLogger(__name__)
        self.webhook_new_logger = logging.getLogger(__name__)


class AlertSystem:
    def __init__(self, log_manager):
        pass


class BotConfig:
    def load_webhook_api_key(self):
        return {'name': 'test', 'privateKey': 'key'}

    @property
    def log_dir(self):
        return "/path/to/log"


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
    def __init__(self, exchange_client, logmanager, alerts):
        self.exchange = exchange_client
        #self.semaphore = semaphore  # Semaphore passed from WebhookListener
        self.log_manager = logmanager
        self.alert_system = alerts
        # self.semaphores = {
        #     'public': asyncio.Semaphore(9),
        #     'private': asyncio.Semaphore(14),
        #     'fills': asyncio.Semaphore(9)
        # }
        self.semaphores = {
            'public': 9,
            'private': 14,
            'fills': 9
        }

    def get_semaphore(self, endpoint_type):
        return self.semaphores.get(endpoint_type, 1)  # asyncio.Semaphore(1)

    async def ccxt_api_call(self, func, endpoint_type, *args, **kwargs):
        """
        Wrapper function for CCXT API calls.

        Parameters:
        - func (callable): The CCXT API function to call.
        - endpoint_type (str): The endpoint type ('private' or 'public').
        - *args: Positional arguments to pass to func.
        - **kwargs: Keyword arguments to pass to func.

        Returns:
        - The result of the API call, or None if an exception was caught.
        """

        if endpoint_type is None:
            self.log_manager.webhook_logger.error(f"Unknown endpoint type: {endpoint_type}")
            return None

        retries = 3
        backoff_factor = 0.5
        rate_limit_wait = 1  # seconds to wait when rate limit is hit
        response = None  # Initialize response before the try block
        try:
            for attempt in range(1, retries + 1):  # Start counting attempts from 1
                try:
                    caller = inspect.stack()[1]  # debug
                    caller_function_name = caller.function  # debug
                    if caller_function_name == 'place_limit_order':
                        self.log_manager.webhook_logger.debug(f'Calling {caller_function_name} with args: {args}')
                    # Check if 'func' is a coroutine function and await it directly

                    # if asyncio.iscoroutinefunction(func):
                    # if False:  # Placeholder for coroutine check
                    #
                    #     #response = await func(*args, **kwargs)
                    #     pass
                    #
                    # else:
                        # Run blocking function in a separate thread
                        # response = await asyncio.to_thread(func, *args, **kwargs)
                    response = func(*args, **kwargs)

                    return response

                except (RequestTimeout, BadSymbol, RateLimitExceeded) as e:
                    self.log_manager.webhook_logger.error(f"Attempt {attempt}: Error during API call "
                                                          f"{type(e).__name__}: {str(e)}")

                    # if isinstance(e, SSLError):
                    #     self.log_manager.webhook_logger.error("SSL Error encountered", exc_info=True)
                    #     return None
                    if isinstance(e, RequestTimeout):
                        self.log_manager.webhook_logger.error("Request Timeout encountered", exc_info=True)
                    if isinstance(e, BadSymbol):
                        self.log_manager.webhook_logger.info(f'Bad Symbol error: {e}')
                        return None

                except ExchangeError as ex:
                    if hasattr(self.exchange, 'last_http_response'):
                        last_response = self.exchange.last_http_response
                        if 'INVALID_LIMIT_PRICE_POST_ONLY' in str(last_response):
                            return 'amend'  # Amend the order
                        elif 'must be greater than minimum amount precision' in str(last_response) or \
                                'must be greater than minimum amount precision' in str(ex):
                            return 'order_size_too_small'

                        elif 'Rate limit exceeded' in str(ex):
                            # Dynamic wait time based on the attempt number, considering both standard and burst rates
                            caller = inspect.stack()[1]
                            caller_function_name = caller.function
                            self.log_manager.webhook_logger.info(
                                f'Rate limit exceeded {caller_function_name}, args: {args}: '
                                f'Retrying in {rate_limit_wait} seconds...')
                            # await self.handle_rate_limit_exceeded(attempt)
                            self.handle_rate_limit_exceeded(attempt)

                        elif 'INSUFFICIENT_FUND' in str(last_response):
                            self.log_manager.webhook_logger.info(f'Exchange error: {ex}', exc_info=True)
                            return 'insufficient_funds'
                        elif 'USD/USD' in str(ex):
                            self.log_manager.webhook_logger.debug(f'USD/USD: {ex}', exc_info=True)
                        else:
                            if 'coinbase does not have market symbol' in str(ex):
                                self.log_manager.webhook_logger.error(f'Exchange error: {ex}')
                                return None
                            else:
                                caller = inspect.stack()[1]
                                if hasattr(self.exchange, 'last_http_response'):
                                    self.log_manager.webhook_logger.debug(
                                        f"Last HTTP Response: {self.exchange.last_http_response}")
                                self.log_manager.webhook_logger.error(f"Exchange error details: {vars(ex)}")
                                self.log_manager.webhook_logger.error(
                                    f'Exchange error during {caller.function}  params: {args}, {kwargs}. Error: {ex}')
                                self.log_manager.webhook_logger.error(f'Exchange error: {ex}\nDetails: '
                                                                      f'{caller.function}', exc_info=True)
                                break  # Break out of the loop for non-rate limit related errors
                # except asyncio.TimeoutError:
                except TimeoutError:
                    if attempt == retries:
                        self.log_manager.webhook_logger.error(f"Request timed out after {retries} attempts")
                        break  # Exit the loop if this was the last attempt
                    # await asyncio.sleep(backoff_factor * 2 ** (attempt - 1))
                    time.sleep(backoff_factor * 2 ** (attempt - 1))

                except RateLimitExceeded as ex:
                    self.log_manager.webhook_logger.error(f"Rate limit exceeded: {ex}")
                    # await self.handle_rate_limit_exceeded(attempt)
                    self.handle_rate_limit_exceeded(attempt)
                except IndexError as e:
                    error_details = traceback.format_exc()
                    if args is not None:
                        return None
                    else:
                        self.log_manager.webhook_logger.error(f'IndexError: {args}\nDetails: {e},   {error_details}')
                        self.log_manager.webhook_logger.debug(f'Index error Trading is unavailable for {args}: {e}')

                except RequestTimeout as timeout_error:
                    max_wait_time = 60  # Maximum wait time of 60 seconds
                    wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                    error_details = traceback.format_exc()
                    self.log_manager.webhook_logger.error(
                        f'Request timeout error: {timeout_error}\nDetails: {error_details}')
                    # await asyncio.sleep(wait_time)  # Use the calculated wait_time
                    time.sleep(wait_time)

                except Exception as e:
                    max_wait_time = 60  # Maximum wait time of 60 seconds
                    wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                    error_details = traceback.format_exc()
                    self.log_manager.webhook_logger.error(f"Error in API call: {e}\nDetails: {error_details}")
                    # await asyncio.sleep(wait_time)  # Use the calculated wait_time here
                    time.sleep(wait_time)

            return None
        finally:
            pass

    # async def handle_rate_limit_exceeded(self, attempt):
    def handle_rate_limit_exceeded(self, attempt):
        """Handle actions required when a rate limit is exceeded."""
        burst_multiplier = 1.5 if attempt <= 3 else 1  # Use burst rate for the first few attempts
        backoff_factor = 0.2
        wait_time = min((2 ** (attempt - 1)) * backoff_factor * burst_multiplier, 60)
        self.log_manager.webhook_logger.info(f'Rate limit exceeded, retrying in {wait_time} seconds...')
        # await asyncio.sleep(wait_time)
        time.sleep(wait_time)

    def handle_general_error(self, ex):
        error_message = str(ex)
        if 'coinbase createOrder() has failed, check your arguments and parameters' in error_message:
            self.log_manager.webhook_logger.debug(f'Error placing limit order: {ex}')
            return 'amend'
        else:
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
            return False


class UnauthorizedError(Exception):
    """Custom exception class for 401 Unauthorized errors."""

    def __init__(self, logmanager, alerts):
        self.log_manager = logmanager
        self.alert_system = alerts

    def retry_on_401(self, max_retries=3, backoff_factor=1.0):
        def decorator_retry(func):
            @functools.wraps(func)
            def wrapper_retry(*args, **kwargs):
                retries = 0
                while retries < max_retries:
                    try:
                        return func(*args, **kwargs)
                    except UnauthorizedError as e:
                        retries += 1
                        time.sleep(backoff_factor * retries)
                        self.log_manager.webhook_new_logger.error(f'Error placing limit order: {e}')
                        # Add token refresh logic here if necessary
                    except Exception as e:
                        raise e
                return None

            return wrapper_retry

        return decorator_retry


class DataUnavailableException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class AuthenticationError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class InsufficientFundsException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class CoinbaseAPIError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class SizeTooSmallException(Exception):
    def __init__(self, message="Order size is too accurate. Order could not be placed."):
        self.message = message
        super().__init__(self.message)


class ProductIDException(Exception):
    def __init__(self, message="Product ID is not known. Order could not be placed."):
        self.message = message
        super().__init__(self.message)


class MaintenanceException(Exception):
    def __init__(self, message="Server is experiencing a maintenance issue. Order could not be placed."):
        self.message = message
        super().__init__(self.message)


class RateLimitException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class BadRequestException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class NotFoundException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class InternalServerErrorException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class EmptyListException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class UnknownException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class AttemptedRetriesException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class CustomExceptions:
    def __init__(self, logmanager, coms):
        self.log_manager = logmanager
        self.coms = coms


class PostOnlyModeException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors


class PriceTooAccurateException(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors
