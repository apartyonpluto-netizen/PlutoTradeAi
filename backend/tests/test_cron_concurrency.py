from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import app as pluto_app
from scan_lock import ScanAlreadyRunningError, user_scan_lock


def test_overlapping_scan_for_same_user_is_rejected(user_id):
    """A second call to _run_autonomous_trade_scan for a user whose scan is
    already in flight must be refused outright, not queue behind it and run
    anyway once the first finishes - that's what makes double-placement
    across overlapping cron ticks structurally impossible rather than just
    unlikely."""
    with user_scan_lock(user_id):
        with pytest.raises(ScanAlreadyRunningError):
            pluto_app._run_autonomous_trade_scan(user_id)


def test_scan_runs_normally_once_the_lock_is_free(user_id):
    # No Webull credentials configured for this fresh user, so the locked
    # inner function raises its own ValidationError almost immediately -
    # what matters here is that it's THAT error, not ScanAlreadyRunningError,
    # proving the lock was acquired and released cleanly with nothing held.
    with pytest.raises(pluto_app.ValidationError) as exc_info:
        pluto_app._run_autonomous_trade_scan(user_id)
    assert "Webull" in str(exc_info.value)


def test_cron_trigger_endpoint_treats_lock_conflict_as_a_benign_skip(user_id):
    """Simulates the exact overlap scenario from review: GitHub Actions (or
    a manual workflow_dispatch) firing the cron-trigger endpoint while a
    scan for that user is already running. The per-user loop must record it
    as an ok/skipped result, not an error, and must not stop processing
    other users."""
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), patch.object(
        pluto_app, "get_autonomy_status", return_value={"current_mode": "AUTONOMOUS"}
    ), patch.object(
        pluto_app, "_run_autonomous_trade_scan", side_effect=ScanAlreadyRunningError("already running")
    ):
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/cron-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["data"]["results"][0]["ok"] is True
    assert payload["data"]["results"][0]["skipped"] == "scan_already_running"
