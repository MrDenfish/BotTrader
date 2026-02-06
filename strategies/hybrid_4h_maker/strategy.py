"""
Hybrid 4h Maker Strategy - StrategyPlugin implementation.

This is the plugin wrapper that delegates to the decomposed modules:
- filters.py: regime, viability, setup detection, compression
- state_machine.py: 5-state dispatch
- entry_logic.py: retest/chase entry
- exit_logic.py: TP/trail/stop management
- diagnostics.py: gate audit, enhanced diagnostics

The original Hybrid4hStrategy class (backtest/strategy_4h_hybrid.py) remains
untouched as the reference implementation for trade-for-trade validation.
"""

from typing import Optional, Any
import pandas as pd

from strategies.core.base import StrategyPlugin
from strategies.core.types import (
    OHLCV, StrategySignal, SignalDirection,
    FillNotification, StrategyMetrics
)
from strategies.hybrid_4h_maker import (
    state_machine, filters, diagnostics
)
from backtest.strategy_4h_hybrid import (
    StrategyState, Setup, Position, EntryOrder, Trade, ExpiredSetup
)
from backtest.config_4h_hybrid import Hybrid4hConfig


class Hybrid4hMakerStrategy(StrategyPlugin):
    """
    4h Hybrid Maker Strategy as a StrategyPlugin.

    Wraps the 5-state machine (FLAT → SETUP_ACTIVE → RETEST_ORDER_WORKING
    → CHASE_ORDER_WORKING → IN_POSITION) behind the standard evaluate() API.
    """

    NAME = 'hybrid_4h_maker'

    # --- Identity ---

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def version(self) -> str:
        return '2.4'

    @property
    def timeframes_required(self) -> list[str]:
        return ['1m', '4h', '1D']

    # --- Lifecycle ---

    def configure(self, config: Any) -> None:
        if not isinstance(config, Hybrid4hConfig):
            raise TypeError(f"Expected Hybrid4hConfig, got {type(config)}")
        self.config = config
        self._init_state()

    def _init_state(self):
        """Initialize all internal state tracking."""
        # State machine
        self.state: dict[str, StrategyState] = {}
        self.pending_setups: dict[str, Setup] = {}
        self.entry_orders: dict[str, EntryOrder] = {}
        self.positions: dict[str, Position] = {}

        # BB width history per symbol
        self.bb_width_history: dict[str, list[float]] = {}

        # Completed trades
        self.trades: list[Trade] = []
        self.expired_setups: list[ExpiredSetup] = []

        # Statistics
        self.signal_count = 0
        self.entry_count = 0
        self.expired_setup_count = 0

        # Chase entry statistics
        self.retest_fill_count = 0
        self.chase_attempt_count = 0
        self.chase_fill_count = 0

        # Gate audit counters
        self.gate_audit = {
            'total_4h_bars': 0,
            'regime_ok': 0,
            'viability_ok': 0,
            'compression_ok': 0,
            'roc_ok': 0,
            'roc_ok_and_compressed': 0,
            'structure_ok': 0,
            'setup_trigger_ok': 0,
            'setup_created': 0,
            'entry_filled': 0,
            'roc_ok_blocked_by_state': 0,
            'setup_eval_attempted': 0
        }

        # Enhanced diagnostics
        self.bb_width_pct_all = []
        self.bb_width_pct_viable = []
        self.atr_pct_regime_ok = []
        self.roc_score_viable = []

        # Gate decision masks
        self.regime_ok_mask = []
        self.viability_ok_mask = []
        self.setup_ok_mask = []
        self.compression_ok_mask = []

        # Decomposition counters
        self.setup_only_count = 0
        self.compression_only_count = 0
        self.intersection_count = 0
        self.neither_count = 0

        # Compression variant tracking
        self.compression_now_mask = []
        self.compression_recent_6_mask = []
        self.compression_recent_12_mask = []

        # Conditional viability tracking
        self.viability_rescued_count = 0

        # State occupancy metrics
        self.state_occupancy = {
            StrategyState.FLAT: 0,
            StrategyState.SETUP_ACTIVE: 0,
            StrategyState.RETEST_ORDER_WORKING: 0,
            StrategyState.CHASE_ORDER_WORKING: 0,
            StrategyState.IN_POSITION: 0
        }

        # Setup lifecycle metrics
        self.setup_canceled_count = 0
        self.setup_active_durations = []
        self.entry_submitted_count = 0

        # Opportunity loss tracking
        self.opp_loss = {
            'roc_ok_while_setup_active': 0,
            'roc_ok_while_position_open': 0,
            'compression_while_setup_active': 0,
            'compression_while_position_open': 0
        }

        self.setup_start_time: dict[str, pd.Timestamp] = {}

    def warmup_bars(self) -> dict[str, int]:
        return {'4h': 200, '1D': 200}

    def start(self) -> None:
        self._init_state()

    def stop(self) -> None:
        pass

    # --- Core Decision ---

    def evaluate(
        self,
        symbol: str,
        bar: OHLCV,
        indicators: dict[str, dict],
        context: dict
    ) -> Optional[StrategySignal]:
        """
        Evaluate current bar through the 5-state machine.

        This mirrors Hybrid4hStrategy.process_bar_1m() exactly,
        delegating to the decomposed state_machine module.
        """
        is_4h_close = context.get('is_4h_close', False)
        is_1d_close = context.get('is_1d_close', False)
        indicators_4h = indicators.get('4h', {})
        indicators_1d = indicators.get('1D', {})

        # Initialize state if needed
        if symbol not in self.state:
            self.state[symbol] = StrategyState.FLAT

        if symbol not in self.bb_width_history:
            self.bb_width_history[symbol] = []

        # Update bb_width_pct history on 4h closes
        if is_4h_close:
            bb_width_pct = indicators_4h.get('bb_width_pct', None)
            if bb_width_pct is not None:
                self.bb_width_history[symbol].append(bb_width_pct)
                max_history = self.config.compression_lookback_4h + 10
                if len(self.bb_width_history[symbol]) > max_history:
                    self.bb_width_history[symbol] = self.bb_width_history[symbol][-max_history:]

        current_state = self.state[symbol]

        # Collect diagnostics on EVERY 4h bar
        if is_4h_close:
            self._collect_4h_diagnostics(
                symbol, bar, indicators_4h, indicators_1d, current_state
            )

        # Age pending setups and orders
        if is_4h_close and symbol in self.pending_setups:
            setup = self.pending_setups[symbol]
            setup.increment_bars()

            bb_width = indicators_4h.get('bb_width', 0)
            if bb_width > 0:
                setup.bb_width_history.append(bb_width)
                max_history = self.config.expansion_lookback + 1
                if len(setup.bb_width_history) > max_history:
                    setup.bb_width_history = setup.bb_width_history[-max_history:]

        if symbol in self.entry_orders:
            self.entry_orders[symbol].increment_bars()

        # State machine dispatch
        result = None
        if current_state == StrategyState.FLAT:
            result = state_machine.handle_flat_state(
                self, symbol, bar, indicators_4h, indicators_1d, is_4h_close
            )
        elif current_state == StrategyState.SETUP_ACTIVE:
            result = state_machine.handle_setup_active_state(
                self, symbol, bar, indicators_4h
            )
        elif current_state == StrategyState.RETEST_ORDER_WORKING:
            result = state_machine.handle_retest_order_working_state(
                self, symbol, bar, indicators_4h
            )
        elif current_state == StrategyState.CHASE_ORDER_WORKING:
            result = state_machine.handle_chase_order_working_state(
                self, symbol, bar, indicators_4h
            )
        elif current_state == StrategyState.IN_POSITION:
            result = state_machine.handle_in_position_state(
                self, symbol, bar, indicators_4h, is_4h_close
            )

        # Convert action dict to StrategySignal if appropriate
        if result is not None:
            action = result.get('action', '')
            if action in ('RETEST_FILLED', 'CHASE_FILLED'):
                return StrategySignal(
                    direction=SignalDirection.BUY,
                    symbol=symbol,
                    timestamp=bar.timestamp,
                    price=result.get('price'),
                    qty=result.get('qty'),
                    post_only=True,
                    reason=action,
                    metadata=result
                )
            elif action in ('STOP_HIT', 'POSITION_CLOSED'):
                return StrategySignal(
                    direction=SignalDirection.SELL,
                    symbol=symbol,
                    timestamp=bar.timestamp,
                    price=result.get('price'),
                    is_stop=action == 'STOP_HIT',
                    reason=action,
                    metadata=result
                )
            elif action in ('TP1_FILLED', 'TP2_FILLED'):
                return StrategySignal(
                    direction=SignalDirection.SELL,
                    symbol=symbol,
                    timestamp=bar.timestamp,
                    price=result.get('price'),
                    qty=result.get('qty'),
                    post_only=True,
                    reason=action,
                    metadata=result
                )

        return None

    def _collect_4h_diagnostics(self, symbol, bar, indicators_4h, indicators_1d,
                                 current_state):
        """Collect diagnostics on every 4h bar (mirrors process_bar_1m diagnostics block)."""
        self.gate_audit['total_4h_bars'] += 1
        self.state_occupancy[current_state] += 1

        bb_width_pct = indicators_4h.get('bb_width_pct', 0)
        self.bb_width_pct_all.append(bb_width_pct)

        regime_ok = filters.check_regime_filter(bar, indicators_1d, self.config)

        viability_ok, was_rescued = filters.check_viability_filter(
            indicators_4h, self.config,
            bb_width_pct_history=self.bb_width_history.get(symbol, []),
        )

        if was_rescued:
            self.viability_rescued_count += 1

        compression_ok = filters.check_compression_only(
            indicators_4h, self.bb_width_history.get(symbol, []), self.config
        )
        setup_ok = filters.check_setup_trigger_only(bar, indicators_4h, self.config)

        compression_signal_now = (bb_width_pct <= self.config.bb_width_pct_threshold)
        self.compression_now_mask.append(compression_signal_now)

        bb_history = self.bb_width_history.get(symbol, [])
        if len(bb_history) >= 6:
            compression_recent_6 = filters.check_recent_compression(
                bb_width_pct_history=bb_history,
                threshold=self.config.bb_width_pct_threshold,
                lookback=6
            )
        else:
            compression_recent_6 = False

        if len(bb_history) >= 12:
            compression_recent_12 = filters.check_recent_compression(
                bb_width_pct_history=bb_history,
                threshold=self.config.bb_width_pct_threshold,
                lookback=12
            )
        else:
            compression_recent_12 = False

        self.compression_recent_6_mask.append(compression_recent_6)
        self.compression_recent_12_mask.append(compression_recent_12)

        self.regime_ok_mask.append(regime_ok)
        self.viability_ok_mask.append(viability_ok)
        self.setup_ok_mask.append(setup_ok)
        self.compression_ok_mask.append(compression_ok)

        if regime_ok:
            self.gate_audit['regime_ok'] += 1
            atr_pct = indicators_4h.get('atr_pct', 0)
            self.atr_pct_regime_ok.append(atr_pct)

        base_ok = regime_ok and viability_ok

        if base_ok:
            self.gate_audit['viability_ok'] += 1
            self.bb_width_pct_viable.append(bb_width_pct)

            if self.config.setup_mode == "roc_score":
                roc_score = indicators_4h.get('roc_score_4h', 0)
                self.roc_score_viable.append(roc_score)

                if roc_score >= self.config.roc_score_thresh:
                    self.gate_audit['roc_ok'] += 1

                    if current_state != StrategyState.FLAT:
                        self.gate_audit['roc_ok_blocked_by_state'] += 1
                    else:
                        self.gate_audit['setup_eval_attempted'] += 1

                    if self.config.use_compression_filter:
                        is_compressed = filters.check_recent_compression(
                            bb_width_pct_history=self.bb_width_history.get(symbol, []),
                            threshold=self.config.bb_width_pct_threshold,
                            lookback=self.config.compression_lookback_4h
                        )
                        if is_compressed:
                            self.gate_audit['roc_ok_and_compressed'] += 1
                    else:
                        self.gate_audit['roc_ok_and_compressed'] += 1

                    if current_state == StrategyState.SETUP_ACTIVE:
                        self.opp_loss['roc_ok_while_setup_active'] += 1
                    elif current_state == StrategyState.IN_POSITION:
                        self.opp_loss['roc_ok_while_position_open'] += 1

            if compression_recent_12:
                if current_state == StrategyState.SETUP_ACTIVE:
                    self.opp_loss['compression_while_setup_active'] += 1
                elif current_state == StrategyState.IN_POSITION:
                    self.opp_loss['compression_while_position_open'] += 1

    # --- Observability ---

    def get_metrics(self) -> StrategyMetrics:
        stats = diagnostics.get_statistics(self)
        return StrategyMetrics(
            total_trades=stats.get('trades', 0),
            winning_trades=len([t for t in self.trades if t.net_pnl > 0]),
            losing_trades=len([t for t in self.trades if t.net_pnl <= 0]),
            gross_pnl=stats.get('gross_pnl', 0),
            total_fees=stats.get('fees', 0),
            net_pnl=stats.get('net_pnl', 0),
            win_rate=stats.get('win_rate', 0),
            profit_factor=stats.get('profit_factor', 0),
            avg_win=stats.get('avg_win', 0),
            avg_loss=stats.get('avg_loss', 0),
            extras=stats
        )

    def get_statistics(self) -> dict:
        """Legacy API for backward compatibility with engine."""
        return diagnostics.get_statistics(self)

    def print_gate_audit(self) -> str:
        """Legacy API."""
        return diagnostics.print_gate_audit(self)

    def print_enhanced_diagnostics(self) -> str:
        """Legacy API."""
        return diagnostics.print_enhanced_diagnostics(self)

    def get_current_state(self, symbol: str) -> StrategyState:
        """Get current state for symbol."""
        return self.state.get(symbol, StrategyState.FLAT)

    # --- State persistence ---

    def get_state(self) -> dict:
        return {
            'state': {s: v.value for s, v in self.state.items()},
            'signal_count': self.signal_count,
            'entry_count': self.entry_count,
            'expired_setup_count': self.expired_setup_count,
            'trade_count': len(self.trades),
        }

    def load_state(self, state: dict) -> None:
        self.signal_count = state.get('signal_count', 0)
        self.entry_count = state.get('entry_count', 0)
        self.expired_setup_count = state.get('expired_setup_count', 0)
