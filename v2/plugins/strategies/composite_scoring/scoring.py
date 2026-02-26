"""Weighted scoring and multi-indicator confirmation gate.

Ported from the scoring section of sighook/signal_manager.buy_sell_scoring().
"""

from __future__ import annotations

from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig


def compute_scores(
    indicators: dict,
    cfg: CompositeScoreConfig,
) -> tuple[float, float, tuple, tuple, dict]:
    """Compute weighted buy/sell scores from indicator results.

    Parameters
    ----------
    indicators : dict from ``compute_indicators()``
    cfg : Strategy configuration.

    Returns
    -------
    (buy_score, sell_score, buy_signal, sell_signal, components)

    ``buy_signal`` / ``sell_signal`` are (decision, score, target) tuples.
    ``components`` has keys ``"buy"`` and ``"sell"`` — lists of per-indicator dicts.
    """
    buy_score = 0.0
    sell_score = 0.0
    buy_components: list[dict] = []
    sell_components: list[dict] = []

    for name, weight in cfg.weights.items():
        raw = indicators.get(name)
        if not isinstance(raw, tuple) or len(raw) != 3:
            continue

        decision = int(raw[0])
        value = float(raw[1] if raw[1] is not None else 0.0)
        threshold = float(raw[2] if raw[2] is not None else 0.0)
        contribution = decision * weight

        row = {
            "indicator": name,
            "decision": decision,
            "value": value,
            "threshold": threshold,
            "weight": weight,
            "contribution": round(contribution, 6),
        }

        if name.startswith("Buy") or name == "W-Bottom":
            buy_score += contribution
            buy_components.append(row)
        if name.startswith("Sell") or name == "M-Top":
            sell_score += contribution
            sell_components.append(row)

    buy_score = round(buy_score, 6)
    sell_score = round(sell_score, 6)

    # Signal tuples
    buy_signal = (
        (1, round(buy_score, 3), cfg.score_buy_target)
        if buy_score >= cfg.score_buy_target
        else (0, round(buy_score, 3), cfg.score_buy_target)
    )
    sell_signal = (
        (1, round(sell_score, 3), cfg.score_sell_target)
        if sell_score >= cfg.score_sell_target
        else (0, round(sell_score, 3), cfg.score_sell_target)
    )

    # Multi-indicator confirmation gate
    buy_fired = sum(
        1 for name in cfg.weights
        if (name.startswith("Buy") or name == "W-Bottom")
        and indicators.get(name, (0,))[0] == 1
    )
    sell_fired = sum(
        1 for name in cfg.weights
        if (name.startswith("Sell") or name == "M-Top")
        and indicators.get(name, (0,))[0] == 1
    )

    suppression_note = None

    if buy_signal[0] == 1 and buy_fired < cfg.min_indicators_required:
        suppression_note = (
            f"buy_suppressed_insufficient_indicators_{buy_fired}_of_{cfg.min_indicators_required}"
        )
        buy_signal = (0, buy_signal[1], buy_signal[2])

    sell_min = cfg.min_sell_indicators_required or cfg.min_indicators_required
    if sell_signal[0] == 1 and sell_fired < sell_min:
        suppression_note = (
            f"sell_suppressed_insufficient_indicators_{sell_fired}_of_{sell_min}"
        )
        sell_signal = (0, sell_signal[1], sell_signal[2])

    components = {
        "buy": buy_components,
        "sell": sell_components,
        "suppression": suppression_note,
    }

    return buy_score, sell_score, buy_signal, sell_signal, components
