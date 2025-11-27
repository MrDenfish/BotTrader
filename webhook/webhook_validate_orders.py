
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
from decimal import Decimal
from decimal import InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional, Union, Dict, Callable

import pandas as pd
import decimal
import json
import re
from Shared_Utils.enum import ValidationCode
from Config.config_manager import CentralConfig as Config
from Shared_Utils.logger import get_logger

# Module-level logger for order validation
_logger = get_logger('webhook', context={'component': 'validate_orders'})


@dataclass
class OrderData:
    trading_pair: str
    time_order_placed: Union[datetime, None]
    type: str
    order_id: str
    side: str
    base_currency: str
    quote_currency: str
    usd_balance: Decimal
    base_decimal: int
    quote_decimal: int
    quote_increment:Decimal
    highest_bid: Decimal
    lowest_ask: Decimal
    maker: Decimal
    taker: Decimal
    spread: Decimal
    open_orders: Union[pd.DataFrame, None] = None
    status: str = 'UNKNOWN'
    source: str = 'UNKNOWN'
    trigger: Dict[str, str] = field(default_factory=lambda: {"trigger": "UNKNOWN", "trigger_note": ""})
    base_avail_balance: Decimal = Decimal('0')
    total_balance_crypto: Decimal = Decimal('0')  # spot_position
    available_to_trade_crypto: Decimal = Decimal('0')
    usd_avail_balance: Decimal = Decimal('0')
    order_amount_fiat: Decimal = Decimal('0')
    order_amount_crypto: Decimal = Decimal('0')
    price: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal('0')  # spot_position
    limit_price: Decimal = Decimal('0')
    filled_price: Optional[Decimal] = None
    spread_pct: Optional[Decimal] = None
    atr_pct: Optional[Decimal] = None
    average_price: Optional[Decimal] = None
    adjusted_price: Optional[Decimal] = None
    adjusted_size: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    avg_quote_volume:Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    parent_id: Optional[str] = None  # For tracking parent orders in OCO or complex orders

    def get_effective_amount(self) -> Decimal:
        """
                Returns the correct value (crypto or fiat) depending on order side.
                - Buy orders use fiat amount.
                - Sell orders use crypto amount.
                """
        if self.side.lower() == 'buy':
            return self.order_amount_fiat or Decimal('0')
        return self.order_amount_crypto or Decimal('0')


    @property
    def is_valid(self) -> bool:
        return (self.get_effective_amount() > 0 and self.adjusted_price is not None)

    def to_dict(self, serializer: Callable = None) -> dict:
        from dataclasses import asdict

        def default_serializer(obj):
            if isinstance(obj, Decimal):
                return str(obj)
            elif isinstance(obj, pd.DataFrame):
                return obj.to_dict(orient="records")
            elif hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        raw = asdict(self)
        if isinstance(raw.get("open_orders"), pd.DataFrame):
            raw["open_orders"] = raw["open_orders"].to_dict(orient="records")

        return json.loads(json.dumps(raw, default=serializer or default_serializer))

    @classmethod
    def from_dict(cls, data: dict) -> 'OrderData':
        try:
            def get_decimal(key_path, default='0'):
                try:
                    val = data
                    for key in key_path if isinstance(key_path, list) else [key_path]:
                        val = val[key]
                    return Decimal(val)
                except (KeyError, TypeError, ValueError, decimal.InvalidOperation):
                    return Decimal(default)

            def get_first_nonzero_decimal(*keys, default='0'):
                for key in keys:
                    val = get_decimal(key)
                    if val != 0:
                        return val
                return Decimal(default)

            def extract_base_quote(pair: str):
                if not pair:
                    return '', ''
                split_pair = pair.replace('-', '/').split('/')
                return (split_pair[0], split_pair[1]) if len(split_pair) == 2 else ('', '')

            product_id = data.get('trading_pair') or data.get('symbol') or data.get('product_id', '')
            base_currency, quote_currency = extract_base_quote(product_id)

            quote_decimal = int(data.get("quote_decimal") or 2)
            base_decimal = int(data.get("base_decimal") or 8)
            quote_increment = Decimal("1").scaleb(-quote_decimal)

            # Fallback-resilient values
            amount = get_first_nonzero_decimal(
                'order_amount_crypto',
                'amount',
                'filled_size',
                'cumulative_quantity',
                ['info', 'order_configuration', 'limit_limit_gtc', 'base_size'],
                ['info', 'filled_size'],
            )

            price = get_first_nonzero_decimal(
                'price',
                'avg_price',
                'limit_price',
                ['info', 'limit_price'],
                ['info', 'order_configuration', 'limit_limit_gtc', 'limit_price'],
            )

            status = data.get('status', 'UNKNOWN')

            # Debug anomaly detection
            if amount == 0 and status.upper() == "FILLED":
                _logger.warning(
                    "FILLED order has amount=0",
                    extra={
                        'order_id': data.get('order_id'),
                        'raw_keys': list(data.keys()),
                        'status': status
                    }
                )
            info_keys = data.get('info', {})
            order_config = info_keys.get('order_configuration', {})
            return cls(
                trading_pair=product_id,
                time_order_placed=data.get('time_order_placed', None),
                type=data.get('type', 'limit'),
                order_id=data.get('order_id') or order_config.get('order_id', 'UNKNOWN'),
                side=data.get('side') or data.get('order_side', 'buy'),
                filled_price=get_decimal(['filled_price']) or price,
                base_currency=base_currency,
                quote_currency=quote_currency,
                usd_balance=get_decimal('usd_balance'),
                base_decimal=base_decimal,
                quote_decimal=quote_decimal,
                quote_increment=quote_increment,
                highest_bid=get_decimal('highest_bid'),
                lowest_ask=get_decimal('lowest_ask'),
                maker=get_decimal('maker_fee'),
                taker=get_decimal('taker_fee'),
                spread=get_decimal('spread'),
                open_orders=data.get('open_orders', None),
                status=status,
                source=data.get('source', 'UNKNOWN'),
                trigger=data.get("trigger") or {"tp_sl_flag": data.get("tp_sl_flag", False)},
                base_avail_balance=get_decimal('base_avail_balance'),
                total_balance_crypto=get_decimal('total_balance_crypto'),
                available_to_trade_crypto=get_decimal('available_to_trade_crypto'),
                usd_avail_balance=get_decimal('usd_avail_balance'),
                order_amount_fiat=get_decimal('order_amount_fiat') or get_decimal('filled_value'),
                order_amount_crypto=amount,
                price=price,
                cost_basis=get_decimal('cost_basis'),
                limit_price=get_decimal('limit_price'),
                average_price=get_decimal('average_price') or get_decimal('avg_price'),
                adjusted_price=get_decimal('adjusted_price'),
                adjusted_size=get_decimal('adjusted_size'),
                stop_loss_price=get_decimal('stop_loss_price') or get_decimal('stop_price'),
                take_profit_price=get_decimal('take_profit_price'),

                avg_quote_volume=get_decimal('avg_quote_volume'),
                volume_24h=get_decimal('volume_24h'),
                parent_id=data.get("parent_order_id"),
            )

        except Exception as e:
            _logger.error(
                "Error creating OrderData from dict",
                extra={'error': str(e), 'data_keys': list(data.keys()) if data else None},
                exc_info=True
            )
            raise

    def debug_summary(self, verbose: bool = False) -> str:
        """Generate a safe, readable summary of this order for debugging/logging."""
        _logger.debug("=" * 50)
        _logger.debug("DEBUG SUMMARY")

        summary_lines = [f"\n📋 OrderData Summary for {self.trading_pair} [{self.side.upper()}]"]

        for key, value in asdict(self).items():
            if isinstance(value, pd.DataFrame):
                if not value.empty:
                    summary_lines.append(f"� {key}: DataFrame with {len(value)} rows")
                else:
                    summary_lines.append(f"� {key}: (empty DataFrame)")
            elif value is None:
                summary_lines.append(f"⚠️ {key}: None")
            elif isinstance(value, Decimal) and value == 0:
                summary_lines.append(f"� {key}: 0 (zero)")
            else:
                if verbose or key in {"order_id", "trading_pair", "side", "order_amount", "usd_avail_balance"}:
                    summary_lines.append(f"{key}: {value}")

        return "\n".join(summary_lines)

