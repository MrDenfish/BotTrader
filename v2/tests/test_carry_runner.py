import pandas as pd

from backtest.rotation.carry_engine import CarryConfig
from backtest.rotation.engine import BacktestResult
from backtest.rotation.run_carry_gauntlet import pick_winner


def _res(net, dd):
    return BacktestResult(equity=pd.Series([1.0, 1.0 + net]), net_return=net,
                          max_drawdown=dd, n_trades=1, days_in_market=1,
                          days_total=2, trades=[])


def _row(scheme, net, dd):
    return {"cfg": CarryConfig(scheme=scheme), "exposure": 0.5,
            "fit": _res(net, dd), "validate": _res(net / 2, dd / 2)}


class TestPickWinner:
    def test_higher_fit_net_wins(self):
        rows = [_row("equal", 0.20, 0.13), _row("inverse_vol", 0.30, 0.13)]
        assert pick_winner(rows)["cfg"].scheme == "inverse_vol"

    def test_dd_ineligible_excluded(self):
        rows = [_row("equal", 0.20, 0.13), _row("inverse_vol", 0.90, 0.40)]
        assert pick_winner(rows)["cfg"].scheme == "equal"

    def test_none_when_nothing_qualifies(self):
        rows = [_row("equal", 0.20, 0.30), _row("inverse_vol", 0.10, 0.40)]
        assert pick_winner(rows) is None
