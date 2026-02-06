# Phase 2.2 Patch Spec — Restore Fill Rate + Reach 2–4 Trades/Month (Maker-First)

**Audience:** Claude Code  
**Target:** Existing Phase 2.1 codebase (`config_4h_hybrid.py`, `strategy_4h_hybrid.py`, `engine_4h_hybrid.py`, optimizer scripts)

## Objective
Phase 2.1 produced high-quality trades but **too few trades** and **poor signal→entry conversion** over 180 days.

**Phase 2.2 goal:** Keep the same edge hypothesis (compression breakouts + fee-multiple exits + maker-first), while:
- Increasing trade frequency to **2–4 trades/month**
- Increasing signal→entry conversion (fill rate) without becoming taker-heavy
- Preserving fee survivability (avoid churn)

## Diagnosis Summary (from 180-day run)
- Signals/day dropped significantly vs the 60-day slice
- Only 1 of 3 setups filled; 2 expired → entry logic too strict/late
- The one filled trade had excellent MFE (+6.14%) and low MAE (-0.85%) → quality is good **when filled**

So we need:
1) **More setups** (looser compression gating and/or shorter donch lookback)
2) **Better fills** (resting retest bid immediately, earlier chase escalation, volatility-aware offsets)
3) **Better observability** (distance-to-fill diagnostics)

---

# A) Indicator / Gate Changes (More Setups)

## A1) Compression Gate: “Recent Compression” instead of “Only compressed at trigger bar”
Current behavior likely requires `bb_width_pct <= threshold` at the breakout bar, which can miss many valid “compression then release” sequences.

### Change
Define a boolean: `recent_compression_ok` on 4h bars:

- `recent_compression_ok = any(bb_width_pct <= bb_width_pct_threshold for the last K completed 4h bars)`
- K defaults to **6** (24 hours), test 6–12.

Then your setup condition becomes:
- `donch_breakout AND recent_compression_ok AND regime_ok AND viability_ok`

### New config params
- `compression_lookback_4h: int = 6`  # 6 bars = 24h

### Notes
- This should increase setups without loosening the compression percentile itself too much.
- Keep the percentile window (e.g. 120–180) as currently implemented.

## A2) Slightly Loosen Compression Threshold and Window (for 2–4/month target)
Phase 2.1 used `bb_width_pct_threshold=20` and got too few setups on 180 days.

### Change (defaults)
- `bb_width_pct_threshold`: **20 → 30**
- `bb_width_window`: **180 → 120** (more adaptive)

### New default preset
- `PHASE22_BASELINE`: threshold=30, window=120, lookback=6

## A3) Donchian Lookback: Shorten to increase breakout opportunities
Optimizer previously showed donch_len=10 is best pocket. For 2–4/month, include slightly faster lengths.

### Change
Expand/search:
- `donch_len`: **{8, 10, 12}** (default 10)

---

# B) Entry Changes (Higher Fill Rate, Still Maker-First)

## B1) Place a Resting Retest Bid Immediately at Setup (not later)
Current retest logic often waits for retest/reclaim conditions before placing the bid, which misses the first dip that would have filled you.

### Change
When a setup triggers (4h close), immediately place a post-only limit buy at:

- `p_retest = breakout_level * (1 - retest_offset)`

Where:
- `breakout_level = donch_high_prev` (or setup high, choose consistent definition)

This order **rests immediately** and stays active until:
- it fills, or
- retest TTL expires, or
- setup TTL expires

### New config params
- `retest_offset: float = 0.0015`  # 0.15% (test 0.10–0.25%)
- `retest_ttl_minutes: int = 45`   # earlier escalation (was 120)

### Post-only rule
Only place if `p_retest < current_close_1m` at placement time; otherwise skip/adjust downward.

## B2) Earlier Chase Escalation + Volatility-Aware Chase Offset
If retest doesn't fill quickly, the move may run away. But we still want maker-first.

### Change
If retest bid not filled by `retest_ttl_minutes`, switch to CHASE mode:

