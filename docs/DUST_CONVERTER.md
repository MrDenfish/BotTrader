# Cryptocurrency Dust to BTC Converter

Automatically convert small cryptocurrency balances (dust) to BTC using the Coinbase Convert API.

## Overview

The dust converter identifies cryptocurrency balances below a configurable threshold ($0.50 by default) and converts them to BTC using Coinbase's Convert API.

## Features

- ✅ Automatic dust detection (balances < $0.50)
- ✅ Direct crypto-to-crypto conversion using Coinbase Convert API
- ✅ Dry-run mode for safe testing
- ✅ Comprehensive logging
- ✅ Configurable thresholds
- ✅ Excludes BTC and stablecoins (USD, USDC, USDT)
- ✅ Rate limiting between conversions

## Usage

### Dry-Run (Safe Testing)

Test without making any actual conversions:

```bash
python scripts/convert_dust_to_btc.py --dry-run
```

This will:
1. Fetch all your Coinbase account balances
2. Identify dust (< $0.50)
3. Calculate total dust value
4. Show what WOULD be converted

### Live Conversion

Execute actual conversions:

```bash
python scripts/convert_dust_to_btc.py
```

⚠️ **Warning:** This will execute real conversions. Use dry-run first!

## How It Works

### 1. Fetch Account Balances

Uses Coinbase Advanced Trade API `/api/v3/brokerage/accounts` to get all account balances.

### 2. Identify Dust

Filters accounts based on:
- Non-zero balance
- USD value < $0.50 (configurable)
- Not BTC, USD, USDC, or USDT
- Account is active and ready

### 3. Convert to BTC

For each dust balance:
1. Create convert quote: `POST /api/v3/brokerage/convert/quote`
2. Commit conversion: `POST /api/v3/brokerage/convert/{trade_id}`
3. Log results

## Configuration

Edit `scripts/convert_dust_to_btc.py`:

```python
# Dust threshold (balances below this are converted)
self.dust_threshold_usd = Decimal("0.50")

# Currencies to exclude from conversion
self.excluded_currencies = {
    "USD", "USDC", "USDT",  # Stablecoins
    "BTC",  # Target currency
}

# Target currency
self.target_currency = "BTC"
```

## Cron Job Setup

### Weekly Conversion (Recommended)

Run every Sunday at 2:00 AM:

```bash
# Edit crontab
crontab -e

# Add this line:
0 2 * * 0 cd /opt/bot && docker exec webhook python scripts/convert_dust_to_btc.py >> /opt/bot/logs/dust_converter.log 2>&1
```

### Other Schedules

```bash
# Daily at 3 AM
0 3 * * * cd /opt/bot && docker exec webhook python scripts/convert_dust_to_btc.py >> /opt/bot/logs/dust_converter.log 2>&1

# First day of month at 1 AM
0 1 1 * * cd /opt/bot && docker exec webhook python scripts/convert_dust_to_btc.py >> /opt/bot/logs/dust_converter.log 2>&1
```

### Check Cron Job Status

```bash
# View current cron jobs
crontab -l

# Check logs
tail -f /opt/bot/logs/dust_converter.log
```

## API Methods Added

### CoinbaseAPI (`Api_manager/coinbase_api.py`)

#### `get_accounts()`
Fetches all account balances from Coinbase.

```python
accounts = await api.get_accounts()
# Returns: List[Dict] with account details
```

#### `create_convert_quote(from_account, to_account, amount)`
Creates a conversion quote.

```python
quote = await api.create_convert_quote(
    from_account="doge-account-uuid",
    to_account="btc-account-uuid",
    amount="100.50"
)
trade_id = quote["data"]["trade"]["id"]
```

#### `commit_convert_trade(trade_id)`
Commits a conversion after creating a quote.

```python
result = await api.commit_convert_trade(trade_id)
# Executes the actual conversion
```

#### `get_convert_trade(trade_id)`
Gets status of a conversion.

```python
status = await api.get_convert_trade(trade_id)
```

## Example Output

### Dry-Run Mode

```
============================================================
🚀 Dust to BTC Converter - DRY RUN MODE
============================================================
Dust threshold: $0.50
Target currency: BTC
Excluded currencies: BTC, USD, USDC, USDT

🔍 Fetching account balances...
📊 Found 47 total accounts
📋 Found 12 non-zero crypto balances
💰 Dust found: 15.234 DOGE = $0.4521
💰 Dust found: 0.0045 ETH = $0.3211
💰 Dust found: 125.5 XRP = $0.2891

📋 Found 3 dust balances

🔍 DRY RUN MODE - No actual conversions will be made
Would convert 3 dust balances:
  • 15.234 DOGE ($0.4521) → BTC
  • 0.0045 ETH ($0.3211) → BTC
  • 125.5 XRP ($0.2891) → BTC

============================================================
📊 CONVERSION SUMMARY
============================================================
Total dust balances: 3
Total dust value: $1.0623
Converted: 0
Failed: 0
Reason: dry_run
============================================================
```

### Live Conversion

```
============================================================
🚀 Dust to BTC Converter - LIVE MODE
============================================================

🔄 Converting 15.234 DOGE to BTC...
📊 Quote: 15.234 DOGE → 0.00001523 BTC (Trade ID: abc12345...)
✅ Converted 15.234 DOGE → 0.00001523 BTC (Status: SUCCESS)

🔄 Converting 0.0045 ETH to BTC...
📊 Quote: 0.0045 ETH → 0.00003211 BTC (Trade ID: def67890...)
✅ Converted 0.0045 ETH → 0.00003211 BTC (Status: SUCCESS)

============================================================
📊 CONVERSION SUMMARY
============================================================
Total dust balances: 3
Total dust value: $1.0623
Converted: 2
Failed: 0
============================================================
```

## Troubleshooting

### Permission Errors

Ensure your Coinbase API key has `trade` permission:
- Create Convert Quote requires: `trade`
- Commit Convert Trade requires: `trade`
- Get Convert Trade requires: `view`

### Minimum Balance Errors

Some cryptocurrencies may have minimum conversion amounts. Very small dust (< $0.01) may fail to convert.

### Rate Limiting

The script includes 0.5s delay between conversions to avoid rate limits. If you hit rate limits, the delay can be increased.

## Safety Features

1. **Dry-run mode**: Test without executing
2. **Excluded currencies**: Never converts BTC or stablecoins
3. **Logging**: All operations logged
4. **Error handling**: Failed conversions don't stop the process
5. **Rate limiting**: Delays between conversions

## Files

- **Script:** `scripts/convert_dust_to_btc.py`
- **API Methods:** `Api_manager/coinbase_api.py` (lines 982-1242)
- **Documentation:** `docs/DUST_CONVERTER.md` (this file)

## Testing Checklist

- [x] Syntax validation
- [ ] Dry-run test (safe)
- [ ] Small live test with one dust balance
- [ ] Full live test with all dust
- [ ] Cron job setup
- [ ] Log monitoring

## Support

For issues or questions:
1. Check logs: `/opt/bot/logs/dust_converter.log`
2. Run with `--dry-run` to diagnose
3. Check Coinbase API status
4. Verify API key permissions
