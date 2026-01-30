"""
Event Study Analysis for 15m Continuation Strategy
===================================================

For EVERY signal (not just filled trades), analyze forward returns to identify:
1. Does the entry signal predict favorable price movement?
2. What's the optimal holding period?
3. Are we exiting too early or too late?

Forward return windows: +15m, +30m, +1h, +2h, +4h
Also tracks MFE (max favorable excursion) in each window

This separates "entry edge" from "exit edge" to understand if:
- Entry has no predictive power → need better entry filter
- Entry is good but exits are bad → need better exit logic
- Both are weak → strategy concept may not work in this regime

Date: January 30, 2026
"""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple

from backtest.engine_multi_tf import BacktestEngineMultiTF
from backtest.strategy_multi_tf import MultiTFConfig
from backtest.data_resampler import DataResampler


class EventStudyEngine:
    """
    Extends backtest to track forward returns for all signals.
    """

    def __init__(
        self,
        config: MultiTFConfig,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime,
        data_dir: Path = Path("backtest/data")
    ):
        self.config = config
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.data_dir = data_dir

        # Data storage
        self.data_signal_tf: Dict[str, pd.DataFrame] = {}
        self.indicators: Dict[str, pd.DataFrame] = {}

        # Event study results
        self.signal_events: List[Dict] = []

    def load_and_prepare_data(self):
        """Load data and calculate indicators for all symbols."""
        print(f"Loading data for {len(self.symbols)} symbols...")
        print(f"Signal TF: {self.config.signal_timeframe}")
        print(f"Period: {self.start_date.date()} to {self.end_date.date()}")

        for symbol in self.symbols:
            csv_filename = f"{symbol.replace('-', '_')}.csv"
            csv_path = self.data_dir / csv_filename

            if not csv_path.exists():
                print(f"⚠️  {symbol}: CSV not found")
                continue

            try:
                # Load and resample to 15m
                df_1m = pd.read_csv(csv_path, parse_dates=['time'])
                df_1m = df_1m[
                    (df_1m['time'] >= self.start_date) &
                    (df_1m['time'] <= self.end_date)
                ].reset_index(drop=True)

                if df_1m.empty:
                    continue

                df_15m = DataResampler.resample_ohlcv(df_1m, '15m')
                df_1h = DataResampler.resample_ohlcv(df_1m, '1h')
                df_4h = DataResampler.resample_ohlcv(df_1m, '4h')

                self.data_signal_tf[symbol] = df_15m

                # Calculate indicators
                from backtest.indicators_roc_atr import calculate_roc, calculate_atr, calculate_volume_ratio

                roc = calculate_roc(
                    df_15m['close'],
                    length=self.config.roc_len,
                    ema_len=self.config.roc_ema_len
                )

                vol_ratio = calculate_volume_ratio(
                    df_15m['volume'],
                    sma_len=self.config.vol_sma_len
                )

                atr_fast = calculate_atr(
                    df_15m[['high', 'low', 'close']],
                    atr_len=self.config.atr_fast_len
                )
                atr_fast_pct = atr_fast / df_15m['close']

                atr_slow = calculate_atr(
                    df_4h[['high', 'low', 'close']],
                    atr_len=self.config.atr_slow_len
                )
                atr_slow_pct = atr_slow / df_4h['close']

                df_slow = pd.DataFrame({
                    'time': df_4h['time'],
                    'atr_slow_pct': atr_slow_pct
                })

                df_indicators = pd.DataFrame({
                    'time': df_15m['time'],
                    'roc': roc,
                    'vol_ratio': vol_ratio,
                    'atr_fast_pct': atr_fast_pct
                })

                df_indicators = pd.merge_asof(
                    df_indicators.sort_values('time'),
                    df_slow.sort_values('time'),
                    on='time',
                    direction='backward'
                )

                self.indicators[symbol] = df_indicators

                print(f"  {symbol}: {len(df_15m)} bars")

            except Exception as e:
                print(f"⚠️  {symbol}: Error - {e}")
                continue

        print(f"✅ Loaded {len(self.data_signal_tf)} symbols\n")

    def run_event_study(self):
        """
        Scan for all entry signals and analyze forward returns.
        """
        print("Running event study...")
        print("Analyzing forward returns: +15m, +30m, +1h, +2h, +4h")
        print("=" * 80)

        total_signals = 0

        for symbol in self.data_signal_tf.keys():
            df = self.data_signal_tf[symbol]
            indicators = self.indicators[symbol]

            # Scan for signals
            for i in range(len(df) - 20):  # Leave room for forward returns
                bar = df.iloc[i]
                ind = indicators.iloc[i]

                # Check entry signal
                roc = ind.get('roc', 0.0)
                vol_ratio = ind.get('vol_ratio', 0.0)

                if pd.isna(roc) or pd.isna(vol_ratio):
                    continue

                entry_signal = (
                    roc >= self.config.roc_entry_threshold and
                    vol_ratio >= self.config.vol_ratio_min
                )

                if not entry_signal:
                    continue

                # Signal triggered! Compute forward returns
                signal_price = bar['close']
                signal_time = bar['time']

                # Forward return windows (in 15m bars)
                windows = {
                    '15m': 1,   # 1 bar = 15 minutes
                    '30m': 2,   # 2 bars = 30 minutes
                    '1h': 4,    # 4 bars = 1 hour
                    '2h': 8,    # 8 bars = 2 hours
                    '4h': 16    # 16 bars = 4 hours
                }

                forward_returns = {}
                mfe_values = {}  # Max favorable excursion

                for window_name, bars_ahead in windows.items():
                    if i + bars_ahead >= len(df):
                        forward_returns[window_name] = np.nan
                        mfe_values[window_name] = np.nan
                        continue

                    # Get future bars for this window
                    future_bars = df.iloc[i+1:i+1+bars_ahead]

                    # Forward return = close at end of window
                    end_price = df.iloc[i + bars_ahead]['close']
                    forward_return = (end_price - signal_price) / signal_price
                    forward_returns[window_name] = forward_return

                    # MFE = max high within window
                    max_high = future_bars['high'].max()
                    mfe = (max_high - signal_price) / signal_price
                    mfe_values[window_name] = mfe

                # Record event
                event = {
                    'symbol': symbol,
                    'time': signal_time,
                    'signal_price': signal_price,
                    'roc': roc,
                    'vol_ratio': vol_ratio,
                    'atr_fast_pct': ind['atr_fast_pct'],
                    'atr_slow_pct': ind['atr_slow_pct'],
                    # Forward returns
                    'fwd_15m': forward_returns['15m'],
                    'fwd_30m': forward_returns['30m'],
                    'fwd_1h': forward_returns['1h'],
                    'fwd_2h': forward_returns['2h'],
                    'fwd_4h': forward_returns['4h'],
                    # MFE (max favorable excursion)
                    'mfe_15m': mfe_values['15m'],
                    'mfe_30m': mfe_values['30m'],
                    'mfe_1h': mfe_values['1h'],
                    'mfe_2h': mfe_values['2h'],
                    'mfe_4h': mfe_values['4h']
                }

                self.signal_events.append(event)
                total_signals += 1

        print(f"✅ Found {total_signals} signals across {len(self.data_signal_tf)} symbols")
        print()

    def analyze_results(self):
        """
        Analyze signal events to determine entry edge.
        """
        if not self.signal_events:
            print("No signals found!")
            return

        df = pd.DataFrame(self.signal_events)

        print("=" * 80)
        print("EVENT STUDY RESULTS")
        print("=" * 80)
        print(f"\nTotal Signals: {len(df)}")
        print(f"Symbols: {df['symbol'].nunique()}")
        print(f"Period: {df['time'].min().date()} to {df['time'].max().date()}")
        print()

        # Forward return statistics
        print("-" * 80)
        print("FORWARD RETURNS (from signal bar close)")
        print("-" * 80)
        print(f"{'Window':<10} {'Mean %':<10} {'Median %':<10} {'Win %':<10} "
              f"{'Mean Win %':<12} {'Mean Loss %':<12} {'Best %':<10} {'Worst %':<10}")
        print("-" * 80)

        for window in ['15m', '30m', '1h', '2h', '4h']:
            col = f'fwd_{window}'
            returns = df[col].dropna()

            if len(returns) == 0:
                continue

            mean_ret = returns.mean() * 100
            median_ret = returns.median() * 100
            win_rate = (returns > 0).sum() / len(returns) * 100

            wins = returns[returns > 0]
            losses = returns[returns <= 0]

            mean_win = wins.mean() * 100 if len(wins) > 0 else 0.0
            mean_loss = losses.mean() * 100 if len(losses) > 0 else 0.0

            best = returns.max() * 100
            worst = returns.min() * 100

            print(f"{window:<10} {mean_ret:>9.2f} {median_ret:>9.2f} {win_rate:>9.1f} "
                  f"{mean_win:>11.2f} {mean_loss:>11.2f} {best:>9.2f} {worst:>9.2f}")

        print()

        # MFE statistics
        print("-" * 80)
        print("MAX FAVORABLE EXCURSION (highest point reached in each window)")
        print("-" * 80)
        print(f"{'Window':<10} {'Mean MFE %':<12} {'Median MFE %':<12} "
              f"{'% MFE > 0.5%':<15} {'% MFE > 1.0%':<15} {'% MFE > 2.0%':<15}")
        print("-" * 80)

        for window in ['15m', '30m', '1h', '2h', '4h']:
            col = f'mfe_{window}'
            mfe = df[col].dropna()

            if len(mfe) == 0:
                continue

            mean_mfe = mfe.mean() * 100
            median_mfe = mfe.median() * 100
            pct_above_50bp = (mfe > 0.005).sum() / len(mfe) * 100
            pct_above_100bp = (mfe > 0.010).sum() / len(mfe) * 100
            pct_above_200bp = (mfe > 0.020).sum() / len(mfe) * 100

            print(f"{window:<10} {mean_mfe:>11.2f} {median_mfe:>11.2f} "
                  f"{pct_above_50bp:>14.1f} {pct_above_100bp:>14.1f} {pct_above_200bp:>14.1f}")

        print()

        # Key diagnostic metrics
        print("=" * 80)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 80)

        # Check if entry has edge
        fwd_1h = df['fwd_1h'].dropna()
        mean_1h = fwd_1h.mean() * 100
        win_rate_1h = (fwd_1h > 0).sum() / len(fwd_1h) * 100 if len(fwd_1h) > 0 else 0

        print(f"\n1-Hour Forward Return:")
        print(f"  Mean: {mean_1h:.2f}%")
        print(f"  Win Rate: {win_rate_1h:.1f}%")

        if mean_1h > 0.3:
            print("  ✅ POSITIVE ENTRY EDGE detected")
        elif mean_1h > 0:
            print("  ⚠️  Weak positive edge (may not survive fees)")
        else:
            print("  ❌ NO ENTRY EDGE - signals are not predictive")

        # Check fee hurdle
        print(f"\nFee Hurdle Analysis (assuming 0.6% round-trip):")
        mfe_1h = df['mfe_1h'].dropna()
        pct_clear_fees = (mfe_1h > 0.006).sum() / len(mfe_1h) * 100 if len(mfe_1h) > 0 else 0
        pct_3x_fees = (mfe_1h > 0.018).sum() / len(mfe_1h) * 100 if len(mfe_1h) > 0 else 0

        print(f"  % signals with MFE > 0.6% (1× fees): {pct_clear_fees:.1f}%")
        print(f"  % signals with MFE > 1.8% (3× fees): {pct_3x_fees:.1f}%")

        if pct_3x_fees > 30:
            print("  ✅ GOOD: Many signals achieve 3× fee hurdle")
        elif pct_clear_fees > 50:
            print("  ⚠️  MARGINAL: Signals clear fees but tight margins")
        else:
            print("  ❌ POOR: Most signals don't even clear fee hurdle")

        # Optimal holding period
        print(f"\nOptimal Holding Period:")
        mean_returns = {
            '15m': df['fwd_15m'].mean() * 100,
            '30m': df['fwd_30m'].mean() * 100,
            '1h': df['fwd_1h'].mean() * 100,
            '2h': df['fwd_2h'].mean() * 100,
            '4h': df['fwd_4h'].mean() * 100
        }
        best_window = max(mean_returns, key=mean_returns.get)
        print(f"  Best mean return at: {best_window} ({mean_returns[best_window]:.2f}%)")

        print("\n" + "=" * 80)

        # Export to CSV for detailed analysis
        output_file = f"event_study_15m_{self.start_date.date()}_to_{self.end_date.date()}.csv"
        df.to_csv(output_file, index=False)
        print(f"\n📊 Detailed results exported to: {output_file}")

        return df


