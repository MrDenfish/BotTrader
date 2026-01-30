# ROC Peak-Drawdown Momentum Strategy (1m) with Dual ATR% Envelopes

**Purpose:** This document is an implementation/design spec intended for *Claude Code* (or another coding assistant) to generate production code for a crypto momentum strategy.

- **Execution timeframe:** 1-minute candles (1m)
- **Entry (given):** ROC threshold + volume filter
- **Primary exit:** ROC falls **X% from its post-entry peak** (“momentum drop”)
- **Risk management:** two ATR% envelopes  
  - **ATR%_slow:** “catastrophic / survival” stop sized to the intended holding horizon  
  - **ATR%_fast:** “profit-protection” trailing stop that activates only after the trade is “armed”

This strategy supports **two variants**:
- **Variant A (20m-style):** short-horizon impulse capture (no hard time exit)
- **Variant B (24h-style):** longer-horizon trend participation (no hard time exit)

> Key design intent: ROC peak-drawdown is the *normal* exit. ATR%_fast protects profits once the move proves itself. ATR%_slow is a tail-risk backstop and should be rare.

---

## 1) Definitions and Notation

### 1.1 Candle Data
Each 1m bar has: `open, high, low, close, volume, timestamp`.

### 1.2 ROC (Rate of Change)
Compute on 1m closes:

- `roc_raw(t) = (close(t) - close(t-L)) / close(t-L)`

Where:
- `L` = ROC lookback length in minutes (integer).

**Smoothing (recommended):**
- `roc = EMA(roc_raw, roc_ema_len)`
- Set `roc_ema_len = 1` to disable smoothing.

### 1.3 ROC Peak and Peak-Drawdown
During an open position, track the maximum ROC observed since entry:

- `roc_peak = max(roc_peak, roc)`

Define the drawdown-from-peak (for LONG):

- `dd = (roc_peak - roc) / max(abs(roc_peak), eps)`

Exit due to momentum drop when:
- `dd >= X_dd`  (e.g., 0.40 means ROC fell 40% from peak)

**Arming requirement:** do not use peak-drawdown until ROC peak is “meaningful”:
- `armed_roc_dd = (roc_peak >= roc_arm_threshold)`

This prevents tiny early peaks from causing immediate exits.

### 1.4 ATR and ATR%
Use ATR expressed as a *fraction of price*:

- `atr_pct = atr / close`

Compute two ATR%s:

- `atr_fast_pct` from **1m** ATR
- `atr_slow_pct` from **higher timeframe** ATR (15m or 1h recommended)

> Higher-timeframe ATR for the slow stop is strongly recommended even though execution is 1m, because long-horizon volatility is not well represented by 1m microstructure noise.

---

## 2) Indicators Required

### 2.1 EMA
Standard EMA over a series.

### 2.2 ATR on Multiple Timeframes
ATR requires True Range (TR). For bars at timeframe TF:

- `TR = max(high - low, abs(high - prev_close), abs(low - prev_close))`
- `ATR = Wilder_RMA(TR, atr_len)` (Wilder’s smoothing) or SMA; be consistent.

**Timeframes:**
- `ATR_fast`: computed on 1m bars.
- `ATR_slow`: computed on aggregated TF bars (15m or 1h), then forward-filled onto 1m timeline:
  - Example: every 15 minutes, finalize a 15m bar → update `atr_slow` → use that value for subsequent 1m bars until next 15m bar closes.

---

## 3) Strategy Parameters

All parameters must be configurable.

### 3.1 Common Parameters
- `roc_len` (L): integer
- `roc_ema_len`: integer (>=1)
- `eps`: float (e.g., `1e-12`)
- `min_bars_after_entry_for_exit`: int (e.g., 3) to avoid immediate churn
- `side`: LONG-only initially (SHORT optional mirror)

### 3.2 Entry Parameters (given: ROC threshold + volume filter)
- `roc_entry_threshold`: float (fraction; e.g., 0.003 = 0.30%)
- `vol_sma_len`: int (e.g., 20–60)
- `vol_ratio_min`: float (e.g., 1.5–3.0)
- `vol_ratio = volume / SMA(volume, vol_sma_len)`
- Entry condition (LONG example):
  - `roc >= roc_entry_threshold` AND `vol_ratio >= vol_ratio_min`

> IMPORTANT: ROC thresholds must be retuned when `roc_len` changes.

### 3.3 ATR%_fast (Profit-protection)
- `atr_fast_len`: int (e.g., 14 or 21)
- `M_fast`: float multiplier (default starting point: **1.8**)
- `fast_trail_activation_k`: float (e.g., 1.0)
- Fast trail arming (LONG):
  - Arm if either:
    - **Profit arm:** `close >= entry_price * (1 + fast_trail_activation_k * atr_fast_pct)`
    - OR **Momentum arm:** `armed_roc_dd` becomes true

