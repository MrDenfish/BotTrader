"""
4h Hybrid Maker Strategy - Core State Machine

Implements the fee-aware swing momentum strategy with:
- 4h Donchian breakout setup
- 1D EMA200 regime filter
- Breakout-retest entry (maker-friendly)
- Fee-multiple profit targets (2× and 4× fees)
- ATR% stop and trail
- Post-only limit orders throughout

State machine:
  FLAT → PENDING_SETUP → ENTRY_ORDER_WORKING → IN_POSITION → FLAT

Design intent: trade less, avoid exhaustion, fill maker-first.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime, timedelta
from enum import Enum
import pandas as pd

from config_4h_hybrid import Hybrid4hConfig


# ========== State Enum ==========

class StrategyState(Enum):
    """
    Phase 2.2 clean state machine for pre-entry and position management.

    Pre-entry flow:
      FLAT → SETUP_ACTIVE → RETEST_ORDER_WORKING → CHASE_ORDER_WORKING → IN_POSITION

    States:
    - FLAT: No active setup, no orders, no position
    - SETUP_ACTIVE: Setup exists and valid; order may not be placeable yet
    - RETEST_ORDER_WORKING: Retest post-only bid currently resting
    - CHASE_ORDER_WORKING: Chase post-only bid currently resting (may be repriced)
    - IN_POSITION: Position open and being managed (TPs/trail/stop)
    """
    FLAT = "FLAT"
    SETUP_ACTIVE = "SETUP_ACTIVE"
    RETEST_ORDER_WORKING = "RETEST_ORDER_WORKING"
    CHASE_ORDER_WORKING = "CHASE_ORDER_WORKING"
    IN_POSITION = "IN_POSITION"


# ========== Data Structures ==========

@dataclass
class Setup:
    """Represents a pending setup waiting for entry"""
    symbol: str
    setup_time: datetime
    setup_type: Literal["donchian", "roc_score"]

    # Breakout level (for Donchian)
    breakout_level: float

    # Entry tracking
    retest_occurred: bool = False
    entry_price_target: Optional[float] = None

    # TTL
    ttl_bars_4h: int = 3
    bars_since_setup: int = 0

    # State
    expired: bool = False

    # Phase 2: Compression tracking
    bb_width_pct_at_setup: float = 0.0

    # Phase 2.1: BB width history (for expansion confirmation)
    bb_width_history: list[float] = None  # Last N bb_width values

    # Phase 2: Chase entry tracking
    setup_created_ts: Optional[datetime] = None
    retest_deadline_ts: Optional[datetime] = None
    chase_attempted: bool = False
    chase_order_placed_ts: Optional[datetime] = None
    chase_deadline_ts: Optional[datetime] = None

    # Phase 2.1c: Price action tracking during TTL (for expired setup diagnostics)
    min_low_during_ttl: float = float('inf')
    max_high_during_ttl: float = 0.0

    # Phase 2.2: Unified entry order tracking (replaces separate EntryOrder)
    entry_order_active: bool = False
    entry_order_type: Optional[Literal["RETEST", "CHASE"]] = None
    entry_price: Optional[float] = None  # Current working bid price
    entry_created_ts: Optional[datetime] = None
    entry_last_update_ts: Optional[datetime] = None
    entry_reprices: int = 0
    entry_price_history: list[float] = None  # Track all reprice levels
    setup_deadline_ts: Optional[datetime] = None  # Hard stop (setup TTL)

    def __post_init__(self):
        """Initialize mutable defaults"""
        if self.bb_width_history is None:
            self.bb_width_history = []
        if self.entry_price_history is None:
            self.entry_price_history = []

    def increment_bars(self):
        """Age the setup"""
        self.bars_since_setup += 1
        if self.bars_since_setup >= self.ttl_bars_4h:
            self.expired = True


@dataclass
class Position:
    """Represents an active position"""
    symbol: str
    entry_time: datetime
    entry_price: float
    qty: float  # Position quantity in base currency

    # Risk management
    stop_price: float

    # Profit targets (post-only sell limits)
    tp1_price: float
    tp1_qty: float
    tp2_price: float
    tp2_qty: float
    runner_qty: float

    # Entry fee (required, no default)
    entry_fee: float

    # Defaults
    tp1_filled: bool = False
    tp1_fill_time: Optional[datetime] = None
    tp2_filled: bool = False
    tp2_fill_time: Optional[datetime] = None

    # Runner management
    trail_price: Optional[float] = None
    highest_close_4h_since_entry: float = 0.0
    trail_mult: float = 2.0  # Phase 2.4: Adaptive trail multiplier based on entry context

    # Fees tracking
    exit_fees: float = 0.0

    # P&L tracking
    gross_pnl: float = 0.0
    net_pnl: float = 0.0

    # Diagnostics
    mfe_pct: float = 0.0  # Max favorable excursion
    mae_pct: float = 0.0  # Max adverse excursion
    bars_held: int = 0

    # Phase 2: Entry diagnostics (stored temporarily)
    entry_type_diagnostic: str = "RETEST"
    entry_wait_minutes_diagnostic: float = 0.0

    # Phase 2.4: Stage C - Compression context at entry (for runner policy)
    entry_compressed_recent_12: bool = False  # Was compression_recent_12 True at entry?


@dataclass
class EntryOrder:
    """Represents a working entry order (post-only buy limit)"""
    symbol: str
    limit_price: float
    qty: float
    placed_time: datetime
    ttl_bars_1m: int = 180
    bars_since_placed: int = 0
    expired: bool = False

    # Phase 2: Chase price tracking
    chase_price: Optional[float] = None

    def increment_bars(self):
        """Age the order"""
        self.bars_since_placed += 1
        if self.bars_since_placed >= self.ttl_bars_1m:
            self.expired = True


@dataclass
class Trade:
    """Completed trade record"""
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime

    # Exit breakdown
    stop_hit: bool = False
    tp1_filled: bool = False
    tp2_filled: bool = False
    runner_exit_reason: Optional[str] = None  # "trail", "time_stop", etc.

    # P&L
    qty_total: float = 0.0
    gross_pnl: float = 0.0
    fees_total: float = 0.0
    net_pnl: float = 0.0

    # Diagnostics
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    bars_held: int = 0

    # Setup info
    setup_type: str = ""
    entry_mode: str = ""

    # Phase 2: Entry diagnostics
    entry_type: Literal["RETEST", "CHASE"] = "RETEST"
    entry_wait_minutes: float = 0.0

    # Phase 2.4: Stage C - Compression context at entry
    entry_compressed_recent_12: bool = False


@dataclass
class ExpiredSetup:
    """Diagnostic record for setups that expired without filling"""
    symbol: str
    setup_time: datetime
    setup_type: str
    breakout_level: float

    # Entry prices that were placed
    retest_limit_price: Optional[float] = None
    chase_limit_price: Optional[float] = None

    # Price action during TTL window
    min_low_during_ttl: float = float('inf')
    max_high_during_ttl: float = 0.0

    # Distance-to-fill analysis
    retest_gap_pct: Optional[float] = None  # (retest_bid - min_low) / breakout_level
    chase_gap_pct: Optional[float] = None   # (chase_bid - min_low) / breakout_level

    # Expiration reason
    expiration_reason: str = ""  # "SETUP_TTL_EXPIRED", "ORDER_TTL_EXPIRED", "CHASE_MAX_EXTENSION", etc.

    # Bollinger compression at setup
    bb_width_pct_at_setup: float = 0.0


# ========== Strategy State Machine ==========

class Hybrid4hStrategy:
    """4h Hybrid Maker Strategy"""

    def __init__(self, config: Hybrid4hConfig):
        self.config = config

        # Phase 2.2: State tracking per symbol (using StrategyState enum)
        self.state: dict[str, StrategyState] = {}
        self.pending_setups: dict[str, Setup] = {}

        # Phase 2.1 (deprecated): Will be removed as we consolidate into Setup
        self.entry_orders: dict[str, EntryOrder] = {}

        self.positions: dict[str, Position] = {}

        # Phase 2.2: Symbol-level BB width history for recent compression check
        self.bb_width_history: dict[str, list[float]] = {}

        # Completed trades
        self.trades: list[Trade] = []

        # Phase 2.1c: Expired setup diagnostics
        self.expired_setups: list[ExpiredSetup] = []

        # Statistics
        self.signal_count = 0
        self.entry_count = 0
        self.expired_setup_count = 0

        # Phase 2: Chase entry statistics
        self.retest_fill_count = 0
        self.chase_attempt_count = 0
        self.chase_fill_count = 0

        # Phase 2.3: Gate audit counters (for diagnostic analysis)
        # Phase 2.4: Split setup stage into explicit gates
        self.gate_audit = {
            'total_4h_bars': 0,
            'regime_ok': 0,
            'viability_ok': 0,
            'compression_ok': 0,          # DEPRECATED in Phase 2.4 (kept for diagnostics)
            'roc_ok': 0,                  # Phase 2.4: ROC-score >= threshold on base_ok bars
            'roc_ok_and_compressed': 0,   # Phase 2.4: ROC-OK bars that also pass compression filter
            'structure_ok': 0,            # Phase 2.4: Additional entry-structure conditions (currently = roc_ok_and_compressed)
            'setup_trigger_ok': 0,        # DEPRECATED: combined roc+structure (kept for compatibility)
            'setup_created': 0,
            'entry_filled': 0,
            # Phase 2.4: Blocker instrumentation (on ROC-OK bars)
            'roc_ok_blocked_by_state': 0,    # State machine not in FLAT
            'setup_eval_attempted': 0         # Actually reached setup evaluation
        }

        # Phase 2.3: Enhanced diagnostics (Section 11 of optimization plan)
        # Lists to collect raw values for distribution analysis
        self.bb_width_pct_all = []           # All 4h bars (sanity check)
        self.bb_width_pct_viable = []        # Only viable bars (regime+viability OK)
        self.atr_pct_regime_ok = []          # ATR% on regime_ok bars
        self.roc_score_viable = []           # ROC-score on viable bars

        # Phase 2.3.1: Gate decision masks (collected on every 4h bar)
        # These are populated during bar processing, decomposition computed in get_statistics()
        self.regime_ok_mask = []             # Boolean: regime filter passed
        self.viability_ok_mask = []          # Boolean: viability filter passed
        self.setup_ok_mask = []              # Boolean: setup trigger passed
        self.compression_ok_mask = []        # Boolean: compression filter passed

        # Setup/compression decomposition counters (computed from masks)
        self.setup_only_count = 0            # Setup trigger OK, compression NOT OK
        self.compression_only_count = 0      # Compression OK, setup trigger NOT OK
        self.intersection_count = 0          # Both setup AND compression OK
        self.neither_count = 0               # Neither setup nor compression OK

        # Phase 2.3.2: Compression variant tracking (same_bar vs recent)
        self.compression_now_mask = []       # Boolean: compression on THIS bar only
        self.compression_recent_6_mask = []  # Boolean: compression in last 6 bars
        self.compression_recent_12_mask = [] # Boolean: compression in last 12 bars

        # Phase 2.3.2: Conditional viability tracking
        self.viability_rescued_count = 0     # Bars rescued by conditional viability

        # Phase 2.4: State occupancy metrics (Option 1 observability)
        self.state_occupancy = {
            StrategyState.FLAT: 0,
            StrategyState.SETUP_ACTIVE: 0,
            StrategyState.RETEST_ORDER_WORKING: 0,
            StrategyState.CHASE_ORDER_WORKING: 0,
            StrategyState.IN_POSITION: 0
        }

        # Phase 2.4: Setup lifecycle metrics (Option 1 observability)
        self.setup_canceled_count = 0
        self.setup_active_durations = []  # List of durations in 4h bars
        self.entry_submitted_count = 0    # Post-only orders placed

        # Phase 2.4: Opportunity loss tracking (Option 1 observability)
        self.opp_loss = {
            'roc_ok_while_setup_active': 0,
            'roc_ok_while_position_open': 0,
            'compression_while_setup_active': 0,
            'compression_while_position_open': 0
        }

        # Phase 2.4: Track setup start time for duration calculation
        self.setup_start_time: dict[str, pd.Timestamp] = {}

    def get_state(self, symbol: str) -> StrategyState:
        """Get current state for symbol"""
        return self.state.get(symbol, StrategyState.FLAT)

    # ========== Main Entry Point ==========

    def process_bar_1m(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict,
        indicators_1d: dict,
        is_4h_close: bool,
        is_1d_close: bool
    ) -> Optional[dict]:
        """
        Process a 1m bar (or signal TF bar) for a symbol.

        Args:
            symbol: Trading symbol
            bar: Current 1m bar (pd.Series with open/high/low/close/volume)
            indicators_4h: 4h indicators (atr_pct, donch_high, etc.)
            indicators_1d: 1D indicators (ema200, ema50, etc.)
            is_4h_close: True if this 1m bar is a 4h boundary
            is_1d_close: True if this 1m bar is a 1D boundary

        Returns:
            Optional action dict for logging
        """

        # Initialize state if needed
        if symbol not in self.state:
            self.state[symbol] = StrategyState.FLAT

        # Phase 2.2: Initialize bb_width_pct history if needed
        if symbol not in self.bb_width_history:
            self.bb_width_history[symbol] = []

        # Phase 2.2: Update symbol-level bb_width_pct history on 4h closes
        if is_4h_close:
            bb_width_pct = indicators_4h.get('bb_width_pct', None)
            if bb_width_pct is not None:
                self.bb_width_history[symbol].append(bb_width_pct)
                # Keep only last compression_lookback_4h + some buffer
                max_history = self.config.compression_lookback_4h + 10
                if len(self.bb_width_history[symbol]) > max_history:
                    self.bb_width_history[symbol] = self.bb_width_history[symbol][-max_history:]

        current_state = self.state[symbol]

        # ========================================================================
        # Phase 2.3.2: CRITICAL - Collect diagnostics on EVERY 4h bar
        # ========================================================================
        # This block MUST execute on EVERY 4h bar regardless of state to ensure
        # masks are populated for ALL 4h bars.

        if is_4h_close:
            # Phase 2.3: Track total 4h bars processed
            self.gate_audit['total_4h_bars'] += 1

            # Phase 2.4: Track state occupancy (Option 1 observability)
            self.state_occupancy[current_state] += 1

            # Phase 2.3: Collect BB width for ALL 4h bars (sanity check)
            bb_width_pct = indicators_4h.get('bb_width_pct', 0)
            self.bb_width_pct_all.append(bb_width_pct)

            # Calculate all gate values
            regime_ok = self._check_regime_filter(bar, indicators_1d)

            # Phase 2.3.2: Updated viability call with conditional logic
            viability_ok, was_rescued = self._check_viability_filter(
                indicators_4h,
                bb_width_pct_history=self.bb_width_history.get(symbol, [])
            )

            # Track rescued count
            if was_rescued:
                self.viability_rescued_count += 1

            # Check setup and compression independently (always)
            compression_ok = self._check_compression_only(
                indicators_4h, self.bb_width_history.get(symbol, [])
            )
            setup_ok = self._check_setup_trigger_only(bar, indicators_4h)

            # Phase 2.3.2: Track compression SIGNALS (always computed, regardless of gate)
            # Separate signal detection from gating behavior:
            # - compression_signal_now = raw signal (bb_width_pct <= threshold)
            # - compression_gate_enabled = whether gate is used in strategy
            # Even with gate OFF, we still compute signals for diagnostics

            compression_signal_now = (bb_width_pct <= self.config.bb_width_pct_threshold)
            self.compression_now_mask.append(compression_signal_now)

            # Phase 2.3.2: Track compression_recent variants (derived from signal history)
            bb_history = self.bb_width_history.get(symbol, [])
            if len(bb_history) >= 6:
                compression_recent_6 = self._check_recent_compression(
                    bb_width_pct_history=bb_history,
                    threshold=self.config.bb_width_pct_threshold,
                    lookback=6
                )
            else:
                compression_recent_6 = False

            if len(bb_history) >= 12:
                compression_recent_12 = self._check_recent_compression(
                    bb_width_pct_history=bb_history,
                    threshold=self.config.bb_width_pct_threshold,
                    lookback=12
                )
            else:
                compression_recent_12 = False

            self.compression_recent_6_mask.append(compression_recent_6)
            self.compression_recent_12_mask.append(compression_recent_12)

            # Store masks for decomposition analysis
            self.regime_ok_mask.append(regime_ok)
            self.viability_ok_mask.append(viability_ok)
            self.setup_ok_mask.append(setup_ok)
            self.compression_ok_mask.append(compression_ok)

            # Update gate audit counters (for compatibility with existing reports)
            if regime_ok:
                self.gate_audit['regime_ok'] += 1
                atr_pct = indicators_4h.get('atr_pct', 0)
                self.atr_pct_regime_ok.append(atr_pct)

            # base_ok = regime_ok AND viability_ok (the ONLY definition of "viable")
            base_ok = regime_ok and viability_ok

            if base_ok:
                self.gate_audit['viability_ok'] += 1

                # Collect distributions on viable bars
                self.bb_width_pct_viable.append(bb_width_pct)

                if self.config.setup_mode == "roc_score":
                    roc_score = indicators_4h.get('roc_score_4h', 0)
                    self.roc_score_viable.append(roc_score)

                    # Stage A: ROC-OK instrumentation (base_ok & roc_score >= thresh)
                    # This counts bars where ROC condition is met, BEFORE compression filter
                    if roc_score >= self.config.roc_score_thresh:
                        self.gate_audit['roc_ok'] += 1

                        # Phase 2.4: Check if ROC-OK bar is blocked by state machine
                        current_state = self.state.get(symbol, StrategyState.FLAT)
                        if current_state != StrategyState.FLAT:
                            self.gate_audit['roc_ok_blocked_by_state'] += 1
                        else:
                            # State is FLAT, setup evaluation will be attempted
                            self.gate_audit['setup_eval_attempted'] += 1

                        # Stage A+: Check if this ROC-OK bar also passes compression filter
                        # This tells us exactly how many ROC-OK bars are lost to compression
                        if self.config.use_compression_filter:
                            is_compressed = self._check_recent_compression(
                                bb_width_pct_history=self.bb_width_history.get(symbol, []),
                                threshold=self.config.bb_width_pct_threshold,
                                lookback=self.config.compression_lookback_4h
                            )
                            if is_compressed:
                                self.gate_audit['roc_ok_and_compressed'] += 1
                        else:
                            # No compression filter = all ROC-OK bars pass
                            self.gate_audit['roc_ok_and_compressed'] += 1

                        # Phase 2.4: Opportunity loss tracking (Option 1 observability)
                        # Track ROC-OK bars missed due to state machine locking
                        if current_state == StrategyState.SETUP_ACTIVE:
                            self.opp_loss['roc_ok_while_setup_active'] += 1
                        elif current_state == StrategyState.IN_POSITION:
                            self.opp_loss['roc_ok_while_position_open'] += 1

                # Phase 2.4: Track compression opportunity loss (on base_ok bars)
                # Check if this bar has compression_recent_12 signal
                if compression_recent_12:
                    if current_state == StrategyState.SETUP_ACTIVE:
                        self.opp_loss['compression_while_setup_active'] += 1
                    elif current_state == StrategyState.IN_POSITION:
                        self.opp_loss['compression_while_position_open'] += 1

        # ========================================================================
        # End of diagnostic collection - continue with normal state machine logic
        # ========================================================================

        # Age pending setups and orders
        if is_4h_close and symbol in self.pending_setups:
            setup = self.pending_setups[symbol]
            setup.increment_bars()

            # Phase 2.1: Track BB width history for expansion confirmation
            bb_width = indicators_4h.get('bb_width', 0)
            if bb_width > 0:
                setup.bb_width_history.append(bb_width)
                # Keep only last N values (expansion_lookback + 1 for comparison)
                max_history = self.config.expansion_lookback + 1
                if len(setup.bb_width_history) > max_history:
                    setup.bb_width_history = setup.bb_width_history[-max_history:]

        if symbol in self.entry_orders:
            self.entry_orders[symbol].increment_bars()

        # Phase 2.2: State machine dispatch with StrategyState enum
        if current_state == StrategyState.FLAT:
            return self._handle_flat_state(symbol, bar, indicators_4h, indicators_1d, is_4h_close)

        elif current_state == StrategyState.SETUP_ACTIVE:
            return self._handle_setup_active_state(symbol, bar, indicators_4h)

        elif current_state == StrategyState.RETEST_ORDER_WORKING:
            return self._handle_retest_order_working_state(symbol, bar, indicators_4h)

        elif current_state == StrategyState.CHASE_ORDER_WORKING:
            return self._handle_chase_order_working_state(symbol, bar, indicators_4h)

        elif current_state == StrategyState.IN_POSITION:
            return self._handle_in_position_state(symbol, bar, indicators_4h, is_4h_close)

        return None

    # ========== State Handlers ==========

    def _handle_flat_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict,
        indicators_1d: dict,
        is_4h_close: bool
    ) -> Optional[dict]:
        """Check for new setup on 4h close"""

        # Early return if not a 4h close (nothing to check in FLAT state)
        if not is_4h_close:
            return None

        # Phase 2.3.2: Check base filters (regime + viability)
        # NOTE: These checks are ALSO done in process_bar_1m() for diagnostics,
        # but we need the results here for gate logic.
        regime_ok = self._check_regime_filter(bar, indicators_1d)
        viability_ok, _ = self._check_viability_filter(
            indicators_4h,
            bb_width_pct_history=self.bb_width_history.get(symbol, [])
        )

        base_ok = regime_ok and viability_ok

        # Early return if base filters don't pass
        if not base_ok:
            return None

        # ========================================================================
        # Continue processing setup trigger (only reached on 4h close with base_ok)
        # ========================================================================

        # Check setup trigger (includes compression check internally)
        if self.config.setup_mode == "donchian":
            # Phase 2.2: Pass bb_width_pct_history for recent compression check
            triggered, breakout_level = self._check_donchian_setup(
                bar, indicators_4h, self.bb_width_history.get(symbol, [])
            )
        elif self.config.setup_mode == "roc_score":
            # Phase 2.3: ROC-score momentum setup
            triggered, breakout_level = self._check_roc_score_setup(
                bar, indicators_4h, self.bb_width_history.get(symbol, [])
            )
        else:
            # Unknown mode
            triggered, breakout_level = False, 0.0

        if triggered:
            # Stage A: STRUCTURE-OK instrumentation (exact conditions before setup creation)
            # Currently this includes: ROC threshold + compression filter (if enabled)
            # This is the last gate before setup creation
            self.gate_audit['structure_ok'] += 1

            # Keep setup_trigger_ok for compatibility
            self.gate_audit['setup_trigger_ok'] += 1
        else:
            return None

        # Phase 2.2: Create setup with all deadlines calculated upfront
        self.signal_count += 1
        self.gate_audit['setup_created'] += 1

        setup = Setup(
            symbol=symbol,
            setup_time=bar.name,
            setup_type=self.config.setup_mode,
            breakout_level=breakout_level,
            ttl_bars_4h=self.config.setup_ttl_bars_4h,
            # Phase 2.2: Store compression value and timing
            bb_width_pct_at_setup=indicators_4h.get('bb_width_pct', 0),
            setup_created_ts=bar.name,
            # Phase 2.2: Calculate all deadlines immediately
            retest_deadline_ts=bar.name + timedelta(minutes=self.config.retest_ttl_minutes),
            chase_deadline_ts=bar.name + timedelta(
                minutes=self.config.retest_ttl_minutes + self.config.chase_ttl_minutes
            ),
            setup_deadline_ts=bar.name + timedelta(hours=self.config.setup_ttl_bars_4h * 4)
        )

        self.pending_setups[symbol] = setup

        # Phase 2.4: Record setup start time for duration tracking
        self.setup_start_time[symbol] = bar.name

        # Phase 2.2: Attempt immediate retest bid placement
        can_place, retest_price = self._can_place_immediate_retest_bid(bar, breakout_level)

        if can_place:
            # Place immediate retest bid
            qty = self.config.notional_usd / retest_price

            setup.entry_order_active = True
            setup.entry_order_type = "RETEST"
            setup.entry_price = retest_price
            setup.entry_created_ts = bar.name
            setup.entry_last_update_ts = bar.name
            setup.entry_price_history.append(retest_price)

            # Phase 2.4: Track entry order submission
            self.entry_submitted_count += 1

            self.state[symbol] = StrategyState.RETEST_ORDER_WORKING

            return {
                "action": "SETUP_TRIGGERED_WITH_RETEST_BID",
                "setup_type": self.config.setup_mode,
                "breakout_level": breakout_level,
                "retest_price": retest_price,
                "qty": qty
            }
        else:
            # Retest bid not placeable yet - wait in SETUP_ACTIVE
            self.state[symbol] = StrategyState.SETUP_ACTIVE

            return {
                "action": "SETUP_TRIGGERED_WAITING",
                "setup_type": self.config.setup_mode,
                "breakout_level": breakout_level,
                "reason": "retest_bid_not_placeable"
            }

    def _handle_pending_setup_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict
    ) -> Optional[dict]:
        """Wait for breakout retest and place entry order"""

        setup = self.pending_setups[symbol]

        # Phase 2.1c: Track min/max price during TTL
        setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
        setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

        # Check expiry
        if setup.expired:
            self.expired_setup_count += 1

            # Phase 2.4: Track setup duration on expiry
            if symbol in self.setup_start_time:
                duration_4h_bars = (bar.name - self.setup_start_time[symbol]).total_seconds() / (60 * 240)  # 4h = 240 minutes
                self.setup_active_durations.append(duration_4h_bars)
                del self.setup_start_time[symbol]

            self.state[symbol] = StrategyState.FLAT
            del self.pending_setups[symbol]
            return {"action": "SETUP_EXPIRED"}

        # Check for retest (breakout-retest entry mode)
        if self.config.entry_mode == "breakout_retest":
            retest_occurred, entry_price = self._check_breakout_retest(bar, setup)

            if retest_occurred:
                # Place post-only buy limit
                if self._can_place_post_only_buy(bar, entry_price):
                    qty = self.config.notional_usd / entry_price

                    order = EntryOrder(
                        symbol=symbol,
                        limit_price=entry_price,
                        qty=qty,
                        placed_time=bar.name,
                        ttl_bars_1m=self.config.entry_ttl_bars_1m
                    )

                    self.entry_orders[symbol] = order
                    self.state[symbol] = StrategyState.RETEST_ORDER_WORKING

                    return {
                        "action": "ENTRY_ORDER_PLACED",
                        "price": entry_price,
                        "qty": qty
                    }

        return None

    def _handle_entry_order_working_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict
    ) -> Optional[dict]:
        """Check if entry order filled"""

        order = self.entry_orders[symbol]
        setup = self.pending_setups[symbol]

        # Phase 2.1c: Track min/max price during TTL
        setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
        setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

        # Check order expiry
        if order.expired:
            del self.entry_orders[symbol]
            # Setup remains pending, can try again
            self.state[symbol] = StrategyState.SETUP_ACTIVE
            return {"action": "ENTRY_ORDER_EXPIRED"}

        # Check if setup expired
        if setup.expired:
            self.expired_setup_count += 1
            del self.entry_orders[symbol]
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "SETUP_EXPIRED_WHILE_ORDER_WORKING"}

        # Phase 2: Check if retest TTL expired -> escalate to chase
        if bar.name >= setup.retest_deadline_ts and not setup.chase_attempted:
            self._place_chase_order(symbol, bar, setup, order, indicators_4h)

        # Phase 2: Check chase fill (if chase order active)
        if setup.chase_attempted and order.chase_price is not None:
            # Check chase TTL expiry first
            if bar.name >= setup.chase_deadline_ts:
                # Chase expired
                self.expired_setup_count += 1
                del self.entry_orders[symbol]
                del self.pending_setups[symbol]
                self.state[symbol] = StrategyState.FLAT
                return {"action": "CHASE_EXPIRED"}

            # Check if chase filled
            if bar['low'] <= order.chase_price:
                # Chase filled!
                self.entry_count += 1
                self.gate_audit['entry_filled'] += 1
                self.chase_fill_count += 1

                entry_wait_minutes = (bar.name - setup.setup_created_ts).total_seconds() / 60

                # Phase 2.4: Track setup duration on fill (successful conversion)
                if symbol in self.setup_start_time:
                    duration_4h_bars = (bar.name - self.setup_start_time[symbol]).total_seconds() / (60 * 240)  # 4h = 240 minutes
                    self.setup_active_durations.append(duration_4h_bars)
                    del self.setup_start_time[symbol]

                # Calculate fees (maker)
                notional_entry = abs(order.qty * order.chase_price)
                entry_fee = notional_entry * self.config.maker_fee

                # Initialize position with chase entry
                position = self._create_position(
                    symbol, bar.name, order.chase_price, order.qty, entry_fee, indicators_4h,
                    entry_type="CHASE", entry_wait_minutes=entry_wait_minutes
                )

                self.positions[symbol] = position
                self.state[symbol] = StrategyState.IN_POSITION

                # Cleanup
                del self.entry_orders[symbol]
                del self.pending_setups[symbol]

                return {
                    "action": "CHASE_FILLED",
                    "price": order.chase_price,
                    "qty": order.qty,
                    "fee": entry_fee,
                    "wait_minutes": entry_wait_minutes
                }

        # Check for retest fill (conservative: low must touch limit price)
        if bar['low'] <= order.limit_price:
            # RETEST FILLED!
            self.entry_count += 1
            self.gate_audit['entry_filled'] += 1
            self.retest_fill_count += 1

            entry_wait_minutes = (bar.name - setup.setup_created_ts).total_seconds() / 60

            # Phase 2.4: Track setup duration on fill (successful conversion)
            if symbol in self.setup_start_time:
                duration_4h_bars = (bar.name - self.setup_start_time[symbol]).total_seconds() / (60 * 240)  # 4h = 240 minutes
                self.setup_active_durations.append(duration_4h_bars)
                del self.setup_start_time[symbol]

            # Calculate fees (maker)
            notional_entry = abs(order.qty * order.limit_price)
            entry_fee = notional_entry * self.config.maker_fee

            # Initialize position with retest entry
            position = self._create_position(
                symbol, bar.name, order.limit_price, order.qty, entry_fee, indicators_4h,
                entry_type="RETEST", entry_wait_minutes=entry_wait_minutes
            )

            self.positions[symbol] = position
            self.state[symbol] = StrategyState.IN_POSITION

            # Cleanup
            del self.entry_orders[symbol]
            del self.pending_setups[symbol]

            return {
                "action": "RETEST_FILLED",
                "price": order.limit_price,
                "qty": order.qty,
                "fee": entry_fee,
                "stop": position.stop_price,
                "tp1": position.tp1_price,
                "tp2": position.tp2_price
            }

        return None

    # ========== Phase 2.2: New State Handlers ==========

    def _handle_setup_active_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict
    ) -> Optional[dict]:
        """
        Phase 2.2: Setup exists but retest bid not yet placeable.

        Logic:
        - Track min/max price during TTL
        - Check if setup expired (setup_deadline_ts)
        - Try to place retest bid on each bar
        - If placeable → transition to RETEST_ORDER_WORKING
        - If setup expires → record diagnostics, transition to FLAT
        """
        setup = self.pending_setups[symbol]

        # Track min/max price during TTL
        setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
        setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

        # Check if setup expired
        if bar.name >= setup.setup_deadline_ts:
            self._record_expired_setup(symbol, setup, "setup_ttl_expired_before_placeable")
            self.expired_setup_count += 1
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "SETUP_EXPIRED_BEFORE_PLACEABLE"}

        # Try to place retest bid
        can_place, retest_price = self._can_place_immediate_retest_bid(bar, setup.breakout_level)

        if can_place:
            # Now placeable - place retest bid
            qty = self.config.notional_usd / retest_price

            setup.entry_order_active = True
            setup.entry_order_type = "RETEST"
            setup.entry_price = retest_price
            setup.entry_created_ts = bar.name
            setup.entry_last_update_ts = bar.name
            setup.entry_price_history.append(retest_price)

            self.state[symbol] = StrategyState.RETEST_ORDER_WORKING

            return {
                "action": "RETEST_BID_NOW_PLACEABLE",
                "retest_price": retest_price,
                "qty": qty
            }

        # Still not placeable - remain in SETUP_ACTIVE
        return None

    def _handle_retest_order_working_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict
    ) -> Optional[dict]:
        """
        Phase 2.2: Retest bid is resting, waiting for fill or escalation.

        Logic:
        - Track min/max price during TTL
        - Check if filled (bar['low'] <= entry_price)
        - Check if retest deadline reached → escalate to chase or expire
        - Check if setup expired → record diagnostics, transition to FLAT
        """
        setup = self.pending_setups[symbol]

        # Track min/max price during TTL
        setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
        setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

        # Check if filled (conservative: low must touch limit price)
        if bar['low'] <= setup.entry_price:
            # RETEST FILLED!
            self.entry_count += 1
            self.gate_audit['entry_filled'] += 1
            self.retest_fill_count += 1

            entry_wait_minutes = (bar.name - setup.setup_created_ts).total_seconds() / 60

            # Calculate fees (maker)
            notional_entry = abs((self.config.notional_usd / setup.entry_price) * setup.entry_price)
            entry_fee = notional_entry * self.config.maker_fee

            qty = self.config.notional_usd / setup.entry_price

            # Initialize position with retest entry
            position = self._create_position(
                symbol, bar.name, setup.entry_price, qty, entry_fee, indicators_4h,
                entry_type="RETEST", entry_wait_minutes=entry_wait_minutes
            )

            self.positions[symbol] = position
            self.state[symbol] = StrategyState.IN_POSITION

            # Cleanup
            del self.pending_setups[symbol]

            return {
                "action": "RETEST_FILLED",
                "price": setup.entry_price,
                "qty": qty,
                "fee": entry_fee,
                "wait_minutes": entry_wait_minutes,
                "stop": position.stop_price,
                "tp1": position.tp1_price,
                "tp2": position.tp2_price
            }

        # Check if retest deadline reached
        if bar.name >= setup.retest_deadline_ts:
            if self.config.enable_chase_entry:
                # Escalate to chase
                self.chase_attempt_count += 1

                atr_pct_4h = indicators_4h.get('atr_pct', 0.01)
                chase_offset = self._calculate_chase_offset(atr_pct_4h)
                chase_price = bar['close'] * (1 - chase_offset)

                # Post-only check
                if self._can_place_post_only_buy(bar, chase_price):
                    setup.entry_order_type = "CHASE"
                    setup.entry_price = chase_price
                    setup.entry_last_update_ts = bar.name
                    setup.entry_reprices = 0
                    setup.entry_price_history.append(chase_price)

                    # Phase 2.4: Track entry order submission
                    self.entry_submitted_count += 1

                    self.state[symbol] = StrategyState.CHASE_ORDER_WORKING

                    return {
                        "action": "ESCALATED_TO_CHASE",
                        "chase_price": chase_price,
                        "chase_offset": chase_offset
                    }
                else:
                    # Chase not placeable - expire
                    self._record_expired_setup(symbol, setup, "chase_not_placeable_after_retest_ttl")
                    self.expired_setup_count += 1
                    del self.pending_setups[symbol]
                    self.state[symbol] = StrategyState.FLAT
                    return {"action": "CHASE_NOT_PLACEABLE_EXPIRED"}
            else:
                # Chase disabled - expire
                self._record_expired_setup(symbol, setup, "retest_ttl_expired_chase_disabled")
                self.expired_setup_count += 1
                del self.pending_setups[symbol]
                self.state[symbol] = StrategyState.FLAT
                return {"action": "RETEST_TTL_EXPIRED"}

        # Check if setup expired (hard stop)
        if bar.name >= setup.setup_deadline_ts:
            self._record_expired_setup(symbol, setup, "setup_ttl_expired_during_retest")
            self.expired_setup_count += 1
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "SETUP_EXPIRED_DURING_RETEST"}

        # Still waiting for fill
        return None

    def _handle_chase_order_working_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict
    ) -> Optional[dict]:
        """
        Phase 2.2: Chase bid is resting, may be repriced.

        Logic:
        - Track min/max price during TTL
        - Check if filled (bar['low'] <= entry_price)
        - Check chase max-extension guard
        - Check if should reprice (time interval + max reprices)
        - Check if chase expired (chase_deadline_ts)
        - Check if setup expired (setup_deadline_ts)
        """
        setup = self.pending_setups[symbol]

        # Track min/max price during TTL
        setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
        setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

        # Check if filled (conservative: low must touch limit price)
        if bar['low'] <= setup.entry_price:
            # CHASE FILLED!
            self.entry_count += 1
            self.gate_audit['entry_filled'] += 1
            self.chase_fill_count += 1

            entry_wait_minutes = (bar.name - setup.setup_created_ts).total_seconds() / 60

            # Calculate fees (maker)
            notional_entry = abs((self.config.notional_usd / setup.entry_price) * setup.entry_price)
            entry_fee = notional_entry * self.config.maker_fee

            qty = self.config.notional_usd / setup.entry_price

            # Initialize position with chase entry
            position = self._create_position(
                symbol, bar.name, setup.entry_price, qty, entry_fee, indicators_4h,
                entry_type="CHASE", entry_wait_minutes=entry_wait_minutes
            )

            self.positions[symbol] = position
            self.state[symbol] = StrategyState.IN_POSITION

            # Cleanup
            del self.pending_setups[symbol]

            return {
                "action": "CHASE_FILLED",
                "price": setup.entry_price,
                "qty": qty,
                "fee": entry_fee,
                "wait_minutes": entry_wait_minutes,
                "reprices": setup.entry_reprices
            }

        # Check chase max-extension guard
        extension = (bar['close'] / setup.breakout_level) - 1.0
        if extension > self.config.chase_max_extension:
            # Price has run too far - don't chase runaway moves
            self._record_expired_setup(symbol, setup, "chase_max_extension_exceeded")
            self.expired_setup_count += 1
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "CHASE_MAX_EXTENSION_EXCEEDED", "extension": extension}

        # Check if should reprice
        time_since_last_update = (bar.name - setup.entry_last_update_ts).total_seconds() / 60
        can_reprice = (
            time_since_last_update >= self.config.chase_reprice_interval_minutes
            and setup.entry_reprices < self.config.chase_max_reprices
        )

        if can_reprice:
            # Calculate new chase price
            atr_pct_4h = indicators_4h.get('atr_pct', 0.01)
            chase_offset = self._calculate_chase_offset(atr_pct_4h)
            new_chase_price = bar['close'] * (1 - chase_offset)

            # Only reprice UP (more aggressive), with safety limit
            old_price = setup.entry_price
            max_increase = old_price * (1 + self.config.max_step_up_per_reprice)
            new_price_capped = min(new_chase_price, max_increase)

            # Post-only safety: ensure still below current close
            maker_safe_price = bar['close'] * (1 - self.config.maker_safety_offset)
            final_price = min(new_price_capped, maker_safe_price)

            if final_price > old_price:
                # Repricing up
                setup.entry_price = final_price
                setup.entry_reprices += 1
                setup.entry_last_update_ts = bar.name
                setup.entry_price_history.append(final_price)

                return {
                    "action": "CHASE_REPRICED",
                    "old_price": old_price,
                    "new_price": final_price,
                    "reprice_num": setup.entry_reprices
                }

        # Check if chase expired
        if bar.name >= setup.chase_deadline_ts:
            self._record_expired_setup(symbol, setup, "chase_ttl_expired")
            self.expired_setup_count += 1
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "CHASE_TTL_EXPIRED"}

        # Check if setup expired (hard stop)
        if bar.name >= setup.setup_deadline_ts:
            self._record_expired_setup(symbol, setup, "setup_ttl_expired_during_chase")
            self.expired_setup_count += 1
            del self.pending_setups[symbol]
            self.state[symbol] = StrategyState.FLAT
            return {"action": "SETUP_EXPIRED_DURING_CHASE"}

        # Still waiting for fill
        return None

    def _handle_in_position_state(
        self,
        symbol: str,
        bar: pd.Series,
        indicators_4h: dict,
        is_4h_close: bool
    ) -> Optional[dict]:
        """Manage position: stops, TPs, runner trail"""

        position = self.positions[symbol]
        position.bars_held += 1

        # Update MFE/MAE
        self._update_mfe_mae(position, bar)

        # 1) Check stop (highest priority, taker exit)
        if bar['low'] <= position.stop_price:
            return self._exit_position_stop(symbol, bar, position.stop_price)

        # 2) Check TP1 (post-only sell limit)
        if not position.tp1_filled and bar['high'] >= position.tp1_price:
            return self._fill_tp1(symbol, bar, position)

        # 3) Check TP2 (post-only sell limit)
        if not position.tp2_filled and bar['high'] >= position.tp2_price:
            return self._fill_tp2(symbol, bar, position)

        # 4) Runner trail (only after TP1)
        if position.tp1_filled:
            if is_4h_close:
                self._update_runner_trail(position, bar, indicators_4h)

            # Check trail breach
            if position.trail_price is not None and bar['low'] <= position.trail_price:
                return self._exit_runner_trail(symbol, bar, position.trail_price)

        return None

    # ========== Helper: Regime Filter ==========

    def _check_regime_filter(self, bar: pd.Series, indicators_1d: dict) -> bool:
        """
        Phase 2.3.2: Enhanced regime filter with slope options and softening band.

        BUG FIX: Removed "ema200 > 0" check (always true for crypto, was no-op).
        """
        close = bar['close']
        ema200 = indicators_1d.get('ema200', 0)

        # Sanity: reject if ema200 is actually zero/missing
        if ema200 == 0:
            return False

        # Phase 2.3.2: Price criterion with optional softening band
        # Hard mode: close > ema200
        # Soft mode: close > ema200 * (1 - band) AND ema200_slope >= 0
        band = self.config.regime_band_pct if hasattr(self.config, 'regime_band_pct') else 0.0

        if close > ema200:
            price_ok = True
        elif band > 0:
            # Softening: allow if close within band AND ema200 trending up/flat
            ema200_slope = indicators_1d.get('ema200_slope', 0)
            price_ok = (close > ema200 * (1 - band)) and (ema200_slope >= 0)
        else:
            price_ok = False

        if not price_ok:
            return False

        # Phase 2.3.2: Optional EMA200 slope filter
        if hasattr(self.config, 'use_ema200_slope_filter') and self.config.use_ema200_slope_filter:
            ema200_slope = indicators_1d.get('ema200_slope', 0)
            if ema200_slope <= 0:
                return False

        # Phase 2.3.2: Optional EMA50 slope filter
        if hasattr(self.config, 'use_ema50_slope_filter') and self.config.use_ema50_slope_filter:
            ema50_slope = indicators_1d.get('ema50_slope', 0)
            if ema50_slope <= 0:
                return False

        return True

    # ========== Helper: Viability Filter ==========

    def _check_viability_filter(self, indicators_4h: dict, bb_width_pct_history: list = None) -> tuple:
        """
        Phase 2.3.2: Enhanced viability with conditional threshold.

        Returns:
            (viability_ok, was_rescued): viability_ok is final result, was_rescued indicates
                                         if conditional viability saved this bar
        """
        atr_pct = indicators_4h.get('atr_pct', 0)
        fee_rt = self.config.round_trip_maker_fee

        # Standard viability
        threshold_normal = self.config.vol_min_mult * fee_rt
        viability_normal = atr_pct >= threshold_normal

        if viability_normal:
            return (True, False)  # Passed without rescue

        # Phase 2.3.2: Conditional viability (compression-aware)
        if hasattr(self.config, 'use_conditional_viability') and self.config.use_conditional_viability:
            if bb_width_pct_history is not None and len(bb_width_pct_history) >= 6:
                # Check if compression_recent is True
                compression_recent = self._check_recent_compression(
                    bb_width_pct_history=bb_width_pct_history,
                    threshold=self.config.bb_width_pct_threshold,
                    lookback=6
                )

                if compression_recent:
                    threshold_compression = self.config.vol_min_mult_compression * fee_rt
                    viability_compression = atr_pct >= threshold_compression

                    if viability_compression:
                        return (True, True)  # Rescued by conditional viability

        return (False, False)  # Failed both

    # ========== Helper: Donchian Setup ==========

    def _check_donchian_setup(
        self,
        bar: pd.Series,
        indicators_4h: dict,
        bb_width_pct_history: list[float]
    ) -> tuple[bool, float]:
        """
        Check if Donchian breakout occurred with compression filter.

        Phase 2.2: Use recent compression check (lookback window) instead of instant check.
        """
        close = bar['close']
        donch_high_prev = indicators_4h.get('donch_high_prev', 0)

        if donch_high_prev == 0:
            return False, 0.0

        if close > donch_high_prev:
            # Phase 2.2: Recent compression filter
            if self.config.use_compression_filter:
                is_compressed = self._check_recent_compression(
                    bb_width_pct_history=bb_width_pct_history,
                    threshold=self.config.bb_width_pct_threshold,
                    lookback=self.config.compression_lookback_4h
                )
                if not is_compressed:
                    # No recent compression - skip setup
                    return False, 0.0

            return True, donch_high_prev

        return False, 0.0

    # ========== Helper: ROC-Score Setup (Phase 2.3) ==========

    def _check_roc_score_setup(
        self,
        bar: pd.Series,
        indicators_4h: dict,
        bb_width_pct_history: list[float] = None  # Phase 2.4: No longer used, kept for backward compatibility
    ) -> tuple[bool, float]:
        """
        Check if ROC-score setup is triggered.

        Phase 2.3: Momentum-based setup using normalized rate of change.

        Logic:
        - ROC-score > threshold (e.g., 1.2)
        - Price > 0 (positive momentum)
        - Optional: Compression filter (same as Donchian)

        Returns:
            (triggered, breakout_level) where breakout_level = current close
        """
        close = bar['close']
        roc_score = indicators_4h.get('roc_score_4h', 0)

        # Check if ROC-score exceeds threshold (strong normalized momentum)
        if roc_score < self.config.roc_score_thresh:
            return False, 0.0

        # Phase 2.4 - Stage B: Compression filter REMOVED from setup gating
        # Previously this blocked 67 out of 68 ROC-eligible setups (98.5% rejection rate)
        # Compression is now captured as entry-time context for exit policy (Stage C)

        # Triggered! Use current close as "breakout level" for entry calculations
        return True, close

    # ========== Phase 2.3: Helper Methods for Setup/Compression Decomposition ==========

    def _check_compression_only(
        self,
        indicators_4h: dict,
        bb_width_pct_history: list[float]
    ) -> bool:
        """
        Check ONLY compression filter (independent of setup trigger).
        Used for decomposition analysis (Section 11.5).
        """
        if not self.config.use_compression_filter:
            return True  # No filter = always passes

        return self._check_recent_compression(
            bb_width_pct_history=bb_width_pct_history,
            threshold=self.config.bb_width_pct_threshold,
            lookback=self.config.compression_lookback_4h
        )

    def _check_setup_trigger_only(
        self,
        bar: pd.Series,
        indicators_4h: dict
    ) -> bool:
        """
        Check ONLY setup trigger (independent of compression filter).
        Used for decomposition analysis (Section 11.5).
        """
        close = bar['close']

        if self.config.setup_mode == "donchian":
            donch_high_prev = indicators_4h.get('donch_high_prev', 0)
            if donch_high_prev == 0:
                return False
            return close > donch_high_prev

        elif self.config.setup_mode == "roc_score":
            roc_score = indicators_4h.get('roc_score_4h', 0)
            return roc_score >= self.config.roc_score_thresh

        return False

    # ========== Helper: Breakout Retest Entry ==========

    def _check_breakout_retest(self, bar: pd.Series, setup: Setup) -> tuple[bool, float]:
        """Check if price retested breakout level and place entry"""

        level = setup.breakout_level
        retest_upper = level * (1 + self.config.retest_band)

        # Retest: low touches within band above level
        if bar['low'] <= retest_upper and bar['close'] > level:
            # Entry price: slightly below level (post-only)
            entry_price = level * (1 - self.config.entry_offset)
            return True, entry_price

        return False, 0.0

    # ========== Helper: Chase Entry (Phase 2) ==========

    def _place_chase_order(
        self,
        symbol: str,
        bar: pd.Series,
        setup: Setup,
        entry_order: EntryOrder,
        indicators_4h: dict
    ) -> None:
        """
        Place chase entry order (Phase 2 fallback).

        If retest doesn't fill within TTL, chase at current price.
        Still post-only for maker fees.

        Phase 2.1 hardening:
        - Max-extension guard: Don't chase if price has run too far
        - Dynamic ATR-scaled offset: Adapt to volatility
        - Expansion confirmation: Only chase if BB expanding
        """
        if not self.config.enable_chase_entry:
            return

        if setup.chase_attempted:
            return  # Already tried

        # Phase 2.1: Max-extension guard
        extension = (bar['close'] / setup.breakout_level) - 1.0
        if extension > self.config.chase_max_extension:
            # Price has run too far - don't chase runaway moves
            self.expired_setup_count += 1  # Count as expired
            return

        # Phase 2.1: Expansion confirmation
        if self.config.require_expansion_for_chase:
            # Check if BB width is expanding (volatility increasing)
            lookback = self.config.expansion_lookback
            if len(setup.bb_width_history) >= lookback + 1:
                # Check if current width > all previous widths in lookback window
                current_width = setup.bb_width_history[-1]
                previous_widths = setup.bb_width_history[-(lookback+1):-1]

                is_expanding = current_width > max(previous_widths) if previous_widths else False

                if not is_expanding:
                    # BB not expanding - don't chase
                    self.expired_setup_count += 1  # Count as expired
                    return

        # Phase 2.1: Dynamic chase offset (ATR-based)
        if self.config.use_dynamic_chase_offset:
            atr_pct = indicators_4h.get('atr_pct', 0)
            if atr_pct > 0:
                chase_offset = self.config.chase_atr_mult * atr_pct
            else:
                chase_offset = self.config.chase_offset  # Fallback to fixed
        else:
            chase_offset = self.config.chase_offset

        # Calculate chase price
        chase_price = bar['close'] * (1.0 - chase_offset)

        # Mark chase attempted
        setup.chase_attempted = True
        setup.chase_order_placed_ts = bar.name
        setup.chase_deadline_ts = bar.name + timedelta(minutes=self.config.chase_ttl_minutes)

        # Store chase price in entry_order
        entry_order.chase_price = chase_price

        # Update chase statistics
        self.chase_attempt_count += 1

    # ========== Helper: Post-Only Placement ==========

    def _can_place_post_only_buy(self, bar: pd.Series, limit_price: float) -> bool:
        """Check if post-only buy can be placed (must not cross book)"""
        # Approximation: limit must be below current close
        return limit_price < bar['close']

    # ========== Phase 2.2 Helpers ==========

    def _check_recent_compression(
        self,
        bb_width_pct_history: list[float],
        threshold: float,
        lookback: int
    ) -> bool:
        """
        Check if BB was compressed in last K 4h bars.

        Phase 2.2 A1: Instead of requiring compression at trigger bar,
        allow if ANY of last K bars was compressed.
        """
        if len(bb_width_pct_history) < lookback:
            return False

        recent = bb_width_pct_history[-lookback:]
        return any(pct <= threshold for pct in recent)

    def _calculate_chase_offset(self, atr_pct_4h: float) -> float:
        """
        Calculate volatility-aware chase offset.

        Phase 2.2 B2: Dynamic offset based on ATR.
        """
        if not self.config.use_dynamic_chase_offset:
            return self.config.chase_offset

        if atr_pct_4h <= 0:
            return self.config.chase_offset_min

        dynamic_offset = self.config.chase_atr_mult * atr_pct_4h
        return max(self.config.chase_offset_min, dynamic_offset)

    def _can_place_immediate_retest_bid(
        self,
        bar: pd.Series,
        breakout_level: float
    ) -> tuple[bool, Optional[float]]:
        """
        Check if immediate retest bid is placeable (post-only).

        Phase 2.2 B1: Place bid immediately at setup creation.

        Returns:
            (placeable, bid_price)
        """
        retest_price = breakout_level * (1 - self.config.retest_offset)

        # Post-only check: bid must be below current close
        if retest_price >= bar['close']:
            return (False, None)

        return (True, retest_price)

    def _record_expired_setup(
        self,
        symbol: str,
        setup: Setup,
        reason: str
    ) -> None:
        """
        Record diagnostics for expired setup.

        Phase 2.2 diagnostics: Track why setups fail to fill.
        """
        expired = ExpiredSetup(
            symbol=symbol,
            setup_time=setup.setup_time,
            setup_type=setup.setup_type,
            breakout_level=setup.breakout_level,
            min_low_during_ttl=setup.min_low_during_ttl,
            max_high_during_ttl=setup.max_high_during_ttl,
            expiration_reason=reason,
            bb_width_pct_at_setup=setup.bb_width_pct_at_setup
        )

        # Add entry prices if order was active
        if setup.entry_order_active:
            expired.retest_limit_price = setup.entry_price if setup.entry_order_type == "RETEST" else None
            expired.chase_limit_price = setup.entry_price if setup.entry_order_type == "CHASE" else None

            # Calculate distance-to-fill
            if setup.entry_price and setup.min_low_during_ttl != float('inf'):
                gap = setup.entry_price - setup.min_low_during_ttl
                gap_pct = (gap / setup.breakout_level) * 100

                if setup.entry_order_type == "RETEST":
                    expired.retest_gap_pct = gap_pct
                elif setup.entry_order_type == "CHASE":
                    expired.chase_gap_pct = gap_pct

        self.expired_setups.append(expired)

    # ========== Helper: Create Position ==========

    def _create_position(
        self,
        symbol: str,
        entry_time: datetime,
        entry_price: float,
        qty: float,
        entry_fee: float,
        indicators_4h: dict,
        entry_type: str = "RETEST",
        entry_wait_minutes: float = 0.0
    ) -> Position:
        """Initialize position with stops and targets"""

        atr_pct = indicators_4h['atr_pct']

        # Stop
        stop_price = entry_price * (1 - self.config.stop_mult * atr_pct)

        # Profit targets (fee-multiple vs ATR-multiple, whichever is larger)
        fee_rt = self.config.round_trip_maker_fee

        tp1_return = max(
            self.config.tp1_fee_mult * fee_rt,
            self.config.tp1_atr_mult * atr_pct
        )
        tp2_return = max(
            self.config.tp2_fee_mult * fee_rt,
            self.config.tp2_atr_mult * atr_pct
        )

        tp1_price = entry_price * (1 + tp1_return)
        tp2_price = entry_price * (1 + tp2_return)

        # Phase 2.4: Stage C - Compression-based runner policy
        # Capture compression context at entry time
        bb_width_history = self.bb_width_history.get(symbol, [])
        entry_compressed_recent_12 = False
        compression_now = False
        compression_recent_6 = False

        if len(bb_width_history) >= 1:
            # Check compression_now (current bar)
            compression_now = bb_width_history[-1] <= self.config.bb_width_pct_threshold

        if len(bb_width_history) >= 6:
            compression_recent_6 = self._check_recent_compression(
                bb_width_pct_history=bb_width_history,
                threshold=self.config.bb_width_pct_threshold,
                lookback=6
            )

        if len(bb_width_history) >= 12:
            entry_compressed_recent_12 = self._check_recent_compression(
                bb_width_pct_history=bb_width_history,
                threshold=self.config.bb_width_pct_threshold,
                lookback=12
            )

        # Stage C verification log (if policy enabled)
        if self.config.use_compression_runner_policy:
            print(f"\n🔍 STAGE C VERIFICATION - Fill @ {entry_time}")
            print(f"   Symbol: {symbol}")
            print(f"   Entry price: ${entry_price:.2f}")
            print(f"   Entry type: {entry_type}")
            print(f"   Compression context:")
            print(f"     - compression_now (current bar):    {compression_now}")
            print(f"     - compression_recent_6 (24h):       {compression_recent_6}")
            print(f"     - compression_recent_12 (48h):      {entry_compressed_recent_12}")

            # Phase 2.4: Improved warmup logging - don't dump NaN arrays
            import math
            if len(bb_width_history) < 12:
                # Check if history contains NaNs
                has_nans = any(math.isnan(val) if isinstance(val, float) else False for val in bb_width_history)
                if has_nans or len(bb_width_history) == 0:
                    print(f"   BB width history: WARMUP (insufficient bars: {len(bb_width_history)}/12 available)")
                else:
                    print(f"   BB width history (last {len(bb_width_history)} bars): {bb_width_history}")
            else:
                # Full history available - show last 12 bars only
                recent_12 = bb_width_history[-12:]
                has_nans = any(math.isnan(val) if isinstance(val, float) else False for val in recent_12)
                if has_nans:
                    # Filter out NaNs for cleaner display
                    valid_vals = [v for v in recent_12 if not (isinstance(v, float) and math.isnan(v))]
                    print(f"   BB width history: PARTIAL WARMUP ({len(valid_vals)}/12 valid values)")
                    if len(valid_vals) > 0:
                        print(f"     Valid values: {valid_vals}")
                else:
                    print(f"   BB width history (last 12 bars): {recent_12}")

            print(f"   Threshold: {self.config.bb_width_pct_threshold}")

        # Apply compression-aware runner/trail parameters (if enabled)
        if self.config.use_compression_runner_policy and entry_compressed_recent_12:
            # Compressed entry: use compressed parameters
            runner_qty_frac = self.config.runner_qty_frac_compressed
            trail_mult = self.config.trail_mult_compressed
            if self.config.use_compression_runner_policy:
                print(f"   → Using COMPRESSED runner policy:")
                print(f"     - runner_qty_frac: {runner_qty_frac:.2%}")
                print(f"     - trail_mult: {trail_mult:.1f}x")
        else:
            # Normal entry OR policy disabled: use normal parameters
            runner_qty_frac = self.config.runner_qty_frac_normal
            trail_mult = self.config.trail_mult_normal
            if self.config.use_compression_runner_policy:
                print(f"   → Using NORMAL runner policy:")
                print(f"     - runner_qty_frac: {runner_qty_frac:.2%}")
                print(f"     - trail_mult: {trail_mult:.1f}x")

        # Scale-out quantities (with adaptive runner fraction)
        tp1_qty = qty * self.config.tp1_qty_frac
        tp2_qty = qty * self.config.tp2_qty_frac
        runner_qty = qty * runner_qty_frac

        position = Position(
            symbol=symbol,
            entry_time=entry_time,
            entry_price=entry_price,
            qty=qty,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp1_qty=tp1_qty,
            tp2_price=tp2_price,
            tp2_qty=tp2_qty,
            runner_qty=runner_qty,
            entry_fee=entry_fee,
            highest_close_4h_since_entry=entry_price,
            trail_mult=trail_mult  # Phase 2.4: Adaptive trail multiplier
        )

        # Phase 2: Store entry diagnostics for later trade recording
        position.entry_type_diagnostic = entry_type
        position.entry_wait_minutes_diagnostic = entry_wait_minutes

        # Phase 2.4: Store compression context
        position.entry_compressed_recent_12 = entry_compressed_recent_12

        return position

    # ========== Helper: MFE/MAE ==========

    def _update_mfe_mae(self, position: Position, bar: pd.Series):
        """Update max favorable/adverse excursion"""
        entry = position.entry_price
        mfe = (bar['high'] - entry) / entry
        mae = (bar['low'] - entry) / entry

        position.mfe_pct = max(position.mfe_pct, mfe)
        position.mae_pct = min(position.mae_pct, mae)

    # ========== Exits: Stop ==========

    def _exit_position_stop(self, symbol: str, bar: pd.Series, stop_price: float) -> dict:
        """Exit entire position at stop (taker)"""
        position = self.positions[symbol]

        exit_price = stop_price
        qty = position.qty

        # Calculate fees (taker)
        notional_exit = abs(qty * exit_price)
        exit_fee = notional_exit * self.config.taker_fee

        # P&L
        gross_pnl = (exit_price - position.entry_price) * qty
        fees_total = position.entry_fee + position.exit_fees + exit_fee
        net_pnl = gross_pnl - fees_total

        # Record trade
        trade = Trade(
            symbol=symbol,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=bar.name,
            stop_hit=True,
            qty_total=qty,
            gross_pnl=gross_pnl,
            fees_total=fees_total,
            net_pnl=net_pnl,
            mfe_pct=position.mfe_pct,
            mae_pct=position.mae_pct,
            bars_held=position.bars_held,
            setup_type=self.config.setup_mode,
            entry_mode=self.config.entry_mode,
            # Phase 2: Entry diagnostics
            entry_type=position.entry_type_diagnostic,
            entry_wait_minutes=position.entry_wait_minutes_diagnostic,
            # Phase 2.4: Compression context
            entry_compressed_recent_12=position.entry_compressed_recent_12
        )

        self.trades.append(trade)

        # Cleanup
        del self.positions[symbol]
        self.state[symbol] = StrategyState.FLAT

        return {
            "action": "STOP_HIT",
            "price": exit_price,
            "qty": qty,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl
        }

    # ========== Exits: TP1 ==========

    def _fill_tp1(self, symbol: str, bar: pd.Series, position: Position) -> dict:
        """Fill TP1 (post-only maker)"""

        exit_price = position.tp1_price
        qty = position.tp1_qty

        # Calculate fees (maker)
        notional_exit = abs(qty * exit_price)
        exit_fee = notional_exit * self.config.maker_fee

        # P&L for this scale-out
        pnl_tp1 = (exit_price - position.entry_price) * qty

        position.tp1_filled = True
        position.tp1_fill_time = bar.name
        position.exit_fees += exit_fee
        position.gross_pnl += pnl_tp1

        return {
            "action": "TP1_FILLED",
            "price": exit_price,
            "qty": qty,
            "pnl": pnl_tp1
        }

    # ========== Exits: TP2 ==========

    def _fill_tp2(self, symbol: str, bar: pd.Series, position: Position) -> dict:
        """Fill TP2 (post-only maker)"""

        exit_price = position.tp2_price
        qty = position.tp2_qty

        # Calculate fees (maker)
        notional_exit = abs(qty * exit_price)
        exit_fee = notional_exit * self.config.maker_fee

        # P&L for this scale-out
        pnl_tp2 = (exit_price - position.entry_price) * qty

        position.tp2_filled = True
        position.tp2_fill_time = bar.name
        position.exit_fees += exit_fee
        position.gross_pnl += pnl_tp2

        # If runner is zero, close position entirely
        if position.runner_qty == 0:
            return self._finalize_position(symbol, bar, "TP2_FILLED_NO_RUNNER")

        return {
            "action": "TP2_FILLED",
            "price": exit_price,
            "qty": qty,
            "pnl": pnl_tp2
        }

    # ========== Exits: Runner Trail ==========

    def _update_runner_trail(self, position: Position, bar: pd.Series, indicators_4h: dict):
        """Update trailing stop for runner on 4h closes"""

        close = bar['close']
        atr_pct = indicators_4h['atr_pct']

        # Update highest close
        if close > position.highest_close_4h_since_entry:
            position.highest_close_4h_since_entry = close

        # Phase 2.4: Use position's trail_mult (adaptive based on entry compression context)
        trail_price = position.highest_close_4h_since_entry * (1 - position.trail_mult * atr_pct)

        # Only move trail up, never down
        if position.trail_price is None or trail_price > position.trail_price:
            position.trail_price = trail_price

    def _exit_runner_trail(self, symbol: str, bar: pd.Series, trail_price: float) -> dict:
        """Exit runner at trailing stop (taker)"""

        position = self.positions[symbol]

        exit_price = trail_price
        qty = position.runner_qty

        # Calculate fees (taker)
        notional_exit = abs(qty * exit_price)
        exit_fee = notional_exit * self.config.taker_fee

        # P&L for runner
        pnl_runner = (exit_price - position.entry_price) * qty

        position.exit_fees += exit_fee
        position.gross_pnl += pnl_runner

        return self._finalize_position(symbol, bar, "TRAIL_HIT")

    # ========== Helper: Finalize Position ==========

    def _finalize_position(self, symbol: str, bar: pd.Series, exit_reason: str) -> dict:
        """Finalize and record completed trade"""

        position = self.positions[symbol]

        # Total fees and P&L
        fees_total = position.entry_fee + position.exit_fees
        net_pnl = position.gross_pnl - fees_total

        # Record trade
        trade = Trade(
            symbol=symbol,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=bar.name,
            tp1_filled=position.tp1_filled,
            tp2_filled=position.tp2_filled,
            runner_exit_reason=exit_reason,
            qty_total=position.qty,
            gross_pnl=position.gross_pnl,
            fees_total=fees_total,
            net_pnl=net_pnl,
            mfe_pct=position.mfe_pct,
            mae_pct=position.mae_pct,
            bars_held=position.bars_held,
            setup_type=self.config.setup_mode,
            entry_mode=self.config.entry_mode,
            # Phase 2: Entry diagnostics
            entry_type=position.entry_type_diagnostic,
            entry_wait_minutes=position.entry_wait_minutes_diagnostic,
            # Phase 2.4: Compression context
            entry_compressed_recent_12=position.entry_compressed_recent_12
        )

        self.trades.append(trade)

        # Cleanup
        del self.positions[symbol]
        self.state[symbol] = StrategyState.FLAT

        return {
            "action": "POSITION_CLOSED",
            "reason": exit_reason,
            "gross_pnl": position.gross_pnl,
            "net_pnl": net_pnl,
            "fees": fees_total
        }

    # ========== Statistics ==========

    def _compute_decomposition(self):
        """
        Phase 2.3.1: Compute setup/compression decomposition from gate decision masks.

        This must be called AFTER all bars have been processed to ensure accurate counts.
        Decomposition is computed on base_ok population (regime_ok AND viability_ok).
        """
        import numpy as np

        # Convert masks to numpy arrays for efficient boolean operations
        regime_ok = np.array(self.regime_ok_mask)
        viability_ok = np.array(self.viability_ok_mask)
        setup_ok = np.array(self.setup_ok_mask)

        # Phase 2.3.2: Use compression_recent_12 for decomposition to match universe-level counts
        # This ensures consistency between decomposition section and universe diagnostic counts
        compression_ok = np.array(self.compression_recent_12_mask)

        # Define base_ok as THE ONLY definition of "viable"
        base_ok = regime_ok & viability_ok

        # Decomposition within base_ok population (using compression_recent_12)
        setup_only = base_ok & setup_ok & ~compression_ok
        compression_only = base_ok & ~setup_ok & compression_ok
        intersection = base_ok & setup_ok & compression_ok
        neither = base_ok & ~setup_ok & ~compression_ok

        # Update counters
        self.setup_only_count = int(np.sum(setup_only))
        self.compression_only_count = int(np.sum(compression_only))
        self.intersection_count = int(np.sum(intersection))
        self.neither_count = int(np.sum(neither))

        # Sanity check: these should sum to base_ok count
        total_decomp = (self.setup_only_count + self.compression_only_count +
                       self.intersection_count + self.neither_count)
        base_ok_count = int(np.sum(base_ok))

        if total_decomp != base_ok_count:
            print(f"⚠️  WARNING: Decomposition sum ({total_decomp}) != base_ok count ({base_ok_count})")

    def get_statistics(self) -> dict:
        """Return strategy statistics"""

        # Phase 2.3.2: Fee floor truth print (ONCE per run)
        print()
        print("=" * 80)
        print("FEE FLOOR VIABILITY PARAMETERS (TRUTH LINE)")
        print("=" * 80)
        print(f"  maker_fee:              {self.config.maker_fee:.4f} ({self.config.maker_fee*100:.2f}%)")
        print(f"  taker_fee:              {self.config.taker_fee:.4f} ({self.config.taker_fee*100:.2f}%)")
        print(f"  round_trip_maker_fee:   {self.config.round_trip_maker_fee:.4f} ({self.config.round_trip_maker_fee*100:.2f}%)")
        print(f"  vol_min_mult:           {self.config.vol_min_mult:.2f}")
        print(f"  viability_floor_atr_pct: {self.config.vol_min_mult * self.config.round_trip_maker_fee:.4f} ({(self.config.vol_min_mult * self.config.round_trip_maker_fee)*100:.2f}%)")

        if hasattr(self.config, 'use_conditional_viability') and self.config.use_conditional_viability:
            print(f"  vol_min_mult_compression: {self.config.vol_min_mult_compression:.2f}")
            print(f"  conditional_floor_atr_pct: {self.config.vol_min_mult_compression * self.config.round_trip_maker_fee:.4f} ({(self.config.vol_min_mult_compression * self.config.round_trip_maker_fee)*100:.2f}%)")
            print(f"  bars_rescued:           {self.viability_rescued_count}")

        print("=" * 80)
        print()

        # Phase 2.3.1: Compute decomposition from collected masks
        self._compute_decomposition()

        total_trades = len(self.trades)
        if total_trades == 0:
            return {
                "signals": self.signal_count,
                "entries": self.entry_count,
                "expired": self.expired_setup_count,
                "trades": 0
            }

        # P&L
        gross_pnl = sum(t.gross_pnl for t in self.trades)
        fees_total = sum(t.fees_total for t in self.trades)
        net_pnl = sum(t.net_pnl for t in self.trades)

        # Win rate
        winners = [t for t in self.trades if t.net_pnl > 0]
        win_rate = len(winners) / total_trades

        # Avg win/loss
        avg_win = sum(t.net_pnl for t in winners) / len(winners) if winners else 0
        losers = [t for t in self.trades if t.net_pnl <= 0]
        avg_loss = sum(t.net_pnl for t in losers) / len(losers) if losers else 0

        # Profit factor
        gross_wins = sum(t.gross_pnl for t in self.trades if t.gross_pnl > 0)
        gross_losses = abs(sum(t.gross_pnl for t in self.trades if t.gross_pnl <= 0))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0

        # TP metrics
        tp1_count = sum(1 for t in self.trades if t.tp1_filled)
        tp2_count = sum(1 for t in self.trades if t.tp2_filled)

        # Hold time
        avg_bars = sum(t.bars_held for t in self.trades) / total_trades

        # Phase 2: Chase entry success rate
        chase_success_rate = (self.chase_fill_count / self.chase_attempt_count) if self.chase_attempt_count > 0 else 0

        return {
            "signals": self.signal_count,
            "entries": self.entry_count,
            "expired": self.expired_setup_count,
            "signal_to_entry_pct": (self.entry_count / self.signal_count * 100) if self.signal_count > 0 else 0,
            "trades": total_trades,
            "gross_pnl": gross_pnl,
            "fees": fees_total,
            "net_pnl": net_pnl,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "tp1_hit_pct": (tp1_count / total_trades * 100) if total_trades > 0 else 0,
            "tp2_hit_pct": (tp2_count / total_trades * 100) if total_trades > 0 else 0,
            "avg_bars_held": avg_bars,
            # Phase 2: Entry diagnostics
            "retest_fills": self.retest_fill_count,
            "chase_fills": self.chase_fill_count,
            "chase_attempts": self.chase_attempt_count,
            "chase_success_rate": chase_success_rate,
            # Phase 2.3: Gate audit data
            "gate_audit": self.gate_audit
        }

    def print_gate_audit(self) -> str:
        """
        Phase 2.3: Format gate audit counters as diagnostic table

        Returns formatted string showing pass rates at each gate.
        This helps identify which filter is suppressing signal frequency.
        """
        total = self.gate_audit['total_4h_bars']

        if total == 0:
            return "\n⚠️  No 4h bars processed (gate audit unavailable)\n"

        lines = []
        lines.append("")
        lines.append("=" * 80)
        lines.append("GATE AUDIT: Signal Frequency Diagnostic")
        lines.append("=" * 80)
        lines.append("")
        lines.append(f"{'Gate':<30} {'Count':>10} {'% of 4h bars':>15} {'Conversion':>15}")
        lines.append("-" * 80)

        # Total 4h bars (baseline)
        lines.append(f"{'Total 4h bars processed':<30} {total:>10,} {100.0:>14.1f}% {'-':>15}")

        # Regime filter
        regime_count = self.gate_audit['regime_ok']
        regime_pct = (regime_count / total * 100) if total > 0 else 0
        lines.append(f"{'↳ Regime filter (EMA200)':<30} {regime_count:>10,} {regime_pct:>14.1f}% {'-':>15}")

        # Viability filter
        viable_count = self.gate_audit['viability_ok']
        viable_pct = (viable_count / total * 100) if total > 0 else 0
        viable_conv = (viable_count / regime_count * 100) if regime_count > 0 else 0
        lines.append(f"{'↳ Viability filter (ATR%)':<30} {viable_count:>10,} {viable_pct:>14.1f}% {viable_conv:>14.1f}%")

        # Stage A: ROC-OK gate (Phase 2.4)
        # Counts base_ok bars where roc_score >= threshold (BEFORE compression filter)
        roc_ok_count = self.gate_audit.get('roc_ok', 0)
        roc_ok_pct = (roc_ok_count / total * 100) if total > 0 else 0
        roc_ok_conv = (roc_ok_count / viable_count * 100) if viable_count > 0 else 0
        lines.append(f"{'↳ ROC-OK (momentum >= thresh)':<30} {roc_ok_count:>10,} {roc_ok_pct:>14.1f}% {roc_ok_conv:>14.1f}%")

        # Stage A+: ROC-OK AND COMPRESSED (Phase 2.4)
        # Counts ROC-OK bars that also pass compression filter
        roc_compressed_count = self.gate_audit.get('roc_ok_and_compressed', 0)
        roc_compressed_pct = (roc_compressed_count / total * 100) if total > 0 else 0
        roc_compressed_conv = (roc_compressed_count / roc_ok_count * 100) if roc_ok_count > 0 else 0
        lines.append(f"{'  ↳ ROC-OK + Compressed':<30} {roc_compressed_count:>10,} {roc_compressed_pct:>14.1f}% {roc_compressed_conv:>14.1f}%")

        # Stage A: STRUCTURE-OK gate (Phase 2.4)
        # Counts exact conditions before setup creation (currently: ROC + compression if enabled)
        structure_ok_count = self.gate_audit.get('structure_ok', 0)
        structure_ok_pct = (structure_ok_count / total * 100) if total > 0 else 0
        structure_ok_conv = (structure_ok_count / roc_ok_count * 100) if roc_ok_count > 0 else 0
        lines.append(f"{'↳ Structure-OK (entry conditions)':<30} {structure_ok_count:>10,} {structure_ok_pct:>14.1f}% {structure_ok_conv:>14.1f}%")

        # Setup created
        setup_count = self.gate_audit['setup_created']
        setup_pct = (setup_count / total * 100) if total > 0 else 0
        setup_conv = (setup_count / structure_ok_count * 100) if structure_ok_count > 0 else 0
        lines.append(f"{'↳ Setup created':<30} {setup_count:>10,} {setup_pct:>14.1f}% {setup_conv:>14.1f}%")

        # Entry filled
        entry_count = self.gate_audit['entry_filled']
        entry_pct = (entry_count / total * 100) if total > 0 else 0
        entry_conv = (entry_count / setup_count * 100) if setup_count > 0 else 0
        lines.append(f"{'↳ Entry filled':<30} {entry_count:>10,} {entry_pct:>14.1f}% {entry_conv:>14.1f}%")

        # DEPRECATED counters (kept for backward compatibility)
        lines.append("")
        lines.append("DEPRECATED COUNTERS (Phase 2.3.2 and earlier):")
        trigger_count = self.gate_audit.get('setup_trigger_ok', 0)
        compression_count = self.gate_audit.get('compression_ok', 0)
        lines.append(f"  setup_trigger_ok (old):     {trigger_count:>10,}  (replaced by structure_ok)")
        lines.append(f"  compression_ok (old):       {compression_count:>10,}  (deprecated)")
        lines.append("")

        lines.append("-" * 80)

        # Phase 2.4: State Machine Blocker Instrumentation
        lines.append("")
        lines.append("STATE MACHINE BLOCKER ANALYSIS (Phase 2.4):")
        lines.append("")

        # Get blocker counts
        roc_ok_blocked = self.gate_audit.get('roc_ok_blocked_by_state', 0)
        setup_eval_attempted = self.gate_audit.get('setup_eval_attempted', 0)

        lines.append(f"On ROC-OK bars (N={roc_ok_count}):")
        lines.append(f"  Blocked by state machine:     {roc_ok_blocked:>10,}  ({(roc_ok_blocked / roc_ok_count * 100) if roc_ok_count > 0 else 0:.1f}%)")
        lines.append(f"  Setup evaluation attempted:   {setup_eval_attempted:>10,}  ({(setup_eval_attempted / roc_ok_count * 100) if roc_ok_count > 0 else 0:.1f}%)")
        lines.append("")

        if roc_ok_blocked > 0:
            lines.append("🚨 STATE MACHINE BLOCKING:")
            lines.append("   The strategy state machine only allows new setup evaluation when in FLAT state.")
            lines.append("   After the first setup is created, the state changes to SETUP_ACTIVE,")
            lines.append("   preventing subsequent ROC-OK bars from reaching setup evaluation logic.")
            lines.append("")
            lines.append("   This is expected behavior for single-trade strategies.")
            lines.append("   To evaluate all ROC-OK opportunities, the state machine would need")
            lines.append("   to allow concurrent setups or queue pending signals.")

        lines.append("")
        lines.append("-" * 80)

        # Bottleneck analysis
        lines.append("")
        lines.append("BOTTLENECK ANALYSIS (Stage A - Phase 2.4):")
        lines.append("")

        # Find the gate with lowest conversion rate
        conversions = [
            ("Regime filter", regime_pct, regime_count),
            ("Viability filter", viable_conv, viable_count),
            ("ROC-OK", roc_ok_conv, roc_ok_count),
            ("Structure-OK", structure_ok_conv, structure_ok_count),
            ("Setup creation", setup_conv, setup_count),
            ("Entry fill", entry_conv, entry_count)
        ]

        # Sort by conversion rate
        conversions.sort(key=lambda x: x[1])

        bottleneck_name, bottleneck_pct, bottleneck_count = conversions[0]

        if bottleneck_count == 0:
            lines.append(f"🚨 CRITICAL: {bottleneck_name} blocks 100% of signals!")
            lines.append(f"   This gate passed 0 times out of {total:,} 4h bars.")
        elif bottleneck_pct < 10:
            lines.append(f"🚨 PRIMARY BOTTLENECK: {bottleneck_name} ({bottleneck_pct:.1f}% conversion)")
            lines.append(f"   This is the most restrictive gate in the pipeline.")
        else:
            lines.append(f"⚠️  Lowest conversion: {bottleneck_name} ({bottleneck_pct:.1f}%)")

        lines.append("")
        lines.append("STAGE A VERIFICATION:")
        lines.append("Expected hierarchy: roc_ok >= structure_ok >= setup_created >= entry_filled")
        hierarchy_check = roc_ok_count >= structure_ok_count >= setup_count >= entry_count
        if hierarchy_check:
            lines.append(f"✅ Hierarchy verified: {roc_ok_count} >= {structure_ok_count} >= {setup_count} >= {entry_count}")
        else:
            lines.append(f"❌ Hierarchy VIOLATED: {roc_ok_count} >= {structure_ok_count} >= {setup_count} >= {entry_count}")
            lines.append("   This indicates a bug in Stage A instrumentation!")

        lines.append("")
        lines.append("=" * 80)

        return "\n".join(lines)

    def print_enhanced_diagnostics(self) -> str:
        """
        Phase 2.3: Enhanced diagnostics (Section 11 of optimization plan)

        Includes:
        - Gate audit (standard)
        - BB width distribution (Section 11.1)
        - Setup/Compression decomposition (Section 11.5)
        - Viability filter distribution
        - ROC-score percentile calibration (if ROC mode)
        """
        import numpy as np

        lines = []

        # Phase 2.3.2: Debug - Verify mask lengths match total_4h_bars
        lines.append("")
        lines.append("=" * 80)
        lines.append("DEBUG: MASK LENGTH VERIFICATION")
        lines.append("=" * 80)
        total_4h_bars = self.gate_audit.get('total_4h_bars', 0)
        lines.append(f"  total_4h_bars (expected):         {total_4h_bars}")
        lines.append(f"  len(regime_ok_mask):              {len(self.regime_ok_mask)}")
        lines.append(f"  len(viability_ok_mask):           {len(self.viability_ok_mask)}")
        lines.append(f"  len(setup_ok_mask):               {len(self.setup_ok_mask)}")
        lines.append(f"  len(compression_ok_mask):         {len(self.compression_ok_mask)}")
        lines.append(f"  len(compression_now_mask):        {len(self.compression_now_mask)}")
        lines.append(f"  len(compression_recent_6_mask):   {len(self.compression_recent_6_mask)}")
        lines.append(f"  len(compression_recent_12_mask):  {len(self.compression_recent_12_mask)}")
        lines.append("")

        # Assertions
        assert len(self.regime_ok_mask) == total_4h_bars, \
            f"regime_ok_mask length mismatch: {len(self.regime_ok_mask)} != {total_4h_bars}"
        assert len(self.viability_ok_mask) == total_4h_bars, \
            f"viability_ok_mask length mismatch: {len(self.viability_ok_mask)} != {total_4h_bars}"
        assert len(self.setup_ok_mask) == total_4h_bars, \
            f"setup_ok_mask length mismatch: {len(self.setup_ok_mask)} != {total_4h_bars}"
        assert len(self.compression_ok_mask) == total_4h_bars, \
            f"compression_ok_mask length mismatch: {len(self.compression_ok_mask)} != {total_4h_bars}"
        assert len(self.compression_now_mask) == total_4h_bars, \
            f"compression_now_mask length mismatch: {len(self.compression_now_mask)} != {total_4h_bars}"
        assert len(self.compression_recent_6_mask) == total_4h_bars, \
            f"compression_recent_6_mask length mismatch: {len(self.compression_recent_6_mask)} != {total_4h_bars}"
        assert len(self.compression_recent_12_mask) == total_4h_bars, \
            f"compression_recent_12_mask length mismatch: {len(self.compression_recent_12_mask)} != {total_4h_bars}"

        lines.append("  ✅ All masks have correct length!")
        lines.append("=" * 80)
        lines.append("")

        # Start with standard gate audit
        lines.append(self.print_gate_audit())

        # Section 11.1: BB Width Percentile Distribution (Sanity Check)
        lines.append("")
        lines.append("=" * 80)
        lines.append("BB WIDTH PERCENTILE DISTRIBUTION (Section 11.1 - Sanity Check)")
        lines.append("=" * 80)
        lines.append("")

        if len(self.bb_width_pct_all) > 0:
            # Filter out NaNs and None values
            bb_all_clean = [x for x in self.bb_width_pct_all if x is not None and not np.isnan(x)]
            if len(bb_all_clean) > 0:
                bb_all = np.array(bb_all_clean)
                lines.append("On ALL 4h bars:")
                lines.append(f"  Count:  {len(bb_all)}")
                lines.append(f"  Min:    {np.min(bb_all):.1f}")
                lines.append(f"  P25:    {np.percentile(bb_all, 25):.1f}")
                lines.append(f"  Median: {np.percentile(bb_all, 50):.1f}")
                lines.append(f"  P75:    {np.percentile(bb_all, 75):.1f}")
                lines.append(f"  Max:    {np.max(bb_all):.1f}")
                lines.append("")

        if len(self.bb_width_pct_viable) > 0:
            # Filter out NaNs and None values
            bb_viable_clean = [x for x in self.bb_width_pct_viable if x is not None and not np.isnan(x)]
            if len(bb_viable_clean) > 0:
                bb_viable = np.array(bb_viable_clean)
                viable_count = len(bb_viable)

                lines.append("On VIABLE bars (regime+viability OK):")
                lines.append(f"  Count:  {viable_count}")
                lines.append(f"  Min:    {np.min(bb_viable):.1f}")
                lines.append(f"  P25:    {np.percentile(bb_viable, 25):.1f}")
                lines.append(f"  Median: {np.percentile(bb_viable, 50):.1f}")
                lines.append(f"  P75:    {np.percentile(bb_viable, 75):.1f}")
                lines.append(f"  Max:    {np.max(bb_viable):.1f}")
                lines.append("")

                # User-requested check: % of viable bars with bb_width_pct <= thresholds
                lines.append("Compression Threshold Analysis (% of viable bars):")
                for thresh in [20, 30, 35, 40]:
                    count_below = np.sum(bb_viable <= thresh)
                    pct_below = (count_below / viable_count * 100) if viable_count > 0 else 0
                    lines.append(f"  bb_width_pct <= {thresh}: {count_below:>4} bars ({pct_below:>5.1f}%)")

                lines.append("")
                current_thresh = self.config.bb_width_pct_threshold
                lines.append(f"Current threshold: {current_thresh}")
                count_current = np.sum(bb_viable <= current_thresh)
                pct_current = (count_current / viable_count * 100) if viable_count > 0 else 0
                lines.append(f"Bars passing current threshold: {count_current} ({pct_current:.1f}%)")

                if pct_current < 5:
                    lines.append("")
                    lines.append(f"⚠️  WARNING: Current threshold ({current_thresh}) is very strict!")
                    lines.append("   Consider loosening to 30 or 35 to restore setup frequency.")
                elif pct_current > 50:
                    lines.append("")
                    lines.append(f"⚠️  WARNING: Current threshold ({current_thresh}) is very loose!")
                    lines.append("   Compression filter may not be providing meaningful filtering.")

        lines.append("")
        lines.append("=" * 80)

        # Section 11.5: Setup/Compression Decomposition
        lines.append("")
        lines.append("=" * 80)
        lines.append("SETUP/COMPRESSION DECOMPOSITION (Section 11.5)")
        lines.append("=" * 80)
        lines.append("")
        lines.append("On VIABLE bars (regime+viability OK):")
        lines.append("Using compression_recent_12 for decomposition (48h lookback)")
        lines.append("")

        viable_total = self.gate_audit['viability_ok']
        setup_only = self.setup_only_count
        compression_only = self.compression_only_count
        intersection = self.intersection_count
        neither = self.neither_count

        lines.append(f"{'Category':<30} {'Count':>10} {'% of viable':>15}")
        lines.append("-" * 60)
        lines.append(f"{'Setup trigger only':<30} {setup_only:>10,} {(setup_only/viable_total*100) if viable_total>0 else 0:>14.1f}%")
        lines.append(f"{'Compression only':<30} {compression_only:>10,} {(compression_only/viable_total*100) if viable_total>0 else 0:>14.1f}%")
        lines.append(f"{'Both (intersection)':<30} {intersection:>10,} {(intersection/viable_total*100) if viable_total>0 else 0:>14.1f}%")
        lines.append(f"{'Neither':<30} {neither:>10,} {(neither/viable_total*100) if viable_total>0 else 0:>14.1f}%")
        lines.append("-" * 60)
        lines.append(f"{'Total viable bars':<30} {viable_total:>10,} {100.0:>14.1f}%")
        lines.append("")

        # Phase 2.3.1: Invariant check
        lines.append("INVARIANT CHECK:")
        decomp_sum = setup_only + compression_only + intersection + neither
        if decomp_sum == viable_total:
            lines.append(f"✅ Decomposition sum ({decomp_sum}) == viable total ({viable_total})")
        else:
            lines.append(f"⚠️  WARNING: Decomposition sum ({decomp_sum}) != viable total ({viable_total})")

        lines.append("")
        lines.append("INTERPRETATION:")
        lines.append("")

        if intersection < 5 and setup_only > 20:
            lines.append("🚨 COMPRESSION is the bottleneck (setup triggers common, compression rare)")
            lines.append("   → Loosen compression threshold (try 30-35)")
            lines.append("   → Increase compression_lookback_4h (try 12)")
        elif intersection < 5 and compression_only > 20:
            lines.append("🚨 SETUP TRIGGER is the bottleneck (compression common, setup rare)")
            lines.append("   → For Donchian: shorten donch_len (try 6-8)")
            lines.append("   → For ROC-score: lower roc_score_thresh (see percentile calibration below)")
        elif intersection < 5 and setup_only < 20 and compression_only < 20:
            lines.append("🚨 BOTH are bottlenecks (both setup and compression are rare)")
            lines.append("   → Loosen compression threshold AND adjust setup trigger threshold")
        else:
            lines.append("✅ Intersection is meaningful - gates are working together")

        lines.append("")
        lines.append("=" * 80)

        # Phase 2.3.2: UNIVERSE-LEVEL DIAGNOSTIC COUNTS
        lines.append("")
        lines.append("=" * 80)
        lines.append("UNIVERSE-LEVEL DIAGNOSTIC COUNTS (Phase 2.3.2)")
        lines.append("=" * 80)
        lines.append("")

        total_bars = len(self.regime_ok_mask)
        regime_ok_arr = np.array(self.regime_ok_mask)
        viability_ok_arr = np.array(self.viability_ok_mask)
        base_ok_arr = regime_ok_arr & viability_ok_arr
        setup_ok_arr = np.array(self.setup_ok_mask)
        compression_now_arr = np.array(self.compression_now_mask)
        compression_recent_6_arr = np.array(self.compression_recent_6_mask)
        compression_recent_12_arr = np.array(self.compression_recent_12_mask)

        # ALL bars universe
        lines.append(f"ALL 4h bars (N={total_bars}):")
        lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr))} ({100*np.sum(setup_ok_arr)/total_bars:.1f}%)")
        lines.append(f"  compression_now:        {int(np.sum(compression_now_arr))} ({100*np.sum(compression_now_arr)/total_bars:.1f}%)")
        lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr))} ({100*np.sum(compression_recent_6_arr)/total_bars:.1f}%)")
        lines.append(f"  compression_recent_12:  {int(np.sum(compression_recent_12_arr))} ({100*np.sum(compression_recent_12_arr)/total_bars:.1f}%)")
        lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr)/total_bars:.1f}%)")
        lines.append("")

        # REGIME_OK universe
        regime_ok_count = int(np.sum(regime_ok_arr))
        if regime_ok_count > 0:
            lines.append(f"REGIME_OK bars (N={regime_ok_count}):")
            lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr & regime_ok_arr))} ({100*np.sum(setup_ok_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
            lines.append(f"  compression_now:        {int(np.sum(compression_now_arr & regime_ok_arr))} ({100*np.sum(compression_now_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
            lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr & regime_ok_arr))} ({100*np.sum(compression_recent_6_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
            lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr & regime_ok_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
            lines.append("")

        # BASE_OK universe (regime + viability)
        base_ok_count = int(np.sum(base_ok_arr))
        if base_ok_count > 0:
            lines.append(f"BASE_OK bars (regime + viability) (N={base_ok_count}):")
            lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append(f"  compression_now:        {int(np.sum(compression_now_arr & base_ok_arr))} ({100*np.sum(compression_now_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr & base_ok_arr))} ({100*np.sum(compression_recent_6_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append(f"  setup & compression_recent_6: {int(np.sum(setup_ok_arr & compression_recent_6_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_recent_6_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append(f"  setup & compression_recent_12: {int(np.sum(setup_ok_arr & compression_recent_12_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_recent_12_arr & base_ok_arr)/base_ok_count:.1f}%)")
            lines.append("")

        # Annualized projections (assuming 180d window)
        days = 180  # Adjust if different
        scale_factor = 365.0 / days

        lines.append("ANNUALIZED PROJECTIONS (180d → 365d):")
        lines.append(f"  setup_only:                     {int(np.sum(setup_ok_arr & base_ok_arr & ~compression_now_arr) * scale_factor)}/year")
        lines.append(f"  setup & compression_now:        {int(np.sum(setup_ok_arr & base_ok_arr & compression_now_arr) * scale_factor)}/year")
        lines.append(f"  setup & compression_recent_6:   {int(np.sum(setup_ok_arr & base_ok_arr & compression_recent_6_arr) * scale_factor)}/year")
        lines.append(f"  setup & compression_recent_12:  {int(np.sum(setup_ok_arr & base_ok_arr & compression_recent_12_arr) * scale_factor)}/year")

        lines.append("")
        lines.append("=" * 80)

        # Viability Filter Distribution
        if len(self.atr_pct_regime_ok) > 0:
            # Filter out NaNs and None values
            atr_clean = [x for x in self.atr_pct_regime_ok if x is not None and not np.isnan(x)]

            if len(atr_clean) > 0:
                lines.append("")
                lines.append("=" * 80)
                lines.append("VIABILITY FILTER DISTRIBUTION")
                lines.append("=" * 80)
                lines.append("")

                atr_arr = np.array(atr_clean)
                lines.append("ATR% distribution on regime_ok bars:")
                lines.append(f"  Count:  {len(atr_arr)}")
                lines.append(f"  Min:    {np.min(atr_arr):.3f}")
                lines.append(f"  P25:    {np.percentile(atr_arr, 25):.3f}")
                lines.append(f"  Median: {np.percentile(atr_arr, 50):.3f}")
                lines.append(f"  P75:    {np.percentile(atr_arr, 75):.3f}")
                lines.append(f"  Max:    {np.max(atr_arr):.3f}")
                lines.append("")

            # Phase 2.3.2: Use correct round_trip_maker_fee (not average)
            fee_rt = self.config.round_trip_maker_fee
            viability_threshold = self.config.vol_min_mult * fee_rt

            lines.append(f"Viability threshold: {viability_threshold:.4f}")
            lines.append(f"  (vol_min_mult={self.config.vol_min_mult} × round_trip_maker_fee={fee_rt:.4f})")
            lines.append("")

            # Test different vol_min_mult values
            lines.append("Viability pass rate at different vol_min_mult:")
            for mult in [0.75, 1.0, 1.25, 1.5]:
                thresh = mult * fee_rt
                count_pass = np.sum(atr_arr >= thresh)
                pct_pass = (count_pass / len(atr_arr) * 100) if len(atr_arr) > 0 else 0
                marker = " (current)" if mult == self.config.vol_min_mult else ""
                lines.append(f"  vol_min_mult={mult:>4.2f}: {count_pass:>4} bars ({pct_pass:>5.1f}%){marker}")

            lines.append("")
            lines.append("=" * 80)

        # ROC-Score Percentile Calibration (Section 11.3)
        if self.config.setup_mode == "roc_score" and len(self.roc_score_viable) > 0:
            lines.append("")
            lines.append("=" * 80)
            lines.append("ROC-SCORE PERCENTILE CALIBRATION (Section 11.3)")
            lines.append("=" * 80)
            lines.append("")

            # Phase 2.4.1: Filter NaN values before percentile computation
            # (Early bars can have NaN roc_score_4h due to warmup)
            import math
            roc_arr_raw = np.array(self.roc_score_viable)
            roc_arr = np.array([x for x in roc_arr_raw if not (isinstance(x, float) and math.isnan(x))])

            if len(roc_arr) == 0:
                lines.append("⚠️  All ROC-score values are NaN (indicator warmup incomplete)")
                lines.append("   Skipping percentile calibration section")
                lines.append("=" * 80)
            else:
                nan_count = len(roc_arr_raw) - len(roc_arr)
                if nan_count > 0:
                    lines.append(f"Note: Filtered {nan_count} NaN values from warmup period ({len(roc_arr)} valid values remain)")
                    lines.append("")

                lines.append("ROC-score distribution on viable bars:")
                lines.append(f"  Min:    {np.min(roc_arr):.3f}")
                lines.append(f"  P25:    {np.percentile(roc_arr, 25):.3f}")
                lines.append(f"  Median: {np.percentile(roc_arr, 50):.3f}")
                lines.append(f"  P75:    {np.percentile(roc_arr, 75):.3f}")
                lines.append(f"  P90:    {np.percentile(roc_arr, 90):.3f}")
                lines.append(f"  P95:    {np.percentile(roc_arr, 95):.3f}")
                lines.append(f"  Max:    {np.max(roc_arr):.3f}")
                lines.append("")

                lines.append("Recommended thresholds by percentile:")
                lines.append("(Target: 60-120 setups/year for 2-4 trades/month after fill conversion)")
                lines.append("")

                viable_per_year = len(roc_arr) / (self.gate_audit['total_4h_bars'] / 365.0) if self.gate_audit['total_4h_bars'] > 0 else 0

                for pct in [60, 70, 80, 85, 90, 95]:
                    thresh = np.percentile(roc_arr, pct)
                    count_above = np.sum(roc_arr >= thresh)
                    pct_above = (count_above / len(roc_arr) * 100) if len(roc_arr) > 0 else 0

                    # Estimate setups/year (on viable bars only, without compression)
                    setups_per_year = count_above / len(roc_arr) * viable_per_year if len(roc_arr) > 0 else 0

                    marker = " (current)" if abs(thresh - self.config.roc_score_thresh) < 0.01 else ""
                    lines.append(f"  P{pct}: {thresh:>6.3f} → {count_above:>4} bars ({pct_above:>5.1f}%) → ~{setups_per_year:>4.0f} setups/year{marker}")

                lines.append("")
                lines.append(f"Current threshold: {self.config.roc_score_thresh:.3f}")
                count_current = np.sum(roc_arr >= self.config.roc_score_thresh)
                pct_current = (count_current / len(roc_arr) * 100) if len(roc_arr) > 0 else 0
                setups_current = count_current / len(roc_arr) * viable_per_year if len(roc_arr) > 0 else 0
                lines.append(f"Bars passing current threshold: {count_current} ({pct_current:.1f}%) → ~{setups_current:.0f} setups/year")

                lines.append("")
                if setups_current < 60:
                    # Phase 2.3.2: Fix logic - lower percentile = lower threshold, more setups
                    p70_thresh = np.percentile(roc_arr, 70)
                    p60_thresh = np.percentile(roc_arr, 60)
                    lines.append("⚠️  Current threshold is too strict for target frequency!")
                    if p70_thresh < self.config.roc_score_thresh:
                        lines.append(f"   Consider P70 ({p70_thresh:.3f}) or P60 ({p60_thresh:.3f}) for more frequent setups")
                    else:
                        lines.append(f"   Note: P70 ({p70_thresh:.3f}) and P60 ({p60_thresh:.3f}) are higher than current threshold")
                        lines.append(f"   Current threshold may already be permissive - check other gates (regime, viability, compression)")
                elif setups_current > 120:
                    # Phase 2.3.2: Higher percentile = higher threshold, fewer setups
                    p90_thresh = np.percentile(roc_arr, 90)
                    p95_thresh = np.percentile(roc_arr, 95)
                    lines.append("⚠️  Current threshold may be too loose!")
                    lines.append(f"   Consider P90 ({p90_thresh:.3f}) or P95 ({p95_thresh:.3f}) for better selectivity")
                else:
                    lines.append("✅ Current threshold is in reasonable range for target frequency")

                lines.append("")
                lines.append("=" * 80)

        # Phase 2.4: Option 1 Observability (State Occupancy, Setup Lifecycle, Opportunity Loss)
        lines.append("")
        lines.append("=" * 80)
        lines.append("OPTION 1 OBSERVABILITY: Single Active Setup/Position Per Symbol")
        lines.append("=" * 80)
        lines.append("")

        # A) State Occupancy Metrics
        lines.append("A) STATE OCCUPANCY (4h bars with state as of bar close):")
        lines.append("")
        lines.append("NOTE: State is sampled at 4h bar close. Intra-bar transitions (e.g., FLAT→SETUP_ACTIVE→")
        lines.append("RETEST_ORDER_WORKING within same 4h bar) count as the final state only.")
        lines.append("")
        total_4h_bars = self.gate_audit.get('total_4h_bars', 0)

        for state in [StrategyState.FLAT, StrategyState.SETUP_ACTIVE,
                      StrategyState.RETEST_ORDER_WORKING, StrategyState.CHASE_ORDER_WORKING,
                      StrategyState.IN_POSITION]:
            count = self.state_occupancy.get(state, 0)
            pct = (count / total_4h_bars * 100) if total_4h_bars > 0 else 0
            minutes = count * 240  # 4h = 240 minutes
            lines.append(f"  {state.value:<25} {count:>4} bars ({pct:>5.1f}%)  = {minutes:>8,} minutes")

        lines.append("")
        lines.append(f"  Total 4h bars: {total_4h_bars}")

        # Calculate non-FLAT time
        non_flat_bars = sum(self.state_occupancy.get(s, 0) for s in [
            StrategyState.SETUP_ACTIVE, StrategyState.RETEST_ORDER_WORKING,
            StrategyState.CHASE_ORDER_WORKING, StrategyState.IN_POSITION
        ])
        non_flat_pct = (non_flat_bars / total_4h_bars * 100) if total_4h_bars > 0 else 0
        lines.append(f"  Non-FLAT time: {non_flat_bars} bars ({non_flat_pct:.1f}%)")

        lines.append("")
        lines.append("-" * 80)
        lines.append("")

        # B) Setup Lifecycle Metrics
        lines.append("B) SETUP LIFECYCLE METRICS:")
        lines.append("")

        setup_created = self.gate_audit.get('setup_created', 0)
        setup_expired = self.expired_setup_count
        entry_filled = self.gate_audit.get('entry_filled', 0)
        orders_submitted = self.entry_submitted_count

        lines.append(f"  Setups created:              {setup_created:>4}")
        lines.append(f"  Setups expired (no fill):    {setup_expired:>4}  ({(setup_expired/setup_created*100) if setup_created > 0 else 0:.1f}%)")
        lines.append(f"  Entries filled (total):      {entry_filled:>4}")
        lines.append("")
        lines.append(f"  Entry orders submitted:      {orders_submitted:>4}")
        lines.append("")
        lines.append("  Conversion rates:")
        lines.append(f"    Setup → Entry:             {entry_filled}/{setup_created} = {(entry_filled/setup_created*100) if setup_created > 0 else 0:.1f}%")
        lines.append(f"    Order Submitted → Filled:  {entry_filled}/{orders_submitted} = {(entry_filled/orders_submitted*100) if orders_submitted > 0 else 0:.1f}%")
        lines.append("")
        lines.append("  Entry type breakdown:")
        lines.append(f"    Retest fills:              {self.retest_fill_count:>4}")
        lines.append(f"    Chase fills:               {self.chase_fill_count:>4}")
        lines.append("")

        # Setup duration statistics
        if len(self.setup_active_durations) > 0:
            import numpy as np
            durations = np.array(self.setup_active_durations)
            avg_duration_bars = np.mean(durations)
            max_duration_bars = np.max(durations)
            avg_duration_minutes = avg_duration_bars * 240  # 4h = 240 min
            max_duration_minutes = max_duration_bars * 240

            lines.append(f"  Setup active duration (avg):  {avg_duration_bars:>5.1f} bars ({avg_duration_minutes:>7,.0f} minutes)")
            lines.append(f"  Setup active duration (max):  {max_duration_bars:>5.1f} bars ({max_duration_minutes:>7,.0f} minutes)")
        else:
            lines.append(f"  Setup active duration:    N/A (no completed setups)")

        lines.append("")
        lines.append("-" * 80)
        lines.append("")

        # C) Opportunity Loss Tracking
        lines.append("C) OPPORTUNITY LOSS WHILE LOCKED (bars missed due to state != FLAT):")
        lines.append("")

        # ROC-OK opportunity loss
        roc_ok_total = self.gate_audit.get('roc_ok', 0)
        roc_setup_active = self.opp_loss.get('roc_ok_while_setup_active', 0)
        roc_position = self.opp_loss.get('roc_ok_while_position_open', 0)
        roc_locked_total = roc_setup_active + roc_position

        lines.append(f"  ROC-OK bars (total on BASE_OK bars):    {roc_ok_total:>4}")
        lines.append(f"    Missed while SETUP_ACTIVE:            {roc_setup_active:>4}  ({(roc_setup_active/roc_ok_total*100) if roc_ok_total > 0 else 0:.1f}%)")
        lines.append(f"    Missed while IN_POSITION:             {roc_position:>4}  ({(roc_position/roc_ok_total*100) if roc_ok_total > 0 else 0:.1f}%)")
        lines.append(f"  Total ROC-OK bars locked out:           {roc_locked_total:>4}  ({(roc_locked_total/roc_ok_total*100) if roc_ok_total > 0 else 0:.1f}%)")
        lines.append("")

        # Compression opportunity loss (with clear denominator label)
        compression_setup_active = self.opp_loss.get('compression_while_setup_active', 0)
        compression_position = self.opp_loss.get('compression_while_position_open', 0)
        compression_locked_total = compression_setup_active + compression_position

        # Count total compression_recent_12 occurrences on BASE_OK bars
        viable_count = self.gate_audit.get('viability_ok', 0)
        compression_total_base_ok = sum(1 for i, is_viable in enumerate(self.viability_ok_mask)
                               if is_viable and i < len(self.compression_recent_12_mask) and self.compression_recent_12_mask[i])

        # Also compute total compression_recent_12 on ALL bars for context
        compression_total_all = sum(self.compression_recent_12_mask)

        lines.append(f"  Compression_recent_12 bars:")
        lines.append(f"    Total on BASE_OK bars:                {compression_total_base_ok:>4}  (denominator for % below)")
        lines.append(f"    Total on ALL 4h bars (context):       {compression_total_all:>4}")
        lines.append(f"    Missed while SETUP_ACTIVE:            {compression_setup_active:>4}  ({(compression_setup_active/compression_total_base_ok*100) if compression_total_base_ok > 0 else 0:.1f}%)")
        lines.append(f"    Missed while IN_POSITION:             {compression_position:>4}  ({(compression_position/compression_total_base_ok*100) if compression_total_base_ok > 0 else 0:.1f}%)")
        lines.append(f"  Total compression bars locked out:      {compression_locked_total:>4}  ({(compression_locked_total/compression_total_base_ok*100) if compression_total_base_ok > 0 else 0:.1f}%)")
        lines.append("")

        lines.append("INTERPRETATION:")
        lines.append("  High lock-out % indicates significant opportunity cost from single-setup constraint.")
        lines.append("  Option 2 (setup queue) could capture additional opportunities, but adds complexity.")
        lines.append("")
        lines.append("=" * 80)

        # Phase 2.4: Stage C - Trade Breakdown by Entry Compression Context
        if len(self.trades) > 0:
            lines.append("")
            lines.append("=" * 80)
            lines.append("STAGE C: TRADE BREAKDOWN BY ENTRY COMPRESSION CONTEXT")
            lines.append("=" * 80)
            lines.append("")

            # Partition trades by compression context
            compressed_trades = [t for t in self.trades if t.entry_compressed_recent_12]
            normal_trades = [t for t in self.trades if not t.entry_compressed_recent_12]

            lines.append(f"Total trades: {len(self.trades)}")
            lines.append(f"  Compressed entries (compression_recent_12=True): {len(compressed_trades)}")
            lines.append(f"  Normal entries (compression_recent_12=False):    {len(normal_trades)}")
            lines.append("")

            # Helper function to compute stats
            def compute_stats(trades_subset, label):
                if len(trades_subset) == 0:
                    return None

                winners = [t for t in trades_subset if t.net_pnl > 0]
                losers = [t for t in trades_subset if t.net_pnl <= 0]
                win_rate = len(winners) / len(trades_subset) if trades_subset else 0

                avg_net = sum(t.net_pnl for t in trades_subset) / len(trades_subset)
                median_net = sorted([t.net_pnl for t in trades_subset])[len(trades_subset) // 2]

                tp1_hit = sum(1 for t in trades_subset if t.tp1_filled)
                tp2_hit = sum(1 for t in trades_subset if t.tp2_filled)
                tp1_rate = tp1_hit / len(trades_subset) if trades_subset else 0
                tp2_rate = tp2_hit / len(trades_subset) if trades_subset else 0

                avg_hold = sum(t.bars_held for t in trades_subset) / len(trades_subset)

                return {
                    'label': label,
                    'count': len(trades_subset),
                    'win_rate': win_rate,
                    'avg_net': avg_net,
                    'median_net': median_net,
                    'tp1_rate': tp1_rate,
                    'tp2_rate': tp2_rate,
                    'avg_hold_bars': avg_hold,
                    'avg_hold_hours': avg_hold / 60.0  # 1m bars → hours
                }

            # Compute stats for each group
            compressed_stats = compute_stats(compressed_trades, "Compressed")
            normal_stats = compute_stats(normal_trades, "Normal")

            # Print comparison table
            lines.append("Performance Comparison:")
            lines.append("")
            lines.append(f"{'Metric':<25} {'Compressed':>15} {'Normal':>15} {'Difference':>15}")
            lines.append("-" * 80)

            if compressed_stats and normal_stats:
                lines.append(f"{'Count':<25} {compressed_stats['count']:>15} {normal_stats['count']:>15} {compressed_stats['count'] - normal_stats['count']:>15}")
                lines.append(f"{'Win Rate':<25} {compressed_stats['win_rate']:>14.1%} {normal_stats['win_rate']:>14.1%} {(compressed_stats['win_rate'] - normal_stats['win_rate']):>14.1%}")
                lines.append(f"{'Avg Net P&L':<25} ${compressed_stats['avg_net']:>14.2f} ${normal_stats['avg_net']:>14.2f} ${compressed_stats['avg_net'] - normal_stats['avg_net']:>14.2f}")
                lines.append(f"{'Median Net P&L':<25} ${compressed_stats['median_net']:>14.2f} ${normal_stats['median_net']:>14.2f} ${compressed_stats['median_net'] - normal_stats['median_net']:>14.2f}")
                lines.append(f"{'TP1 Hit Rate':<25} {compressed_stats['tp1_rate']:>14.1%} {normal_stats['tp1_rate']:>14.1%} {(compressed_stats['tp1_rate'] - normal_stats['tp1_rate']):>14.1%}")
                lines.append(f"{'TP2 Hit Rate':<25} {compressed_stats['tp2_rate']:>14.1%} {normal_stats['tp2_rate']:>14.1%} {(compressed_stats['tp2_rate'] - normal_stats['tp2_rate']):>14.1%}")
                lines.append(f"{'Avg Hold (hours)':<25} {compressed_stats['avg_hold_hours']:>14.1f} {normal_stats['avg_hold_hours']:>14.1f} {compressed_stats['avg_hold_hours'] - normal_stats['avg_hold_hours']:>14.1f}")
            elif compressed_stats:
                lines.append(f"{'Count':<25} {compressed_stats['count']:>15} {'N/A':>15} {'N/A':>15}")
                lines.append(f"{'Win Rate':<25} {compressed_stats['win_rate']:>14.1%} {'N/A':>15} {'N/A':>15}")
                lines.append(f"{'Avg Net P&L':<25} ${compressed_stats['avg_net']:>14.2f} {'N/A':>15} {'N/A':>15}")
            elif normal_stats:
                lines.append(f"{'Count':<25} {'N/A':>15} {normal_stats['count']:>15} {'N/A':>15}")
                lines.append(f"{'Win Rate':<25} {'N/A':>15} {normal_stats['win_rate']:>14.1%} {'N/A':>15}")
                lines.append(f"{'Avg Net P&L':<25} {'N/A':>15} ${normal_stats['avg_net']:>14.2f} {'N/A':>15}")

            lines.append("")
            lines.append("INTERPRETATION:")
            lines.append("  Use this breakdown to calibrate Stage C runner policy parameters.")
            lines.append("  If compressed entries underperform, consider tighter TP targets or skip compression filter.")
            lines.append("  If compressed entries outperform, increase runner_qty_frac_compressed for better tail capture.")
            lines.append("")
            lines.append("=" * 80)

        lines.append("")

        return "\n".join(lines)
