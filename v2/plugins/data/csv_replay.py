"""CSV replay data provider for backtesting.

Loads CSV OHLCV data, resamples to higher timeframes, computes indicators,
and emits CandleEvents bar-by-bar. Ported from strategies/engines/data_providers.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import DataProvider
from v2.core.types import Candle, CandleEvent

logger = logging.getLogger(__name__)


@registry.plugin("data", "csv_replay")
class CsvReplayProvider(DataProvider):
    """Replays historical CSV data as CandleEvents.

    This provider is the backtest equivalent of a live websocket feed.
    It loads CSV files, computes indicators at 4h and 1D timeframes,
    aligns them into the base timeframe, and emits one CandleEvent
    per bar with pre-computed indicators attached as metadata.
    """

    name = "csv_replay"

    def __init__(
        self,
        event_bus: EventBus,
        data_dir: str = "backtest/data",
        days: int = 60,
        resolution: str = "1m",
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._data_dir = data_dir
        self._days = days
        self._resolution = resolution
        self._config = kwargs.get("config", None)

        # Populated during start()
        self._aligned_data: dict[str, pd.DataFrame] = {}
        self._4h_timestamps: dict[str, set] = {}
        self._1d_timestamps: dict[str, set] = {}

    async def start(self, symbols: list[str]) -> None:
        """Load data for all symbols. Does NOT emit events yet.

        Events are emitted via ``replay()`` which is called by the
        backtest runner in App.
        """
        for symbol in symbols:
            df_base = self._load_csv(symbol)
            if df_base is None:
                logger.warning("No data found for %s — skipping", symbol)
                continue

            df_4h, df_1d, df_aligned, ts_4h, ts_1d = self._calculate_indicators(
                df_base, self._config
            )
            self._aligned_data[symbol] = df_aligned
            self._4h_timestamps[symbol] = ts_4h
            self._1d_timestamps[symbol] = ts_1d

            logger.info(
                "%s: %d base bars, %d 4h bars, %d 1D bars",
                symbol, len(df_aligned), len(df_4h), len(df_1d),
            )

    async def stop(self) -> None:
        self._aligned_data.clear()

    # ------------------------------------------------------------------
    # Replay — called by backtest runner
    # ------------------------------------------------------------------

    def replay(self, symbol: str) -> int:
        """Emit CandleEvents for *symbol* bar-by-bar. Returns bar count."""
        df = self._aligned_data.get(symbol)
        if df is None:
            return 0

        ts_4h = self._4h_timestamps.get(symbol, set())
        ts_1d = self._1d_timestamps.get(symbol, set())
        count = 0

        for ts, row in df.iterrows():
            candle = Candle(
                symbol=symbol,
                timestamp=ts.to_pydatetime(),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume", 0.0),
                timeframe=self._resolution,
            )

            # Pack indicators and context into CandleEvent metadata-style
            # by publishing a special backtest candle event that carries
            # the pre-computed indicators.
            indicators = {
                "4h": {
                    "atr_pct": row.get("atr_pct_4h", 0),
                    "donch_high_prev": row.get("donch_high_prev_4h", 0),
                    "bb_width_pct": row.get("bb_width_pct_4h", 0),
                    "bb_width": row.get("bb_width_4h", 0),
                    "roc_score_4h": row.get("roc_score_4h", 0),
                },
                "1D": {
                    "ema200": row.get("ema200_1d", 0),
                    "ema50": row.get("ema50_1d", 0),
                    "ema200_slope": row.get("ema200_slope_1d", 0),
                    "ema50_slope": row.get("ema50_slope_1d", 0),
                },
            }

            context = {
                "is_4h_close": ts in ts_4h,
                "is_1d_close": ts in ts_1d,
            }

            self._bus.publish(BacktestCandleEvent(
                candle=candle, indicators=indicators, context=context,
            ))
            count += 1

            if count % 50_000 == 0:
                logger.info("  %s: replayed %d / %d bars", symbol, count, len(df))

        return count

    # ------------------------------------------------------------------
    # CSV loading
    # ------------------------------------------------------------------

    def _load_csv(self, symbol: str) -> pd.DataFrame | None:
        data_path = Path(self._data_dir)
        symbol_underscore = symbol.replace("-", "_")
        candidates = [
            data_path / f"{symbol_underscore}_{self._resolution}.csv",
            data_path / f"{symbol}_{self._resolution}.csv",
            data_path / f"{symbol_underscore}.csv",
        ]

        csv_path = None
        for p in candidates:
            if p.exists():
                csv_path = p
                break

        if csv_path is None:
            logger.warning("%s: CSV not found (tried %s)", symbol, candidates)
            return None

        df = pd.read_csv(csv_path)
        time_col = "time" if "time" in df.columns else "timestamp"
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col).sort_index()
        df.index.name = "timestamp"

        # Filter to last N days
        from datetime import timedelta
        end_date = df.index[-1]
        start_date = end_date - timedelta(days=self._days)
        df = df[df.index >= start_date]

        logger.info("%s: loaded %d bars (%s)", symbol, len(df), self._resolution)
        return df

    # ------------------------------------------------------------------
    # Indicator calculation (ported from data_providers.py)
    # ------------------------------------------------------------------

    def _calculate_indicators(
        self, df_base: pd.DataFrame, config: Any
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, set, set]:
        from backtest.data_resampler import DataResampler

        df_4h = DataResampler.resample_ohlcv(df_base, "4h", label="right")
        df_1d = DataResampler.resample_ohlcv(df_base, "1d", label="right")

        ts_4h = set(df_4h.index)
        ts_1d = set(df_1d.index)

        if config is not None:
            df_4h = self._calc_4h(df_4h, config)
            df_1d = self._calc_1d(df_1d, config)
            df_aligned = self._align(df_base, df_4h, df_1d)
        else:
            # No config — skip indicator computation, return raw data.
            # Strategies that compute their own indicators (e.g. composite_scoring)
            # don't need the 4h/1D indicators from the data provider.
            df_aligned = df_base.copy()

        return df_4h, df_1d, df_aligned, ts_4h, ts_1d

    @staticmethod
    def _calc_4h(df: pd.DataFrame, config: Any) -> pd.DataFrame:
        atr_len = config.atr_len_4h

        # True range
        high_low = df["high"] - df["low"]
        high_pc = abs(df["high"] - df["close"].shift(1))
        low_pc = abs(df["low"] - df["close"].shift(1))
        df["tr"] = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
        df["atr"] = df["tr"].ewm(span=atr_len, adjust=False).mean()
        df["atr_pct"] = df["atr"] / df["close"]

        # Donchian
        df["donch_high_prev"] = df["high"].shift(1).rolling(config.donch_len).max()

        # Bollinger Bands
        df["bb_mid"] = df["close"].rolling(config.bb_len).mean()
        df["bb_std"] = df["close"].rolling(config.bb_len).std()
        df["bb_upper"] = df["bb_mid"] + config.bb_k * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - config.bb_k * df["bb_std"]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

        df["bb_width_pct"] = df["bb_width"].rolling(config.bb_width_window).apply(
            lambda x: (x <= x.iloc[-1]).sum() / len(x) * 100 if len(x) > 0 else 0,
            raw=False,
        )

        # ROC score
        roc_raw = df["close"].pct_change(config.roc_len_4h)
        if config.roc_ema_len > 1:
            df["roc_4h"] = roc_raw.ewm(span=config.roc_ema_len, adjust=False).mean()
        else:
            df["roc_4h"] = roc_raw
        df["roc_score_4h"] = df["roc_4h"] / df["atr_pct"].replace(0, np.nan)

        return df

    @staticmethod
    def _calc_1d(df: pd.DataFrame, config: Any) -> pd.DataFrame:
        df["ema200"] = df["close"].ewm(span=config.ema200_len_1d, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=config.ema50_len_1d, adjust=False).mean()

        lookback = config.ema_slope_lookback_days
        df["ema200_slope"] = df["ema200"] - df["ema200"].shift(lookback)
        df["ema50_slope"] = df["ema50"] - df["ema50"].shift(lookback)

        return df

    @staticmethod
    def _align(
        df_base: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame
    ) -> pd.DataFrame:
        df_4h_ind = df_4h[
            ["atr_pct", "donch_high_prev", "bb_width_pct", "bb_width", "roc_score_4h"]
        ].copy()
        df_4h_ind.columns = [
            "atr_pct_4h", "donch_high_prev_4h", "bb_width_pct_4h",
            "bb_width_4h", "roc_score_4h",
        ]

        df_1d_ind = df_1d[
            ["ema200", "ema50", "ema200_slope", "ema50_slope"]
        ].copy()
        df_1d_ind.columns = [
            "ema200_1d", "ema50_1d", "ema200_slope_1d", "ema50_slope_1d",
        ]

        df_aligned = df_base.copy()
        df_aligned = pd.merge_asof(
            df_aligned, df_4h_ind, left_index=True, right_index=True,
            direction="backward",
        )
        df_aligned = pd.merge_asof(
            df_aligned, df_1d_ind, left_index=True, right_index=True,
            direction="backward",
        )
        return df_aligned


# ---------------------------------------------------------------------------
# Backtest-specific event (extends CandleEvent with indicators)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from v2.core.types import CandleEvent as _BaseCandleEvent


@dataclass(frozen=True)
class BacktestCandleEvent:
    """CandleEvent with pre-computed indicators and context for backtesting."""
    candle: Candle
    indicators: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
