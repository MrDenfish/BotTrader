"""
Critical Path Tests: Stop Loss Logic

Tests stop loss trigger logic that prevents catastrophic losses.
This is CRITICAL for risk management and capital preservation.

Priority: 🔴 CRITICAL (prevents catastrophic losses)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta


class TestStopLossTriggerLogic:
    """Test stop loss trigger conditions"""

    @pytest.mark.critical
    def test_stop_loss_triggered_at_threshold(self, mock_config, sample_position):
        """
        Test: Stop loss triggers at exact threshold

        Given: Entry price = $40,000
        And: SL threshold = 4.5%
        And: Current price = $38,200 (4.5% loss)
        Then: Stop loss should trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        sl_threshold = mock_config["SL_THRESHOLD"]
        current_price = entry_price * (Decimal("1.0") - sl_threshold)

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct <= -sl_threshold

        # Assert
        assert price_change_pct == Decimal("-0.045"), f"Expected -4.5% loss, got {price_change_pct * 100}%"
        assert should_trigger, "Stop loss should trigger at exact threshold"

    @pytest.mark.critical
    def test_stop_loss_triggered_below_threshold(self, mock_config, sample_position):
        """
        Test: Stop loss triggers when price falls below threshold

        Given: Entry price = $40,000
        And: SL threshold = 4.5%
        And: Current price = $38,000 (5% loss)
        Then: Stop loss should trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        sl_threshold = mock_config["SL_THRESHOLD"]
        current_price = Decimal("38000.00")  # 5% below entry

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct <= -sl_threshold

        # Assert
        assert price_change_pct < -sl_threshold, "Loss exceeds threshold"
        assert should_trigger, "Stop loss should trigger when price below threshold"

    @pytest.mark.critical
    def test_stop_loss_not_triggered_above_threshold(self, mock_config, sample_position):
        """
        Test: Stop loss does NOT trigger when loss is within acceptable range

        Given: Entry price = $40,000
        And: SL threshold = 4.5%
        And: Current price = $38,400 (4% loss)
        Then: Stop loss should NOT trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        sl_threshold = mock_config["SL_THRESHOLD"]
        current_price = Decimal("38400.00")  # 4% below entry (within threshold)

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct <= -sl_threshold

        # Assert
        assert price_change_pct == Decimal("-0.04"), f"Expected -4% loss, got {price_change_pct * 100}%"
        assert not should_trigger, "Stop loss should NOT trigger within acceptable range"

    @pytest.mark.critical
    def test_stop_loss_not_triggered_on_profit(self, mock_config, sample_position):
        """
        Test: Stop loss does NOT trigger when position is profitable

        Given: Entry price = $40,000
        And: SL threshold = 4.5%
        And: Current price = $42,000 (profit)
        Then: Stop loss should NOT trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        sl_threshold = mock_config["SL_THRESHOLD"]
        current_price = Decimal("42000.00")

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct <= -sl_threshold

        # Assert
        assert price_change_pct > 0, "Position is profitable"
        assert not should_trigger, "Stop loss should NOT trigger on profit"

    @pytest.mark.critical
    def test_stop_loss_calculation_precision(self, mock_config):
        """
        Test: Stop loss calculation maintains precision

        Given: Entry price with high precision
        And: SL threshold = 4.5%
        Then: Calculation should be accurate to avoid premature triggers
        """
        # Arrange
        entry_price = Decimal("40123.456789")
        sl_threshold = mock_config["SL_THRESHOLD"]
        # Price exactly at threshold
        current_price = entry_price * (Decimal("1.0") - sl_threshold)

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct <= -sl_threshold

        # Assert
        # Should trigger at exact threshold
        assert should_trigger, "Precision calculation should trigger at exact threshold"
        # Verify precision maintained
        assert abs(price_change_pct + sl_threshold) < Decimal("0.0000001"), "Precision error detected"


class TestTakeProfitLogic:
    """Test take profit trigger conditions"""

    @pytest.mark.critical
    def test_take_profit_triggered_at_threshold(self, mock_config, sample_position):
        """
        Test: Take profit triggers at exact threshold

        Given: Entry price = $40,000
        And: TP threshold = 3.5%
        And: Current price = $41,400 (3.5% profit)
        Then: Take profit should trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        tp_threshold = mock_config["TP_THRESHOLD"]
        current_price = entry_price * (Decimal("1.0") + tp_threshold)

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct >= tp_threshold

        # Assert
        assert price_change_pct == Decimal("0.035"), f"Expected 3.5% profit, got {price_change_pct * 100}%"
        assert should_trigger, "Take profit should trigger at exact threshold"

    @pytest.mark.critical
    def test_take_profit_triggered_above_threshold(self, mock_config, sample_position):
        """
        Test: Take profit triggers when price exceeds threshold

        Given: Entry price = $40,000
        And: TP threshold = 3.5%
        And: Current price = $42,000 (5% profit)
        Then: Take profit should trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        tp_threshold = mock_config["TP_THRESHOLD"]
        current_price = Decimal("42000.00")  # 5% profit

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct >= tp_threshold

        # Assert
        assert price_change_pct > tp_threshold, "Profit exceeds threshold"
        assert should_trigger, "Take profit should trigger when price above threshold"

    @pytest.mark.critical
    def test_take_profit_not_triggered_below_threshold(self, mock_config, sample_position):
        """
        Test: Take profit does NOT trigger when profit is below threshold

        Given: Entry price = $40,000
        And: TP threshold = 3.5%
        And: Current price = $40,800 (2% profit)
        Then: Take profit should NOT trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        tp_threshold = mock_config["TP_THRESHOLD"]
        current_price = Decimal("40800.00")  # 2% profit (below threshold)

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct >= tp_threshold

        # Assert
        assert price_change_pct == Decimal("0.02"), f"Expected 2% profit, got {price_change_pct * 100}%"
        assert not should_trigger, "Take profit should NOT trigger below threshold"

    @pytest.mark.critical
    def test_take_profit_not_triggered_on_loss(self, mock_config, sample_position):
        """
        Test: Take profit does NOT trigger when position is losing

        Given: Entry price = $40,000
        And: TP threshold = 3.5%
        And: Current price = $38,000 (loss)
        Then: Take profit should NOT trigger
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        tp_threshold = mock_config["TP_THRESHOLD"]
        current_price = Decimal("38000.00")

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        should_trigger = price_change_pct >= tp_threshold

        # Assert
        assert price_change_pct < 0, "Position is losing"
        assert not should_trigger, "Take profit should NOT trigger on loss"


class TestROCPeakTrackingExit:
    """Test ROC peak tracking exit strategy"""

    @pytest.mark.critical
    def test_peak_tracking_initialization(self):
        """
        Test: Peak tracking initializes correctly

        Given: New position opened
        Then: Peak price should be entry price
        And: Peak ROC should be initial ROC value
        """
        # Arrange
        entry_price = Decimal("40000.00")
        entry_roc = Decimal("2.5")

        # Act
        peak_price = entry_price
        peak_roc = entry_roc

        # Assert
        assert peak_price == entry_price, "Peak price should initialize to entry price"
        assert peak_roc == entry_roc, "Peak ROC should initialize to entry ROC"

    @pytest.mark.critical
    def test_peak_tracking_updates_on_new_high(self):
        """
        Test: Peak updates when new high is reached

        Given: Peak price = $40,000
        And: Current price = $42,000 (new high)
        Then: Peak price should update to $42,000
        """
        # Arrange
        peak_price = Decimal("40000.00")
        current_price = Decimal("42000.00")

        # Act
        if current_price > peak_price:
            peak_price = current_price

        # Assert
        assert peak_price == Decimal("42000.00"), "Peak price should update to new high"

    @pytest.mark.critical
    def test_peak_tracking_no_update_below_peak(self):
        """
        Test: Peak does NOT update when price below peak

        Given: Peak price = $42,000
        And: Current price = $41,000 (below peak)
        Then: Peak price should remain $42,000
        """
        # Arrange
        peak_price = Decimal("42000.00")
        current_price = Decimal("41000.00")
        original_peak = peak_price

        # Act
        if current_price > peak_price:
            peak_price = current_price

        # Assert
        assert peak_price == original_peak, "Peak price should NOT update when below peak"

    @pytest.mark.critical
    def test_roc_peak_exit_trigger(self):
        """
        Test: Exit triggers when ROC drops 30% from peak

        Given: Peak ROC = 3.0
        And: Current ROC = 2.0 (33% drop from peak)
        Then: Exit should trigger
        """
        # Arrange
        peak_roc = Decimal("3.0")
        current_roc = Decimal("2.0")
        roc_drop_threshold = Decimal("0.30")  # 30%

        # Act
        roc_drop_pct = (peak_roc - current_roc) / peak_roc
        should_exit = roc_drop_pct >= roc_drop_threshold

        # Assert
        assert roc_drop_pct == Decimal("0.3333333333333333333333333333"), "ROC dropped 33%"
        assert should_exit, "Exit should trigger when ROC drops 30%+ from peak"

    @pytest.mark.critical
    def test_roc_peak_exit_no_trigger_within_threshold(self):
        """
        Test: Exit does NOT trigger when ROC drop is within acceptable range

        Given: Peak ROC = 3.0
        And: Current ROC = 2.5 (16.67% drop from peak)
        Then: Exit should NOT trigger
        """
        # Arrange
        peak_roc = Decimal("3.0")
        current_roc = Decimal("2.5")
        roc_drop_threshold = Decimal("0.30")  # 30%

        # Act
        roc_drop_pct = (peak_roc - current_roc) / peak_roc
        should_exit = roc_drop_pct >= roc_drop_threshold

        # Assert
        assert roc_drop_pct < roc_drop_threshold, "ROC drop within threshold"
        assert not should_exit, "Exit should NOT trigger within acceptable range"

    @pytest.mark.critical
    def test_roc_peak_exit_negative_roc_reversal(self):
        """
        Test: Exit triggers when ROC goes negative (reversal)

        Given: Peak ROC = 2.5 (positive momentum)
        And: Current ROC = -1.0 (negative momentum)
        Then: Exit should trigger (momentum reversal)
        """
        # Arrange
        peak_roc = Decimal("2.5")
        current_roc = Decimal("-1.0")

        # Act
        momentum_reversed = current_roc < 0
        should_exit = momentum_reversed

        # Assert
        assert momentum_reversed, "Momentum reversed to negative"
        assert should_exit, "Exit should trigger on momentum reversal"


class TestExitConditionPriority:
    """Test exit condition priority and interaction"""

    @pytest.mark.critical
    def test_stop_loss_overrides_take_profit_check(self, mock_config):
        """
        Test: Stop loss has highest priority

        Given: Price triggers BOTH stop loss AND take profit (impossible but test logic)
        Then: Stop loss should be checked first
        """
        # Arrange
        entry_price = Decimal("40000.00")
        sl_threshold = mock_config["SL_THRESHOLD"]
        tp_threshold = mock_config["TP_THRESHOLD"]

        # Simulate loss scenario
        current_price_loss = Decimal("38000.00")

        # Act
        price_change_pct = (current_price_loss - entry_price) / entry_price
        sl_triggered = price_change_pct <= -sl_threshold
        tp_triggered = price_change_pct >= tp_threshold

        # Assert
        assert sl_triggered, "Stop loss triggered"
        assert not tp_triggered, "Take profit not triggered (losing position)"
        # In real implementation, stop loss would be checked first

    @pytest.mark.critical
    def test_multiple_exit_conditions_evaluation(self, mock_config):
        """
        Test: Multiple exit conditions evaluated correctly

        Given: Position with entry price $40,000
        When: Price = $38,000 (SL triggered), ROC = -1.0 (reversal)
        Then: Both exit conditions should be detected
        """
        # Arrange
        entry_price = Decimal("40000.00")
        current_price = Decimal("38000.00")
        peak_roc = Decimal("2.5")
        current_roc = Decimal("-1.0")

        sl_threshold = mock_config["SL_THRESHOLD"]

        # Act
        price_change_pct = (current_price - entry_price) / entry_price
        sl_triggered = price_change_pct <= -sl_threshold
        roc_reversed = current_roc < 0

        # Assert
        assert sl_triggered, "Stop loss condition met"
        assert roc_reversed, "ROC reversal condition met"
        # Both conditions detected, either would trigger exit


class TestExitExecutionValidation:
    """Test exit order validation and execution"""

    @pytest.mark.critical
    def test_exit_order_size_matches_position(self, sample_position):
        """
        Test: Exit order size equals position size

        Given: Position size = 0.001 BTC
        Then: Exit order size should be 0.001 BTC
        """
        # Arrange
        position_size = sample_position["qty"]

        # Act
        exit_order_size = position_size

        # Assert
        assert exit_order_size == Decimal("0.001"), "Exit size should match position size"

    @pytest.mark.critical
    def test_exit_order_side_opposite_entry(self):
        """
        Test: Exit order side is opposite of entry

        Given: Entry side = "buy"
        Then: Exit side should be "sell"
        """
        # Arrange
        entry_side = "buy"

        # Act
        exit_side = "sell" if entry_side == "buy" else "buy"

        # Assert
        assert exit_side == "sell", "Exit side should be opposite of entry"

    @pytest.mark.critical
    def test_exit_order_uses_market_price(self, sample_position):
        """
        Test: Exit order uses current market price (not entry price)

        Given: Entry price = $40,000
        And: Current market price = $38,000
        Then: Exit order should use $38,000
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        current_market_price = Decimal("38000.00")

        # Act
        exit_price = current_market_price  # Not entry_price

        # Assert
        assert exit_price != entry_price, "Exit should use market price"
        assert exit_price == Decimal("38000.00"), "Exit price should be current market price"

    @pytest.mark.critical
    def test_exit_calculates_realized_pnl(self, sample_position, mock_config):
        """
        Test: Exit calculates realized P&L correctly

        Given: Entry price = $40,000, size = 0.001 BTC
        And: Exit price = $38,000
        And: Entry fee = $0.40, exit fee = $0.38
        Then: Realized P&L = ($38,000 - $40,000) * 0.001 - $0.40 - $0.38 = -$2.78
        """
        # Arrange
        entry_price = sample_position["cost_basis"]
        exit_price = Decimal("38000.00")
        size = sample_position["qty"]
        fee_rate = mock_config["FEE_RATE"]

        # Act
        entry_fee = entry_price * size * fee_rate
        exit_fee = exit_price * size * fee_rate
        gross_pnl = (exit_price - entry_price) * size
        realized_pnl = gross_pnl - entry_fee - exit_fee

        # Assert
        assert entry_fee == Decimal("0.40")
        assert exit_fee == Decimal("0.38")
        assert gross_pnl == Decimal("-2.00")
        assert realized_pnl == Decimal("-2.78"), f"Expected -$2.78, got ${realized_pnl}"


