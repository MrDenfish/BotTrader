"""Tests for mean_reversion_v3 strategy plugin and the exit_manager features it depends on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import Portfolio, Position, TickerEvent
from v2.plugins.strategies.mean_reversion_v3.config import MeanReversionV3Config
from v2.plugins.strategies.mean_reversion_v3.indicators import (
    _compute_supertrend,
    compute_indicators,
)
from v2.plugins.strategies.mean_reversion_v3.scoring import compute_buy_score


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture(autouse=True)
def _discover():
    registry.discover_plugins()
    yield


@pytest.fixture
def bus():
    return EventBus()


def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """Build an OHLCV DataFrame from a closes series."""
    n = len(closes)
    if volumes is None:
        volumes = [1000.0] * n
    ts = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    df = pd.DataFrame({
        "open": closes,
        "high": [c * 1.002 for c in closes],
        "low": [c * 0.998 for c in closes],
        "close": closes,
        "volume": volumes,
    }, index=ts)
    return df


def _make_portfolio(symbol="BTC-USD", qty=0.001, avg_entry=100.0, entry_offset_min=0) -> Portfolio:
    return Portfolio(
        cash_balance=Decimal("10000"),
        positions={
            symbol: Position(
                symbol=symbol,
                qty=Decimal(str(qty)),
                avg_entry_price=Decimal(str(avg_entry)),
                cost_basis=Decimal(str(qty * avg_entry)),
                entry_time=datetime.now(timezone.utc) - timedelta(minutes=entry_offset_min),
            ),
        },
    )


def _make_ticker(symbol="BTC-USD", price=100.0) -> TickerEvent:
    return TickerEvent(
        symbol=symbol,
        price=price,
        timestamp=datetime.now(timezone.utc),
    )


# =====================================================================
# Supertrend
# =====================================================================


class TestSupertrend:
    def test_returns_uptrend_on_rising_series(self):
        # Strong uptrend: closes climb steadily
        closes = [100 + i * 0.5 for i in range(50)]
        df = _make_df(closes)
        line, direction = _compute_supertrend(df, period=10, multiplier=3.0)
        # Last bar should be in uptrend (direction == +1)
        assert direction.iloc[-1] == 1

    def test_returns_downtrend_on_falling_series(self):
        closes = [100 - i * 0.5 for i in range(50)]
        df = _make_df(closes)
        _, direction = _compute_supertrend(df, period=10, multiplier=3.0)
        assert direction.iloc[-1] == -1

    def test_handles_insufficient_data(self):
        df = _make_df([100, 101, 102])
        line, direction = _compute_supertrend(df, period=10, multiplier=3.0)
        # Should not crash; returns NaN/0
        assert len(line) == 3


# =====================================================================
# Indicator computation
# =====================================================================


class TestIndicators:
    def test_returns_expected_keys(self):
        cfg = MeanReversionV3Config()
        # Need enough bars for all indicators
        closes = [100 + np.sin(i / 5) * 2 for i in range(100)]
        df = _make_df(closes)
        ind = compute_indicators(df, cfg)
        assert "Buy Touch" in ind
        assert "Buy RSI" in ind
        assert "Buy ROC" in ind
        assert "Buy MACD" in ind
        assert "W-Bottom" in ind
        assert "Buy Volume Div" in ind
        # Must NOT include the dropped indicators
        assert "Buy Swing" not in ind
        assert "Buy Ratio" not in ind
        # Raw values
        assert "_RSI" in ind
        assert "_ADX" in ind
        assert "_Supertrend_dir" in ind

    def test_returns_empty_when_insufficient_bars(self):
        cfg = MeanReversionV3Config()
        df = _make_df([100, 101, 102])
        assert compute_indicators(df, cfg) == {}


# =====================================================================
# Scoring
# =====================================================================


class TestScoring:
    def test_score_sums_weighted_decisions(self):
        cfg = MeanReversionV3Config()
        # Manually craft an indicator dict where Touch and RSI fire (weights 2.0 each)
        ind = {
            "Buy Touch": (1, 0.0, 0.0),
            "Buy RSI": (1, 0.0, 0.0),
            "W-Bottom": (0, 0.0, 0.0),
            "Buy Volume Div": (0, 0.0, 0.0),
            "Buy MACD": (0, 0.0, 0.0),
            "Buy ROC": (0, 0.0, 0.0),
        }
        score, signal, components = compute_buy_score(ind, cfg)
        assert score == 4.0  # 2.0 + 2.0
        # Signal should be suppressed: only 2 indicators fired, need 3
        assert signal[0] == 0
        assert "insufficient_indicators" in components["suppression"]

    def test_signal_fires_when_threshold_and_count_met(self):
        cfg = MeanReversionV3Config()
        ind = {
            "Buy Touch": (1, 0.0, 0.0),       # 2.0
            "Buy RSI": (1, 0.0, 0.0),          # 2.0
            "W-Bottom": (0, 0.0, 0.0),
            "Buy Volume Div": (1, 0.0, 0.0),   # 1.8
            "Buy MACD": (0, 0.0, 0.0),
            "Buy ROC": (0, 0.0, 0.0),
        }
        score, signal, components = compute_buy_score(ind, cfg)
        assert score == 5.8
        assert signal[0] == 1  # Fires: above 3.0 target, 3 indicators
        assert components["suppression"] is None


# =====================================================================
# Strategy gates (Stage 1)
# =====================================================================


class TestStrategyGates:
    def test_blocks_when_supertrend_downtrend(self):
        # Falling closes -> Supertrend downtrend
        closes = [100 - i * 0.3 for i in range(120)]
        df = _make_df(closes)
        cfg = MeanReversionV3Config()
        ind = compute_indicators(df, cfg)
        assert ind.get("_Supertrend_dir") == -1

    def test_passes_when_supertrend_uptrend(self):
        closes = [100 + i * 0.3 for i in range(120)]
        df = _make_df(closes)
        cfg = MeanReversionV3Config()
        ind = compute_indicators(df, cfg)
        assert ind.get("_Supertrend_dir") == 1


# =====================================================================
# Exit Manager — new features
# =====================================================================


def _make_exit_manager(bus, **overrides):
    defaults = {
        "hard_stop_pct": 0.055,
        "soft_stop_enabled": False,
        "trailing_enabled": False,
        "check_interval_sec": 0,
        "maker_fee_pct": 0.0,
        "taker_fee_pct": 0.0,
    }
    defaults.update(overrides)
    return registry.create_risk("exit_manager", event_bus=bus, **defaults)


class TestBreakevenStop:
    def test_does_not_trigger_below_breakeven_trigger(self, bus):
        em = _make_exit_manager(bus, breakeven_trigger_pct=0.007)
        portfolio = _make_portfolio(avg_entry=100.0)
        # Price moves up 0.5% — below the 0.7% trigger
        em.on_ticker(_make_ticker(price=100.5), portfolio)
        assert "BTC-USD" not in em._breakeven_active or not em._breakeven_active["BTC-USD"]

    def test_activates_at_trigger(self, bus):
        em = _make_exit_manager(bus, breakeven_trigger_pct=0.007)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=100.7), portfolio)  # +0.7%
        assert em._breakeven_active.get("BTC-USD") is True

    def test_exits_when_price_returns_to_entry_after_activation(self, bus):
        em = _make_exit_manager(bus, breakeven_trigger_pct=0.007)
        portfolio = _make_portfolio(avg_entry=100.0)
        # Activate
        em.on_ticker(_make_ticker(price=100.7), portfolio)
        assert em._breakeven_active.get("BTC-USD") is True
        # Drop back to entry — should trigger breakeven_stop
        em.on_ticker(_make_ticker(price=100.0), portfolio)
        assert "BTC-USD" in em._pending_exits

    def test_disabled_when_trigger_zero(self, bus):
        em = _make_exit_manager(bus, breakeven_trigger_pct=0.0)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=110.0), portfolio)  # +10%
        assert em._breakeven_active.get("BTC-USD", False) is False


class TestFixedTakeProfit:
    def test_triggers_at_threshold(self, bus):
        em = _make_exit_manager(bus, fixed_take_profit_pct=0.0175)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=101.75), portfolio)  # +1.75%
        assert "BTC-USD" in em._pending_exits

    def test_does_not_trigger_below_threshold(self, bus):
        em = _make_exit_manager(bus, fixed_take_profit_pct=0.0175)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=101.0), portfolio)  # +1.0%
        assert "BTC-USD" not in em._pending_exits

    def test_disabled_when_threshold_zero(self, bus):
        em = _make_exit_manager(bus, fixed_take_profit_pct=0.0)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=110.0), portfolio)  # +10%, no TP set
        assert "BTC-USD" not in em._pending_exits


class TestStaleExitRegardlessOfPnl:
    def test_legacy_only_negative_pnl(self, bus):
        em = _make_exit_manager(
            bus,
            max_hold_hours=1,
            stale_exit_regardless_of_pnl=False,
        )
        # Position held 2 hours, P&L positive — legacy behavior keeps it
        portfolio = _make_portfolio(avg_entry=100.0, entry_offset_min=120)
        em.on_ticker(_make_ticker(price=101.0), portfolio)  # +1%
        assert "BTC-USD" not in em._pending_exits

    def test_regardless_flag_exits_on_positive_pnl(self, bus):
        em = _make_exit_manager(
            bus,
            max_hold_hours=1,
            stale_exit_regardless_of_pnl=True,
        )
        portfolio = _make_portfolio(avg_entry=100.0, entry_offset_min=120)
        em.on_ticker(_make_ticker(price=101.0), portfolio)  # +1%
        assert "BTC-USD" in em._pending_exits


class TestTrailingDisabled:
    def test_trailing_does_not_activate_when_disabled(self, bus):
        em = _make_exit_manager(
            bus,
            trailing_enabled=False,
            trailing_activation_pct=0.02,
        )
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_ticker(_make_ticker(price=110.0), portfolio)  # +10%, would normally activate
        assert em._trailing_active.get("BTC-USD", False) is False
