#!/usr/bin/env python3
"""Diagnose portfolio tracker state by replaying fills from the DB.

Usage (on AWS):
    docker exec -it v2-kraken python /app/scripts/diagnose_portfolio.py

Or locally with DATABASE_URL set:
    DATABASE_URL=postgresql://... python scripts/diagnose_portfolio.py
"""
import asyncio
import os
import sys
from collections import defaultdict
from decimal import Decimal


async def main():
    import asyncpg

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)

    exchange = "paper-kraken"

    # 1. Fetch all fills
    rows = await pool.fetch(
        "SELECT symbol, side, price, qty, fee, timestamp "
        "FROM v2_fills WHERE exchange = $1 ORDER BY timestamp ASC",
        exchange,
    )
    print(f"\n{'='*60}")
    print(f"PORTFOLIO DIAGNOSTIC — exchange: {exchange}")
    print(f"{'='*60}")
    print(f"Total fills in DB: {len(rows)}")

    if not rows:
        print("No fills found. Nothing to diagnose.")
        await pool.close()
        return

    # 2. Replay fills (same logic as PortfolioTracker)
    initial_balance = Decimal("10000")
    cash = initial_balance
    positions: dict[str, Decimal] = {}
    avg_entries: dict[str, Decimal] = {}
    last_buy_prices: dict[str, float] = {}

    buy_count = 0
    sell_count = 0
    total_buy_volume = Decimal("0")
    total_sell_volume = Decimal("0")
    total_fees = Decimal("0")

    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        price = Decimal(str(row["price"]))
        qty = Decimal(str(row["qty"]))
        fee = Decimal(str(row["fee"]))
        notional = price * qty

        if side == "BUY":
            cash -= notional + fee
            existing_qty = positions.get(symbol, Decimal("0"))
            existing_cost = avg_entries.get(symbol, Decimal("0")) * existing_qty
            new_qty = existing_qty + qty
            if new_qty > 0:
                avg_entries[symbol] = (existing_cost + notional) / new_qty
            positions[symbol] = new_qty
            last_buy_prices[symbol] = float(price)
            buy_count += 1
            total_buy_volume += notional
        elif side == "SELL":
            cash += notional - fee
            existing_qty = positions.get(symbol, Decimal("0"))
            new_qty = existing_qty - qty
            if new_qty <= Decimal("1e-12"):
                positions.pop(symbol, None)
                avg_entries.pop(symbol, None)
            else:
                positions[symbol] = new_qty
            sell_count += 1
            total_sell_volume += notional

        total_fees += fee

    # 3. Compute totals
    open_positions = {s: q for s, q in positions.items() if q > Decimal("1e-12")}

    # Value positions at their last BUY fill price (what replay_fills does)
    mtm_at_fill_prices = sum(
        float(q) * last_buy_prices.get(s, float(avg_entries.get(s, Decimal("0"))))
        for s, q in open_positions.items()
    )
    total_at_fill_prices = float(cash) + mtm_at_fill_prices

    # Value positions at avg entry (cost basis)
    cost_basis = sum(
        float(q) * float(avg_entries.get(s, Decimal("0")))
        for s, q in open_positions.items()
    )
    total_at_cost = float(cash) + cost_basis

    # 4. Print results
    print(f"\n--- FILL SUMMARY ---")
    print(f"Buys:  {buy_count} (${total_buy_volume:.2f})")
    print(f"Sells: {sell_count} (${total_sell_volume:.2f})")
    print(f"Fees:  ${total_fees:.2f}")
    print(f"Date range: {rows[0]['timestamp']} → {rows[-1]['timestamp']}")

    print(f"\n--- PORTFOLIO STATE (after replaying all fills) ---")
    print(f"Cash:              ${float(cash):>12.2f}")
    print(f"Positions (cost):  ${cost_basis:>12.2f}")
    print(f"Total (at cost):   ${total_at_cost:>12.2f}")
    print(f"Positions (fill):  ${mtm_at_fill_prices:>12.2f}  ← what replay_fills uses for _last_prices")
    print(f"Total (at fill):   ${total_at_fill_prices:>12.2f}  ← _period_start_value after hydration")

    print(f"\n--- OPEN POSITIONS ({len(open_positions)} symbols) ---")
    if open_positions:
        print(f"{'Symbol':<12} {'Qty':>14} {'Avg Entry':>12} {'Cost Basis':>12} {'Last Buy':>12}")
        print("-" * 64)
        for sym in sorted(open_positions.keys()):
            qty = open_positions[sym]
            entry = avg_entries.get(sym, Decimal("0"))
            cost = float(qty) * float(entry)
            last = last_buy_prices.get(sym, 0)
            print(f"{sym:<12} {float(qty):>14.8f} ${float(entry):>10.4f} ${cost:>10.2f} ${last:>10.4f}")
    else:
        print("  (none)")

    print(f"\n--- DIAGNOSIS ---")
    diff = total_at_fill_prices - float(initial_balance)
    print(f"Initial balance:   ${float(initial_balance):>12.2f}")
    print(f"Current total:     ${total_at_fill_prices:>12.2f}")
    print(f"Difference:        ${diff:>+12.2f}")
    if abs(diff) > 100:
        print(f"\n⚠  The portfolio tracker shows ${total_at_fill_prices:.2f} vs initial $10,000.")
        print(f"   This {diff:+.2f} difference comes from open position MTM.")
        print(f"   Once tickers update _last_prices to CURRENT market prices,")
        print(f"   the _total_value() will differ further from this replay estimate.")
    print()

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
