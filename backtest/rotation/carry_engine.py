"""Daily-loop backtest for the regime-gated carry strategy.

Spec: docs/superpowers/specs/2026-07-28-regime-gated-carry-design.md
sections 2-5. Key mechanics: dual-layer 200d gates evaluated daily
("fast out"), entries only at the weekly rebalance ("slow in"), ALL
sells lagged one bar (manual-unstake handicap), no-redistribution
sleeve weights, cash earns zero. No staking yield is modeled anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.rotation.engine import BacktestResult
from v2.plugins.strategies.momentum_rotation.core import daily_vol, market_filter
from v2.plugins.strategies.regime_carry.core import carry_targets, sleeve_trades


@dataclass
class CarryConfig:
    scheme: str = "inverse_vol"
    ma_len: int = 200
    cap: float = 0.50
    drift_band: float = 0.20
    exposure: float = 1.0
    vol_lookback: int = 60
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    rebalance_weekday: int = 0
    universe: tuple = ("BTC-USD", "ETH-USD", "SOL-USD")
    btc: str = "BTC-USD"


class CarryBacktest:
    def __init__(self, bars: dict[str, pd.DataFrame], cfg: CarryConfig) -> None:
        self._bars = bars
        self._cfg = cfg

    def _closes_upto(self, symbol: str, day: pd.Timestamp) -> pd.Series:
        df = self._bars[symbol]
        return df.loc[df.index <= day, "close"]

    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
        cfg = self._cfg
        cost = cfg.fee_per_side + cfg.slippage_per_side
        days = self._bars[cfg.btc].loc[start:end].index

        cash = 1.0
        pos: dict[str, float] = {}                 # symbol -> units
        queued_sells: dict[str, float] = {}        # symbol -> units to sell next bar
        trades: list[dict] = []
        equity_curve: dict[pd.Timestamp, float] = {}
        in_market_days = 0

        def price(sym: str, day: pd.Timestamp) -> float | None:
            s = self._closes_upto(sym, day)
            return float(s.iloc[-1]) if len(s) else None

        def equity(day: pd.Timestamp) -> float:
            total = cash
            for sym, units in pos.items():
                p = price(sym, day)
                if p is not None:
                    total += units * p
            return total

        for day in days:
            # 1) execute sells queued on the previous bar (exit lag)
            if queued_sells:
                eq_before = equity(day)
                for sym, units in list(queued_sells.items()):
                    p = price(sym, day)
                    if p is None:
                        continue
                    units = min(units, pos.get(sym, 0.0))
                    if units <= 0:
                        queued_sells.pop(sym)
                        continue
                    notional = units * p
                    cash += notional * (1.0 - cost)
                    pos[sym] = pos.get(sym, 0.0) - units
                    if pos[sym] <= 1e-12:
                        pos.pop(sym, None)
                    trades.append({"date": day, "symbol": sym, "side": "sell",
                                   "weight": notional / max(eq_before, 1e-12)})
                    queued_sells.pop(sym)

            # 2) gates from data <= day
            master = market_filter(self._closes_upto(cfg.btc, day), cfg.ma_len)
            gate = {
                s: market_filter(self._closes_upto(s, day), cfg.ma_len)
                for s in cfg.universe
            }

            # 3) daily exit signals -> queue for next bar
            for sym in list(pos):
                if master is not True or gate.get(sym) is not True:
                    if sym not in queued_sells:
                        queued_sells[sym] = pos[sym]

            # 4) weekly entries / drift rebalance
            if day.weekday() == cfg.rebalance_weekday and master is True:
                eligible = {s for s in cfg.universe if gate.get(s) is True}
                vols = {
                    s: daily_vol(self._closes_upto(s, day), cfg.vol_lookback, 0)
                    for s in cfg.universe
                }
                targets = carry_targets(
                    vols, eligible, cfg.universe, cfg.scheme, cfg.cap, cfg.exposure
                )
                eq = equity(day)
                current = {
                    s: (pos.get(s, 0.0) * (price(s, day) or 0.0)) / max(eq, 1e-12)
                    for s in cfg.universe
                }
                deltas = sleeve_trades(current, targets, cfg.drift_band)
                for sym, dw in deltas.items():
                    p = price(sym, day)
                    if p is None or abs(dw) < 1e-12:
                        continue
                    if dw < 0:
                        units = min((-dw) * eq / p, pos.get(sym, 0.0))
                        if units > 0 and sym not in queued_sells:
                            queued_sells[sym] = units
                    elif sym not in queued_sells:
                        notional = min(dw * eq, cash)
                        if notional > 0:
                            cash -= notional
                            pos[sym] = pos.get(sym, 0.0) + notional * (1.0 - cost) / p
                            trades.append({"date": day, "symbol": sym, "side": "buy",
                                           "weight": notional / max(eq, 1e-12)})

            if pos:
                in_market_days += 1
            equity_curve[day] = equity(day)

        eq_series = pd.Series(equity_curve).sort_index()
        peak = eq_series.cummax()
        max_dd = float(((peak - eq_series) / peak).max()) if len(eq_series) else 0.0
        return BacktestResult(
            equity=eq_series,
            net_return=float(eq_series.iloc[-1] - 1.0),
            max_drawdown=max_dd,
            n_trades=len(trades),
            days_in_market=in_market_days,
            days_total=len(days),
            trades=trades,
        )
