from dataclasses import asdict
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional, Union

import pandas as pd

from Config.config_manager import CentralConfig as Config


@dataclass
class OrderData:
    trading_pair: str
    side: str
    type: str
    order_id: str
    order_amount: Decimal
    filled_price: Decimal
    base_currency: str
    quote_currency: str
    usd_avail_balance: Decimal
    usd_balance: Decimal
    base_avail_balance: Decimal
    total_balance_crypto: Decimal  # spot_position
    available_to_trade_crypto: Decimal
    base_decimal: int
    quote_decimal: int
    highest_bid: Decimal
    lowest_ask: Decimal
    maker_fee: Decimal
    taker_fee: Decimal
    spread: Decimal
    open_orders: Union[pd.DataFrame, None] = None
    status: str = 'UNKNOWN'
    source: str = 'UNKNOWN'
    price: Decimal = Decimal('0')
    cost_basis: Decimal = Decimal('0')  # spot_position
    limit_price: Decimal = Decimal('0')
    average_price: Optional[Decimal] = None
    adjusted_price: Optional[Decimal] = None
    adjusted_size: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'OrderData':
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
        product_id = data.get('trading_pair') or data.get('symbol') or data.get('product_id', '')
        base_currency, quote_currency = extract_base_quote(product_id)

        return cls(
            source=data.get('source', 'UNKNOWN'),
            order_id=data.get('id') or data.get('info', {}).get('order_id'),
            trading_pair=product_id.replace('-', '/'),
            side=data.get('side', '').lower(),
            type=data.get('type', '').lower(),
            order_amount=get_decimal('order_amount'),
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
            highest_bid=get_decimal('highest_bid'),
            lowest_ask=get_decimal('lowest_ask'),
            maker_fee=get_decimal('maker_fee'),
            taker_fee=get_decimal('taker_fee'),
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
    def get_instance(cls, logmanager, order_book,shared_utils_precision):
        """
        Singleton method to ensure only one instance of ValidateOrders exists.
        """
        if cls._instance is None:
            cls._instance = cls(logmanager, order_book,shared_utils_precision)
        return cls._instance

    def __init__(self, logmanager, order_book, shared_utils_precision):
        """
        Initializes the ValidateOrders instance.
        """
        self.config = Config()
        self.order_book = order_book
        self.shared_utils_precision = shared_utils_precision
        self.log_manager = logmanager

        # Only store necessary attributes
        self._min_sell_value = self.config.min_sell_value
        self._min_order_amount = self.config.min_order_amount
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
    def min_order_amount(self):
        return self._min_order_amount  # Minimum order amount

    @property
    def max_value_of_crypto_to_buy_more(self):
        return self.max_value_of_crypto_to_buy_more  # max value of a sell order

    @property
    def version(self):
        return self._version

    def build_order_data_from_validation_result(self, validation_result: dict, order_book_details: dict, precision_data: tuple) -> OrderData:
        """
        Converts a validated dictionary response into a structured OrderData instance.
        """
        details = validation_result.get("details", {})
        base_deci, quote_deci, *_ = precision_data

        trading_pair = details.get("trading_pair", "")
        base_currency = details.get("asset", trading_pair.split('/')[0])
        quote_currency = trading_pair.split('/')[1] if '/' in trading_pair else 'USD'

        return OrderData(
            source=details.get("source", "webhook"),
            order_id=details.get("order_id", ""),  # Or None if you want
            trading_pair=trading_pair,
            side=details.get("side", "buy"),
            type=details.get("Order Type", "limit").lower(),
            order_amount=Decimal(details.get("order_amount", 0)),
            price=Decimal("0"),  # Price may be set later
            cost_basis=Decimal("0"),  # Fill in if available
            limit_price=Decimal(details.get("limit_price", 0)),
            filled_price=Decimal(details.get("average_price", 0)),
            base_currency=base_currency,
            quote_currency=quote_currency,
            usd_avail_balance=Decimal(details.get("usd_avail_balance", 0)),
            usd_balance=Decimal(details.get("usd_balance", 0)),
            base_avail_balance=Decimal(details.get("base_balance", 0)),
            total_balance_crypto=Decimal(details.get("available_to_trade_crypto", 0)),  # fallback
            available_to_trade_crypto=Decimal(details.get("available_to_trade_crypto", 0)),
            base_decimal=details.get("base_decimal", base_deci),
            quote_decimal=details.get("quote_decimal", quote_deci),
            highest_bid=Decimal(order_book_details.get("highest_bid", 0)),
            lowest_ask=Decimal(order_book_details.get("lowest_ask", 0)),
            maker_fee=Decimal(details.get("maker_fee", "0")),
            taker_fee=Decimal(details.get("taker_fee", "0")),
            spread=Decimal(order_book_details.get("spread", 0)),
            open_orders=details.get("Open Orders", pd.DataFrame()),
            status=details.get("status", "UNKNOWN"),
            average_price=Decimal(details.get("average_price", 0)) if details.get("average_price") else None,
            adjusted_price=Decimal(details.get("adjusted_price", 0)) if details.get("adjusted_price") else None,
            adjusted_size=Decimal(details.get("adjusted_size", 0)) if details.get("adjusted_size") else None,
            stop_loss_price=Decimal(details.get("stop_loss_price", 0)) if details.get("stop_loss_price") else None,
            take_profit_price=Decimal(details.get("take_profit_price", 0)) if details.get("take_profit_price") else None,
        )

    @staticmethod
    def validate_order_conditions(order_details: OrderData, open_orders):
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
        order_size = order_details.order_amount
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
            response_msg["error"] = f"KEY_ERROR: Missing key in order_details or open_orders: {e}"
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
            order_amount = to_decimal(data.order_amount)

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
                if adjusted_usd >= order_amount and (hodling or base_bal_value <= self.min_order_amount):
                    valid = True
                    condition = '✅ Buy order conditions met.'
                elif adjusted_usd < order_amount and order_amount > self.min_order_amount:
                    self.log_manager.info(
                        f"⚠️ Order reduced: USD available ${adjusted_usd} < order amount ${order_amount} for {trading_pair}."
                    )
                    valid = True
                    condition = 'Reduced buy order submitted.'
            elif side == 'sell' and not hodling:
                if base_bal_value >= self.min_order_amount:
                    valid = True
                    condition = f'✅️ {trading_pair} has sufficient balance to sell.'
                else:
                    condition = f'⚠️ {trading_pair} has insufficient balance to sell.'

            if not valid:
                self.log_manager.info(f'❌ Order validation failed for {trading_pair}: {condition}')

            return base_avail, base_bal_value, valid, condition

        except Exception as e:
            self.log_manager.error(f'⚠️ validate_orders error: {e}', exc_info=True)
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
                    "order_amount": order_data.order_amount,
                    "sell_amount": order_data.available_to_trade_crypto,
                    "Open Orders": open_orders,
                }
            }

            # ✅ If order is valid
            if is_valid:
                response_msg["message"] = f"✅ Order validation successful for {order_data.trading_pair}."
                return response_msg

            # ✅ Crypto balance value too high to buy more
            if base_balance_value is not None and base_balance_value > Decimal("1.0"):
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

                    self.log_manager.info(
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

                self.log_manager.info(
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
            self.log_manager.error(f"⚠️ fetch_and_validate_rules() error: {e}", exc_info=True)
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
                self.log_manager.error(f"⚠️ Missing required fields: {missing_fields}")
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
                    self.log_manager.info(
                        f"⚠️ Insufficient {symbol} for SELL (after fee). Trying {adjusted_amount}, Available {available_crypto}"
                    )
                    response_msg['error'] = "Insufficient balance for SELL order"
                    response_msg['details']['condition'] = f"Required {adjusted_amount}, Available {available_crypto}"
                    return order_data, response_msg

            elif side == 'buy':
                # Increase required USD to account for maker fee
                total_cost_with_fee = (amount * price) * (Decimal('1') + maker_fee_rate)
                if total_cost_with_fee > usd_available:
                    self.log_manager.info(f"⚠️ Insufficient USD for BUY (incl. fee): Required {total_cost_with_fee}, Available {usd_available}")
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
            self.log_manager.error(f"❌ Error in validate_and_adjust_order: {e}", exc_info=True)
            response_msg['error'] = "Error in validate_and_adjust_order"
            response_msg['details']['condition'] = str(e)
            return order_data, response_msg

    # def _initialize_validation_response(self, data, base_balance, base_value_usd, condition):
    #     return {
    #         "is_valid": False,
    #         "error": None,
    #         "code": "200",
    #         "message": f"Order validation failed for {data.get('trading_pair')}",
    #         "details": {
    #             "Order Id": None,
    #             "asset": data.get("base_currency"),
    #             "trading_pair": data.get("trading_pair"),
    #             "side": data.get("side"),
    #             "base_balance": base_balance,
    #             "base_bal_value": base_value_usd,
    #             "base_avail_to_trade": data.get('available_to_trade_crypto', ''),
    #             "available_to_trade_crypto": data.get('available_to_trade_crypto', ''),
    #             "usd_avail_balance": data.get('usd_avail_balance', ''),
    #             "condition": condition,
    #             "quote_decimal": data.get('quote_decimal', ''),
    #             "base_decimal": data.get('base_decimal', ''),
    #             "order_amount": data.get('order_amount'),
    #             "sell_amount": data.get('available_to_trade_crypto', ''),
    #             "Open Orders": data.get('open_orders', pd.DataFrame()),
    #         }
    #     }

    # def _handle_missing_base_balance(self, data, response):
    #     open_orders = data.get('open_orders', pd.DataFrame())
    #
    #     if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
    #         open_orders = open_orders.copy()
    #         open_orders['product_id'] = open_orders['info'].apply(
    #             lambda x: x.get('product_id', '').replace('-', '/') if isinstance(x, dict) else None
    #         )
    #         matching = open_orders[open_orders['product_id'] == data['trading_pair']]
    #         side = matching.iloc[0]['side'] if not matching.empty else 'Unknown'
    #
    #         self.log_manager.info(
    #             f"{data['side']} order blocked for {data['trading_pair']}, open order exists on side {side}."
    #         )
    #         response.update(
    #             {
    #                 "is_valid": False,
    #                 "error": f"Open order exists for {data['trading_pair']} on side {side}.",
    #                 "code": "416",
    #             }
    #         )
    #         response["details"]["Open Orders"] = side
    #     else:
    #         side = data.get("side", "unknown")
    #         self.log_manager.info(f"{side} order not valid. {data['trading_pair']} balance is {data.get('base_balance')}")
    #         response.update(
    #             {
    #                 "is_valid": False,
    #                 "error": f"Insufficient balance to place {side} order.",
    #                 "code": "413" if side == "buy" else "414"
    #             }
    #         )
    #
    #     return response

    # def fetch_and_validate_rules(self, validate_data: dict) -> dict:
    #     """
    #     Validates whether an order should be placed based on balance, trading rules, and existing open orders.
    #
    #     Args:
    #         validate_data (dict): Contains normalized order information.
    #
    #     Returns:
    #         dict: Structured response indicating if order is valid or rejected with reason.
    #     """
    #     try:
    #
    #         base_balance, base_value_usd, is_valid_order, condition = self.validate_orders(validate_data)
    #
    #         response = self._initialize_validation_response(validate_data, base_balance, base_value_usd, condition)
    #
    #         # ✅ Immediate return if validation passes
    #         if is_valid_order:
    #             response["is_valid"] = True
    #             response["message"] = f"✅ Order validation successful for {validate_data.get('trading_pair')}."
    #             return response
    #
    #         # ❌ If base balance exists but value is too high for a buy
    #         if base_value_usd is not None and base_value_usd > Decimal("1.0"):
    #             response.update(
    #                 {
    #                     "is_valid": False,
    #                     "error": f"Base balance value {base_value_usd} exceeds limit.",
    #                     "code": "415"
    #                 }
    #             )
    #             return response
    #
    #         # ❌ If base balance is 0 or None
    #         if base_balance is None or base_balance == 0.0:
    #             return self._handle_missing_base_balance(validate_data, response)
    #
    #         # ❌ Catch-all fallback for unhandled invalid cases
    #         response["is_valid"] = False
    #         response["error"] = condition or "Unknown validation error"
    #         response["code"] = "417" if not condition else "200"
    #         response["details"]["condition"] = condition or "Unknown"
    #         return response
    #
    #     except Exception as e:
    #         self.log_manager.error(f'fetch_and_validate_rules: {e}', exc_info=True)
    #         return {
    #             "is_valid": False,
    #             "error": f"Unexpected error: {e}",
    #             "code": "500",
    #             "message": "Internal error while validating order.",
    #             "details": {}
    #         }

    #     - place_order()
    #     - process_limit_and_tp_sl_orders()
    #
    #     Args:
    #         validate_data (dict): Data containing order details and account balances.
    #
    #     Returns:
    #         dict: Contains validation status, error messages, and relevant details.
    #     """
    #     try:
    #         # Step 1: Normalize data
    #         validate_data = self.normalize_validate_data(validate_data)
    #         base_balance, base_balance_value, valid_order, condition = self.validate_orders(validate_data)
    #
    #         response_msg = {
    #             "is_valid": valid_order,
    #             "error": None,
    #             "code": "200",
    #             "message": f"Order validation failed for {validate_data.get('trading_pair')}",
    #             "details": {
    #                 "Order Id": None,  # order_details.get('order_id', ''),
    #                 "asset": validate_data.get("base_currency"),
    #                 "trading_pair": validate_data.get("trading_pair"),
    #                 "side": validate_data.get("side"),
    #                 "base_balance": base_balance,
    #                 "base_bal_value": base_balance_value,
    #                 "base_avail_to_trade":validate_data.get('available_to_trade_crypto', ''),
    #                 "available_to_trade_crypto": validate_data.get('available_to_trade_crypto', ''),
    #                 "usd_avail_balance": validate_data.get('usd_avail_balance', ''),
    #                 "condition": condition,
    #                 "quote_decimal": validate_data.get('quote_decimal', ''),
    #                 "base_decimal": validate_data.get('base_decimal', ''),
    #                 "order_amount": validate_data.get('order_amount'),
    #                 "sell_amount": validate_data.get('available_to_trade_crypto', ''), # to data sell amount will always be the balance.
    #                 "Open Orders": validate_data.get('open_orders', ''),  # Will be updated if open orders exist
    #             }
    #         }
    #
    #         # ✅ If order is valid, return immediately
    #         if valid_order:
    #             response_msg["is_valid"] = True
    #             response_msg["message"] = f"✅ Order validation successful for {validate_data.get('trading_pair')}."
    #             return response_msg
    #
    #         # ✅ Handle case where base balance value exceeds threshold. Do not buy is value is more then $1.00
    #         if base_balance_value is not None and base_balance_value > Decimal("1.0"):
    #             response_msg["is_valid"] = False
    #             response_msg["error"] = f"Base balance value {base_balance_value} exceeds limit."
    #             response_msg["code"] = "415"
    #             response_msg['condition'] = condition
    #             return response_msg
    #
    #         # ✅ Handle case where base balance is zero or missing
    #         if base_balance is None or base_balance == 0.0:
    #             open_orders = validate_data.get('open_orders', pd.DataFrame())
    #
    #             if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
    #                 # Prevent modifying original DataFrame
    #                 open_orders = open_orders.copy()
    #
    #                 # ✅ Extract `product_id` safely from `info`
    #                 open_orders['product_id'] = open_orders['info'].apply(
    #                     lambda x: x.get('product_id', '').replace('-', '/') if isinstance(x, dict) else None
    #                 )
    #
    #                 # ✅ Find matching open orders for the trading pair
    #                 matching_order = open_orders.loc[open_orders['product_id'] == validate_data['trading_pair']]
    #
    #                 # ✅ Extract the side of the open order (if any)
    #                 order_side = matching_order.iloc[0]['side'] if not matching_order.empty else 'Unknown'
    #
    #                 self.log_manager.info(
    #                     f'fetch_and_validate_rules: {validate_data["side"]} order will not be placed '
    #                     f'for {validate_data["trading_pair"]} as there is an open order to {order_side}.'
    #                 )
    #
    #                 response_msg["is_valid"] = False
    #                 response_msg["error"] = f"Open order exists for {validate_data['trading_pair']} on side {order_side}."
    #                 response_msg["code"] = "416"
    #                 response_msg["details"]["Open Orders"] = order_side
    #                 return response_msg  # ❌ Block duplicate orders
    #
    #             else:
    #                 self.log_manager.info(
    #                     f'fetch_and_validate_rules: {validate_data.get("details",{}).get("side")} order not valid. '
    #                     f'{validate_data["trading_pair"]} balance is {base_balance}'
    #                 )
    #                 response_msg["is_valid"] = False
    #                 side = validate_data.get("details",{}).get("side")
    #                 response_msg["error"] = f'Insufficient balance to place {side} order.'
    #                 if side == 'buy':
    #                     response_msg["code"] = "413"
    #                 elif side == 'sell':
    #                     response_msg["code"] = "414"
    #                 return response_msg
    #
    #         # ❌ Default return for invalid orders
    #         response_msg["is_valid"] = False
    #         condition = response_msg.get('details', {}).get('condition', None)
    #         if condition is None:
    #             response_msg["details"]["condition"] = "Unknown"
    #             response_msg["code"] = "417"
    #             self.log_manager.info(f'fetch_and_validate_rules: Order is not valid due to unknown conditions.')
    #         else:
    #             response_msg["error"] = condition
    #             response_msg["code"] = "200"
    #
    #
    #
    #         return response_msg
    #
    #     except Exception as e:
    #         self.log_manager.error(f'fetch_and_validate_rules: {e}', exc_info=True)
    #         return {
    #             "is_valid": False,
    #             "error": f"Unexpected error: {e}",
    #             "code": "500",
    #             "message": "Internal error while validating order.",
    #             "details": {}
    #         }

    # def validate_orders(self, validate_data):
    #     """
    #     Validate whether an order should be placed, considering open orders and balances.
    #
    #     Called from:
    #     - fetch_and_validate_rules()
    #
    #     Args:
    #         validate_data (dict): Contains trading pair, balances, order details, and open orders.
    #
    #     Returns:
    #         tuple: (base_balance, base_balance_value, valid_order, condition)
    #     """
    #
    #     def get_decimal_value(key, default='0'):
    #         """ Safely fetch and convert a value from `validate_data` to Decimal. """
    #         try:
    #             value = validate_data.get(key, default)
    #             return Decimal(value) if value is not None else Decimal(default)
    #         except InvalidOperation:
    #             return Decimal(default)
    #
    #     try:
    #         # Extract key details from validate_data
    #         trading_pair = validate_data.get('trading_pair', '')
    #         if trading_pair == 'UMA/USD':
    #             pass
    #         quote_currency = validate_data.get('quote_currency', trading_pair.split('/')[1])
    #         base_currency = validate_data.get('base_currency', trading_pair.split('/')[0])
    #         side = validate_data.get('side', '')
    #
    #         # Extract numerical values
    #         usd_avail_balance = get_decimal_value('usd_avail_balance')
    #         base_balance = get_decimal_value('base_avail_balance')
    #         base_available = get_decimal_value('available_to_trade_crypto')
    #         highest_bid = get_decimal_value('highest_bid')
    #         lowest_ask = get_decimal_value('lowest_ask')
    #         quote_price = get_decimal_value('quote_price', (highest_bid + lowest_ask) / 2)
    #         order_amount = get_decimal_value('order_amount')
    #         base_balance_value = base_available * highest_bid
    #         base_deci = validate_data.get('base_decimal', 0)  # Extract base decimal precision
    #         quote_deci = validate_data.get('quote_decimal', 0)
    #         open_orders = validate_data.get('open_orders', None)
    #
    #         condition = None
    #         valid_order = False
    #
    #         # ✅ **Quantize base_balance to the correct decimal places**
    #         base_balance = base_balance.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_HALF_UP)
    #
    #         # Adjust precision for quote balance
    #         convert = 'usd' if quote_currency == 'USD' else 'quote'
    #         adjusted_usd_balance = self.shared_utils_precision.adjust_precision(
    #             base_deci, quote_deci, usd_avail_balance, convert=convert
    #         )
    #
    #         # Compute base balance value in USD equivalent
    #         base_balance_value = Decimal(0)
    #         if base_currency != 'USD' and not base_balance.is_zero():
    #             base_balance_value = base_balance * quote_price
    #             base_balance_value = self.shared_utils_precision.adjust_precision(
    #                 base_deci, quote_deci, base_balance_value, convert=convert
    #             )
    #
    #         # ✅ **Check for open orders in DataFrame**
    #         if isinstance(open_orders, pd.DataFrame) and not open_orders.empty:
    #             # Ensure `product_id` is extracted from `info` column safely
    #             open_orders['product_id'] = open_orders['info'].apply(
    #                 lambda x: x.get('product_id').replace('-', '/') if isinstance(x, dict) else None
    #             )
    #
    #             # ✅ **Filter open orders by `trading_pair`**
    #             matching_orders = open_orders.loc[
    #                 (open_orders['product_id'] == trading_pair) & (open_orders['remaining'] > 0)
    #                 ]
    #
    #             if not matching_orders.empty:
    #                 condition = f'⚠️ Open order exists for {trading_pair}. Blocking new order.'
    #                 return base_balance, base_balance_value, valid_order, condition  # Block new order
    #
    #         # ✅ **Determine whether an order should be placed**
    #         hodling = base_currency in self.hodl
    #
    #         if side == 'buy':
    #             if adjusted_usd_balance < order_amount and order_amount > self.min_order_amount:
    #                 order_amount = round(order_amount, 2)
    #                 self.log_manager.info(
    #                     f'⚠️ Order sized reduced: Available after order submitted ${adjusted_usd_balance}. Reduced order ${order_amount} to BUY'
    #                     f' {trading_pair}.'
    #                 )
    #                 condition = f"Reduced order submitted {trading_pair}."
    #                 valid_order = True
    #             elif adjusted_usd_balance >= order_amount and (hodling or base_balance_value <= self.min_order_amount):
    #                 condition = '✅ Buy order conditions met.'
    #                 valid_order = True
    #
    #         elif side == 'sell' and not hodling:
    #             if base_balance_value >= self.min_order_amount:
    #                 condition = f'✅️ {trading_pair} has sufficient balance to sell.'
    #                 valid_order = True
    #             else:
    #                 condition = f'⚠️ {trading_pair} has insufficient balance to sell.'
    #                 valid_order = False
    #
    #         if not valid_order:
    #             self.log_manager.info(f'❌ Order validation failed for {trading_pair}: {condition}')
    #
    #         return base_balance, base_balance_value, valid_order, condition
    #
    #     except Exception as e:
    #         self.log_manager.error(f'⚠️ validate_orders: {e}', exc_info=True)
    #         return None, None, False, None
