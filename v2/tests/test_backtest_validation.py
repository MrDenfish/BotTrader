"""Trade-for-trade validation: v2 backtest vs Phase 1 backtest.

Runs the same strategy (hybrid_4h_maker) with the same config on the
same data through both engines and verifies identical results.
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)


def _run_phase1(config, symbol="BTC-USD", days=360):
    """Run Phase 1 backtest engine and return (stats, trades)."""
    from strategies.hybrid_4h_maker.strategy import Hybrid4hMakerStrategy
    from strategies.engines.backtest_engine import BacktestEngine

    strategy = Hybrid4hMakerStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        config=config,
        data_dir="backtest/data",
        resolution="1m",
    )
    engine.load([symbol], days=days)
    stats = engine.run()
    return stats, strategy.trades


def _run_v2(config, symbol="BTC-USD", days=360):
    """Run v2 backtest and return (stats, trades)."""
    from v2.core.app import App
    from v2.core import registry

    # Build config that points to the right plugins.
    # Both csv_replay (indicator calculation) and strategy need the Hybrid4hConfig.
    overrides = {
        "app": {"mode": "backtest", "symbols": [symbol]},
        "exchange": {"type": "backtest"},
        "data_providers": [{"type": "csv_replay", "data_dir": "backtest/data",
                            "days": days, "resolution": "1m",
                            "config": config}],
        "strategies": [{"type": "hybrid_4h_maker", "config": config}],
        "risk": {"type": "basic"},
        "execution": {"type": "maker_only"},
        "storage": {"type": "sqlite"},
        "observers": [],
    }

    app = App(overrides=overrides)
    result = asyncio.run(app.run())

    # Get trades from strategy
    strategy = app._strategies[0]
    trades = strategy.trades

    return result, trades


@pytest.fixture(scope="module")
def backtest_results():
    """Run both engines once and cache results for all tests in this module."""
    from backtest.config_4h_hybrid import get_phase2_3_roc_baseline_config
    config = get_phase2_3_roc_baseline_config()

    phase1_stats, phase1_trades = _run_phase1(config)
    v2_stats, v2_trades = _run_v2(config)

    return {
        "phase1_stats": phase1_stats,
        "phase1_trades": phase1_trades,
        "v2_stats": v2_stats,
        "v2_trades": v2_trades,
        "config": config,
    }


class TestTradeForTradeMatch:
    """Verify v2 produces identical results to Phase 1."""

    def test_same_trade_count(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]
        assert p1["trades"] == v2["trades"], (
            f"Trade count mismatch: Phase 1={p1['trades']}, v2={v2['trades']}"
        )

    def test_same_net_pnl(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]
        assert abs(p1["net_pnl"] - v2["net_pnl"]) < 0.01, (
            f"Net P&L mismatch: Phase 1={p1['net_pnl']:.4f}, v2={v2['net_pnl']:.4f}"
        )

    def test_same_gross_pnl(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]
        assert abs(p1["gross_pnl"] - v2["gross_pnl"]) < 0.01, (
            f"Gross P&L mismatch: Phase 1={p1['gross_pnl']:.4f}, v2={v2['gross_pnl']:.4f}"
        )

    def test_same_fees(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]
        assert abs(p1["fees"] - v2["fees"]) < 0.01, (
            f"Fees mismatch: Phase 1={p1['fees']:.4f}, v2={v2['fees']:.4f}"
        )

    def test_same_win_rate(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]
        if p1["trades"] > 0:
            assert abs(p1["win_rate"] - v2["win_rate"]) < 0.001, (
                f"Win rate mismatch: Phase 1={p1['win_rate']:.4f}, v2={v2['win_rate']:.4f}"
            )

    def test_trade_by_trade_entry_prices(self, backtest_results):
        p1_trades = backtest_results["phase1_trades"]
        v2_trades = backtest_results["v2_trades"]

        assert len(p1_trades) == len(v2_trades), "Different number of trades"

        for i, (p1, v2) in enumerate(zip(p1_trades, v2_trades)):
            assert abs(p1.entry_price - v2.entry_price) < 0.01, (
                f"Trade {i}: entry price mismatch "
                f"Phase 1={p1.entry_price}, v2={v2.entry_price}"
            )

    def test_trade_by_trade_net_pnl(self, backtest_results):
        p1_trades = backtest_results["phase1_trades"]
        v2_trades = backtest_results["v2_trades"]

        assert len(p1_trades) == len(v2_trades), "Different number of trades"

        for i, (p1, v2) in enumerate(zip(p1_trades, v2_trades)):
            assert abs(p1.net_pnl - v2.net_pnl) < 0.01, (
                f"Trade {i}: net P&L mismatch "
                f"Phase 1={p1.net_pnl:.4f}, v2={v2.net_pnl:.4f}"
            )

    def test_gate_audit_matches(self, backtest_results):
        p1 = backtest_results["phase1_stats"]
        v2 = backtest_results["v2_stats"]

        p1_audit = p1.get("gate_audit", {})
        v2_audit = v2.get("gate_audit", {})

        for key in ["total_4h_bars", "regime_ok", "viability_ok", "setup_created"]:
            if key in p1_audit and key in v2_audit:
                assert p1_audit[key] == v2_audit[key], (
                    f"Gate audit {key}: Phase 1={p1_audit[key]}, v2={v2_audit[key]}"
                )
