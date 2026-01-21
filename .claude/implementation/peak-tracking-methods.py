# Peak Tracking Helper Methods for position_monitor.py
# Insert these methods after _fetch_current_fees() method (around line 156)

    def _init_peak_tracking_for_position(self, symbol: str, entry_price: Decimal, trigger_type: str = None):
        """
        Initialize peak tracking state for a new position.

        Args:
            symbol: Trading pair symbol
            entry_price: Entry price for the position
            trigger_type: Trigger that opened this position (e.g., 'ROC_MOMO')
        """
        if symbol not in self.peak_tracking_state:
            self.peak_tracking_state[symbol] = {
                'peak_price': entry_price,
                'price_history': [],  # Rolling window for 5-min smoothing
                'entry_time': datetime.now(),
                'trigger_type': trigger_type,
                'breakeven_activated': False
            }
            self.logger.info(
                f"[PEAK_TRACK] {symbol}: Initialized peak tracking | "
                f"entry=${entry_price:.6f}, trigger={trigger_type}"
            )

    def _update_peak_tracking(self, symbol: str, current_price: Decimal) -> None:
        """
        Update peak tracking state with current price.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price
        """
        if symbol not in self.peak_tracking_state:
            return

        state = self.peak_tracking_state[symbol]

        # Add current price to rolling window
        state['price_history'].append(float(current_price))

        # Keep only last N prices for smoothing window
        max_history = self.peak_smoothing_mins
        if len(state['price_history']) > max_history:
            state['price_history'] = state['price_history'][-max_history:]

        # Calculate smoothed price (5-min SMA)
        smoothed_price = Decimal(str(sum(state['price_history']) / len(state['price_history'])))

        # Update peak if smoothed price is higher
        if smoothed_price > state['peak_price']:
            old_peak = state['peak_price']
            state['peak_price'] = smoothed_price
            self.logger.debug(
                f"[PEAK_TRACK] {symbol}: New peak | "
                f"${old_peak:.6f} → ${smoothed_price:.6f} "
                f"(raw: ${current_price:.6f}, {len(state['price_history'])}-price SMA)"
            )

    def _check_peak_tracking_exit(
        self,
        symbol: str,
        current_price: Decimal,
        entry_price: Decimal,
        pnl_pct: Decimal
    ) -> Tuple[bool, str]:
        """
        Check if position should exit based on peak tracking strategy.

        ROC Peak Tracking Exit Logic:
        1. Must hit +6% profit to activate
        2. Once activated: Move hard stop to break-even (0% including fees)
        3. Track peak price (5-min SMA)
        4. Exit if price drops -5% from peak
        5. Exit after 24 hours regardless

        Args:
            symbol: Trading pair symbol
            current_price: Current market price
            entry_price: Entry price of position
            pnl_pct: Current P&L percentage (fee-aware)

        Returns:
            (should_exit, exit_reason) tuple
        """
        # Only applies to ROC trades with peak tracking enabled
        if not self.peak_tracking_enabled:
            return False, None

        if symbol not in self.peak_tracking_state:
            return False, None

        state = self.peak_tracking_state[symbol]
        trigger_type = state.get('trigger_type', '').upper()

        # Only apply to ROC triggers
        if trigger_type not in [t.upper() for t in self.peak_tracking_triggers]:
            return False, None

        # Update peak tracking
        self._update_peak_tracking(symbol, current_price)

        # 1. Check 24-hour time limit
        time_held_mins = (datetime.now() - state['entry_time']).total_seconds() / 60
        if time_held_mins >= self.peak_max_hold_mins:
            self.logger.info(
                f"[PEAK_TRACK] {symbol}: 24-hour time limit reached | "
                f"held={time_held_mins:.0f}min, entry=${entry_price:.6f}, current=${current_price:.6f}, "
                f"pnl={pnl_pct:.2%}"
            )
            return True, f"peak_track_24hr_limit_{pnl_pct:.2%}"

        # 2. Check if minimum profit threshold reached (+6%)
        if pnl_pct < self.peak_min_profit_pct:
            # Not yet activated - use standard stops
            return False, None

        # 3. Activate break-even protection (one-time)
        if not state['breakeven_activated']:
            state['breakeven_activated'] = True
            self.logger.info(
                f"[PEAK_TRACK] {symbol}: ✅ +6% profit reached, activating peak tracking | "
                f"entry=${entry_price:.6f}, current=${current_price:.6f}, "
                f"peak=${state['peak_price']:.6f}, pnl={pnl_pct:.2%}"
            )

        # 4. Calculate smoothed price for exit check
        if len(state['price_history']) == 0:
            return False, None

        smoothed_price = Decimal(str(sum(state['price_history']) / len(state['price_history'])))
        peak_price = state['peak_price']

        # 5. Check peak drawdown exit (-5% from peak)
        drawdown_from_peak = (smoothed_price - peak_price) / peak_price

        if drawdown_from_peak <= -self.peak_drawdown_pct:
            self.logger.info(
                f"[PEAK_TRACK] {symbol}: 🎯 Peak drawdown exit triggered | "
                f"peak=${peak_price:.6f}, smoothed=${smoothed_price:.6f}, "
                f"drawdown={drawdown_from_peak:.2%}, pnl={pnl_pct:.2%}"
            )
            return True, f"peak_track_-5pct_from_{peak_price:.6f}"

        # 6. Check break-even protection (if price drops back to entry)
        # Fee-aware break-even: need to cover 1.2% round-trip fees
        total_fees = self.fallback_maker_fee + self.fallback_taker_fee  # 1.2%
        breakeven_threshold = -total_fees  # -1.2%

        if pnl_pct <= breakeven_threshold:
            self.logger.info(
                f"[PEAK_TRACK] {symbol}: 🛡️ Break-even protection triggered | "
                f"entry=${entry_price:.6f}, current=${current_price:.6f}, "
                f"pnl={pnl_pct:.2%}, peak_was=${peak_price:.6f}"
            )
            return True, f"peak_track_breakeven_{pnl_pct:.2%}"

        # Still holding - log status periodically
        self.logger.debug(
            f"[PEAK_TRACK] {symbol}: Holding | "
            f"peak=${peak_price:.6f}, current=${smoothed_price:.6f}, "
            f"drawdown={drawdown_from_peak:.2%}, pnl={pnl_pct:.2%}, "
            f"held={time_held_mins:.0f}min/{self.peak_max_hold_mins}min"
        )

        return False, None

    def _cleanup_peak_tracking(self, symbol: str):
        """
        Clean up peak tracking state when position is closed.

        Args:
            symbol: Trading pair symbol
        """
        if symbol in self.peak_tracking_state:
            state = self.peak_tracking_state[symbol]
            entry_time = state.get('entry_time')
            peak_price = state.get('peak_price')
            time_held = (datetime.now() - entry_time).total_seconds() / 60 if entry_time else 0

            self.logger.info(
                f"[PEAK_TRACK] {symbol}: Cleaning up state | "
                f"peak_was=${peak_price:.6f}, held={time_held:.0f}min"
            )
            del self.peak_tracking_state[symbol]
