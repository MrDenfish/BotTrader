#!/usr/bin/env python3
"""
Verify the accuracy of the email report against the database.
Compares key metrics reported in the email with actual database values.
"""

import os
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Database connection
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cursor = conn.cursor()

print("=" * 80)
print("EMAIL REPORT ACCURACY VERIFICATION")
print("=" * 80)
print()

# From the email report (as of 2025-11-27 07:46 UTC):
EMAIL_METRICS = {
    'realized_pnl': 0.00,
    'unrealized_pnl': 0.00,
    'win_rate_pct': 26.3,
    'win_rate_wins': 5,
    'win_rate_total': 19,
    'avg_win': 3.17,
    'avg_loss': -15.83,
    'profit_factor': 0.08,
    'max_drawdown_pct': 294.5,
    'trigger_limit_orders': 16,
    'trigger_limit_wins': 3,
    'trigger_limit_losses': 12,
    'trigger_limit_win_rate_pct': 18.8,
    'trigger_limit_total_pnl': -190.12,
    'fifo_version': 2,
    'fifo_total_allocations': 3525,
    'fifo_sells_matched': 2884,
    'fifo_buys_used': 2874,
    'fifo_unmatched_sells': 13,
    'fifo_total_pnl': -1152.88,
}

print("📧 EMAIL REPORT METRICS (2025-11-27 07:46 UTC)")
print("-" * 80)
for key, value in EMAIL_METRICS.items():
    print(f"  {key}: {value}")
print()

# 1. FIFO Allocation Health
print("\n1️⃣  VERIFYING: FIFO Allocation Health")
print("-" * 80)

cursor.execute("""
    SELECT
        allocation_version,
        COUNT(*) as total_allocations,
        COUNT(DISTINCT sell_order_id) as sells_matched,
        COUNT(DISTINCT buy_order_id) as buys_used,
        SUM(CASE WHEN buy_order_id IS NULL THEN 1 ELSE 0 END) as unmatched_sells,
        SUM(pnl_usd) as total_pnl
    FROM fifo_allocations
    WHERE allocation_version = 2
    GROUP BY allocation_version
""")

fifo_result = cursor.fetchone()
if fifo_result:
    version, total_alloc, sells_matched, buys_used, unmatched, total_pnl = fifo_result

    print(f"Database Values:")
    print(f"  Version: {version}")
    print(f"  Total Allocations: {total_alloc}")
    print(f"  Sells Matched: {sells_matched}")
    print(f"  Buys Used: {buys_used}")
    print(f"  Unmatched Sells: {unmatched}")
    print(f"  Total PnL: ${total_pnl:.2f}")
    print()

    # Comparison
    print("Comparison with Email:")
    print(f"  ✓ Version: {version} vs {EMAIL_METRICS['fifo_version']} - {'MATCH' if version == EMAIL_METRICS['fifo_version'] else 'MISMATCH'}")
    print(f"  ✓ Total Allocations: {total_alloc} vs {EMAIL_METRICS['fifo_total_allocations']} - {'MATCH' if total_alloc == EMAIL_METRICS['fifo_total_allocations'] else 'MISMATCH'}")
    print(f"  ✓ Sells Matched: {sells_matched} vs {EMAIL_METRICS['fifo_sells_matched']} - {'MATCH' if sells_matched == EMAIL_METRICS['fifo_sells_matched'] else 'MISMATCH'}")
    print(f"  ✓ Buys Used: {buys_used} vs {EMAIL_METRICS['fifo_buys_used']} - {'MATCH' if buys_used == EMAIL_METRICS['fifo_buys_used'] else 'MISMATCH'}")
    print(f"  ✓ Unmatched Sells: {unmatched} vs {EMAIL_METRICS['fifo_unmatched_sells']} - {'MATCH' if unmatched == EMAIL_METRICS['fifo_unmatched_sells'] else 'MISMATCH'}")
    print(f"  ✓ Total PnL: ${total_pnl:.2f} vs ${EMAIL_METRICS['fifo_total_pnl']:.2f} - {'MATCH' if abs(total_pnl - EMAIL_METRICS['fifo_total_pnl']) < 0.01 else 'MISMATCH'}")

# 2. Win Rate from trade_records
print("\n2️⃣  VERIFYING: Win Rate")
print("-" * 80)

