#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] BotTrader starting (Option A)..."
required=(DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD)
for v in "${required[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    echo "[entrypoint] ERROR: $v is required but not set."; exit 1
  fi
done

echo "[entrypoint] Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."
for i in {1..60}; do
  (</dev/tcp/${DB_HOST}/${DB_PORT}) >/dev/null 2>&1 && break
  sleep 1
  if [[ $i -eq 60 ]]; then echo "[entrypoint] Postgres not reachable"; exit 1; fi
done
echo "[entrypoint] Postgres is reachable."

mode="${BOT_RUN_MODE:-both}"
case "$mode" in
  webhook) exec python -m main --run webhook ;;
  sighook) exec python -m main --run sighook ;;
  both|*)  exec python -m main --run both ;;
esac

