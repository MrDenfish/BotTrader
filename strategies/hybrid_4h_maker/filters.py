"""
Filters for the 4h Hybrid Maker Strategy.

Extracted from Hybrid4hStrategy: regime filter, viability filter,
setup detection (Donchian, ROC-score), and compression checks.
"""

from typing import Optional
import pandas as pd


def check_regime_filter(bar: 'OHLCV', indicators_1d: dict, config) -> bool:
    """
    Enhanced regime filter with slope options and softening band.

    Checks close > EMA200 (with optional softening band and slope filters).
    """
    close = bar.close
    ema200 = indicators_1d.get('ema200', 0)

    if ema200 == 0:
        return False

    band = getattr(config, 'regime_band_pct', 0.0)

    if close > ema200:
        price_ok = True
    elif band > 0:
        ema200_slope = indicators_1d.get('ema200_slope', 0)
        price_ok = (close > ema200 * (1 - band)) and (ema200_slope >= 0)
    else:
        price_ok = False

    if not price_ok:
        return False

    if getattr(config, 'use_ema200_slope_filter', False):
        ema200_slope = indicators_1d.get('ema200_slope', 0)
        if ema200_slope <= 0:
            return False

    if getattr(config, 'use_ema50_slope_filter', False):
        ema50_slope = indicators_1d.get('ema50_slope', 0)
        if ema50_slope <= 0:
            return False

    return True


def check_viability_filter(
    indicators_4h: dict,
    config,
    bb_width_pct_history: list = None,
    check_recent_compression_fn=None,
) -> tuple[bool, bool]:
    """
    Enhanced viability with conditional threshold.

    Returns:
        (viability_ok, was_rescued)
    """
    atr_pct = indicators_4h.get('atr_pct', 0)
    fee_rt = config.round_trip_maker_fee

    threshold_normal = config.vol_min_mult * fee_rt
    viability_normal = atr_pct >= threshold_normal

    if viability_normal:
        return (True, False)

    if getattr(config, 'use_conditional_viability', False):
        if bb_width_pct_history is not None and len(bb_width_pct_history) >= 6:
            if check_recent_compression_fn is not None:
                compression_recent = check_recent_compression_fn(
                    bb_width_pct_history=bb_width_pct_history,
                    threshold=config.bb_width_pct_threshold,
                    lookback=6
                )
            else:
                compression_recent = check_recent_compression(
                    bb_width_pct_history=bb_width_pct_history,
                    threshold=config.bb_width_pct_threshold,
                    lookback=6
                )

            if compression_recent:
                threshold_compression = config.vol_min_mult_compression * fee_rt
                viability_compression = atr_pct >= threshold_compression

                if viability_compression:
                    return (True, True)

    return (False, False)


def check_donchian_setup(
    bar: 'OHLCV',
    indicators_4h: dict,
    bb_width_pct_history: list[float],
    config,
) -> tuple[bool, float]:
    """
    Check if Donchian breakout occurred with compression filter.
    """
    close = bar.close
    donch_high_prev = indicators_4h.get('donch_high_prev', 0)

    if donch_high_prev == 0:
        return False, 0.0

    if close > donch_high_prev:
        if config.use_compression_filter:
            is_compressed = check_recent_compression(
                bb_width_pct_history=bb_width_pct_history,
                threshold=config.bb_width_pct_threshold,
                lookback=config.compression_lookback_4h
            )
            if not is_compressed:
                return False, 0.0

        return True, donch_high_prev

    return False, 0.0


def check_roc_score_setup(
    bar: 'OHLCV',
    indicators_4h: dict,
    config,
    bb_width_pct_history: list[float] = None,
) -> tuple[bool, float]:
    """
    Check if ROC-score setup is triggered.

    Momentum-based setup using normalized rate of change.
    """
    close = bar.close
    roc_score = indicators_4h.get('roc_score_4h', 0)

    if roc_score < config.roc_score_thresh:
        return False, 0.0

    return True, close


def check_compression_only(
    indicators_4h: dict,
    bb_width_pct_history: list[float],
    config,
) -> bool:
    """
    Check ONLY compression filter (independent of setup trigger).
    Used for decomposition analysis.
    """
    if not config.use_compression_filter:
        return True

    return check_recent_compression(
        bb_width_pct_history=bb_width_pct_history,
        threshold=config.bb_width_pct_threshold,
        lookback=config.compression_lookback_4h
    )


def check_setup_trigger_only(
    bar: 'OHLCV',
    indicators_4h: dict,
    config,
) -> bool:
    """
    Check ONLY setup trigger (independent of compression filter).
    Used for decomposition analysis.
    """
    close = bar.close

    if config.setup_mode == "donchian":
        donch_high_prev = indicators_4h.get('donch_high_prev', 0)
        if donch_high_prev == 0:
            return False
        return close > donch_high_prev

    elif config.setup_mode == "roc_score":
        roc_score = indicators_4h.get('roc_score_4h', 0)
        return roc_score >= config.roc_score_thresh

    return False


def check_recent_compression(
    bb_width_pct_history: list[float],
    threshold: float,
    lookback: int
) -> bool:
    """
    Check if BB was compressed in last K 4h bars.

    Instead of requiring compression at trigger bar,
    allow if ANY of last K bars was compressed.
    """
    if len(bb_width_pct_history) < lookback:
        return False

    recent = bb_width_pct_history[-lookback:]
    return any(pct <= threshold for pct in recent)
