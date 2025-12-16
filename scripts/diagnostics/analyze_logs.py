#!/usr/bin/env python3
"""
Structured Log Analysis Tool for BotTrader

Analyzes JSON logs from the structured logging system to provide insights on:
- Trading activity (BUY, SELL, ORDER_SENT)
- Errors and warnings
- Performance metrics
- Component activity
- Context-based filtering (trade_id, symbol, etc.)

Usage:
    # Analyze all logs
    python analyze_logs.py

    # Analyze specific log file
    python analyze_logs.py --file logs/webhook.log

    # Show only errors
    python analyze_logs.py --level ERROR

    # Filter by symbol
    python analyze_logs.py --symbol BTC-USD

    # Show trading activity only
    python analyze_logs.py --trading-only

    # Time range analysis
    python analyze_logs.py --last 1h
    python analyze_logs.py --last 24h
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional


class LogAnalyzer:
    """Analyzes structured JSON logs from BotTrader."""

    # Trading log levels
    TRADING_LEVELS = {'BUY', 'SELL', 'ORDER_SENT', 'TAKE_PROFIT', 'STOP_LOSS',
                      'TAKE_LOSS', 'INSUFFICIENT_FUNDS', 'BAD_ORDER'}

    # Level priorities for sorting
    LEVEL_PRIORITY = {
        'CRITICAL': 0, 'ERROR': 1, 'WARNING': 2, 'BAD_ORDER': 3,
        'ORDER_SENT': 4, 'BUY': 5, 'SELL': 6, 'INFO': 7, 'DEBUG': 8
    }

    def __init__(self, log_dir: str = 'logs'):
        """Initialize log analyzer."""
        self.log_dir = Path(log_dir)
        self.entries: List[Dict[str, Any]] = []
        self.stats = {
            'total_entries': 0,
            'by_level': Counter(),
            'by_logger': Counter(),
            'by_component': Counter(),
            'errors': [],
            'trades': [],
            'warnings': [],
        }

    def load_log_file(self, log_file: Path) -> int:
        """Load and parse a single JSON log file."""
        count = 0

        try:
            with open(log_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = json.loads(line)
                        self.entries.append(entry)
                        count += 1
                    except json.JSONDecodeError as e:
                        print(f"Warning: Invalid JSON at {log_file}:{line_num}: {e}", file=sys.stderr)
                        continue
        except Exception as e:
            print(f"Error reading {log_file}: {e}", file=sys.stderr)

        return count

    def load_all_logs(self) -> None:
        """Load all .log files from log directory."""
        if not self.log_dir.exists():
            print(f"Error: Log directory '{self.log_dir}' not found", file=sys.stderr)
            return

        log_files = list(self.log_dir.glob('*.log'))

        if not log_files:
            print(f"Warning: No .log files found in {self.log_dir}", file=sys.stderr)
            return

        print(f"Loading logs from {self.log_dir}...")
        for log_file in sorted(log_files):
            count = self.load_log_file(log_file)
            if count > 0:
                print(f"  ✓ {log_file.name}: {count} entries")

        print(f"\nTotal entries loaded: {len(self.entries)}")

    def analyze(self) -> None:
        """Analyze loaded log entries and compute statistics."""
        self.stats['total_entries'] = len(self.entries)

        for entry in self.entries:
            level = entry.get('level', 'UNKNOWN')
            logger = entry.get('logger', 'unknown')

            # Count by level and logger
            self.stats['by_level'][level] += 1
            self.stats['by_logger'][logger] += 1

            # Extract component from context
            context = entry.get('context', {})
            if isinstance(context, dict):
                component = context.get('component')
                if component:
                    self.stats['by_component'][component] += 1

            # Collect errors
            if level in ('ERROR', 'CRITICAL'):
                self.stats['errors'].append(entry)

            # Collect warnings
            if level == 'WARNING':
                self.stats['warnings'].append(entry)

            # Collect trading activity
            if level in self.TRADING_LEVELS:
                self.stats['trades'].append(entry)

    def filter_entries(
        self,
        level: Optional[str] = None,
        logger: Optional[str] = None,
        symbol: Optional[str] = None,
        trade_id: Optional[str] = None,
        component: Optional[str] = None,
        trading_only: bool = False,
        last: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Filter log entries by criteria."""
        filtered = self.entries.copy()

        # Filter by log level
        if level:
            filtered = [e for e in filtered if e.get('level') == level.upper()]

        # Filter by logger name
        if logger:
            filtered = [e for e in filtered if logger.lower() in e.get('logger', '').lower()]

        # Filter by trading levels only
        if trading_only:
            filtered = [e for e in filtered if e.get('level') in self.TRADING_LEVELS]

        # Filter by context fields
        if symbol or trade_id or component:
            def matches_context(entry):
                ctx = entry.get('context', {})
                if not isinstance(ctx, dict):
                    return False

                if symbol and ctx.get('symbol') != symbol:
                    return False
                if trade_id and ctx.get('trade_id') != trade_id:
                    return False
                if component and ctx.get('component') != component:
                    return False

                return True

            filtered = [e for e in filtered if matches_context(e)]

        # Filter by time range
        if last:
            cutoff_time = self._parse_time_range(last)
            if cutoff_time:
                filtered = [e for e in filtered if self._parse_timestamp(e.get('timestamp')) >= cutoff_time]

        return filtered

    def _parse_time_range(self, time_str: str) -> Optional[datetime]:
        """Parse time range string (e.g., '1h', '24h', '7d')."""
        from datetime import timezone
        time_str = time_str.strip().lower()

        try:
            if time_str.endswith('h'):
                hours = int(time_str[:-1])
                return datetime.now(timezone.utc) - timedelta(hours=hours)
            elif time_str.endswith('d'):
                days = int(time_str[:-1])
                return datetime.now(timezone.utc) - timedelta(days=days)
            elif time_str.endswith('m'):
                minutes = int(time_str[:-1])
                return datetime.now(timezone.utc) - timedelta(minutes=minutes)
        except ValueError:
            pass

        return None

    def _parse_timestamp(self, timestamp: Optional[str]) -> datetime:
        """Parse ISO timestamp from log entry."""
        from datetime import timezone
        if not timestamp:
            return datetime.min.replace(tzinfo=timezone.utc)

        try:
            # Handle 'Z' suffix
            if timestamp.endswith('Z'):
                timestamp = timestamp[:-1] + '+00:00'
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    def print_summary(self) -> None:
        """Print analysis summary."""
        print("\n" + "=" * 70)
        print("BOTTRADER LOG ANALYSIS SUMMARY")
        print("=" * 70)

        print(f"\n📊 Total Log Entries: {self.stats['total_entries']}")

        # By level
        print("\n📈 Entries by Level:")
        for level, count in sorted(self.stats['by_level'].items(),
                                   key=lambda x: self.LEVEL_PRIORITY.get(x[0], 99)):
            percentage = (count / self.stats['total_entries'] * 100) if self.stats['total_entries'] else 0
            icon = self._get_level_icon(level)
            print(f"   {icon} {level:20} {count:6} ({percentage:5.1f}%)")

        # By logger
        print("\n📝 Top Loggers:")
        for logger, count in self.stats['by_logger'].most_common(10):
            percentage = (count / self.stats['total_entries'] * 100) if self.stats['total_entries'] else 0
            print(f"   • {logger:25} {count:6} ({percentage:5.1f}%)")

        # By component
        if self.stats['by_component']:
            print("\n🔧 Top Components:")
            for component, count in self.stats['by_component'].most_common(10):
                percentage = (count / self.stats['total_entries'] * 100) if self.stats['total_entries'] else 0
                print(f"   • {component:25} {count:6} ({percentage:5.1f}%)")

        # Trading activity
        if self.stats['trades']:
            print(f"\n💰 Trading Activity: {len(self.stats['trades'])} events")
            trade_counts = Counter(t.get('level') for t in self.stats['trades'])
            for level, count in trade_counts.most_common():
                print(f"   • {level:20} {count:6}")

        # Errors
        if self.stats['errors']:
            print(f"\n❌ Errors Found: {len(self.stats['errors'])}")
            self._print_entries(self.stats['errors'][:5], "Recent Errors")

        # Warnings
        if self.stats['warnings']:
            print(f"\n⚠️  Warnings Found: {len(self.stats['warnings'])}")
            self._print_entries(self.stats['warnings'][:5], "Recent Warnings")

    def _get_level_icon(self, level: str) -> str:
        """Get emoji icon for log level."""
        icons = {
            'CRITICAL': '🔴',
            'ERROR': '❌',
            'WARNING': '⚠️ ',
            'INFO': 'ℹ️ ',
            'DEBUG': '🐛',
            'BUY': '🟢',
            'SELL': '🔴',
            'ORDER_SENT': '📤',
            'TAKE_PROFIT': '💰',
            'STOP_LOSS': '🛑',
            'BAD_ORDER': '🚫',
            'INSUFFICIENT_FUNDS': '💸',
        }
        return icons.get(level, '  ')

    def _print_entries(self, entries: List[Dict[str, Any]], title: str = "Log Entries") -> None:
        """Print formatted log entries."""
        if not entries:
            print(f"\n{title}: None")
            return

        print(f"\n{title}:")
        print("-" * 70)

        for i, entry in enumerate(entries, 1):
            timestamp = entry.get('timestamp', 'N/A')
            level = entry.get('level', 'UNKNOWN')
            logger = entry.get('logger', 'unknown')
            message = entry.get('message', '')

            # Format timestamp (just time portion)
            try:
                dt = self._parse_timestamp(timestamp)
                time_str = dt.strftime('%H:%M:%S')
            except:
                time_str = timestamp[:19] if len(timestamp) > 19 else timestamp

            icon = self._get_level_icon(level)
            print(f"\n{i}. [{time_str}] {icon} {level} - {logger}")
            print(f"   Message: {message}")

            # Show context
            context = entry.get('context', {})
            if isinstance(context, dict) and context:
                ctx_str = ', '.join(f"{k}={v}" for k, v in context.items())
                print(f"   Context: {ctx_str}")

            # Show extra fields
            extra = entry.get('extra', {})
            if isinstance(extra, dict) and extra:
                extra_str = ', '.join(f"{k}={v}" for k, v in extra.items())
                print(f"   Extra: {extra_str}")

            # Show exception info (truncated)
            if entry.get('exc_info'):
                exc_lines = entry['exc_info'].split('\n')
                print(f"   Exception: {exc_lines[0]}")
                if len(exc_lines) > 1:
                    print(f"              {exc_lines[-1]}")

    def export_filtered(self, entries: List[Dict[str, Any]], output_file: str) -> None:
        """Export filtered entries to JSON file."""
        output_path = Path(output_file)

        with open(output_path, 'w') as f:
            json.dump(entries, f, indent=2)

        print(f"\n✓ Exported {len(entries)} entries to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Analyze BotTrader structured JSON logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--dir', default='logs', help='Log directory (default: logs)')
    parser.add_argument('--file', help='Analyze specific log file')
    parser.add_argument('--level', help='Filter by log level (ERROR, WARNING, INFO, etc.)')
    parser.add_argument('--logger', help='Filter by logger name')
    parser.add_argument('--symbol', help='Filter by trading symbol (e.g., BTC-USD)')
    parser.add_argument('--trade-id', help='Filter by trade ID')
    parser.add_argument('--component', help='Filter by component name')
    parser.add_argument('--trading-only', action='store_true', help='Show only trading activity')
    parser.add_argument('--last', help='Show entries from last N time (e.g., 1h, 24h, 7d)')
    parser.add_argument('--export', help='Export filtered results to JSON file')
    parser.add_argument('--limit', type=int, default=50, help='Limit number of entries to show (default: 50)')

    args = parser.parse_args()

    # Initialize analyzer
    analyzer = LogAnalyzer(log_dir=args.dir)

    # Load logs
    if args.file:
        count = analyzer.load_log_file(Path(args.file))
        print(f"Loaded {count} entries from {args.file}")
    else:
        analyzer.load_all_logs()

    if not analyzer.entries:
        print("\nNo log entries found. Exiting.")
        return 1

    # Analyze
    analyzer.analyze()

    # Apply filters
    filtered = analyzer.filter_entries(
        level=args.level,
        logger=args.logger,
        symbol=args.symbol,
        trade_id=args.trade_id,
        component=args.component,
        trading_only=args.trading_only,
        last=args.last,
    )

    # Show results
    if args.level or args.logger or args.symbol or args.trade_id or args.component or args.trading_only or args.last:
        # Filtered view
        print(f"\n🔍 Filtered Results: {len(filtered)} entries")
        analyzer._print_entries(filtered[:args.limit], "Filtered Log Entries")

        if len(filtered) > args.limit:
            print(f"\n... and {len(filtered) - args.limit} more entries (use --limit to see more)")
    else:
        # Summary view
        analyzer.print_summary()

    # Export if requested
    if args.export:
        analyzer.export_filtered(filtered, args.export)

    print("\n" + "=" * 70)
    return 0


if __name__ == '__main__':
    sys.exit(main())