- `chase_offset = max(chase_offset_min, chase_atr_mult * atr_pct_4h)`
- `p_chase = current_close_1m * (1 - chase_offset)`

### New config params
- `enable_chase_entry: bool = True`
- `chase_offset_min: float = 0.0005`  # 0.05%
- `chase_atr_mult: float = 0.25`
- `chase_ttl_minutes: int = 120`

## B3) Chase Max-Extension Guard (avoid late, adverse entries)
Only allow CHASE if price hasn't extended too far above breakout level:

- `extension = (current_close_1m / breakout_level) - 1`
- Allow chase only if `extension <= chase_max_extension`

### New config param
- `chase_max_extension: float = 0.005`  # 0.5% (test 0.3–1.0%)

## B4) Chase Reprice Attempts (increase fills without crossing)
One chase order may miss. Allow a small number of reprices while still post-only.

### Change
During chase window:
- Every `chase_reprice_interval_minutes` (e.g. 30), if order not filled:
  - cancel old chase order
  - place a new post-only chase bid at updated `p_chase`

Stop after `chase_max_reprices` attempts or TTL expiry.

### New config params
- `chase_max_reprices: int = 3`
- `chase_reprice_interval_minutes: int = 30`

## B5) Setup TTL Increase (allow fills)
Setups expired too often. Increase the setup TTL.

### Change
- `setup_ttl_bars_4h`: **3 → 6** (12h → 24h)

---

# C) Diagnostics (Must Add)

Add per-setup metrics for every expired setup:
- `min_low_1m_after_setup` during setup lifetime
- `min_low_gap_to_retest = (p_retest - min_low) / breakout_level`
- `min_low_gap_to_chase_min = (min(p_chase_prices) - min_low) / breakout_level`
- `was_order_placeable` flags (post-only)
- `retest_placed`, `chase_placed`, `chase_reprices`

Add these columns to optimizer CSV export:
- `expired_setups`
- `avg_gap_to_fill_retest`
- `avg_gap_to_fill_chase`
- `entry_type_counts` (RETEST vs CHASE)
- `avg_entry_wait_minutes`

This will tell you if you’re bidding too low, too late, or your sim is overly strict.

---

# D) Profit Capture & Risk (Keep, with minor adjustment)

Keep:
- `tp1_fee_mult = 3`
- `tp2_fee_mult = 8`
- trailing activated after TP1

Optional small improvement:
- shift size later if not already:
  - `tp1_qty_frac = 0.30`
  - `tp2_qty_frac = 0.50`
  - runner = 0.20

Stop:
- keep current stop_mult baseline, but allow optimizer to vary: **2.0–3.5**
- Consider “catastrophic-only for first N minutes” only if stopouts remain frequent (Phase 2.3)

---

# E) Optimizer Changes (Hit 2–4 Trades/Month)

## E1) Update parameter bounds
Focus on the productive region and new Phase 2.2 knobs:

- `donch_len`: [8, 10, 12]
- `bb_width_pct_threshold`: [20, 25, 30, 35] (default 30)
- `bb_width_window`: [120, 180] (default 120)
- `compression_lookback_4h`: [6, 12]
- `retest_offset`: [0.0010, 0.0015, 0.0020, 0.0025]
- `retest_ttl_minutes`: [30, 45, 60]
- `chase_offset_min`: [0.0005, 0.0010]
- `chase_atr_mult`: [0.20, 0.25, 0.35]
- `chase_max_extension`: [0.003, 0.005, 0.008]
- `chase_ttl_minutes`: [60, 120, 180]
- `chase_max_reprices`: [2, 3, 4]
- `setup_ttl_bars_4h`: [6] (or [6, 9] if needed)
- `stop_mult`: [2.0, 2.5, 3.0, 3.5]

## E2) Trade frequency constraint
Add a soft constraint scoring for the target range:
- Target trades/month: **2–4** (ideal)
- Acceptable range: **1–6** (no penalty)
- Penalize configs outside acceptable range

