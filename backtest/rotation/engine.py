"""Daily-loop portfolio backtest for the momentum rotation strategy.

Implements spec sections 4-6 exactly: daily 'fast out' gate check,
weekly 'slow in' rebalance, per-side fee + slippage on every traded
notional, cash earns zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.rotation.universe import eligible_symbols
from v2.plugins.strategies.momentum_rotation.core import (
    daily_vol,
    inverse_vol_weights,
    market_filter,
    momentum_score,
    raw_return,
    select_holdings,
)


@dataclass
class RotationConfig:
    lookback: int = 60
    skip: int = 2
    band: int = 8
    k: int = 4
    cap: float = 0.30
    exposure: float = 1.0
    volume_floor: float = 10e6
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    ma_len: int = 200
    rebalance_weekday: int = 0
    min_age_days: int = 180
    top_n: int = 25


@dataclass
class BacktestResult:
    equity: pd.Series
    net_return: float
    max_drawdown: float
    n_trades: int
    days_in_market: int
    days_total: int
    trades: list[dict] = field(default_factory=list)


class RotationBacktest:
    """Daily-loop simulator: fast-out gate, weekly rebalance, per-trade costs.

    Note: a held position whose bars stop updating (delisting/halt) is
    valued at its last-known close until the next rebalance drops it from
    the target set, so its eventual exit fill is an approximation at that
    stale price. The `max_stale_days` guard in `eligible_symbols` bounds
    how long a symbol can be newly selected or re-entered while stale,
    but does not retroactively re-price an already-held stale position.
    """

    def __init__(
        self, bars: dict[str, pd.DataFrame], btc_symbol: str, cfg: RotationConfig
    ) -> None:
        self._bars = bars
        self._btc = btc_symbol
        self._cfg = cfg

    def _closes_upto(self, symbol: str, day: pd.Timestamp) -> pd.Series:
        df = self._bars[symbol]
        return df.loc[df.index <= day, "close"]

    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
        cfg = self._cfg
        cost = cfg.fee_per_side + cfg.slippage_per_side
        days = self._bars[self._btc].loc[start:end].index

        cash = 1.0
        pos: dict[str, float] = {}       # symbol -> units
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

        def sell(sym: str, notional: float, day: pd.Timestamp, eq_ref: float) -> None:
            """Sell up to `notional` dollars of `sym`'s current position."""
            nonlocal cash
            p = price(sym, day)
            held = pos.get(sym, 0.0)
            if p is None or held <= 0 or notional <= 0:
                return
            notional = min(notional, held * p)
            units = notional / p
            remaining = held - units
            if remaining <= 1e-9:
                pos.pop(sym, None)
            else:
                pos[sym] = remaining
            cash += notional * (1.0 - cost)
            trades.append({"date": day, "symbol": sym, "side": "sell",
                           "weight": notional / max(eq_ref, 1e-12)})

        def buy(sym: str, notional: float, day: pd.Timestamp, eq_ref: float) -> None:
            """Buy `notional` dollars of `sym`, funded entirely from cash."""
            nonlocal cash
            p = price(sym, day)
            if p is None or notional <= 0 or notional > cash:
                return
            cash -= notional
            pos[sym] = pos.get(sym, 0.0) + (notional * (1.0 - cost)) / p
            trades.append({"date": day, "symbol": sym, "side": "buy",
                           "weight": notional / max(eq_ref, 1e-12)})

        for day in days:
            risk_on = market_filter(self._closes_upto(self._btc, day), cfg.ma_len)
            eq = equity(day)

            # Fast out: any non-True gate liquidates everything immediately.
            if risk_on is not True and pos:
                for sym in list(pos):
                    p = price(sym, day)
                    notional = pos[sym] * p if p is not None else 0.0
                    sell(sym, notional, day, eq)

            # Slow in: entries/rotation only on rebalance day while risk-on.
            if risk_on is True and day.weekday() == cfg.rebalance_weekday:
                universe = eligible_symbols(
                    self._bars, day, cfg.volume_floor,
                    min_age_days=cfg.min_age_days, top_n=cfg.top_n,
                )
                scores: dict[str, float] = {}
                for sym in universe:
                    closes = self._closes_upto(sym, day)
                    rr = raw_return(closes, cfg.lookback, cfg.skip)
                    sc = momentum_score(closes, cfg.lookback, cfg.skip)
                    if rr is not None and rr > 0 and sc is not None:
                        scores[sym] = sc
                ranked = sorted(scores, key=scores.get, reverse=True)
                target_syms = select_holdings(ranked, list(pos), cfg.k, cfg.band)
                vols = {
                    s: daily_vol(self._closes_upto(s, day), cfg.lookback, cfg.skip)
                    for s in target_syms
                }
                weights = {
                    s: w * cfg.exposure
                    for s, w in inverse_vol_weights(vols, cfg.cap).items()
                }

                eq = equity(day)
                threshold = 0.005 * eq

                # Sells first (including full exits), then buys with freed cash.
                for sym in list(pos):
                    p = price(sym, day)
                    if p is None:
                        continue
                    cur = pos[sym] * p
                    tgt = weights.get(sym, 0.0) * eq
                    if cur - tgt > threshold:
                        sell(sym, cur - tgt, day, eq)
                for sym, w in weights.items():
                    p = price(sym, day)
                    if p is None:
                        continue
                    cur = pos.get(sym, 0.0) * p
                    tgt = w * eq
                    if tgt - cur > threshold:
                        buy(sym, min(tgt - cur, cash), day, eq)

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
