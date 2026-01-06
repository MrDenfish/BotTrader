# Session: Convert Crypto to Crypto

**Date:** 2026-01-05
**Time Started:** 08:00 PST
**Time Completed:** 09:30 PST
**Status:** COMPLETE

---

## Session Overview

This session focuses on implementing crypto-to-crypto conversion functionality in the BotTrader system.

**Context:**
- Current system likely trades crypto/USD pairs
- Need to understand current architecture and conversion requirements
- Determine if this is for portfolio rebalancing, cross-pair trading, or other use cases

---

## Goals

**To be defined based on user requirements:**
- Understand the specific use case for crypto-to-crypto conversion
- Identify which cryptocurrencies need conversion support
- Determine integration points in the existing system
- Implement conversion logic
- Test and deploy

---

## Progress

### Phase 1: Discovery ✅
- [x] Understand current trading pair architecture
- [x] Identify use case for crypto-to-crypto conversion (dust to BTC)
- [x] Review Coinbase API support for crypto-to-crypto trades
- [x] Determine which cryptocurrencies are involved (all non-BTC, non-stablecoin balances < $0.50)

### Phase 2: Design ✅
- [x] Design conversion flow (two-step: quote → commit)
- [x] Identify affected components (CoinbaseAPI, new script)
- [x] Plan database changes (none needed - conversion only)
- [x] Define API integration requirements (Convert API endpoints)

### Phase 3: Implementation ✅
- [x] Implement conversion logic (Convert API integration)
- [x] Add validation and error handling
- [x] Update relevant managers/handlers (CoinbaseAPI)
- [x] Add logging and monitoring

### Phase 4: Testing & Deployment
- [ ] Test conversion locally (dry-run)
- [ ] Deploy to AWS
- [ ] Set up cron job on AWS
- [ ] Monitor production behavior

---

## Key Decisions

1. **Dust Threshold**: Set to $0.50 USD (configurable)
2. **Target Currency**: BTC (most liquid, best long-term hold)
3. **Excluded Currencies**: USD, USDC, USDT (stablecoins), BTC (target)
4. **Conversion Method**: Coinbase Convert API (direct crypto-to-crypto, no intermediate USD trades)
5. **Safety**: Dry-run mode required before first live run
6. **Frequency**: Weekly cron job (Sunday 2:00 AM recommended)
7. **Rate Limiting**: 0.5s delay between conversions

## Implementation Notes

- User provided critical API endpoint information from their own Coinbase documentation research
- Convert API uses two-step process: create quote → commit trade
- No database changes required (conversion is portfolio-only operation)
- Script is standalone and can run independently via cron
- All conversions logged for audit trail

---

## Session Log

### Implementation Progress

**Phase 1: Account Balance Fetching** ✅
- Added `get_accounts()` method to `Api_manager/coinbase_api.py` (lines 982-1058)
- Fetches all account balances from Coinbase Advanced Trade API
- Supports pagination with cursor
- Returns list of accounts with currency, balance, and metadata

**Phase 2: Dust Converter Script** ✅
- Created `scripts/convert_dust_to_btc.py`
- Implements dust detection logic:
  - Threshold: $0.50 USD
  - Target: BTC
  - Excludes: USD, USDC, USDT, BTC
- Features:
  - Dry-run mode for testing
  - Automatic price fetching
  - Dust identification and USD value calculation
  - Comprehensive logging

**Phase 3: Coinbase Convert API Integration** ✅
- User provided correct API endpoints after research
- Implemented three Convert API methods in CoinbaseAPI:
  - `create_convert_quote()` - Creates conversion quote
  - `commit_convert_trade()` - Commits the conversion
  - `get_convert_trade()` - Gets conversion status
- Integrated Convert API into dust converter script
- Two-step conversion process: quote → commit
- Rate limiting: 0.5s delay between conversions

**Phase 4: Documentation** ✅
- Created comprehensive `docs/DUST_CONVERTER.md`
- Usage instructions (dry-run and live modes)
- Cron job setup with multiple schedule examples
- Troubleshooting guide
- API methods documentation
- Safety features explanation

### Files Modified

1. **Api_manager/coinbase_api.py**
   - Added `get_accounts()` method (lines 982-1058)
   - Added `create_convert_quote()` method (lines 1060-1127)
   - Added `commit_convert_trade()` method (lines 1129-1190)
   - Added `get_convert_trade()` method (lines 1192-1242)
   - Total: +261 lines

2. **scripts/convert_dust_to_btc.py** (New file)
   - Full dust converter implementation
   - ~417 lines
   - Dry-run mode for safe testing
   - Automatic dust detection and conversion
   - Comprehensive error handling and logging

3. **docs/DUST_CONVERTER.md** (New file)
   - Complete usage documentation
   - Cron job setup instructions
   - API reference
   - Troubleshooting guide
   - ~267 lines