### Penalty Formula

```python
def calculate_frequency_penalty(num_trades: int, days: int) -> float:
    """
    Calculate penalty multiplier based on trade frequency.
    Returns: penalty factor in [0.0, 1.0] where 1.0 = no penalty
    """
    trades_per_month = num_trades / (days / 30.0)

    # No penalty if in acceptable range [1, 6]
    if 1.0 <= trades_per_month <= 6.0:
        # Bonus for hitting target zone [2, 4]
        if 2.0 <= trades_per_month <= 4.0:
            return 1.0  # No penalty, ideal
        else:
            return 0.95  # Slight penalty for acceptable but not ideal

    # Penalize configs outside acceptable range
    # Distance from nearest acceptable boundary
    if trades_per_month < 1.0:
        distance = 1.0 - trades_per_month
    else:  # trades_per_month > 6.0
        distance = trades_per_month - 6.0

    # Exponential penalty: 10% per unit distance, max 50% penalty
    penalty = min(0.5, distance * 0.10)
    return 1.0 - penalty

# Usage in optimizer scoring:
final_score = base_score * calculate_frequency_penalty(num_trades, days)
```

### Examples
- 3.0 trades/month (target zone) → penalty = 1.0 (no penalty)
- 5.0 trades/month (acceptable) → penalty = 0.95
- 0.5 trades/month (too sparse) → penalty = 0.95 (distance = 0.5)
- 8.0 trades/month (too frequent) → penalty = 0.80 (distance = 2.0)
- 10.0 trades/month (way too frequent) → penalty = 0.60 (distance = 4.0)

This ensures:
1. Configs in target [2, 4] are not penalized
2. Configs in acceptable [1, 6] have minimal penalty
3. Sparse configs (< 1/month) are penalized moderately
4. Over-trading configs (> 6/month) are penalized progressively

## E3) Minimum trades for ranking
If testing only 60 days:
- `min_trades_for_rank = 6` (to reduce noise)
If testing 180+ days:
- `min_trades_for_rank = 20`

---

# F) File-Level Patch Tasks

## F1) `backtest/config_4h_hybrid.py`
Add new fields:
- `compression_lookback_4h`
- `retest_offset`
- `setup_ttl_bars_4h` (if not already configurable)
- `chase_offset_min`
- `chase_atr_mult`
- `chase_max_extension`
- `chase_max_reprices`
- `chase_reprice_interval_minutes`
Update defaults for `PHASE22_BASELINE`.

## F2) `backtest/engine_4h_hybrid.py`
Ensure you compute and forward-fill:
- `bb_width`, `bb_width_pct`
- 4h Donchian levels
- `atr_pct_4h`
No changes beyond adding `recent_compression_ok` helper access to last K bb_width_pct values.

## F3) `backtest/strategy_4h_hybrid.py`
Changes:
1) Setup trigger uses `recent_compression_ok` when enabled.
2) On setup creation:
   - immediately place `p_retest` bid (post-only check)
3) Retest TTL:
   - if not filled by deadline, start chase mode
4) Chase mode:
   - compute `p_chase` using volatility-aware offset
   - enforce `chase_max_extension`
   - allow reprices up to `chase_max_reprices`
5) Setup TTL: extend to 6×4h by default
6) Add expired-setup diagnostics and entry wait tracking

## F4) Optimizer scripts
- Expand parameter grid as per E1
- Export new diagnostics columns
- Add scoring/penalty for trades/month outside target

---

# G) Suggested Baseline for Phase 2.2

Use this as the next “default run” before sweeping:

- `donch_len = 10`
- `bb_width_window = 120`
- `bb_width_pct_threshold = 30`
- `compression_lookback_4h = 6`
- `vol_min_mult = 1.0` (unchanged unless too restrictive)
- `setup_ttl_bars_4h = 6`
- `retest_offset = 0.0015`
- `retest_ttl_minutes = 45`
- `enable_chase_entry = True`
- `chase_offset_min = 0.0005`
- `chase_atr_mult = 0.25`
- `chase_max_extension = 0.005`
- `chase_ttl_minutes = 120`
- `chase_max_reprices = 3`
- `chase_reprice_interval_minutes = 30`
- `tp1_fee_mult = 3.0`, `tp2_fee_mult = 8.0`
- `tp1_qty_frac = 0.30`, `tp2_qty_frac = 0.50`
- `stop_mult = 2.5`

