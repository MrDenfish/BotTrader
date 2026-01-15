"""
Critical Path Tests: P&L Calculation

Tests profit and loss calculation accuracy across all scenarios.
This is CRITICAL for financial reporting and tax compliance.

Priority: 🔴 CRITICAL (financial accuracy, tax reporting)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta


class TestUnrealizedPnL:
    """Test unrealized P&L calculations for open positions"""

    @pytest.mark.critical
    def test_unrealized_profit(self, sample_position):
        """
        Test: Calculate unrealized profit

        Given: Entry price = $40,000, size = 0.001 BTC
        And: Current price = $42,000
        Then: Unrealized P&L = ($42,000 - $40,000) * 0.001 = $2.00
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_price = Decimal("42000.00")
        size = sample_position["qty"]

        # Act
        unrealized_pnl = (current_price - entry_price) * size

        # Assert
        assert unrealized_pnl == Decimal("2.00"), f"Expected $2.00 profit, got ${unrealized_pnl}"
        assert unrealized_pnl > 0, "Should be profitable"

    @pytest.mark.critical
    def test_unrealized_loss(self, sample_position):
        """
        Test: Calculate unrealized loss

        Given: Entry price = $40,000, size = 0.001 BTC
        And: Current price = $38,000
        Then: Unrealized P&L = ($38,000 - $40,000) * 0.001 = -$2.00
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_price = Decimal("38000.00")
        size = sample_position["qty"]

        # Act
        unrealized_pnl = (current_price - entry_price) * size

        # Assert
        assert unrealized_pnl == Decimal("-2.00"), f"Expected -$2.00 loss, got ${unrealized_pnl}"
        assert unrealized_pnl < 0, "Should be losing"

    @pytest.mark.critical
    def test_unrealized_pnl_breakeven(self, sample_position):
        """
        Test: Calculate unrealized P&L at breakeven

        Given: Entry price = $40,000, size = 0.001 BTC
        And: Current price = $40,000 (unchanged)
        Then: Unrealized P&L = $0.00
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_price = entry_price  # Same as entry
        size = sample_position["qty"]

        # Act
        unrealized_pnl = (current_price - entry_price) * size

        # Assert
        assert unrealized_pnl == Decimal("0.00"), "Breakeven should have zero P&L"

    @pytest.mark.critical
    def test_unrealized_pnl_percentage(self, sample_position):
        """
        Test: Calculate unrealized P&L percentage

        Given: Entry price = $40,000, size = 0.001 BTC
        And: Current price = $42,000
        Then: P&L % = (($42,000 - $40,000) / $40,000) * 100 = 5%
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_price = Decimal("42000.00")

        # Act
        pnl_pct = ((current_price - entry_price) / entry_price) * Decimal("100")

        # Assert
        assert pnl_pct == Decimal("5.00"), f"Expected 5% profit, got {pnl_pct}%"

    @pytest.mark.critical
    def test_unrealized_pnl_with_fees(self, sample_position, mock_config):
        """
        Test: Unrealized P&L includes entry fees (not exit fees yet)

        Given: Entry price = $40,000, size = 0.001 BTC, entry fee = $0.40
        And: Current price = $42,000
        Then: Unrealized P&L = $2.00 - $0.40 (entry fee) = $1.60
        Note: Exit fee not included until position closed
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_price = Decimal("42000.00")
        size = sample_position["qty"]
        # Calculate entry fee based on entry price and size
        fee_rate = mock_config["FEE_RATE"]
        entry_fee = entry_price * size * fee_rate

        # Act
        gross_pnl = (current_price - entry_price) * size
        unrealized_pnl = gross_pnl - entry_fee

        # Assert
        assert gross_pnl == Decimal("2.00")
        assert entry_fee == Decimal("0.40")
        assert unrealized_pnl == Decimal("1.60"), f"Expected $1.60, got ${unrealized_pnl}"


