"""Round-trip trade matcher — links buy metadata to sell outcomes.

Uses FIFO matching (same algorithm as the existing P&L collectors) to pair
buy fills with sell fills and extract rich metadata from both sides.
This is the data foundation for the Edge Analysis and Entry Quality pages.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class RoundTrip:
    """A completed buy-sell trade pair with metadata from both sides."""

    symbol: str
    buy_timestamp: datetime
    sell_timestamp: datetime
    buy_price: float
    sell_price: float
    qty: float
    buy_fee: float
    sell_fee: float
    gross_pnl: float
    net_pnl: float
    gross_pnl_pct: float
    net_pnl_pct: float
    hold_seconds: int
    # Entry metadata
    trigger: str | None = None
    buy_score: float | None = None
    indicator_count: int | None = None
    indicators_fired: list[str] = field(default_factory=list)
    adx: float | None = None
    rvol: float | None = None
    atr_percentile: float | None = None
    # Exit metadata
    exit_reason: str | None = None
    peak_price: float | None = None
    drawdown_pct: float | None = None


@dataclass
class MatchSummary:
    """Summary statistics from the FIFO matching process."""

    total_round_trips: int = 0
    unmatched_buys: int = 0
    unmatched_sell_qty: float = 0.0
    date_range_start: datetime | None = None
    date_range_end: datetime | None = None


def _parse_metadata(raw) -> dict:
    """Parse JSONB metadata from asyncpg (may be str or dict)."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return dict(raw)


def _extract_indicators_fired(meta: dict) -> list[str]:
    """Extract names of buy indicators that fired (decision=1)."""
    components = meta.get("score_components", {}).get("buy", [])
    return [c["indicator"] for c in components if c.get("decision") == 1]


async def collect_round_trips(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    exchange: str = "",
) -> tuple[list[RoundTrip], MatchSummary]:
    """FIFO-match buy/sell fills into round-trip trades with full metadata.

    Pre-populates buy queues from fills before the period so that
    cross-period sells get correctly matched.

    Returns (round_trips, summary).
    """
    prior_params: list = [start]
    ex_clause_1 = ""
    if exchange:
        prior_params.append(exchange)
        ex_clause_1 = f" AND exchange = ${len(prior_params)}"

    prior_rows = await pool.fetch(
        f"""
        SELECT symbol, side, price, qty, fee, timestamp, metadata
        FROM v2_fills
        WHERE timestamp < $1{ex_clause_1}
        ORDER BY timestamp ASC
        """,
        *prior_params,
    )

    period_params: list = [start, end]
    ex_clause_2 = ""
    if exchange:
        period_params.append(exchange)
        ex_clause_2 = f" AND exchange = ${len(period_params)}"

    period_rows = await pool.fetch(
        f"""
        SELECT symbol, side, price, qty, fee, timestamp, metadata
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2{ex_clause_2}
        ORDER BY timestamp ASC
        """,
        *period_params,
    )

    return _match_round_trips(prior_rows, period_rows)


# Buy queue entry: (remaining_qty, price, fee_per_unit, timestamp, metadata_dict)
_BuyEntry = tuple[float, float, float, datetime, dict]


