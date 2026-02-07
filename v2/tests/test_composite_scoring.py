"""Tests for Milestone 3 composite scoring strategy plugin."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import Candle, Direction, TickerEvent
from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig
from v2.plugins.strategies.composite_scoring.guardrails import Guardrails
from v2.plugins.strategies.composite_scoring.indicators import compute_indicators
from v2.plugins.strategies.composite_scoring.scoring import compute_scores


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_df(n: int = 100, base_price: float = 97000.0, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed)
    timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
    closes = base_price + np.cumsum(rng.normal(0, 50, n))
    highs = closes + rng.uniform(10, 100, n)
    lows = closes - rng.uniform(10, 100, n)
    opens = closes + rng.normal(0, 30, n)
    volumes = rng.uniform(0.5, 5.0, n)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=pd.DatetimeIndex(timestamps, name="timestamp"))
    return df


def _make_candle(
    symbol: str = "BTC-USD",
    price: float = 97000.0,
    ts: datetime | None = None,
) -> Candle:
    return Candle(
        symbol=symbol,
        timestamp=ts or datetime.now(),
        open=price - 10,
        high=price + 50,
        low=price - 50,
        close=price,
        volume=1.5,
    )


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------

class TestCompositeScoreConfig:
    def test_defaults(self):
        cfg = CompositeScoreConfig()
        assert cfg.score_buy_target == 5.5
        assert cfg.score_sell_target == 5.5
        assert cfg.bb_window == 20
        assert cfg.macd_fast == 12
        assert cfg.rsi_window == 14
        assert len(cfg.weights) == 14

    def test_override(self):
        cfg = CompositeScoreConfig(score_buy_target=4.0, cooldown_bars=10)
        assert cfg.score_buy_target == 4.0
        assert cfg.cooldown_bars == 10

    def test_from_dict(self):
        d = {"score_buy_target": 3.0, "rsi_buy": 25.0}
        cfg = CompositeScoreConfig(**d)
        assert cfg.score_buy_target == 3.0
        assert cfg.rsi_buy == 25.0


# ------------------------------------------------------------------
# Indicators
# ------------------------------------------------------------------

class TestIndicators:
    def test_returns_empty_if_insufficient_data(self):
        df = _make_df(n=5)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert result == {}

    def test_returns_all_indicator_keys(self):
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)

        expected_keys = {
            "Buy Touch", "Sell Touch", "Buy Ratio", "Sell Ratio",
            "Buy MACD", "Sell MACD", "Buy RSI", "Sell RSI",
            "Buy ROC", "Sell ROC", "W-Bottom", "M-Top",
            "Buy Swing", "Sell Swing",
            "_RSI", "_ROC", "_upper", "_lower", "_MACD_Hist",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_tuples_are_normalized(self):
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)

        for key in ["Buy Touch", "Sell Touch", "Buy RSI", "Sell RSI",
                     "Buy MACD", "Sell MACD", "Buy ROC", "Sell ROC"]:
            t = result[key]
            assert isinstance(t, tuple) and len(t) == 3
            assert isinstance(t[0], int) and t[0] in (0, 1)
            assert isinstance(t[1], float)
            assert isinstance(t[2], float)

    def test_rsi_within_range(self):
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        rsi = result["_RSI"]
        assert 0.0 <= rsi <= 100.0

    def test_w_bottom_m_top_always_zero_on_last_row(self):
        """v1 behaviour: W-Bottom/M-Top never fire on the last row."""
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert result["W-Bottom"][0] == 0
        assert result["M-Top"][0] == 0

    def test_buy_swing_never_fires(self):
        """v1 behaviour: rolling_high includes current bar, so close > rolling_high is always False."""
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert result["Buy Swing"][0] == 0


# ------------------------------------------------------------------
# Scoring
# ------------------------------------------------------------------

class TestScoring:
    def test_all_zeros_gives_hold(self):
        """All indicators at 0 → score below threshold → hold."""
        indicators = {
            name: (0, 0.0, 0.0) for name in CompositeScoreConfig().weights
        }
        cfg = CompositeScoreConfig()
        buy_score, sell_score, buy_sig, sell_sig, _ = compute_scores(indicators, cfg)
        assert buy_score == 0.0
        assert sell_score == 0.0
        assert buy_sig[0] == 0
        assert sell_sig[0] == 0

    def test_high_buy_score_triggers(self):
        """Enough buy indicators firing → buy signal."""
        cfg = CompositeScoreConfig(score_buy_target=3.0)
        indicators = {
            "Buy Touch": (1, 97000.0, 96500.0),
            "Buy RSI": (1, 15.0, 20.0),
            "Buy ROC": (1, 6.0, 5.0),
            "Buy MACD": (0, 0.1, 0.0),
            "Buy Ratio": (0, 1.01, 1.0),
            "Buy Swing": (0, 97000.0, 0.0),
            "W-Bottom": (0, 96800.0, 50.0),
            "Sell Touch": (0, 97000.0, 97500.0),
            "Sell RSI": (0, 50.0, 80.0),
            "Sell ROC": (0, 1.0, -2.0),
            "Sell MACD": (0, -0.1, 0.0),
            "Sell Ratio": (0, 1.01, 0.95),
            "Sell Swing": (0, 97000.0, 0.0),
            "M-Top": (0, 97200.0, 50.0),
        }
        buy_score, sell_score, buy_sig, sell_sig, _ = compute_scores(indicators, cfg)
        # Buy Touch (1.5) + Buy RSI (1.5) + Buy ROC (2.0) = 5.0 >= 3.0
        assert buy_score == pytest.approx(5.0)
        assert buy_sig[0] == 1
        assert sell_sig[0] == 0

    def test_multi_indicator_gate_suppresses(self):
        """Single indicator firing with high weight still blocked by min_indicators_required."""
        cfg = CompositeScoreConfig(
            score_buy_target=2.0,
            min_indicators_required=2,
            weights={"Buy RSI": 3.0, "Buy ROC": 3.0,
                     "Sell RSI": 3.0, "Sell ROC": 3.0},
        )
        indicators = {
            "Buy RSI": (1, 15.0, 20.0),   # fires
            "Buy ROC": (0, 1.0, 5.0),     # does not fire
            "Sell RSI": (0, 50.0, 80.0),
            "Sell ROC": (0, 1.0, -2.0),
        }
        buy_score, _, buy_sig, _, comps = compute_scores(indicators, cfg)
        assert buy_score == pytest.approx(3.0)
        # Score exceeds target but only 1 indicator fired → suppressed
        assert buy_sig[0] == 0
        assert "suppressed" in comps["suppression"]


# ------------------------------------------------------------------
# Guardrails
# ------------------------------------------------------------------

class TestGuardrails:
    def test_first_trade_no_guardrails(self):
        g = Guardrails()
        cfg = CompositeScoreConfig()
        buy_sig = (1, 6.0, 5.5)
        sell_sig = (0, 1.0, 5.5)
        _, _, action, note = g.apply("BTC-USD", buy_sig, sell_sig, 6.0, 1.0, 100, cfg)
        assert action == "buy"
        assert note is None

    def test_hysteresis_suppresses_marginal_flip(self):
        g = Guardrails()
        # Disable cooldown so we isolate hysteresis behaviour
        cfg = CompositeScoreConfig(flip_hysteresis_pct=0.10, cooldown_bars=0)

        # First trade: buy
        buy_sig = (1, 6.0, 5.5)
        sell_sig = (0, 1.0, 5.5)
        g.apply("BTC-USD", buy_sig, sell_sig, 6.0, 1.0, 100, cfg)

        # Attempt sell with marginal score (5.8 < 5.5 * 1.10 = 6.05)
        buy_sig2 = (0, 1.0, 5.5)
        sell_sig2 = (1, 5.8, 5.5)
        _, _, action, note = g.apply("BTC-USD", buy_sig2, sell_sig2, 1.0, 5.8, 101, cfg)
        assert action == "hold"
        assert note == "sell_suppressed_by_hysteresis"

    def test_hysteresis_allows_strong_flip(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(flip_hysteresis_pct=0.10, cooldown_bars=0)

        # First trade: buy
        g.apply("BTC-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 100, cfg)

        # Sell with strong score (7.0 >= 5.5 * 1.10 = 6.05)
        _, _, action, _ = g.apply(
            "BTC-USD", (0, 1.0, 5.5), (1, 7.0, 5.5), 1.0, 7.0, 101, cfg,
        )
        assert action == "sell"

    def test_cooldown_blocks_opposite(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(cooldown_bars=5)

        # Buy at bar 100
        g.apply("BTC-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 100, cfg)

        # Try sell at bar 103 (within cooldown: 100 + 5 = 105)
        _, _, action, note = g.apply(
            "BTC-USD", (0, 1.0, 5.5), (1, 7.0, 5.5), 1.0, 7.0, 103, cfg,
        )
        assert action == "hold"
        assert note == "sell_suppressed_by_cooldown"

    def test_cooldown_expires(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(cooldown_bars=5)

        g.apply("BTC-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 100, cfg)

        # Sell at bar 106 (after cooldown: 100 + 5 = 105)
        _, _, action, _ = g.apply(
            "BTC-USD", (0, 1.0, 5.5), (1, 7.0, 5.5), 1.0, 7.0, 106, cfg,
        )
        assert action == "sell"

    def test_state_persistence(self):
        g = Guardrails()
        cfg = CompositeScoreConfig()
        g.apply("BTC-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 100, cfg)

        state = g.get_state()
        assert state["last_side"]["BTC-USD"] == "long"

        g2 = Guardrails()
        g2.load_state(state)
        assert g2._last_side["BTC-USD"] == "long"


# ------------------------------------------------------------------
# Strategy plugin
# ------------------------------------------------------------------

class TestCompositeScoringStrategy:
    def setup_method(self):
        registry.discover_plugins()

    def test_plugin_registered(self):
        plugins = registry.list_plugins()
        assert "composite_scoring" in plugins["strategy"]

    def test_configure_with_defaults(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({})
        assert s._config.score_buy_target == 5.5

    def test_configure_with_dict(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"score_buy_target": 4.0, "rsi_buy": 25.0})
        assert s._config.score_buy_target == 4.0
        assert s._config.rsi_buy == 25.0

    def test_configure_with_config_object(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        cfg = CompositeScoreConfig(score_buy_target=3.0)
        s.configure(cfg)
        assert s._config.score_buy_target == 3.0

    def test_warmup_returns_before_min_bars(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"min_bars": 50})

        # Send 49 candles — should return None each time
        base_ts = datetime(2026, 1, 1)
        for i in range(49):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0,
                close=97000.0, volume=1.0,
            )
            result = s.on_candle(candle, {})
            assert result is None

    def test_produces_signal_after_warmup(self):
        """After enough bars, the strategy should return Signal or None (not crash)."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"min_bars": 40, "score_buy_target": 0.1})

        base_ts = datetime(2026, 1, 1)
        results = []
        for i in range(100):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0 + i * 10,
                high=97100.0 + i * 10,
                low=96900.0 + i * 10,
                close=97000.0 + i * 10,
                volume=1.0 + i * 0.01,
            )
            result = s.on_candle(candle, {})
            if result is not None:
                results.append(result)

        # With a very low threshold, some signals should fire
        # (at minimum, the ROC momentum should trigger on steady uptrend)
        # But we're not guaranteeing signals — just that it doesn't crash
        # and returns proper types.
        for sig in results:
            assert sig.direction in (Direction.BUY, Direction.SELL)
            assert sig.symbol == "BTC-USD"
            assert sig.reason != ""

    def test_on_ticker_captures_24h_roc(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({})

        ticker = TickerEvent(
            symbol="BTC-USD",
            price=97000.0,
            timestamp=datetime.now(),
            change_24h_pct=9.5,
        )
        s.on_ticker(ticker)
        assert s._roc_24h["BTC-USD"] == 9.5

    def test_on_backtest_bar_delegates_to_process(self):
        """on_backtest_bar should work identically to on_candle."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"min_bars": 40})

        base_ts = datetime(2026, 1, 1)
        for i in range(50):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0,
                close=97000.0, volume=1.0,
            )
            # Both paths should not crash
            s.on_backtest_bar("BTC-USD", candle, {}, {})

    def test_state_roundtrip(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({})

        # Inject some state
        s._bar_idx["BTC-USD"] = 42
        s._roc_24h["BTC-USD"] = 9.5
        s._guardrails._last_side["BTC-USD"] = "long"

        state = s.get_state()
        assert state["bar_idx"]["BTC-USD"] == 42
        assert state["roc_24h"]["BTC-USD"] == 9.5

        s2 = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s2.configure({})
        s2.load_state(state)
        assert s2._bar_idx["BTC-USD"] == 42
        assert s2._roc_24h["BTC-USD"] == 9.5
        assert s2._guardrails._last_side["BTC-USD"] == "long"