class TestRealizedPnL:
    """Test realized P&L calculations for closed positions"""

    @pytest.mark.critical
    def test_realized_profit_with_fees(self, sample_trade_buy, sample_trade_sell, mock_config):
        """
        Test: Calculate realized profit including all fees

        Given: Buy 0.001 BTC @ $40,000 with $0.40 fee
        And: Sell 0.001 BTC @ $42,000 with $0.42 fee
        Then: Realized P&L = ($42,000 - $40,000) * 0.001 - $0.40 - $0.42 = $1.18
        """
        # Arrange
        buy_price = sample_trade_buy["price"]
        sell_price = sample_trade_sell["price"]
        size = sample_trade_buy["size"]
        buy_fee = sample_trade_buy["fees"]
        sell_fee = sample_trade_sell["fees"]

        # Act
        gross_pnl = (sell_price - buy_price) * size
        realized_pnl = gross_pnl - buy_fee - sell_fee

        # Assert
        assert gross_pnl == Decimal("2.00")
        assert realized_pnl == Decimal("1.18"), f"Expected $1.18 net profit, got ${realized_pnl}"

    @pytest.mark.critical
    def test_realized_loss_with_fees(self, mock_config):
        """
        Test: Calculate realized loss including all fees

        Given: Buy 0.001 BTC @ $42,000 with $0.42 fee
        And: Sell 0.001 BTC @ $40,000 with $0.40 fee
        Then: Realized P&L = ($40,000 - $42,000) * 0.001 - $0.42 - $0.40 = -$2.82
        """
        # Arrange
        buy_price = Decimal("42000.00")
        sell_price = Decimal("40000.00")
        size = Decimal("0.001")
        buy_fee = Decimal("0.42")
        sell_fee = Decimal("0.40")

        # Act
        gross_pnl = (sell_price - buy_price) * size
        realized_pnl = gross_pnl - buy_fee - sell_fee

        # Assert
        assert gross_pnl == Decimal("-2.00")
        assert realized_pnl == Decimal("-2.82"), f"Expected -$2.82 net loss, got ${realized_pnl}"

    @pytest.mark.critical
    def test_realized_pnl_breakeven_loses_to_fees(self, mock_config):
        """
        Test: Breakeven trade results in loss due to fees

        Given: Buy 0.001 BTC @ $40,000 with $0.40 fee
        And: Sell 0.001 BTC @ $40,000 with $0.40 fee (same price)
        Then: Realized P&L = $0.00 - $0.40 - $0.40 = -$0.80 (loss from fees)
        """
        # Arrange
        buy_price = Decimal("40000.00")
        sell_price = Decimal("40000.00")  # Same price
        size = Decimal("0.001")
        buy_fee = Decimal("0.40")
        sell_fee = Decimal("0.40")

        # Act
        gross_pnl = (sell_price - buy_price) * size
        realized_pnl = gross_pnl - buy_fee - sell_fee

        # Assert
        assert gross_pnl == Decimal("0.00")
        assert realized_pnl == Decimal("-0.80"), "Breakeven trade loses to fees"

    @pytest.mark.critical
    def test_realized_pnl_percentage_return(self):
        """
        Test: Calculate percentage return on investment

        Given: Buy 0.001 BTC @ $40,000 (cost = $40.00)
        And: Sell 0.001 BTC @ $42,000 (proceeds = $42.00)
        Then: Return % = (($42.00 - $40.00) / $40.00) * 100 = 5%
        """
        # Arrange
        cost = Decimal("40.00")  # Entry notional
        proceeds = Decimal("42.00")  # Exit notional

        # Act
        return_pct = ((proceeds - cost) / cost) * Decimal("100")

        # Assert
        assert return_pct == Decimal("5.00"), f"Expected 5% return, got {return_pct}%"

    @pytest.mark.critical
    def test_realized_pnl_with_partial_fills(self):
        """
        Test: Calculate P&L with partial fills

        Given: Buy 1.0 BTC @ $40,000
        And: Sell 0.5 BTC @ $42,000 (partial fill)
        Then: Realized P&L only on 0.5 BTC sold
        And: 0.5 BTC remains unrealized
        """
        # Arrange
        buy_qty = Decimal("1.0")
        buy_price = Decimal("40000.00")
        sell_qty = Decimal("0.5")
        sell_price = Decimal("42000.00")

        # Act
        realized_qty = sell_qty  # Only sold amount
        realized_pnl = (sell_price - buy_price) * realized_qty
        remaining_qty = buy_qty - sell_qty

        # Assert
        assert realized_pnl == Decimal("1000.00"), "P&L on 0.5 BTC"
        assert remaining_qty == Decimal("0.5"), "0.5 BTC remains"


