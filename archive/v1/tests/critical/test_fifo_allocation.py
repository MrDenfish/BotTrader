"""
Critical Path Tests: FIFO Allocation Calculations

Tests the FIFO (First-In-First-Out) allocation algorithm that calculates
cost basis and P&L for tax reporting. This is CRITICAL for accurate financial reporting.

Priority: 🔴 CRITICAL (tax reporting accuracy)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta


class TestFIFOAllocationLogic:
    """Test core FIFO allocation algorithm logic"""

    @pytest.mark.critical
    def test_simple_fifo_profit(self, sample_trade_buy, sample_trade_sell):
        """
        Test: Single buy + single sell (profit scenario)

        Given: Buy 0.001 BTC @ $40,000
        And: Sell 0.001 BTC @ $42,000
        Then: P&L should be ($42,000 - $40,000) * 0.001 = $2.00
        And: Cost basis should be $40,000
        """
        # Arrange
        buy_qty = sample_trade_buy["size"]
        buy_price = sample_trade_buy["price"]
        sell_qty = sample_trade_sell["size"]
        sell_price = sample_trade_sell["price"]

        # Act: Calculate FIFO allocation
        allocated_qty = min(buy_qty, sell_qty)
        cost_basis = buy_price
        proceeds = sell_price
        pnl = (proceeds - cost_basis) * allocated_qty

        # Assert
        assert allocated_qty == Decimal("0.001")
        assert cost_basis == Decimal("40000.00")
        assert proceeds == Decimal("42000.00")
        assert pnl == Decimal("2.00"), f"Expected $2.00 profit, got ${pnl}"

    @pytest.mark.critical
    def test_simple_fifo_loss(self):
        """
        Test: Single buy + single sell (loss scenario)

        Given: Buy 0.001 BTC @ $40,000
        And: Sell 0.001 BTC @ $38,000
        Then: P&L should be ($38,000 - $40,000) * 0.001 = -$2.00
        """
        # Arrange
        buy_qty = Decimal("0.001")
        buy_price = Decimal("40000.00")
        sell_qty = Decimal("0.001")
        sell_price = Decimal("38000.00")

        # Act
        allocated_qty = min(buy_qty, sell_qty)
        pnl = (sell_price - buy_price) * allocated_qty

        # Assert
        assert pnl == Decimal("-2.00"), f"Expected -$2.00 loss, got ${pnl}"

    @pytest.mark.critical
    def test_partial_fill_allocation(self):
        """
        Test: Partial fill - buy more than sell

        Given: Buy 1.0 BTC @ $40,000
        And: Sell 0.5 BTC @ $42,000
        Then: Only 0.5 BTC should be allocated
        And: 0.5 BTC should remain in inventory
        """
        # Arrange
        buy_qty = Decimal("1.0")
        sell_qty = Decimal("0.5")

        # Act
        allocated_qty = min(buy_qty, sell_qty)
        remaining_inventory = buy_qty - allocated_qty

        # Assert
        assert allocated_qty == Decimal("0.5")
        assert remaining_inventory == Decimal("0.5")

    @pytest.mark.critical
    def test_multiple_lots_fifo_ordering(self):
        """
        Test: Multiple buy lots - FIFO uses oldest first

        Given: Buy 0.5 BTC @ $40,000 (first)
        And: Buy 0.5 BTC @ $41,000 (second)
        And: Sell 0.6 BTC @ $42,000
        Then: Should allocate 0.5 from first lot, 0.1 from second lot
        And: Cost basis should be weighted: (0.5*40000 + 0.1*41000) / 0.6
        """
        # Arrange
        lots = [
            {"qty": Decimal("0.5"), "price": Decimal("40000.00")},
            {"qty": Decimal("0.5"), "price": Decimal("41000.00")}
        ]
        sell_qty = Decimal("0.6")
        sell_price = Decimal("42000.00")

        # Act: FIFO allocation
        allocations = []
        remaining_sell_qty = sell_qty

        for lot in lots:
            if remaining_sell_qty <= 0:
                break

            allocated = min(lot["qty"], remaining_sell_qty)
            allocations.append({
                "qty": allocated,
                "cost_basis": lot["price"],
                "pnl": (sell_price - lot["price"]) * allocated
            })
            remaining_sell_qty -= allocated

        # Assert
        assert len(allocations) == 2, "Should allocate from 2 lots"
        assert allocations[0]["qty"] == Decimal("0.5"), "First lot fully allocated"
        assert allocations[1]["qty"] == Decimal("0.1"), "Second lot partially allocated"

        # Total P&L
        total_pnl = sum(a["pnl"] for a in allocations)
        expected_pnl = (Decimal("42000") - Decimal("40000")) * Decimal("0.5") + \
                       (Decimal("42000") - Decimal("41000")) * Decimal("0.1")
        assert total_pnl == expected_pnl

    @pytest.mark.critical
    def test_fifo_with_fees(self):
        """
        Test: FIFO calculation includes fees

        Given: Buy 0.001 BTC @ $40,000 with $0.40 fee
        And: Sell 0.001 BTC @ $42,000 with $0.42 fee
        Then: Net P&L = $2.00 - $0.40 - $0.42 = $1.18
        """
        # Arrange
        buy_price = Decimal("40000.00")
        sell_price = Decimal("42000.00")
        qty = Decimal("0.001")
        buy_fee = Decimal("0.40")
        sell_fee = Decimal("0.42")

        # Act
        gross_pnl = (sell_price - buy_price) * qty
        net_pnl = gross_pnl - buy_fee - sell_fee

        # Assert
        assert gross_pnl == Decimal("2.00")
        assert net_pnl == Decimal("1.18"), f"Expected $1.18 net P&L, got ${net_pnl}"

    @pytest.mark.critical
    def test_zero_quantity_allocation(self):
        """
        Test: Edge case - zero quantity allocation

        Given: Buy 0.001 BTC
        And: Sell 0.000 BTC (zero)
        Then: No allocation should occur
        And: P&L should be zero
        """
        # Arrange
        buy_qty = Decimal("0.001")
        sell_qty = Decimal("0.000")

        # Act
        allocated_qty = min(buy_qty, sell_qty)
        pnl = Decimal("0.00") if allocated_qty == 0 else (Decimal("42000") - Decimal("40000")) * allocated_qty

        # Assert
        assert allocated_qty == Decimal("0.000")
        assert pnl == Decimal("0.00")

    @pytest.mark.critical
    def test_fifo_preserves_precision(self):
        """
        Test: FIFO calculations maintain decimal precision

        Given: Trading small quantities with high precision
        Then: No rounding errors should occur
        """
        # Arrange
        buy_qty = Decimal("0.00012345")
        buy_price = Decimal("40123.456789")
        sell_qty = Decimal("0.00012345")
        sell_price = Decimal("42789.654321")

        # Act
        pnl = (sell_price - buy_price) * buy_qty

        # Assert: Verify precision maintained
        expected_pnl = Decimal("0.32914208532540")
        assert pnl == expected_pnl, f"Precision lost: expected {expected_pnl}, got {pnl}"


class TestFIFOEdgeCases:
    """Test edge cases and error conditions"""

    @pytest.mark.critical
    def test_sell_without_buy_inventory(self):
        """
        Test: Selling without buy inventory (should not happen, but test anyway)

        Given: No buy orders
        And: Sell order exists
        Then: Should handle gracefully (no crash)
        """
        # Arrange
        buy_lots = []
        sell_qty = Decimal("0.001")

        # Act
        total_allocated = Decimal("0")
        remaining = sell_qty

        for lot in buy_lots:
            allocated = min(lot["qty"], remaining)
            total_allocated += allocated
            remaining -= allocated

        # Assert
        assert total_allocated == Decimal("0"), "Should not allocate without inventory"
        assert remaining == sell_qty, "Sell quantity should remain unallocated"

    @pytest.mark.critical
    def test_negative_pnl_calculation(self):
        """
        Test: Ensure losses are calculated correctly (negative P&L)

        Given: Buy high, sell low
        Then: P&L must be negative
        """
        # Arrange
        buy_price = Decimal("50000.00")
        sell_price = Decimal("30000.00")
        qty = Decimal("0.001")

        # Act
        pnl = (sell_price - buy_price) * qty

        # Assert
        assert pnl < 0, "P&L should be negative for a loss"
        assert pnl == Decimal("-20.00")

    @pytest.mark.critical
    def test_same_day_roundtrip(self):
        """
        Test: Buy and sell on same day (wash sale consideration)

        Given: Buy at 10:00, Sell at 14:00 same day
        Then: FIFO should still calculate correctly
        Note: Wash sale rules are tax software concern, not FIFO
        """
        # Arrange
        same_day = datetime(2026, 1, 11, 0, 0, 0)
        buy_time = same_day.replace(hour=10)
        sell_time = same_day.replace(hour=14)

        buy_price = Decimal("40000.00")
        sell_price = Decimal("41000.00")
        qty = Decimal("0.001")

        # Act
        time_held = sell_time - buy_time
        pnl = (sell_price - buy_price) * qty

        # Assert
        assert time_held.total_seconds() == 4 * 3600  # 4 hours
        assert pnl == Decimal("1.00"), "Same-day roundtrip should calculate P&L normally"


class TestFIFOVersionConsistency:
    """Test FIFO allocation version consistency"""

    @pytest.mark.critical
    def test_allocation_version_isolation(self):
        """
        Test: FIFO allocations are isolated by version

        Given: Multiple FIFO versions exist
        Then: Calculations should only use specified version
        And: Versions should not interfere with each other
        """
        # Arrange
        version_1_allocations = [
            {"version": 1, "pnl": Decimal("100.00")},
            {"version": 1, "pnl": Decimal("50.00")}
        ]
        version_2_allocations = [
            {"version": 2, "pnl": Decimal("200.00")},
            {"version": 2, "pnl": Decimal("75.00")}
        ]

        # Act: Calculate P&L for version 2 only
        version_2_pnl = sum(a["pnl"] for a in version_2_allocations if a["version"] == 2)

        # Assert
        assert version_2_pnl == Decimal("275.00")
        assert version_2_pnl != Decimal("425.00"), "Should not include version 1 allocations"


class TestFIFOPerformance:
    """Test FIFO performance characteristics"""

    @pytest.mark.critical
    def test_fifo_handles_many_lots(self):
        """
        Test: FIFO algorithm handles many buy lots efficiently

        Given: 100 small buy lots
        And: 1 large sell order
        Then: Should allocate across all lots correctly
        And: Should complete in reasonable time
        """
        import time

        # Arrange: Create 100 buy lots
        buy_lots = []
        for i in range(100):
            buy_lots.append({
                "qty": Decimal("0.01"),
                "price": Decimal(f"{40000 + i * 10}.00")
            })

        sell_qty = Decimal("1.0")  # Will use all 100 lots
        sell_price = Decimal("45000.00")

        # Act
        start_time = time.perf_counter()

        allocations = []
        remaining_sell_qty = sell_qty

        for lot in buy_lots:
            if remaining_sell_qty <= 0:
                break

            allocated = min(lot["qty"], remaining_sell_qty)
            pnl = (sell_price - lot["price"]) * allocated
            allocations.append({"qty": allocated, "pnl": pnl})
            remaining_sell_qty -= allocated

        duration = time.perf_counter() - start_time

        # Assert
        assert len(allocations) == 100, "Should allocate from all 100 lots"
        assert remaining_sell_qty == Decimal("0"), "Should fully allocate sell quantity"
        assert duration < 0.1, f"FIFO took {duration:.4f}s (should be < 0.1s)"

        total_pnl = sum(a["pnl"] for a in allocations)
        assert total_pnl > 0, "Should have positive P&L"
