# Ops alerts

Atlas can email you when **nightly backup fails** or when **durable logs** on `atlas-data` grow past a threshold. Alerts use the [Resend](https://resend.com) HTTP API (same provider as password reset and email verification).

## One-time server setup

1. Create `/etc/atlas-ops.env` from `deploy/atlas-ops.env.example` (mode `600`, owner root).
2. Set `OPS_ALERT_TO` to your inbox, `OPS_ALERT_FROM` to a verified sender on your domain, and `RESEND_API_KEY` to your Resend sending key.
3. Point cron at the wrapper (re-run host setup or edit cron by hand):

```bash
sudo cp deploy/atlas-ops.env.example /etc/atlas-ops.env
sudo chmod 600 /etc/atlas-ops.env
# edit /etc/atlas-ops.env with real values
sudo bash deploy/setup_host_stability.sh
```

`setup_host_stability.sh` installs:

| Cron | Script | Purpose |
| :--- | :--- | :--- |
| `15 2 * * *` | `deploy/backup_cron.sh` | Backup + email on failure |
| `0 6 * * *` | `deploy/check_log_disk.sh` | Email if `data/logs` is over the byte threshold |
| `*/5 * * * *` | `deploy/watchdog.sh` | Restart web+api if `/api/ready` fails |

Logs: `/var/log/atlas-backup.log`, `/var/log/atlas-log-disk.log`, `/var/log/atlas-watchdog.log`.

## Test without sending mail

```bash
OPS_ALERT_DRY_RUN=1 deploy/ops_alert.sh "test subject" "test body"
```

## Manual checks

```bash
deploy/check_log_disk.sh
deploy/backup.sh   # or deploy/backup_cron.sh to exercise the failure mail path
```

If `/etc/atlas-ops.env` is missing or incomplete, scripts log to syslog (`logger -t atlas-ops-alert`) and exit successfully so backups still run.
