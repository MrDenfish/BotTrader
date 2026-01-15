# BotTrader Backtesting Framework

Simulate trading strategy execution using historical data to validate performance before deployment.

## Overview

The backtesting framework replays historical OHLCV data and simulates your trading strategy's buy/sell decisions, tracking all positions, exits, P&L, and performance metrics.

## Quick Start

### Basic Usage

```bash
# Run 30-day backtest with current production settings
python3 run_backtest.py --days 30

# Run specific date range
python3 run_backtest.py --start 2025-12-01 --end 2026-01-12

# Test different configurations
python3 run_backtest.py --days 60 --config aggressive

# Export results to CSV
python3 run_backtest.py --days 30 --export results.csv
```

### Available Configurations

- `production` - Current production parameters (default)
- `aggressive` - Tighter TP/SL (4.0%/4.0%)
- `conservative` - Wider TP/SL (3.0%/5.0%)
- `roc_sensitive` - Lower ROC thresholds, more trades
- `roc_strict` - Higher ROC thresholds, fewer trades

## Strategy Parameters Tested

### Current Production Config
```
ROC Buy Threshold: 5.0%
ROC Sell Threshold: -2.0%
Take Profit: 3.5%
Stop Loss: 4.5%
ROC Peak Drop Exit: 30%
Order Size (ROC): $25
Order Size (Signal): $15
```

## Framework Components

### 1. Configuration (`config.py`)
- `StrategyConfig` - Trading strategy parameters
- `BacktestConfig` - Backtest execution settings
- Preset configurations for testing variations

### 2. Data Models (`models.py`)
- `Position` - Open trading position with peak tracking
- `Trade` - Completed trade record with P&L
- `BacktestResults` - Aggregate performance metrics

### 3. Engine (`engine.py`)
- `BacktestEngine` - Core backtesting simulation
- Replays historical data chronologically
- Simulates entry/exit decisions
- Tracks positions and capital

### 4. Reporter (`reporter.py`)
- `BacktestReporter` - Results formatting and display
- Print summary statistics
- Export trades to CSV
- Compare multiple configurations

## Output Metrics

### Performance Metrics
- Total P&L
- Total Return %
- Total Fees
- Final Capital

### Trade Statistics
- Total Trades
- Win Rate %
- Profit Factor
- Average Win/Loss
- Winning vs Losing Trades

### Risk Metrics
- Maximum Drawdown ($)
- Maximum Drawdown (%)

### Trade Breakdown
- ROC Momentum Trades
- Standard Signal Trades
- Exit Reasons (TP, SL, ROC Peak, Reversal)

## Example Output

```
══════════════════════════════════════════════════════════════════════
  BACKTEST RESULTS SUMMARY
══════════════════════════════════════════════════════════════════════

📊 Overview:
──────────────────────────────────────────────────────────────────────
  Strategy: BotTrader Strategy
  Period: 2025-12-13 to 2026-01-12
  Initial Capital: $10,000.00
  Final Capital: $10,523.45

💰 Performance:
──────────────────────────────────────────────────────────────────────
  Total P&L: +$523.45
  Total Return: +5.23%
  Total Fees: $125.50

📈 Trade Statistics:
──────────────────────────────────────────────────────────────────────
  Total Trades: 156
  Win Rate: 58.3%
  Profit Factor: 1.45
  Average Win: $12.50
  Average Loss: $8.75

⚠️  Risk Metrics:
──────────────────────────────────────────────────────────────────────
  Max Drawdown: $235.00
  Max Drawdown %: 2.35%

🔍 Trade Breakdown:
──────────────────────────────────────────────────────────────────────
  ROC Momentum Trades: 89
  Standard Signal Trades: 67

  Take Profit Exits: 45
  Stop Loss Exits: 62
  ROC Peak/Reversal Exits: 49
```

## Prerequisites

- Python 3.10+
- PostgreSQL database access (SSH tunnel on port 5433)
- Required packages:
  ```bash
  pip install sqlalchemy pandas psycopg2-binary
  ```

## Database Requirements

The backtest requires access to your PostgreSQL database containing:
- `ohlcv_data` table with historical price data
- Columns: `time`, `symbol`, `open`, `high`, `low`, `close`, `volume`

Connection is via SSH tunnel:
```bash
ssh -L 5433:localhost:5432 bottrader-aws -N
```

## Advanced Usage

### Compare Multiple Configurations

```python
from backtest.config import *
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter

# Run multiple configurations
configs = [
    ('Production', CURRENT_PRODUCTION),
    ('Aggressive', AGGRESSIVE_TP_SL),
    ('Conservative', CONSERVATIVE_TP_SL),
]

results_list = []
for name, strategy in configs:
    engine = BacktestEngine(strategy, backtest_config, DB_URL)
    results = engine.run()
    results.strategy_name = name
    results_list.append(results)

# Compare results
BacktestReporter.compare_strategies(results_list)
```

### Custom Configuration

```python
from backtest.config import StrategyConfig

custom_strategy = StrategyConfig(
    roc_buy_threshold=Decimal("6.0"),
    roc_sell_threshold=Decimal("-2.5"),
    take_profit_pct=Decimal("0.040"),
    stop_loss_pct=Decimal("0.045"),
    roc_peak_drop_pct=Decimal("0.25"),
    order_size_roc=Decimal("30.00"),
)

# Run with custom config
engine = BacktestEngine(custom_strategy, backtest_config, DB_URL)
results = engine.run()
```

## Limitations

### Current Implementation
1. **Simplified ROC Calculation**: Uses price change as proxy for ROC
   - Full implementation would use historical lookback window
   - Acceptable for initial testing, refine for production

2. **No Composite Scoring**: Currently only tests ROC momentum trades
   - Future: Add RSI, MACD, Bollinger Bands scoring
   - Focus on ROC validates recent optimization

3. **Fixed Slippage**: Uses constant 0.1% slippage
   - Real trading may have variable slippage
   - Conservative estimate for most scenarios

4. **No Market Hours**: Trades 24/7
   - Crypto markets don't close
   - Not an issue for this use case

## Roadmap

### Phase 2 Enhancements
- [ ] Add composite indicator scoring
- [ ] Implement proper ROC calculation with historical window
- [ ] Add symbol-specific backtesting
- [ ] Performance visualization (equity curve charts)
- [ ] Monte Carlo simulation
- [ ] Walk-forward optimization

### Phase 3 Features
- [ ] Multi-timeframe analysis
- [ ] Parameter optimization grid search
- [ ] Genetic algorithm for parameter tuning
- [ ] Machine learning signal integration

## Troubleshooting

### SSH Tunnel Not Connected
```bash
# Start SSH tunnel
ssh -L 5433:localhost:5432 bottrader-aws -N &
```

### Database Permission Errors
- Verify username/password in `run_backtest.py`
- Check that `bot_user` has SELECT permissions on `ohlcv_data`

### No Data Returned
- Check date range is within available data (Sep 2025 - Jan 2026)
- Verify SSH tunnel is active: `ps aux | grep 5433`

### Import Errors
- Ensure Python path includes project root
- Install missing dependencies: `pip install sqlalchemy pandas psycopg2-binary`

## Support

For questions or issues:
1. Check this README
2. Review backtest code in `backtest/` directory
3. Examine example runs in session documentation

---

**Last Updated**: January 12, 2026
**Version**: 1.0.0
