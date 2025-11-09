# SharedDataManager/leader_board.py

from __future__ import annotations

import math

from typing import Callable, Any, Optional
from dataclasses import dataclass

from piptools.writer import strip_comes_from_line_re
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from TableModels.trade_record import TradeRecord
from sqlalchemy.dialects.postgresql import insert
from datetime import datetime, timedelta, timezone
from TableModels.active_symbols import ActiveSymbol
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.logger import get_logger

# Module-level logger
_logger = get_logger('leader_board', context={'component': 'leader_board'})

# Add type for clarity: fetch_precision takes symbol -> tuple[base_deci, quote_deci, base_quant, quote_quant]
FetchPrecisionFn = Callable[[str], tuple[int, int, object, object]]

AdjustPrecisionFn = Callable[[int, int, Any, str], Any]

# ---- Tunables (same defaults we tested) ----
WIN_RATE_MIN = 0.35
PF_MIN = 1.30
LOOKBACK_HOURS = 24
MIN_N_24H = 3
# (Optional long window for fallback)
MIN_N_72H = 7

@dataclass
class LeaderboardConfig:
    lookback_hours: int = LOOKBACK_HOURS
    min_n_24h: int = MIN_N_24H
    win_rate_min: float = WIN_RATE_MIN
    pf_min: float = PF_MIN

async def _fetch_last_window_sells(session: AsyncSession, since_utc: datetime):
    """
    Pull last-window SELL trades with realized pnl_usd for all symbols.
    Uses TradeRecord model if you have it; else switch to plain text() query.
    """
    # Model path preferred:
    try:
        stmt = (
            select(TradeRecord.symbol, TradeRecord.pnl_usd)
            .where(
                and_(
                    TradeRecord.side == 'sell',
                    TradeRecord.status == 'filled',
                    TradeRecord.order_time >= since_utc
                )
            )
        )
        rows = (await session.execute(stmt)).all()
        return [{"symbol": r[0], "pnl_usd": r[1]} for r in rows]
    except Exception:
        # Fallback: plain SQL if model not present
        from sqlalchemy import text
        stmt = text("""
            SELECT symbol, pnl_usd
            FROM trade_records
            WHERE side='sell' AND status='filled'
              AND order_time >= :since
        """)
        rows = (await session.execute(stmt, {"since": since_utc})).all()
        return [{"symbol": r[0], "pnl_usd": r[1]} for r in rows]

def _fold_metrics(rows):
    by = {}
    for r in rows:
        sym = r["symbol"]
        pnl = float(r["pnl_usd"] or 0.0)
        d = by.setdefault(sym, {"n":0,"wins":0,"losses":0,"gp":0.0,"gl":0.0,"sum":0.0})
        d["n"] += 1
        d["sum"] += pnl
        if pnl > 0:
            d["wins"] += 1
            d["gp"] += pnl
        elif pnl < 0:
            d["losses"] += 1
            d["gl"] += pnl
    out = []
    for sym, d in by.items():
        n = d["n"]
        wins, losses = d["wins"], d["losses"]
        win_rate = wins / n if n else 0.0
        mean_pnl = d["sum"] / n if n else 0.0
        gp, gl = d["gp"], d["gl"]
        pf = (gp / abs(gl)) if gl < 0 else None
        pf_norm = min(max(pf or 0.0, 0.0), 10.0) / 10.0
        score = mean_pnl * (1.0 + pf_norm) * math.sqrt(max(n, 1))
        out.append({
            "symbol": sym, "n": n, "wins": wins, "losses": losses, "win_rate": win_rate,
            "mean_pnl": mean_pnl, "gross_profit": gp, "gross_loss": gl,
            "profit_factor": pf, "score": score
        })
    return out

async def recompute_and_upsert_active_symbols(session: AsyncSession, cfg: LeaderboardConfig, logger_manager: LoggerManager,
                                              fetch_precision: FetchPrecisionFn, adjust_precision: Optional[AdjustPrecisionFn] = None) -> None:

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg.lookback_hours)

    rows = await _fetch_last_window_sells(session, since)
    metrics = _fold_metrics(rows)

    # Upsert into active_symbols

    upserts = []
    for m in metrics:
        base_deci, quote_deci, _, _ = fetch_precision(m["symbol"])
        mean_pnl = adjust_precision(base_deci, quote_deci, m["mean_pnl"], "quote")
        gross_loss = adjust_precision(base_deci, quote_deci, m["gross_loss"], "quote")
        gross_profit = adjust_precision(base_deci, quote_deci, m["gross_profit"], "quote")
        profit_factor = adjust_precision(base_deci, quote_deci, m["profit_factor"], "quote")
        score = adjust_precision(base_deci, quote_deci, m["score"], "base")
        eligible = (
            m["n"] >= cfg.min_n_24h and
            m["win_rate"] >= cfg.win_rate_min and
            mean_pnl > 0 and
            (profit_factor or 0.0) >= cfg.pf_min
        )

        stmt = insert(ActiveSymbol).values(
            symbol=m["symbol"],
            as_of=now,
            window_hours=cfg.lookback_hours,
            n=m["n"], wins=m["wins"], losses=m["losses"],
            win_rate=m["win_rate"], mean_pnl=mean_pnl,
            gross_profit=gross_profit, gross_loss=gross_loss,
            profit_factor=profit_factor, score=score,
            eligible=eligible
        ).on_conflict_do_update(
            index_elements=[ActiveSymbol.symbol],
            set_={
                "as_of": now,
                "window_hours": cfg.lookback_hours,
                "n": m["n"], "wins": m["wins"], "losses": m["losses"],
                "win_rate": m["win_rate"], "mean_pnl": mean_pnl,
                "gross_profit": gross_profit, "gross_loss": gross_loss,
                "profit_factor": profit_factor, "score": score,
                "eligible": eligible
            }
        )
        upserts.append(stmt)
    symbols = {r["symbol"] for r in rows}
    eligible_cnt = sum(1 for m in metrics if (
            m["n"] >= cfg.min_n_24h and m["win_rate"] >= cfg.win_rate_min and
            m["mean_pnl"] > 0 and (m["profit_factor"] or 0) >= cfg.pf_min
    ))
    _logger.info("Leaderboard scan completed",
                extra={'rows': len(rows), 'symbols': len(symbols), 'eligible': eligible_cnt})

    for s in upserts:
        await session.execute(s)
    await session.commit()

