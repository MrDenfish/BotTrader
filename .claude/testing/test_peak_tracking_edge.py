#!/usr/bin/env python3
"""
Peak Tracking Strategy Test - EDGE-USD Historical Data
Tests the peak tracking exit logic against actual EDGE-USD price data
from January 3, 2026 07:38-18:24 UTC
"""

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple

class PeakTrackingSimulator:
    """Simulates peak tracking strategy on historical data"""

    def __init__(self):
        # Peak tracking parameters (from .env)
        self.peak_drawdown_pct = Decimal('0.05')  # -5% from peak
        self.peak_min_profit_pct = Decimal('0.06')  # Enable at +6% profit
        self.peak_smoothing_mins = 5  # 5-minute SMA
        self.peak_max_hold_mins = 1440  # 24 hours

        # Fee structure (from .env)
        self.maker_fee = Decimal('0.004')  # 0.4% maker
        self.taker_fee = Decimal('0.008')  # 0.8% taker

        # Position state
        self.entry_price = None
        self.entry_time = None
        self.peak_price = None
        self.price_history = []
        self.breakeven_activated = False

        # Results tracking
        self.events = []

    def calculate_pnl_pct(self, entry: Decimal, exit: Decimal) -> Decimal:
        """Calculate fee-aware P&L percentage"""
        entry_cost = entry * (Decimal('1') + self.maker_fee)
        exit_revenue = exit * (Decimal('1') - self.taker_fee)
        return (exit_revenue - entry_cost) / entry_cost

    def init_position(self, price: Decimal, timestamp: datetime):
        """Initialize position at entry price"""
        self.entry_price = price
        self.entry_time = timestamp
        self.peak_price = price
        self.price_history = [float(price)]
        self.breakeven_activated = False

        self.events.append({
            'time': timestamp,
            'event': 'ENTRY',
            'price': price,
            'pnl_pct': Decimal('0'),
            'peak': price,
            'smoothed': price,
            'detail': f"ROC BUY signal at ${price:.6f}"
        })

    def update_peak(self, current_price: Decimal):
        """Update 5-minute SMA peak tracking"""
        # Add current price to rolling window
        self.price_history.append(float(current_price))

        # Keep only last N prices
        if len(self.price_history) > self.peak_smoothing_mins:
            self.price_history = self.price_history[-self.peak_smoothing_mins:]

        # Calculate smoothed price (5-min SMA)
        smoothed_price = Decimal(str(sum(self.price_history) / len(self.price_history)))

        # Update peak if smoothed price is higher
        if smoothed_price > self.peak_price:
            self.peak_price = smoothed_price
            return smoothed_price, True  # Peak updated

        return smoothed_price, False  # No peak update

    def check_exit(self, current_price: Decimal, timestamp: datetime) -> Tuple[bool, str, Decimal]:
        """
        Check if position should exit based on peak tracking logic

        Returns:
            (should_exit, exit_reason, smoothed_price)
        """
        if not self.entry_price:
            return False, None, current_price

        # Update peak tracking
        smoothed_price, peak_updated = self.update_peak(current_price)

        # Calculate current P&L
        pnl_pct = self.calculate_pnl_pct(self.entry_price, current_price)

        # 1. Check 24-hour time limit
        time_held_mins = (timestamp - self.entry_time).total_seconds() / 60
        if time_held_mins >= self.peak_max_hold_mins:
            return True, f"24hr_limit_{pnl_pct:.2%}", smoothed_price

        # 2. Check if minimum profit threshold reached (+6%)
        if pnl_pct < self.peak_min_profit_pct:
            # Not yet activated - would use standard stops (but we don't simulate those)
            return False, None, smoothed_price

        # 3. Activate break-even protection (one-time)
        if not self.breakeven_activated:
            self.breakeven_activated = True
            self.events.append({
                'time': timestamp,
                'event': 'BREAKEVEN_ACTIVATED',
                'price': current_price,
                'pnl_pct': pnl_pct,
                'peak': self.peak_price,
                'smoothed': smoothed_price,
                'detail': f"+6% profit reached, peak tracking active"
            })

        # 4. Check peak drawdown exit (-5% from peak)
        drawdown_from_peak = (smoothed_price - self.peak_price) / self.peak_price

        if drawdown_from_peak <= -self.peak_drawdown_pct:
            return True, f"peak_drawdown_{drawdown_from_peak:.2%}_from_${self.peak_price:.6f}", smoothed_price

        # 5. Check break-even protection (if price drops back to entry)
        total_fees = self.maker_fee + self.taker_fee  # 1.2%
        breakeven_threshold = -total_fees

        if pnl_pct <= breakeven_threshold:
            return True, f"breakeven_protection_{pnl_pct:.2%}", smoothed_price

        # Log peak updates
        if peak_updated:
            self.events.append({
                'time': timestamp,
                'event': 'PEAK_UPDATE',
                'price': current_price,
                'pnl_pct': pnl_pct,
                'peak': self.peak_price,
                'smoothed': smoothed_price,
                'detail': f"New peak: ${self.peak_price:.6f} (5-min SMA)"
            })

        return False, None, smoothed_price

    def close_position(self, exit_price: Decimal, exit_reason: str, timestamp: datetime):
        """Close position and record results"""
        final_pnl_pct = self.calculate_pnl_pct(self.entry_price, exit_price)
        time_held_mins = (timestamp - self.entry_time).total_seconds() / 60

        self.events.append({
            'time': timestamp,
            'event': 'EXIT',
            'price': exit_price,
            'pnl_pct': final_pnl_pct,
            'peak': self.peak_price,
            'smoothed': exit_price,
            'detail': f"{exit_reason} | Held {time_held_mins:.0f}min | P&L: {final_pnl_pct:.2%}"
        })

        return final_pnl_pct, time_held_mins


