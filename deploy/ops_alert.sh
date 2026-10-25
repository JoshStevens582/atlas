#!/usr/bin/env bash
# Email (or syslog) when backups fail or log disk is high. See docs/ops_alerts.md.
set -euo pipefail

subject="${1:-Atlas ops alert}"
body="${2:-No details provided.}"

env_file="${ATLAS_OPS_ENV:-/etc/atlas-ops.env}"
if [ -f "$env_file" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

if [ "${OPS_ALERT_DRY_RUN:-}" = "1" ]; then
  echo "ops_alert dry run: to=${OPS_ALERT_TO:-} subject=$subject"
  exit 0
fi

to="${OPS_ALERT_TO:-}"
from="${OPS_ALERT_FROM:-}"
key="${RESEND_API_KEY:-}"

if [ -z "$to" ]; then
  logger -t atlas-ops-alert "OPS_ALERT_TO unset; subject=$subject"
  exit 0
fi

if [ -z "$key" ] || [ -z "$from" ]; then
  logger -t atlas-ops-alert "RESEND_API_KEY or OPS_ALERT_FROM unset; subject=$subject"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  logger -t atlas-ops-alert "python3 missing; cannot send alert email"
  exit 0
fi

payload="$(
  OPS_ALERT_FROM="$from" OPS_ALERT_TO="$to" OPS_ALERT_SUBJECT="$subject" OPS_ALERT_BODY="$body" python3 <<'PY'
import json
import os

print(
    json.dumps(
        {
            "from": os.environ["OPS_ALERT_FROM"],
            "to": [os.environ["OPS_ALERT_TO"]],
            "subject": os.environ["OPS_ALERT_SUBJECT"],
            "text": os.environ["OPS_ALERT_BODY"],
        }
    )
)
PY
)"

curl -fsS --max-time 30 -X POST https://api.resend.com/emails \
  -H "Authorization: Bearer $key" \
  -H "Content-Type: application/json" \
  -d "$payload" >/dev/null

logger -t atlas-ops-alert "sent alert to $to: $subject"