---

## Success Criteria (Phase 2.2)
Over 180–365 days:
- Trades/month in **2–4** band (acceptable 1–6)
- Net P&L positive under closer-to-actual fees
- Entry conversion improves (signals→filled entries)
- Win rate and PF remain reasonable (do not chase 90% win rate)
- Missed fill diagnostics show gaps shrinking (bids are “close enough”)

---

**End of Phase 2.2 Patch Spec**


---

# Phase 2.2 Addendum — Clean State Enum + Repricing Policy

This addendum refines the Phase 2.2 patch spec by:
1) Defining a **clean, explicit state enum** for pre-entry behavior (retest vs chase)  
2) Defining a **maker-first repricing policy** that improves fill rate while avoiding accidental taker behavior

## 1) Clean State Enum (Phase 2.2)

Replace the ambiguous pre-entry states (`PENDING_SETUP`, `ENTRY_ORDER_WORKING`) with explicit states that match intent:

### 1.1 Enum
- `FLAT` — no active setup, no orders, no position
- `SETUP_ACTIVE` — a setup exists and is valid; an order may or may not be placeable yet
- `RETEST_ORDER_WORKING` — a **retest** post-only bid is currently resting
- `CHASE_ORDER_WORKING` — a **chase** post-only bid is currently resting (and may be repriced)
- `IN_POSITION` — position open and being managed (TPs / trail / stop)

### 1.2 State Transition Diagram (high-level)
1) `FLAT → SETUP_ACTIVE`  
   When 4h setup triggers and all gates pass (regime, viability, compression/recent compression).
2) `SETUP_ACTIVE → RETEST_ORDER_WORKING`  
   Immediately attempt to place the **retest bid** (`p_retest = breakout_level*(1-retest_offset)`) if post-only placeable.
3) `SETUP_ACTIVE → SETUP_ACTIVE` (self-loop)  
   If retest bid is not placeable (too close), keep setup active and retry on next 1m bar (or after a short delay).
4) `RETEST_ORDER_WORKING → IN_POSITION`  
   If `low_1m <= p_retest` (fill) → maker fill.
5) `RETEST_ORDER_WORKING → CHASE_ORDER_WORKING`  
   If retest TTL expires without fill → cancel retest, begin chase mode (if enabled and chase guard passes).
6) `CHASE_ORDER_WORKING → IN_POSITION`  
   If `low_1m <= p_chase` (fill) → maker fill.
7) `CHASE_ORDER_WORKING → FLAT`  
   If chase TTL expires (or max reprices exceeded) without fill → abandon setup.
8) `SETUP_ACTIVE/RETEST_ORDER_WORKING/CHASE_ORDER_WORKING → FLAT`  
   If setup TTL expires → abandon setup and cancel any working orders.
9) `IN_POSITION → FLAT`  
   Upon full position exit.

### 1.3 Logging hooks by state (recommended)
- On entering `SETUP_ACTIVE`: log `setup_time`, `breakout_level`, `bb_width_pct`, `atr_pct_4h`
- On entering `RETEST_ORDER_WORKING`: log `p_retest`, `retest_deadline`
- On entering `CHASE_ORDER_WORKING`: log `p_chase`, `chase_deadline`, `extension`, `chase_offset_used`
- On `setup_expired`: log min-low diagnostics and gap-to-fill (see Phase 2.2 diagnostics section)

---

## 2) Maker-First Repricing Policy (CHASE_ORDER_WORKING)

**Design goal:** Improve fill probability without turning the strategy into a taker or into a “bid lower and never fill” pattern.

