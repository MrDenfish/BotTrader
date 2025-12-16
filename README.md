# BotTrader

Cryptocurrency trading bot for Coinbase Advanced Trade with automated market making, FIFO allocation tracking, and dynamic symbol filtering.

## Features

- **Automated Trading** - Webhook-driven order execution via Coinbase websockets
- **Passive Market Making** - Spread-based market making with break-even exits and volatility checks
- **Dynamic Symbol Filtering** - Data-driven symbol exclusion based on rolling performance metrics
- **FIFO Accounting** - Accurate P&L tracking with FIFO allocation engine
- **Email Reports** - Automated daily performance reports via AWS SES
- **Position Monitoring** - Real-time position tracking with protective stop-losses
- **Strategy Tracking** - Snapshot-based strategy versioning for performance analysis

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 14+
- Coinbase Advanced Trade API credentials

### Local Development

```bash
# 1. Clone repository
git clone <repo-url>
cd BotTrader

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys and configuration

# 4. Start database (Docker)
docker compose up -d db

# 5. Run locally
python main.py
```

### Production Deployment (AWS)

```bash
# Deploy latest code to AWS server
ssh bottrader-aws "cd /opt/bot && ./update.sh"

# Check container status
ssh bottrader-aws "docker ps"

# View logs
ssh bottrader-aws "docker logs -f webhook"
ssh bottrader-aws "docker logs -f sighook"

# Verify dynamic filter initialized
ssh bottrader-aws "docker logs webhook 2>&1 | grep 'Dynamic.*Filter'"
```

## Architecture

### Core Components

- **`main.py`** - Local development entry point
- **`webhook/`** - Coinbase websocket listener & order execution engine
- **`sighook/`** - Trading signal generation & strategy management
- **`botreport/`** - Daily email reporting service
- **`MarketDataManager/`** - Market data aggregation & passive order management
- **`Shared_Utils/`** - Shared utilities including dynamic symbol filter
- **`SharedDataManager/`** - Database connection & trade recording
- **`Config/`** - Configuration management
- **`TableModels/`** - SQLAlchemy ORM models
- **`database/`** - PostgreSQL schema, migrations, and utilities

### Supporting Directories

- **`scripts/`** - Diagnostic tools, analytics, and utilities
  - `diagnostics/` - Log analysis and verification tools
  - `analytics/` - Weekly strategy reviews
  - `utils/` - General utilities
  - `deployment/` - Deployment scripts
  - `migrations/` - Database migrations

- **`tests/`** - Unit and integration tests
- **`docs/`** - Comprehensive documentation
  - `active/` - Current operational documentation
  - `archive/` - Historical documentation
  - `planning/` - Future work and roadmaps
  - `analysis/` - Performance reports
  - `reminders/` - Scheduled maintenance tasks

- **`data/`** - Data files and archives
  - `archive/` - Historical data snapshots
  - `sample_reports/` - Sample email reports

## Configuration

### Environment Variables

Key configuration in `.env`:

```bash
# Coinbase API
CB_API_KEY=your_api_key
CB_API_SECRET=your_secret

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/bot_trader_db

# Email Reports
EMAIL_TO=your@email.com
AWS_REGION=us-west-2

# Dynamic Symbol Filter
DYNAMIC_FILTER_ENABLED=true
DYNAMIC_FILTER_MIN_WIN_RATE=0.30
DYNAMIC_FILTER_MAX_SPREAD_PCT=0.02

# Passive Market Making
PASSIVE_IGNORE_FEES_FOR_SPREAD=true
EXCLUDED_SYMBOLS=A8-USD,PENGU-USD,TNSR-USD
```

See `.env.dynamic_filter_example` for complete configuration options.

## Documentation

### Quick Links

**For Developers:**
- [Architecture Deep Dive](docs/active/architecture/ARCHITECTURE_DEEP_DIVE.md)
- [FIFO Allocations Design](docs/active/architecture/FIFO_ALLOCATIONS_DESIGN.md)
- [Database Access Guide](docs/active/deployment/DATABASE_ACCESS_GUIDE.md)

**For Operations:**
- [AWS Deployment Checklist](docs/active/deployment/AWS_DEPLOYMENT_CHECKLIST.md)
- [Logging Guide](docs/active/guides/LOGGING_PHASE1_GUIDE.md)
- [AWS Troubleshooting](docs/active/deployment/AWS_POSTGRES_TROUBLESHOOTING.md)

**For Features:**
- [Dynamic Symbol Filter](docs/active/features/DYNAMIC_FILTER_DOCUMENTATION.md) - Data-driven symbol filtering
- [Passive Market Making](docs/archive/sessions/PASSIVE_MM_FIXES_SESSION.md) - Break-even exits and volatility checks
- [Session Summary](docs/archive/sessions/SESSION_SUMMARY_DEC15_2025.md) - Latest improvements (Dec 15, 2025)

**Documentation Index:**
- [Full Documentation Index](docs/README.md)

## Testing

```bash
# Run all tests
pytest tests/

# Run specific test
pytest tests/test_fifo_engine.py

# Run with coverage
pytest --cov=. tests/

# Generate coverage report
pytest --cov=. --cov-report=html tests/
open htmlcov/index.html
```

## Scripts

### Diagnostics

