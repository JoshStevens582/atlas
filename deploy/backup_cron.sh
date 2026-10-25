#!/usr/bin/env bash
# Wrapper for /etc/cron.d/atlas-backup: log output and email on failure.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_file="${ATLAS_BACKUP_LOG:-/var/log/atlas-backup.log}"

if "$repo_dir/deploy/backup.sh" >>"$log_file" 2>&1; then
  exit 0
fi

status=$?
tail_text="$(tail -n 40 "$log_file" 2>/dev/null || true)"
"$repo_dir/deploy/ops_alert.sh" \
  "Atlas backup failed on $(hostname -s 2>/dev/null || hostname)" \
  "Exit code ${status}. Last lines from ${log_file}:

${tail_text}"
exit "$status"
