# Hybrid 4h Swing Momentum Strategy (Maker-First, Fee-Aware, Post-Only)

**Purpose:** An implementation/design spec intended for Claude Code to generate production code for a crypto strategy that can survive high fees by using **post-only maker orders**, **low turnover**, and **fee-multiple profit capture**.

- **Source data:** 1m OHLCV candles (resample as needed)
- **Execution resolution:** 1m (for realistic limit-fill simulation)  
- **Signal timeframe:** 4h  
- **Regime timeframe:** 1D  
- **Universe:** Start with BTC-USD only until net-positive; add others later.

> Key design intent: trade less often, avoid buying spike closes, fill maker-first, and only play when volatility is large enough to plausibly clear round-trip fees.

---

## 0) Fees and why this strategy exists

Coinbase fee tiers can be punitive. This design assumes you are willing to:
- Use **post-only limits** for entries and profit-taking
- Accept **missed fills**
- Use **taker exits** only for protective stops

### Fee parameters (config)
- `MAKER_FEE` (e.g., 0.004 actual; 0.006 worst-case)
- `TAKER_FEE` (e.g., 0.008 actual; 0.012 worst-case)

### Fee charging (per fill, notional-based)
For any fill:
- `fee = abs(qty * fill_price) * fee_rate`

Do **not** use a single blended fee for entry+exit. Fees are per execution and may differ maker vs taker.

---

## 1) Timeframes and data handling

### 1.1 Bar construction (from 1m)
Resample 1m to:
- 4h OHLCV for signals/volatility
- 1D OHLCV for regime
- Use **1m bars** for order simulation (fills, stops)

OHLCV aggregation:
- open = first
- high = max
- low = min
- close = last
- volume = sum

### 1.2 Alignment rules
- 4h indicators update only when a 4h candle closes.
- 1D indicators update only when a daily candle closes.
- Entry/exit order logic runs on each 1m candle using the latest available 4h/1D values.

---

## 2) Indicators

### 2.1 ATR% on 4h (volatility scale)
Compute ATR on 4h bars:
- TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
- ATR via Wilder’s RMA (preferred)

Then:
- `atr_pct_4h = ATR_4h / close_4h`

Config:
- `atr_len_4h` (default 14)

### 2.2 Daily regime filter (1D)
Compute:
- `EMA_1d_200`
- `EMA_1d_50`

Regime allows longs when:
- `close_1d > EMA_1d_200`
- optional: `EMA_1d_50 slope > 0` (e.g., EMA50[t] > EMA50[t-5])

Config:
- `ema200_len_1d = 200`
- `ema50_len_1d = 50`
- `ema_slope_lookback_days = 5` (optional)

### 2.3 4h setup triggers (choose one; implement both as options)

#### A) Donchian breakout
- `donch_high_prev = highest_high_4h(donch_len, exclude_current=True)`
Trigger when:
- `close_4h > donch_high_prev`

Config:
- `donch_len` (default 20)

#### B) Vol-adjusted ROC score
Compute ROC on 4h:
- `roc_raw = (close_4h - close_4h[L]) / close_4h[L]`
- `roc = EMA(roc_raw, roc_ema_len)`
- `roc_score = roc / atr_pct_4h`

Trigger when:
- `roc_score >= roc_score_thresh`

Config:
- `roc_len_4h` (default 6)
- `roc_ema_len` (default 3)
- `roc_score_thresh` (default 1.2)

> Vol-adjusted ROC is preferred over fixed ROC% because it scales with volatility.

---

## 3) Fee-aware viability filters (do not skip)

### 3.1 Round-trip maker fee estimate
- `fee_rt_est = 2 * MAKER_FEE`  (maker entry + maker TP exits)

### 3.2 Minimum volatility to bother trading
Only allow new setups when:
- `atr_pct_4h >= vol_min_mult * fee_rt_est`

Config:
- `vol_min_mult` (default 2.0)

Interpretation: if typical 4h volatility is too small relative to fees, skip trades entirely.

---

## 4) Execution model: post-only limits with conservative fills

This section defines the backtest fill logic. The backtest MUST be conservative; missed fills are expected.

### 4.1 Post-only rule
A post-only order must **not** cross the book at placement time.

In backtest with OHLC only, approximate:
- A **buy** post-only limit at price `p_buy` is placeable only if `p_buy < current_close_1m` (or `< current_best_ask` if modeled).
- A **sell** post-only limit at `p_sell` is placeable only if `p_sell > current_close_1m`.

If not placeable, do not “force” it; either adjust the price further away or skip.

### 4.2 Maker limit fill rule (conservative)
If a buy limit order rests at `p_buy`, it fills **only if**:
- a later 1m candle has `low_1m <= p_buy`
Fill price = `p_buy` and fee uses `MAKER_FEE`.

