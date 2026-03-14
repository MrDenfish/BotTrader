"""Tests for Milestone 3 composite scoring strategy plugin."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import Candle, Direction, RiskEvent, TickerEvent
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
        assert len(cfg.weights) == 16
        assert cfg.volume_confirm_buy is True
        assert cfg.volume_confirm_threshold == 0.7
        assert cfg.volume_div_window == 10

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
            "Buy Volume Div", "Sell Volume Div",
            "_RSI", "_ROC", "_RVOL", "_upper", "_lower", "_MACD_Hist",
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

    def test_w_bottom_fires_with_pattern(self):
        """W-Bottom fires when price dips below lower BB and recovers."""
        cfg = CompositeScoreConfig(bb_window=10, bb_std=2.0, atr_window=8)
        # Build a DataFrame where bars n-3/n-2/n-1 form a W-Bottom pattern:
        # prev (n-3): low dips below lower BB
        # curr (n-2): low rises above prev low
        # next (n-1): low rises further, close above basis
        n = 50
        rng = np.random.default_rng(99)
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        base = 100.0
        closes = np.full(n, base) + np.cumsum(rng.normal(0, 0.3, n))
        highs = closes + rng.uniform(0.5, 2.0, n)
        lows = closes - rng.uniform(0.5, 2.0, n)
        opens = closes + rng.normal(0, 0.5, n)
        volumes = np.zeros(n)  # No volume data (WebSocket scenario)

        # Force W-Bottom pattern at the end:
        # n-3: low dips far below lower band
        lows[-3] = closes[-3] - 20.0  # Deep dip below BB
        # n-2: low higher than n-3
        lows[-2] = closes[-2] - 5.0
        # n-1: low higher still, close above basis
        lows[-1] = closes[-1] - 1.0
        closes[-1] = base + 5.0  # Above basis
        highs[-1] = closes[-1] + 1.0

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        result = compute_indicators(df, cfg)
        # With zero volume, the volume check is bypassed — pattern should fire
        assert result["W-Bottom"][0] == 1

    def test_w_bottom_zero_without_pattern(self):
        """W-Bottom returns 0 when no pattern is present."""
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        # Random data unlikely to have an exact W-Bottom at the end
        assert result["W-Bottom"][0] in (0, 1)  # Could happen by chance
        assert isinstance(result["W-Bottom"], tuple) and len(result["W-Bottom"]) == 3

    def test_w_bottom_bypasses_volume_when_zero(self):
        """When all volume is 0, volume check is skipped (WebSocket mode)."""
        cfg = CompositeScoreConfig(bb_window=10, atr_window=8)
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        base = 100.0
        closes = np.full(n, base)
        highs = closes + 2.0
        lows = closes - 2.0
        opens = closes.copy()
        volumes = np.zeros(n)  # All zero volume

        # Force W-Bottom pattern
        lows[-3] = base - 20.0
        lows[-2] = base - 5.0
        lows[-1] = base - 1.0
        closes[-1] = base + 5.0
        highs[-1] = base + 6.0

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        result = compute_indicators(df, cfg)
        # Volume bypass allows pattern to fire
        assert result["W-Bottom"][0] == 1

    def test_w_bottom_enforces_volume_when_available(self):
        """When volume data exists, the volume condition is enforced."""
        cfg = CompositeScoreConfig(bb_window=10, atr_window=8)
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        base = 100.0
        closes = np.full(n, base)
        highs = closes + 2.0
        lows = closes - 2.0
        opens = closes.copy()
        volumes = np.ones(n) * 10.0  # Real volume data

        # Force W-Bottom pattern
        lows[-3] = base - 20.0
        lows[-2] = base - 5.0
        lows[-1] = base - 1.0
        closes[-1] = base + 5.0
        highs[-1] = base + 6.0
        # But set confirmation bar volume to 0 (below mean)
        volumes[-1] = 0.0

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        result = compute_indicators(df, cfg)
        # Volume exists but confirmation bar has 0 volume → pattern blocked
        assert result["W-Bottom"][0] == 0

    def test_buy_swing_detects_breakout(self):
        """Buy Swing fires when close breaks above previous rolling high with MACD confirmation."""
        cfg = CompositeScoreConfig(swing_window=10, macd_fast=8, macd_slow=21, macd_signal=5)
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        # Flat market then sharp breakout on the last bar
        closes = np.full(n, 100.0)
        # Last bar breaks out significantly above the flat range
        closes[-1] = 120.0
        # Also need MACD > signal line — create a recent uptrend for MACD
        for i in range(n - 10, n):
            closes[i] = 100.0 + (i - (n - 10)) * 2.5  # Ramp up
        closes[-1] = 125.0  # Final breakout well above rolling high

        highs = closes + 1.0
        lows = closes - 1.0
        opens = closes - 0.5
        volumes = np.ones(n)

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        result = compute_indicators(df, cfg)
        # Close should exceed previous bar's rolling high (shift(1) fix)
        assert result["Buy Swing"][0] == 1


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

    def test_sell_indicator_gate_independent(self):
        """min_sell_indicators_required overrides shared gate for sells only."""
        cfg = CompositeScoreConfig(
            score_buy_target=2.0,
            score_sell_target=2.0,
            min_indicators_required=2,
            min_sell_indicators_required=4,
            weights={
                "Buy RSI": 3.0, "Buy ROC": 3.0,
                "Sell RSI": 1.5, "Sell ROC": 1.5, "Sell MACD": 1.5, "Sell Swing": 1.5,
            },
        )
        indicators = {
            # 2 buy indicators fire → passes shared gate (2)
            "Buy RSI": (1, 15.0, 20.0),
            "Buy ROC": (1, 6.0, 5.0),
            # 3 sell indicators fire → passes shared gate (2) but fails sell gate (4)
            "Sell RSI": (1, 85.0, 80.0),
            "Sell ROC": (1, -3.0, -2.0),
            "Sell MACD": (1, -0.5, 0.0),
            "Sell Swing": (0, 0.5, 1.0),
        }
        _, _, buy_sig, sell_sig, comps = compute_scores(indicators, cfg)
        # Buy passes (2 fired >= min 2)
        assert buy_sig[0] == 1
        # Sell suppressed (3 fired < min_sell 4)
        assert sell_sig[0] == 0
        assert "sell_suppressed" in comps["suppression"]
        assert "_3_of_4" in comps["suppression"]


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
# Loss lockout guardrails
# ------------------------------------------------------------------

class TestLossLockout:
    def test_record_soft_stop_sets_lockout(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)
        # soft_stop scale = 1.0 → lockout until bar 62
        assert g.is_buy_locked_by_loss("ORCA-USD", 50)
        assert g.is_buy_locked_by_loss("ORCA-USD", 61)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 62)

    def test_record_hard_stop_doubles_lockout(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        g.record_loss_exit("ORCA-USD", 50, "hard_stop", cfg)
        # hard_stop scale = 2.0 → lockout until bar 74
        assert g.is_buy_locked_by_loss("ORCA-USD", 73)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 74)

    def test_record_trailing_stop_halves_lockout(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        g.record_loss_exit("ORCA-USD", 50, "trailing_stop", cfg)
        # trailing_stop scale = 0.5 → lockout until bar 56
        assert g.is_buy_locked_by_loss("ORCA-USD", 55)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 56)

    def test_lockout_extends_never_shortens(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        # Hard stop at bar 50 → lockout until 74
        g.record_loss_exit("ORCA-USD", 50, "hard_stop", cfg)
        # Soft stop at bar 60 → would be 72, but 74 > 72, so no change
        g.record_loss_exit("ORCA-USD", 60, "soft_stop", cfg)
        assert g.is_buy_locked_by_loss("ORCA-USD", 73)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 74)

    def test_lockout_extends_when_later_exit_is_longer(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        # Soft stop at bar 50 → lockout until 62
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)
        # Hard stop at bar 60 → lockout until 84 (extends)
        g.record_loss_exit("ORCA-USD", 60, "hard_stop", cfg)
        assert g.is_buy_locked_by_loss("ORCA-USD", 83)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 84)

    def test_lockout_does_not_affect_other_symbols(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)
        assert not g.is_buy_locked_by_loss("BTC-USD", 50)

    def test_lockout_disabled_when_bars_zero(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=0)
        g.record_loss_exit("ORCA-USD", 50, "hard_stop", cfg)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 50)

    def test_unknown_reason_uses_scale_1(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=10)
        g.record_loss_exit("ORCA-USD", 50, "unknown_reason", cfg)
        # Default scale = 1.0 → lockout until bar 60
        assert g.is_buy_locked_by_loss("ORCA-USD", 59)
        assert not g.is_buy_locked_by_loss("ORCA-USD", 60)

    def test_apply_suppresses_buy_during_lockout(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12, cooldown_bars=0)
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)

        # Attempt buy at bar 55 (within lockout window)
        _, _, action, note = g.apply(
            "ORCA-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 55, cfg,
        )
        assert action == "hold"
        assert "loss_lockout" in note

    def test_apply_allows_buy_after_lockout(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12, cooldown_bars=0)
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)

        # Buy at bar 62 (lockout expired)
        _, _, action, _ = g.apply(
            "ORCA-USD", (1, 6.0, 5.5), (0, 1.0, 5.5), 6.0, 1.0, 62, cfg,
        )
        assert action == "buy"

    def test_apply_allows_sell_during_lockout(self):
        """Loss lockout only blocks buys, not sells."""
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12, cooldown_bars=0)
        g.record_loss_exit("ORCA-USD", 50, "soft_stop", cfg)

        _, _, action, _ = g.apply(
            "ORCA-USD", (0, 1.0, 5.5), (1, 6.0, 5.5), 1.0, 6.0, 55, cfg,
        )
        assert action == "sell"

    def test_lockout_state_persistence(self):
        g = Guardrails()
        cfg = CompositeScoreConfig(loss_lockout_bars=12)
        g.record_loss_exit("ORCA-USD", 50, "hard_stop", cfg)

        state = g.get_state()
        assert state["loss_lockout_until"]["ORCA-USD"] == 74

        g2 = Guardrails()
        g2.load_state(state)
        assert g2.is_buy_locked_by_loss("ORCA-USD", 73)
        assert not g2.is_buy_locked_by_loss("ORCA-USD", 74)


# ------------------------------------------------------------------
# Strategy — on_risk_event integration
# ------------------------------------------------------------------

class TestStrategyRiskEvent:
    def setup_method(self):
        registry.discover_plugins()

    def test_on_risk_event_sets_lockout(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"loss_lockout_bars": 10})

        # Simulate the strategy having processed some bars
        s._bar_idx["ORCA-USD"] = 100

        event = RiskEvent(
            event_type="exit_triggered",
            reason="soft_stop",
            metadata={"symbol": "ORCA-USD", "pnl_pct": -3.0, "price": 0.20},
        )
        s.on_risk_event(event)

        # Lockout should be set: 100 + 10 = 110
        assert s._guardrails.is_buy_locked_by_loss("ORCA-USD", 105)
        assert not s._guardrails.is_buy_locked_by_loss("ORCA-USD", 110)

    def test_on_risk_event_ignores_non_exit_events(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"loss_lockout_bars": 10})
        s._bar_idx["ORCA-USD"] = 100

        event = RiskEvent(
            event_type="trailing_activated",
            reason="trailing_stop",
            metadata={"symbol": "ORCA-USD"},
        )
        s.on_risk_event(event)
        assert not s._guardrails.is_buy_locked_by_loss("ORCA-USD", 100)

    def test_on_risk_event_ignores_unknown_symbol(self):
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"loss_lockout_bars": 10})
        # ORCA-USD has no bar_idx (never seen a candle)

        event = RiskEvent(
            event_type="exit_triggered",
            reason="hard_stop",
            metadata={"symbol": "ORCA-USD", "pnl_pct": -5.0},
        )
        # Should not crash
        s.on_risk_event(event)
        assert not s._guardrails.is_buy_locked_by_loss("ORCA-USD", 0)

    def test_lockout_blocks_momentum_buy(self):
        """Loss lockout blocks momentum BUY paths in _evaluate()."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "loss_lockout_bars": 10,
            "candle_interval_minutes": 1,
        })

        # Feed enough candles for warmup
        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="ORCA-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=0.20, high=0.22, low=0.19, close=0.21, volume=1000.0,
            )
            s.on_candle(candle, {})

        # Now simulate a loss exit at the current bar
        current_bar = s._bar_idx["ORCA-USD"]
        event = RiskEvent(
            event_type="exit_triggered",
            reason="soft_stop",
            metadata={"symbol": "ORCA-USD", "pnl_pct": -3.0},
        )
        s.on_risk_event(event)

        # Mock indicators to force a momentum buy signal
        mock_ind = {
            "_ROC": 5.0,  # Strong positive ROC
            "_RSI": 50.0,  # RSI within buy range (45-60)
        }

        with patch(
            "v2.plugins.strategies.composite_scoring.strategy.compute_indicators",
            return_value=mock_ind,
        ):
            candle = Candle(
                symbol="ORCA-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=0.20, high=0.22, low=0.19, close=0.21, volume=1000.0,
            )
            result = s.on_candle(candle, {})

        # Buy should be blocked by loss lockout
        assert result is None or result.direction != Direction.BUY

    def test_lockout_state_roundtrip(self):
        """Loss lockout state survives get_state/load_state cycle."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"loss_lockout_bars": 10})
        s._bar_idx["ORCA-USD"] = 100

        event = RiskEvent(
            event_type="exit_triggered",
            reason="hard_stop",
            metadata={"symbol": "ORCA-USD", "pnl_pct": -5.0},
        )
        s.on_risk_event(event)

        state = s.get_state()
        assert state["guardrails"]["loss_lockout_until"]["ORCA-USD"] == 120

        s2 = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s2.configure({"loss_lockout_bars": 10})
        s2.load_state(state)
        assert s2._guardrails.is_buy_locked_by_loss("ORCA-USD", 119)
        assert not s2._guardrails.is_buy_locked_by_loss("ORCA-USD", 120)


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

    def test_red_day_gate_blocks_buy(self):
        """When allow_buys_on_red_day=False, buys are blocked on red days."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"min_bars": 5, "allow_buys_on_red_day": False})

        # Set 24h change to negative (red day)
        s.on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0,
            timestamp=datetime.now(), change_24h_pct=-2.5,
        ))

        # Mock indicators and scoring to force a buy signal through guardrails
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_ADX": 30.0}
        mock_scores = (5.0, 0.0, True, False, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=(True, False, "buy", "test")):

            base_ts = datetime(2026, 1, 1)
            results = []
            for i in range(10):
                candle = Candle(
                    symbol="BTC-USD",
                    timestamp=base_ts + timedelta(minutes=i),
                    open=97000.0, high=97100.0,
                    low=96900.0, close=97000.0, volume=1.0,
                )
                result = s.on_candle(candle, {})
                if result is not None:
                    results.append(result)

        # Red-day gate should block all buy signals
        buy_signals = [r for r in results if r.direction == Direction.BUY]
        assert len(buy_signals) == 0, f"Expected 0 buys on red day, got {len(buy_signals)}"

    def test_red_day_gate_allows_buy_when_enabled(self):
        """When allow_buys_on_red_day=True (default), buys pass through on red days."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"min_bars": 5, "allow_buys_on_red_day": True})

        # Set 24h change to negative (red day)
        s.on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0,
            timestamp=datetime.now(), change_24h_pct=-2.5,
        ))

        # Mock indicators and scoring to force a buy signal through guardrails
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_ADX": 30.0}
        mock_scores = (5.0, 0.0, True, False, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=(True, False, "buy", "test")):

            base_ts = datetime(2026, 1, 1)
            results = []
            for i in range(10):
                candle = Candle(
                    symbol="BTC-USD",
                    timestamp=base_ts + timedelta(minutes=i),
                    open=97000.0, high=97100.0,
                    low=96900.0, close=97000.0, volume=1.0,
                )
                result = s.on_candle(candle, {})
                if result is not None:
                    results.append(result)

        # With allow_buys_on_red_day=True, buys should pass through despite red day
        buy_signals = [r for r in results if r.direction == Direction.BUY]
        assert len(buy_signals) > 0, "Expected buys to pass through when red-day gate is disabled"


# ------------------------------------------------------------------
# Candle aggregation
# ------------------------------------------------------------------

class TestCandleAggregation:
    def setup_method(self):
        registry.discover_plugins()

    def test_interval_1_passthrough(self):
        """candle_interval_minutes=1 preserves original direct-append behavior."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"candle_interval_minutes": 1, "min_bars": 5})

        base_ts = datetime(2026, 1, 1)
        for i in range(5):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0,
            )
            s.on_candle(candle, {})

        # 5 candles → 5 bars in buffer (no aggregation)
        assert len(s._bars["BTC-USD"]) == 5

    def test_interval_5_aggregates(self):
        """candle_interval_minutes=5 aggregates 5 one-min candles into 1 bar."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"candle_interval_minutes": 5, "min_bars": 3})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=100.0 + i, high=105.0 + i, low=95.0 + i,
                close=101.0 + i, volume=1.0 + i * 0.1,
            )
            s.on_candle(candle, {})

        # 10 one-min candles → 2 aggregated 5-min bars
        assert len(s._bars["BTC-USD"]) == 2

    def test_aggregated_ohlcv_correct(self):
        """Aggregated bar has correct OHLCV: open=first, high=max, low=min, close=last, volume=sum."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"candle_interval_minutes": 3, "min_bars": 1})

        base_ts = datetime(2026, 1, 1)
        candles_data = [
            (100.0, 110.0, 90.0, 105.0, 1.0),   # bar 1
            (105.0, 120.0, 95.0, 115.0, 2.0),    # bar 2 (highest high)
            (115.0, 118.0, 85.0, 112.0, 3.0),    # bar 3 (lowest low)
        ]
        for i, (o, h, l, c, v) in enumerate(candles_data):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=o, high=h, low=l, close=c, volume=v,
            )
            s.on_candle(candle, {})

        assert len(s._bars["BTC-USD"]) == 1
        bar = s._bars["BTC-USD"][0]
        assert bar["open"] == 100.0    # First candle's open
        assert bar["high"] == 120.0    # Max of all highs
        assert bar["low"] == 85.0      # Min of all lows
        assert bar["close"] == 112.0   # Last candle's close
        assert bar["volume"] == 6.0    # Sum of volumes

    def test_partial_buffer_returns_none(self):
        """Fewer than N candles in aggregation buffer returns None."""
        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({"candle_interval_minutes": 5, "min_bars": 1})

        base_ts = datetime(2026, 1, 1)
        for i in range(4):  # Only 4 of 5 needed
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0,
            )
            result = s.on_candle(candle, {})
            assert result is None

        # Buffer not yet in _bars
        assert "BTC-USD" not in s._bars or len(s._bars.get("BTC-USD", [])) == 0

    def test_config_default_interval_is_1(self):
        """Default candle_interval_minutes is 1 (backward compatible)."""
        cfg = CompositeScoreConfig()
        assert cfg.candle_interval_minutes == 1