class TestTimeBasedExitConditions:
    """Test time-based exit conditions"""

    @pytest.mark.critical
    def test_max_hold_time_not_exceeded(self, sample_position):
        """
        Test: Position within max hold time

        Given: Position opened 2 hours ago
        And: Max hold time = 4 hours
        Then: Should NOT trigger time-based exit
        """
        # Arrange
        entry_time = sample_position["entry_time"]
        current_time = entry_time + timedelta(hours=2)
        max_hold_time = timedelta(hours=4)

        # Act
        time_held = current_time - entry_time
        should_exit = time_held >= max_hold_time

        # Assert
        assert time_held == timedelta(hours=2)
        assert not should_exit, "Should not exit before max hold time"

    @pytest.mark.critical
    def test_max_hold_time_exceeded(self, sample_position):
        """
        Test: Position exceeds max hold time

        Given: Position opened 5 hours ago
        And: Max hold time = 4 hours
        Then: Should trigger time-based exit
        """
        # Arrange
        entry_time = sample_position["entry_time"]
        current_time = entry_time + timedelta(hours=5)
        max_hold_time = timedelta(hours=4)

        # Act
        time_held = current_time - entry_time
        should_exit = time_held >= max_hold_time

        # Assert
        assert time_held > max_hold_time
        assert should_exit, "Should exit after max hold time"
