"""
Test ATR-based Trailing Stop Logic

This test verifies the trailing stop implementation in position_monitor.py
"""

import os
from decimal import Decimal
from unittest.mock import Mock, AsyncMock
import asyncio

# Set required environment variables for testing
os.environ['MAX_LOSS_PCT'] = '0.025'
os.environ['MIN_PROFIT_PCT'] = '0.035'
os.environ['HARD_STOP_PCT'] = '0.05'
os.environ['TRAILING_STOP_ENABLED'] = 'true'
os.environ['TRAILING_STOP_ATR_PERIOD'] = '14'
os.environ['TRAILING_STOP_ATR_MULT'] = '2.0'
os.environ['TRAILING_STEP_ATR_MULT'] = '0.5'
os.environ['TRAILING_MIN_DISTANCE_PCT'] = '0.01'
os.environ['TRAILING_MAX_DISTANCE_PCT'] = '0.02'
os.environ['POSITION_CHECK_INTERVAL'] = '30'

from MarketDataManager.position_monitor import PositionMonitor


class TestTrailingStop:
    """Test the ATR-based trailing stop logic."""

    def setup(self):
        """Set up test fixtures."""
        # Create mock dependencies
        self.shared_data_manager = Mock()
        self.trade_order_manager = AsyncMock()
        self.shared_utils_precision = Mock()
        self.logger = Mock()

        # Initialize PositionMonitor
        self.monitor = PositionMonitor(
            shared_data_manager=self.shared_data_manager,
            trade_order_manager=self.trade_order_manager,
            shared_utils_precision=self.shared_utils_precision,
            logger=self.logger
        )

    async def test_trailing_stop_initialization(self):
        """Test that trailing stop state is initialized on first check."""
        print("\n=== Test 1: Trailing Stop Initialization ===")

        # Setup mock data
        product_id = "BTC-USD"
        symbol = "BTC"
        current_price = Decimal("50000.00")
        avg_entry = Decimal("49000.00")
        atr_pct = Decimal("0.02")  # 2% ATR

        self.shared_data_manager.market_data = {
            'atr_pct_cache': {product_id: atr_pct}
        }

        # First check - should initialize state
        result = await self.monitor._check_trailing_stop(symbol, product_id, current_price, avg_entry)

        assert result == False, "First check should not trigger exit"
        assert product_id in self.monitor.trailing_stops, "State should be initialized"
        assert self.monitor.trailing_stops[product_id]['last_high'] == current_price
        assert self.monitor.trailing_stops[product_id]['stop_price'] is None  # Not profitable enough yet
        print(f"✓ State initialized: {self.monitor.trailing_stops[product_id]}")

    async def test_trailing_stop_activation_when_profitable(self):
        """Test that stop is activated when position becomes profitable."""
        print("\n=== Test 2: Trailing Stop Activation ===")

        product_id = "BTC-USD"
        symbol = "BTC"
        avg_entry = Decimal("49000.00")
        current_price = Decimal("50000.00")  # +2.04% profit
        atr_pct = Decimal("0.02")  # 2% ATR

        self.shared_data_manager.market_data = {
            'atr_pct_cache': {product_id: atr_pct}
        }

        # Initialize
        await self.monitor._check_trailing_stop(symbol, product_id, current_price, avg_entry)

        # Check again with same price - should activate stop
        result = await self.monitor._check_trailing_stop(symbol, product_id, current_price, avg_entry)

        assert result == False, "Should not trigger exit yet"
        assert self.monitor.trailing_stops[product_id]['stop_price'] is not None
        stop_price = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"✓ Stop activated at: ${stop_price:.2f} (Current: ${current_price:.2f}, Entry: ${avg_entry:.2f})")

    async def test_trailing_stop_raises_on_new_high(self):
        """Test that stop is raised when price makes new high."""
        print("\n=== Test 3: Trailing Stop Raises on New High ===")

        product_id = "BTC-USD"
        symbol = "BTC"
        avg_entry = Decimal("49000.00")
        atr_pct = Decimal("0.02")  # 2% ATR

        self.shared_data_manager.market_data = {
            'atr_pct_cache': {product_id: atr_pct}
        }

        # Initialize at profitable price
        price1 = Decimal("50000.00")
        await self.monitor._check_trailing_stop(symbol, product_id, price1, avg_entry)
        await self.monitor._check_trailing_stop(symbol, product_id, price1, avg_entry)  # Activate stop

        stop1 = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"Initial stop: ${stop1:.2f} at price ${price1:.2f}")

        # Price moves higher
        price2 = Decimal("52000.00")  # +4.00% move
        await self.monitor._check_trailing_stop(symbol, product_id, price2, avg_entry)

        stop2 = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"New stop: ${stop2:.2f} at price ${price2:.2f}")

        assert stop2 > stop1, f"Stop should be raised (old: ${stop1:.2f}, new: ${stop2:.2f})"
        print(f"✓ Stop raised by ${stop2 - stop1:.2f}")

    async def test_trailing_stop_never_lowers(self):
        """Test that stop is never lowered when price decreases."""
        print("\n=== Test 4: Trailing Stop Never Lowers ===")

        product_id = "BTC-USD"
        symbol = "BTC"
        avg_entry = Decimal("49000.00")
        atr_pct = Decimal("0.02")  # 2% ATR

        self.shared_data_manager.market_data = {
            'atr_pct_cache': {product_id: atr_pct}
        }

        # Initialize at high price
        high_price = Decimal("52000.00")
        await self.monitor._check_trailing_stop(symbol, product_id, high_price, avg_entry)
        await self.monitor._check_trailing_stop(symbol, product_id, high_price, avg_entry)  # Activate

        stop_at_high = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"Stop at high: ${stop_at_high:.2f} (Price: ${high_price:.2f})")

        # Price drops but not below stop
        lower_price = Decimal("51000.00")
        await self.monitor._check_trailing_stop(symbol, product_id, lower_price, avg_entry)

        stop_after_drop = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"Stop after drop: ${stop_after_drop:.2f} (Price: ${lower_price:.2f})")

        assert stop_after_drop == stop_at_high, "Stop should not be lowered"
        print(f"✓ Stop remained at ${stop_after_drop:.2f}")

    async def test_trailing_stop_triggers_exit(self):
        """Test that exit is triggered when price falls below stop."""
        print("\n=== Test 5: Trailing Stop Triggers Exit ===")

        product_id = "BTC-USD"
        symbol = "BTC"
        avg_entry = Decimal("49000.00")
        atr_pct = Decimal("0.02")  # 2% ATR

        self.shared_data_manager.market_data = {
            'atr_pct_cache': {product_id: atr_pct}
        }

        # Initialize and activate
        price_high = Decimal("52000.00")
        await self.monitor._check_trailing_stop(symbol, product_id, price_high, avg_entry)
        await self.monitor._check_trailing_stop(symbol, product_id, price_high, avg_entry)

        stop_price = self.monitor.trailing_stops[product_id]['stop_price']
        print(f"Stop set at: ${stop_price:.2f}")

        # Price falls below stop
        price_low = stop_price - Decimal("100.00")
        result = await self.monitor._check_trailing_stop(symbol, product_id, price_low, avg_entry)

        assert result == True, f"Should trigger exit when price (${price_low:.2f}) <= stop (${stop_price:.2f})"
        assert product_id not in self.monitor.trailing_stops, "State should be cleared after trigger"
        print(f"✓ Exit triggered at ${price_low:.2f} (Stop was ${stop_price:.2f})")

    async def test_no_atr_data_skips_trailing(self):
        """Test that trailing stop is skipped when no ATR data available."""
        print("\n=== Test 6: No ATR Data Handling ===")

        product_id = "ETH-USD"
        symbol = "ETH"
        current_price = Decimal("3000.00")
        avg_entry = Decimal("2900.00")

        # No ATR data
        self.shared_data_manager.market_data = {
            'atr_pct_cache': {},
            'atr_price_cache': {}
        }

        result = await self.monitor._check_trailing_stop(symbol, product_id, current_price, avg_entry)

        assert result == False, "Should skip when no ATR data"
        assert product_id not in self.monitor.trailing_stops, "Should not initialize without ATR"
        print("✓ Correctly skipped trailing stop without ATR data")

    async def run_all_tests(self):
        """Run all tests in sequence."""
        print("\n" + "="*60)
        print("Testing ATR-Based Trailing Stop Logic")
        print("="*60)

        tests = [
            self.test_trailing_stop_initialization,
            self.test_trailing_stop_activation_when_profitable,
            self.test_trailing_stop_raises_on_new_high,
            self.test_trailing_stop_never_lowers,
            self.test_trailing_stop_triggers_exit,
            self.test_no_atr_data_skips_trailing,
        ]

        passed = 0
        failed = 0

        for test in tests:
            self.setup()  # Reset for each test
            try:
                await test()
                passed += 1
            except AssertionError as e:
                failed += 1
                print(f"✗ FAILED: {e}")
            except Exception as e:
                failed += 1
                print(f"✗ ERROR: {e}")

        print("\n" + "="*60)
        print(f"Test Results: {passed} passed, {failed} failed")
        print("="*60)

        return failed == 0


async def main():
    """Run all tests."""
    test_suite = TestTrailingStop()
    success = await test_suite.run_all_tests()

    if success:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