cursor.execute("""
    SELECT
        COUNT(*) FILTER (WHERE realized_profit > 0) as wins,
        COUNT(*) FILTER (WHERE realized_profit < 0) as losses,
        COUNT(*) FILTER (WHERE realized_profit = 0) as breakeven,
        COUNT(*) as total,
        (COUNT(*) FILTER (WHERE realized_profit > 0)::float / COUNT(*)::float * 100) as win_rate_pct
    FROM trade_records
    WHERE realized_profit IS NOT NULL
        AND order_time >= NOW() - INTERVAL '90 days'
""")

winrate_result = cursor.fetchone()
if winrate_result:
    wins, losses, breakeven, total, win_rate_pct = winrate_result

    print(f"Database Values (last 90 days):")
    print(f"  Wins: {wins}")
    print(f"  Losses: {losses}")
    print(f"  Breakeven: {breakeven}")
    print(f"  Total: {total}")
    print(f"  Win Rate: {win_rate_pct:.1f}%")
    print()

    print("Comparison with Email:")
    print(f"  ✓ Wins: {wins} vs {EMAIL_METRICS['win_rate_wins']} - {'MATCH' if wins == EMAIL_METRICS['win_rate_wins'] else 'MISMATCH'}")
    print(f"  ✓ Total (excluding breakeven): {wins + losses} vs {EMAIL_METRICS['win_rate_total']} - {'MATCH' if (wins + losses) == EMAIL_METRICS['win_rate_total'] else 'MISMATCH'}")
    print(f"  ✓ Win Rate %: {win_rate_pct:.1f}% vs {EMAIL_METRICS['win_rate_pct']}% - {'MATCH' if abs(win_rate_pct - EMAIL_METRICS['win_rate_pct']) < 0.5 else 'MISMATCH'}")

# 3. Trade Stats (Avg Win, Avg Loss, Profit Factor)
print("\n3️⃣  VERIFYING: Trade Stats")
print("-" * 80)

cursor.execute("""
    SELECT
        AVG(realized_profit) FILTER (WHERE realized_profit > 0) as avg_win,
        AVG(realized_profit) FILTER (WHERE realized_profit < 0) as avg_loss,
        SUM(realized_profit) FILTER (WHERE realized_profit > 0) as total_wins,
        SUM(ABS(realized_profit)) FILTER (WHERE realized_profit < 0) as total_losses
    FROM trade_records
    WHERE realized_profit IS NOT NULL
        AND order_time >= NOW() - INTERVAL '90 days'
""")

trade_stats = cursor.fetchone()
if trade_stats:
    avg_win, avg_loss, total_wins, total_losses = trade_stats
    profit_factor = total_wins / total_losses if total_losses and total_losses > 0 else 0

    print(f"Database Values:")
    print(f"  Avg Win: ${avg_win:.2f}")
    print(f"  Avg Loss: ${avg_loss:.2f}")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print()

    print("Comparison with Email:")
    print(f"  ✓ Avg Win: ${avg_win:.2f} vs ${EMAIL_METRICS['avg_win']:.2f} - {'MATCH' if abs(avg_win - EMAIL_METRICS['avg_win']) < 0.01 else 'MISMATCH'}")
    print(f"  ✓ Avg Loss: ${avg_loss:.2f} vs ${EMAIL_METRICS['avg_loss']:.2f} - {'MATCH' if abs(avg_loss - EMAIL_METRICS['avg_loss']) < 0.01 else 'MISMATCH'}")
    print(f"  ✓ Profit Factor: {profit_factor:.2f} vs {EMAIL_METRICS['profit_factor']:.2f} - {'MATCH' if abs(profit_factor - EMAIL_METRICS['profit_factor']) < 0.01 else 'MISMATCH'}")

# 4. Trigger Breakdown
print("\n4️⃣  VERIFYING: Trigger Breakdown")
print("-" * 80)

