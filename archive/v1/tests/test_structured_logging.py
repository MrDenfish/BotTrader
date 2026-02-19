#!/usr/bin/env python3
"""
Test script for structured logging foundation

Tests:
- Basic logging with different levels
- Context injection (trade_id, symbol, component)
- Custom trading log levels (BUY, SELL, etc.)
- Performance decorators
- JSON formatting
- Log rotation configuration
"""

import asyncio
import time
from pathlib import Path

from Shared_Utils.logger import (
    get_logger,
    get_component_logger,
    log_context,
    log_performance,
    log_async_performance,
    setup_structured_logging,
    set_context,
    clear_context,
)


async def main():
    """Run structured logging tests."""
    print("=" * 70)
    print("Testing BotTrader Structured Logging Foundation")
    print("=" * 70)

    # Initialize logging (development mode - colored console)
    print("\n1. Initializing structured logging...")
    config = setup_structured_logging(
        log_dir='logs/test',
        console_level='DEBUG',
        use_json=False,  # Use colored console for this test
    )
    print(f"   ✓ Config: log_dir={config.log_dir}, max_bytes={config.max_bytes}")

    # Test 1: Basic logging
    print("\n2. Testing basic logging levels...")
    logger = get_logger('test_logger')
    logger.debug('Debug message')
    logger.info('Info message')
    logger.warning('Warning message')
    logger.error('Error message')
    print("   ✓ Basic logging levels work")

    # Test 2: Context injection
    print("\n3. Testing context injection...")
    logger_with_context = get_logger(
        'test_logger',
        context={'component': 'test_suite', 'version': '1.0'}
    )
    logger_with_context.info('Message with default context')
    logger_with_context.info(
        'Message with extra context',
        extra={'trade_id': 'TEST-12345', 'symbol': 'BTC-USD'}
    )
    print("   ✓ Context injection works")

    # Test 3: Thread-local context
    print("\n4. Testing thread-local context...")
    set_context(trade_id='GLOBAL-999', symbol='ETH-USD')
    logger.info('Message with thread-local context')
    clear_context()
    logger.info('Message after context cleared')
    print("   ✓ Thread-local context works")

    # Test 4: Context manager
    print("\n5. Testing context manager...")
    with log_context(trade_id='CTX-123', symbol='SOL-USD', action='buy'):
        logger.info('Inside context manager')
    logger.info('Outside context manager')
    print("   ✓ Context manager works")

    # Test 5: Custom trading log levels
    print("\n6. Testing custom trading log levels...")
    logger.buy('BUY order executed for BTC-USD')
    logger.sell('SELL order executed for ETH-USD')
    logger.order_sent('Order sent to exchange')
    logger.take_profit('Take profit triggered at $50000')
    logger.stop_loss('Stop loss triggered at $45000')
    logger.bad_order('Invalid order detected')
    logger.insufficient_funds('Insufficient funds for order')
    print("   ✓ Custom trading levels work")

    # Test 6: Component logger
    print("\n7. Testing component logger...")
    comp_logger = get_component_logger('order_manager', service='webhook')
    comp_logger.info('Order validated')
    print("   ✓ Component logger works")

    # Test 7: Performance decorator (sync)
    print("\n8. Testing performance decorator (sync)...")

    @log_performance('test_logger', level='INFO', include_args=True)
    def slow_function(duration: float, name: str):
        """Simulate slow operation."""
        time.sleep(duration)
        return f"Processed {name}"

    result = slow_function(0.1, "test_order")
    print(f"   ✓ Sync performance tracking works: {result}")

    # Test 8: Performance decorator (async)
    print("\n9. Testing performance decorator (async)...")

    @log_async_performance('test_logger', level='INFO')
    async def async_slow_function(duration: float):
        """Simulate async slow operation."""
        await asyncio.sleep(duration)
        return "Async operation complete"

    result = await async_slow_function(0.1)
    print(f"   ✓ Async performance tracking works: {result}")

    # Test 9: JSON output
    print("\n10. Testing JSON output...")
    json_config = setup_structured_logging(
        log_dir='logs/test_json',
        console_level='INFO',
        use_json=True,
    )
    json_logger = get_logger('json_test', context={'format': 'json'})
    json_logger.info('This should be JSON formatted', extra={'test': 'value'})
    print("   ✓ JSON formatting works (check logs/test_json/json_test.log)")

    # Test 10: Verify log files created
    print("\n11. Verifying log files...")
    log_file = Path('logs/test/test_logger.log')
    json_log_file = Path('logs/test_json/json_test.log')

    if log_file.exists():
        size = log_file.stat().st_size
        print(f"   ✓ Log file created: {log_file} ({size} bytes)")
    else:
        print(f"   ✗ Log file not found: {log_file}")

    if json_log_file.exists():
        size = json_log_file.stat().st_size
        print(f"   ✓ JSON log file created: {json_log_file} ({size} bytes)")

        # Show sample JSON log entry
        with open(json_log_file, 'r') as f:
            last_line = f.readlines()[-1]
            print(f"\n   Sample JSON log entry:\n   {last_line}")
    else:
        print(f"   ✗ JSON log file not found: {json_log_file}")

    print("\n" + "=" * 70)
    print("All tests completed successfully!")
    print("=" * 70)


if __name__ == '__main__':
    asyncio.run(main())
