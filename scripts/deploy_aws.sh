#!/bin/bash
set -euo pipefail

# =============================================================================
# BotTrader AWS Deployment Script
# Runs locally, SSHes to EC2 (/opt/bot) to pull main and rebuild containers.
# Replaces the hand-typed sequence in CLAUDE.md / .claude/DEPLOYMENT.md.
# =============================================================================
# Usage:
#   ./scripts/deploy_aws.sh                    # Deploy v2-kraken (the default)
#   ./scripts/deploy_aws.sh dashboard          # Deploy dashboard only
#   ./scripts/deploy_aws.sh all                # Rebuild + restart every service
#   ./scripts/deploy_aws.sh --yes              # Skip the confirmation prompt
#   ./scripts/deploy_aws.sh --force            # Proceed despite open positions
#   ./scripts/deploy_aws.sh --no-push          # Skip 'git push origin main'
#   ./scripts/deploy_aws.sh --dry-run          # Run the guards, then stop
#
# Code is baked into the v2 images, so every deploy is 'up -d --build';
# 'docker compose restart' alone never picks up new code.
#
# Why there is no clock-window guard here (unlike StockAgent): the only
# scheduled job on the host is the BTC-200d regime watcher (host cron,
# 00:15 UTC) and it runs outside the containers, so a deploy cannot collide
# with it. The cost that matters is the RESTART itself: v2-kraken needs
# ~6.7 h of bars before composite_scoring can trade again, and any open
# position is carried through the restart on stored state. Guard C surfaces
# both before anything is touched.
# =============================================================================

# --- Configuration ---
SSH_HOST="${BOTTRADER_SSH_HOST:-bottrader-aws}"   # alias from ~/.ssh/config
REMOTE_DIR="${BOTTRADER_REMOTE_DIR:-/opt/bot}"
COMPOSE_FILE="docker-compose.aws.yml"
DEPLOY_LOG="${BOTTRADER_DEPLOY_LOG:-logs/deploys.log}"   # logs/ is gitignored
HEALTH_WAIT_SECONDS="${BOTTRADER_HEALTH_WAIT:-240}"

# --- Parse arguments ---
SERVICE="v2-kraken"
FORCE=false
ASSUME_YES=false
DO_PUSH=true
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --yes|-y) ASSUME_YES=true ;;
        --no-push) DO_PUSH=false ;;
        --dry-run) DRY_RUN=true ;;
        v2-kraken|dashboard|caddy|db|all) SERVICE="$arg" ;;
        -h|--help) grep -E '^# (Usage:|  \./scripts/deploy_aws\.sh)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown argument: $arg (see --help)"; exit 1 ;;
    esac
done

cd "$(git rev-parse --show-toplevel)"

echo "=== BotTrader AWS Deploy ==="
echo "Host:    $SSH_HOST:$REMOTE_DIR"
echo "Service: $SERVICE"
echo ""

# --- Step 0: Deploy guards ---
# These run for EVERY caller, not just Claude Code. Permission rules in
# .claude/settings*.json only gate the agent; a human typing the command is
# unaffected by them, so the guard that matters lives here.

LOCAL_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
LOCAL_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
LOCAL_SUBJ=$(git log -1 --pretty=%s 2>/dev/null || echo "")
DEPLOY_START=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DEPLOY_RESULT="incomplete"

mkdir -p "$(dirname "$DEPLOY_LOG")"
log_deploy_outcome() {
    rc=$?
    if [ "$DEPLOY_RESULT" = "incomplete" ]; then
        if [ $rc -eq 0 ]; then DEPLOY_RESULT="ok"; else DEPLOY_RESULT="failed(rc=$rc)"; fi
    fi
    printf '%s  sha=%-10s service=%-10s host=%-16s %s\n' \
        "$DEPLOY_START" "$LOCAL_SHA" "$SERVICE" "$SSH_HOST" "$DEPLOY_RESULT" \
        >> "$DEPLOY_LOG"
}
trap log_deploy_outcome EXIT