### 2.1 Principle
- **Repricing should be one-directional upward (more aggressive)** to improve fills.
- **Downward repricing is allowed only as a safety action** to maintain post-only placeability (avoid crossing / accidental taker assumptions).

### 2.2 Definitions
- `p_old`: currently working chase bid price
- `p_target`: target chase bid given current market and chase offset  
  - `p_target = current_close_1m * (1 - chase_offset)`
- `chase_offset`: volatility-aware:
  - `chase_offset = max(chase_offset_min, chase_atr_mult * atr_pct_4h)`
- `maker_safety_offset`: small offset used only if a safety-down reprice is required  
  - default can reuse `chase_offset_min`

Optional:
- `max_step_up_per_reprice`: cap how much you can increase the bid per reprice (prevents jumping too far)
  - e.g. 0.10%–0.25%

### 2.3 Reprice schedule
- Evaluate repricing every `chase_reprice_interval_minutes` (e.g., 30 minutes).
- Stop repricing after `chase_max_reprices` or when chase TTL expires.

### 2.4 Upward-only reprice rule (normal)
If `p_target > p_old`:
1) compute:
   - `p_up = p_target`
   - if using step cap: `p_up = min(p_target, p_old * (1 + max_step_up_per_reprice))`
2) cancel existing order and place new post-only bid at `p_up`.

### 2.5 Safety-down rule (only to remain post-only)
If `p_target <= p_old`, normally **do nothing** (keep p_old).  
However, if the current p_old is no longer “post-only placeable” under your simulation rule, then:
1) cancel order
2) place a new maker-safe bid at:
   - `p_safe = current_close_1m * (1 - maker_safety_offset)`
3) transition remains `CHASE_ORDER_WORKING`.

**Important:** This safety-down is *not* a discretionary “less aggressive” reprice. It is solely to preserve maker-only behavior assumptions.

### 2.6 Suggested new/updated config params
- `max_step_up_per_reprice: float = 0.0020` (0.20%)  *(optional but recommended)*
- `maker_safety_offset: float = 0.0005` (0.05%)  *(optional; can reuse chase_offset_min)*

### 2.7 Pseudocode (CHASE_ORDER_WORKING)
```python
if low_1m <= p_old:
    fill_entry(p_old, maker_fee)
    state = IN_POSITION
    return

if now >= chase_deadline or chase_reprices_done >= chase_max_reprices:
    cancel_order()
    abandon_setup()
    state = FLAT
    return

if minutes_since_last_reprice >= chase_reprice_interval_minutes:
    chase_offset = max(chase_offset_min, chase_atr_mult * atr_pct_4h)
    p_target = close_1m * (1 - chase_offset)

    if p_target > p_old:
        p_new = min(p_target, p_old * (1 + max_step_up_per_reprice))
        cancel_order()
        place_post_only_bid(p_new)
        p_old = p_new
        chase_reprices_done += 1
        last_reprice_ts = now

    else:
        if not post_only_placeable(p_old, close_1m):
            p_safe = close_1m * (1 - maker_safety_offset)
            cancel_order()
            place_post_only_bid(p_safe)
            p_old = p_safe
            chase_reprices_done += 1
            last_reprice_ts = now
```

---

## 3) Patch Notes (what Claude should change)

### 3.1 `backtest/strategy_4h_hybrid.py`
- Replace Phase 2.2 pre-entry states with the clean enum above.
- Ensure transitions follow the diagram.
- Implement repricing logic exactly as in section 2.

### 3.2 `backtest/config_4h_hybrid.py`
Add optional parameters:
- `max_step_up_per_reprice`
- `maker_safety_offset`

Update baseline preset to include reasonable defaults.

---

**End of addendum.**


---

# Backtest Implementation Clarification — Repricing Without Real Order IDs

In live trading, repricing implies **canceling** an existing order and **placing** a new one, typically using broker/exchange order IDs.

In this backtest framework, we do **not** track real exchange order IDs. Instead, treat “orders” as **simulated intents** represented by fields on the setup/order state object. “Cancel” and “replace” are implemented by **overwriting** these fields.

