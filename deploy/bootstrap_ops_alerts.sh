#!/usr/bin/env bash
# One-time: build /etc/atlas-ops.env from /opt/atlas/.env (Resend) and send a test mail.
# Run on the server as root: bash deploy/bootstrap_ops_alerts.sh
set -eu

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
atlas_env="${ATLAS_ENV_FILE:-/opt/atlas/.env}"
ops_env="${ATLAS_OPS_ENV:-/etc/atlas-ops.env}"

if [ ! -f "$atlas_env" ]; then
  echo "Missing $atlas_env" >&2
  exit 1
fi

to="$(
  python3 - "$atlas_env" "$ops_env" <<'PY'
import sys
from pathlib import Path

src, dest = Path(sys.argv[1]), Path(sys.argv[2])
values: dict[str, str] = {}
for line in src.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, val = line.partition("=")
    values[key.strip()] = val.strip().strip('"').strip("'")

key = values.get("SMTP_PASSWORD") or values.get("RESEND_API_KEY") or ""
if not key:
    raise SystemExit(f"SMTP_PASSWORD not set in {src}")

alert_to = values.get("OPS_ALERT_TO", "joshuastevensynwa@gmail.com")
smtp_from = values.get("SMTP_FROM", "Atlas <noreply@customchatandautomation.co.za>")
if "<" in smtp_from and ">" in smtp_from:
    addr = smtp_from.split("<", 1)[1].split(">", 1)[0].strip()
    alert_from = f"Atlas Ops <{addr}>"
else:
    alert_from = "Atlas Ops <noreply@customchatandautomation.co.za>"

dest.write_text(
    f'OPS_ALERT_TO={alert_to}\nOPS_ALERT_FROM="{alert_from}"\nRESEND_API_KEY={key}\n',
    encoding="utf-8",
)
dest.chmod(0o600)
print(alert_to)
PY
)"

echo "Wrote $ops_env (alert to: $to)"

sed -i 's/\r$//' "$repo_dir/deploy/ops_alert.sh" 2>/dev/null || true
bash "$repo_dir/deploy/ops_alert.sh" \
  "Atlas ops alerts enabled" \
  "Backup failures and high log disk will email this address. This is the test message."