If a sell limit rests at `p_sell`, it fills **only if**:
- a later 1m candle has `high_1m >= p_sell`
Fill price = `p_sell` and fee uses `MAKER_FEE`.

### 4.3 Protective stop rule (assume taker)
Stops are “must exit” → assume taker:
If in long and `low_1m <= stop_price`:
- fill at `stop_price`, fee uses `TAKER_FEE`

> This is intentionally pessimistic and realistic.

---

## 5) Strategy state machine (LONG only initially)

### 5.1 States
1) `FLAT` (no position, no pending)
2) `PENDING_SETUP` (a 4h trigger occurred; waiting to enter)
3) `ENTRY_ORDER_WORKING` (post-only buy order resting)
4) `IN_POSITION` (position open; managing stops/TPs)

Only one position per symbol. Optional cooldown after exit.

### 5.2 Setup TTL
A setup expires after `setup_ttl_bars_4h` 4h candles (default 3 = 12h).
If not entered by then, discard.

---

## 6) Entry design (maker-first)

Do NOT buy the signal candle close. Use one of the following entry modes.

### 6.1 Entry Mode 1: Pullback + reclaim (recommended)

**Goal:** avoid buying exhaustion; improve fill quality.

After a 4h trigger, define:
- `EMA20_4h`
- Pullback zone around EMA:
  - `zone_upper = EMA20_4h * (1 + zone_width * atr_pct_4h)`
  - `zone_lower = EMA20_4h * (1 - zone_width * atr_pct_4h)`

Pullback condition:
- at least one 1m candle has `low_1m <= zone_upper` and preferably `low_1m <= EMA20_4h`

Reclaim condition (simple):
- a 1m candle closes above `EMA20_4h` after a prior candle dipped below/near it.

When reclaim occurs, place a post-only buy limit:
- `p_buy = min(close_1m * (1 - entry_offset), EMA20_4h * (1 - entry_offset2))`

Order TTL:
- cancel if not filled after `entry_ttl_bars_1m` (e.g., 180 minutes), then either try again on next reclaim or expire with setup TTL.

Config:
- `ema_pullback_4h = 20`
- `zone_width` (default 0.5)
- `entry_offset = 0.001` (0.10%)
- `entry_offset2 = 0.001`
- `entry_ttl_bars_1m` (default 180)
- `setup_ttl_bars_4h` (default 3)

### 6.2 Entry Mode 2: Breakout-retest (maker-friendly continuation)

After 4h trigger, wait for price to retest the breakout level:

- Let `level = setup_high` (or donchian high)
- Retest when:
  - `low_1m <= level * (1 + retest_band)` and later
  - `close_1m > level`

Place a post-only buy limit slightly below level:
- `p_buy = level * (1 - entry_offset)`

Config:
- `retest_band = 0.002` (0.20% default)
- `entry_offset = 0.001` (0.10% default)

---

## 7) Position sizing

Start simple:
- `notional_usd = 100` (configurable)
- `qty = notional_usd / entry_price`

---

## 8) Risk: ATR% stop

On entry:
- `stop_price = entry_price * (1 - stop_mult * atr_pct_4h)`

Config:
- `stop_mult` (default 2.0)

Stop is evaluated on each 1m candle; if hit, exit remaining qty at stop using `TAKER_FEE`.

---

## 9) Fee-multiple profit capture (core improvement)

### 9.1 Fee-based profit thresholds
Use:
- `fee_rt_est = 2 * MAKER_FEE`

Define target returns:
- `T1 = max(tp1_fee_mult * fee_rt_est, tp1_atr_mult * atr_pct_4h)`
- `T2 = max(tp2_fee_mult * fee_rt_est, tp2_atr_mult * atr_pct_4h)`

Prices:
- `tp1_price = entry_price * (1 + T1)`
- `tp2_price = entry_price * (1 + T2)`

Defaults:
- `tp1_fee_mult = 2.0`
- `tp2_fee_mult = 4.0`
- `tp1_atr_mult = 1.0`
- `tp2_atr_mult = 2.0`

### 9.2 Scale-out sizes (maker)
- Sell `tp1_qty_frac` at TP1 (default 0.40)
- Sell `tp2_qty_frac` at TP2 (default 0.40)
- Runner remainder (default 0.20)

All TP orders must be post-only sell limits; fills use maker fee.

---

## 10) Runner management: trailing stop after TP1

After TP1 fills, activate trailing for remaining qty:

Track on each completed 4h candle:
- `highest_close_4h_since_entry = max(highest_close_4h_since_entry, close_4h)`

Trail:
- `trail_price = highest_close_4h_since_entry * (1 - trail_mult * atr_pct_4h)`

