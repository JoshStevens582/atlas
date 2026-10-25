# Ops alerts

Atlas can email you when **nightly backup fails** or when **durable logs** on `atlas-data` grow past a threshold. Alerts use the [Resend](https://resend.com) HTTP API (same provider as password reset and email verification).

## One-time server setup

On the production server (reads Resend key from `/opt/atlas/.env`, does not print it):

```bash
sudo bash /opt/atlas/deploy/bootstrap_ops_alerts.sh
```

Optional: set `OPS_ALERT_TO=you@example.com` in `/opt/atlas/.env` before running bootstrap; default is the Gmail inbox used for password-reset testing.

Or create `/etc/atlas-ops.env` by hand from `deploy/atlas-ops.env.example` (mode `600`, owner root). Quote `OPS_ALERT_FROM` if it contains spaces or angle brackets.

Then ensure crons are installed:

```bash
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