```bash
# Analyze logs
python scripts/diagnostics/analyze_logs.py

# Verify email report accuracy
python scripts/diagnostics/verify_email_report.py

# Performance analysis
python scripts/diagnostics/diagnostic_performance_analysis.py
```

### Analytics

```bash
# Weekly strategy review (runs automatically on server)
bash scripts/analytics/weekly_strategy_review.sh
```

See [scripts/README.md](scripts/README.md) for complete script documentation.

## Docker Services

### Development (docker-compose.yml)

```bash
docker compose up -d     # Start all services
docker compose down      # Stop all services
docker compose logs -f   # View logs
```

### Production (docker-compose.aws.yml)

```bash
# On AWS server
cd /opt/bot
docker compose -f docker-compose.aws.yml up -d

# Services:
# - db: PostgreSQL database
# - webhook: Websocket listener & order execution
# - sighook: Signal generation & strategy
# - report-job: Daily email reports
# - leaderboard-job: Leaderboard updates
```

## Key Improvements (December 2025)

### WebSocket Stability
- Increased ping timeout from 20s to 60s (tolerates network latency)
- Reduced unnecessary reconnections

### PassiveOrderManager Enhancements
- **Break-even exits** - Prevents fee-only losses from flat price movement
- **Time-based exits** - Forces exit after max lifetime (prevents indefinite holding)
- **Pre-entry volatility checks** - Validates recent price movement before entry
- **Fee-aware spread validation** - Accounts for maker/taker fees in spread calculations

### Dynamic Symbol Filtering
- **Automatic exclusion** based on performance metrics (win rate, avg P&L, total P&L, spread)
- **Automatic re-inclusion** when performance improves
- **Data-driven decisions** - No more manual blacklist maintenance
- **1-hour cache** - Efficient with minimal overhead

## Monitoring

### Daily Reports

Automated email reports sent at 02:05, 08:05, 14:05, 20:05 PT with:
- Current positions and P&L
- Daily/weekly/monthly performance
- Risk metrics (drawdown, capital allocation)
- Top performers and losers

### Weekly Strategy Reviews

Automated weekly analysis (Mondays 9am PT) includes:
- Symbol performance ranking
- Signal quality metrics
- Time-of-day profitability
- Market condition correlation

### Logs

```bash
# Real-time logs
ssh bottrader-aws "docker logs -f webhook"
ssh bottrader-aws "docker logs -f sighook"

# Search for errors
ssh bottrader-aws "docker logs webhook 2>&1 | grep -i error | tail -50"

# Check dynamic filter activity
ssh bottrader-aws "docker logs webhook 2>&1 | grep 'dynamic.*filter' | tail -20"
```

## Database

### Connect to Database

```bash
# From AWS server
ssh bottrader-aws "docker exec -it db psql -U bot_user -d bot_trader_db"

# Common queries
SELECT * FROM trade_records ORDER BY order_time DESC LIMIT 10;
SELECT * FROM fifo_allocations WHERE allocation_version = 2 ORDER BY created_at DESC LIMIT 10;
SELECT symbol, COUNT(*), SUM(pnl_usd) FROM fifo_allocations WHERE allocation_version = 2 GROUP BY symbol;
```

### Schema

Key tables:
- `trade_records` - All trades from Coinbase
- `fifo_allocations` - FIFO-based P&L calculations
- `ohlcv_data` - Historical price data
- `strategy_snapshots` - Strategy version tracking
- `cash_transactions` - Deposit/withdrawal tracking (pending implementation)

## Troubleshooting

### Common Issues

**Webhook container unhealthy:**
- Known issue: Coinbase USER subscription fails occasionally
- Impact: Minimal - health check fails but trading functionality works
- Fix: Container automatically reconnects

**Dynamic filter not excluding symbols:**
- Check: `DYNAMIC_FILTER_ENABLED=true` in .env
- Verify: Minimum trades threshold met (default 5)
- Logs: Search for "Dynamic Symbol Filter initialized"

**Passive orders not placing:**
- Check: Symbol not in excluded list
- Verify: Spread meets minimum requirements
- Check: Recent volatility sufficient (5min OHLCV check)

See [docs/active/deployment/AWS_POSTGRES_TROUBLESHOOTING.md](docs/active/deployment/AWS_POSTGRES_TROUBLESHOOTING.md) for detailed troubleshooting.

## Project Status

**Current Version:** 6.2.0 (December 2025)
**Production Status:** ✅ Operational on AWS EC2
**Latest Deployment:** December 15, 2025 (commit 24f4526)

### Recent Enhancements

- Dynamic symbol filtering system (Dec 15, 2025)
- PassiveOrderManager improvements (Dec 15, 2025)
- WebSocket stability fix (Dec 15, 2025)
- FIFO single engine deployment (Dec 4, 2025)
- Documentation reorganization (Dec 15, 2025)

### Next Steps

See [docs/planning/](docs/planning/) for planned improvements:
- Cash transactions integration (high priority)
- Schema cleanup (scheduled Dec 29, 2025)
- Strategy optimization evaluation (Jan 7, 2025)
- TPSL coordination enhancements

## License

Private project - All rights reserved

## Contact

For issues or questions, see [docs/README.md](docs/README.md) for documentation or review session summaries.

---

**Last Updated:** December 15, 2025
**Maintainer:** BotTrader Team
**Repository:** Private
