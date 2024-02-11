
import functools
import re
import time
import asyncio
import traceback

from ccxt.base.errors import RequestTimeout
from ccxt import ExchangeError  # Import the specific CCXT
# AuthenticationError


class ApiExceptions:
    def __init__(self, logmanager, alerts):
        self.log_manager = logmanager
        self.alerts = alerts

    async def ccxt_api_call(self, func, *args, **kwargs):
        """
        Wrapper function for CCXT api(Coinbase Cloud) calls.

        Parameters:
        - func (callable): The CCXT api(Coinbase Cloud) function to call.
        - *args: Positional arguments to pass to func.
        - **kwargs: Keyword arguments to pass to func.

        Returns:
        - The result of the api(Coinbase Cloud) call, or None if an exception was caught.
        """
        retries = 3
        backoff_factor = 0.3
        rate_limit_wait = 1  # seconds
        for attempt in range(retries):
            try:
                return await func(*args, **kwargs)

            except ExchangeError as ex:
                if 'Rate limit exceeded' in str(ex):
                    max_wait_time = 60  # Maximum wait time of 60 seconds
                    wait_time = min(rate_limit_wait * (2 ** attempt), max_wait_time)
                    self.log_manager.sighook_logger.error(f'Rate limit exceeded: Retrying in {wait_time} seconds...')
                    await asyncio.sleep(wait_time)  # Use the calculated wait_time here
                elif 'Insufficient funds' in str(ex):
                    self.log_manager.sighook_logger.error(f'Exchange error: {ex}')
                elif 'USD/USD' in str(ex):
                    self.log_manager.sighook_logger.debug(f'USD/USD: {ex}')

            except asyncio.TimeoutError as e:
                # Handle timeout specifically
                self.log_manager.sighook_logger.error(f"Timeout occurred: {e}")
                await asyncio.sleep(backoff_factor * (2 ** attempt))
            except IndexError as e:
                self.log_manager.sighook_logger.info(f'Index error Trading is unavailable for {args}: {e}')
                # Add more specific logging or handling here
                return None
            except RequestTimeout as timeout_error:
                self.log_manager.sighook_logger.error(f'Request timeout error: {timeout_error}')
                await asyncio.sleep(backoff_factor * (2 ** attempt))
            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f"Error in API call: {e}\nDetails: {error_details}")
                await asyncio.sleep(backoff_factor * (2 ** attempt))
        return None

    def handle_general_error(self, ex, func):
        error_message = str(ex)
        if 'coinbase createOrder() has failed, check your arguments and parameters' in error_message:
            self.log_manager.sighook_logger.error(f'Error placing limit order: {ex}')
            return 'amend'
        elif 'list index out of range' in error_message:
            self.log_manager.sighook_logger.error(f'List index out of range: {ex}  occured when calling '
                                                  f'{func.__name__}')
            return False
        else:
            self.log_manager.sighook_logger.error(f'Error placing limit order: {ex}')
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
                    except UnauthorizedError as ue:
                        retries += 1
                        time.sleep(backoff_factor * retries)
                        self.log_manager.sighook_logger.error(f'Error placing limit order: {ue}')
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
    def __init__(self, message, loggmanager, errors=None):
        super().__init__(message)
        self.log_manager = loggmanager
        self.errors = errors

    def handle_webhook_error(self, e):
        """Handle errors that occur while processing a webhook request."""
        exception_map = {
            401: AuthenticationError,
            429: RateLimitException,
            400: BadRequestException,
            404: NotFoundException,
            500: InternalServerErrorException,
        }
        extra_error_details = {
            'action': "action",
            'trading_pair': "trading_pair",
            'buy_size': "buy_size",
            'formatted_time': "formatted_time",
        }
        # Extract status code from the exception message
        match = re.search(r'\b(\d{3})\b', str(e))
        status_code = int(match.group(1)) if match else None

        # Map status_code to custom exceptions
        exception_to_raise = exception_map.get(getattr(e, 'status_code', None), UnknownException)

        # Raise the exception and handle it in the except block
        try:
            raise exception_to_raise(
                f"An error occurred with status code: {status_code}, error: {e}",
                extra_error_details)
        except RateLimitException:
            self.log_manager.sighook_logger.error(f'handle_webhook_error: Rate limit hit. "Retrying in 60 seconds..."')
            time.sleep(60)
            # handle_action(action, trading_pair, buy_size, formatted_time)
        except (BadRequestException, NotFoundException, InternalServerErrorException, UnknownException) as ex:
            self.log_manager.sighook_logger.error(f'handle_webhook_error: {ex}. Additional info: '
                                                  f'{getattr(ex, "errors", "N/A")}')
        except Exception as ex:
            self.log_manager.sighook_logger.error(f'handle_webhook_error: An unhandled exception occurred: {ex}. '
                                                  f'Additional info: {getattr(ex, "errors", "N/A")}')