def fetch_edge_data():
    """Fetch EDGE-USD price data from database via SSH"""
    import subprocess

    print("Fetching EDGE-USD data from AWS database...")

    query = """
    SELECT
        time,
        open,
        high,
        low,
        close,
        volume
    FROM ohlcv_data
    WHERE symbol = 'EDGE-USD'
      AND time >= '2026-01-03 07:30:00+00'
      AND time <= '2026-01-03 19:00:00+00'
    ORDER BY time ASC;
    """

    cmd = [
        'ssh', 'bottrader-aws',
        f'psql postgresql://bot_user:7317botTrade4ssm@127.0.0.1:5432/bot_trader_db -t -A -F"," -c "{query}"'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"Error: {result.stderr}")
            return []

        # Parse CSV output
        data = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(',')
            if len(parts) >= 6:
                data.append({
                    'timestamp': datetime.fromisoformat(parts[0].replace('+00', '+00:00')),
                    'open': Decimal(parts[1]),
                    'high': Decimal(parts[2]),
                    'low': Decimal(parts[3]),
                    'close': Decimal(parts[4]),
                    'volume': Decimal(parts[5])
                })

        print(f"✓ Loaded {len(data)} candles from database")
        return data

    except Exception as e:
        print(f"Error fetching data: {e}")
        return []


