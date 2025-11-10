#!/usr/bin/env python3
"""
Signal Quality Diagnostic Tool

Analyzes score.jsonl to evaluate trading signal quality.
This helps identify if signals are causing profitability issues.

Usage:
    python diagnostic_signal_quality.py [--hours 24] [--min-score 5.0]
"""

import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import Dict, List


class SignalQualityAnalyzer:
    """Analyzes signal quality from score.jsonl logs."""

    def __init__(self, hours: int = 24, min_score: float = 5.0):
        self.hours = hours
        self.min_score = min_score
        self.signals = []

    def load_signals(self, file_path: str = "/app/logs/score.jsonl"):
        """Load signals from JSONL file."""
        path = Path(file_path)
        if not path.exists():
            path = Path("logs/score.jsonl")

        if not path.exists():
            print(f"❌ Signal log file not found: {file_path}")
            return False

        print(f"📂 Loading signals from: {path}")

        cutoff_time = datetime.now() - timedelta(hours=self.hours)

        with open(path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    # Parse timestamp
                    ts_str = data.get('ts', '')
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                        if ts < cutoff_time:
                            continue

                    self.signals.append(data)
                except Exception as e:
                    print(f"⚠️  Error parsing line {line_num}: {e}")
                    continue

        print(f"✓ Loaded {len(self.signals)} signals from last {self.hours} hours")
        return len(self.signals) > 0

    def analyze_all(self):
        """Run all signal analyses."""
        if not self.signals:
            print("No signals to analyze")
            return

        print("\n" + "=" * 80)
        print(f"🔍 SIGNAL QUALITY ANALYSIS (Last {self.hours} Hours)")
        print("=" * 80)
        print()

        self.action_distribution()
        self.score_distribution()
        self.indicator_analysis()
        self.symbol_signal_quality()
        self.trigger_analysis()
        self.recommendations()

    def action_distribution(self):
        """Analyze distribution of actions."""
        print("📊 ACTION DISTRIBUTION")
        print("-" * 80)

        actions = Counter(s.get('action', 'unknown') for s in self.signals)
        total = len(self.signals)

        print(f"Total Signals: {total}")
        for action, count in actions.most_common():
            pct = (count / total * 100) if total > 0 else 0
            print(f"  {action.upper():<6}: {count:>5} ({pct:>5.1f}%)")

        # Analysis
        buy_pct = (actions.get('buy', 0) / total * 100) if total > 0 else 0
        sell_pct = (actions.get('sell', 0) / total * 100) if total > 0 else 0
        hold_pct = (actions.get('hold', 0) / total * 100) if total > 0 else 0

        if hold_pct > 80:
            print("\n⚠️  High HOLD percentage (>80%) - bot may be too conservative")
            print("   Recommendation: Lower score thresholds or adjust indicator weights")
        elif buy_pct < 5 and sell_pct < 5:
            print("\n⚠️  Very low BUY/SELL signals (<5%) - bot is barely trading")
            print("   Recommendation: Review signal generation logic")

        print()

    def score_distribution(self):
        """Analyze score distributions."""
        print("📈 SCORE DISTRIBUTION")
        print("-" * 80)

        buy_scores = [s.get('buy_score', 0) for s in self.signals if 'buy_score' in s]
        sell_scores = [s.get('sell_score', 0) for s in self.signals if 'sell_score' in s]

        if buy_scores:
            avg_buy = sum(buy_scores) / len(buy_scores)
            max_buy = max(buy_scores)
            above_threshold = sum(1 for s in buy_scores if s >= self.min_score)

            print(f"BUY Scores:")
            print(f"  Average: {avg_buy:.2f}")
            print(f"  Maximum: {max_buy:.2f}")
            print(f"  Above Threshold ({self.min_score}): {above_threshold} ({above_threshold/len(buy_scores)*100:.1f}%)")

            if avg_buy < self.min_score * 0.6:
                print(f"  ⚠️  Average far below threshold - signals rarely trigger")

        if sell_scores:
            avg_sell = sum(sell_scores) / len(sell_scores)
            max_sell = max(sell_scores)
            above_threshold = sum(1 for s in sell_scores if s >= self.min_score)

            print(f"\nSELL Scores:")
            print(f"  Average: {avg_sell:.2f}")
            print(f"  Maximum: {max_sell:.2f}")
            print(f"  Above Threshold ({self.min_score}): {above_threshold} ({above_threshold/len(sell_scores)*100:.1f}%)")

            if avg_sell < self.min_score * 0.6:
                print(f"  ⚠️  Average far below threshold - signals rarely trigger")

        print()

    def indicator_analysis(self):
        """Analyze indicator contributions."""
        print("🎯 INDICATOR CONTRIBUTION ANALYSIS")
        print("-" * 80)

        # Analyze buy indicators
        buy_components = defaultdict(list)
        sell_components = defaultdict(list)

        for signal in self.signals:
            if signal.get('action') == 'buy' and 'top_buy_components' in signal:
                for comp in signal['top_buy_components']:
                    indicator = comp.get('indicator', 'unknown')
                    contribution = comp.get('contribution', 0)
                    buy_components[indicator].append(contribution)

            if signal.get('action') == 'sell' and 'top_sell_components' in signal:
                for comp in signal['top_sell_components']:
                    indicator = comp.get('indicator', 'unknown')
                    contribution = comp.get('contribution', 0)
                    sell_components[indicator].append(contribution)

        if buy_components:
            print("Top BUY Indicators (by average contribution):")
            sorted_buy = sorted(buy_components.items(),
                              key=lambda x: sum(x[1])/len(x[1]) if x[1] else 0,
                              reverse=True)
            for indicator, contribs in sorted_buy[:10]:
                avg = sum(contribs) / len(contribs) if contribs else 0
                freq = len(contribs)
                print(f"  {indicator:<20}: Avg={avg:>5.2f}, Frequency={freq:>4}")

        if sell_components:
            print("\nTop SELL Indicators (by average contribution):")
            sorted_sell = sorted(sell_components.items(),
                               key=lambda x: sum(x[1])/len(x[1]) if x[1] else 0,
                               reverse=True)
            for indicator, contribs in sorted_sell[:10]:
                avg = sum(contribs) / len(contribs) if contribs else 0
                freq = len(contribs)
                print(f"  {indicator:<20}: Avg={avg:>5.2f}, Frequency={freq:>4}")

        print()

    def symbol_signal_quality(self):
        """Analyze signal quality by symbol."""
        print("💎 SIGNAL QUALITY BY SYMBOL (Top 15)")
        print("-" * 80)

        symbol_signals = defaultdict(lambda: {'buy': 0, 'sell': 0, 'hold': 0, 'total': 0})

        for signal in self.signals:
            symbol = signal.get('symbol', 'unknown')
            action = signal.get('action', 'hold')
            symbol_signals[symbol][action] += 1
            symbol_signals[symbol]['total'] += 1

        # Calculate signal rate for each symbol
        symbol_stats = []
        for symbol, stats in symbol_signals.items():
            signal_rate = (stats['buy'] + stats['sell']) / stats['total'] if stats['total'] > 0 else 0
            symbol_stats.append({
                'symbol': symbol,
                'total': stats['total'],
                'buy': stats['buy'],
                'sell': stats['sell'],
                'hold': stats['hold'],
                'signal_rate': signal_rate
            })

        # Sort by signal rate
        symbol_stats.sort(key=lambda x: x['signal_rate'], reverse=True)

        print(f"{'Symbol':<15} {'Total':>6} {'Buy':>5} {'Sell':>5} {'Hold':>5} {'Signal%':>8}")
        print("-" * 60)

        for stats in symbol_stats[:15]:
            print(f"{stats['symbol']:<15} {stats['total']:>6} {stats['buy']:>5} {stats['sell']:>5} "
                  f"{stats['hold']:>5} {stats['signal_rate']*100:>7.1f}%")

        # Identify problematic symbols
        low_signal_symbols = [s for s in symbol_stats if s['signal_rate'] < 0.05 and s['total'] > 10]
        if low_signal_symbols:
            print(f"\n⚠️  {len(low_signal_symbols)} symbols with <5% signal rate (mostly HOLD)")
            print("   These symbols may not be suitable for the strategy")

        print()

    def trigger_analysis(self):
        """Analyze trigger types."""
        print("🎬 TRIGGER TYPE ANALYSIS")
        print("-" * 80)

        triggers = Counter()
        for signal in self.signals:
            if signal.get('action') in ['buy', 'sell']:
                trigger = signal.get('trigger', 'unknown')
                triggers[trigger] += 1

        if triggers:
            print("Trigger Distribution:")
            total = sum(triggers.values())
            for trigger, count in triggers.most_common():
                pct = (count / total * 100) if total > 0 else 0
                print(f"  {trigger:<15}: {count:>4} ({pct:>5.1f}%)")

            # Analysis
            if triggers.get('roc_buy', 0) + triggers.get('roc_momo', 0) > total * 0.6:
                print("\n⚠️  High ROC trigger rate (>60%)")
                print("   Bot is heavily momentum-driven - may chase prices")
            elif triggers.get('score', 0) > total * 0.8:
                print("\n✓ Mostly score-based triggers - balanced approach")
        else:
            print("No trigger data available")

        print()

    def recommendations(self):
        """Provide recommendations based on analysis."""
        print("💡 RECOMMENDATIONS")
        print("-" * 80)

        actions = Counter(s.get('action', 'unknown') for s in self.signals)
        total = len(self.signals)
        hold_pct = (actions.get('hold', 0) / total * 100) if total > 0 else 0

        buy_scores = [s.get('buy_score', 0) for s in self.signals if 'buy_score' in s]
        avg_buy = sum(buy_scores) / len(buy_scores) if buy_scores else 0

        recommendations = []

        # Check hold percentage
        if hold_pct > 80:
            recommendations.append({
                'issue': 'Too many HOLD signals (>80%)',
                'impact': 'Bot not trading frequently enough',
                'solution': 'Lower SCORE_BUY_TARGET and SCORE_SELL_TARGET (try 4.5 instead of 5.5)',
                'config': 'Config/constants_trading.py or environment variable'
            })

        # Check average scores
        if avg_buy < self.min_score * 0.7:
            recommendations.append({
                'issue': f'Average buy score ({avg_buy:.2f}) far below threshold ({self.min_score})',
                'impact': 'Signals rarely meet threshold requirements',
                'solution': 'Adjust indicator weights or lower threshold',
                'config': 'sighook/signal_manager.py (STRATEGY_WEIGHTS)'
            })

        # Check if ROC is dominating
        triggers = Counter(s.get('trigger', '') for s in self.signals if s.get('action') in ['buy', 'sell'])
        roc_count = triggers.get('roc_buy', 0) + triggers.get('roc_momo', 0)
        if triggers and roc_count > sum(triggers.values()) * 0.6:
            recommendations.append({
                'issue': 'ROC momentum triggers dominate (>60%)',
                'impact': 'May be chasing price movements, buying high',
                'solution': 'Increase ROC threshold from 5% to 7-8%, or reduce ROC weight',
                'config': 'Config/constants_trading.py (ROC_THRESHOLD)'
            })

        # Output recommendations
        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                print(f"{i}. ISSUE: {rec['issue']}")
                print(f"   Impact: {rec['impact']}")
                print(f"   Solution: {rec['solution']}")
                print(f"   File: {rec['config']}")
                print()
        else:
            print("✓ Signal quality appears reasonable. Check trade execution and TP/SL settings.")

        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Signal Quality Diagnostic')
    parser.add_argument('--hours', type=int, default=24, help='Hours to analyze (default: 24)')
    parser.add_argument('--min-score', type=float, default=5.5, help='Minimum score threshold (default: 5.5)')
    parser.add_argument('--file', type=str, default='/app/logs/score.jsonl', help='Path to score.jsonl')
    args = parser.parse_args()

    analyzer = SignalQualityAnalyzer(hours=args.hours, min_score=args.min_score)

    if analyzer.load_signals(args.file):
        analyzer.analyze_all()
    else:
        print("\n❌ Unable to load signals. Check file path.")
        print(f"   Expected: {args.file}")
        print("   Or: logs/score.jsonl")


if __name__ == "__main__":
    main()