## 1) Backtest Order Representation (Recommended)

Represent a working entry order as a simple in-memory object (or fields on `PendingSetup` / state):

- `entry_order_active: bool`
- `entry_order_type: str` in {`RETEST`, `CHASE`}
- `entry_price: float`  (the current working bid price)
- `entry_created_ts`
- `entry_last_update_ts`
- `entry_reprices: int`
- `entry_deadline_ts`
- Optional diagnostics:
  - `entry_price_history: list[float]` (or store min/max)
  - `entry_placeable_failures: int`

In this model:
- “Place order” = set `entry_order_active=True`, set price & timestamps
- “Cancel order” = set `entry_order_active=False` (optionally keep last price in diagnostics)
- “Replace order” = update `entry_price` and `entry_last_update_ts`, increment `entry_reprices`

## 2) Fill Check (Backtest)

Each 1m bar, if `entry_order_active`:

- For a **buy** limit at `entry_price`:
  - filled if `low_1m <= entry_price`
  - fill price = `entry_price`
  - fee = maker fee (since post-only intent)

If filled:
- open position
- clear entry order state

## 3) Repricing Semantics (Backtest)

Instead of canceling by order ID, implement repricing as updating the current working bid price.

### Key principle (UP-only repricing)
Repricing should move the bid **UP (more aggressive)** to improve fill odds.
For a buy bid, “more aggressive” means **higher price** (closer to current market).

Therefore:
- if the newly computed bid `p_chase_new` is **greater than** the existing `p_chase_old`, then reprice upward:
  - `p_chase_old = p_chase_new`
  - increment `entry_reprices`

(If you used the opposite inequality earlier, correct it: for bids, UP means a higher price.)

### Pseudocode (Backtest repricing: overwrite fields)
```python
# Called each 1m bar while in CHASE_ORDER_WORKING and not filled
if chase_mode and entry_order_active and not filled:
    if (now - entry_last_update_ts) >= reprice_interval:
        chase_offset = max(chase_offset_min, chase_atr_mult * atr_pct_4h)
        p_chase_new = close_1m * (1 - chase_offset)

        # UP-only repricing: move bid higher (more aggressive)
        if p_chase_new > entry_price:
            entry_price = min(p_chase_new, entry_price * (1 + max_step_up_per_reprice))
            entry_reprices += 1
            entry_last_update_ts = now

        # Safety-down only to remain post-only placeable (optional)
        elif not post_only_placeable(entry_price, close_1m):
            entry_price = close_1m * (1 - maker_safety_offset)
            entry_reprices += 1
            entry_last_update_ts = now
```

## 4) Mapping “Cancel old chase order” to Backtest Code

When the spec says:
- “cancel old chase order and place new one”

In backtest, interpret as:
- update `entry_price` (and `entry_last_update_ts`)
- increment reprices counter
- optionally append to `entry_price_history`

No order IDs are needed.

## 5) Note About Your Proposed Snippet

You suggested:
```python
if p_chase_new < order.chase_price:  # Only reprice UP (more aggressive)
```

For **buy** orders, this condition is reversed:
- A **higher** bid is more aggressive
- So the UP-only check should be:
  - `if p_chase_new > order.chase_price`

If you were thinking of a sell order, the inequality would flip. For this long-entry chase bid, use `>` for upward repricing.

---

**End of clarification.**


---

# Phase 2.2 Clarification — Order Expiry Timers and Recent Compression Check

This section clarifies how to handle **order expiry** in backtesting/live logic when you do not track real exchange order IDs, and confirms the intended implementation of the **recent compression** gate.

## 1) Order Expiry: Per-Order TTL vs Mode-Transition Deadlines

### Recommendation (aligned with your preference)
**Remove individual per-order TTLs** and use only **mode transition deadlines** tied to the setup timestamp. This is simpler, reduces edge-case complexity, and matches the state machine goals (SETUP_ACTIVE → RETEST → CHASE → ABANDON).

