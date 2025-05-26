
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional, Union

import pandas as pd

from Config.config_manager import CentralConfig as Config


@dataclass
class OrderData:
    trading_pair: str
    time_order_placed: Union[datetime, None]
    type: str
    order_id: str
    side: str
    filled_price: Decimal
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
    trigger: str = 'UNKNOWN'
    base_avail_balance: Decimal = Decimal('0')
    total_balance_crypto: Decimal = Decimal('0')  # spot_position
    available_to_trade_crypto: Decimal = Decimal('0')
    usd_avail_balance: Decimal = Decimal('0')
    order_amount_fiat: Decimal = Decimal('0')
    order_amount_crypto: Decimal = Decimal('0')
    price: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal('0')  # spot_position
    limit_price: Decimal = Decimal('0')
    average_price: Optional[Decimal] = None
    adjusted_price: Optional[Decimal] = None
    adjusted_size: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None

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


    @classmethod
    def from_dict(cls, data: dict) -> 'OrderData':
        """Used when rebuilding an OrderData object from raw data
        -snapshot from order_tracker
        -WebSocket or REST API payload
        -Data loaded from a .json file or DB"""



        def get_decimal(key, default='0'):
            val = data.get(key, default)
            try:
                return Decimal(val)
            except:
                return Decimal(default)

        def extract_base_quote(pair: str):
            """Safely split a trading pair using either '-' or '/'."""
            if not pair:
                return '', ''
            split_pair = pair.replace('-', '/').split('/')
            return (split_pair[0], split_pair[1]) if len(split_pair) == 2 else ('', '')

        # Pull a product or trading pair string from possible keys
        if isinstance(data, dict):
            product_id = data.get('trading_pair') or data.get('symbol') or data.get('product_id', '')
            base_currency, quote_currency = extract_base_quote(product_id)
        else:
            product_id = data.trading_pair or data.symbol or data.product_id
            base_currency, quote_currency = extract_base_quote(product_id)

        raw_qdec = data.get("quote_decimal")  # could be '', None, or int/str
        try:
            quote_decimal = int(raw_qdec)
        except (TypeError, ValueError):
            quote_decimal = 2  # sane default – adjust if needed

        quote_increment = Decimal("1").scaleb(-quote_decimal)  # 1e-quote_decimal
        return cls(
            source=data.get('source', 'UNKNOWN'),
            time_order_placed=None,
            volume_24h=None,
            trigger='None',
            order_id=data.get('id') or data.get('info', {}).get('order_id'),
            trading_pair=product_id.replace('-', '/'),
            side=data.get('side', '').lower(),
            type=data.get('type', '').lower(),
            order_amount_fiat=get_decimal('order_amount'),
            order_amount_crypto = get_decimal('adjusted_size') or get_decimal('available_to_trade_crypto') or Decimal("0"),
            price=get_decimal('price'),
            cost_basis=data.get('cost_basis'),  # spot_position
            limit_price=get_decimal(data.get('info', {}).get('order_configuration', {}).get('limit_limit_gtc', {}).get('limit_price')),
            filled_price=get_decimal(data.get('info', {}).get('average_filled_price')),
            base_currency=data.get('base_currency') or base_currency,
            quote_currency=data.get('quote_currency') or quote_currency,
            usd_avail_balance=get_decimal('usd_avail_balance'),
            usd_balance=get_decimal('usd_balance'),
            base_avail_balance=get_decimal('base_avail_balance'),
            total_balance_crypto=data.get('total_balance_crypto'),  # spot_position
            available_to_trade_crypto=get_decimal('available_to_trade_crypto'),
            base_decimal=int(data.get('base_decimal', 8)),
            quote_decimal=int(data.get('quote_decimal', 2)),
            quote_increment=quote_increment,
            highest_bid=get_decimal('highest_bid'),
            lowest_ask=get_decimal('lowest_ask'),
            maker=get_decimal('maker_fee'),
            taker=get_decimal('taker_fee'),
            spread=get_decimal('spread'),
            open_orders=data.get('open_orders', pd.DataFrame()),
            status=data.get('status', 'UNKNOWN'),
            average_price=get_decimal('average_price') if data.get('average_price') else None,
            adjusted_price=get_decimal('adjusted_price') if data.get('adjusted_price') else None,
            adjusted_size=get_decimal('adjusted_size') if data.get('adjusted_size') else None,
            stop_loss_price=get_decimal('stop_loss_price') if data.get('stop_loss_price') else None,
            take_profit_price=get_decimal('take_profit_price') if data.get('take_profit_price') else None
        )

    def debug_summary(self, verbose: bool = False) -> str:
        """Generate a safe, readable summary of this order for debugging/logging."""
        summary_lines = [f"\n� OrderData Summary for {self.trading_pair} [{self.side.upper()}]"]

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
    def get_instance(cls, logger_manager, order_book, shared_utils_precision):
        """
        Singleton method to ensure only one instance of ValidateOrders exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, order_book, shared_utils_precision)
        return cls._instance

    def __init__(self, logger_manager, order_book, shared_utils_precision):
        """
        Initializes the ValidateOrders instance.
        """
        self.config = Config()
        self.order_book = order_book
        self.shared_utils_precision = shared_utils_precision
        self.logger = logger_manager  # 🙂

        # Only store necessary attributes
        self._min_sell_value = self.config.min_sell_value
        self._min_order_amount_fiat = self.config.min_order_amount_fiat
        self._max_value_of_crypto_to_buy_more = self.config.max_value_of_crypto_to_buy_more
        self._hodl = self.config.hodl  # ✅ Ensure this is used elsewhere
        self._version = self.config.program_version  # ✅ Ensure this is required

    @property
    def hodl(self):
        return self._hodl

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
            base_deci, quote_deci, *_ = precision_data
            side = details.get("side", "buy")
            buy_amount = self.shared_utils_precision.safe_decimal(details.get("order_amount_fiat"))
            sell_amount = self.shared_utils_precision.safe_decimal(details.get("base_balance"))
            trading_pair = details.get("trading_pair", "")
            base_currency = details.get("asset", trading_pair.split('/')[0])
            quote_currency = trading_pair.split('/')[1] if '/' in trading_pair else 'USD'
            order_amount = buy_amount if side == "buy" else sell_amount

            raw_qdec = details.get("quote_decimal")  # could be '', None, or int/str
            try:
                quote_decimal = int(raw_qdec)
            except (TypeError, ValueError):
                quote_decimal = 2  # sane default – adjust if needed

            quote_increment = Decimal("1").scaleb(-quote_decimal)  # 1e-quote_decimal

            return OrderData(
                source=details.get("source", "webhook"),
                time_order_placed=None,
                volume_24h=None,
                trigger=details.get("trigger", ""),
                order_id=details.get("order_id", ""),
                trading_pair=trading_pair,
                side=details.get("side", "buy"),
                type=details.get("Order Type", "limit").lower(),
                order_amount_fiat=details.get('order_amount_fiat'),
                order_amount_crypto=self.shared_utils_precision.safe_decimal(details.get("adjusted_size")) or \
                                    self.shared_utils_precision.safe_decimal(details.get("available_to_trade_crypto")) or \
                                    Decimal("0"),
                price=Decimal("0"),
                cost_basis=Decimal("0"),
                limit_price=self.shared_utils_precision.safe_decimal(details.get("limit_price")),
                filled_price=self.shared_utils_precision.safe_decimal(details.get("average_price")),
                base_currency=base_currency,
                quote_currency=quote_currency,
                usd_avail_balance=self.shared_utils_precision.safe_decimal(details.get("usd_avail_balance")),
                usd_balance=self.shared_utils_precision.safe_decimal(details.get("usd_balance")),
                base_avail_balance=self.shared_utils_precision.safe_decimal(details.get("base_balance")),
                total_balance_crypto=self.shared_utils_precision.safe_decimal(details.get("available_to_trade_crypto")),
                available_to_trade_crypto=self.shared_utils_precision.safe_decimal(details.get("available_to_trade_crypto")),
                base_decimal=details.get("base_decimal", base_deci),
                quote_decimal=details.get("quote_decimal", quote_deci),
                quote_increment=quote_increment,
                highest_bid=self.shared_utils_precision.safe_decimal(order_book_details.get("highest_bid")),
                lowest_ask=self.shared_utils_precision.safe_decimal(order_book_details.get("lowest_ask")),
                maker=self.shared_utils_precision.safe_decimal(details.get("maker_fee")),
                taker=self.shared_utils_precision.safe_decimal(details.get("taker_fee")),
                spread=self.shared_utils_precision.safe_decimal(order_book_details.get("spread")),
                open_orders=details.get("Open Orders", pd.DataFrame()),
                status=details.get("status", "VALID"),
                average_price=self.shared_utils_precision.safe_decimal(details.get("average_price")) if details.get("average_price") else None,
                adjusted_price=self.shared_utils_precision.safe_decimal(details.get("adjusted_price")) if details.get("adjusted_price") else None,
                adjusted_size=self.shared_utils_precision.safe_decimal(details.get("adjusted_size")) if details.get("adjusted_size") else None,
                stop_loss_price=self.shared_utils_precision.safe_decimal(details.get("stop_loss_price")) if details.get("stop_loss_price") else None,
                take_profit_price=self.shared_utils_precision.safe_decimal(details.get("take_profit_price")) if details.get(
                    "take_profit_price") else None,
            )
        except Exception as e:
            raise Exception(f"Error creating OrderData object: {e}")

    def validate_order_conditions(self, order_details: OrderData, open_orders):
        """
        Validates order conditions based on account balances and active open orders.
        called from:
        place_order()
        process_limit_and_tp_sl_orders(()
        Args:
            order_details (dict): Data about the order to validate.
            open_orders (DataFrame): DataFrame of active open orders.

        Returns:
            dict: Contains validation status, error messages, and detailed information.
        """

        side = order_details.side
        usd_balance = order_details.usd_avail_balance
        order_size = order_details.get_effective_amount()

        #order_size = order_details.order_amount_fiat if side == 'buy' else order_details.order_amount_crypto
        base_balance = order_details.base_avail_balance
        symbol = order_details.trading_pair
        asset =symbol.split('/')[0]



        trailing_stop_active = False  # Flag for trailing stop orders

        response_msg = {
            "is_valid": False,
            "error": None,
            "code": "200",
            "message": f"Validation failed for {symbol}",
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

        if order_details.type == 'market':
            if side == 'buy':
                if usd_balance < order_size:
                    response = {
                        "base_balance": base_balance,
                        "Order Size": order_size,
                        "condition": None
                    }

        try:
            if open_orders.empty:
                if side == 'buy' and usd_balance < order_size:
                    response_msg["error"] = f"INSUFFICIENT_QUOTE. Required: {order_size}, Available: {usd_balance}"
                    response_msg["code"] = "413"
                    return response_msg

                if side == 'sell' and base_balance <= 0:
                    response_msg["error"] = f"INSUFFICIENT_BASE balance to sell {symbol}."
                    response_msg["code"] = "414"
                    return response_msg
                if side == 'sell' and asset in self.hodl:
                    response_msg["error"] = f"HODLing {symbol} sell order rejected."
                    response_msg["code"] = "424"
                    return response_msg
                response_msg["is_valid"] = True
                response_msg["message"] = "Order validated successfully."
                response_msg["code"] = "200"
                return response_msg

            if symbol in open_orders.symbol.values:
                response_msg["error"] = f"OPEN_ORDER"
                response_msg["code"] = "411"
                response_msg["condition"] = f"Open order exists for {symbol}."
                return response_msg

            response_msg["is_valid"] = True
            response_msg["message"] = "Order validated successfully."
            response_msg["code"] = "200"

            return response_msg

        except KeyError as e:
            response_msg["error"] = f" ❌ KEY_ERROR: Missing key in order_details or open_orders: {e}"
            response_msg["code"] = "500"
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
            base_currency, quote_currency = trading_pair.split('/')
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

            # Adjust balances
            adjusted_usd = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, usd_avail, convert='usd' if quote_currency == 'USD' else 'quote'
            )

            base_bal_value = available_to_trade * quote_price
            base_bal_value = self.shared_utils_precision.adjust_precision(
                base_deci, quote_deci, base_bal_value, convert='usd' if quote_currency == 'USD' else 'quote'
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

            # Order logic
            condition = ""
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
            elif side == 'sell' and not hodling:
                if base_bal_value >= self.min_sell_value:
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
                            "code": "416",
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
                        "code": "413" if order_data.side == 'buy' else "414",
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
            maker_fee_rate = Decimal(str(order_data.get('maker_fee', '0.0')))  # Default to 0.0

            # ✅ Base price and precision
            price = Decimal(order_data.get('highest_bid' if side == 'sell' else 'lowest_ask', 0))
            quote_decimal = Decimal('1').scaleb(-order_data.get('quote_decimal', 2))
            price = price.quantize(quote_decimal, rounding=ROUND_HALF_UP)

            # ✅ Order book check and price buffer
            order_book = await self.order_book.get_order_book(order_data)
            latest_lowest_ask = Decimal(order_book['order_book']['asks'][0][0]) if order_book['order_book']['asks'] else price
            latest_highest_bid = Decimal(order_book['order_book']['bids'][0][0]) if order_book['order_book']['bids'] else price

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
