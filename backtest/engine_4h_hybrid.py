"""
Backtest Engine for 4h Hybrid Maker Strategy

Loads 1m data, resamples to 4h and 1D, calculates indicators, and runs the strategy.

Key features:
- Conservative post-only fill simulation
- Proper indicator alignment (4h updates only on 4h closes)
- Comprehensive diagnostics and logging
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config_4h_hybrid import Hybrid4hConfig
from strategy_4h_hybrid import Hybrid4hStrategy
from data_resampler import DataResampler


class Hybrid4hBacktestEngine:
    """Backtest engine for 4h hybrid strategy"""

    def __init__(self, config: Hybrid4hConfig, data_dir: str = "backtest/data", resolution: str = '1m'):
        """
        Initialize backtest engine

        Args:
            config: Strategy configuration
            data_dir: Directory containing CSV data files
            resolution: Base data resolution ('1m' or '5m', default: '1m')
        """
        self.config = config
        self.data_dir = Path(data_dir)
        self.resolution = resolution
        self.strategy = Hybrid4hStrategy(config)

        # Phase 2.3.2: Store actual 4h/1d timestamps for accurate boundary detection
        self._4h_timestamps = set()
        self._1d_timestamps = set()

    def load_data(self, symbol: str, days: int = 60, resolution: str = None) -> Optional[pd.DataFrame]:
        """
        Load OHLCV data at specified resolution

        Args:
            symbol: Trading pair (e.g., 'BTC-USD')
            days: Number of days to load
            resolution: '1m' or '5m' (default: use engine's resolution)

        Returns:
            DataFrame with OHLCV data at specified resolution
        """

        if resolution is None:
            resolution = self.resolution

        # Try multiple naming conventions
        symbol_underscore = symbol.replace('-', '_')
        csv_paths = [
            self.data_dir / f"{symbol_underscore}_{resolution}.csv",  # BTC_USD_5m.csv
            self.data_dir / f"{symbol}_{resolution}.csv",             # BTC-USD_5m.csv
            self.data_dir / f"{symbol_underscore}.csv",               # BTC_USD.csv (fallback for 1m)
        ]

        csv_path = None
        for path in csv_paths:
            if path.exists():
                csv_path = path
                break

        if csv_path is None:
            print(f"⚠️  {symbol}: CSV not found (tried {csv_paths})")
            return None

        df = pd.read_csv(csv_path)

        # Parse timestamp (column may be 'time' or 'timestamp')
        time_col = 'time' if 'time' in df.columns else 'timestamp'
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col)
        df = df.sort_index()
        df.index.name = 'timestamp'  # Normalize name

        # Filter to last N days
        end_date = df.index[-1]
        start_date = end_date - timedelta(days=days)
        df = df[df.index >= start_date]

        print(f"  {symbol}: {len(df)} bars ({resolution})")

        return df

    def load_1m_data(self, symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
        """Legacy method for backward compatibility - loads 1m data"""
        return self.load_data(symbol, days, resolution='1m')

    def calculate_indicators(
        self,
        df_base: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Resample to 4h and 1D, calculate indicators.

        Args:
            df_base: Base timeframe data (1m or 5m)

        Returns:
            (df_4h, df_1d, df_aligned) where df_aligned has 4h/1D values merged into base timeframe
        """

        # Resample to 4h (works from both 1m and 5m base)
        df_4h = DataResampler.resample_ohlcv(df_base, '4h', label='right')

        # Resample to 1d
        df_1d = DataResampler.resample_ohlcv(df_base, '1d', label='right')

        # Phase 2.3.2: Store actual 4h/1d timestamps for accurate boundary detection
        self._4h_timestamps = set(df_4h.index)
        self._1d_timestamps = set(df_1d.index)

        # Calculate 4h indicators
        df_4h = self._calculate_4h_indicators(df_4h)

        # Calculate 1D indicators
        df_1d = self._calculate_1d_indicators(df_1d)

        # Align: merge 4h and 1D values into base timeframe bars (forward fill)
        df_aligned = self._align_indicators(df_base, df_4h, df_1d)

        return df_4h, df_1d, df_aligned

    def _calculate_4h_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate 4h indicators: ATR%, Donchian, Bollinger Bands (Phase 2), ROC-score (Phase 2.3)"""

        # ATR
        atr_len = self.config.atr_len_4h
        df['tr'] = self._calculate_true_range(df)
        df['atr'] = df['tr'].ewm(span=atr_len, adjust=False).mean()
        df['atr_pct'] = df['atr'] / df['close']

        # Donchian (exclude current bar)
        donch_len = self.config.donch_len
        df['donch_high_prev'] = df['high'].shift(1).rolling(donch_len).max()

        # Phase 2: Bollinger Bands + Bandwidth
        bb_len = self.config.bb_len
        bb_k = self.config.bb_k

        df['bb_mid'] = df['close'].rolling(bb_len).mean()
        df['bb_std'] = df['close'].rolling(bb_len).std()
        df['bb_upper'] = df['bb_mid'] + bb_k * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - bb_k * df['bb_std']

        # Bandwidth (normalized by mid)
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

        # Bandwidth percentile (compression score)
        bb_width_window = self.config.bb_width_window
        df['bb_width_pct'] = df['bb_width'].rolling(bb_width_window).apply(
            lambda x: (x <= x.iloc[-1]).sum() / len(x) * 100 if len(x) > 0 else 0,
            raw=False
        )

        # Phase 2.3: ROC-score (normalized momentum)
        # ROC: Rate of Change over N bars
        roc_len = self.config.roc_len_4h
        roc_raw = df['close'].pct_change(roc_len)

        # Smooth with EMA if configured
        if self.config.roc_ema_len > 1:
            df['roc_4h'] = roc_raw.ewm(span=self.config.roc_ema_len, adjust=False).mean()
        else:
            df['roc_4h'] = roc_raw

        # ROC-score: ROC / ATR% (momentum normalized by volatility)
        df['roc_score_4h'] = df['roc_4h'] / df['atr_pct'].replace(0, np.nan)

        return df

    def _calculate_1d_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate 1D indicators: EMA200, EMA50"""

        ema200_len = self.config.ema200_len_1d
        ema50_len = self.config.ema50_len_1d

        df['ema200'] = df['close'].ewm(span=ema200_len, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=ema50_len, adjust=False).mean()

        # Phase 2.3.2: Calculate EMA slopes (always, for diagnostics)
        lookback = self.config.ema_slope_lookback_days
        df['ema200_slope'] = df['ema200'] - df['ema200'].shift(lookback)
        df['ema50_slope'] = df['ema50'] - df['ema50'].shift(lookback)

        return df

    def _calculate_true_range(self, df: pd.DataFrame) -> pd.Series:
        """Calculate true range"""
        high_low = df['high'] - df['low']
        high_prev_close = abs(df['high'] - df['close'].shift(1))
        low_prev_close = abs(df['low'] - df['close'].shift(1))

        tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        return tr

    def _align_indicators(
        self,
        df_base: pd.DataFrame,
        df_4h: pd.DataFrame,
        df_1d: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Merge 4h and 1D indicator values into base timeframe bars (forward fill).

        Args:
            df_base: Base timeframe data (1m or 5m)
            df_4h: 4h bars with indicators
            df_1d: 1D bars with indicators

        Returns:
            df_base with additional columns:
            - atr_pct_4h, donch_high_prev_4h, bb_width_pct_4h, roc_score_4h (from 4h)
            - ema200_1d, ema50_1d (from 1D)
        """

        # Select indicator columns (Phase 2: add bb_width_pct; Phase 2.1: add bb_width; Phase 2.3: add roc_score)
        df_4h_ind = df_4h[['atr_pct', 'donch_high_prev', 'bb_width_pct', 'bb_width', 'roc_score_4h']].copy()
        df_4h_ind.columns = ['atr_pct_4h', 'donch_high_prev_4h', 'bb_width_pct_4h', 'bb_width_4h', 'roc_score_4h']

        df_1d_ind = df_1d[['ema200', 'ema50', 'ema200_slope', 'ema50_slope']].copy()
        df_1d_ind.columns = ['ema200_1d', 'ema50_1d', 'ema200_slope_1d', 'ema50_slope_1d']

        # Merge using merge_asof (forward fill)
        df_aligned = df_base.copy()
        df_aligned = pd.merge_asof(df_aligned, df_4h_ind, left_index=True, right_index=True, direction='backward')
        df_aligned = pd.merge_asof(df_aligned, df_1d_ind, left_index=True, right_index=True, direction='backward')

        return df_aligned

    def run_backtest(self, symbol: str, days: int = 60) -> dict:
        """
        Run backtest for a symbol using configured resolution.

        Returns:
            Statistics dictionary
        """

        print(f"\nBacktesting {symbol} ({self.resolution} base data)...")

        # Load data at configured resolution
        df_base = self.load_data(symbol, days)
        if df_base is None:
            return {"error": "Data not found"}

        # DATA VALIDATION: Print timestamp window and bar counts
        first_ts = df_base.index[0]
        last_ts = df_base.index[-1]
        print(f"  Data window: {first_ts} to {last_ts} ({len(df_base)} {self.resolution} bars)")

        # Calculate indicators (works from both 1m and 5m base)
        df_4h, df_1d, df_aligned = self.calculate_indicators(df_base)

        # Calculate 4h bars PRE-warmup (from raw data)
        df_4h_raw = DataResampler.resample_ohlcv(df_base, '4h', label='right')
        bars_4h_pre_warmup = len(df_4h_raw)
        bars_4h_post_warmup = len(df_4h)
        bars_dropped = bars_4h_pre_warmup - bars_4h_post_warmup

        print(f"  4h bars: {bars_4h_pre_warmup} pre-warmup → {bars_4h_post_warmup} post-warmup ({bars_dropped} dropped)")
        print(f"  1D bars: {len(df_1d)} post-warmup")

        # VALIDATION: Check expected bar counts
        expected_4h_bars_180d = 1080
        expected_4h_bars_365d = 2190

        if days >= 350:
            expected = expected_4h_bars_365d
            tolerance = 0.05  # 5% tolerance
        elif days >= 170:
            expected = expected_4h_bars_180d
            tolerance = 0.05
        else:
            expected = int((days / 180.0) * expected_4h_bars_180d)
            tolerance = 0.10  # Wider tolerance for shorter periods

        deviation = abs(bars_4h_post_warmup - expected) / expected

        if deviation > tolerance:
            print(f"  ⚠️  WARNING: 4h bar count {bars_4h_post_warmup} vs expected ~{expected} (deviation: {deviation:.1%})")
        else:
            print(f"  ✅ 4h bar count validation: {bars_4h_post_warmup} vs expected ~{expected} (within tolerance)")

        # Run strategy bar-by-bar
        total_bars = len(df_aligned)
        print(f"  Processing {total_bars} {self.resolution} bars...")

        for idx, (ts, bar) in enumerate(df_aligned.iterrows()):
            # Check if this is a 4h or 1D boundary
            is_4h_close = self._is_timeframe_boundary(ts, '4h')
            is_1d_close = self._is_timeframe_boundary(ts, '1D')

            # Get indicators
            indicators_4h = {
                'atr_pct': bar.get('atr_pct_4h', 0),
                'donch_high_prev': bar.get('donch_high_prev_4h', 0),
                'bb_width_pct': bar.get('bb_width_pct_4h', 0),  # Phase 2
                'bb_width': bar.get('bb_width_4h', 0),  # Phase 2.1 (raw width for expansion check)
                'roc_score_4h': bar.get('roc_score_4h', 0)  # Phase 2.3 (ROC-score setup)
            }

            indicators_1d = {
                'ema200': bar.get('ema200_1d', 0),
                'ema50': bar.get('ema50_1d', 0),
                'ema200_slope': bar.get('ema200_slope_1d', 0),
                'ema50_slope': bar.get('ema50_slope_1d', 0)
            }

            # Process bar
            self.strategy.process_bar_1m(
                symbol=symbol,
                bar=bar,
                indicators_4h=indicators_4h,
                indicators_1d=indicators_1d,
                is_4h_close=is_4h_close,
                is_1d_close=is_1d_close
            )

            # Progress indicator
            if (idx + 1) % 10000 == 0:
                print(f"    Processed {idx + 1}/{total_bars} bars...")

        print(f"  ✅ Backtest complete")

        # Get statistics
        stats = self.strategy.get_statistics()

        return stats

    def _is_timeframe_boundary(self, ts: pd.Timestamp, timeframe: str) -> bool:
        """
        Check if timestamp is at a timeframe boundary.

        Phase 2.3.2: Uses actual resampled bar timestamps instead of time arithmetic
        to handle data that doesn't align with standard boundaries (e.g., :08 minutes).
        """

        if timeframe == '4h':
            # Check if this timestamp is in the set of actual 4h bar timestamps
            return ts in self._4h_timestamps

        elif timeframe == '1D':
            # Check if this timestamp is in the set of actual 1d bar timestamps
            return ts in self._1d_timestamps

        return False

    def print_results(self, stats: dict, symbol: str):
        """Print backtest results"""

        print("\n" + "=" * 80)
        print(f"BACKTEST RESULTS: {symbol}")
        print("=" * 80)
        print(f"Config: {self.config}")
        print()

        if "error" in stats:
            print(f"❌ Error: {stats['error']}")
            print("=" * 80)
            return

        print(f"Signals: {stats['signals']}")
        print(f"Entries: {stats['entries']}")
        print(f"Signal→Entry: {stats.get('signal_to_entry_pct', 0):.1f}%")
        print(f"Expired: {stats['expired']}")
        print()

        print(f"Trades: {stats['trades']}")
        print(f"Gross P&L: ${stats.get('gross_pnl', 0):.2f}")
        print(f"Fees: ${stats.get('fees', 0):.2f}")
        print(f"Net P&L: ${stats.get('net_pnl', 0):.2f}")
        print()

        if stats['trades'] > 0:
            print(f"Win Rate: {stats.get('win_rate', 0):.1%}")
            print(f"Profit Factor: {stats.get('profit_factor', 0):.2f}")
            print(f"Avg Win: ${stats.get('avg_win', 0):.2f}")
            print(f"Avg Loss: ${stats.get('avg_loss', 0):.2f}")
            print()

            print(f"TP1 Hit: {stats.get('tp1_hit_pct', 0):.1f}%")
            print(f"TP2 Hit: {stats.get('tp2_hit_pct', 0):.1f}%")
            print(f"Avg Hold: {stats.get('avg_bars_held', 0):.1f} bars (1m)")

        print("=" * 80)

        # Phase 2.3: Print enhanced diagnostics if available
        if 'gate_audit' in stats and stats['gate_audit'].get('total_4h_bars', 0) > 0:
            print(self.strategy.print_enhanced_diagnostics())

    def export_trades(self, filename: str = "trades_4h_hybrid.csv"):
        """Export trades to CSV"""

        if not self.strategy.trades:
            print("No trades to export")
            return

        trades_data = []
        for t in self.strategy.trades:
            trades_data.append({
                'symbol': t.symbol,
                'entry_time': t.entry_time,
                'entry_price': t.entry_price,
                'exit_time': t.exit_time,
                'qty': t.qty_total,
                'gross_pnl': t.gross_pnl,
                'fees': t.fees_total,
                'net_pnl': t.net_pnl,
                'win': t.net_pnl > 0,
                'stop_hit': t.stop_hit,
                'tp1_filled': t.tp1_filled,
                'tp2_filled': t.tp2_filled,
                'runner_exit': t.runner_exit_reason,
                'mfe_pct': t.mfe_pct * 100,
                'mae_pct': t.mae_pct * 100,
                'bars_held': t.bars_held,
                'setup_type': t.setup_type,
                'entry_mode': t.entry_mode,
                # Phase 2: Entry diagnostics
                'entry_type': t.entry_type,
                'entry_wait_minutes': t.entry_wait_minutes
            })

        df_trades = pd.DataFrame(trades_data)
        df_trades.to_csv(filename, index=False)
        print(f"📊 Trades exported: {filename}")


if __name__ == "__main__":
    # Quick test
    from config_4h_hybrid import get_default_config

    config = get_default_config()
    engine = Hybrid4hBacktestEngine(config)

    # Test with BTC
    stats = engine.run_backtest("BTC-USD", days=60)
    engine.print_results(stats, "BTC-USD")
    engine.export_trades("trades_4h_hybrid_btc_test.csv")
