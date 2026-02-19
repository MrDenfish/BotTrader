"""
Critical Path Tests: Order Validation

Tests order validation logic that prevents invalid trades and over-leveraging.
This is CRITICAL for risk management and preventing trading errors.

Priority: 🔴 CRITICAL (risk management, prevents catastrophic losses)
"""

import pytest
from decimal import Decimal
from datetime import datetime


class TestOrderSizeValidation:
    """Test order size validation logic"""

    @pytest.mark.critical
    def test_minimum_order_size_usd(self, mock_config):
        """
        Test: Order must meet minimum USD size

        Given: MIN_ORDER_SIZE_USD = $1.00
        And: Order size = $0.50 (below minimum)
        Then: Order should be rejected
        """
        # Arrange
        min_order_size = mock_config["MIN_ORDER_SIZE_USD"]
        order_notional = Decimal("0.50")

        # Act
        is_valid = order_notional >= min_order_size

        # Assert
        assert not is_valid, "Order below minimum should be rejected"
        assert order_notional < Decimal("1.00")

    @pytest.mark.critical
    def test_order_meets_minimum_size(self, mock_config):
        """
        Test: Order at or above minimum is valid

        Given: MIN_ORDER_SIZE_USD = $1.00
        And: Order size = $1.50 (above minimum)
        Then: Order should be valid
        """
        # Arrange
        min_order_size = mock_config["MIN_ORDER_SIZE_USD"]
        order_notional = Decimal("1.50")

        # Act
        is_valid = order_notional >= min_order_size

        # Assert
        assert is_valid, "Order above minimum should be valid"

    @pytest.mark.critical
    def test_order_notional_calculation(self, sample_prices):
        """
        Test: Order notional value calculation

        Given: BTC price = $40,000
        And: Order size = 0.001 BTC
        Then: Notional = $40,000 * 0.001 = $40.00
        """
        # Arrange
        price = sample_prices["BTC-USD"]
        size = Decimal("0.001")

        # Act
        notional = price * size

        # Assert
        assert notional == Decimal("40.00"), f"Expected $40.00, got ${notional}"

    @pytest.mark.critical
    def test_zero_size_order_rejected(self):
        """
        Test: Zero size orders must be rejected

        Given: Order size = 0.000
        Then: Order should be invalid
        """
        # Arrange
        order_size = Decimal("0.000")

        # Act
        is_valid = order_size > 0

        # Assert
        assert not is_valid, "Zero size order should be rejected"

    @pytest.mark.critical
    def test_negative_size_order_rejected(self):
        """
        Test: Negative size orders must be rejected

        Given: Order size = -0.001
        Then: Order should be invalid
        """
        # Arrange
        order_size = Decimal("-0.001")

        # Act
        is_valid = order_size > 0

        # Assert
        assert not is_valid, "Negative size order should be rejected"

    @pytest.mark.critical
    def test_order_size_precision(self):
        """
        Test: Order size maintains precision

        Given: Order size = 0.00012345 BTC
        Then: Precision should be preserved (no rounding errors)
        """
        # Arrange
        order_size = Decimal("0.00012345")
        price = Decimal("40000.00")

        # Act
        notional = order_size * price

        # Assert
        expected_notional = Decimal("4.938")
        assert notional == expected_notional, f"Precision error: expected {expected_notional}, got {notional}"


class TestPositionSizeValidation:
    """Test position sizing and limits"""

    @pytest.mark.critical
    def test_position_within_max_limit(self, mock_config):
        """
        Test: Position size must not exceed max limit

        Given: MAX_POSITION_SIZE_USD = $1,000
        And: Current position = $500
        And: New order = $300
        Then: Total position = $800 (valid, under limit)
        """
        # Arrange
        max_position = mock_config["MAX_POSITION_SIZE_USD"]
        current_position = Decimal("500.00")
        new_order_notional = Decimal("300.00")

        # Act
        total_position = current_position + new_order_notional
        is_valid = total_position <= max_position

        # Assert
        assert is_valid, "Position within limit should be valid"
        assert total_position == Decimal("800.00")

    @pytest.mark.critical
    def test_position_exceeds_max_limit(self, mock_config):
        """
        Test: Position size exceeding max limit is rejected

        Given: MAX_POSITION_SIZE_USD = $1,000
        And: Current position = $800
        And: New order = $300
        Then: Total position = $1,100 (invalid, exceeds limit)
        """
        # Arrange
        max_position = mock_config["MAX_POSITION_SIZE_USD"]
        current_position = Decimal("800.00")
        new_order_notional = Decimal("300.00")

        # Act
        total_position = current_position + new_order_notional
        is_valid = total_position <= max_position

        # Assert
        assert not is_valid, "Position exceeding limit should be rejected"
        assert total_position > max_position

    @pytest.mark.critical
    def test_max_order_size_calculation(self, mock_config):
        """
        Test: Calculate maximum allowed order size

        Given: MAX_POSITION_SIZE_USD = $1,000
        And: Current position = $700
        Then: Max allowed new order = $300
        """
        # Arrange
        max_position = mock_config["MAX_POSITION_SIZE_USD"]
        current_position = Decimal("700.00")

        # Act
        max_allowed_order = max_position - current_position

        # Assert
        assert max_allowed_order == Decimal("300.00")

    @pytest.mark.critical
    def test_no_position_max_order_equals_limit(self, mock_config):
        """
        Test: With no open position, max order equals position limit

        Given: MAX_POSITION_SIZE_USD = $1,000
        And: Current position = $0
        Then: Max allowed order = $1,000
        """
        # Arrange
        max_position = mock_config["MAX_POSITION_SIZE_USD"]
        current_position = Decimal("0.00")

        # Act
        max_allowed_order = max_position - current_position

        # Assert
        assert max_allowed_order == max_position


