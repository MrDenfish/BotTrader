BotTrader Docker

This folder contains the Dockerfile and entrypoint for v2 trading containers.

v1 Docker artifacts (Dockerfile.bot, entrypoint.bot.sh, ssm-env.sh) have been
archived to archive/v1/docker/.

Directory layout

docker/
├─ entrypoint/
│  └─ entrypoint.v2.sh         # Starts v2 bot (V2_CONFIG selects YAML config)
├─ Dockerfile.v2                # v2 trading bot image
├─ disk-cleanup.sh              # Docker disk cleanup (runs daily via cron)
├─ deploy_aws_ssh.sh            # SSH deployment helper
├─ sync-env-to-ssm.sh           # Sync .env to AWS SSM
├─ update.sh                    # Quick update helper
└─ README.md                    # (this file)

Build

From repo root:
docker build -f docker/Dockerfile.v2 -t bottrader-v2:prod .

Compose (production)

See docker-compose.aws.yml at the repo root. Active services:
- db — PostgreSQL (shared)
- v2-paper — Coinbase paper trading
- v2-kraken — Kraken paper trading

Daily reporting is handled by the daily_report_v2 observer plugin
inside each v2 container (no separate report container needed).

v2 entrypoint

docker/entrypoint/entrypoint.v2.sh:
- Loads .env if present
- Waits for Postgres to be reachable
- Starts v2 with config from V2_CONFIG env var (defaults to v2/paper_trading.yaml)

Deployment

IMPORTANT: v2 containers bake code into the image via COPY . /app.
Always use --build when deploying changes:

  docker compose -f docker-compose.aws.yml up -d --build v2-paper v2-kraken

A simple restart will NOT pick up code changes.
