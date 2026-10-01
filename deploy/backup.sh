#!/usr/bin/env bash
# Back up the atlas-data volume (SQLite database, Chroma index, uploads).
#
#   1. Stop the api for a few seconds so nothing is writing to the SQLite files.
#   2. Pack /app/data into a .tar.gz in BACKUP_DIR, then start the api again.
#   3. Delete local archives older than KEEP_DAYS.
#   4. Copy the archives to an off-server bucket with rclone and delete bucket
#      copies older than REMOTE_KEEP_DAYS.
#
# Where the bucket is and the keys for it live in BACKUP_ENV_FILE, which sits on
# the server (root-only) and is never in the repo. See docs/backups.md.
# Any failure exits non-zero, so cron's log shows it. The api is started again
# even if a step fails.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

backup_dir="${BACKUP_DIR:-/var/backups/atlas}"
keep_days="${KEEP_DAYS:-14}"
remote_keep_days="${REMOTE_KEEP_DAYS:-30}"
env_file="${BACKUP_ENV_FILE:-/etc/atlas-backup.env}"
rclone_image="${RCLONE_IMAGE:-rclone/rclone:1.68.2}"
# Extra `docker run` arguments for the upload step (CI uses it to mount a folder).
read -r -a rclone_docker_args <<< "${RCLONE_DOCKER_ARGS:-}"

archive_name="atlas-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"

if [ ! -f "$env_file" ]; then
  echo "Missing $env_file: no off-server destination configured." >&2
  exit 1
fi
remote="${BACKUP_REMOTE:-$(grep '^BACKUP_REMOTE=' "$env_file" | cut -d= -f2-)}"
if [ -z "$remote" ]; then
  echo "BACKUP_REMOTE is not set in $env_file." >&2
  exit 1
fi

api_container="$(docker compose ps -aq api)"
if [ -z "$api_container" ]; then
  echo "api container not found. Is the stack created?" >&2
  exit 1
fi

mkdir -p "$backup_dir"

api_stopped=0
start_api() {
  if [ "$api_stopped" = 1 ]; then
    docker compose start api
    api_stopped=0
  fi
}
trap start_api EXIT

docker compose stop api
api_stopped=1
# Written as .partial and renamed, so a half-written file never looks like a backup.
docker run --rm --volumes-from "$api_container:ro" -v "$backup_dir:/backup" alpine \
  tar -czf "/backup/$archive_name.partial" -C /app/data .
start_api

mv "$backup_dir/$archive_name.partial" "$backup_dir/$archive_name"
tar -tzf "$backup_dir/$archive_name" > /dev/null
echo "Wrote $backup_dir/$archive_name"

find "$backup_dir" -name 'atlas-data-*.tar.gz' -mtime "+$keep_days" -delete

# --s3-no-check-bucket: the key is limited to this one bucket, so rclone must not
# try to create it first (that is refused with 403). The bucket already exists.
docker run --rm --env-file "$env_file" "${rclone_docker_args[@]}" \
  -v "$backup_dir:/backup:ro" "$rclone_image" \
  copy /backup "$remote" --include 'atlas-data-*.tar.gz' --s3-no-check-bucket
docker run --rm --env-file "$env_file" "${rclone_docker_args[@]}" "$rclone_image" \
  delete "$remote" --min-age "${remote_keep_days}d" --include 'atlas-data-*.tar.gz'
echo "Uploaded to the off-server bucket."
