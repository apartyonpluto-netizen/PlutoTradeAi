from __future__ import annotations

"""One-shot trigger script for the plutotradeai-autonomous-scan-trigger
Render Cron Job - see render.yaml.

Makes exactly ONE authenticated POST to /api/autonomy/cron-trigger and
exits 0 on success (HTTP 200), non-zero otherwise, so Render's own Cron
Job run history correctly reflects success/failure. Unlike
continuous_monitor_worker.py this is NOT a long-running loop - Render's
Cron Job scheduler itself handles "every N minutes" (see the `schedule`
field on this service in render.yaml), so this script's whole job is a
single request then exit, matching exactly what
.github/workflows/autonomous-scan-scheduler.yml's curl step already did.

Replaces that GitHub Actions workflow as the PRIMARY trigger - found live
2026-08-28 that GitHub's own scheduled-workflow mechanism is best-effort
and unreliable for a 5-minute cadence: real GitHub Actions run history
showed runs landing hours apart instead of every 5 minutes, then stopping
entirely for 22+ hours. Not a billing/quota issue - GitHub's own Actions
minutes usage showed 0/2000 consumed for the billing period. This matches
GitHub's own documented caveat that the schedule event "can be delayed
during periods of high load" and is not a guaranteed-delivery mechanism,
especially at short intervals. Render's own Cron Job primitive is a
first-party, dedicated scheduled-invocation mechanism on the SAME
infrastructure as the web service itself, not a cross-platform
best-effort trigger - and it's exactly what this endpoint's own docstring
in app.py already anticipated ("Called on a timer by a Render Cron Job
(or, currently, a GitHub Actions schedule...").

The GitHub Actions workflow is deliberately left in place, not deleted -
same defense-in-depth precedent already established for
fast-monitor-scheduler.yml (see that file's own comment: "an INDEPENDENT
FALLBACK LAYER, not a replacement"). Overlapping triggers from both are
safe: /api/autonomy/cron-trigger's own per-user user_scan_lock treats a
concurrent call as an ordinary ScanAlreadyRunningError skip, not an
error.

Required environment variables:
    CRON_TRIGGER_URL    Full URL of the cron-trigger endpoint.
    CRON_SECRET         The same shared secret the web service itself
                         reads from its own CRON_SECRET env var - copy
                         the EXACT value from the plutotradeai web
                         service's Environment tab into this cron job's
                         own Environment tab. Render's Blueprint spec has
                         no way to share one generated secret across two
                         services automatically - see MONITOR_WORKER_SECRET's
                         identical setup in render.yaml for the same
                         reasoning already established for the
                         continuous-monitor worker."""

import os
import sys

import requests

REQUEST_TIMEOUT_SECONDS = 120.0  # this endpoint processes every registered user sequentially - generous on purpose


def main() -> int:
    url = os.environ.get("CRON_TRIGGER_URL", "").strip()
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not url or not secret:
        print("CRON_TRIGGER_URL and CRON_SECRET are both required - refusing to run", file=sys.stderr)
        return 1

    try:
        response = requests.post(url, headers={"X-Cron-Secret": secret}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as error:
        print(f"request failed: {error}", file=sys.stderr)
        return 1

    print(f"HTTP status: {response.status_code}")
    print(response.text[:2000])
    if response.status_code != 200:
        print(f"cron trigger failed with status {response.status_code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
