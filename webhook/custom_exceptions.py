

from requests.exceptions import ConnectionError, Timeout
import time
import functools
import asyncio
import traceback

from ccxt.base.errors import RequestTimeout
from ccxt import ExchangeError  # Import the specific CCXT


class ApiExceptions:
    def __init__(self, logmanager, alerts):
        self.log_manager = logmanager
        self.alert_system = alerts

    async def ccxt_api_call(self, func, *args, **kwargs):
        """
                Wrapper function for CCXT api(Coinbase) calls.

                Parameters:
                - func (callable): The CCXT api(Coinbase) function to call.
                - *args: Positional arguments to pass to func.
                - **kwargs: Keyword arguments to pass to func.

                Returns:
                - The result of the api(Coinbase) call, or None if an exception was caught.
                """
        retries = 3
        backoff_factor = 0.3
        rate_limit_wait = 5  # seconds to wait when rate limit is hit

        for attempt in range(retries):
            try:
                return func(*args, **kwargs)

            except ExchangeError as ex:
                if 'Rate limit exceeded' in str(ex):
                    max_wait_time = 60  # Maximum wait time of 60 seconds
                    wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                    self.log_manager.webhook_logger.error(f'Rate limit exceeded: Retrying in {wait_time} seconds...')
                    await asyncio.sleep(wait_time)  # Use the calculated wait_time here
                elif 'Insufficient funds' in str(ex):
                    self.log_manager.webhook_logger.error(f'Exchange error: {ex}')
                elif 'Insufficient balance in source account' in str(ex):
                    error_details = traceback.format_exc()
                    self.log_manager.webhook_logger.error(f'custom exceptions: Error details: {error_details}')
                    self.log_manager.webhook_logger.info(f'Base amount too granular, base amount will be adjusted {ex}')
                    return 'insufficient base balance'
                elif 'USD/USD' in str(ex):
                    self.log_manager.webhook_logger.debug(f'USD/USD: {ex}')
                # error_details = traceback.format_exc() # debug statement
                # self.log_manager.webhook_logger.error(f'try_place_order: Error placing order: {error_details}')
                return None
            except asyncio.TimeoutError as e:
                # Handle timeout specifically
                self.log_manager.webhook_logger.error(f"Timeout occurred: {e}")
                await asyncio.sleep(backoff_factor * (2 ** attempt))
            except IndexError as e:
                self.log_manager.webhook_logger.info(f'Index error Trading is unavailable for {args}: {e}')
                # Add more specific logging or handling here
                return None
            except RequestTimeout as timeout_error:
                self.log_manager.webhook_logger.error(f'Request timeout error: {timeout_error}')
                await asyncio.sleep(backoff_factor * (2 ** attempt))
            except CoinbaseAPIError as ez:
                error_details = traceback.format_exc()
                self.log_manager.webhook_logger.error(f'custom exceptions: Error details: {error_details}')
                self.log_manager.webhook_logger.error(f'custom exceptions: Error placing order: {ez}')
                return ez, func.__name__
            except Exception as ex:
                error_details = traceback.format_exc()
                self.log_manager.webhook_logger.error(f'try_place_order: Error placing order: {error_details}')
                # Check if it's a data-related error (adjust as needed)
                if isinstance(ex, (IndexError, DataUnavailableException)):
                    return None  # Return None immediately for data-related errors
                elif 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                    self.handle_general_error(ex)
                    return 'amend'
                else:
                    await asyncio.sleep(backoff_factor * (2 ** attempt))
                # self.log_manager.webhook_logger.error(f'Error in {func.__name__} with symbol {args[0]}: {ex}')
        return None

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
    def __init__(self, message="Order size is to accurate. Order could not be placed."):
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