cursor.execute("""
    SELECT
        trigger,
        COUNT(*) as orders,
        COUNT(*) FILTER (WHERE realized_profit > 0) as wins,
        COUNT(*) FILTER (WHERE realized_profit < 0) as losses,
        (COUNT(*) FILTER (WHERE realized_profit > 0)::float / NULLIF(COUNT(*)::float, 0) * 100) as win_rate_pct,
        SUM(realized_profit) as total_pnl
    FROM trade_records
    WHERE realized_profit IS NOT NULL
        AND order_time >= NOW() - INTERVAL '90 days'
        AND trigger IS NOT NULL
    GROUP BY trigger
    ORDER BY total_pnl DESC
""")

trigger_results = cursor.fetchall()
if trigger_results:
    print(f"Database Values:")
    for trigger, orders, wins, losses, win_rate, pnl in trigger_results:
        print(f"  {trigger}: {orders} orders, {wins} wins, {losses} losses, {win_rate:.1f}% win rate, ${pnl:.2f} PnL")
    print()

    # Check for LIMIT trigger
    limit_trigger = next((t for t in trigger_results if 'LIMIT' in str(t[0])), None)
    if limit_trigger:
        trigger, orders, wins, losses, win_rate, pnl = limit_trigger
        print("Comparison with Email (LIMIT trigger):")
        print(f"  ✓ Orders: {orders} vs {EMAIL_METRICS['trigger_limit_orders']} - {'MATCH' if orders == EMAIL_METRICS['trigger_limit_orders'] else 'MISMATCH'}")
        print(f"  ✓ Wins: {wins} vs {EMAIL_METRICS['trigger_limit_wins']} - {'MATCH' if wins == EMAIL_METRICS['trigger_limit_wins'] else 'MISMATCH'}")
        print(f"  ✓ Losses: {losses} vs {EMAIL_METRICS['trigger_limit_losses']} - {'MATCH' if losses == EMAIL_METRICS['trigger_limit_losses'] else 'MISMATCH'}")
        print(f"  ✓ Win Rate: {win_rate:.1f}% vs {EMAIL_METRICS['trigger_limit_win_rate_pct']}% - {'MATCH' if abs(win_rate - EMAIL_METRICS['trigger_limit_win_rate_pct']) < 0.5 else 'MISMATCH'}")
        print(f"  ✓ Total PnL: ${pnl:.2f} vs ${EMAIL_METRICS['trigger_limit_total_pnl']:.2f} - {'MATCH' if abs(pnl - EMAIL_METRICS['trigger_limit_total_pnl']) < 0.01 else 'MISMATCH'}")

# 5. Max Drawdown
print("\n5️⃣  VERIFYING: Max Drawdown")
print("-" * 80)

cursor.execute("""
    WITH cumulative_pnl AS (
        SELECT
            order_time,
            realized_profit,
            SUM(realized_profit) OVER (ORDER BY order_time) as cumulative
        FROM trade_records
        WHERE realized_profit IS NOT NULL
        ORDER BY order_time
    ),
    running_peak AS (
        SELECT
            order_time,
            cumulative,
            MAX(cumulative) OVER (ORDER BY order_time ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as peak
        FROM cumulative_pnl
    ),
    drawdowns AS (
        SELECT
            order_time,
            cumulative,
            peak,
            (cumulative - peak) as drawdown,
            CASE
                WHEN peak > 0 THEN ((cumulative - peak) / peak * 100)
                WHEN peak < 0 THEN ((peak - cumulative) / ABS(peak) * 100)
                ELSE 0
            END as drawdown_pct
        FROM running_peak
    )
    SELECT
        MIN(drawdown) as max_drawdown_usd,
        MIN(drawdown_pct) as max_drawdown_pct
    FROM drawdowns
""")

dd_result = cursor.fetchone()
if dd_result:
    max_dd_usd, max_dd_pct = dd_result

    print(f"Database Values:")
    print(f"  Max Drawdown (USD): ${max_dd_usd:.2f}")
    print(f"  Max Drawdown (%): {max_dd_pct:.2f}%")
    print()

    print("Comparison with Email:")
    print(f"  ✓ Max Drawdown %: {max_dd_pct:.2f}% vs {EMAIL_METRICS['max_drawdown_pct']}% - {'MATCH' if abs(max_dd_pct - EMAIL_METRICS['max_drawdown_pct']) < 1.0 else 'MISMATCH'}")

print()
print("=" * 80)
print("VERIFICATION COMPLETE")
print("=" * 80)

cursor.close()
conn.close()
