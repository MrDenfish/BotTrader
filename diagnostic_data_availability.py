#!/usr/bin/env python3
"""
Data Availability Diagnostic Script

Evaluates historical data for parameter tuning analysis:
- Database trade records (count, date range, symbols)
- scores.jsonl (entries, date range)
- tpsl.jsonl (entries, date range)
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from Config.config_manager import CentralConfig
from Config.environment import Environment
from sqlalchemy import create_engine, text


def get_db_connection():
    """Get database connection using CentralConfig."""
    config = CentralConfig()
    engine = create_engine(config.db_url, pool_pre_ping=True)
    return engine.connect()


def analyze_database_trades(conn):
    """Analyze trade records in database."""
    print("\n" + "="*80)
    print("DATABASE ANALYSIS: Trade Records")
    print("="*80)

    # Try common table names
    table_candidates = [
        "public.trade_records",
        "public.report_trades",
        "trade_records",
        "report_trades"
    ]

    trade_table = None
    for table in table_candidates:
        try:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            trade_table = table
            print(f"✅ Found trade table: {table}")
            break
        except Exception:
            continue

    if not trade_table:
        print("❌ No trade table found. Tried:", table_candidates)
        return None

    # Get total count
    result = conn.execute(text(f"SELECT COUNT(*) FROM {trade_table}"))
    total_trades = result.scalar()
    print(f"\n📊 Total Trades: {total_trades:,}")

    if total_trades == 0:
        print("⚠️  No trades in database!")
        return None

    # Get date range - try multiple timestamp column names
    ts_cols = ["order_time", "trade_time", "filled_at", "ts", "timestamp", "created_at"]
    ts_col = None

    for col in ts_cols:
        try:
            query = text(f"""
                SELECT
                    MIN({col}) as first_trade,
                    MAX({col}) as last_trade,
                    MAX({col}) - MIN({col}) as duration
                FROM {trade_table}
                WHERE {col} IS NOT NULL
            """)
            result = conn.execute(query).fetchone()
            if result and result[0]:
                ts_col = col
                first_trade, last_trade, duration = result
                break
        except Exception:
            continue

    if ts_col:
        print(f"\n📅 Date Range (using {ts_col}):")
        print(f"   First Trade: {first_trade}")
        print(f"   Last Trade:  {last_trade}")
        print(f"   Duration:    {duration}")

        # Calculate days
        if hasattr(duration, 'days'):
            days = duration.days
        else:
            days = (last_trade - first_trade).days if last_trade and first_trade else 0
        print(f"   Days of Data: {days}")
    else:
        print(f"\n⚠️  Could not find timestamp column. Tried: {ts_cols}")

    # Get symbol distribution
    symbol_cols = ["symbol", "product_id", "trading_pair"]
    symbol_col = None

    for col in symbol_cols:
        try:
            query = text(f"""
                SELECT {col}, COUNT(*) as trade_count
                FROM {trade_table}
                WHERE {col} IS NOT NULL
                GROUP BY {col}
                ORDER BY trade_count DESC
                LIMIT 20
            """)
            results = conn.execute(query).fetchall()
            if results:
                symbol_col = col
                break
        except Exception:
            continue

    if symbol_col:
        print(f"\n💹 Top Symbols Traded (using {symbol_col}):")
        print(f"   {'Symbol':<15} {'Trades':>10}")
        print(f"   {'-'*15} {'-'*10}")
        for symbol, count in results[:15]:
            print(f"   {symbol:<15} {count:>10,}")

        total_symbols = conn.execute(text(f"SELECT COUNT(DISTINCT {symbol_col}) FROM {trade_table}")).scalar()
        print(f"\n   Total Unique Symbols: {total_symbols}")

    # Get PnL column if available
    pnl_cols = ["realized_profit", "pnl_usd", "pnl", "profit"]
    pnl_col = None

    for col in pnl_cols:
        try:
            query = text(f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE {col} > 0) as wins,
                    COUNT(*) FILTER (WHERE {col} < 0) as losses,
                    SUM({col}) as total_pnl,
                    AVG({col}) as avg_pnl
                FROM {trade_table}
                WHERE {col} IS NOT NULL
            """)
            result = conn.execute(query).fetchone()
            if result and result[0]:
                pnl_col = col
                total, wins, losses, total_pnl, avg_pnl = result
                break
        except Exception:
            continue

    if pnl_col:
        print(f"\n💰 PnL Summary (using {pnl_col}):")
        print(f"   Total Trades:  {total:,}")
        print(f"   Wins:          {wins:,} ({wins/total*100:.1f}%)")
        print(f"   Losses:        {losses:,} ({losses/total*100:.1f}%)")
        print(f"   Total PnL:     ${total_pnl:,.2f}")
        print(f"   Avg PnL/Trade: ${avg_pnl:,.2f}")

    return {
        "table": trade_table,
        "total_trades": total_trades,
        "ts_col": ts_col,
        "symbol_col": symbol_col,
        "pnl_col": pnl_col,
        "first_trade": first_trade if ts_col else None,
        "last_trade": last_trade if ts_col else None,
        "days": days if ts_col else None
    }


