"""Pure carry allocation math — no I/O, no event bus.

Shared by the carry backtest engine (backtest/rotation/carry_engine.py)
and the future live regime_carry strategy plugin.

No-redistribution rule (spec section 3): base weights are computed over
the FULL universe, then gated-out sleeves are zeroed. Weight freed by a
gated-out asset becomes cash, never a larger allocation to survivors.
"""
from __future__ import annotations

from v2.plugins.strategies.momentum_rotation.core import inverse_vol_weights


def carry_targets(
    vols: dict[str, float | None],
    eligible: set[str],
    universe: tuple[str, ...],
    scheme: str,
    cap: float = 0.50,
    exposure: float = 1.0,
) -> dict[str, float]:
    if scheme == "equal":
        base = {s: min(1.0 / len(universe), cap) for s in universe}
    elif scheme == "inverse_vol":
        base = inverse_vol_weights({s: vols.get(s) for s in universe}, cap=cap)
    else:
        raise ValueError(f"unknown scheme: {scheme!r}")

    out: dict[str, float] = {}
    for s in universe:
        v = vols.get(s)
        gated_in = s in eligible and v is not None and v > 0
        out[s] = base.get(s, 0.0) * exposure if gated_in else 0.0
    return out


def sleeve_trades(
    current: dict[str, float],
    target: dict[str, float],
    band: float = 0.20,
) -> dict[str, float]:
    """Per-symbol weight delta to trade, with drift-band suppression.

    Exits (target == 0, current held) always trade — the band never
    suppresses a gate-mandated exit.
    """
    out: dict[str, float] = {}
    for s in set(current) | set(target):
        cur = current.get(s, 0.0)
        tgt = target.get(s, 0.0)
        if tgt <= 0.0:
            out[s] = -cur if cur > 1e-9 else 0.0
        elif abs(cur - tgt) > band * tgt:
            out[s] = tgt - cur
        else:
            out[s] = 0.0
    return out