# Guard A: the remote pulls origin/main, so what we push must be main.
# Deploying from a feature branch would push a stale main and report the
# feature branch's SHA in the log - two different lies.
if [ "$LOCAL_BRANCH" != "main" ]; then
    DEPLOY_RESULT="blocked(not-on-main)"
    echo "BLOCKED: you are on branch '$LOCAL_BRANCH'. Production pulls origin/main,"
    echo "so deploy from main: merge your branch first, then re-run."
    exit 1
fi

# Guard B: confirm the target. Interactive only, so cron and CI never hang.
DIRTY_TREE=$(git status --porcelain 2>/dev/null | grep -cv '^??' || true)
if [ -t 0 ] && [ -t 1 ] && [ "$ASSUME_YES" != true ]; then
    echo "About to deploy:"
    echo "  host:    $SSH_HOST:$REMOTE_DIR"
    echo "  service: $SERVICE"
    echo "  commit:  $LOCAL_SHA  $LOCAL_SUBJ"
    if [ "$DIRTY_TREE" -gt 0 ]; then
        echo "  NOTE:    working tree has $DIRTY_TREE modified file(s) - uncommitted changes will NOT deploy"
    fi
    if [ "$SERVICE" = "v2-kraken" ] || [ "$SERVICE" = "all" ]; then
        echo "  NOTE:    restarting v2-kraken starts a ~6.7 h warmup blackout (no composite entries)"
    fi
    echo ""
    printf "Deploy to production? [y/N] "
    read -r CONFIRM
    case "$CONFIRM" in
        y|Y|yes|YES) ;;
        *) DEPLOY_RESULT="aborted(by-user)"; echo "Aborted."; exit 1 ;;
    esac
    echo ""
fi

# --- Step 1: SSH connectivity ---
echo "--- Step 1: Testing SSH ---"
ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" "echo 'SSH OK'" || {
    DEPLOY_RESULT="failed(ssh)"
    echo "ERROR: cannot connect to $SSH_HOST."
    echo "A timeout with the dashboard still answering on 443 usually means the"
    echo "security-group port-22 rule does not include your current IP"
    echo "(see memory: ec2_maintenance.md, 'SSH access / security group')."
    exit 1
}
echo ""

# Guard C: open positions on the paper book. A restart carries them through
# on stored state, but risk-manager state (PerformanceFilter window) is NOT
# restored yet (P3 open item), so the operator should know what is exposed.
if [ "$SERVICE" = "v2-kraken" ] || [ "$SERVICE" = "all" ] || [ "$SERVICE" = "db" ]; then
    echo "--- Step 2: Open-position check ---"
    OPEN_POS=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" \
        "docker exec db sh -c 'psql -U bot_user -d \"\$POSTGRES_DB\" -tAc \"SELECT count(*) FROM v2_positions WHERE qty > 1e-9\"'" \
        2>/dev/null | tr -d '[:space:]' || true)
    if [ -z "$OPEN_POS" ]; then
        echo "WARNING: could not read v2_positions (db container down or query failed)."
        OPEN_POS="unknown"
    fi
    if [ "$OPEN_POS" = "0" ]; then
        echo "No open positions. Safe to restart."
    else
        echo "WARNING: $OPEN_POS open position(s) on the paper book."
        if [ "$FORCE" = true ]; then
            echo "Proceeding anyway (--force)."
        elif [ -t 0 ] && [ -t 1 ] && [ "$ASSUME_YES" != true ]; then
            printf "Restart with open positions? [y/N] "
            read -r CONFIRM2
            case "$CONFIRM2" in
                y|Y|yes|YES) ;;
                *) DEPLOY_RESULT="aborted(open-positions)"; echo "Aborted."; exit 1 ;;
            esac
        else
            DEPLOY_RESULT="blocked(open-positions)"
            echo "BLOCKED: non-interactive run with open positions. Re-run with --force."
            exit 1
        fi
    fi
    echo ""
