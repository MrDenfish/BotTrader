#!/bin/bash
# Scheduled Docker cleanup to prevent disk exhaustion on t3.medium (20GB EBS).
# Add to crontab: 0 4 * * * /opt/bot/docker/disk-cleanup.sh >> /opt/bot/logs/disk-cleanup.log 2>&1

set -euo pipefail

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') Starting Docker disk cleanup"

# Remove stopped containers, unused networks, dangling images older than 72h
docker system prune -f --filter "until=72h"

# Remove ALL unused images (not just dangling) older than 7 days
docker image prune -af --filter "until=168h"

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') Disk cleanup complete"
df -h / | tail -1