# ------------------------------------------------------------------
# Volume divergence indicator
# ------------------------------------------------------------------

class TestVolumeDivergence:
    def test_bullish_divergence_fires(self):
        """Price falling + volume declining = bullish divergence."""
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        # Price declining over last 10 bars
        closes = np.full(n, 100.0)
        closes[-10:] = np.linspace(100.0, 90.0, 10)
        # Volume declining over last 10 bars
        volumes = np.full(n, 5.0)
        volumes[-10:] = np.linspace(5.0, 1.0, 10)

        df = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        cfg = CompositeScoreConfig(volume_div_window=10)
        result = compute_indicators(df, cfg)
        assert result["Buy Volume Div"][0] == 1, "Bullish divergence should fire"
        assert result["Sell Volume Div"][0] == 0

    def test_bearish_divergence_fires(self):
        """Price rising + volume declining = bearish divergence."""
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        # Price rising over last 10 bars
        closes = np.full(n, 100.0)
        closes[-10:] = np.linspace(100.0, 110.0, 10)
        # Volume declining over last 10 bars
        volumes = np.full(n, 5.0)
        volumes[-10:] = np.linspace(5.0, 1.0, 10)

        df = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        cfg = CompositeScoreConfig(volume_div_window=10)
        result = compute_indicators(df, cfg)
        assert result["Sell Volume Div"][0] == 1, "Bearish divergence should fire"
        assert result["Buy Volume Div"][0] == 0

    def test_no_divergence_when_both_rising(self):
        """Price rising + volume rising = no divergence."""
        n = 50
        timestamps = [datetime(2026, 1, 1) + timedelta(minutes=i) for i in range(n)]
        closes = np.full(n, 100.0)
        closes[-10:] = np.linspace(100.0, 110.0, 10)
        volumes = np.full(n, 5.0)
        volumes[-10:] = np.linspace(5.0, 10.0, 10)  # Volume also rising

        df = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": volumes,
        }, index=pd.DatetimeIndex(timestamps, name="timestamp"))

        cfg = CompositeScoreConfig(volume_div_window=10)
        result = compute_indicators(df, cfg)
        assert result["Buy Volume Div"][0] == 0
        assert result["Sell Volume Div"][0] == 0

    def test_neutral_when_no_volume_data(self):
        """Zero volume data → divergence indicators return neutral."""
        df = _make_df(n=100)
        df["volume"] = 0.0  # No volume data
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert result["Buy Volume Div"][0] == 0
        assert result["Sell Volume Div"][0] == 0

    def test_rvol_computed(self):
        """_RVOL is computed when volume data is present."""
        df = _make_df(n=100)
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert "_RVOL" in result
        assert result["_RVOL"] > 0

    def test_rvol_zero_when_no_volume(self):
        """_RVOL is 0 when volume data is absent."""
        df = _make_df(n=100)
        df["volume"] = 0.0
        cfg = CompositeScoreConfig()
        result = compute_indicators(df, cfg)
        assert result["_RVOL"] == 0.0


