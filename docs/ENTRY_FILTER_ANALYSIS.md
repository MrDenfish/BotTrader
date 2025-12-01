# Entry Filter Analysis - Symbol Blacklist

**Date**: 2025-11-30
**Branch**: feature/profitability-optimization
**Analysis Period**: Last 30 days

## Executive Summary

Analysis of 1,266 position exits over the past 30 days reveals significant opportunity to improve profitability through symbol filtering. By blacklisting 23 poor-performing symbols (< 20% win rate), we can achieve:

- **Win Rate Improvement**: 44.5% → 53.1% (+8.5%)
- **P&L Recovery**: +$585.57 (avoiding losses)
- **Total P&L**: $4,990.66 → $5,576.23 (+11.7%)
- **Trade Reduction**: 1,266 → 1,031 trades (-18.6%, filtering out noise)

## Current Performance (30 Days)

```
Total Exits:     1,266
Wins:            564
Win Rate:        44.5%
Total P&L:       $4,990.66
Avg P&L/Trade:   $3.94
Avg Win:         $13.01
Avg Loss:        -$3.34
R:R Ratio:       3.89:1
```

## Worst Performing Symbols (< 20% Win Rate)

These 23 symbols represent 235 trades with a combined loss of -$585.57:

```
Symbol       | Exits | Wins | WR%  | Total P&L
-------------|-------|------|------|----------
XAN-USD      |     5 |    0 |  0.0 |  -$216.01
LTC-USD      |    11 |    0 |  0.0 |  -$111.33
BTC-USD      |     6 |    0 |  0.0 |   -$53.28
MUSE-USD     |     5 |    0 |  0.0 |   -$25.98
AVNT-USD     |     5 |    0 |  0.0 |   -$14.64
MLN-USD      |     5 |    0 |  0.0 |    -$8.62
SWFTC-USD    |    14 |    0 |  0.0 |    -$5.21
AVAX-USD     |    15 |    0 |  0.0 |    -$2.09
TAO-USD      |    10 |    0 |  0.0 |    -$1.13
ENA-USD      |     7 |    0 |  0.0 |    -$0.66
SOL-USD      |    11 |    0 |  0.0 |    -$0.56
ADA-USD      |     5 |    0 |  0.0 |    -$0.31
SUI-USD      |    14 |    1 |  7.1 |    -$1.42
NEAR-USD     |    11 |    1 |  9.1 |    -$3.36
ORCA-USD     |    20 |    2 | 10.0 |    -$3.78
(+ 8 more symbols)
```

**Complete Blacklist** (23 symbols):
```
1INCH-USD, AAVE-USD, ADA-USD, AVAX-USD, AVNT-USD, BCH-USD, BONK-USD,
BTC-USD, ENA-USD, HBAR-USD, LTC-USD, MLN-USD, MON-USD, MUSE-USD,
NEAR-USD, ORCA-USD, PENGU-USD, SOL-USD, SUI-USD, SWFTC-USD, TAO-USD,
TIA-USD, XAN-USD
```

## Best Performing Symbols (Keep Trading)

Top 10 symbols by win rate (min 5 trades):

```
Symbol      | Exits | Wins | WR%   | Total P&L
------------|-------|------|-------|----------
DASH-USD    |   125 |  125 | 100.0 | $4,426.83
ZEC-USD     |    52 |   51 |  98.1 |   $631.30
ZEN-USD     |    43 |   40 |  93.0 |   $776.61
LPT-USD     |    11 |    8 |  72.7 |    $57.30
STRK-USD    |    18 |   13 |  72.2 |    $62.79
ICP-USD     |    49 |   35 |  71.4 |   $405.99
FIL-USD     |    25 |   17 |  68.0 |   $178.98
QNT-USD     |     9 |    6 |  66.7 |     $2.50
ZK-USD      |    35 |   23 |  65.7 |   $227.52
MINA-USD    |    13 |    8 |  61.5 |    $23.69
```

## Projected Performance (After Blacklist)

```
Filtered Exits:  1,031 (from 1,266)
Filtered Wins:   547 (from 564)
Win Rate:        53.1% (+8.5%)
Total P&L:       $5,576.23 (+$585.57)
```

## Implementation Plan

### 1. Create Blacklist Configuration
- Add `SYMBOL_BLACKLIST` to config or environment variable
- Store as list in code or database

### 2. Modify Signal Generation (sighook)
- Filter out blacklisted symbols from buy_sell_matrix
- Log when symbols are filtered
- Track blacklist effectiveness

### 3. Deployment Strategy
- Test locally with paper trading first
- Deploy to production
- Monitor for 7 days
- Re-evaluate blacklist quarterly

## Expected Impact

**Immediate Benefits**:
- Avoid 235 low-quality trades per month
- Recover $585.57 in avoided losses
- Improve win rate by 8.5 percentage points
- Focus capital on high-probability setups

**Long-term Benefits**:
- Cleaner P&L trajectory
- Reduced drawdown from poor performers
- Better risk-adjusted returns
- Improved psychological confidence (higher win rate)

## Risks and Mitigations

**Risk**: Market conditions change, currently poor symbols improve
**Mitigation**: Re-evaluate blacklist monthly based on rolling 30-day performance

**Risk**: Blacklist becomes stale as new symbols are added to exchange
**Mitigation**: Only blacklist symbols with >= 5 trades, ensuring statistical significance

**Risk**: Reducing trade count may reduce absolute profits
**Mitigation**: Filtered P&L is $585.57 HIGHER, not lower - we're avoiding losses, not limiting upside

## Next Steps

1. ✅ Complete analysis and identify blacklist
2. 🔄 Create configuration file
3. ⏳ Implement filter in sighook
4. ⏳ Test locally
5. ⏳ Deploy to production
6. ⏳ Monitor for 7 days
7. ⏳ Analyze results and adjust

## Appendix: Analysis Queries

```sql
-- Worst performers (< 20% WR)
SELECT
    symbol,
    COUNT(*) as total_exits,
    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate,
    ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl
FROM trade_records
WHERE
    order_time > NOW() - INTERVAL '30 days'
    AND side = 'sell'
    AND pnl_usd IS NOT NULL
GROUP BY symbol
HAVING COUNT(*) >= 5
    AND ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) < 20
ORDER BY win_rate ASC, total_pnl ASC;
```
