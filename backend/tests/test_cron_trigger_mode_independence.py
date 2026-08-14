from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import auth
import app as pluto_app
from autonomy.scan_run_log import list_scan_runs

def _post_cron_trigger():
    # Read CRON_SECRET at CALL time, not import time - a test that
    # monkeypatches it must still authenticate successfully.
    with pluto_app.app.test_client() as client:
        return client.post("/api/autonomy/cron-trigger", headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")})


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be approved,
    which a bare fixture user_id string alone does not satisfy."""
    user = auth.register_user(f"scanruns-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


# --- existing-position monitoring stays independent of mode -----------------------


def test_off_mode_user_with_webull_configured_still_gets_reconciled(user_id):
    """The core fix: a user NOT in AUTONOMOUS mode must still have
    existing positions/pending orders reconciled by the scheduled cron
    tick - only NEW-entry scanning is mode-gated."""
    monitor_result = {"has_unresolved_ambiguous_submission": False, "has_incomplete_manual_resolution": False, "still_transitional": False, "entries_checked": 2, "still_transitional_count": 1}
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value=monitor_result) as mock_monitor, \
         patch.object(pluto_app, "_run_autonomous_trade_scan") as mock_full_scan:
        response = _post_cron_trigger()

    assert response.status_code == 200
    mock_monitor.assert_called_once_with(user_id)
    mock_full_scan.assert_not_called()  # OFF mode must never trigger the opportunity-scanning/new-entry path

    runs = list_scan_runs(user_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "skipped"
    assert runs[0]["account_mode"] == "OFF"
    assert "reconciled" in runs[0]["reason"].lower()
    assert "2" in runs[0]["reason"]  # entries_checked surfaced in the human-readable reason


def test_paper_mode_user_also_gets_reconciled_not_just_off(user_id):
    monitor_result = {"entries_checked": 0, "still_transitional_count": 0}
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "PAPER"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value=monitor_result) as mock_monitor:
        _post_cron_trigger()
    mock_monitor.assert_called_once_with(user_id)


def test_off_mode_user_without_webull_configured_is_recorded_but_not_scanned(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=False), \
         patch.object(pluto_app, "_run_fast_order_monitor") as mock_monitor:
        _post_cron_trigger()

    mock_monitor.assert_not_called()
    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "skipped"
    assert "not configured" in runs[0]["reason"]


def test_reconciliation_failure_for_a_non_autonomous_user_is_recorded_as_failed(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", side_effect=RuntimeError("broker timeout")):
        response = _post_cron_trigger()

    assert response.status_code == 200  # one user's failure must not break the whole tick
    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "failed"
    assert "broker timeout" in runs[0]["error"]


def test_scan_already_running_for_a_non_autonomous_user_is_recorded_as_skipped_not_failed(user_id):
    from scan_lock import ScanAlreadyRunningError
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", side_effect=ScanAlreadyRunningError("busy")):
        _post_cron_trigger()

    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "skipped"
    assert "concurrent" in runs[0]["reason"]


# --- AUTONOMOUS mode still gets the full scan, and gets its own durable record ----


def test_autonomous_mode_user_gets_the_full_scan_and_a_processed_record(user_id):
    scan_result = {
        "ok": True, "placed_count": 1, "skipped_count": 1,
        "placed": [{"ticker": "AAPL", "status": "placed"}],
        "skipped": [{"ticker": "MSFT", "status": "skipped", "reason_skipped": "LLM vetoed"}],
        "candidates_found": 5, "candidates_qualifying": 2,
        "entries_allowed": True, "new_entries_blocked_reason": "",
    }
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "AUTONOMOUS"}), \
         patch.object(pluto_app, "_run_autonomous_trade_scan", return_value=scan_result) as mock_full_scan, \
         patch.object(pluto_app, "_run_fast_order_monitor") as mock_monitor:
        _post_cron_trigger()

    mock_full_scan.assert_called_once_with(user_id)
    mock_monitor.assert_not_called()  # AUTONOMOUS path does its own reconciliation internally - no separate call

    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "processed"
    assert runs[0]["account_mode"] == "AUTONOMOUS"
    assert runs[0]["candidates_found"] == 5
    assert runs[0]["candidates_qualifying"] == 2
    assert runs[0]["orders_outcomes"]["placed"] == 1


def test_autonomous_scan_failure_is_recorded_as_failed_with_a_redacted_error(user_id, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "leaked-cron-secret-value-789")
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "AUTONOMOUS"}), \
         patch.object(pluto_app, "_run_autonomous_trade_scan", side_effect=RuntimeError("auth failed: leaked-cron-secret-value-789")), \
         patch.object(pluto_app, "get_webull_credentials", side_effect=RuntimeError("no creds")):
        response = _post_cron_trigger()

    assert response.status_code == 200
    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "failed"
    assert "leaked-cron-secret-value-789" not in runs[0]["error"]
    assert "***REDACTED***" in runs[0]["error"]


def test_scan_already_running_for_an_autonomous_user_is_recorded_as_skipped(user_id):
    from scan_lock import ScanAlreadyRunningError
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "AUTONOMOUS"}), \
         patch.object(pluto_app, "_run_autonomous_trade_scan", side_effect=ScanAlreadyRunningError("busy")):
        _post_cron_trigger()

    runs = list_scan_runs(user_id)
    assert runs[0]["status"] == "skipped"


# --- scheduled vs actual start time, monitor heartbeat -----------------------------


def test_every_record_carries_scheduled_and_actual_start_times(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=False):
        _post_cron_trigger()

    run = list_scan_runs(user_id)[0]
    assert run.get("scheduled_start_time")
    assert run.get("actual_start_time")
    assert run.get("completion_time")


def test_nearest_scheduled_slot_rounds_down_to_the_five_minute_grid():
    from datetime import datetime, timezone
    now = datetime(2026, 8, 14, 13, 47, 22, tzinfo=timezone.utc)
    slot = pluto_app._nearest_scheduled_slot(now)
    assert slot == datetime(2026, 8, 14, 13, 45, 0, 0, tzinfo=timezone.utc)


def test_every_record_carries_a_monitor_heartbeat_snapshot(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=False):
        _post_cron_trigger()

    run = list_scan_runs(user_id)[0]
    heartbeat = run.get("monitor_heartbeat")
    assert isinstance(heartbeat, dict)
    assert "fast_monitor_healthy" in heartbeat


def test_monitor_heartbeat_snapshot_failure_does_not_block_the_tick(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=False), \
         patch.object(pluto_app, "_fast_monitor_health_status", side_effect=RuntimeError("boom")):
        response = _post_cron_trigger()

    assert response.status_code == 200
    run = list_scan_runs(user_id)[0]
    assert "error" in run.get("monitor_heartbeat", {})


# --- authenticated per-user scan-runs endpoint --------------------------------------


def test_scan_runs_endpoint_returns_only_this_users_own_records(user_id, other_user_id):
    from autonomy.scan_run_log import record_scan_run
    registered_user_id = _registered_user(user_id[:8])
    record_scan_run(registered_user_id, {"status": "processed", "ticker": "mine"})
    record_scan_run(other_user_id, {"status": "processed", "ticker": "not-mine"})

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/api/autonomy/scan-runs")

    assert response.status_code == 200
    payload = response.get_json()
    runs = payload["data"]["scan_runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "processed"


def test_scan_runs_endpoint_requires_auth():
    with pluto_app.app.test_client() as client:
        response = client.get("/api/autonomy/scan-runs")
    assert response.status_code in (401, 302, 403)
