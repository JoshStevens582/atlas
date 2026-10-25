#!/usr/bin/env bash
# Warn when durable logs on atlas-data exceed a byte threshold. See docs/ops_alerts.md.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

env_file="${ATLAS_OPS_ENV:-/etc/atlas-ops.env}"
if [ -f "$env_file" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

threshold="${LOG_DISK_ALERT_BYTES:-52428800}"
log_dir="${LOG_DISK_PATH:-/app/data/logs}"

api_container="$(docker compose ps -aq api)"
if [ -z "$api_container" ]; then
  echo "api container not found" >&2
  exit 1
fi

size_bytes="$(
  docker compose exec -T api sh -c "du -sb '${log_dir}' 2>/dev/null | cut -f1" | tr -d '\r\n'
)"
if [ -z "$size_bytes" ] || ! [[ "$size_bytes" =~ ^[0-9]+$ ]]; then
  echo "Could not measure log directory size under ${log_dir}" >&2
  exit 1
fi

echo "Log directory ${log_dir}: ${size_bytes} bytes (threshold ${threshold})"

if [ "$size_bytes" -lt "$threshold" ]; then
  exit 0
fi

mb=$((size_bytes / 1048576))
bash "$repo_dir/deploy/ops_alert.sh" \
  "Atlas log disk high on $(hostname -s 2>/dev/null || hostname)" \
  "Durable logs under ${log_dir} use about ${mb} MiB (threshold $((threshold / 1048576)) MiB).
Check rotation (LOG_FILE_MAX_BYTES / LOG_FILE_BACKUP_COUNT) and atlas-data volume free space.
See docs/logging.md and docs/ops_alerts.md."
