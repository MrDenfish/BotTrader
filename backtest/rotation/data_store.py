"""Daily-bar cache: Kraken bulk OHLCVT CSVs + REST top-up.

Kraken's REST OHLC endpoint returns at most ~720 bars, so multi-year
history MUST come from the quarterly bulk OHLCVT export
(support.kraken.com, "Downloadable historical OHLCVT data"); REST only
fills the tail since the last export.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_COLS = ["open", "high", "low", "close", "volume"]
_REST_URL = "https://api.kraken.com/0/public/OHLC"


class DailyBarStore:
    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol}.csv"

    # -- loading ---------------------------------------------------------
    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        df = pd.read_csv(p, parse_dates=["date"], index_col="date")
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
        df = df[df.index.notna()]
        if df.empty:
            return None
        return df[_COLS].sort_index()

    def symbols(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.csv"))

    def _save(self, symbol: str, df: pd.DataFrame) -> None:
        out = df[~df.index.duplicated(keep="first")].sort_index()
        out.to_csv(self._path(symbol), index_label="date")

    # -- bulk import -----------------------------------------------------
    def import_ohlcvt_csv(self, symbol: str, csv_path: str | Path) -> int:
        raw = pd.read_csv(
            csv_path, header=None,
            names=["ts", "open", "high", "low", "close", "volume", "trades"],
        )
        df = raw.assign(
            date=pd.to_datetime(raw["ts"], unit="s", utc=True).dt.normalize()
        ).set_index("date")[_COLS]
        existing = self.load(symbol)
        merged = df if existing is None else pd.concat([existing, df])
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
        self._save(symbol, merged)
        return len(merged)

    # -- REST top-up -----------------------------------------------------
    def _fetch_rest_ohlc(self, kraken_pair: str) -> list[list]:
        resp = requests.get(
            _REST_URL, params={"pair": kraken_pair, "interval": 1440}, timeout=30
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise RuntimeError(f"Kraken OHLC error for {kraken_pair}: {payload['error']}")
        result = payload["result"]
        key = next(k for k in result if k != "last")
        rows = result[key]
        # Kraken's `last` marks the id (timestamp) of the current, still-open
        # frame; committed rows have ts < last. Dropping ts >= last prevents
        # storing the in-progress final candle as if it were final. If `last`
        # is absent/0, conservatively drop the final row.
        last = int(result.get("last", 0))
        if last:
            return [r for r in rows if int(r[0]) < last]
        return rows[:-1] if rows else rows

    def top_up_from_rest(self, symbol: str, kraken_pair: str) -> int:
        rows = self._fetch_rest_ohlc(kraken_pair)
        df = pd.DataFrame(
            rows, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"]
        )
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = df[c].astype(float)
        df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.normalize()
        df = df.set_index("date")[_COLS]
        existing = self.load(symbol)
        if existing is not None:
            df = df[df.index > existing.index.max()]
            merged = pd.concat([existing, df])
        else:
            merged = df
        self._save(symbol, merged)
        logger.info("top_up %s: +%d rows", symbol, len(df))
        return len(df)
