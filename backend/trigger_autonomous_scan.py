from __future__ import annotations

"""One-shot trigger script for the plutotradeai-autonomous-scan-trigger
Render Cron Job - see render.yaml.

Makes an authenticated POST to /api/autonomy/cron-trigger, then a second
one to /api/autonomy/fast-monitor-trigger, and exits 0 only if both
succeeded (HTTP 200), so Render's own Cron Job run history correctly
reflects success/failure of either layer. Unlike continuous_monitor_worker.py
this is NOT a long-running loop - Render's Cron Job scheduler itself
handles "every N minutes" (see the `schedule` field on this service in
render.yaml), so this script's whole job is two requests then exit.

Replaces GitHub Actions as the PRIMARY trigger for BOTH endpoints - found
live 2026-08-28 (for cron-trigger) and again 2026-09-04 (for
fast-monitor-trigger, which was still GitHub-only at that point) that
GitHub's own scheduled-workflow mechanism is best-effort and unreliable at
a 5-minute cadence: real run history showed runs landing hours apart, then
stopping entirely for 14-22+ hours at a time, in both cases confirmed NOT
a billing/quota issue. This matches GitHub's own documented caveat that
the schedule event "can be delayed during periods of high load" and is
not a guaranteed-delivery mechanism. Render's own Cron Job primitive is a
first-party, dedicated scheduled-invocation mechanism on the SAME
infrastructure as the web service itself, not a cross-platform
best-effort trigger.

fast-monitor-trigger was deliberately folded into THIS existing cron job
rather than given its own separate Render Cron Job service: Render bills
Cron Jobs for actual runtime, not a flat per-service fee, so piggybacking
a second, fast (~seconds) request onto a job that's already running costs
nothing extra - a second service would be pure added cost for no added
reliability. This does give up the original fast-monitor-scheduler.yml's
2-minute offset from the full scan (see that file's own comment) but that
offset existed only to interleave two independently-unreliable GitHub
schedules; both requests now ride the same reliable Render cron tick, and
continuous_monitor_worker.py's ~10s polling already covers the gap between
ticks regardless.

Both GitHub Actions workflows are deliberately left in place, not deleted -
defense-in-depth: if this cron job's own service is ever down, deleted, or
out of Render credits, they still provide a slower fallback. Overlapping
triggers from any combination of sources are safe: both endpoints'
underlying per-user locks treat a concurrent call as an ordinary skip, not
an error.

Required environment variables:
    CRON_TRIGGER_URL         Full URL of the cron-trigger endpoint.
    FAST_MONITOR_TRIGGER_URL Full URL of the fast-monitor-trigger endpoint.
    CRON_SECRET               The same shared secret the web service itself
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

REQUEST_TIMEOUT_SECONDS = 120.0  # cron-trigger processes every registered user sequentially - generous on purpose


def _post(label: str, url: str, secret: str) -> bool:
    """POSTs to one endpoint and returns whether it succeeded (HTTP 200),
    printing enough for Render's own log viewer to diagnose a failure
    without needing to reproduce it."""
    try:
        response = requests.post(url, headers={"X-Cron-Secret": secret}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as error:
        print(f"{label}: request failed: {error}", file=sys.stderr)
        return False

    print(f"{label}: HTTP status {response.status_code}")
    print(response.text[:2000])
    if response.status_code != 200:
        print(f"{label}: failed with status {response.status_code}", file=sys.stderr)
        return False
    return True


def main() -> int:
    cron_trigger_url = os.environ.get("CRON_TRIGGER_URL", "").strip()
    fast_monitor_url = os.environ.get("FAST_MONITOR_TRIGGER_URL", "").strip()
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not cron_trigger_url or not fast_monitor_url or not secret:
        print(
            "CRON_TRIGGER_URL, FAST_MONITOR_TRIGGER_URL, and CRON_SECRET are all required - refusing to run",
            file=sys.stderr,
        )
        return 1

    # Attempt both regardless of the other's outcome - one endpoint being
    # down must not hide whether the other one is healthy.
    cron_trigger_ok = _post("cron-trigger", cron_trigger_url, secret)
    fast_monitor_ok = _post("fast-monitor-trigger", fast_monitor_url, secret)

    return 0 if (cron_trigger_ok and fast_monitor_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