### 3.4 ATR%_slow (Catastrophic / survival)
- `tf_slow`: `"15m"` or `"1h"`
- `atr_slow_len`: int (e.g., 14)
- `M_slow`: float multiplier (variant-specific)

### 3.5 ROC Peak-Drawdown Exit
- `X_dd`: drawdown threshold fraction (variant-specific)
- `roc_arm_threshold`: minimum ROC peak before enabling ROC-dd exits (variant-specific)
- Optional (recommended) `peak_persistence_bars`: int (2–5)  
  Mitigates one-bar ROC spikes setting an unrealistic peak.
- Optional `roc_peak_tolerance`: float (e.g., 0.99) for persistence checks.

---

## 4) Stops and Exits (ATR% Formulas)

### 4.1 LONG Stops

**Slow stop (catastrophic):**
- `stop_slow = entry_price * (1 - M_slow * atr_slow_pct)`

**Fast trailing stop (profit protection), only when armed:**
- Track `high_since_entry = max(high_since_entry, high)`
- `stop_fast = max(stop_fast, high_since_entry * (1 - M_fast * atr_fast_pct))`

**Active stop:**
- If fast not armed: `active_stop = stop_slow`
- If fast armed: `active_stop = max(stop_slow, stop_fast)` (tightest stop on LONG side)

### 4.2 Exit Conditions (recommended priority)
Evaluate each new 1m bar in deterministic order:

1. **ATR%_slow stop hit:** if `low <= stop_slow` → exit_reason=`"ATR_SLOW_STOP"`
2. **ATR%_fast stop hit (if armed):** if `low <= stop_fast` → exit_reason=`"ATR_FAST_STOP"`
3. **ROC peak-drawdown exit (if armed and bars_in_trade >= min_bars_after_entry_for_exit):**
   - `dd = (roc_peak - roc) / max(abs(roc_peak), eps)`
   - if `dd >= X_dd` → exit_reason=`"ROC_PEAK_DD"`

> Alternative ordering: some prefer ROC exit before ATR_fast stop. If so, document and keep deterministic.

### 4.3 Fill Model
Define consistently for backtest and live:
- Stop fill at stop price (idealized) or at next bar open (conservative).
Pick one and keep consistent.

---

## 5) ROC Peak Tracking Options

### Option A (simpler, recommended): Smooth ROC
- `roc = EMA(roc_raw, roc_ema_len)`
- `roc_peak = max(roc_peak, roc)`

Maintain `roc_arm_threshold` to prevent early micro-peaks.

### Option B (enhanced): Peak persistence
Avoid single-bar spikes setting the peak.

Example logic (LONG):
- Maintain `candidate_peak` and `candidate_count`
- If `roc > candidate_peak`: set `candidate_peak=roc`, `candidate_count=1`
- Else if `roc >= candidate_peak * roc_peak_tolerance`: `candidate_count += 1`
- Else: `candidate_count = 0`
- If `candidate_count >= peak_persistence_bars`: `roc_peak = candidate_peak`

Implement Option A by default; allow Option B via a config flag.

---

## 6) Variant Defaults (Starting Points)

These are **starting points** (not guaranteed optimal). The key is relative design: short vs long horizon.

### Variant A: 20m-style impulse capture (no time exit)
- `roc_len`: **9**
- `roc_ema_len`: **5**
- `roc_entry_threshold`: start **0.002–0.004** (0.20%–0.40%) and tune per asset
- `vol_sma_len`: 20
- `vol_ratio_min`: 2.0
- `atr_fast_len`: 14
- `M_fast`: **1.8**
- `tf_slow`: **15m**
- `atr_slow_len`: 14
- `M_slow`: **3.0**
- `roc_arm_threshold`: **0.0015** (0.15%)
- `X_dd`: **0.40**
- `fast_trail_activation_k`: 1.0
- `min_bars_after_entry_for_exit`: 3

### Variant B: 24h-style trend participation (no time exit)
Two approaches:

**Approach 1 (single ROC on 1m):**
- `roc_len`: **60**
- `roc_ema_len`: **9**
- `roc_entry_threshold`: start **0.006–0.012** (0.60%–1.20%) and tune
- `vol_sma_len`: 30–60
- `vol_ratio_min`: 1.5–2.5
- `atr_fast_len`: 21
- `M_fast`: **2.2**
- `tf_slow`: **1h**
- `atr_slow_len`: 14
- `M_slow`: **3.5**
- `roc_arm_threshold`: **0.0030** (0.30%)
- `X_dd`: **0.60**
- `fast_trail_activation_k`: 1.0
- `min_bars_after_entry_for_exit`: 3

**Approach 2 (recommended, multi-timeframe ROC):**
- Execute on 1m, but compute momentum ROC on 5m/15m for better signal stability:
  - Example: ROC on 5m with L=12–24 (1–2 hours), plus smoothing.
- Keep ATR% design identical; only entry and ROC peak-drawdown exit use the higher-TF ROC.

---

