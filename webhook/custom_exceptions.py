

from requests.exceptions import ConnectionError, Timeout
import time
import functools

from ccxt.base.errors import AuthenticationError as CCXTAuthenticationError  # Import the specific CCXT AuthenticationError

class ApiExceptions:
    def __init__(self, logmanager, alerts):
        self.log_manager = logmanager
        self.alert_system = alerts

    def ccxt_api_call(self, func, *args, **kwargs):
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

            except RateLimitException as rle:
                self.log_manager.sighook_logger.error(
                    f'Rate limit exceeded: {rle}. Retrying in {rate_limit_wait} seconds...')
                time.sleep(rate_limit_wait)  # Wait before retrying
                rate_limit_wait *= 2  # Increase wait time for subsequent retries

            except (ConnectionError, Timeout, UnauthorizedError) as e:
                self.log_manager.webhook_logger.error(f'{e.__class__.__name__} error: {e}')
                time.sleep(backoff_factor * (2 ** attempt))

            except CCXTAuthenticationError as auth_error:  # Catching the CCXT AuthenticationError
                self.log_manager.webhook_logger.error(f'Authentication error in {func.__name__}: {auth_error}')
                return None
            except CoinbaseAPIError as ez:
                error_message = str(ez)
                return ez, func.__name__
            except Exception as ex:
                # Check if it's a data-related error (adjust as needed)
                if isinstance(ex, (IndexError, DataUnavailableException)):
                    return None  # Return None immediately for data-related errors
                elif 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                    self.handle_general_error(ex)
                    return 'amend'
                else:
                    time.sleep(backoff_factor * (2 ** attempt))
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