def _match_round_trips(
    prior_rows: list,
    period_rows: list,
) -> tuple[list[RoundTrip], MatchSummary]:
    """Pure computation: FIFO match fills into round trips."""
    buy_queues: dict[str, deque[_BuyEntry]] = defaultdict(deque)

    # Replay prior fills to build buy queues
    for row in prior_rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        qty = float(row["qty"])
        price = float(row["price"])
        fee = float(row["fee"])

        if side == "BUY":
            fpu = fee / qty if qty else 0.0
            meta = _parse_metadata(row.get("metadata"))
            buy_queues[symbol].append((qty, price, fpu, row["timestamp"], meta))
        elif side == "SELL":
            remaining = qty
            while remaining > 1e-12 and buy_queues[symbol]:
                bq_qty, bq_price, bq_fpu, bq_ts, bq_meta = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)
                leftover = bq_qty - matched
                if leftover > 1e-12:
                    buy_queues[symbol][0] = (leftover, bq_price, bq_fpu, bq_ts, bq_meta)
                else:
                    buy_queues[symbol].popleft()
                remaining -= matched

    # Process period fills
    round_trips: list[RoundTrip] = []
    unmatched_sell_qty = 0.0

    for row in period_rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        qty = float(row["qty"])
        price = float(row["price"])
        fee = float(row["fee"])

        if side == "BUY":
            fpu = fee / qty if qty else 0.0
            meta = _parse_metadata(row.get("metadata"))
            buy_queues[symbol].append((qty, price, fpu, row["timestamp"], meta))

        elif side == "SELL":
            sell_fpu = fee / qty if qty else 0.0
            sell_meta = _parse_metadata(row.get("metadata"))
            sell_ts = row["timestamp"]
            remaining = qty

            while remaining > 1e-12 and buy_queues[symbol]:
                bq_qty, bq_price, bq_fpu, bq_ts, bq_meta = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)

                # P&L
                gross = (price - bq_price) * matched
                buy_fee = bq_fpu * matched
                sell_fee = sell_fpu * matched
                net = gross - buy_fee - sell_fee

                cost_basis = bq_price * matched + buy_fee
                gross_pct = (price - bq_price) / bq_price * 100 if bq_price else 0.0
                net_pct = net / cost_basis * 100 if cost_basis else 0.0

                hold = sell_ts - bq_ts
                hold_secs = int(hold.total_seconds()) if hold else 0

                round_trips.append(
                    RoundTrip(
                        symbol=symbol,
                        buy_timestamp=bq_ts,
                        sell_timestamp=sell_ts,
                        buy_price=bq_price,
                        sell_price=price,
                        qty=matched,
                        buy_fee=buy_fee,
                        sell_fee=sell_fee,
                        gross_pnl=gross,
                        net_pnl=net,
                        gross_pnl_pct=round(gross_pct, 4),
                        net_pnl_pct=round(net_pct, 4),
                        hold_seconds=hold_secs,
                        trigger=bq_meta.get("trigger") or bq_meta.get("signal_reason"),
                        buy_score=bq_meta.get("buy_score"),
                        indicator_count=bq_meta.get("indicator_count"),
                        indicators_fired=_extract_indicators_fired(bq_meta),
                        adx=bq_meta.get("adx"),
                        rvol=bq_meta.get("rvol"),
                        atr_percentile=bq_meta.get("atr_percentile"),
                        exit_reason=sell_meta.get("signal_reason"),
                        peak_price=sell_meta.get("peak_price"),
                        drawdown_pct=sell_meta.get("drawdown_pct"),
                    )
                )

                leftover = bq_qty - matched
                if leftover > 1e-12:
                    buy_queues[symbol][0] = (
                        leftover, bq_price, bq_fpu, bq_ts, bq_meta,
                    )
                else:
                    buy_queues[symbol].popleft()
                remaining -= matched

            if remaining > 1e-12:
                unmatched_sell_qty += remaining

    unmatched_buys = sum(len(q) for q in buy_queues.values())

    summary = MatchSummary(
        total_round_trips=len(round_trips),
        unmatched_buys=unmatched_buys,
        unmatched_sell_qty=round(unmatched_sell_qty, 8),
        date_range_start=round_trips[0].sell_timestamp if round_trips else None,
        date_range_end=round_trips[-1].sell_timestamp if round_trips else None,
    )

    logger.info(
        "Round-trip matcher: %d trades, %d open positions, "
        "%.4f unmatched sell qty, range %s to %s",
        summary.total_round_trips,
        summary.unmatched_buys,
        summary.unmatched_sell_qty,
        summary.date_range_start,
        summary.date_range_end,
    )

    return round_trips, summary