## 7) State Machine (Per Position)

Maintain the following state after entry:

- `in_position: bool`
- `entry_price: float`
- `bars_in_trade: int`
- `high_since_entry: float` (LONG)
- `roc_peak: float`
- `armed_roc_dd: bool`
- `armed_fast_trail: bool`
- `stop_slow: float`
- `stop_fast: float` (initialize to `-inf` for LONG)
- Optional persistence:
  - `candidate_peak, candidate_count`

---

## 8) Pseudocode (LONG)

```python
def on_new_bar(bar):
    update_indicators(bar)  # roc, atr_fast_pct, atr_slow_pct, vol_ratio, etc.

    if not in_position:
        if entry_signal_long(roc, vol_ratio):
            enter_long(bar)
        return

    # Update state
    bars_in_trade += 1
    high_since_entry = max(high_since_entry, bar.high)

    # Update ROC peak (Option A)
    roc_peak = max(roc_peak, roc)

    # Arm ROC-dd exit once peak is meaningful
    if roc_peak >= roc_arm_threshold:
        armed_roc_dd = True

    # Arm fast trail once trade shows strength
    profit_arm = bar.close >= entry_price * (1 + fast_trail_activation_k * atr_fast_pct)
    if profit_arm or armed_roc_dd:
        armed_fast_trail = True

    # Compute stops (ATR% form)
    stop_slow = entry_price * (1 - M_slow * atr_slow_pct)

    if armed_fast_trail:
        candidate_fast = high_since_entry * (1 - M_fast * atr_fast_pct)
        stop_fast = max(stop_fast, candidate_fast)

    # Exits (priority)
    if bar.low <= stop_slow:
        exit_trade(reason="ATR_SLOW_STOP", bar=bar)
        return

    if armed_fast_trail and bar.low <= stop_fast:
        exit_trade(reason="ATR_FAST_STOP", bar=bar)
        return

    if armed_roc_dd and bars_in_trade >= min_bars_after_entry_for_exit:
        dd = (roc_peak - roc) / max(abs(roc_peak), eps)
        if dd >= X_dd:
            exit_trade(reason="ROC_PEAK_DD", bar=bar)
            return


def enter_long(bar):
    in_position = True
    entry_price = bar.close  # define fill model
    bars_in_trade = 0
    high_since_entry = bar.high
    roc_peak = roc
    armed_roc_dd = False
    armed_fast_trail = False
    stop_fast = float("-inf")
```

---

## 9) Logging and Metrics (Required)

For each trade, record at minimum:
- `entry_time, entry_price, exit_time, exit_price`
- `exit_reason` in {`ROC_PEAK_DD`, `ATR_FAST_STOP`, `ATR_SLOW_STOP`}
- `roc_len, roc_ema_len, X_dd, roc_arm_threshold`
- `atr_fast_len, M_fast, atr_slow_len, M_slow, tf_slow`
- `roc_peak, roc_at_exit, dd_at_exit`
- MAE/MFE in **ATR% units** (recommended):
  - `mae_pct = (min_low_since_entry - entry_price) / entry_price` (LONG negative)
  - Express relative to atr_slow_pct_at_entry: `mae_in_atrslow = abs(mae_pct) / atr_slow_pct_at_entry`
  - Similarly for MFE.

Aggregate diagnostics:
- Exit reason frequency:
  - ATR_SLOW should be rare (tail risk)
  - ATR_FAST mostly after meaningful profit
  - ROC_PEAK_DD should dominate “normal” exits

---

## 10) Implementation Deliverables (What Claude should generate)

1. **Indicators module**
   - ROC (with optional EMA smoothing)
   - ATR on 1m
   - Bar aggregation for 15m/1h and ATR on aggregated bars
   - Forward-fill alignment of higher-TF ATR onto 1m steps
   - SMA(volume) and volume ratio

2. **Strategy/state module**
   - Parameter dataclass/config
   - Position state machine (LONG first)
   - Deterministic exit ordering
   - Logging hooks

3. **Backtest harness integration**
   - Works on historical OHLCV arrays
   - Produces trade list + summary metrics

---

## 11) Notes on ROC Lookback `L` (Guidance for Tuning)

- Smaller `L` (5–12): faster, noisier, more entries; needs smoothing + arming
- Larger `L` (30–120): slower, fewer entries; better for longer holds

Starting points:
- Variant A: `L=9` (good 1m impulse scale)
- Variant B: `L=60` if staying on 1m ROC, OR compute ROC on 5m/15m for better stability

Whenever `L` changes, retune:
- `roc_entry_threshold`
- `roc_arm_threshold`
- `X_dd`

---

## 12) Optional Enhancements (Nice-to-have)

- Spread/liquidity filter (avoid thin books)
- ATR% regime filter (avoid top percentile volatility spikes at entry)
- Cooldown after exits (reduce churn in chop)
- SHORT support (mirror logic)

---

**End of spec.**
