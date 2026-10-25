#!/usr/bin/env bash
# If the public front door stops answering, restart web + api (not redis/data).
# Installed on the server by deploy/setup_host_stability.sh (cron every 5 min).
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
health_url="${ATLAS_WATCHDOG_URL:-http://127.0.0.1/api/ready}"

if curl_err="$(curl -fsS --max-time 15 -o /dev/null "$health_url" 2>&1)"; then
  exit 0
fi

logger -t atlas-watchdog "health check failed ($health_url): ${curl_err}; restarting web and api"
cd "$repo_dir"
docker compose restart web api
