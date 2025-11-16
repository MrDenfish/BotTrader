#!/usr/bin/env bash
set -euo pipefail

# set a safe PATH for cron
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

cd /opt/bot
/usr/bin/docker compose \
  --env-file /opt/bot/.env \
  -f /opt/bot/docker-compose.aws.yml \
  run --rm report-job >> /opt/bot/logs/report-cron.log 2>&1
