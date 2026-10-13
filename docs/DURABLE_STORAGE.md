# Durable storage

Accounts, trades, credentials, and history are JSON files under `PLUTO_DATA_DIR` (on Render, the 1 GB disk mounted at `/var/data`). A snapshot that lives on that same disk does not survive losing the disk. Off-site copies do.

The app stays on JSON. A move of every flock-protected file into Postgres would be a second source of truth for order records, and it is not required to survive disk loss. Snapshots are still taken locally (see `docs/RUNBOOK_BACKUP_RESTORE.md`). When the bucket below is set, each new snapshot is also uploaded. Nothing in this path places or changes a broker order, and nothing writes a restore over the live directory.

## What Tre provisions

Create a private S3-compatible bucket. Cloudflare R2 is the cheap default: 10 GB-month of storage is free, and there is no egress fee. A PlutoTrade snapshot is JSON plus one compressed archive, so this stays inside the free tier. Past that, R2 is about $0.015 per GB-month. Confirm the current price in the Cloudflare dashboard before turning it on.

On the **web service** (the one with the disk), set:

| Variable | Example |
|---|---|
| `PLUTO_BACKUP_S3_BUCKET` | `plutotrade-backups` |
| `PLUTO_BACKUP_S3_ACCESS_KEY_ID` | R2 token access key id |
| `PLUTO_BACKUP_S3_SECRET_ACCESS_KEY` | R2 token secret |
| `PLUTO_BACKUP_S3_ENDPOINT` | `https://<accountid>.r2.cloudflarestorage.com` |
| `PLUTO_BACKUP_S3_REGION` | `auto` for R2, `us-east-1` for AWS |
| `PLUTO_BACKUP_S3_PREFIX` | `plutotrade/` (optional) |

Use a token that can read and write only that bucket. Do not put the secret in git or in a log. The worker and the cron do not need these variables; the web service uploads at the end of the daily snapshot (after 20:00 UTC) and when an admin clicks Back up now.

Until the variables are set, local snapshots still run and Admin → Backups shows off-site as not configured. No new Render service and no Postgres bill.

AWS S3 works with the same variables (`PLUTO_BACKUP_S3_ENDPOINT` can be omitted; region must match the bucket). Cost is S3 storage plus a few PUT/GET requests, cents at this size.

## Restore

Restores are deliberate and never overwrite `/var/data` from the app. `offsite_backup.restore_archive` extracts into a directory you choose and refuses the live data dir. The runbook has the rest of the steps (emergency stop, broker sync). Download from the bucket if the disk itself is gone:

```
PLUTO_BACKUP_S3_BUCKET=... \
PLUTO_BACKUP_S3_ACCESS_KEY_ID=... \
PLUTO_BACKUP_S3_SECRET_ACCESS_KEY=... \
PLUTO_BACKUP_S3_ENDPOINT=... \
PLUTO_BACKUP_S3_REGION=auto \
python -c "from pathlib import Path; import offsite_backup; offsite_backup.download('pluto-data-YYYYMMDDTHHMMSSZ.tar.gz', Path('/tmp/pluto-restore'))"
```

Run that from `backend/` on a machine that has the keys, then inspect `/tmp/pluto-restore` before copying any file back. Treat the archive as secret: it contains encrypted credentials and trading records.
