"""Pure carry allocation math — no I/O, no event bus.

Shared by the carry backtest engine (backtest/rotation/carry_engine.py)
and the future live regime_carry strategy plugin.

No-redistribution rule (spec section 3): base weights are computed over
the FULL universe, then gated-out sleeves are zeroed. Weight freed by a
gated-out asset becomes cash, never a larger allocation to survivors.

Reduced-book convention (spec section 7): an asset with no computable
volatility (vol is None or <= 0 — e.g. insufficient price history) is
not part of the book at all. Base weights are computed over the
*effective universe* of assets that have valid vol data, not the full
universe — earlier eras where a symbol's data hasn't begun yet
naturally run a smaller, fully-deployed book. This is distinct from
the no-redistribution rule above, which applies only to gate-closures
of assets that DO have data but fail the eligibility gate: those
sleeves' weight is zeroed and freed to cash, never redistributed to
survivors.
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
    effective = tuple(s for s in universe if vols.get(s) is not None and vols[s] > 0)
    if not effective:
        return {s: 0.0 for s in universe}

    if scheme == "equal":
        base = {s: min(1.0 / len(effective), cap) for s in effective}
    elif scheme == "inverse_vol":
        base = inverse_vol_weights({s: vols.get(s) for s in effective}, cap=cap)
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
