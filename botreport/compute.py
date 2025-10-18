# from statistics import mean, pstdev
# from typing import Sequence, List
# from botreport.models import ReportBundle
#
# def _nonzero(xs: Sequence[float]) -> list[float]:
#     return [x for x in xs if x is not None]
#
# def compute_metrics(bundle: ReportBundle,
#                     closed_trade_pnls: List[float],
#                     wins: List[float],
#                     losses: List[float],
#                     breakevens: int) -> ReportBundle:
#     m = bundle.metrics
#
#     # Totals
#     m.total_trades = len(closed_trade_pnls) + int(breakevens or 0)
#     m.breakeven_trades = int(breakevens or 0)
#
#     # Win rate (include breakevens in denom if that’s your convention)
#     denom = m.total_trades or 0
#     m.win_rate_pct = (100.0 * (len(wins) / denom)) if denom > 0 else None
#
#     # Avg win/loss
#     m.avg_win  = mean(wins)   if wins   else None
#     m.avg_loss = mean(losses) if losses else None
#
#     # Ratios
#     if (m.avg_win is not None) and (m.avg_loss not in (None, 0.0)):
#         m.avg_w_over_avg_l = m.avg_win / abs(m.avg_loss)
#
#     gross_profits = sum(x for x in wins if x is not None and x > 0)
#     gross_losses  = sum(abs(x) for x in losses if x is not None and x < 0)
#     m.profit_factor = (gross_profits / gross_losses) if gross_losses else None
#
#     # Expectancy / mean
#     if denom > 0:
#         net = sum(x for x in closed_trade_pnls if x is not None)
#         m.expectancy_per_trade = net / denom
#         m.mean_pnl_per_trade = m.expectancy_per_trade
#
#         # Stdev & Sharpe-like
#         stdev = pstdev([x for x in closed_trade_pnls if x is not None]) if len(closed_trade_pnls) > 1 else None
#         m.stdev_pnl_per_trade = stdev
#         m.sharpe_like_per_trade = (m.mean_pnl_per_trade / stdev) if (stdev and stdev != 0) else None
#
#     return bundle