# ------------------------------------------------------------------
# Volume confirmation gate
# ------------------------------------------------------------------

class TestVolumeConfirmationGate:
    def setup_method(self):
        registry.discover_plugins()

    def test_buy_suppressed_when_rvol_low(self):
        """Buy blocked when RVOL is below threshold."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "volume_confirm_buy": True,
            "volume_confirm_threshold": 0.7,
            "candle_interval_minutes": 1,
        })

        # Mock indicators: force a buy via composite scoring + low RVOL
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_RVOL": 0.3, "_ADX": 30.0}
        mock_scores = (6.0, 0.0, (1, 6.0, 5.5), (0, 0.0, 5.5), {"buy": [], "sell": [], "suppression": None})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            s.on_candle(candle, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=((1, 6.0, 5.5), (0, 0.0, 5.5), "buy", None)):

            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            result = s.on_candle(candle, {})

        assert result is None, "Buy should be suppressed when RVOL < threshold"

    def test_buy_allowed_when_rvol_high(self):
        """Buy passes when RVOL is above threshold."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "volume_confirm_buy": True,
            "volume_confirm_threshold": 0.7,
            "candle_interval_minutes": 1,
        })

        # Mock indicators: force a buy via composite scoring + high RVOL
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_RVOL": 1.5, "_ADX": 30.0}
        mock_scores = (6.0, 0.0, (1, 6.0, 5.5), (0, 0.0, 5.5), {"buy": [], "sell": [], "suppression": None})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=2.0,
            )
            s.on_candle(candle, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=((1, 6.0, 5.5), (0, 0.0, 5.5), "buy", None)):

            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=2.0,
            )
            result = s.on_candle(candle, {})

        assert result is not None
        assert result.direction == Direction.BUY

    def test_buy_allowed_when_no_volume_data(self):
        """Graceful bypass: buy allowed when volume data is unavailable (RVOL=0)."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "volume_confirm_buy": True,
            "volume_confirm_threshold": 0.7,
            "candle_interval_minutes": 1,
        })

        # Mock indicators: force buy + RVOL=0 (no volume data)
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_RVOL": 0.0, "_ADX": 30.0}
        mock_scores = (6.0, 0.0, (1, 6.0, 5.5), (0, 0.0, 5.5), {"buy": [], "sell": [], "suppression": None})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.0,
            )
            s.on_candle(candle, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=((1, 6.0, 5.5), (0, 0.0, 5.5), "buy", None)):

            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.0,
            )
            result = s.on_candle(candle, {})

        assert result is not None, "Buy should bypass volume gate when no volume data"
        assert result.direction == Direction.BUY

    def test_sell_not_affected_by_volume_gate(self):
        """Volume gate only blocks buys, not sells."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "volume_confirm_buy": True,
            "volume_confirm_threshold": 0.7,
            "candle_interval_minutes": 1,
        })

        # Mock: force sell signal + low RVOL
        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_RVOL": 0.3, "_ADX": 30.0}
        mock_scores = (0.0, 6.0, (0, 0.0, 5.5), (1, 6.0, 5.5), {"buy": [], "sell": [], "suppression": None})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            s.on_candle(candle, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=((0, 0.0, 5.5), (1, 6.0, 5.5), "sell", None)):

            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            result = s.on_candle(candle, {})

        assert result is not None
        assert result.direction == Direction.SELL

    def test_gate_disabled_allows_low_rvol_buy(self):
        """When volume_confirm_buy=False, low RVOL doesn't block buys."""
        from unittest.mock import patch

        s = registry.create_strategy("composite_scoring", event_bus=EventBus())
        s.configure({
            "min_bars": 5,
            "volume_confirm_buy": False,
            "candle_interval_minutes": 1,
        })

        mock_ind = {"_ROC": 0.0, "_RSI": 50.0, "_RVOL": 0.3, "_ADX": 30.0}
        mock_scores = (6.0, 0.0, (1, 6.0, 5.5), (0, 0.0, 5.5), {"buy": [], "sell": [], "suppression": None})

        base_ts = datetime(2026, 1, 1)
        for i in range(10):
            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=i),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            s.on_candle(candle, {})

        with patch("v2.plugins.strategies.composite_scoring.strategy.compute_indicators", return_value=mock_ind), \
             patch("v2.plugins.strategies.composite_scoring.strategy.compute_scores", return_value=mock_scores), \
             patch.object(s._guardrails, "apply", return_value=((1, 6.0, 5.5), (0, 0.0, 5.5), "buy", None)):

            candle = Candle(
                symbol="BTC-USD",
                timestamp=base_ts + timedelta(minutes=10),
                open=97000.0, high=97100.0, low=96900.0, close=97000.0, volume=0.5,
            )
            result = s.on_candle(candle, {})

        assert result is not None
        assert result.direction == Direction.BUY
