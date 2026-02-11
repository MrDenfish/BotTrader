"""Slack Block Kit renderer for daily report."""

from __future__ import annotations

from ..models import ReportData


class SlackRenderer:
    """Renders a ReportData into Slack Block Kit JSON blocks."""

    def render(self, data: ReportData) -> list[dict]:
        blocks: list[dict] = []

        # Header
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"BotTrader v2 — Daily Report ({data.report_date})",
            },
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

        # 3. System health
        health = f"*Signals:* {data.signals.total} ({data.signals.buy_count}B / {data.signals.sell_count}S)"
        if data.risk.vetoes or data.risk.circuit_breaker_trips:
            health += (
                f"\n:warning: {data.risk.vetoes} vetoes, "
                f"{data.risk.circuit_breaker_trips} CB trips"
            )

        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": health}})

        # 4. Comparison (if data exists)
        if data.comparison and (data.comparison.v1_signal_count or data.comparison.v2_signal_count):
            rate = data.comparison.agreement_rate
            emoji = ":white_check_mark:" if rate >= 0.8 else ":large_yellow_circle:" if rate >= 0.5 else ":red_circle:"
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *v1/v2 Agreement: {rate:.1%}*\n"
                        f"v1: {data.comparison.v1_signal_count} · "
                        f"v2: {data.comparison.v2_signal_count} · "
                        f"Matched: {data.comparison.agreement_count}"
                    ),
                },
            })

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"BotTrader v2 · {data.generated_at.strftime('%H:%M UTC') if data.generated_at else ''}"},
            ],
        })

        return blocks
