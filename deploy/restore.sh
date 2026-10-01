#!/usr/bin/env bash
# Unpack a backup made by deploy/backup.sh.
#
#   deploy/restore.sh ARCHIVE --into VOLUME   unpack into a scratch volume.
#                                             The live stack is not touched.
#                                             Use it to check a backup works.
#   deploy/restore.sh ARCHIVE --live          REPLACE the live data with the
#                                             backup. Stops the api, wipes
#                                             /app/data, unpacks, starts the api.
set -euo pipefail

usage() {
  echo "Usage: $0 ARCHIVE --into VOLUME | $0 ARCHIVE --live" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
archive_path="$1"
mode="$2"
[ -f "$archive_path" ] || { echo "No such file: $archive_path" >&2; exit 1; }
archive_dir="$(cd "$(dirname "$archive_path")" && pwd)"
archive_name="$(basename "$archive_path")"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

case "$mode" in
  --into)
    [ "$#" -eq 3 ] || usage
    docker run --rm -v "$3:/app/data" -v "$archive_dir:/backup:ro" alpine \
      tar -xzf "/backup/$archive_name" -C /app/data
    echo "Unpacked $archive_name into volume $3."
    ;;
  --live)
    [ "$#" -eq 2 ] || usage
    api_container="$(docker compose ps -aq api)"
    [ -n "$api_container" ] || { echo "api container not found." >&2; exit 1; }
    docker compose stop api
    docker run --rm --volumes-from "$api_container" -v "$archive_dir:/backup:ro" alpine \
      sh -c "find /app/data -mindepth 1 -delete && tar -xzf '/backup/$archive_name' -C /app/data"
    docker compose start api
    echo "Live data replaced with $archive_name."
    ;;
  *)
    usage
    ;;
esac
