

class ApiException(Exception):
    """Base class for all API-related exceptions."""
    pass

class CoinbaseAPIError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors

# Authentication and Authorization Exceptions
class AuthenticationError(ApiException):
    """Raised when authentication to the API fails."""
    def __init__(self, message="Authentication failed", errors=None):
        super().__init__(message)
        self.errors = errors


class UnauthorizedError(ApiException):
    """Raised for 401 Unauthorized errors."""
    def __init__(self, message="Unauthorized access", errors=None):
        super().__init__(message)
        self.errors = errors


# Request and Response Errors
class BadRequestException(ApiException):
    """Raised when the request format or parameters are invalid."""
    def __init__(self, message="Bad request error", errors=None):
        super().__init__(message)
        self.errors = errors


class NotFoundException(ApiException):
    """Raised when a requested resource is not found."""
    def __init__(self, message="Resource not found", errors=None):
        super().__init__(message)
        self.errors = errors


class InternalServerErrorException(ApiException):
    """Raised for 500 internal server errors."""
    def __init__(self, message="Internal server error", errors=None):
        super().__init__(message)
        self.errors = errors


# Rate Limit and Circuit Breaker Exceptions
class RateLimitException(ApiException):
    """Raised when the API rate limit is exceeded."""
    def __init__(self, message="Rate limit exceeded", errors=None):
        super().__init__(message)
        self.errors = errors


class CircuitBreakerOpenException(ApiException):
    """Raised when the circuit breaker is open to prevent further calls."""
    def __init__(self, message="Circuit breaker is open", errors=None):
        super().__init__(message)
        self.errors = errors


# Specific Trading and Data Exceptions
class InsufficientFundsException(ApiException):
    """Raised when there are not enough funds to complete a trade."""
    def __init__(self, message="Insufficient funds", errors=None):
        super().__init__(message)
        self.errors = errors


class BadSymbolException(ApiException):
    """Raised for invalid or unsupported trading symbols."""
    def __init__(self, message="Invalid trading symbol", errors=None):
        super().__init__(message)
        self.errors = errors


class SizeTooSmallException(ApiException):
    """Raised when the order size is below the allowed minimum."""
    def __init__(self, message="Order size is too small", errors=None):
        super().__init__(message)
        self.errors = errors


class ProductIDException(ApiException):
    """Raised when an unknown or invalid product ID is used."""
    def __init__(self, message="Invalid product ID", errors=None):
        super().__init__(message)
        self.errors = errors


class MaintenanceException(ApiException):
    """Raised when the server is in maintenance mode and cannot process requests."""
    def __init__(self, message="Server maintenance in progress", errors=None):
        super().__init__(message)
        self.errors = errors


class EmptyListException(ApiException):
    """Raised when an expected data list is empty, possibly due to data unavailability."""
    def __init__(self, message="Expected list is empty", errors=None):
        super().__init__(message)
        self.errors = errors


class UnknownException(ApiException):
    """Raised for any unknown or unhandled exception."""
    def __init__(self, message="An unknown error occurred", errors=None):
        super().__init__(message)
        self.errors = errors


# Retry and Backoff Exceptions
class AttemptedRetriesException(ApiException):
    """Raised after all retry attempts have failed."""
    def __init__(self, message="All retry attempts failed", errors=None):
        super().__init__(message)
        self.errors = errors


# Specific to Order Types
class PostOnlyModeException(ApiException):
    """Raised when an order is rejected due to 'post-only' mode requirements."""
    def __init__(self, message="Order rejected: post-only mode requirement not met", errors=None):
        super().__init__(message)
        self.errors = errors


class PriceTooAccurateException(ApiException):
    """Raised when the price precision exceeds allowed limits."""
    def __init__(self, message="Price precision too accurate", errors=None):
        super().__init__(message)
        self.errors = errors