If 1m candle low breaches trail → exit remainder at trail using `TAKER_FEE`.

Config:
- `trail_mult = 2.0`

Optional time stop:
- exit runner after `runner_max_bars_4h` (e.g., 10) if desired.

---

## 11) Exit order of operations (deterministic)

On each 1m candle while in position:

1) If `low_1m <= stop_price`: exit all remaining qty at stop (taker)
2) Else if TP1 working and `high_1m >= tp1_price`: fill TP1 (maker)
3) Else if TP2 working and `high_1m >= tp2_price`: fill TP2 (maker)
4) Else update runner trail at 4h boundaries and check trail breach

---

## 12) Logging & required diagnostics

Per trade:
- entry time/price, qty, entry fee, entry mode, setup type
- whether entry filled or missed; time-to-fill
- stop price and whether stop triggered
- TP1/TP2 prices, fill times, fees, quantities
- runner exit price/time and fees
- gross pnl, fees total, net pnl
- MFE/MAE (% and ATR% units)
- maximum favorable move in multiples of `fee_rt_est`

Aggregate:
- trades/month
- gross vs net
- average win %, average loss %
- profit factor gross and net
- maker fill rate (entries and TPs)
- missed fill rate
- % trades reaching:
  - +2× fee_rt_est
  - +4× fee_rt_est
  - +6× fee_rt_est
- capture ratio: realized profit / MFE

---

## 13) Default parameters (BTC starting point)

Regime:
- `ema200_len_1d = 200`
- `ema50_len_1d = 50`
- `ema_slope_lookback_days = 5` (optional)

Setup (pick one):
- Donchian: `donch_len = 20`
- ROC-score: `roc_len_4h = 6`, `roc_ema_len = 3`, `roc_score_thresh = 1.2`

ATR:
- `atr_len_4h = 14`

Entry:
- `entry_mode = pullback_reclaim`
- `ema_pullback_4h = 20`
- `zone_width = 0.5`
- `entry_offset = 0.001`
- `entry_offset2 = 0.001`
- `entry_ttl_bars_1m = 180`
- `setup_ttl_bars_4h = 3`

Stops/Targets:
- `stop_mult = 2.0`
- `tp1_fee_mult = 2.0`
- `tp2_fee_mult = 4.0`
- `tp1_atr_mult = 1.0`
- `tp2_atr_mult = 2.0`
- `tp1_qty_frac = 0.40`
- `tp2_qty_frac = 0.40`
- `trail_mult = 2.0`

Fee-aware viability:
- `vol_min_mult = 2.0`

Fees (run two scenarios):
- Worst-case: `MAKER_FEE=0.006`, `TAKER_FEE=0.012`
- Closer-to-actual: `MAKER_FEE=0.004`, `TAKER_FEE=0.008`

---

## 14) Pseudocode (high-level)

```python
for each 1m bar:
    update_4h_and_1d_bars_if_boundary()
    if new_4h_close: update 4h indicators
    if new_1d_close: update 1d indicators

    if state == FLAT:
        if regime_allows_long and setup_triggered and atr_pct_4h >= vol_min_mult*(2*MAKER_FEE):
            pending_setup = create_setup(ttl=setup_ttl_bars_4h)
            state = PENDING_SETUP

    if state in {PENDING_SETUP, ENTRY_ORDER_WORKING}:
        if pending_setup.expired: reset to FLAT
        else:
            if entry_conditions_met (pullback/reclaim OR retest):
                if post_only_placeable(p_buy):
                    place_limit_buy(p_buy)
                    state = ENTRY_ORDER_WORKING
            if limit_buy_working and low_1m <= p_buy:
                fill_entry(p_buy, maker_fee)
                init_stops_and_targets()
                place_tp_orders_post_only()
                state = IN_POSITION

    if state == IN_POSITION:
        if low_1m <= stop_price:
            exit_remaining(stop_price, taker_fee)
            state = FLAT
            continue

        if not tp1_filled and high_1m >= tp1_price and post_only_tp_was_resting:
            fill_tp1(tp1_price, maker_fee)

        if not tp2_filled and high_1m >= tp2_price and post_only_tp_was_resting:
            fill_tp2(tp2_price, maker_fee)

        if tp1_filled:
            update_trail_on_4h_closes()
            if low_1m <= trail_price:
                exit_remaining(trail_price, taker_fee)
                state = FLAT
```

Implementation note: “post_only_tp_was_resting” means the TP was placed when price was below the limit; otherwise do not assume maker.

---

## 15) Pass/Fail criteria

This strategy is viable only if:
- winners are **multi-%** often enough to clear fee multiples
- a meaningful fraction of trades hit TP2
- maker fill rate is high (entries + TP orders)
- net PnL remains positive under closer-to-actual fee schedule

---

**End of spec.**
