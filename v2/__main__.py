"""Entry point: python -m v2 --config config.yaml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    # Intercept subcommands before argparse
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        from v2.plugins.observability.daily_report_v2.cli import report_main
        report_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "optimize":
        from v2.backtest.optimizer import optimize_main
        optimize_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        prog="bottrader-v2",
        description="BotTrader v2 — plugin-based trading system",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["live", "paper", "backtest"],
        default=None,
        help="Override app mode",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List registered plugins and exit",
    )
    args = parser.parse_args()

    # Logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # List plugins mode
    if args.list_plugins:
        from v2.core import registry
        registry.discover_plugins()
        for category, names in registry.list_plugins().items():
            print(f"{category}: {', '.join(names) if names else '(none)'}")
        return

    # Build overrides from CLI
    overrides = {}
    if args.mode:
        overrides["app"] = {"mode": args.mode}

    # Run
    from v2.core.app import App
    app = App(config_path=args.config, overrides=overrides or None)
    try:
        result = asyncio.run(app.run())
        if result is not None:
            _print_backtest_results(result, app)
    except KeyboardInterrupt:
        pass


def _print_backtest_results(stats: dict, app) -> None:
    """Print backtest results summary."""
    if "error" in stats:
        print(f"\nError: {stats['error']}")
        return

    trades = stats.get('trades', 0)
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS (v2)")
    print("=" * 70)

    print(f"  Trades:         {trades}  ({stats.get('winners', 0)}W / {stats.get('losers', 0)}L)")
    print(f"  Gross P&L:      ${stats.get('gross_pnl', 0):>10.2f}")
    print(f"  Fees:           ${stats.get('fees', 0):>10.2f}")
    print(f"  Net P&L:        ${stats.get('net_pnl', 0):>10.2f}")

    if trades > 0:
        pf = stats.get('profit_factor', 0)
        pf_str = f"{pf:.2f}" if pf != float('inf') else "inf"
        print(f"  Win Rate:       {stats.get('win_rate', 0):>10.1%}")
        print(f"  Profit Factor:  {pf_str:>10}")
        print(f"  Avg Win:        ${stats.get('avg_win', 0):>10.2f}")
        print(f"  Avg Loss:       ${stats.get('avg_loss', 0):>10.2f}")
        if stats.get('avg_hold_minutes'):
            hold_hrs = stats['avg_hold_minutes'] / 60
            print(f"  Avg Hold:       {hold_hrs:>10.1f} hrs")
        print(f"  Max Drawdown:   {stats.get('max_drawdown_pct', 0):>10.2f}%")

    print(f"  Open Positions: {stats.get('open_positions', 0)}")

    # Signals / fills / vetoes
    print(f"\n  Signals:  {stats.get('signals_buy', 0)} buy / {stats.get('signals_sell', 0)} sell")
    print(f"  Fills:    {stats.get('buy_fills', 0)} buy / {stats.get('sell_fills', 0)} sell")
    print(f"  Vetoes:   {stats.get('vetoes', 0)}")

    # Exit reasons
    exit_reasons = stats.get('exit_reasons', {})
    if exit_reasons:
        print(f"\n  Exit Reasons:")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:20s} {count:>3}")

    # Trade log
    trade_log = stats.get('trade_log', [])
    if trade_log:
        print(f"\n  {'Symbol':<10} {'Entry':>10} {'Exit':>10} {'P&L':>8} {'P&L%':>7} {'Hold':>8} {'Reason'}")
        print("  " + "-" * 68)
        for t in trade_log:
            hold_hrs = t['hold_min'] / 60
            print(f"  {t['symbol']:<10} ${t['entry_price']:>9.2f} ${t['exit_price']:>9.2f} "
                  f"${t['net_pnl']:>7.2f} {t['pnl_pct']:>6.1f}% {hold_hrs:>6.1f}h {t['exit_reason']}")

    print("=" * 70)

    # Print enhanced diagnostics if available
    for strategy in app._strategies:
        if hasattr(strategy, "print_enhanced_diagnostics"):
            print(strategy.print_enhanced_diagnostics())

    # Print diagnostic summary if available
    diag = stats.get("diagnostics_backtest_diagnostics")
    if diag:
        _print_diagnostic_summary(diag)


def _print_diagnostic_summary(diag: dict) -> None:
    """Print diagnostic backtest summary (MFE/MAE, regime, triggers, correlations)."""
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)

    n = diag.get("diagnostic_trades", 0)
    print(f"  Diagnostic trades: {n}")
    if diag.get("jsonl_path"):
        print(f"  Output: {diag['jsonl_path']}")

    if n == 0:
        return

    # P&L by trigger type
    by_trigger = diag.get("pnl_by_trigger", {})
    if by_trigger:
        print(f"\n  P&L by Entry Trigger:")
        print(f"  {'Trigger':<20} {'Trades':>6} {'P&L':>10} {'Win%':>7}")
        print("  " + "-" * 45)
        for trigger, data in sorted(by_trigger.items(), key=lambda x: -x[1]["count"]):
            win_rate = data["wins"] / data["count"] * 100 if data["count"] > 0 else 0
            print(f"  {trigger:<20} {data['count']:>6} ${data['pnl']:>9.2f} {win_rate:>6.1f}%")

    # P&L by market regime
    by_regime = diag.get("pnl_by_regime", {})
    if by_regime:
        print(f"\n  P&L by Market Regime:")
        print(f"  {'Regime':<25} {'Trades':>6} {'P&L':>10} {'Win%':>7}")
        print("  " + "-" * 50)
        for regime, data in sorted(by_regime.items(), key=lambda x: -x[1]["count"]):
            win_rate = data["wins"] / data["count"] * 100 if data["count"] > 0 else 0
            print(f"  {regime:<25} {data['count']:>6} ${data['pnl']:>9.2f} {win_rate:>6.1f}%")

    # P&L by exit reason
    by_exit = diag.get("pnl_by_exit_reason", {})
    if by_exit:
        print(f"\n  P&L by Exit Reason:")
        print(f"  {'Reason':<25} {'Trades':>6} {'P&L':>10}")
        print("  " + "-" * 43)
        for reason, data in sorted(by_exit.items(), key=lambda x: -x[1]["count"]):
            print(f"  {reason:<25} {data['count']:>6} ${data['pnl']:>9.2f}")

    # MFE/MAE statistics
    mfe_mae = diag.get("mfe_mae_stats", {})
    if mfe_mae:
        print(f"\n  MFE/MAE Statistics (how close trades get to profit):")
        print(f"    MFE avg:    {mfe_mae.get('mfe_avg_pct', 0):>7.3f}%  (best unrealized gain)")
        print(f"    MFE median: {mfe_mae.get('mfe_median_pct', 0):>7.3f}%")
        print(f"    MAE avg:    {mfe_mae.get('mae_avg_pct', 0):>7.3f}%  (worst unrealized loss)")
        print(f"    MAE median: {mfe_mae.get('mae_median_pct', 0):>7.3f}%")

        if "winners_mfe_avg_pct" in mfe_mae:
            print(f"\n    Winners MFE avg: {mfe_mae['winners_mfe_avg_pct']:>7.3f}%")
            print(f"    Winners MAE avg: {mfe_mae['winners_mae_avg_pct']:>7.3f}%")
        if "losers_mfe_avg_pct" in mfe_mae:
            print(f"    Losers MFE avg:  {mfe_mae['losers_mfe_avg_pct']:>7.3f}%")
            print(f"    Losers MAE avg:  {mfe_mae['losers_mae_avg_pct']:>7.3f}%")

        post_fav = mfe_mae.get("post_exit_favorable_avg_pct", 0)
        if post_fav:
            print(f"\n    Post-exit favorable avg: {post_fav:>7.3f}%  (money left on table)")
            print(f"    Post-exit favorable med: {mfe_mae.get('post_exit_favorable_median_pct', 0):>7.3f}%")

    # Top correlated indicator pairs
    correlations = diag.get("indicator_correlations", {})
    if correlations:
        print(f"\n  Top Correlated Indicator Pairs:")
        sorted_corr = sorted(correlations.items(), key=lambda x: -abs(x[1]))
        for pair, r in sorted_corr[:10]:
            print(f"    {pair:<40} r={r:>6.3f}")

    print("=" * 70)


if __name__ == "__main__":
    main()
