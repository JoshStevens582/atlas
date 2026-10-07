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

chmod 755 "$repo_dir/deploy/watchdog.sh"

cron_weekly="/etc/cron.d/atlas-weekly-reboot"
cat >"$cron_weekly" <<'EOF'
# Full VM reboot: Sunday 03:00 UTC (05:00 SAST). Clears slow memory growth.
0 3 * * 0 root /sbin/reboot
EOF
chmod 644 "$cron_weekly"

cron_watchdog="/etc/cron.d/atlas-watchdog"
cat >"$cron_watchdog" <<EOF
# Restart web+api if /api/ready fails through Caddy on localhost.
*/5 * * * * root $repo_dir/deploy/watchdog.sh >>/var/log/atlas-watchdog.log 2>&1
EOF
chmod 644 "$cron_watchdog"

touch /var/log/atlas-watchdog.log
chmod 644 /var/log/atlas-watchdog.log

echo "Swap:"
swapon --show
echo "Cron:"
ls -la /etc/cron.d/atlas-*
