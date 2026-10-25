#!/usr/bin/env bash
# One-time host hardening for the Rackzar KLX2 box: swap, swappiness, weekly
# reboot, and a compose watchdog cron. Safe to re-run (swap skipped if present).
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
swap_file="${ATLAS_SWAP_FILE:-/swapfile}"
swap_gb="${ATLAS_SWAP_GB:-2}"

if ! swapon --show | grep -q "$swap_file"; then
  if [ ! -f "$swap_file" ]; then
    echo "Creating ${swap_gb}G swap at $swap_file"
    fallocate -l "${swap_gb}G" "$swap_file" || dd if=/dev/zero of="$swap_file" bs=1M count=$((swap_gb * 1024)) status=progress
    chmod 600 "$swap_file"
    mkswap "$swap_file"
  fi
  swapon "$swap_file"
fi

if ! grep -qF "$swap_file" /etc/fstab; then
  echo "$swap_file none swap sw 0 0" >>/etc/fstab
fi

sysctl_conf="/etc/sysctl.d/99-atlas.conf"
if [ ! -f "$sysctl_conf" ]; then
  cat >"$sysctl_conf" <<'EOF'
# Prefer RAM, use swap before OOM on a small VPS.
vm.swappiness=10
EOF
  sysctl --system >/dev/null
fi

chmod 755 "$repo_dir/deploy/watchdog.sh" \
  "$repo_dir/deploy/ops_alert.sh" \
  "$repo_dir/deploy/backup_cron.sh" \
  "$repo_dir/deploy/check_log_disk.sh"

cron_weekly="/etc/cron.d/atlas-weekly-reboot"
cat >"$cron_weekly" <<'EOF'
# Full VM reboot: Sunday 03:00 UTC (05:00 SAST). Clears slow memory growth.
0 3 * * 0 root /sbin/reboot
EOF
chmod 644 "$cron_weekly"

cron_watchdog="/etc/cron.d/atlas-watchdog"
cat >"$cron_watchdog" <<EOF
# Restart web+api if /api/ready fails through Caddy on localhost.
*/5 * * * * root /bin/bash $repo_dir/deploy/watchdog.sh >>/var/log/atlas-watchdog.log 2>&1
EOF
chmod 644 "$cron_watchdog"

touch /var/log/atlas-watchdog.log
chmod 644 /var/log/atlas-watchdog.log

cron_backup="/etc/cron.d/atlas-backup"
cat >"$cron_backup" <<EOF
# Nightly atlas-data backup; emails on failure when /etc/atlas-ops.env is set.
15 2 * * * root /bin/bash $repo_dir/deploy/backup_cron.sh
EOF
chmod 644 "$cron_backup"

cron_log_disk="/etc/cron.d/atlas-log-disk"
cat >"$cron_log_disk" <<EOF
# Email if durable logs exceed LOG_DISK_ALERT_BYTES (default 50 MiB).
0 6 * * * root /bin/bash $repo_dir/deploy/check_log_disk.sh >>/var/log/atlas-log-disk.log 2>&1
EOF
chmod 644 "$cron_log_disk"

touch /var/log/atlas-backup.log /var/log/atlas-log-disk.log
chmod 644 /var/log/atlas-backup.log /var/log/atlas-log-disk.log

echo "Swap:"
swapon --show
echo "Cron:"
ls -la /etc/cron.d/atlas-*
