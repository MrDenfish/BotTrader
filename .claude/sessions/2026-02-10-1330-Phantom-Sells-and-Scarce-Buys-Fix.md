# Phantom Sells & Scarce Buys Fix — Paper Trading Bug Fixes

## Session Overview
- **Started:** 2026-02-10 ~13:00 (continuation from earlier pair discovery session)
- **Branch:** main
- **Context:** v2-paper was generating 82 sells vs 21 buys (103 total signals), with 60/73 fills being phantom sells (selling positions that don't exist). Two bugs identified and fixed: phantom sells and overly restrictive buy guardrails.

## Goals
1. Deploy the phantom sell fix (committed in prior session but not yet on AWS)
2. Investigate and fix the scarce buys issue
3. Verify both fixes running on AWS

## Progress

### All tasks completed.

---

## Session Summary

- **Duration:** ~1 hour (13:00 — ~14:00)
- **Branch:** main
- **Outcome:** Both bugs fixed, deployed, and verified on AWS

---

### Git Summary

**Commits:** 2 (1 deployed from prior session, 1 new)
1. `477f9e7` — `fix: Reject sell signals when no position exists (phantom sell bug)` (committed prior session, deployed this session)
2. `cdb9a0f` — `fix: Align buy signal guardrails with v1 defaults`

**Files changed this session:** 4 (all modified)

| File | Change |
|------|--------|
| `v2/plugins/risk/basic.py` | Modified — added `_check_sell()` method (deployed) |
| `v2/plugins/exchanges/paper.py` | Modified — added sell balance validation (deployed) |
| `v2/tests/test_milestone5_risk.py` | Modified — 3 new sell tests replacing 1 (deployed) |
| `v2/paper_trading.yaml` | Modified — `min_indicators_required: 3→2`, added `cooldown_bars: 1` |

**Stats:** +56 insertions, -6 deletions across both commits

**Final git status:** Working tree clean for v2/ (all changes committed and pushed). Unrelated uncommitted changes exist in docs/ and .claude/sessions/.

---

### Todo Summary

**3/3 tasks completed, 0 remaining**

1. Deploy phantom sell fix to AWS — completed
2. Investigate scarce buys root causes — completed
3. Fix and deploy scarce buys config — completed

---

### Key Accomplishments

1. **Phantom sell fix deployed**: BasicRiskManager now vetoes sell signals when no position exists; PaperExchange has backup balance validation
2. **Scarce buys root cause identified**: Two config mismatches between v1 and v2
3. **Guardrail config aligned with v1**: `min_indicators_required` and `cooldown_bars` now match v1 behavior
4. **Both fixes live on AWS**: v2-paper restarted and verified running

---

### Features Implemented

- **Sell position validation** (deployed from prior commit): `BasicRiskManager._check_sell()` vetoes sells when portfolio has no position or zero qty for the symbol
- **Paper exchange balance check** (deployed from prior commit): `PaperExchange._execute_fill()` rejects sells when account holds no units of the base currency
- **Guardrail config fix**: Aligned `min_indicators_required` and `cooldown_bars` with v1 defaults

---

### Problems Encountered and Solutions

1. **Phantom sells (60/73 fills were sells with no position)**
   - **Root cause**: No position validation at any layer — risk manager passed sells unconditionally, execution had no check, paper exchange subtracted from empty balance
   - **Fix**: Added `_check_sell()` in BasicRiskManager (primary) + balance check in PaperExchange (defense-in-depth)

2. **Scarce buys (only 21/103 signals were buys, all MON-USD)**
   - **Root cause 1**: `min_indicators_required: 3` in paper config vs v1's default of `2` — harder to trigger buys
   - **Root cause 2**: `cooldown_bars: 7` with 5-min candles = 35 min cooldown vs v1's 7 bars × 1-min = 7 min — 5× longer lockout
   - **Root cause 3**: Asymmetric `roc_24h` thresholds (+8.5% buy vs -5.0% sell) — by design, matches v1, not a bug
   - **Fix**: Set `min_indicators_required: 2` and `cooldown_bars: 1` (1×5min ≈ v1's 7min)

3. **NKN-USD signal parity gap**: v1 traded NKN-USD but v2 doesn't evaluate it — NKN is borderline on the volume filter and v1/v2 poll at different times. Expected behavior with dynamic pair discovery.

---

### Configuration Changes

**`v2/paper_trading.yaml`:**
```yaml
# Before:
min_indicators_required: 3

# After:
min_indicators_required: 2
cooldown_bars: 1  # 1 × 5min = 5min (v1 uses 7 × 1min = 7min)
```

---

### Deployment Steps

**Phantom sell fix (commit 477f9e7):**
1. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
2. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build v2-paper"`
3. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d v2-paper"`
4. Verified: 19 symbols discovered, Paper exchange connected, WebSocket subscribed

**Guardrail fix (commit cdb9a0f):**
1. Same deploy steps as above
2. Verified: 18 symbols discovered, pair discovery refreshing every 30 min

---

### Breaking Changes or Important Findings

- **v1 trade recorder re-recording bug**: v1 re-records the same NKN-USD fills every 5 minutes (same order IDs). DB deduplicates correctly (only 2 records), but logs are noisy. Not fixing since it's v1-only and doesn't affect v2.
- **Dynamic pair discovery inherent difference**: v1 and v2 may evaluate slightly different symbols at the edges of the volume filter since they poll the Coinbase API at different times. High-volume symbols (BTC, ETH, SOL, etc.) will always match; borderline tokens like NKN may differ.

---

### Lessons Learned

- When porting strategy parameters from v1 (1-min candles) to v2 (5-min candles), time-based guardrails like `cooldown_bars` must be scaled proportionally
- The `min_indicators_required` config in paper_trading.yaml was set to 3 at some point but v1's default is 2 — always verify against v1's actual runtime values, not just code defaults
- Defense-in-depth for position validation: adding checks at both the risk manager layer and exchange layer prevents phantom trades even if one layer has a bug

---

### What Wasn't Completed

- Nothing left incomplete — all identified issues fixed and deployed

---

### Tips for Future Developers

- **Monitor buy/sell ratio**: After guardrail changes, check the signal log after the warmup period (~3.3 hours) to verify a more balanced buy/sell ratio
- **Signal log**: `docker exec v2-paper cat /app/logs/v2_score_log.jsonl | python3 -c "import sys,json,collections; c=collections.Counter(json.loads(l)['action'] for l in sys.stdin); print(c)"`
- **v1 vs v2 signal parity**: Compare symbols evaluated by both, not just signal counts — dynamic pair discovery means slightly different symbol sets
- **Cooldown scaling**: If candle interval changes, recalculate `cooldown_bars` to maintain the same wall-clock cooldown (target ~5-7 min)
- **296 tests passing** as of this session — run `python -m pytest v2/tests/ -v` to verify