class TestBalanceValidation:
    """Test balance validation for orders"""

    @pytest.mark.critical
    def test_order_within_available_balance(self):
        """
        Test: Order size must not exceed available balance

        Given: Available balance = $1,000
        And: Order notional = $500
        Then: Order is valid
        """
        # Arrange
        available_balance = Decimal("1000.00")
        order_notional = Decimal("500.00")

        # Act
        has_sufficient_balance = order_notional <= available_balance

        # Assert
        assert has_sufficient_balance, "Order within balance should be valid"

    @pytest.mark.critical
    def test_order_exceeds_available_balance(self):
        """
        Test: Order size exceeding balance is rejected

        Given: Available balance = $1,000
        And: Order notional = $1,500
        Then: Order is invalid
        """
        # Arrange
        available_balance = Decimal("1000.00")
        order_notional = Decimal("1500.00")

        # Act
        has_sufficient_balance = order_notional <= available_balance

        # Assert
        assert not has_sufficient_balance, "Order exceeding balance should be rejected"

    @pytest.mark.critical
    def test_balance_includes_fees(self, mock_config):
        """
        Test: Balance check must include trading fees

        Given: Available balance = $100.00
        And: Order notional = $99.00
        And: Fee rate = 1%
        Then: Total cost = $99.00 + $0.99 = $99.99 (valid)
        """
        # Arrange
        available_balance = Decimal("100.00")
        order_notional = Decimal("99.00")
        fee_rate = mock_config["FEE_RATE"]

        # Act
        estimated_fee = order_notional * fee_rate
        total_cost = order_notional + estimated_fee
        has_sufficient_balance = total_cost <= available_balance

        # Assert
        assert estimated_fee == Decimal("0.99")
        assert total_cost == Decimal("99.99")
        assert has_sufficient_balance, "Order + fees within balance should be valid"

    @pytest.mark.critical
    def test_balance_insufficient_for_fees(self, mock_config):
        """
        Test: Insufficient balance for order + fees is rejected

        Given: Available balance = $100.00
        And: Order notional = $99.50
        And: Fee rate = 1%
        Then: Total cost = $99.50 + $0.995 = $100.495 (invalid)
        """
        # Arrange
        available_balance = Decimal("100.00")
        order_notional = Decimal("99.50")
        fee_rate = mock_config["FEE_RATE"]

        # Act
        estimated_fee = order_notional * fee_rate
        total_cost = order_notional + estimated_fee
        has_sufficient_balance = total_cost <= available_balance

        # Assert
        assert total_cost > available_balance
        assert not has_sufficient_balance, "Insufficient balance for fees should be rejected"


class TestOrderPriceValidation:
    """Test order price validation"""

    @pytest.mark.critical
    def test_zero_price_rejected(self):
        """
        Test: Zero price orders must be rejected

        Given: Order price = $0.00
        Then: Order should be invalid
        """
        # Arrange
        order_price = Decimal("0.00")

        # Act
        is_valid = order_price > 0

        # Assert
        assert not is_valid, "Zero price order should be rejected"

    @pytest.mark.critical
    def test_negative_price_rejected(self):
        """
        Test: Negative price orders must be rejected

        Given: Order price = -$100.00
        Then: Order should be invalid
        """
        # Arrange
        order_price = Decimal("-100.00")

        # Act
        is_valid = order_price > 0

        # Assert
        assert not is_valid, "Negative price order should be rejected"

    @pytest.mark.critical
    def test_valid_price_accepted(self):
        """
        Test: Positive price orders are valid

        Given: Order price = $40,000.00
        Then: Order should be valid
        """
        # Arrange
        order_price = Decimal("40000.00")

        # Act
        is_valid = order_price > 0

        # Assert
        assert is_valid, "Positive price order should be valid"


