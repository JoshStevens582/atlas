# Backups

Atlas keeps its state in the `atlas-data` Docker volume: the SQLite database
(users, threads), the Chroma index, and uploaded files. Redis is not backed up:
it only holds rate-limit counters and the answer cache. The handbook Library
can be rebuilt with `python -m atlas.reset_library`.

`deploy/backup.sh` stops the api for a few seconds, packs the volume into
`/var/backups/atlas/atlas-data-<time>.tar.gz`, starts the api again, and copies
the archive to an off-server bucket with rclone. Local copies older than 14 days
and bucket copies older than 30 days are deleted.

## One-time setup on the server

Create an S3-compatible bucket (for example Backblaze B2) and an application
key limited to that bucket. Put it in a root-only file. No quotes around values:

```
# /etc/atlas-backup.env   (chmod 600, owner root)
BACKUP_REMOTE=:s3:YOUR-BUCKET-NAME
RCLONE_S3_PROVIDER=Other
RCLONE_S3_ENDPOINT=https://s3.YOUR-REGION.backblazeb2.com
RCLONE_S3_ACCESS_KEY_ID=...
RCLONE_S3_SECRET_ACCESS_KEY=...
```

Run it once by hand to check, then schedule it nightly (with failure email — see
`docs/ops_alerts.md`):

```
cd /opt/atlas && deploy/backup.sh
sudo bash deploy/setup_host_stability.sh
```

That installs `deploy/backup_cron.sh` at 02:15 server time. To run only the
backup without cron: `deploy/backup.sh` or `deploy/backup_cron.sh`.

## Restore

Check a backup is good without touching the live site:

```
deploy/restore.sh /var/backups/atlas/atlas-data-<time>.tar.gz --into scratch_check
```

Replace the live data (the site is down while this runs):

```
deploy/restore.sh /var/backups/atlas/atlas-data-<time>.tar.gz --live
```

To restore from the bucket, first copy an archive down with
`rclone copy :s3:YOUR-BUCKET-NAME/atlas-data-<time>.tar.gz /var/backups/atlas`
using the same `RCLONE_S3_*` settings.

CI runs the whole round trip on every pull request: backup, upload to a local
folder, restore into a scratch volume, and an integrity check on the restored
database.
