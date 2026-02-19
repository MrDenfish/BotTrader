#!/usr/bin/env python3
"""
Quick test script to verify FIFO integration in daily report.
Tests both FIFO disabled and enabled modes without sending email.
"""
import os
import sys

# Test with FIFO disabled (default)
print("=" * 80)
print("TEST 1: FIFO DISABLED (Backward Compatibility)")
print("=" * 80)
os.environ['USE_FIFO_ALLOCATIONS'] = '0'

# Import after setting env var
from botreport.aws_daily_report import run_queries, get_db_conn, build_html

# Get database connection
conn = get_db_conn()

# Run queries
try:
    total_pnl, open_pos, recent_trades, errors, detect_notes, fifo_health = run_queries(conn)

    print(f"\n✓ Query execution successful")
    print(f"  - Total PnL: ${total_pnl:,.2f}")
    print(f"  - Open positions: {len(open_pos)}")
    print(f"  - Recent trades: {len(recent_trades)}")
    print(f"  - FIFO health: {fifo_health}")
    print(f"  - Errors: {len(errors)}")
    print(f"  - Detect notes: {len(detect_notes)}")

    # Check that FIFO health is None when disabled
    if fifo_health is None:
        print(f"\n✓ FIFO health correctly None when disabled")
    else:
        print(f"\n✗ ERROR: FIFO health should be None when disabled, got {fifo_health}")
        sys.exit(1)

    # Build HTML to verify no errors
    html = build_html(
        total_pnl=total_pnl,
        open_pos=open_pos,
        recent_trades=recent_trades,
        errors=errors,
        detect_notes=detect_notes,
        fifo_health=fifo_health
    )

    # Check that FIFO section is NOT in HTML
    if "FIFO Allocation Health" not in html:
        print(f"✓ FIFO health section correctly excluded from HTML when disabled")
    else:
        print(f"✗ ERROR: FIFO health section should not appear when disabled")
        sys.exit(1)

    # Check build version
    if "Build:v9" in html:
        print(f"✓ Build version updated to v9")
    else:
        print(f"⚠ Warning: Expected Build:v9 in detect notes")

    print(f"\n✅ TEST 1 PASSED: Backward compatibility maintained")

except Exception as e:
    print(f"\n✗ TEST 1 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test with FIFO enabled
print("\n" + "=" * 80)
print("TEST 2: FIFO ENABLED")
print("=" * 80)
os.environ['USE_FIFO_ALLOCATIONS'] = '1'
os.environ['FIFO_ALLOCATION_VERSION'] = '2'

# Reload constants to pick up new env var
from importlib import reload
from Config import constants_report
reload(constants_report)

# Verify constants updated
if constants_report.USE_FIFO_ALLOCATIONS:
    print(f"✓ USE_FIFO_ALLOCATIONS=True")
else:
    print(f"✗ ERROR: USE_FIFO_ALLOCATIONS should be True")
    sys.exit(1)

if constants_report.FIFO_ALLOCATION_VERSION == 2:
    print(f"✓ FIFO_ALLOCATION_VERSION=2")
else:
    print(f"✗ ERROR: FIFO_ALLOCATION_VERSION should be 2, got {constants_report.FIFO_ALLOCATION_VERSION}")
    sys.exit(1)

# Run queries again with FIFO enabled
try:
    # Need to reload the report module to pick up updated constants
    from botreport import aws_daily_report
    reload(aws_daily_report)

    total_pnl, open_pos, recent_trades, errors, detect_notes, fifo_health = aws_daily_report.run_queries(conn)

    print(f"\n✓ Query execution successful")
    print(f"  - Total PnL: ${total_pnl:,.2f}")
    print(f"  - FIFO health: {fifo_health}")

    # Check that FIFO health is populated when enabled
    if fifo_health is not None:
        print(f"\n✓ FIFO health populated when enabled:")
        print(f"  - Version: {fifo_health['version']}")
        print(f"  - Total allocations: {fifo_health['total_allocations']:,}")
        print(f"  - Sells matched: {fifo_health['sells_matched']:,}")
        print(f"  - Buys used: {fifo_health['buys_used']:,}")
        print(f"  - Unmatched sells: {fifo_health['unmatched_sells']:,}")
        print(f"  - Total PnL: ${fifo_health['total_pnl']:,.2f}")
    else:
        print(f"\n✗ ERROR: FIFO health should be populated when enabled")
        sys.exit(1)

    # Build HTML to verify FIFO section appears
    html = aws_daily_report.build_html(
        total_pnl=total_pnl,
        open_pos=open_pos,
        recent_trades=recent_trades,
        errors=errors,
        detect_notes=detect_notes,
        fifo_health=fifo_health
    )

    # Check that FIFO section IS in HTML
    if "FIFO Allocation Health" in html:
        print(f"\n✓ FIFO health section correctly included in HTML when enabled")
    else:
        print(f"\n✗ ERROR: FIFO health section should appear when enabled")
        sys.exit(1)

    # Check for FIFO note in detect_notes
    fifo_notes = [n for n in detect_notes if 'FIFO' in n]
    if fifo_notes:
        print(f"✓ FIFO notes in detect_notes: {fifo_notes}")
    else:
        print(f"⚠ Warning: No FIFO notes found in detect_notes")

    print(f"\n✅ TEST 2 PASSED: FIFO integration working correctly")

except Exception as e:
    print(f"\n✗ TEST 2 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 80)
print("ALL TESTS PASSED ✅")
print("=" * 80)
print("\nFIFO integration successfully implemented:")
print("  ✓ Backward compatible (disabled by default)")
print("  ✓ FIFO allocations used when enabled")
print("  ✓ FIFO health metrics displayed in report")
print("  ✓ No errors in report generation")