def analyze_jsonl_file(filepath, name):
    """Analyze a JSONL file."""
    print(f"\n" + "="*80)
    print(f"JSONL ANALYSIS: {name}")
    print("="*80)

    path = Path(filepath)
    if not path.exists():
        print(f"❌ File not found: {filepath}")
        return None

    # Get file info
    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    print(f"\n📄 File: {filepath}")
    print(f"   Size: {size_mb:.2f} MB ({size_bytes:,} bytes)")

    # Parse entries
    entries = []
    timestamps = []
    symbols = Counter()
    parse_errors = 0

    print(f"\n📖 Parsing entries...")
    with open(path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)

                # Extract timestamp
                ts_str = entry.get('ts') or entry.get('timestamp') or entry.get('time')
                if ts_str:
                    try:
                        if isinstance(ts_str, str):
                            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        else:
                            ts = datetime.fromtimestamp(float(ts_str), tz=timezone.utc)
                        timestamps.append(ts)
                    except Exception:
                        pass

                # Extract symbol
                sym = entry.get('symbol') or entry.get('product_id')
                if sym:
                    symbols[sym] += 1

            except json.JSONDecodeError:
                parse_errors += 1

    print(f"   Total Entries: {len(entries):,}")
    if parse_errors:
        print(f"   Parse Errors:  {parse_errors}")

    if not entries:
        print("⚠️  No valid entries found")
        return None

    # Date range
    if timestamps:
        timestamps.sort()
        first_ts = timestamps[0]
        last_ts = timestamps[-1]
        duration = last_ts - first_ts

        print(f"\n📅 Date Range:")
        print(f"   First Entry: {first_ts}")
        print(f"   Last Entry:  {last_ts}")
        print(f"   Duration:    {duration}")
        print(f"   Days:        {duration.days}")

    # Symbol distribution
    if symbols:
        print(f"\n💹 Top Symbols in {name}:")
        print(f"   {'Symbol':<15} {'Entries':>10}")
        print(f"   {'-'*15} {'-'*10}")
        for symbol, count in symbols.most_common(15):
            print(f"   {symbol:<15} {count:>10,}")
        print(f"\n   Total Unique Symbols: {len(symbols)}")

    # Sample entry structure
    if entries:
        print(f"\n📋 Sample Entry (first):")
        sample = entries[0]
        for key in sorted(sample.keys())[:10]:  # Show first 10 keys
            val = sample[key]
            if isinstance(val, (list, dict)):
                print(f"   {key}: {type(val).__name__} (length: {len(val)})")
            else:
                val_str = str(val)[:50]  # Truncate long values
                print(f"   {key}: {val_str}")
        if len(sample.keys()) > 10:
            print(f"   ... ({len(sample.keys())} total keys)")

    return {
        "filepath": str(filepath),
        "size_mb": size_mb,
        "total_entries": len(entries),
        "parse_errors": parse_errors,
        "first_ts": timestamps[0] if timestamps else None,
        "last_ts": timestamps[-1] if timestamps else None,
        "days": duration.days if timestamps else None,
        "unique_symbols": len(symbols),
        "top_symbols": symbols.most_common(10)
    }