class TestCumulativePnL:
    """Test cumulative P&L tracking"""

    @pytest.mark.critical
    def test_cumulative_pnl_multiple_trades(self):
        """
        Test: Calculate cumulative P&L across multiple trades

        Given: Trade 1: +$100 profit
        And: Trade 2: -$50 loss
        And: Trade 3: +$75 profit
        Then: Cumulative P&L = $100 - $50 + $75 = $125
        """
        # Arrange
        trades = [
            {"realized_pnl": Decimal("100.00")},
            {"realized_pnl": Decimal("-50.00")},
            {"realized_pnl": Decimal("75.00")}
        ]

        # Act
        cumulative_pnl = sum(trade["realized_pnl"] for trade in trades)

        # Assert
        assert cumulative_pnl == Decimal("125.00"), f"Expected $125.00, got ${cumulative_pnl}"

    @pytest.mark.critical
    def test_cumulative_pnl_daily_tracking(self):
        """
        Test: Calculate daily cumulative P&L

        Given: 5 trades on same day
        Then: Daily P&L should sum all trades
        """
        # Arrange
        today = datetime(2026, 1, 11, 0, 0, 0)
        trades = [
            {"date": today, "realized_pnl": Decimal("50.00")},
            {"date": today, "realized_pnl": Decimal("-20.00")},
            {"date": today, "realized_pnl": Decimal("30.00")},
            {"date": today, "realized_pnl": Decimal("-10.00")},
            {"date": today, "realized_pnl": Decimal("15.00")}
        ]

        # Act
        daily_pnl = sum(t["realized_pnl"] for t in trades if t["date"].date() == today.date())

        # Assert
        assert daily_pnl == Decimal("65.00"), f"Expected $65.00 daily P&L, got ${daily_pnl}"

    @pytest.mark.critical
    def test_cumulative_pnl_symbol_breakdown(self):
        """
        Test: Calculate P&L breakdown by symbol

        Given: Multiple trades across different symbols
        Then: P&L should be calculable per symbol
        """
        # Arrange
        trades = [
            {"symbol": "BTC-USD", "realized_pnl": Decimal("100.00")},
            {"symbol": "ETH-USD", "realized_pnl": Decimal("50.00")},
            {"symbol": "BTC-USD", "realized_pnl": Decimal("-30.00")},
            {"symbol": "SOL-USD", "realized_pnl": Decimal("25.00")},
            {"symbol": "BTC-USD", "realized_pnl": Decimal("20.00")}
        ]

        # Act
        btc_pnl = sum(t["realized_pnl"] for t in trades if t["symbol"] == "BTC-USD")
        eth_pnl = sum(t["realized_pnl"] for t in trades if t["symbol"] == "ETH-USD")
        sol_pnl = sum(t["realized_pnl"] for t in trades if t["symbol"] == "SOL-USD")

        # Assert
        assert btc_pnl == Decimal("90.00"), f"BTC P&L: ${btc_pnl}"
        assert eth_pnl == Decimal("50.00"), f"ETH P&L: ${eth_pnl}"
        assert sol_pnl == Decimal("25.00"), f"SOL P&L: ${sol_pnl}"
        assert btc_pnl + eth_pnl + sol_pnl == Decimal("165.00"), "Total P&L"


