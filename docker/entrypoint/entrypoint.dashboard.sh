#!/bin/bash
# ------------------------------------------------------------------------------
# entrypoint.dashboard.sh
# ------------------------------------------------------------------------------
# Starts the BotTrader Streamlit dashboard.
# Runtime configuration (port, address, headless mode) is injected via
# environment variables in docker-compose.aws.yml — no flags needed here.
# ------------------------------------------------------------------------------
set -e

export PYTHONPATH="/app:${PYTHONPATH:-}"

exec streamlit run v2/dashboard/app.py
