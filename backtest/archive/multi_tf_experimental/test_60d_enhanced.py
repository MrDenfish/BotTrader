"""
60-Day Enhanced 15m Strategy Test with Event Study
===================================================

Tests enhanced strategy with professional upgrades:
1. ATR-normalized ROC (roc_score = roc / atr_fast_pct)
2. 1h trend filter (1h close > 1h EMA(50))
3. Scale-out profit taking
4. Fee-aware stop arming

Plus event study to separate entry edge from exit edge.

Date: January 30, 2026
"""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List

from backtest.strategy_15m_enhanced import Strategy15mEnhanced, Enhanced15mConfig
from backtest.data_resampler import DataResampler
from backtest.indicators_roc_atr import calculate_roc, calculate_atr, calculate_volume_ratio


class Enhanced15mBacktestEngine:
    """
    Backtest engine for enhanced 15m strategy with event study.
    """

    def __init__(
        self,
        config: Enhanced15mConfig,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime
    ):
        self.config = config
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.data_dir = Path("backtest/data")

        # Data
        self.data_15m: Dict[str, pd.DataFrame] = {}
        self.data_1h: Dict[str, pd.DataFrame] = {}
        self.data_4h: Dict[str, pd.DataFrame] = {}
        self.indicators: Dict[str, pd.DataFrame] = {}

        # Strategy
        self.strategy = Strategy15mEnhanced(config)

        # Event study
        self.signal_events: List[Dict] = []

    def log(self, msg: str):
        print(msg)

    def load_data(self):
        """Load and resample data for all symbols."""
        self.log(f"Loading data for {len(self.symbols)} symbols...")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")

        for symbol in self.symbols:
            csv_file = f"{symbol.replace('-', '_')}.csv"
            csv_path = self.data_dir / csv_file

            if not csv_path.exists():
                self.log(f"  ⚠️  {symbol}: CSV not found")
                continue

            try:
                # Load 1m
                df_1m = pd.read_csv(csv_path, parse_dates=['time'])
                df_1m = df_1m[
                    (df_1m['time'] >= self.start_date) &
                    (df_1m['time'] <= self.end_date)
                ].reset_index(drop=True)

                if df_1m.empty:
                    continue

                # Resample
                df_15m = DataResampler.resample_ohlcv(df_1m, '15m')
                df_1h = DataResampler.resample_ohlcv(df_1m, '1h')
                df_4h = DataResampler.resample_ohlcv(df_1m, '4h')

                self.data_15m[symbol] = df_15m
                self.data_1h[symbol] = df_1h
                self.data_4h[symbol] = df_4h

                self.log(f"  {symbol}: {len(df_15m)} bars (15m)")

            except Exception as e:
                self.log(f"  ⚠️  {symbol}: Error - {e}")

        self.log(f"✅ Loaded {len(self.data_15m)} symbols\n")

    def calculate_indicators(self):
        """Calculate all indicators including 1h trend filter."""
        self.log("Calculating indicators...")

        for symbol in self.data_15m.keys():
            df_15m = self.data_15m[symbol]
            df_1h = self.data_1h[symbol]
            df_4h = self.data_4h[symbol]

            # 15m indicators
            roc = calculate_roc(df_15m['close'], self.config.roc_len, self.config.roc_ema_len)
            vol_ratio = calculate_volume_ratio(df_15m['volume'], self.config.vol_sma_len)

            atr_fast = calculate_atr(df_15m[['high', 'low', 'close']], self.config.atr_fast_len)
            atr_fast_pct = atr_fast / df_15m['close']

            # 4h ATR_slow
            atr_slow = calculate_atr(df_4h[['high', 'low', 'close']], self.config.atr_slow_len)
            atr_slow_pct = atr_slow / df_4h['close']

            # 1h trend: EMA(50)
            ema50_1h = df_1h['close'].ewm(span=self.config.trend_ema_len, adjust=False).mean()

            # Merge into 15m
            df_indicators = pd.DataFrame({
                'time': df_15m['time'],
                'roc': roc,
                'vol_ratio': vol_ratio,
                'atr_fast_pct': atr_fast_pct
            })

            # Merge 4h ATR_slow
            df_4h_subset = pd.DataFrame({
                'time': df_4h['time'],
                'atr_slow_pct': atr_slow_pct
            })

            df_indicators = pd.merge_asof(
                df_indicators.sort_values('time'),
                df_4h_subset.sort_values('time'),
                on='time',
                direction='backward'
            )

            # Merge 1h trend
            df_1h_subset = pd.DataFrame({
                'time': df_1h['time'],
                'close_1h': df_1h['close'],
                'ema50_1h': ema50_1h
            })

            df_indicators = pd.merge_asof(
                df_indicators.sort_values('time'),
                df_1h_subset.sort_values('time'),
                on='time',
                direction='backward'
            )

            self.indicators[symbol] = df_indicators

        self.log("✅ Indicators calculated\n")

    def run_backtest_with_event_study(self):
        """Run backtest and collect event study data."""
        self.log(f"Running backtest: {self.config.variant_name}")
        self.log("=" * 80)

        total_bars = sum(len(df) for df in self.data_15m.values())
        self.log(f"Total bars: {total_bars}")

        # Get all timestamps
        all_times = []
        for df in self.data_15m.values():
            all_times.extend(df['time'].tolist())
        unique_times = sorted(set(all_times))

        bars_processed = 0

        for timestamp in unique_times:
            for symbol in self.data_15m.keys():
                df_15m = self.data_15m[symbol]
                df_indicators = self.indicators[symbol]

                bar_idx = df_15m[df_15m['time'] == timestamp].index
                if len(bar_idx) == 0:
                    continue

                bar_idx = bar_idx[0]
                bar = df_15m.iloc[bar_idx]
                indicators = df_indicators.iloc[bar_idx]

                # Event study: check if this bar triggers a signal
                # (before processing, to capture ALL signals including those that don't fill)
                self._capture_signal_event(symbol, bar, indicators, bar_idx, df_15m)

                # Process bar
                self.strategy.process_bar(symbol, bar, indicators)

                bars_processed += 1

            if bars_processed % 1000 == 0:
                print(f"\r  Processed {bars_processed}/{total_bars} bars...", end='')

        print(f"\r  Processed {total_bars}/{total_bars} bars...")
        self.log("✅ Backtest complete\n")

    def _capture_signal_event(
        self,
        symbol: str,
        bar: pd.Series,
        indicators: pd.Series,
        bar_idx: int,
        df_15m: pd.DataFrame
    ):
        """
        Capture signal event for event study (even if it doesn't fill).
        """
        # Check if this would trigger a signal
        if symbol in self.strategy.positions or symbol in self.strategy.signals:
            return  # Already in position/signal

        # Check signal conditions
        roc = indicators.get('roc', 0.0)
        atr_fast_pct = indicators.get('atr_fast_pct', 0.0)
        vol_ratio = indicators.get('vol_ratio', 0.0)
        close_1h = indicators.get('close_1h', 0.0)
        ema_1h = indicators.get('ema50_1h', 0.0)

        if pd.isna(roc) or pd.isna(atr_fast_pct) or atr_fast_pct == 0:
            return

        roc_score = roc / atr_fast_pct

        # Check conditions
        roc_condition = roc_score >= self.config.roc_score_threshold
        vol_condition = vol_ratio >= self.config.vol_ratio_min

        trend_condition = True
        if self.config.trend_filter_enabled:
            if not pd.isna(close_1h) and not pd.isna(ema_1h):
                trend_condition = close_1h > ema_1h
            else:
                trend_condition = False

        if not (roc_condition and vol_condition and trend_condition):
            return

        # Signal triggered! Compute forward returns
        signal_price = bar['close']
        signal_time = bar['time']

        windows = {
            '15m': 1,
            '30m': 2,
            '1h': 4,
            '2h': 8,
            '4h': 16
        }

        forward_returns = {}
        mfe_values = {}

        for window_name, bars_ahead in windows.items():
            if bar_idx + bars_ahead >= len(df_15m):
                forward_returns[window_name] = np.nan
                mfe_values[window_name] = np.nan
                continue

            future_bars = df_15m.iloc[bar_idx+1:bar_idx+1+bars_ahead]
            end_price = df_15m.iloc[bar_idx + bars_ahead]['close']
            forward_return = (end_price - signal_price) / signal_price
            forward_returns[window_name] = forward_return

            max_high = future_bars['high'].max()
            mfe = (max_high - signal_price) / signal_price
            mfe_values[window_name] = mfe

        # Record event
        self.signal_events.append({
            'symbol': symbol,
            'time': signal_time,
            'signal_price': signal_price,
            'roc': roc,
            'roc_score': roc_score,
            'vol_ratio': vol_ratio,
            'atr_fast_pct': atr_fast_pct,
            'close_1h': close_1h,
            'ema50_1h': ema_1h,
            'trend_ok': trend_condition,
            'fwd_15m': forward_returns['15m'],
            'fwd_30m': forward_returns['30m'],
            'fwd_1h': forward_returns['1h'],
            'fwd_2h': forward_returns['2h'],
            'fwd_4h': forward_returns['4h'],
            'mfe_15m': mfe_values['15m'],
            'mfe_30m': mfe_values['30m'],
            'mfe_1h': mfe_values['1h'],
            'mfe_2h': mfe_values['2h'],
            'mfe_4h': mfe_values['4h']
        })

    def print_results(self):
        """Print backtest and event study results."""
        results = self.strategy.get_results_summary()

        self.log("=" * 80)
        self.log("BACKTEST RESULTS")
        self.log("=" * 80)
        self.log(f"Config: {self.config.variant_name}")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")
        self.log(f"Symbols: {len(self.symbols)}")
        self.log("")
        self.log(f"Signals: {results['total_signals']}")
        self.log(f"Entries: {results['total_entries']}")
        self.log(f"Signal→Entry: {results['signal_to_entry_pct']:.1f}%")
        self.log(f"Expired: {results['signals_expired']}")
        self.log("")
        self.log(f"Trades: {results['total_trades']}")
        self.log(f"P&L: ${results['total_pnl']:.2f}")
        self.log(f"Win Rate: {results['win_rate']*100:.1f}%")
        self.log(f"Profit Factor: {results['profit_factor']:.2f}")
        self.log(f"Avg Win: ${results['avg_win']:.2f}")
        self.log(f"Avg Loss: ${results['avg_loss']:.2f}")
        self.log(f"Avg Hold: {results['avg_bars_held']:.1f} bars")
        self.log("")
        self.log(f"Scale-outs: {results['scale_outs']} ({results['scale_out_rate']:.1f}%)")
        self.log(f"Gross P&L: ${results['gross_pnl']:.2f}")
        self.log(f"Fees: ${results['total_fees']:.2f}")
        self.log("=" * 80)

        # Event study
        if self.signal_events:
            self.log("\n" + "=" * 80)
            self.log("EVENT STUDY")
            self.log("=" * 80)

            df_events = pd.DataFrame(self.signal_events)
            self.log(f"\nTotal Signals: {len(df_events)}")

            self.log("\nForward Returns:")
            self.log(f"{'Window':<10} {'Mean %':<10} {'Median %':<10} {'Win %':<10}")
            self.log("-" * 40)

            for window in ['15m', '30m', '1h', '2h', '4h']:
                col = f'fwd_{window}'
                returns = df_events[col].dropna()
                if len(returns) == 0:
                    continue
                mean_ret = returns.mean() * 100
                median_ret = returns.median() * 100
                win_rate = (returns > 0).sum() / len(returns) * 100
                self.log(f"{window:<10} {mean_ret:>9.2f} {median_ret:>9.2f} {win_rate:>9.1f}")

            self.log("\nMFE (Max Favorable Excursion):")
            self.log(f"{'Window':<10} {'Mean MFE %':<12} {'% > 0.6%':<10} {'% > 1.8%':<10}")
            self.log("-" * 42)

            for window in ['15m', '30m', '1h', '2h', '4h']:
                col = f'mfe_{window}'
                mfe = df_events[col].dropna()
                if len(mfe) == 0:
                    continue
                mean_mfe = mfe.mean() * 100
                pct_above_60bp = (mfe > 0.006).sum() / len(mfe) * 100
                pct_above_180bp = (mfe > 0.018).sum() / len(mfe) * 100
                self.log(f"{window:<10} {mean_mfe:>11.2f} {pct_above_60bp:>9.1f} {pct_above_180bp:>9.1f}")

            self.log("=" * 80)

            # Export events
            event_file = f"event_study_{self.config.variant_name}_{self.start_date.date()}_to_{self.end_date.date()}.csv"
            df_events.to_csv(event_file, index=False)
            self.log(f"\n📊 Event study exported: {event_file}")

        return results


