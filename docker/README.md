BotTrader Docker

This folder contains Dockerfiles and bootstrap/entrypoint scripts for:

Bot runtime (Dockerfile.bot) — runs the trading bot

Daily report (Dockerfile.report) — runs the reporting job

SSM bootstrap (docker/bootstrap/ssm-env.sh) — loads config from AWS SSM

Bot entrypoint (docker/entrypoint/entrypoint.bot.sh) — sources SSM, sanity‑prints config, launches bot

Directory layout

docker/
├─ bootstrap/
│  └─ ssm-env.sh                # Loads /bottrader/<env> params into env vars (DB_*, etc.)
├─ entrypoint/
│  └─ entrypoint.bot.sh         # Sources ssm-env, prints config, starts bot
├─ README.md                    # (this file)
Dockerfile.bot                  # Trading bot image
Dockerfile.report               # Reporting image

Prerequisites

AWS IAM role or credentials with:

ssm:GetParametersByPath (with decryption) for the chosen path

kms:Decrypt for the key used by SecureString (default alias/aws/ssm works)

SSM parameters populated under /bottrader/prod (or /bottrader/dev)

RDS Postgres reachable from your EC2/host; inbound 5432 allowed from your instance’s security group

SSM parameter hierarchy

Under /bottrader/<env> (e.g., /bottrader/prod):

/db/* → exported as DB_<KEY>

HOST (String) — xxx.rds.amazonaws.com

PORT (String) — 5432

TYPE (String) — postgresql

NAME (String) — bot_trader_db

USER (String) — tradebot_user

PASSWORD (SecureString)

SSLMODE (String) — require for RDS

Optional: ECHO_SQL, MAX_OVERFLOW, MONITOR_INTERVAL, CONNECTION_THRESHOLD

/app/* → exported as <KEY> (uppercased)

/alert/* → exported as <KEY> (uppercased)

/docker/db/* → exported as DOCKER_DB_<KEY> (if you use that branch)

Keep key names UPPERCASE and avoid duplicates (no lowercase name/user/password/host).

Build

From repo root:
# Bot image
docker build -f Dockerfile.bot -t bottrader:prod .

# Reporting image
docker build -f Dockerfile.report -t bottrader-report:prod .

Run (one‑off)
Verify SSM export only (no app)
docker run --rm \
  -e AWS_REGION=us-west-2 \
  -e SSM_ROOT=/bottrader/prod \
  --entrypoint /bin/sh bottrader:prod -lc \
  '. /usr/local/bin/ssm-env; env | grep -E "^DB_(HOST|PORT|USER|NAME|SSLMODE)"'

Run the bot
docker run -d --name bot \
  -e AWS_REGION=us-west-2 \
  -e SSM_ROOT=/bottrader/prod \
  bottrader:prod

docker logs -f bot
# Early logs show a sanitized DB snapshot from entrypoint.bot.sh

Run the reporting job (on demand)
docker run --rm \
  -e AWS_REGION=us-west-2 \
  -e SSM_ROOT=/bottrader/prod \
  bottrader-report:prod

Compose (example)
services:
  bot:
    image: bottrader:prod
    environment:
      AWS_REGION: us-west-2
      SSM_ROOT: /bottrader/prod
    restart: unless-stopped

  report:
    image: bottrader-report:prod
    environment:
      AWS_REGION: us-west-2
      SSM_ROOT: /bottrader/prod
    # schedule externally (cron, ECS scheduled task, etc.)

How config is loaded

docker/bootstrap/ssm-env.sh:

Recursively fetches SSM params under $SSM_ROOT

Exports:

/db/<KEY> → DB_<KEY>

/docker/db/<KEY> → DOCKER_DB_<KEY>

/app/<KEY> and /alert/<KEY> → <KEY> (uppercased)

Safe to source multiple times; uses aws + jq

docker/entrypoint/entrypoint.bot.sh:

Asserts AWS_REGION and SSM_ROOT

Sources ssm-env

Sets ALLOW_LOCAL_DOTENV=false to prevent .env* overrides

Prints a sanitized DB config snapshot

Starts the app (python -m main --run both)

SSL (RDS)

Images download the AWS RDS trust bundle to /etc/ssl/certs/rds-global-bundle.pem.
Your app should create the SQLAlchemy asyncpg engine with an SSL context when DB_SSLMODE=require.

Example (inside the app):
python

import os, ssl
from sqlalchemy.ext.asyncio import create_async_engine

def create_engine_from_env(url: str):
    connect_args = {}
    if os.getenv("DB_SSLMODE", "").lower() in ("require","verify-ca","verify-full"):
        connect_args = {"ssl": ssl.create_default_context()}
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)

Troubleshooting

“no pg_hba.conf entry … no encryption”

Ensure /bottrader/prod/db/SSLMODE=require

Confirm engine passes an SSLContext (connect_args={"ssl": ssl.create_default_context()})

Password auth failed

Verify only one password param exists (/prod/db/PASSWORD) and it’s correct (SecureString)

Check startup log for the effective DB_* (entrypoint prints host/user/name/sslmode)

Timeout / could not connect

RDS SG must allow inbound 5432 from the EC2 instance SG

From inside container: nc -vz <DB_HOST> 5432 (install netcat-openbsd if needed)

Env not loading

Container must have AWS_REGION + SSM_ROOT

IAM role must allow SSM + KMS decrypt for the path/key

Inside container: . /usr/local/bin/ssm-env; env | grep ^DB_

Security notes

Store secrets as SecureString in SSM.

Rotate DB passwords after accidental plaintext exposure.

Avoid copying .env* into the image (Dockerfile.bot removes them).

Use least‑privilege IAM for reading/writing SSM paths.

FAQ

Q: Where do I set prod vs dev?
Set SSM_ROOT to /bottrader/prod or /bottrader/dev at runtime.

Q: Do I need to edit entrypoint paths after moving scripts?
No—Dockerfiles copy them to /usr/local/bin/ssm-env and /usr/local/bin/entrypoint-bot. The entrypoint uses those fixed paths.

Q: Can I test without starting the bot?
Yes. Override entrypoint or run /bin/sh -lc '. /usr/local/bin/ssm-env; env | grep ^DB_'.