def run_simulation():
    """Run peak tracking simulation on EDGE-USD data"""

    print("=" * 80)
    print("PEAK TRACKING SIMULATION - EDGE-USD (Jan 3, 2026 07:38-18:24 UTC)")
    print("=" * 80)
    print()

    # Fetch data
    candles = fetch_edge_data()
    if not candles:
        print("❌ No data available")
        return

    # Find first ROC BUY signal (ROC > +2.0%)
    print("Searching for first ROC BUY signal (ROC > +2.0%)...")

    entry_candle = None
    for i in range(1, len(candles)):
        prev_close = candles[i-1]['close']
        curr_close = candles[i]['close']
        roc = ((curr_close - prev_close) / prev_close) * 100

        if roc > Decimal('2.0'):
            entry_candle = candles[i]
            print(f"✓ First ROC signal at {entry_candle['timestamp']} | ${entry_candle['close']:.6f} | ROC: {roc:.2f}%")
            break

    if not entry_candle:
        print("❌ No ROC signal found")
        return

    # Initialize simulator
    sim = PeakTrackingSimulator()
    sim.init_position(entry_candle['close'], entry_candle['timestamp'])

    # Run simulation on subsequent candles
    print(f"\nSimulating peak tracking strategy...")
    print(f"Entry: ${sim.entry_price:.6f} at {sim.entry_time}")
    print()

    exit_triggered = False
    exit_candle = None
    exit_reason = None

    # Find entry index
    entry_idx = candles.index(entry_candle)

    for i in range(entry_idx + 1, len(candles)):
        candle = candles[i]
        current_price = candle['close']

        should_exit, reason, smoothed = sim.check_exit(current_price, candle['timestamp'])

        if should_exit:
            exit_triggered = True
            exit_candle = candle
            exit_reason = reason
            break

    # Close position
    if exit_triggered:
        final_pnl, hold_time = sim.close_position(exit_candle['close'], exit_reason, exit_candle['timestamp'])
    else:
        # Position still open at end of data
        final_candle = candles[-1]
        final_pnl, hold_time = sim.close_position(final_candle['close'], "end_of_data", final_candle['timestamp'])

    # Print results
    print("=" * 80)
    print("SIMULATION RESULTS")
    print("=" * 80)
    print()
    print(f"Entry Price:      ${sim.entry_price:.6f}")
    print(f"Entry Time:       {sim.entry_time}")
    print(f"Peak Price:       ${sim.peak_price:.6f} (5-min SMA)")
    print(f"Exit Price:       ${exit_candle['close'] if exit_triggered else candles[-1]['close']:.6f}")
    print(f"Exit Time:        {exit_candle['timestamp'] if exit_triggered else candles[-1]['timestamp']}")
    print(f"Exit Reason:      {exit_reason if exit_triggered else 'end_of_data'}")
    print(f"Hold Time:        {hold_time:.0f} minutes ({hold_time/60:.1f} hours)")
    print(f"Final P&L:        {final_pnl:.2%} (fee-aware)")
    print()

    # Calculate peak gain
    peak_gain = ((sim.peak_price - sim.entry_price) / sim.entry_price)
    print(f"Peak Gain:        +{peak_gain:.2%} (from entry to peak)")

    # Calculate drawdown from peak to exit
    if exit_triggered:
        exit_price = exit_candle['close']
        smoothed_exit = Decimal(str(sum(sim.price_history) / len(sim.price_history)))
        dd_from_peak = ((smoothed_exit - sim.peak_price) / sim.peak_price)
        print(f"Exit Drawdown:    {dd_from_peak:.2%} (from peak)")

    print()
    print("=" * 80)
    print("KEY EVENTS")
    print("=" * 80)
    print()

    for event in sim.events:
        if event['event'] in ['ENTRY', 'BREAKEVEN_ACTIVATED', 'EXIT']:
            print(f"{event['time'].strftime('%H:%M:%S')} | {event['event']:20s} | "
                  f"${event['price']:.6f} | P&L: {event['pnl_pct']:>7.2%} | "
                  f"Peak: ${event['peak']:.6f}")
            print(f"         {event['detail']}")
            print()

    # Show peak updates (condensed)
    peak_updates = [e for e in sim.events if e['event'] == 'PEAK_UPDATE']
    if peak_updates:
        print(f"Peak Updates: {len(peak_updates)} times")
        print(f"  First: {peak_updates[0]['time'].strftime('%H:%M:%S')} → ${peak_updates[0]['peak']:.6f}")
        print(f"  Last:  {peak_updates[-1]['time'].strftime('%H:%M:%S')} → ${peak_updates[-1]['peak']:.6f}")
        print()

    print("=" * 80)
    print("COMPARISON: OLD vs NEW STRATEGY")
    print("=" * 80)
    print()

    # Old strategy: Fixed TP at +6% (gross) = +4.8% (net after fees)
    old_tp_gross = Decimal('0.06')
    old_tp_price = sim.entry_price * (Decimal('1') + old_tp_gross)
    old_tp_net = sim.calculate_pnl_pct(sim.entry_price, old_tp_price)

    print(f"OLD Strategy (Fixed TP):")
    print(f"  Exit at: ${old_tp_price:.6f} (+{old_tp_gross:.1%} gross)")
    print(f"  Net P&L: {old_tp_net:.2%}")
    print()

    print(f"NEW Strategy (Peak Tracking):")
    print(f"  Exit at: ${exit_candle['close'] if exit_triggered else candles[-1]['close']:.6f}")
    print(f"  Net P&L: {final_pnl:.2%}")
    print()

    improvement = final_pnl - old_tp_net
    print(f"Improvement: {improvement:+.2%} ({improvement/old_tp_net:.1%} relative gain)")
    print()

    # Calculate what would have happened if exited at absolute peak
    peak_exit_pnl = sim.calculate_pnl_pct(sim.entry_price, sim.peak_price)
    print(f"Theoretical Max (exit at peak ${sim.peak_price:.6f}): {peak_exit_pnl:.2%}")
    print(f"Capture Rate: {(final_pnl / peak_exit_pnl):.1%} of theoretical max")
    print()


if __name__ == "__main__":
    run_simulation()
