"""Composite scoring for mean_reversion_v3.

Buy-side only — sells are handled by exit_manager (fixed TP, hard stop,
breakeven, time limit). Scoring is a weighted sum over the 6 retained
indicators. The buy_score must clear ``score_buy_target`` AND at least
``min_indicators_required`` distinct buy indicators must fire.
"""

from __future__ import annotations

from v2.plugins.strategies.mean_reversion_v3.config import MeanReversionV3Config


def compute_buy_score(
    indicators: dict,
    cfg: MeanReversionV3Config,
) -> tuple[float, tuple, dict]:
    """Compute weighted buy score and signal tuple.

    Returns
    -------
    (buy_score, buy_signal, components)
        buy_signal is (decision, score, target). components has the
        per-indicator breakdown plus buy_fired count and suppression note.
    """
    buy_score = 0.0
    components: list[dict] = []

    for name, weight in cfg.weights.items():
        raw = indicators.get(name)
        if not isinstance(raw, tuple) or len(raw) != 3:
            continue
        decision = int(raw[0])
        value = float(raw[1] if raw[1] is not None else 0.0)
        threshold = float(raw[2] if raw[2] is not None else 0.0)
        contribution = decision * weight
        components.append({
            "indicator": name,
            "decision": decision,
            "value": value,
            "threshold": threshold,
            "weight": weight,
            "contribution": round(contribution, 6),
        })
        buy_score += contribution

    buy_score = round(buy_score, 6)
    buy_signal = (
        (1, round(buy_score, 3), cfg.score_buy_target)
        if buy_score >= cfg.score_buy_target
        else (0, round(buy_score, 3), cfg.score_buy_target)
    )

    buy_fired = sum(1 for c in components if c["decision"] == 1)

    suppression_note: str | None = None
    if buy_signal[0] == 1 and buy_fired < cfg.min_indicators_required:
        suppression_note = (
            f"buy_suppressed_insufficient_indicators_{buy_fired}_of_{cfg.min_indicators_required}"
        )
        buy_signal = (0, buy_signal[1], buy_signal[2])

    return buy_score, buy_signal, {
        "buy": components,
        "buy_fired": buy_fired,
        "suppression": suppression_note,
    }