Use these three deadlines:

1) **Retest deadline**
- `retest_deadline_ts = setup_time + retest_ttl_minutes`
- Behavior: if not filled by this time, transition from `RETEST_ORDER_WORKING → CHASE_ORDER_WORKING` (if enabled and chase guard passes)

2) **Chase deadline**
- `chase_deadline_ts = setup_time + retest_ttl_minutes + chase_ttl_minutes`
- Behavior: if not filled by this time (or reprices exhausted), abandon setup and return to `FLAT`

3) **Setup deadline (hard stop)**
- `setup_deadline_ts = setup_time + setup_ttl_bars_4h * 4h`
- Behavior: regardless of mode, if the setup hits this deadline without entry fill, abandon and return to `FLAT`

### Implications
- A “cancel and replace” (reprice) does **not** reset the chase deadline. Reprices are bounded by the same chase window.
- You may still keep a `chase_max_reprices` limit to prevent thrashing.

### Implementation fields (recommended)
On the setup object:
- `setup_time`
- `retest_deadline_ts`
- `chase_deadline_ts`
- `setup_deadline_ts`
- `entry_order_active`
- `entry_order_type` (RETEST/CHASE)
- `entry_price`
- `entry_last_update_ts`
- `entry_reprices`

### State behavior with deadlines
- On setup creation: compute all deadlines immediately.
- During `RETEST_ORDER_WORKING`:
  - if filled → `IN_POSITION`
  - elif now >= retest_deadline_ts → enter CHASE mode (or abandon if chase disabled/guard fails)
  - elif now >= setup_deadline_ts → abandon
- During `CHASE_ORDER_WORKING`:
  - if filled → `IN_POSITION`
  - elif now >= chase_deadline_ts → abandon
  - elif now >= setup_deadline_ts → abandon (hard stop)
  - elif repricing interval elapsed and reprices remaining → update entry_price (UP-only policy)

> This matches your proposed scheme exactly.

---

## 2) A1 Implementation Detail — Recent Compression Check

Your suggested function aligns with the updated spec and makes sense to implement.

### Proposed implementation
```python
def check_recent_compression(bb_width_pct_history: list[float], threshold: float, lookback: int) -> bool:
    """True if BB width percentile was compressed recently."""
    if len(bb_width_pct_history) < lookback:
        return False
    recent = bb_width_pct_history[-lookback:]
    return any(pct <= threshold for pct in recent)
```

### Does it align with the spec?
Yes. Phase 2.2 defines `recent_compression_ok` as:
- “compressed in any of the last K completed 4h bars”

So `any(...)` is the canonical implementation.

### Should it be “any bar compressed” or “most bars compressed”?
For the **Phase 2.2 goal (2–4 trades/month)**, use **ANY** as the default, because:

- “Most bars compressed” is substantially stricter and will likely recreate the 180-day sparsity problem.
- The breakout event often occurs shortly after a period of compression; requiring *most* bars compressed can exclude valid “compression → first expansion” transitions.

#### Optional enhancement (if you later get too many low-quality setups)
Add a configurable “compression quorum”:

- `compression_quorum = 0.50` means at least 50% of last K bars compressed.

Implementation:
```python
def check_recent_compression(bb_width_pct_history, threshold, lookback, quorum=0.0) -> bool:
    if len(bb_width_pct_history) < lookback:
        return False
    recent = bb_width_pct_history[-lookback:]
    hits = sum(1 for pct in recent if pct <= threshold)
    return hits >= max(1, int(lookback * quorum))
```

Defaults:
- Phase 2.2 baseline: `quorum = 0.0` (ANY)
- Later tightening: `quorum = 0.34` or `0.50` if needed.

### Recommendation
- Implement **ANY** now to recover frequency.
- Keep the quorum option behind a parameter if you need later quality tightening.

---

**End of clarification.**


---

# H) Phase 2.2 Implementation Order (Recommended)