def run_60day_event_study():
    """
    Run 60-day event study on 15m continuation strategy.
    """
    print("=" * 80)
    print("60-DAY EVENT STUDY: 15m CONTINUATION STRATEGY")
    print("=" * 80)
    print("\nGoal: Determine if entry signals have predictive power")
    print("Method: Analyze forward returns for ALL signals (not just filled trades)")
    print()

    # 60-day period
    end_date = datetime(2026, 1, 29)
    start_date = end_date - timedelta(days=60)

    # Expanded symbol list: majors + volatile coins
    symbols = [
        # Majors
        'BTC-USD', 'ETH-USD',
        # Large caps
        'SOL-USD', 'XRP-USD', 'ADA-USD',
        # Mid/volatile
        'DOGE-USD', 'MATIC-USD', 'LINK-USD', 'DOT-USD', 'AVAX-USD'
    ]

    # Configuration: 15m + continuation (best from 7-day test)
    config = MultiTFConfig(
        variant_name="15M_CONT_60D",
        signal_timeframe='15m',
        entry_mode='continuation',

        # ROC parameters
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.012,  # 1.2%
        roc_arm_threshold=0.006,

        # Volume filter
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # ATR
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,
        tf_slow='4h',
        atr_slow_len=14,
        M_slow=3.0,

        # Exit
        X_dd=0.40,

        # Entry
        entry_max_bars_to_wait=10,
        min_bars_after_entry_for_exit=2,

        # Sizing
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    # Run event study
    engine = EventStudyEngine(
        config=config,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date
    )

    engine.load_and_prepare_data()
    engine.run_event_study()
    results = engine.analyze_results()

    return results


if __name__ == "__main__":
    run_60day_event_study()