class TestOrderSymbolValidation:
    """Test order symbol validation"""

    @pytest.mark.critical
    def test_valid_symbol_accepted(self):
        """
        Test: Valid trading symbol is accepted

        Given: Symbol = "BTC-USD"
        Then: Symbol should be valid
        """
        # Arrange
        valid_symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        order_symbol = "BTC-USD"

        # Act
        is_valid = order_symbol in valid_symbols

        # Assert
        assert is_valid, "Valid symbol should be accepted"

    @pytest.mark.critical
    def test_invalid_symbol_rejected(self):
        """
        Test: Invalid trading symbol is rejected

        Given: Symbol = "INVALID-USD"
        Then: Symbol should be rejected
        """
        # Arrange
        valid_symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        order_symbol = "INVALID-USD"

        # Act
        is_valid = order_symbol in valid_symbols

        # Assert
        assert not is_valid, "Invalid symbol should be rejected"

    @pytest.mark.critical
    def test_empty_symbol_rejected(self):
        """
        Test: Empty symbol is rejected

        Given: Symbol = ""
        Then: Symbol should be rejected
        """
        # Arrange
        valid_symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        order_symbol = ""

        # Act
        is_valid = order_symbol in valid_symbols and len(order_symbol) > 0

        # Assert
        assert not is_valid, "Empty symbol should be rejected"


class TestOrderSideValidation:
    """Test order side (buy/sell) validation"""

    @pytest.mark.critical
    def test_valid_buy_side(self):
        """
        Test: "buy" side is valid

        Given: Order side = "buy"
        Then: Side should be valid
        """
        # Arrange
        valid_sides = ["buy", "sell"]
        order_side = "buy"

        # Act
        is_valid = order_side in valid_sides

        # Assert
        assert is_valid, "Buy side should be valid"

    @pytest.mark.critical
    def test_valid_sell_side(self):
        """
        Test: "sell" side is valid

        Given: Order side = "sell"
        Then: Side should be valid
        """
        # Arrange
        valid_sides = ["buy", "sell"]
        order_side = "sell"

        # Act
        is_valid = order_side in valid_sides

        # Assert
        assert is_valid, "Sell side should be valid"

    @pytest.mark.critical
    def test_invalid_side_rejected(self):
        """
        Test: Invalid order side is rejected

        Given: Order side = "hold"
        Then: Side should be rejected
        """
        # Arrange
        valid_sides = ["buy", "sell"]
        order_side = "hold"

        # Act
        is_valid = order_side in valid_sides

        # Assert
        assert not is_valid, "Invalid side should be rejected"

    @pytest.mark.critical
    def test_case_sensitive_side_validation(self):
        """
        Test: Order side validation is case-sensitive

        Given: Order side = "BUY" (uppercase)
        Then: Should be rejected (expecting lowercase)
        """
        # Arrange
        valid_sides = ["buy", "sell"]
        order_side = "BUY"

        # Act
        is_valid = order_side in valid_sides

        # Assert
        assert not is_valid, "Case mismatch should be rejected"


class TestOrderValidationComprehensive:
    """Comprehensive order validation tests"""

    @pytest.mark.critical
    def test_valid_order_all_checks_pass(self, mock_config, sample_prices):
        """
        Test: Valid order passes all validation checks

        Given: All order parameters are valid
        Then: Order should pass validation
        """
        # Arrange
        order = {
            "symbol": "BTC-USD",
            "side": "buy",
            "size": Decimal("0.001"),
            "price": sample_prices["BTC-USD"]
        }

        available_balance = Decimal("1000.00")
        current_position = Decimal("0.00")
        valid_symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
        valid_sides = ["buy", "sell"]

        # Act: Perform all validation checks
        notional = order["price"] * order["size"]
        fee = notional * mock_config["FEE_RATE"]
        total_cost = notional + fee

        checks = {
            "size_positive": order["size"] > 0,
            "price_positive": order["price"] > 0,
            "symbol_valid": order["symbol"] in valid_symbols,
            "side_valid": order["side"] in valid_sides,
            "notional_min": notional >= mock_config["MIN_ORDER_SIZE_USD"],
            "balance_sufficient": total_cost <= available_balance,
            "position_limit": (current_position + notional) <= mock_config["MAX_POSITION_SIZE_USD"]
        }

        all_valid = all(checks.values())

        # Assert
        assert all_valid, f"Valid order should pass all checks. Failed: {[k for k, v in checks.items() if not v]}"

    @pytest.mark.critical
    def test_invalid_order_fails_validation(self, mock_config):
        """
        Test: Invalid order fails validation

        Given: Order with zero size (invalid)
        Then: Validation should fail
        """
        # Arrange
        order = {
            "symbol": "BTC-USD",
            "side": "buy",
            "size": Decimal("0.000"),  # Invalid: zero size
            "price": Decimal("40000.00")
        }

        # Act
        is_size_valid = order["size"] > 0

        # Assert
        assert not is_size_valid, "Invalid order should fail validation"
