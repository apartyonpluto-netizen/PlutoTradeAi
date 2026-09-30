# Runbook: backups and restore

## What exists

- Daily compressed snapshot of the Render data disk (`/var/data`), taken by
  the 5-minute cron after 20:00 UTC when the last one is older than 20 hours
  (`backend/backups.py`). The last 7 are kept in `/var/data/backups/`, each
  with a `.json` manifest (time, file count, SHA-256).
- Admin → Backups: list, "Back up now", "Verify" (checksum + every JSON file
  parses), "Download".
- Snapshots are on the SAME disk. They protect against damaged or wrongly
  rewritten files, not against losing the disk. Download one at least weekly
  and store it elsewhere. Downloads contain encrypted credentials and all
  trading records - treat them as sensitive.

## Restore (manual, deliberate)

Never restore over live data while the app is trading.

1. Turn on the emergency stop (Account Hub) and set `PLUTO_DISABLE_NEW_ENTRIES=1`
   on the web service. Suspend the cron job and the monitor worker in Render.
2. In Admin → Backups, **Verify** the snapshot you intend to use.
3. Open a Render shell on the web service and copy the current data aside
   first - never delete it:
   `D=/var/data/pre-restore-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$D" && cp -a /var/data/users /var/data/*.json "$D"/`
4. Extract only what you need into a scratch directory and inspect it:
   `mkdir /tmp/restore && tar -xzf /var/data/backups/<name>.tar.gz -C /tmp/restore`
5. Copy back the specific files to restore (e.g. one user's
   `overnight_orders.json`). Prefer restoring single files over the whole tree.
6. Compare with the broker before resuming: dashboard → Broker sync → Check now.
   The broker is the source of truth; a restored file older than the broker's
   state will show differences - resolve them, do not hide them.
7. Resume the cron job and worker; clear `PLUTO_DISABLE_NEW_ENTRIES`; turn the
   emergency stop off only after Broker sync is clean or its differences are
   understood.