def run_60day_enhanced_test():
    """Run 60-day enhanced strategy test."""
    print("=" * 80)
    print("60-DAY ENHANCED 15M STRATEGY TEST")
    print("=" * 80)
    print("\nEnhancements:")
    print("  1. ATR-normalized ROC (roc_score = roc / atr_fast_pct)")
    print("  2. 1h trend filter (1h close > 1h EMA(50))")
    print("  3. Scale-out profit taking (50% at +1.0× ATR)")
    print("  4. Fee-aware stop arming (only after profit > 2.5× fees)")
    print("\n" + "=" * 80 + "\n")

    # 60 days
    end_date = datetime(2026, 1, 29)
    start_date = end_date - timedelta(days=60)

    # Expanded symbols
    symbols = [
        'BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD', 'ADA-USD',
        'DOGE-USD', 'MATIC-USD', 'LINK-USD', 'DOT-USD', 'AVAX-USD'
    ]

    # Enhanced config
    config = Enhanced15mConfig(
        variant_name="15M_ENH_60D",

        # ATR-normalized ROC
        roc_len=9,
        roc_ema_len=5,
        roc_score_threshold=1.0,  # ROC ≥ 1.0× ATR_fast%

        # Volume
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # Trend filter
        trend_filter_enabled=True,
        trend_ema_len=50,

        # ATR
        atr_fast_len=14,
        M_fast=2.0,
        atr_slow_len=14,
        M_slow=3.0,

        # ROC exit
        X_dd=0.40,
        roc_arm_threshold_score=0.5,

        # Scale-out
        scale_out_enabled=True,
        scale_out_pct=0.5,
        profit_target_k=1.0,

        # Fee-aware arming
        fast_trail_activation_mode="fee_multiple",
        fast_trail_fee_multiple=2.5,

        # Entry
        entry_mode='continuation',
        entry_max_bars_to_wait=10,
        min_bars_after_entry_for_exit=2,

        # Sizing
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    # Run
    engine = Enhanced15mBacktestEngine(
        config=config,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date
    )

    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest_with_event_study()
    results = engine.print_results()

    return results


if __name__ == "__main__":
    run_60day_enhanced_test()
