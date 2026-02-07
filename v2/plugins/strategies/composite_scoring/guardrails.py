"""Guardrails: hysteresis, cooldown, and conflict resolution.

Ported from the guardrail section of sighook/signal_manager.buy_sell_scoring().
Maintains per-symbol state for position-direction tracking and cooldown.
"""

from __future__ import annotations

from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig


class Guardrails:
    """Stateful guardrails for the composite scoring strategy.

    Tracks per-symbol position direction and cooldown bar index.
    """

    def __init__(self) -> None:
        self._last_side: dict[str, str] = {}      # "long" | "short"
        self._cooldown_until: dict[str, int] = {}  # bar index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        symbol: str,
        buy_signal: tuple,
        sell_signal: tuple,
        buy_score: float,
        sell_score: float,
        bar_idx: int,
        cfg: CompositeScoreConfig,
    ) -> tuple[tuple, tuple, str, str | None]:
        """Apply hysteresis, cooldown, resolve conflicts, update state.

        Returns
        -------
        (buy_signal, sell_signal, action, guardrail_note)
        """
        note: str | None = None

        # --- Hysteresis ---
        hyst = 1.0 + max(0.0, cfg.flip_hysteresis_pct)
        last_side = self._last_side.get(symbol)

        if last_side == "long" and sell_signal[0] == 1:
            if sell_score < (cfg.score_sell_target * hyst):
                note = "sell_suppressed_by_hysteresis"
                sell_signal = (0, sell_signal[1], sell_signal[2])
        elif last_side == "short" and buy_signal[0] == 1:
            if buy_score < (cfg.score_buy_target * hyst):
                note = "buy_suppressed_by_hysteresis"
                buy_signal = (0, buy_signal[1], buy_signal[2])

        # --- Cooldown ---
        cd_until = self._cooldown_until.get(symbol, -1)
        if bar_idx < cd_until:
            if last_side == "long":
                note = "sell_suppressed_by_cooldown"
                sell_signal = (0, sell_signal[1], sell_signal[2])
            elif last_side == "short":
                note = "buy_suppressed_by_cooldown"
                buy_signal = (0, buy_signal[1], buy_signal[2])

        # --- Conflict resolution ---
        if buy_signal[0] == 1 and sell_signal[0] == 0:
            action = "buy"
        elif sell_signal[0] == 1 and buy_signal[0] == 0:
            action = "sell"
        elif buy_signal[0] == 1 and sell_signal[0] == 1:
            action = "buy" if buy_score > sell_score else "sell"
        else:
            action = "hold"

        # --- State update ---
        prev_side = self._last_side.get(symbol)
        curr_side = (
            "long" if action == "buy"
            else ("short" if action == "sell" else prev_side)
        )
        if prev_side != curr_side and action in ("buy", "sell"):
            self._last_side[symbol] = curr_side
            self._cooldown_until[symbol] = bar_idx + max(0, cfg.cooldown_bars)

        return buy_signal, sell_signal, action, note

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "last_side": dict(self._last_side),
            "cooldown_until": dict(self._cooldown_until),
        }

    def load_state(self, state: dict) -> None:
        self._last_side = dict(state.get("last_side", {}))
        self._cooldown_until = dict(state.get("cooldown_until", {}))
