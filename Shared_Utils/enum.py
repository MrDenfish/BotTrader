from enum import Enum

class ExitCondition(Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    UNKNOWN = "unknown"

class ValidationCode(Enum):
    """" 200–299 for success responses

        400–499 for client-side HTTP errors (bad input, unauthorized)

        500–599 for server-side errors (exceptions, DB fail)

        600+ for custom app-level codes that avoid collision with standard HTTP spec"""

    SUCCESS = "200"
    NO_ORDER_PLACED = "601"
    SKIPPED_OPEN_ORDER = "600"
    SKIPPED_HODL = "602"
    UNABLE_TO_ADJUST_PRICE = "612"
    INSUFFICIENT_QUOTE = "613"
    INSUFFICIENT_BASE = "614"
    ORDER_NOT_CANCELED = "615"
    BAD_REQUEST = "400"
    UNAUTHORIZED = "401"
    INVALID_JSON_FORMAT = "402"
    FORBIDDEN = "403"
    MISSING_UUID = "410"
    DUPLICATE_UUID = "411"
    MISSING_ACTION = "412"
    PRICE_BELOW_ASK = "618"
    ORDER_BLOCKED_EXISTING_OPEN_ORDER = "611"
    ORDER_BLOCKED_AMOUNT_MISSING_OR_ZERO = "700"
    PRECISION_ERROR = "622"
    BUY_CONDITIONS_NOT_FAVORABLE = "623"
    ORDER_BUILD_FAILED = "625"
    RATE_LIMIT = "429"
    HODL_REJECT = "624"
    INTERNAL_SERVER_ERROR = "500"
    INVALID_REQUEST = "501"
    UNKNOWN_ERROR = "520"
    NETWORK_ERROR = "521"
    TIMEOUT = "522"
    UNHANDLED_EXCEPTION = "599"
