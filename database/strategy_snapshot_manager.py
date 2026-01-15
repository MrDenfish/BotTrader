#!/usr/bin/env python3
"""
CLI tool for managing strategy snapshots.

Usage:
    python3 database/strategy_snapshot_manager.py create --note "Description of changes"
    python3 database/strategy_snapshot_manager.py list
    python3 database/strategy_snapshot_manager.py show <snapshot_id>
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text, create_engine


# Database connection from environment
DB_USER = os.getenv("DB_USER", "bot_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "7317botTrade4ssm")
DB_NAME = os.getenv("DB_NAME", "bot_trader_db")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def create_snapshot(note: str = None):
    """Create a new strategy snapshot with current .env parameters."""

    # Read current configuration from .env
    config = {
        "score_buy_target": float(os.getenv("SCORE_BUY_TARGET", "2.5")),
        "score_sell_target": float(os.getenv("SCORE_SELL_TARGET", "2.5")),
        "indicator_weights": {
            'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 1.5,
            'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
            'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 1.5,
            'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
        },
        "rsi_buy_threshold": float(os.getenv("RSI_OVERSOLD", "25")),
        "rsi_sell_threshold": float(os.getenv("RSI_OVERBOUGHT", "75")),
        "roc_buy_threshold": float(os.getenv("ROC_BUY_24H", "2")),
        "roc_sell_threshold": float(os.getenv("ROC_SELL_24H", "1")),
        "macd_signal_threshold": 0.0,  # Not configurable via .env
        "tp_threshold": float(os.getenv("TAKE_PROFIT", "0.025")) * 100,  # Convert to percentage
        "sl_threshold": float(os.getenv("STOP_LOSS", "-0.01")) * 100,  # Convert to percentage
        "cooldown_bars": int(os.getenv("COOLDOWN_BARS", "7")),
        "flip_hysteresis_pct": float(os.getenv("FLIP_HYSTERESIS_PCT", "0.10")),
        "min_indicators_required": 2,  # Hard-coded based on recent changes
        "excluded_symbols": ['A8-USD', 'PENGU-USD'],  # Hard-coded based on trading_strategy.py
        "max_spread_pct": 1.0,  # Default value
    }

    print("=" * 80)
    print("Creating Strategy Snapshot")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  Score Buy Target:      {config['score_buy_target']}")
    print(f"  Score Sell Target:     {config['score_sell_target']}")
    print(f"  RSI Buy Threshold:     {config['rsi_buy_threshold']}")
    print(f"  RSI Sell Threshold:    {config['rsi_sell_threshold']}")
    print(f"  ROC Buy Threshold:     {config['roc_buy_threshold']}")
    print(f"  ROC Sell Threshold:    {config['roc_sell_threshold']}")
    print(f"  Take Profit:           {config['tp_threshold']}%")
    print(f"  Stop Loss:             {config['sl_threshold']}%")
    print(f"  Cooldown Bars:         {config['cooldown_bars']}")
    print(f"  Flip Hysteresis:       {config['flip_hysteresis_pct'] * 100}%")
    print(f"  Min Indicators Req:    {config['min_indicators_required']}")
    print(f"  Excluded Symbols:      {', '.join(config['excluded_symbols'])}")
    print(f"\n  Indicator Weights:")
    for indicator, weight in config['indicator_weights'].items():
        print(f"    {indicator:15} {weight}")

    if note:
        print(f"\nNotes: {note}")

    # Compute config hash
    import hashlib
    config_json = json.dumps(config, sort_keys=True)
    config_hash = hashlib.sha256(config_json.encode()).hexdigest()

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            # Check if config already exists and is active
            check_query = text("""
                SELECT snapshot_id, active_from FROM strategy_snapshots
                WHERE config_hash = :hash AND active_until IS NULL
            """)
            result = conn.execute(check_query, {"hash": config_hash})
            existing = result.fetchone()

            if existing:
                print(f"\n❌ This configuration already exists as active snapshot:")
                print(f"   Snapshot ID: {existing[0]}")
                print(f"   Active From: {existing[1]}")
                print(f"\nNo changes made.")
                return

            # Archive previous active snapshot
            archive_query = text("""
                UPDATE strategy_snapshots
                SET active_until = NOW()
                WHERE active_until IS NULL
            """)
            result = conn.execute(archive_query)
            archived_count = result.rowcount

            if archived_count > 0:
                print(f"\n✅ Archived {archived_count} previous snapshot(s)")

            # Insert new snapshot
            insert_query = text("""
                INSERT INTO strategy_snapshots (
                    active_from, score_buy_target, score_sell_target,
                    indicator_weights, rsi_buy_threshold, rsi_sell_threshold,
                    roc_buy_threshold, roc_sell_threshold, macd_signal_threshold,
                    tp_threshold, sl_threshold, cooldown_bars, flip_hysteresis_pct,
                    min_indicators_required, excluded_symbols, max_spread_pct,
                    config_hash, notes, created_by
                )
                VALUES (
                    NOW(), :score_buy, :score_sell, CAST(:weights AS jsonb),
                    :rsi_buy, :rsi_sell, :roc_buy, :roc_sell, :macd_threshold,
                    :tp, :sl, :cooldown, :hysteresis, :min_indicators,
                    CAST(:excluded AS text[]), :max_spread, :hash, :notes, 'cli-tool'
                )
                RETURNING snapshot_id, active_from
            """)

            result = conn.execute(insert_query, {
                "score_buy": config["score_buy_target"],
                "score_sell": config["score_sell_target"],
                "weights": json.dumps(config["indicator_weights"]),
                "rsi_buy": config["rsi_buy_threshold"],
                "rsi_sell": config["rsi_sell_threshold"],
                "roc_buy": config["roc_buy_threshold"],
                "roc_sell": config["roc_sell_threshold"],
                "macd_threshold": config["macd_signal_threshold"],
                "tp": config["tp_threshold"],
                "sl": config["sl_threshold"],
                "cooldown": config["cooldown_bars"],
                "hysteresis": config["flip_hysteresis_pct"],
                "min_indicators": config["min_indicators_required"],
                "excluded": config["excluded_symbols"],
                "max_spread": config["max_spread_pct"],
                "hash": config_hash,
                "notes": note
            })

            row = result.fetchone()
            conn.commit()

            print(f"\n✅ Created new strategy snapshot:")
            print(f"   Snapshot ID: {row[0]}")
            print(f"   Active From: {row[1]}")
            print(f"\n" + "=" * 80)

    except Exception as e:
        print(f"\n❌ Error creating snapshot: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def list_snapshots(limit: int = 10):
    """List recent strategy snapshots."""
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            query = text("""
                SELECT
                    snapshot_id,
                    active_from,
                    active_until,
                    score_buy_target,
                    min_indicators_required,
                    cooldown_bars,
                    notes,
                    CASE WHEN active_until IS NULL THEN '✅ ACTIVE' ELSE '' END as status
                FROM strategy_snapshots
                ORDER BY active_from DESC
                LIMIT :limit
            """)

            result = conn.execute(query, {"limit": limit})
            rows = result.fetchall()

            if not rows:
                print("No snapshots found.")
                return

            print("=" * 120)
            print("Strategy Snapshots")
            print("=" * 120)
            print(f"{'Snapshot ID':<38} {'Active From':<20} {'Active Until':<20} {'Buy':<6} {'MinInd':<7} {'Cool':<5} {'Status':<10} Notes")
            print("-" * 120)

            for row in rows:
                snapshot_id = str(row[0])
                active_from = row[1].strftime("%Y-%m-%d %H:%M:%S") if row[1] else ""
                active_until = row[2].strftime("%Y-%m-%d %H:%M:%S") if row[2] else ""
                score_buy = row[3]
                min_ind = row[4]
                cooldown = row[5]
                notes = (row[6][:40] + "...") if row[6] and len(row[6]) > 40 else (row[6] or "")
                status = row[7]

                print(f"{snapshot_id:<38} {active_from:<20} {active_until:<20} {score_buy:<6} {min_ind:<7} {cooldown:<5} {status:<10} {notes}")

            print("=" * 120)

    except Exception as e:
        print(f"❌ Error listing snapshots: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def show_snapshot(snapshot_id: str):
    """Show detailed information about a specific snapshot."""
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            query = text("""
                SELECT * FROM strategy_snapshots
                WHERE snapshot_id = :id::uuid
            """)

            result = conn.execute(query, {"id": snapshot_id})
            row = result.fetchone()

            if not row:
                print(f"❌ Snapshot {snapshot_id} not found.")
                return

            # Get column names
            columns = result.keys()
            row_dict = dict(zip(columns, row))

            print("=" * 80)
            print(f"Strategy Snapshot: {row_dict['snapshot_id']}")
            print("=" * 80)

            print(f"\nStatus: {'✅ ACTIVE' if row_dict['active_until'] is None else 'Archived'}")
            print(f"Active From:  {row_dict['active_from']}")
            print(f"Active Until: {row_dict['active_until'] or 'N/A (currently active)'}")
            print(f"Created By:   {row_dict['created_by']}")

            print(f"\nScoring Targets:")
            print(f"  Buy Target:   {row_dict['score_buy_target']}")
            print(f"  Sell Target:  {row_dict['score_sell_target']}")

            print(f"\nIndicator Thresholds:")
            print(f"  RSI Buy:      {row_dict['rsi_buy_threshold']}")
            print(f"  RSI Sell:     {row_dict['rsi_sell_threshold']}")
            print(f"  ROC Buy:      {row_dict['roc_buy_threshold']}")
            print(f"  ROC Sell:     {row_dict['roc_sell_threshold']}")
            print(f"  MACD Signal:  {row_dict['macd_signal_threshold']}")

            print(f"\nRisk Management:")
            print(f"  Take Profit:  {row_dict['tp_threshold']}%")
            print(f"  Stop Loss:    {row_dict['sl_threshold']}%")

            print(f"\nTrade Guardrails:")
            print(f"  Cooldown Bars:        {row_dict['cooldown_bars']}")
            print(f"  Flip Hysteresis:      {row_dict['flip_hysteresis_pct'] * 100}%")
            print(f"  Min Indicators Req:   {row_dict['min_indicators_required']}")

            print(f"\nSymbol Filters:")
            print(f"  Excluded Symbols: {', '.join(row_dict['excluded_symbols']) if row_dict['excluded_symbols'] else 'None'}")
            print(f"  Max Spread:       {row_dict['max_spread_pct']}%")

            print(f"\nIndicator Weights:")
            weights = row_dict['indicator_weights']
            for indicator, weight in sorted(weights.items()):
                print(f"  {indicator:15} {weight}")

            if row_dict['notes']:
                print(f"\nNotes:")
                print(f"  {row_dict['notes']}")

            print(f"\nConfig Hash: {row_dict['config_hash']}")
            print("=" * 80)

    except Exception as e:
        print(f"❌ Error showing snapshot: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Manage strategy snapshots for bot configuration tracking"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new strategy snapshot")
    create_parser.add_argument("--note", type=str, help="Notes about this configuration")

    # List command
    list_parser = subparsers.add_parser("list", help="List recent snapshots")
    list_parser.add_argument("--limit", type=int, default=10, help="Number of snapshots to show")

    # Show command
    show_parser = subparsers.add_parser("show", help="Show detailed snapshot information")
    show_parser.add_argument("snapshot_id", type=str, help="Snapshot ID to display")

    args = parser.parse_args()

    if args.command == "create":
        create_snapshot(args.note)
    elif args.command == "list":
        list_snapshots(args.limit)
    elif args.command == "show":
        show_snapshot(args.snapshot_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