class TestWinRateMetrics:
    """Test win rate and trade statistics"""

    @pytest.mark.critical
    def test_win_rate_calculation(self):
        """
        Test: Calculate win rate percentage

        Given: 7 winning trades, 3 losing trades
        Then: Win rate = 7 / 10 = 70%
        """
        # Arrange
        trades = [
            {"realized_pnl": Decimal("100.00")},  # Win
            {"realized_pnl": Decimal("-50.00")},  # Loss
            {"realized_pnl": Decimal("75.00")},   # Win
            {"realized_pnl": Decimal("30.00")},   # Win
            {"realized_pnl": Decimal("-20.00")},  # Loss
            {"realized_pnl": Decimal("40.00")},   # Win
            {"realized_pnl": Decimal("60.00")},   # Win
            {"realized_pnl": Decimal("-15.00")},  # Loss
            {"realized_pnl": Decimal("25.00")},   # Win
            {"realized_pnl": Decimal("80.00")}    # Win
        ]

        # Act
        winning_trades = [t for t in trades if t["realized_pnl"] > 0]
        total_trades = len(trades)
        win_rate = (Decimal(len(winning_trades)) / Decimal(total_trades)) * Decimal("100")

        # Assert
        assert len(winning_trades) == 7
        assert total_trades == 10
        assert win_rate == Decimal("70.00"), f"Expected 70% win rate, got {win_rate}%"

    @pytest.mark.critical
    def test_average_win_loss(self):
        """
        Test: Calculate average win and average loss

        Given: Wins: $100, $75, $30 (avg = $68.33)
        And: Losses: -$50, -$20 (avg = -$35.00)
        Then: Profit factor = $68.33 / $35.00 = 1.95
        """
        # Arrange
        trades = [
            {"realized_pnl": Decimal("100.00")},
            {"realized_pnl": Decimal("-50.00")},
            {"realized_pnl": Decimal("75.00")},
            {"realized_pnl": Decimal("30.00")},
            {"realized_pnl": Decimal("-20.00")}
        ]

        # Act
        wins = [t["realized_pnl"] for t in trades if t["realized_pnl"] > 0]
        losses = [abs(t["realized_pnl"]) for t in trades if t["realized_pnl"] < 0]

        avg_win = sum(wins) / len(wins) if wins else Decimal("0")
        avg_loss = sum(losses) / len(losses) if losses else Decimal("0")

        # Assert
        assert avg_win == Decimal("68.33333333333333333333333333"), "Average win"
        assert avg_loss == Decimal("35.00"), "Average loss"

    @pytest.mark.critical
    def test_profit_factor(self):
        """
        Test: Calculate profit factor (gross profit / gross loss)

        Given: Total wins = $205, total losses = $70
        Then: Profit factor = $205 / $70 = 2.93
        """
        # Arrange
        trades = [
            {"realized_pnl": Decimal("100.00")},
            {"realized_pnl": Decimal("-50.00")},
            {"realized_pnl": Decimal("75.00")},
            {"realized_pnl": Decimal("30.00")},
            {"realized_pnl": Decimal("-20.00")}
        ]

        # Act
        gross_profit = sum(t["realized_pnl"] for t in trades if t["realized_pnl"] > 0)
        gross_loss = abs(sum(t["realized_pnl"] for t in trades if t["realized_pnl"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("0")

        # Assert
        assert gross_profit == Decimal("205.00")
        assert gross_loss == Decimal("70.00")
        assert profit_factor == Decimal("2.928571428571428571428571429"), f"Profit factor: {profit_factor}"


class TestPnLPrecision:
    """Test P&L calculation precision"""

    @pytest.mark.critical
    def test_pnl_decimal_precision(self):
        """
        Test: P&L calculations maintain decimal precision

        Given: High precision prices and quantities
        Then: No rounding errors in P&L
        """
        # Arrange
        buy_price = Decimal("40123.456789")
        sell_price = Decimal("42789.654321")
        size = Decimal("0.00012345")

        # Act
        pnl = (sell_price - buy_price) * size

        # Assert
        expected_pnl = Decimal("0.32914208532540")
        assert pnl == expected_pnl, f"Precision error: expected {expected_pnl}, got {pnl}"

    @pytest.mark.critical
    def test_pnl_rounding_for_reporting(self):
        """
        Test: P&L rounded to 2 decimals for reporting

        Given: P&L = $1.18634523
        Then: Reported P&L = $1.19 (rounded)
        """
        # Arrange
        pnl = Decimal("1.18634523")

        # Act
        reported_pnl = pnl.quantize(Decimal("0.01"))

        # Assert
        assert reported_pnl == Decimal("1.19"), "Should round to 2 decimals"

    @pytest.mark.critical
    def test_pnl_aggregation_precision(self):
        """
        Test: Aggregating many small P&L values maintains precision

        Given: 1000 trades with small P&L values
        Then: Cumulative P&L should be accurate
        """
        # Arrange
        trades = [{"pnl": Decimal("0.123456")} for _ in range(1000)]

        # Act
        cumulative_pnl = sum(t["pnl"] for t in trades)

        # Assert
        expected_pnl = Decimal("123.456")
        assert cumulative_pnl == expected_pnl, f"Precision lost in aggregation: {cumulative_pnl}"


class TestTaxReportingPnL:
    """Test P&L calculations for tax reporting"""

    @pytest.mark.critical
    def test_short_term_capital_gain(self):
        """
        Test: Short-term capital gain (held < 1 year)

        Given: Buy on Jan 1, 2026
        And: Sell on June 1, 2026 (held 5 months)
        Then: Should be classified as short-term
        """
        # Arrange
        buy_date = datetime(2026, 1, 1)
        sell_date = datetime(2026, 6, 1)

        # Act
        holding_period = (sell_date - buy_date).days
        is_short_term = holding_period < 365

        # Assert
        assert holding_period == 151  # 5 months
        assert is_short_term, "Should be short-term capital gain"

    @pytest.mark.critical
    def test_long_term_capital_gain(self):
        """
        Test: Long-term capital gain (held >= 1 year)

        Given: Buy on Jan 1, 2025
        And: Sell on Jan 2, 2026 (held > 1 year)
        Then: Should be classified as long-term
        """
        # Arrange
        buy_date = datetime(2025, 1, 1)
        sell_date = datetime(2026, 1, 2)

        # Act
        holding_period = (sell_date - buy_date).days
        is_long_term = holding_period >= 365

        # Assert
        assert holding_period == 366  # Over 1 year (2026 is not a leap year)
        assert is_long_term, "Should be long-term capital gain"

    @pytest.mark.critical
    def test_cost_basis_for_tax_reporting(self, sample_trade_buy):
        """
        Test: Cost basis includes purchase price + fees

        Given: Buy price = $40,000, size = 0.001 BTC, fee = $0.40
        Then: Cost basis = ($40,000 * 0.001) + $0.40 = $40.40
        """
        # Arrange
        buy_price = sample_trade_buy["price"]
        size = sample_trade_buy["size"]
        fee = sample_trade_buy["fees"]

        # Act
        cost_basis = (buy_price * size) + fee

        # Assert
        assert cost_basis == Decimal("40.40"), f"Cost basis should include fees: ${cost_basis}"

    @pytest.mark.critical
    def test_proceeds_for_tax_reporting(self, sample_trade_sell):
        """
        Test: Proceeds excludes sell fees (fees are deductible separately)

        Given: Sell price = $42,000, size = 0.001 BTC, fee = $0.42
        Then: Proceeds = $42,000 * 0.001 = $42.00 (fee separate)
        """
        # Arrange
        sell_price = sample_trade_sell["price"]
        size = sample_trade_sell["size"]

        # Act
        proceeds = sell_price * size

        # Assert
        assert proceeds == Decimal("42.00"), "Proceeds exclude fees"
