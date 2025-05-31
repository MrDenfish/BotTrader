from decimal import Decimal
from datetime import datetime


class PerformanceTracker:
    def __init__(self, logger, shared_utils_precision, fee_monitor=None):
        self.logger = logger
        self.fee_monitor = fee_monitor
        self.shared_utils_precision = shared_utils_precision
        self.completed_trades = []  # list of trade performance dicts
        self.symbol_stats = {}      # aggregate stats per symbol

    def record_trade_result(self, buy_order: dict, sell_order: dict):
        """
        Record the outcome of a completed trade (buy + sell pair)
        and calculate performance metrics.
        """
        try:
            symbol = buy_order['symbol']
            base_deci, quote_deci,_,_ = self.shared_utils_precision.get_symbol_precision(symbol)
            size = self.shared_utils_precision.safe_convert(sell_order.get('size'), base_deci)
            entry_price = self.shared_utils_precision.safe_convert(buy_order.get('price'), quote_deci)
            exit_price = self.shared_utils_precision.safe_convert(sell_order.get('price'), quote_deci)
            buy_fee = self.shared_utils_precision.safe_convert(buy_order.get('total_fees_usd'),quote_deci)
            sell_fee = self.shared_utils_precision.safe_convert(sell_order.get('total_fees_usd'), quote_deci)
            gross_profit = (exit_price - entry_price) * size
            total_fees = buy_fee + sell_fee
            net_profit = gross_profit - total_fees
            roi = (net_profit / (entry_price * size)) * Decimal('100') if entry_price > 0 else Decimal('0')

            duration = (sell_order['order_time'] - buy_order['order_time']).total_seconds() / 60

            result = {
                'symbol': symbol,
                'size': float(size),
                'entry_price': float(entry_price),
                'exit_price': float(exit_price),
                'gross_profit': float(gross_profit),
                'net_profit': float(net_profit),
                'roi_percent': float(roi),
                'fees': float(total_fees),
                'duration_minutes': float(duration),
                'buy_id': buy_order['order_id'],
                'sell_id': sell_order['order_id'],
                'timestamp': datetime.utcnow().isoformat()
            }

            self.completed_trades.append(result)
            self.logger.info(f"📈 Recorded trade result: {result}")
            self._update_symbol_stats(symbol, result)

        except Exception as e:
            self.logger.error(f"❌ Error in record_trade_result: {e}", exc_info=True)

    def _update_symbol_stats(self, symbol: str, trade: dict):
        """Aggregate per-symbol stats."""
        stats = self.symbol_stats.setdefault(symbol, {
            'trades': 0,
            'total_net_profit': 0.0,
            'total_roi': 0.0,
            'wins': 0,
            'losses': 0,
        })

        stats['trades'] += 1
        stats['total_net_profit'] += trade['net_profit']
        stats['total_roi'] += trade['roi_percent']
        if trade['net_profit'] > 0:
            stats['wins'] += 1
        else:
            stats['losses'] += 1

    def get_report(self, symbol=None):
        """Return performance summary (all trades or per symbol)."""
        if symbol:
            return self.symbol_stats.get(symbol, {})
        return {
            'summary': {
                'total_trades': len(self.completed_trades),
                'symbols': list(self.symbol_stats.keys()),
            },
            'per_symbol': self.symbol_stats,
        }
