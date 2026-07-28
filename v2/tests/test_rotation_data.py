import pandas as pd
import pytest

from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.universe import eligible_symbols


@pytest.fixture
def store(tmp_path):
    return DailyBarStore(tmp_path)


def _write_ohlcvt(path, rows):
    # Kraken bulk format: headerless ts,o,h,l,c,vol,trades
    with open(path, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


DAY = 86400
T0 = 1577836800  # 2020-01-01 00:00 UTC


class TestDailyBarStore:
    def test_import_and_load(self, store, tmp_path):
        p = tmp_path / "XBTUSD_1440.csv"
        _write_ohlcvt(p, [
            (T0, 100, 110, 90, 105, 12.5, 300),
            (T0 + DAY, 105, 115, 100, 112, 9.1, 250),
        ])
        n = store.import_ohlcvt_csv("BTC-USD", p)
        assert n == 2
        df = store.load("BTC-USD")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 2
        assert df.index[0] == pd.Timestamp("2020-01-01", tz="UTC")
        assert df["close"].iloc[1] == 112

    def test_reimport_dedupes(self, store, tmp_path):
        p = tmp_path / "a.csv"
        _write_ohlcvt(p, [(T0, 1, 1, 1, 1, 1, 1)])
        store.import_ohlcvt_csv("BTC-USD", p)
        assert store.import_ohlcvt_csv("BTC-USD", p) == 1

    def test_load_missing_returns_none(self, store):
        assert store.load("NOPE-USD") is None

    def test_top_up_merges_only_newer(self, store, tmp_path, monkeypatch):
        p = tmp_path / "a.csv"
        _write_ohlcvt(p, [(T0, 100, 110, 90, 105, 12.5, 300)])
        store.import_ohlcvt_csv("BTC-USD", p)

        def fake_fetch(self, pair):
            # Kraken REST rows: [time, open, high, low, close, vwap, volume, count]
            return [
                [T0, "999", "999", "999", "999", "0", "999", 1],       # older/equal: ignored
                [T0 + DAY, "105", "115", "100", "112", "0", "9.1", 2], # newer: added
            ]

        monkeypatch.setattr(DailyBarStore, "_fetch_rest_ohlc", fake_fetch)
        added = store.top_up_from_rest("BTC-USD", "XBTUSD")
        assert added == 1
        df = store.load("BTC-USD")
        assert len(df) == 2
        assert df["close"].iloc[0] == 105  # bulk row NOT overwritten


class TestEligibleSymbols:
    def _bars(self, n_days, close, volume, end="2024-06-30"):
        idx = pd.date_range(end=end, periods=n_days, freq="D", tz="UTC")
        return pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close, "volume": volume},
            index=idx,
        )

    def test_volume_floor_and_age(self):
        bars = {
            "BIG-USD": self._bars(400, close=10.0, volume=2_000_000),   # $20M/day
            "THIN-USD": self._bars(400, close=1.0, volume=1_000_000),   # $1M/day
            "YOUNG-USD": self._bars(90, close=10.0, volume=2_000_000),  # too new
        }
        got = eligible_symbols(bars, pd.Timestamp("2024-06-30", tz="UTC"),
                               volume_floor=10_000_000)
        assert got == ["BIG-USD"]

    def test_no_lookahead(self):
        # Volume explodes AFTER asof; must not count
        b = self._bars(400, close=1.0, volume=1_000)
        b.loc[b.index > pd.Timestamp("2024-01-01", tz="UTC"), "volume"] = 1e9
        got = eligible_symbols({"X-USD": b}, pd.Timestamp("2024-01-01", tz="UTC"),
                               volume_floor=10_000_000)
        assert got == []

    def test_top_n_ranked_by_dollar_volume(self):
        bars = {f"S{i}-USD": self._bars(400, 10.0, (i + 2) * 1_000_000) for i in range(30)}
        got = eligible_symbols(bars, pd.Timestamp("2024-06-30", tz="UTC"),
                               volume_floor=5_000_000, top_n=3)
        assert got == ["S29-USD", "S28-USD", "S27-USD"]
