"""Live strategy probe — runs composite_scoring locally against fresh REST data.

Answers the question "would the strategy fire a buy right now, and if not, why?"
without touching the live v2-kraken container. Used by the Bot Health page to
distinguish "idle by design" (regime / volume guardrails blocking) from "stale"
(bot should have fired and didn't).

Reads the same YAML the live bot uses, calls the same scoring code. The only
divergence from the live bot is the candle source — REST OHLC vs the bot's
WS-aggregated candles — which can differ in edge cases (recent ticks, gaps).
For the "is the regime buy-able?" question that's acceptable; for snapshot-
of-actual-state we'll add an in-process observer post-July.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
import yaml

from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig
from v2.plugins.strategies.composite_scoring.indicators import compute_indicators
from v2.plugins.strategies.composite_scoring.scoring import compute_scores

logger = logging.getLogger(__name__)

# Container path is the production location (image baked via COPY . /app);
# the second path covers local repo testing from the project root.
_CONFIG_CANDIDATE_PATHS = (
    Path("/app/v2/kraken_paper_trading.yaml"),
    Path(__file__).resolve().parents[1] / "kraken_paper_trading.yaml",
)
_REST_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Map internal symbol prefixes to Kraken REST pair names. Kraken's REST uses
# legacy "X"/"Z" prefixes for the original assets and bare symbols for most
# others; we cover the common cases the bot trades. Anything not in the table
# is tried with the trailing "USD" appended (works for most newer alts).
_INTERNAL_TO_REST: dict[str, str] = {
    "BTC-USD": "XBTUSD",
    "ETH-USD": "ETHUSD",
    "DOGE-USD": "XDGUSD",
    "XMR-USD": "XMRUSD",
}


@st.cache_data(ttl=3600)
def load_production_config() -> CompositeScoreConfig:
    """Load the composite_scoring config block from the deployed YAML.

    Cached because the YAML is baked into the image — it doesn't change
    within a container lifetime.
    """
    path = next((p for p in _CONFIG_CANDIDATE_PATHS if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"kraken_paper_trading.yaml not found at any of: {_CONFIG_CANDIDATE_PATHS}"
        )
    with path.open() as f:
        yml = yaml.safe_load(f)
    strat_block = next(
        s["config"] for s in yml["strategies"] if s["type"] == "composite_scoring"
    )
    valid = {f.name for f in dataclasses.fields(CompositeScoreConfig)}
    return CompositeScoreConfig(**{k: v for k, v in strat_block.items() if k in valid})


def _to_rest_pair(symbol: str) -> str:
    """Internal symbol → Kraken REST pair name."""
    if symbol in _INTERNAL_TO_REST:
        return _INTERNAL_TO_REST[symbol]
    base, _, quote = symbol.partition("-")
    return f"{base}{quote}"


def _fetch_ohlc_5m_bars(symbol: str, cfg: CompositeScoreConfig) -> pd.DataFrame | None:
    """Fetch ~12h of 1-min OHLC and aggregate to the strategy's interval.

    Returns None on any failure. The strategy needs at least ``cfg.min_bars``
    aggregated bars to evaluate.
    """
    rest_pair = _to_rest_pair(symbol)
    try:
        resp = requests.get(
            _REST_OHLC_URL,
            params={"pair": rest_pair, "interval": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.debug("OHLC fetch failed for %s", symbol, exc_info=True)
        return None

    if data.get("error"):
        return None
    result = data.get("result") or {}
    pair_key = next((k for k in result.keys() if k != "last"), None)
    if pair_key is None:
        return None
    rows = result[pair_key]
    if not rows:
        return None

    df_1m = pd.DataFrame(
        rows, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"]
    )
    df_1m["ts"] = pd.to_datetime(df_1m["ts"], unit="s", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df_1m[col] = df_1m[col].astype(float)
    df_1m = df_1m.set_index("ts")

    df_agg = (
        df_1m.resample(f"{cfg.candle_interval_minutes}min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    if len(df_agg) < cfg.min_bars:
        return None
    return df_agg.tail(cfg.buffer_size)


def probe_symbol(symbol: str, cfg: CompositeScoreConfig) -> dict[str, Any] | None:
    """Run indicators + scoring on fresh REST data; return per-symbol state.

    Returns None if the symbol can't be probed (REST failure, insufficient bars).
    The returned dict includes which guardrails would veto a buy — even when
    no buy candidate exists, the guardrail snapshot tells the regime story.
    """
    df = _fetch_ohlc_5m_bars(symbol, cfg)
    if df is None:
        return None

    ind = compute_indicators(df, cfg)
    if not ind:
        return None

    buy_s, sell_s, _buy_sig, _sell_sig, _comp = compute_scores(ind, cfg)

    buy_names = [n for n in cfg.weights if n.startswith("Buy") or n == "W-Bottom"]
    fired_names = [
        n.replace("Buy ", "")
        for n in buy_names
        if isinstance(ind.get(n), tuple) and ind[n][0] == 1
    ]

    adx = float(ind.get("_ADX", 0.0))
    rvol = float(ind.get("_RVOL", 0.0))
    atr_pctile = float(ind.get("_atr_percentile", 0.0))
    sma_slope = float(ind.get("_sma_slope_pct", 0.0))

    # Independent check of each guardrail that gates a buy. We list every gate
    # the live strategy applies in order; "blocked_by" is a human-readable
    # explanation of why this symbol would not produce a buy right now.
    blocked: list[str] = []
    if buy_s < cfg.score_buy_target:
        blocked.append(f"score<{cfg.score_buy_target}")
    if len(fired_names) < cfg.min_indicators_required:
        blocked.append(f"indicators<{cfg.min_indicators_required}")
    if cfg.adx_gate_enabled and adx < cfg.adx_min_threshold:
        blocked.append(f"ADX<{cfg.adx_min_threshold}")
    if cfg.volume_confirm_buy and rvol > 0 and rvol < cfg.volume_confirm_threshold:
        blocked.append(f"RVOL<{cfg.volume_confirm_threshold}")
    if cfg.regime_filter_enabled and atr_pctile > cfg.regime_max_atr_percentile:
        blocked.append(f"ATR%>{cfg.regime_max_atr_percentile}")
    if (
        cfg.regime_filter_enabled
        and getattr(cfg, "regime_require_uptrend", False)
        and sma_slope <= 0
    ):
        blocked.append("downtrend")

    return {
        "symbol": symbol,
        "buy_score": round(buy_s, 2),
        "indicators_fired": len(fired_names),
        "fired_names": fired_names,
        "adx": round(adx, 1),
        "rvol": round(rvol, 2),
        "atr_pctile": round(atr_pctile, 1),
        "sma_slope_pct": round(sma_slope, 3),
        "blocked_by": blocked,
        "would_fire": len(blocked) == 0,
    }


@st.cache_data(ttl=300, show_spinner=False)
def probe_universe(symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    """Probe a list of symbols. Cached 5 min — the regime doesn't change fast.

    Symbols are passed as a tuple because Streamlit cache keys must be hashable.
    """
    cfg = load_production_config()
    out: list[dict[str, Any]] = []
    for sym in symbols:
        result = probe_symbol(sym, cfg)
        if result is not None:
            out.append(result)
    return out


def summarize(probe_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate probe results into status-pill inputs."""
    total = len(probe_results)
    passing = sum(1 for r in probe_results if r["would_fire"])
    cfg = load_production_config()
    # Per-gate pass counts — useful header for the "why" panel.
    by_gate = {
        "score": sum(1 for r in probe_results if r["buy_score"] >= cfg.score_buy_target),
        "indicators": sum(
            1 for r in probe_results if r["indicators_fired"] >= cfg.min_indicators_required
        ),
        "ADX": sum(1 for r in probe_results if r["adx"] >= cfg.adx_min_threshold),
        "RVOL": sum(
            1 for r in probe_results if r["rvol"] >= cfg.volume_confirm_threshold
        ),
        "regime": sum(
            1 for r in probe_results if r["atr_pctile"] <= cfg.regime_max_atr_percentile
        ),
    }
    return {
        "total": total,
        "passing": passing,
        "by_gate": by_gate,
        "any_passing": passing > 0,
    }