class ValidateOrders:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, order_book, shared_utils_precision, shared_utils_utility, shared_data_manager):
        """
        Singleton method to ensure only one instance of ValidateOrders exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, order_book, shared_utils_precision, shared_utils_utility,
                                shared_data_manager)
        return cls._instance

    def __init__(self, logger_manager, order_book, shared_utils_precision, shared_utils_utility, shared_data_manager):
        """
        Initializes the ValidateOrders instance.
        """
        self.config = Config()
        self.order_book = order_book
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_data_manager = shared_data_manager
        self.logger = logger_manager  # 🙂

        # Only store necessary attributes
        self._min_sell_value = self.config.min_sell_value
        self._min_order_amount_fiat = self.config.min_order_amount_fiat
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._hodl = self.config.hodl  # ✅ Ensure this is used elsewhere
        self._shill_coins = self.config.shill_coins  # ✅ Block buys for shill coins
        self._version = self.config.program_version  # ✅ Ensure this is required

    @property
    def hodl(self):
        return self._hodl

    @property
    def shill_coins(self):
        return self._shill_coins

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})


    @property
    def min_sell_value(self):
        return self._min_sell_value  # Minimum value of a sell order

    @property
    def min_order_amount_fiat(self):
        return self._min_order_amount_fiat  # Minimum order amount

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self.max_value_of_crypto_to_buy_more  # max value of a sell order

    @property
    def version(self):
        return self._version

    def build_order_data_from_validation_result(self, validation_result: dict, order_book_details: dict, precision_data: tuple) -> OrderData:
        try:
            details = validation_result.get("details", {})
            side = details.get("side", "buy")
            source = details.get("source", "reconciled")

            maker_fee, taker_fee, base_deci, quote_deci, quote_increment = (
                self.shared_utils_utility.prepare_order_fees_and_decimals(details, precision_data)
            )
            basics = self.shared_utils_utility.assign_basic_order_fields(details)

            fiat_amt = Decimal(details.get("order_amount_fiat", 0))
            crypto_amt = Decimal(details.get("base_balance", 0))
            order_amount_fiat, order_amount_crypto = self.shared_utils_utility.initialize_order_amounts(side, fiat_amt, crypto_amt)

            return OrderData(
                source=source,
                time_order_placed=None,
                volume_24h=None,
                trigger=details.get("trigger", {}),
                order_id=details.get("order_id", "UNKNOWN"),
                side=side,
                type=details.get("Order Type", "limit").lower(),
                price=Decimal("0"),
                cost_basis=Decimal("0"),
                limit_price=Decimal(details.get("limit_price", 0)),
                filled_price=Decimal(details.get("average_price", 0)),
                open_orders=details.get("Open Orders", pd.DataFrame()),
                status=details.get("status", "VALID"),
                adjusted_price=Decimal(details.get("adjusted_price", 0)),
                adjusted_size=Decimal(details.get("adjusted_size", 0)),
                stop_loss_price=Decimal(details.get("stop_loss_price", 0)),
                take_profit_price=Decimal(details.get("take_profit_price", 0)),
                maker=maker_fee,
                taker=taker_fee,
                base_decimal=base_deci,
                quote_decimal=quote_deci,
                quote_increment=quote_increment,
                highest_bid=Decimal(order_book_details.get("bid", 0)),
                lowest_ask=Decimal(order_book_details.get("ask", 0)),
                spread=Decimal(order_book_details.get("spread", 0)),
                **basics,
                order_amount_fiat=order_amount_fiat,
                order_amount_crypto=order_amount_crypto,
            )
        except Exception as e:
            raise Exception(f"Error creating OrderData from validation: {e}")

    def validate_order_conditions(self, order_details: OrderData, open_orders) -> dict:
        """
        Validates order conditions based on account balances and active open orders.

        Args:
            order_details (OrderData): Normalized order data.
            open_orders (DataFrame): DataFrame of active open orders.

        Returns:
            dict: Contains validation status, code, and details.
        """
        side = order_details.side
        usd_balance = order_details.usd_avail_balance
        order_size = order_details.get_effective_amount()
        base_balance = order_details.base_avail_balance
        symbol = order_details.trading_pair
        asset = symbol.split('/')[0]

        response_msg = {
            "is_valid": False,
            "error": None,
            "code": ValidationCode.ORDER_BUILD_FAILED.value,
            "message": f"Validation failed for {symbol} order build incomplete",
            "details": {
                "Order Id": None,
                "Order Type": order_details.type,
                "asset": asset,
                "trading_pair": symbol,
                "side": side,
                "usd_balance": usd_balance,
                "base_avail_to_trade": order_details.available_to_trade_crypto,
                "base_balance": base_balance,
                "Order Size": order_size,
                "trigger": order_details.trigger,
                "condition": None
            }
        }

        try:
            if open_orders.empty:
                if side == 'buy' and usd_balance < order_size:
                    response_msg["error"] = "INSUFFICIENT_QUOTE"
                    response_msg["code"] = ValidationCode.INSUFFICIENT_QUOTE.value
                    return response_msg

                if side == 'sell' and base_balance <= 0:
                    response_msg["error"] = "INSUFFICIENT_BASE"
                    response_msg["code"] = ValidationCode.INSUFFICIENT_BASE.value
                    return response_msg

                # HODL check: Block SELL for HODL assets (only buys allowed)
                if side == 'sell' and asset in self.hodl:
                    response_msg["error"] = "HODL_REJECT"
                    response_msg["code"] = ValidationCode.HODL_REJECT.value
                    response_msg["condition"] = f"HODLing {symbol}, sell blocked."
                    return response_msg

                # SHILL_COINS check: Block BUY for shill coins (only sells allowed)
                if side == 'buy' and asset in self.shill_coins:
                    response_msg["error"] = "SHILL_COIN_REJECT"
                    response_msg["code"] = ValidationCode.HODL_REJECT.value  # Reuse HODL_REJECT code
                    response_msg["condition"] = f"{symbol} is a shill coin, buy blocked."
                    response_msg["message"] = f"Skipping {symbol}: shill coin, only sells allowed."
                    return response_msg

                # Anti-duplicate buy logic: Block BUY if already holding > MIN_ORDER_AMOUNT_FIAT
                # This prevents buying more of the same coin and handles crypto dust
                if side == 'buy':
                    # Calculate current position value
                    current_price = order_details.limit_price or order_details.price
                    total_balance_crypto = order_details.total_balance_crypto or Decimal('0')
                    position_value = total_balance_crypto * current_price

                    # If we already hold more than MIN_ORDER_AMOUNT_FIAT, block the buy
                    if position_value >= self.min_order_amount_fiat:
                        response_msg["error"] = "POSITION_EXISTS"
                        response_msg["code"] = ValidationCode.SKIPPED_OPEN_ORDER.value  # Reuse existing code
                        response_msg["condition"] = f"Already holding ${position_value:.2f} worth of {asset} (>= ${self.min_order_amount_fiat})"
                        response_msg["message"] = f"Skipping {symbol}: already holding significant position (${position_value:.2f})."
                        response_msg["details"]["position_value"] = float(position_value)
                        return response_msg

                # ✅ Success case
                response_msg["is_valid"] = True
                response_msg["message"] = "Order validated successfully."
                response_msg["code"] = ValidationCode.SUCCESS.value
                return response_msg

            # 🔁 Order exists for symbol
            if symbol in open_orders.symbol.values:
                response_msg["error"] = "SKIPPED_OPEN_ORDER"
                response_msg["code"] = ValidationCode.SKIPPED_OPEN_ORDER.value
                response_msg["condition"] = f"Open order exists for {symbol}."
                response_msg["message"] = f"Skipping {symbol}: open order already exists."
                return response_msg

            # ✅ No issue, allow order
            response_msg["is_valid"] = True
            response_msg["message"] = "Order validated successfully."
            response_msg["code"] = ValidationCode.SUCCESS.value
            return response_msg

        except KeyError as e:
            response_msg["error"] = f"❌ KEY_ERROR: {e}"
            response_msg["code"] = ValidationCode.INTERNAL_SERVER_ERROR.value
            return response_msg

    @staticmethod
    def build_validate_data(order_details, open_orders, order_book_details):
        """Called from:
        place_order()
        """

        return {
            **order_details,
            'usd_avail_balance': order_details['usd_avail_balance'],
            'base_avail_balance': order_details['base_avail_balance'],
            'base_currency': order_details['trading_pair'].split('/')[0],
            "base_avail_to_trade": order_details.get('available_to_trade_crypto', ''),
            'available_to_trade_crypto':order_details['available_to_trade_crypto'],
            'order_amount': order_details['order_amount'],
            'highest_bid': order_book_details['highest_bid'],
            'lowest_ask': order_book_details['lowest_ask'],
            'spread': order_book_details['spread'],
            'open_orders': open_orders
        }

    def validate_orders(self, data: OrderData) -> tuple:
        """
        Validate whether an order should be placed, considering open orders and balances.

        Args:
            data (OrderData): Dataclass with trading pair, balances, order details, and open orders.

        Returns:
            tuple: (base_balance, base_balance_value, valid_order, condition)
        """

        def to_decimal(value, default='0'):
            try:
                return Decimal(value)
            except (InvalidOperation, TypeError):
                return Decimal(default)

        try:
            trading_pair = data.trading_pair
            base_currency, quote_currency = re.split(r'[-/]', trading_pair)
            side = data.side.lower()

            # Extract and normalize values
            usd_avail = to_decimal(data.usd_avail_balance)
            base_avail = to_decimal(data.base_avail_balance)
            available_to_trade = to_decimal(data.available_to_trade_crypto)
            highest_bid = to_decimal(data.highest_bid)
            lowest_ask = to_decimal(data.lowest_ask)
            order_size = data.get_effective_amount()
           # order_size = data.order_amount_fiat if side == 'buy' else data.order_amount_crypto

            base_deci = data.base_decimal
            quote_deci = data.quote_decimal

            # Estimate quote price from bid/ask midpoint if not provided
            quote_price = (highest_bid + lowest_ask) / 2
            quote_price = quote_price.quantize(Decimal(f'1e-{quote_deci}'), rounding=ROUND_HALF_UP)

            # Debug: Log price calculation for insufficient balance diagnosis
            self.logger.debug(
                f"[VALIDATION] {trading_pair} price calc: highest_bid={highest_bid}, "
                f"lowest_ask={lowest_ask}, quote_price={quote_price}"
            )

            # Adjust balances
            adjusted_usd = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, usd_avail, convert='usd' if quote_currency == 'USD' else 'quote'
            )

            base_bal_value = available_to_trade * quote_price
            base_bal_value = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, base_bal_value, convert='usd' if quote_currency == 'USD' else 'quote'
            )

            # Debug: Log balance value calculation
            self.logger.debug(
                f"[VALIDATION] {trading_pair} balance calc: available_to_trade={available_to_trade}, "
                f"base_bal_value={base_bal_value}, min_sell_value={self.min_sell_value}"
            )

            # Open order check
            open_orders = data.open_orders if isinstance(data.open_orders, pd.DataFrame) else pd.DataFrame()
            if not open_orders.empty:
                open_orders = open_orders.copy()
                open_orders['product_id'] = open_orders['info'].apply(
                    lambda x: x.get('product_id', '').replace('-', '/') if isinstance(x, dict) else None
                )
                match = open_orders.loc[
                    (open_orders['product_id'] == trading_pair) & (open_orders['remaining'] > 0)
                    ]
                if not match.empty:
                    return base_avail, base_bal_value, False, f"⚠️ Open order exists for {trading_pair}. Blocking new order."
            has_open_order, open_order = self.shared_utils_utility.has_open_orders(trading_pair, self.open_orders)
            # Order logic
            condition = '❌ Order conditions not met.'
            valid = False
            hodling = base_currency in self.hodl



            if side == 'buy':
                if adjusted_usd >= order_size and (hodling or base_bal_value <= self.min_order_amount_fiat):
                    valid = True
                    condition = '✅ Buy order conditions met.'
                elif adjusted_usd < order_size and order_size > self.min_order_amount_fiat:
                    self.logger.info(
                        f"⚠️ Order reduced: USD available ${adjusted_usd} < order amount ${order_size} for {trading_pair}."
                    )
                    valid = True
                    condition = 'Reduced buy order submitted.'
                else:
                    if base_bal_value >= self.min_order_amount_fiat:
                        condition = f'❌ Buy conditions failed: There is a balance for {trading_pair} of ${base_bal_value}'
                    else:
                        condition = f'❌ Buy conditions failed: Not enough USD or not in buy zone.'

            elif side == 'sell':
                if hodling:
                    condition = f'⛔ {trading_pair} is marked HODL — sell blocked.'
                elif base_bal_value >= self.min_sell_value:
                    valid = True
                    condition = f'✅️ {trading_pair} has sufficient balance to sell.'
                else:
                    condition = f'⚠️ {trading_pair} has insufficient balance to sell.'

            if not valid:
                self.logger.info(f'❌ Order validation failed for {trading_pair}: {condition}')

            return base_avail, base_bal_value, valid, condition

        except Exception as e:
            self.logger.error(f'❌️ validate_orders error: {e}', exc_info=True)
            return None, None, False, "Exception occurred during validation."

    def fetch_and_validate_rules(self, order_data: OrderData) -> dict:
        """
        Validate order conditions, ensure balance sufficiency, and check for duplicate orders.

        Args:
            order_data (OrderData): Structured data for order validation.

        Returns:
            dict: Validation results, error info, and structured details.
        """
        try:

            base_balance, base_balance_value, is_valid, condition = self.validate_orders(order_data)

            open_orders = order_data.open_orders if isinstance(order_data.open_orders, pd.DataFrame) else pd.DataFrame()
            response_msg = {
                "is_valid": is_valid,
                "error": None,
                "code": "200",
                "message": f"Order validation failed for {order_data.trading_pair}",
                "details": {
                    "Order Id": None,
                    "source": order_data.source,
                    "asset": order_data.base_currency,
                    "trigger": order_data.trigger,
                    "trading_pair": order_data.trading_pair,
                    "side": order_data.side,
                    "base_balance": base_balance,
                    "base_bal_value": base_balance_value,
                    "base_avail_to_trade": order_data.available_to_trade_crypto,
                    "available_to_trade_crypto": order_data.available_to_trade_crypto,
                    "usd_avail_balance": order_data.usd_avail_balance,
                    "usd_balance": order_data.usd_balance,
                    "condition": condition,
                    "quote_decimal": order_data.quote_decimal,
                    "base_decimal": order_data.base_decimal,
                    "order_amount_fiat": order_data.order_amount_fiat,
                    "order_amount_crypto": order_data.order_amount_crypto,
                    "sell_amount": order_data.available_to_trade_crypto,
                    "maker_fee": order_data.maker,
                    "taker_fee": order_data.taker,
                    "Open Orders": open_orders,
                    "24h Quote Volume": order_data.volume_24h
                }
            }

            # ✅ If order is valid
            if is_valid:
                response_msg["message"] = f"✅ Order validation successful for {order_data.trading_pair}."
                return response_msg
            if not is_valid:
                if "insufficient balance to sell" in condition:
                    response_msg.update(
                        {
                            "is_valid": False,
                            "error": f"Insufficient balance to sell {order_data.base_currency}.",
                            "code": "414",
                            "details": {**response_msg["details"], "condition": condition}
                        }
                    )
                    return response_msg


            # ✅ Crypto balance value too high to buy more
            if base_balance_value is not None and base_balance_value > Decimal("1.0") and order_data.side.lower() == 'buy':
                response_msg.update(
                    {
                        "is_valid": False,
                        "error": f"Base balance value ${base_balance_value} exceeds limit.",
                        "code": "415",
                        "details": {**response_msg["details"], "condition": condition}
                    }
                )
                return response_msg

            # ✅ Insufficient crypto or duplicate orders
            if base_balance is None or base_balance == 0:
                open_orders = order_data.open_orders if isinstance(order_data.open_orders, pd.DataFrame) else pd.DataFrame()
                if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
                    open_orders = open_orders.copy()
                    open_orders['product_id'] = open_orders['info'].apply(
                        lambda x: x.get('product_id', '').replace('-', '/') if isinstance(x, dict) else None
                    )
                    matching_order = open_orders[open_orders['product_id'] == order_data.trading_pair]
                    order_side = matching_order.iloc[0]['side'] if not matching_order.empty else 'Unknown'

                    self.logger.info(
                        f"⚠️ Order blocked: {order_data.side} order for {order_data.trading_pair} conflicts with existing {order_side} order."
                    )
                    response_msg.update(
                        {
                            "is_valid": False,
                            "error": f"Open order exists for {order_data.trading_pair} on side {order_side}.",
                            "code": "300",
                            "details": {**response_msg["details"], "Open Orders": order_side}
                        }
                    )
                    return response_msg

                self.logger.info(
                    f"⚠️ Insufficient balance for {order_data.trading_pair} {order_data.side} order. Balance: {base_balance}"
                )
                response_msg.update(
                    {
                        "is_valid": False,
                        "error": f"Insufficient balance to place {order_data.side} order.",
                        "code": "313" if order_data.side == 'buy' else "314",
                    }
                )
                return response_msg

            # ❌ Default fallback
            response_msg["details"]["condition"] = condition or "Unknown"
            response_msg["error"] = condition or "Unclear rejection reason"
            response_msg["code"] = "417" if not condition else "200"
            return response_msg

        except Exception as e:
            self.logger.error(f"❌ fetch_and_validate_rules() error: {e}", exc_info=True)
            return {
                "is_valid": False,
                "error": f"Unexpected error: {e}",
                "code": "500",
                "message": "Internal error while validating order.",
                "details": {}
            }

    async def validate_and_adjust_order(self, order_data):
        """
        Validates order data and adjusts price dynamically to ensure post-only compliance.
        Args:
            order_data (dict): Contains order details including price, size, and balances.
        Returns:
            tuple: (adjusted order_data, response_msg)
        """
        response_msg = {
            "is_valid": False,
            "error": "A required field is missing",
            "code": "400",
            "message": f"Order validation failed for {order_data.get('trading_pair')}",
            "details": {
                "Order Id": order_data.get('order_id', ''),
                "asset": order_data.get('trading_pair').split('/')[0],
                "trading_pair": order_data.get("trading_pair"),
                "side": order_data.get("side"),
                "base_balance": order_data.get("base_avail_balance"),
                "base_bal_value": order_data.get("base_avail_balance"),
                "base_avail_to_trade": order_data.get('available_to_trade_crypto', ''),
                "condition": "Missing fields",
                "Open Orders": "N/A",
            }
        }

        try:
            # ✅ Required field check
            required_fields = [
                'trading_pair', 'side', 'adjusted_size', 'highest_bid',
                'lowest_ask', 'available_to_trade_crypto'
            ]
            missing_fields = [field for field in required_fields if order_data.get(field) is None]
            if missing_fields:
                self.logger.error(f"⚠️ Missing required fields: {missing_fields}")
                response_msg['details']['condition'] = f"Missing fields: {missing_fields}"
                return order_data, response_msg

            side = order_data['side'].lower()
            symbol = order_data['trading_pair'].replace('/', '-')
            maker_fee_rate = self.shared_utils_precision.safe_convert(order_data.get('maker_fee', '0.0'), order_data['quote_decimal'])  # Default to 0.0

            # ✅ Base price and precision
            price = Decimal(order_data.get('highest_bid' if side == 'sell' else 'lowest_ask', 0))
            quote_decimal = Decimal('1').scaleb(-order_data.get('quote_decimal', 2))
            price = price.quantize(quote_decimal, rounding=ROUND_HALF_UP)

            # ✅ Order book check and price buffer
            order_book = await self.order_book.get_order_book(order_data)
            latest_lowest_ask = order_book['lowest_ask'] or price
            latest_highest_bid = order_book['highest_bid'] or price

            price_buffer_pct = Decimal('0.001')
            min_buffer = Decimal('0.0000001')

            if side == 'buy' and price >= latest_lowest_ask:
                price = max(latest_lowest_ask * (1 - price_buffer_pct), latest_lowest_ask - min_buffer)
            elif side == 'sell' and price <= latest_highest_bid:
                price = min(latest_highest_bid * (1 + price_buffer_pct), latest_highest_bid + min_buffer)

            price = price.quantize(quote_decimal, rounding=ROUND_DOWN)

            # ✅ Balance & fee validation
            amount = Decimal(order_data['adjusted_size'])
            available_crypto = Decimal(order_data.get('available_to_trade_crypto', 0))
            usd_available = Decimal(order_data.get('usd_balance', 0))

            if side == 'sell':
                adjusted_amount = amount  # No need to reduce amount — fee comes from USD proceeds
                if adjusted_amount > available_crypto:
                    self.logger.info(
                        f"⚠️ Insufficient {symbol} for SELL (after fee). Trying {adjusted_amount}, Available {available_crypto}"
                    )
                    response_msg['error'] = "Insufficient balance for SELL order"
                    response_msg['details']['condition'] = f"Required {adjusted_amount}, Available {available_crypto}"
                    return order_data, response_msg

            elif side == 'buy':
                # Increase required USD to account for maker fee
                total_cost_with_fee = (amount * price) * (Decimal('1') + maker_fee_rate)
                if total_cost_with_fee > usd_available:
                    self.logger.info(f"⚠️ Insufficient USD for BUY (incl. fee): Required {total_cost_with_fee}, Available {usd_available}")
                    response_msg['error'] = "Insufficient USD for BUY order (incl. fee)"
                    response_msg['details']['condition'] = f"Required {total_cost_with_fee}, Available {usd_available}"
                    return order_data, response_msg
                adjusted_amount = amount  # No need to shrink size, just verify cost

            else:
                adjusted_amount = amount

            # ✅ Final adjustments
            adjusted_amount = adjusted_amount.quantize(Decimal(f'1e-{order_data.get("base_decimal", 8)}'), rounding=ROUND_DOWN)
            order_data['adjusted_price'] = price
            order_data['adjusted_size'] = adjusted_amount
            order_data['total_cost_with_fee'] = float(adjusted_amount * price * (Decimal('1') + maker_fee_rate))
            order_data['fee_applied'] = float(maker_fee_rate)

            response_msg = {
                "is_valid": True,
                "error": None,
                "code": "200",
                "message": f"Order validation successful for {order_data.get('trading_pair')}",
                "details": {
                    "Order Id": order_data.get('order_id', ''),
                    "asset": order_data.get('trading_pair').split('/')[0],
                    "trading_pair": order_data.get("trading_pair"),
                    "side": order_data.get("side"),
                    "base_balance": order_data.get("base_avail_balance"),
                    "base_bal_value": order_data.get("base_avail_balance"),
                    "base_avail_to_trade": order_data.get('available_to_trade_crypto', ''),
                    "condition": "Fee-adjusted",
                    "Open Orders": "N/A"
                }
            }

            return order_data, response_msg

        except Exception as e:
            self.logger.error(f"❌ Error in validate_and_adjust_order: {e}", exc_info=True)
            response_msg['error'] = "Error in validate_and_adjust_order"
            response_msg['details']['condition'] = str(e)
            return order_data, response_msg