fi

if [ "$DRY_RUN" = true ]; then
    DEPLOY_RESULT="dry-run"
    echo "--- Dry run: guards passed, stopping before push/deploy ---"
    exit 0
fi

# --- Step 3: Push latest code ---
if [ "$DO_PUSH" = true ]; then
    echo "--- Step 3: Pushing main to origin ---"
    git push origin main
    echo ""
else
    echo "--- Step 3: Push skipped (--no-push) ---"
    echo ""
fi

# --- Step 4: Remote deploy ---
echo "--- Step 4: Deploying on remote ---"
ssh -o BatchMode=yes "$SSH_HOST" \
    "REMOTE_DIR='$REMOTE_DIR' COMPOSE_FILE='$COMPOSE_FILE' SERVICE='$SERVICE' HEALTH_WAIT='$HEALTH_WAIT_SECONDS' bash -s" << 'REMOTE'
set -euo pipefail
cd "$REMOTE_DIR"

echo "Pulling latest code..."
git fetch origin
git checkout -q main
git pull --ff-only origin main || {
    echo "ERROR: fast-forward failed - local commits on the server?"
    echo "SSH in and inspect: git -C $REMOTE_DIR status; git -C $REMOTE_DIR log --oneline -5"
    exit 1
}
REMOTE_SHA=$(git rev-parse --short HEAD)
echo "Remote HEAD: $REMOTE_SHA  $(git log -1 --pretty=%s)"
DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
if [ "$DIRTY" -gt 0 ]; then
    echo "NOTE: remote working tree has $DIRTY uncommitted path(s) (host-only files, deleted backtest data) - expected."
fi
echo ""

if [ "$SERVICE" = "all" ]; then
    echo "Building all images..."
    docker compose -f "$COMPOSE_FILE" build
    echo "Restarting all services..."
    docker compose -f "$COMPOSE_FILE" up -d
else
    echo "Rebuilding and restarting $SERVICE..."
    docker compose -f "$COMPOSE_FILE" up -d --build "$SERVICE"
fi
echo ""

# Post-deploy: wait for v2-kraken's heartbeat healthcheck (start_period 120s).
if [ "$SERVICE" = "v2-kraken" ] || [ "$SERVICE" = "all" ]; then
    echo "Waiting up to ${HEALTH_WAIT}s for v2-kraken to report healthy..."
    WAITED=0
    STATUS="unknown"
    while [ "$WAITED" -lt "$HEALTH_WAIT" ]; do
        STATUS=$(docker inspect --format '{{.State.Health.Status}}' v2-kraken 2>/dev/null || echo "missing")
        if [ "$STATUS" = "healthy" ]; then break; fi
        if [ "$STATUS" = "missing" ] || [ "$STATUS" = "unhealthy" ]; then break; fi
        sleep 15
        WAITED=$((WAITED + 15))
    done
    echo "v2-kraken health: $STATUS (after ${WAITED}s)"
    if [ "$STATUS" != "healthy" ]; then
        echo "WARNING: v2-kraken is not healthy yet. Check: docker logs v2-kraken --tail 100"
        echo "(The heartbeat healthcheck can lag the 120s start_period; 'starting' is not an error.)"
    fi
    echo ""
fi

echo "Running containers:"
docker compose -f "$COMPOSE_FILE" ps
echo ""
echo "Recent $SERVICE logs:"
if [ "$SERVICE" = "all" ]; then
    docker compose -f "$COMPOSE_FILE" logs --tail=10 v2-kraken 2>/dev/null || true
else
    docker compose -f "$COMPOSE_FILE" logs --tail=20 "$SERVICE" 2>/dev/null || true
fi
REMOTE

echo ""
echo "=== Deploy Complete ==="
echo "Verify: ssh $SSH_HOST 'cd $REMOTE_DIR && git log --oneline -3'"
