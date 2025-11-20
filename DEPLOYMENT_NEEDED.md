# 🚨 URGENT: Bot Deployment Needed for PnL Fix

## Problem Detected

The bot is currently running **OLD CODE** that causes incorrect parent matching for sell orders.

## Evidence

### ZEC-USD Trades from 21:35-21:39 (Evening Session)

**What HAPPENED (bot using old code):**
```
❌ WRONG | 21:35:32 | Sell $665.18 | Matched to Oct 27 parent | PnL: +$13.46
❌ WRONG | 21:37:02 | Sell $666.49 | Matched to Oct 27 parent | PnL: +$13.52
❌ WRONG | 21:39:53 | Sell $663.18 | Matched to Oct 27 parent | PnL: +$13.25
                                                        Total: +$40.23 (FALSE PROFIT)
```

**What SHOULD HAVE happened (with fixed code):**
```
✅ CORRECT | 21:35:32 | Sell should match to 21:35:01 Buy $666.76 | PnL: ~-$0.20
✅ CORRECT | 21:37:02 | Sell should match to 21:36:07 Buy $663.82 | PnL: ~-$0.13
✅ CORRECT | 21:39:53 | Sell should match to 21:39:08 Buy $662.70 | PnL: ~-$0.05
                                                           Total: ~-$0.38 (ACTUAL LOSS)
```

## Impact

**Database shows:** +$39.80 profit (8-hour period)
**Actual result:** -$0.58 loss (8-hour period)
**Discrepancy:** $40.38 ❌

All performance reports are **INCORRECT** while bot runs old code.

## Solution

Deploy the `fix/pnl-calculation-bug` branch to the bot:

```bash
# On the machine running the bot:
cd /path/to/BotTrader
git fetch origin
git checkout fix/pnl-calculation-bug
git pull origin fix/pnl-calculation-bug

# Restart the bot
# (Use whatever command you normally use - docker restart, systemctl restart, etc.)
```

## Verification After Deployment

Run this to verify new trades get correct parents:

```bash
psql postgresql://bot_user:@127.0.0.1:5432/bot_trader_db -f verify_pnl_fix.sql
```

Look for:
- ✅ Sell times and parent times should be same day (or within hours)
- ✅ Discrepancy should be near $0.00 (within $1-2 for fees/rounding)
- ❌ If parent times are weeks old (Oct 27), bot is still running old code

## Historical Data

The historical data from earlier today (4 DASH + 4 ZEC sells from 09:48-17:59) has been fixed via SQL.

Only NEW trades since 21:35 are getting wrong parents due to bot running old code.

---

**Created:** 2025-11-19 22:00
**Branch to deploy:** `fix/pnl-calculation-bug`
**Commit:** 6b76a5e
