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

        # Trading activity
        trade = data.trade_stats
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Buys:* {trade.buy_count} (${float(trade.buy_volume_usd):,.2f})"},
                {"type": "mrkdwn", "text": f"*Sells:* {trade.sell_count} (${float(trade.sell_volume_usd):,.2f})"},
                {"type": "mrkdwn", "text": f"*Fees:* ${float(trade.total_fees):,.2f}"},
                {"type": "mrkdwn", "text": f"*Symbols:* {len(trade.symbols_traded)}"},
            ],
        })

        blocks.append({"type": "divider"})

        # P&L
        pnl = data.pnl
        pnl_emoji = ":chart_with_upwards_trend:" if pnl.net_pnl > 0 else ":chart_with_downwards_trend:"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{pnl_emoji} *Net P&L:* ${float(pnl.net_pnl):,.2f}\n"
                    f"*Win Rate:* {pnl.win_rate:.1%} ({pnl.win_count}W / {pnl.loss_count}L)"
                ),
            },
        })

        # Signals
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Signals:* {data.signals.total} "
                    f"({data.signals.buy_count}B / {data.signals.sell_count}S)"
                ),
            },
        })

        # Risk
        if data.risk.vetoes or data.risk.circuit_breaker_trips:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Risk:* {data.risk.vetoes} vetoes, "
                        f"{data.risk.circuit_breaker_trips} circuit breaker trips"
                    ),
                },
            })

        # Comparison
        if data.comparison:
            rate = data.comparison.agreement_rate
            if rate >= 0.8:
                emoji = ":white_check_mark:"
            elif rate >= 0.5:
                emoji = ":large_yellow_circle:"
            else:
                emoji = ":red_circle:"

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *v1 vs v2 Agreement:* {rate:.1%}\n"
                        f"v1: {data.comparison.v1_signal_count} signals | "
                        f"v2: {data.comparison.v2_signal_count} signals | "
                        f"Matched: {data.comparison.agreement_count}"
                    ),
                },
            })

            if data.comparison.symbols_only_v1 or data.comparison.symbols_only_v2:
                parts = []
                if data.comparison.symbols_only_v1:
                    parts.append(f"v1-only: {', '.join(sorted(data.comparison.symbols_only_v1))}")
                if data.comparison.symbols_only_v2:
                    parts.append(f"v2-only: {', '.join(sorted(data.comparison.symbols_only_v2))}")
                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": " | ".join(parts)}],
                })

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC') if data.generated_at else 'N/A'} by BotTrader v2"},
            ],
        })

        return blocks
