"""Slack Block Kit renderer for daily report."""

from __future__ import annotations

from ..models import ReportData


class SlackRenderer:
    """Renders a ReportData into Slack Block Kit JSON blocks."""

    def render(self, data: ReportData) -> list[dict]:
        blocks: list[dict] = []

        # Header — period-aware
        ex_label = f" [{data.exchange_name}]" if data.exchange_name else ""
        if data.period_hours and data.period_hours < 24:
            title = (
                f"BotTrader v2{ex_label} — {data.period_hours}h Report "
                f"({data.report_date} "
                f"{data.period_start.strftime('%H:%M') if data.period_start else ''}-"
                f"{data.period_end.strftime('%H:%M') if data.period_end else ''} UTC)"
            )
        else:
            title = f"BotTrader v2{ex_label} — Daily Report ({data.report_date})"

        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": title},
        })

        # 1. Hero P&L
        pnl = data.pnl
        pnl_emoji = ":chart_with_upwards_trend:" if pnl.net_pnl > 0 else ":chart_with_downwards_trend:" if pnl.net_pnl < 0 else ":heavy_minus_sign:"
        fills = data.trade_stats.buy_count + data.trade_stats.sell_count
        hero = (
            f"{pnl_emoji} *Net P&L: ${float(pnl.net_pnl):,.2f}*\n"
            f"{pnl.win_count}W / {pnl.loss_count}L ({pnl.win_rate:.1%}) · "
            f"{fills} fills · ${float(data.trade_stats.total_fees):,.2f} fees"
        )
        if pnl.best_trade or pnl.worst_trade:
            parts = []
            if pnl.best_trade:
                parts.append(f"Best: {pnl.best_trade[0]} ${float(pnl.best_trade[1]):,.2f}")
            if pnl.worst_trade:
                parts.append(f"Worst: {pnl.worst_trade[0]} ${float(pnl.worst_trade[1]):,.2f}")
            hero += "\n" + " · ".join(parts)

        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": hero}})

        # 1.5 Portfolio snapshot
        if data.portfolio:
            p = data.portfolio
            change = p.ending_value - p.starting_value
            change_pct = (change / p.starting_value * 100) if p.starting_value else 0
            sign = "+" if change >= 0 else ""
            drawdown = p.high_watermark - p.low_watermark
            dd_pct = (drawdown / p.high_watermark * 100) if p.high_watermark else 0
            portfolio_text = (
                f"*Portfolio*\n"
                f"${p.ending_value:,.2f} ({sign}{change_pct:.2f}%)\n"
                f"High: ${p.high_watermark:,.2f} · Low: ${p.low_watermark:,.2f} · "
                f"Drawdown: {dd_pct:.1f}%\n"
                f"Cash: ${p.cash_balance:,.2f} · Positions: ${p.positions_value:,.2f}"
            )
            blocks.append({"type": "divider"})
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": portfolio_text}})

        # 2. P&L by symbol (compact)
        if pnl.by_symbol:
            lines = []
            for sym, sym_pnl in sorted(pnl.by_symbol.items(), key=lambda x: float(x[1]), reverse=True):
                sign = "+" if sym_pnl > 0 else ""
                lines.append(f"{sym}: {sign}${float(sym_pnl):,.2f}")
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*P&L by Symbol*\n{' · '.join(lines)}"},
            })

        # 2.5 Trade log (compact — max 10 entries)
        if data.trade_log:
            lines = []
            for t in data.trade_log[:10]:
                side_icon = ":large_green_circle:" if t.side == "BUY" else ":red_circle:"
                pnl_str = f" P&L ${t.realized_pnl:+,.2f}" if t.realized_pnl is not None else ""
                reason_str = f" [{t.exit_reason}]" if t.exit_reason else ""
                lines.append(
                    f"{side_icon} {t.symbol} {t.side} {t.qty:.4f} @ ${t.price:,.2f}{pnl_str}{reason_str}"
                )
            if len(data.trade_log) > 10:
                lines.append(f"_...and {len(data.trade_log) - 10} more_")
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Trade Log*\n" + "\n".join(lines)},
            })

        # 3. System health
        health = f"*Signals:* {data.signals.total} ({data.signals.buy_count}B / {data.signals.sell_count}S)"
        if data.risk.vetoes or data.risk.circuit_breaker_trips or data.risk.stale_cancellations:
            parts = []
            if data.risk.vetoes:
                veto_str = f"{data.risk.vetoes} vetoes"
                if data.risk.performance_filter_vetoes:
                    veto_str += f" ({data.risk.performance_filter_vetoes} perf filter)"
                parts.append(veto_str)
            if data.risk.circuit_breaker_trips:
                parts.append(f"{data.risk.circuit_breaker_trips} CB trips")
            if data.risk.stale_cancellations:
                parts.append(f"{data.risk.stale_cancellations} stale cancels")
            health += f"\n:warning: {', '.join(parts)}"

        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": health}})

        # 3.5 Exit manager
        if data.exit_manager:
            em = data.exit_manager
            parts = [f"Hard stops: {em.hard_stops}"]
            if em.stale_exits:
                parts.append(f"Stale exits: {em.stale_exits}")
            parts.append(f"Trailing stops: {em.trailing_stops}")
            parts.append(f"Signal exits: {em.signal_exits}")
            exit_text = (
                f"*Exit Manager*\n"
                f"{' · '.join(parts)}\n"
                f"Trailing activations: {em.trailing_activations} · "
                f"Total exits: {em.total_exits}"
            )
            if em.events:
                exit_text += "\n"
                for ev in em.events[:5]:
                    exit_text += (
                        f"\n{ev.symbol} {ev.reason} @ ${ev.price:,.2f} ({ev.pnl_pct:+.2f}%)"
                    )
                if len(em.events) > 5:
                    exit_text += f"\n_...and {len(em.events) - 5} more_"
            blocks.append({"type": "divider"})
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": exit_text}})

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"BotTrader v2 · {data.generated_at.strftime('%H:%M UTC') if data.generated_at else ''}"},
            ],
        })

        return blocks
