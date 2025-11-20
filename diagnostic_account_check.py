#!/usr/bin/env python3
"""
Simplified Diagnostic Script: Compare Coinbase Account vs Database
===================================================================
This script will:
1. Query your actual Coinbase account balances
2. Query your database positions
3. Compare and identify discrepancies

Run this from your BotTrader directory:
    python diagnostic_simple.py
"""

import asyncio
import aiohttp
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

# Add your project paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Config.config_manager import CentralConfig
from Api_manager.coinbase_api import CoinbaseAPI
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.utility import SharedUtility
from SharedDataManager.shared_data_manager import SharedDataManager as SDM
from database_manager.database_session_manager import DatabaseSessionManager as DatabaseSessionManager


class SimpleAccountDiagnostic:
    def __init__(self):
        self.config = CentralConfig()
        log_config = {"log_level": "INFO"}
        self.logger_manager = LoggerManager(log_config)
        self.logger = self.logger_manager.loggers['shared_logger']

        # Initialize utilities (pass SDM class, not instance)
        self.precision = PrecisionUtils(self.logger_manager, SDM)
        self.utility = SharedUtility(self.logger_manager)

        # Database manager (async)
        self.db_manager = DatabaseSessionManager

    async def get_coinbase_accounts(self, session, cb_api):
        """Get actual account balances from Coinbase"""
        print("\n" + "=" * 80)
        print("STEP 1: QUERYING COINBASE API - ACTUAL ACCOUNT BALANCES")
        print("=" * 80)

        try:
            request_path = '/api/v3/brokerage/accounts'
            jwt_token = cb_api.generate_rest_jwt('GET', request_path)
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_token}'
            }

            url = f'{cb_api.rest_url}{request_path}'

            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    accounts = data.get('accounts', [])

                    print(f"\n✅ Found {len(accounts)} accounts in Coinbase\n")

                    balances = {}
                    total_usd = Decimal('0')
                    total_crypto_usd = Decimal('0')

                    for acc in accounts:
                        currency = acc.get('currency')
                        available = Decimal(acc.get('available_balance', {}).get('value', '0'))
                        hold = Decimal(acc.get('hold', {}).get('value', '0'))
                        total = available + hold

                        if total > 0:
                            balances[currency] = {
                                'available': float(available),
                                'hold': float(hold),
                                'total': float(total)
                            }

                            # Track USD vs crypto
                            if currency in ['USD', 'USDC', 'USDT']:
                                total_usd += total
                                print(f"💵 {currency:12s} | ${total:>12.2f}")
                            else:
                                # Crypto - we'd need price to get USD value
                                # For now just show quantity
                                print(f"🪙 {currency:12s} | {total:>12.8f} units")
                                total_crypto_usd += Decimal('0')  # Unknown value

                    print(f"\n{'=' * 80}")
                    print(f"📊 TOTAL CASH (USD+USDC+USDT): ${total_usd:.2f}")
                    print(f"📊 CRYPTO HOLDINGS: {len([c for c in balances if c not in ['USD', 'USDC', 'USDT']])} different coins")
                    print(f"⚠️  Note: Crypto values need price lookup for USD value")

                    return balances, float(total_usd)
                else:
                    text = await resp.text()
                    print(f"❌ Error {resp.status}: {text}")
                    return {}, 0.0

        except Exception as e:
            print(f"❌ Exception getting balances: {e}")
            import traceback
            traceback.print_exc()
            return {}, 0.0

    async def get_coinbase_fills_summary(self, cb_api):
        """Get summary of recent fills from Coinbase"""
        print("\n" + "=" * 80)
        print("STEP 2: QUERYING COINBASE API - RECENT TRADING ACTIVITY")
        print("=" * 80)

        try:
            params = {"limit": 100}  # Last 100 fills
            filled_response = await cb_api.get_historical_orders_batch(params)
            orders = filled_response.get("orders", [])

            print(f"\n✅ Retrieved last {len(orders)} orders from Coinbase")

            # Calculate net position per symbol from recent fills
            positions = {}

            for order in orders:
                if order.get("status") != "FILLED":
                    continue

                symbol = order.get("product_id")
                side = order.get("side", "").lower()
                filled_size = Decimal(order.get("filled_size", 0))

                if symbol not in positions:
                    positions[symbol] = {'buys': 0, 'sells': 0, 'net_qty': Decimal('0')}

                if side == "buy":
                    positions[symbol]['buys'] += 1
                    positions[symbol]['net_qty'] += filled_size
                elif side == "sell":
                    positions[symbol]['sells'] += 1
                    positions[symbol]['net_qty'] -= filled_size

            # Show summary
            print(f"\n📊 Trading Activity (last {len(orders)} fills):\n")

            for symbol in sorted(positions.keys()):
                pos = positions[symbol]
                net = pos['net_qty']
                print(f"{symbol:15s} | Buys: {pos['buys']:>3} | Sells: {pos['sells']:>3} | Net from fills: {float(net):>10.2f}")

            return positions

        except Exception as e:
            print(f"❌ Exception getting fills: {e}")
            import traceback
            traceback.print_exc()
            return {}

    async def get_db_positions(self):
        """Get positions from database"""
        print("\n" + "=" * 80)
        print("STEP 3: QUERYING DATABASE - report_positions TABLE")
        print("=" * 80)

        try:
            async with self.db_manager.get_session() as session:
                from sqlalchemy import text

                result = await session.execute(text("""
                    SELECT 
                        symbol,
                        position_qty,
                        avg_entry_price,
                        ABS(position_qty * avg_entry_price) as notional,
                        updated_at
                    FROM public.report_positions
                    WHERE ABS(position_qty) > 0.00001
                    ORDER BY notional DESC
                """))

                rows = result.fetchall()

                print(f"\n✅ Found {len(rows)} positions in database\n")

                db_positions = {}
                total_notional = 0

                for row in rows:
                    symbol = row[0]
                    qty = float(row[1])
                    avg_price = float(row[2])
                    notional = float(row[3])
                    updated = row[4]

                    db_positions[symbol] = {
                        'qty': qty,
                        'avg_price': avg_price,
                        'notional': notional,
                        'side': 'long' if qty > 0 else 'short',
                        'updated_at': str(updated)
                    }

                    total_notional += notional

                    print(f"{symbol:15s} | {db_positions[symbol]['side']:5s} | Qty: {qty:>12.2f} | "
                          f"Avg: ${avg_price:>10.4f} | Notional: ${notional:>12.2f} | Updated: {updated}")

                print(f"\n{'=' * 80}")
                print(f"📊 TOTAL NOTIONAL IN DATABASE: ${total_notional:,.2f}")

                return db_positions, total_notional

        except Exception as e:
            print(f"❌ Exception querying database: {e}")
            import traceback
            traceback.print_exc()
            return {}, 0.0

    async def get_db_trade_summary(self):
        """Get trade summary from database"""
        print("\n" + "=" * 80)
        print("STEP 4: QUERYING DATABASE - TRADE SUMMARY")
        print("=" * 80)

        try:
            async with self.db_manager.get_session() as session:
                from sqlalchemy import text

                result = await session.execute(text("""
                    SELECT 
                        COUNT(*) as total_trades,
                        COUNT(CASE WHEN realized_profit IS NOT NULL THEN 1 END) as trades_with_pnl,
                        SUM(COALESCE(realized_profit, 0)) as total_realized_pnl,
                        MIN(order_time) as first_trade,
                        MAX(order_time) as last_trade
                    FROM public.trade_records
                    WHERE order_time >= NOW() - INTERVAL '30 days'
                """))

                row = result.fetchone()

                print(f"\n📊 Last 30 Days:")
                print(f"  Total Trades: {row[0]}")
                print(f"  Trades with PnL: {row[1]}")
                print(f"  Total Realized PnL: ${float(row[2] or 0):.2f}")
                print(f"  First Trade: {row[3]}")
                print(f"  Last Trade: {row[4]}")

                return {
                    'total_trades': row[0],
                    'realized_pnl': float(row[2] or 0),
                    'first_trade': row[3],
                    'last_trade': row[4]
                }

        except Exception as e:
            print(f"❌ Exception querying trades: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def compare_and_diagnose(self, cb_balances, cb_cash, cb_fills, db_positions, db_notional, db_trades):
        """Compare results and diagnose issues"""
        print("\n" + "=" * 80)
        print("DIAGNOSIS & COMPARISON")
        print("=" * 80)

        # Manual values you reported
        manual_cash = 57.06
        manual_crypto = 194.28
        manual_total = 251.34

        print(f"\n💰 CASH COMPARISON:")
        print(f"  Your manual check:        ${manual_cash:.2f}")
        print(f"  Coinbase API returned:    ${cb_cash:.2f}")
        if abs(cb_cash - manual_cash) < 1:
            print(f"  ✅ Match!")
        else:
            print(f"  ⚠️  Difference: ${abs(cb_cash - manual_cash):.2f}")

        print(f"\n📊 POSITIONS COMPARISON:")
        print(f"  Your manual check (total): ${manual_total:.2f}")
        print(f"  Database thinks you have:  ${db_notional:,.2f}")
        print(f"  Difference:                ${abs(db_notional - manual_total):,.2f}")

        if db_notional > 1000 and manual_total < 500:
            print(f"\n  🚨 MAJOR ISSUE DETECTED!")
            print(f"  Database has {int(db_notional / manual_total)}x more than reality!")

        print(f"\n📈 TRADING ACTIVITY:")
        print(f"  Last trade in DB: {db_trades.get('last_trade', 'Unknown')}")
        print(f"  Total trades (30d): {db_trades.get('total_trades', 0)}")
        print(f"  Realized PnL (30d): ${db_trades.get('realized_pnl', 0):.2f}")

        # List symbols in DB but check if they make sense
        print(f"\n🔍 POSITION DETAILS:")
        if db_positions:
            print(f"\n  Database thinks you have positions in {len(db_positions)} symbols:")
            for symbol, pos in list(db_positions.items())[:10]:
                print(f"    {symbol}: {pos['side']} {abs(pos['qty']):.2f} @ ${pos['avg_price']:.4f} = ${pos['notional']:.2f}")

            print(f"\n  ❓ Do you actually have these positions open in Coinbase?")
            print(f"     Please check your Coinbase portfolio and compare!")

        # Diagnosis
        print(f"\n" + "=" * 80)
        print("LIKELY DIAGNOSIS")
        print("=" * 80)

        if db_notional > manual_total * 10:
            print("""
🎯 Most Likely Issue: STALE POSITION DATA

The database thinks you have ~$100k in positions, but you only have $251 total.

This suggests:
1. The bot closed positions (sold everything)
2. BUT the report_positions table was never updated
3. Now it shows "phantom" positions that don't exist

ROOT CAUSE: The position tracking logic isn't properly updating when trades close.

SOLUTION: We need to:
1. Truncate/rebuild the report_positions table from actual Coinbase data
2. Fix the position update logic so this doesn't happen again
3. Investigate WHY positions aren't being updated on sells
""")

        return {
            'manual_total': manual_total,
            'db_notional': db_notional,
            'discrepancy': abs(db_notional - manual_total),
            'severity': 'CRITICAL' if db_notional > manual_total * 10 else 'WARNING'
        }

    async def run(self):
        """Run the full diagnostic"""
        print("\n" + "=" * 80)
        print("🔍 BOTTRADER ACCOUNT DIAGNOSTIC 🔍")
        print("=" * 80)
        print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 80)

        async with aiohttp.ClientSession() as session:
            # Initialize Coinbase API
            cb_api = CoinbaseAPI(
                session=session,
                shared_utils_utility=self.utility,
                logger_manager=self.logger_manager,
                shared_utils_precision=self.precision
            )

            # Get data from Coinbase
            cb_balances, cb_cash = await self.get_coinbase_accounts(session, cb_api)
            cb_fills = await self.get_coinbase_fills_summary(cb_api)

            # Get data from Database
            db_positions, db_notional = await self.get_db_positions()
            db_trades = await self.get_db_trade_summary()

            # Compare and diagnose
            diagnosis = self.compare_and_diagnose(
                cb_balances, cb_cash, cb_fills,
                db_positions, db_notional, db_trades
            )

            print(f"\n✅ Diagnostic complete!")
            print(f"Severity: {diagnosis['severity']}")

            return diagnosis


if __name__ == "__main__":
    print("Starting diagnostic...")
    diagnostic = SimpleAccountDiagnostic()
    results = asyncio.run(diagnostic.run())

    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print("""
Based on the results above, we need to:

1. Understand WHERE the position update logic lives
   - Which module updates report_positions?
   - Is it triggered on every trade?
   - Or only periodically?

2. Check if report_positions is a TABLE or VIEW
   - If it's a table, it needs manual updates
   - If it's a view, it should auto-calculate

3. Rebuild the positions from scratch using actual Coinbase data

Would you like me to:
A) Help find and fix the position update logic?
B) Create a script to rebuild report_positions from Coinbase?
C) Investigate why sells aren't updating positions?
D) All of the above?
""")