def main():
    """Main diagnostic routine."""
    print("\n" + "="*80)
    print("DATA AVAILABILITY DIAGNOSTIC")
    print("="*80)

    # Get environment info
    env = Environment()
    print(f"\nEnvironment: {env.env_name} (Docker: {env.is_docker})")

    results = {}

    # 1. Analyze database
    try:
        print("\n🔌 Connecting to database...")
        conn = get_db_connection()
        results['database'] = analyze_database_trades(conn)
        conn.close()
    except Exception as e:
        print(f"\n❌ Database connection failed: {e}")
        results['database'] = None

    # 2. Analyze scores.jsonl
    try:
        score_path = env.score_jsonl_path
        results['scores'] = analyze_jsonl_file(score_path, "scores.jsonl")
    except Exception as e:
        print(f"\n❌ scores.jsonl analysis failed: {e}")
        results['scores'] = None

    # 3. Analyze tpsl.jsonl
    try:
        tpsl_path = env.tp_sl_log_path
        results['tpsl'] = analyze_jsonl_file(tpsl_path, "tpsl.jsonl")
    except Exception as e:
        print(f"\n❌ tpsl.jsonl analysis failed: {e}")
        results['tpsl'] = None

    # Summary
    print("\n" + "="*80)
    print("SUMMARY: Data Availability for Parameter Tuning")
    print("="*80)

    db = results.get('database')
    scores = results.get('scores')
    tpsl = results.get('tpsl')

    if db:
        print(f"\n✅ DATABASE TRADES:")
        print(f"   • {db['total_trades']:,} total trades")
        print(f"   • {db['days']} days of history" if db.get('days') else "   • Date range unknown")
        print(f"   • Avg {db['total_trades']/max(db['days'],1):.1f} trades/day" if db.get('days') else "")
    else:
        print(f"\n❌ DATABASE: No data available")

    if scores:
        print(f"\n✅ SIGNAL SCORES (scores.jsonl):")
        print(f"   • {scores['total_entries']:,} signal entries")
        print(f"   • {scores['days']} days of history" if scores.get('days') else "   • Date range unknown")
        print(f"   • {scores['unique_symbols']} unique symbols")
    else:
        print(f"\n❌ SIGNAL SCORES: No data available")

    if tpsl:
        print(f"\n✅ TP/SL DATA (tpsl.jsonl):")
        print(f"   • {tpsl['total_entries']:,} TP/SL entries")
        print(f"   • {tpsl['days']} days of history" if tpsl.get('days') else "   • Date range unknown")
        print(f"   • {tpsl['unique_symbols']} unique symbols")
    else:
        print(f"\n❌ TP/SL DATA: No data available")

    # Recommendations
    print("\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)

    if db and db['total_trades'] >= 200:
        print(f"\n✅ SUFFICIENT DATA for parameter correlation analysis")
        print(f"   • {db['total_trades']:,} trades exceeds minimum (200)")
        if db.get('days'):
            print(f"   • {db['days']} days provides good temporal coverage")
    elif db and db['total_trades'] < 200:
        print(f"\n⚠️  LIMITED DATA for statistical analysis")
        print(f"   • Only {db['total_trades']} trades (recommend 200+ minimum)")
        print(f"   • Results may not be statistically significant")
        print(f"   • Suggest collecting more data before tuning")
    else:
        print(f"\n❌ INSUFFICIENT DATA")
        print(f"   • Cannot perform parameter analysis without trade data")

    if scores and scores['total_entries'] >= 500:
        print(f"\n✅ EXCELLENT signal score coverage")
        print(f"   • {scores['total_entries']:,} signal snapshots available")
        print(f"   • Can correlate indicator behavior with outcomes")
    elif scores:
        print(f"\n⚠️  Limited signal score data")
        print(f"   • Only {scores['total_entries']} entries")

    print("\n" + "="*80)


if __name__ == "__main__":
    main()