To minimize breaking changes and isolate cause/effect, implement Phase 2.2 in stages:

## H1) Phase 2.2a — Diagnostics First
1) Add / verify **ExpiredSetup tracking** and “distance-to-fill” diagnostics:
   - `min_low_1m_after_setup`
   - `gap_to_retest`, `gap_to_chase_min`
   - `entry_placeable_failures`
   - `entry_type` + `entry_wait_minutes`
2) Run **current Phase 2.1** (no behavior changes) with diagnostics enabled:
   - 60 days sanity run
   - 180 days baseline run

**Goal:** establish a baseline for setup frequency, conversion, and where fills are being missed.

## H2) Phase 2.2b — Entry Logic Refactor (Fill Rate)
1) Implement **B1** (immediate resting retest bid)
2) Implement **B2–B4** (earlier chase escalation + volatility-aware chase + repricing policy)
3) Implement **B5** (setup TTL increase and deadline-only timers)

Testing sequence:
- 60 days: confirm logic correctness, no runaway order updates, and fill rate improves
- 180 days: confirm conversion improves vs Phase 2.1 baseline

**Goal:** improve signal→entry conversion without adding churn.

## H3) Phase 2.2c — Gate Loosening (Setup Frequency)
1) Implement **A1–A3** (recent compression lookback, threshold/window tweaks, donch_len adjustments)
2) Test sequence:
- 60 days: quick correctness & frequency sanity check
- 180 days: validate setup frequency increases toward 2–4 trades/month target

**Goal:** restore trade frequency while preserving quality.

## H4) Phase 2.2d — Optimizer Update
1) Update parameter grid/bounds (Section E)
2) Run comprehensive sweep on a window long enough for meaningful sample size:
   - Prefer 180–365 days for ranking
3) Select best config and freeze as “Phase 2.3 candidate”

**Goal:** pick a robust configuration after structure is stabilized.

---

# I) Known Parameter Interactions (Warnings)

Certain parameters interact strongly; avoid tuning them all at once. Start from the baseline (Section G) and tune one group at a time.

## I1) Retest vs Chase Balance
- **retest_offset** too large (bid too low) → retest fills drop → CHASE fills dominate.
- **retest_ttl_minutes** too short → early escalation → CHASE dominates even when retest would have filled.
- **chase_offset_min** too small (bid too close) → more CHASE fills but higher risk of “late entry” adverse selection.

**Practical guidance:** aim for a healthy mix (not 0% chase and not 100% chase). Track RE/CH counts per window.

## I2) Compression Lookback vs Threshold
- Longer `compression_lookback_4h` **and** loose `bb_width_pct_threshold` → too many setups (quality drops).
- Short lookback **and** tight threshold → too few setups (like Phase 2.1 sparsity).

**Guidance:** adjust one at a time:
- first loosen threshold/window to restore frequency
- then adjust lookback for quality

## I3) Chase Max-Extension vs Chase Window
- Tight `chase_max_extension` **and** long chase TTL → lots of waiting with no eligible chase placement.
- Loose max-extension **and** short chase TTL → more chase attempts but potentially worse entries.

**Guidance:** if you tighten max-extension, shorten chase TTL and rely more on retest bids.

## I4) Setup TTL vs Reprice Dynamics
- Short setup TTL + long reprice interval + many allowed reprices → reprices never actually occur before expiry.
- Very long TTL + frequent reprices → excessive order churn (even if simulated).

**Guidance:** keep reprices bounded (2–4) and interval moderate (30–60m), then tune TTL.

## I5) BB Percentile Window vs Threshold
- Smaller `bb_width_window` makes percentile ranks more “twitchy.” A threshold of 30 on window 120 behaves differently than 180.

**Guidance:** treat window length as a structural choice; re-tune thresholds after changing the window.

---

**Recommendation:** Start with the baseline preset in Section G, tune one group at a time, and always re-check:
- trades/month (2–4 target)
- conversion rate (signals→fills)
- maker vs taker fee incidence
- net expectancy stability across time splits
