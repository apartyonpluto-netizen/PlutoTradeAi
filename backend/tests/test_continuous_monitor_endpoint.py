from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


# --- _continuous_monitor_health_status ------------------------------------------


def test_never_called_is_unhealthy():
    with patch.object(pluto_app, "get_continuous_monitor_heartbeat_status", return_value={}):
        status = pluto_app._continuous_monitor_health_status()
    assert status["healthy"] is False
    assert "never" in status["reason"].lower()
    assert status["age_seconds"] is None


def test_worker_alive_and_reconciliation_recently_completed_is_healthy():
    heartbeat = {
        "last_request_run_id": "run-1",
        "last_request_received_at": _iso(-timedelta(seconds=2)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=1)),
    }
    with patch.object(pluto_app, "get_continuous_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._continuous_monitor_health_status()
    assert status["healthy"] is True


def test_worker_stopped_calling_is_unhealthy():
    heartbeat = {
        "last_request_run_id": "run-1",
        "last_request_received_at": _iso(-timedelta(seconds=200)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=199)),
    }
    with patch.object(pluto_app, "get_continuous_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._continuous_monitor_health_status()
    assert status["healthy"] is False
    assert "worker" in status["reason"].lower()


def test_worker_alive_but_reconciliation_never_completed_is_unhealthy():
    """The worker IS reaching the endpoint (recent), but the most recent
    request's reconciliation never finished - a DIFFERENT failure mode
    (the endpoint's own logic is stuck), distinguishable from the worker
    itself being down."""
    heartbeat = {
        "last_request_run_id": "run-2",
        "last_request_received_at": _iso(-timedelta(seconds=2)),
        "last_completed_run_id": "run-1",  # an OLDER run's completion
        "last_completed_at": _iso(-timedelta(seconds=500)),
    }
    with patch.object(pluto_app, "get_continuous_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._continuous_monitor_health_status()
    assert status["healthy"] is False
    assert "reconciliation" in status["reason"].lower() or "stuck" in status["reason"].lower()


def test_worker_alive_but_reconciliation_stale_is_unhealthy():
    heartbeat = {
        "last_request_run_id": "run-1",
        "last_request_received_at": _iso(-timedelta(seconds=2)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=200)),
    }
    with patch.object(pluto_app, "get_continuous_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._continuous_monitor_health_status()
    assert status["healthy"] is False


# --- api_autonomy_continuous_monitor_tick endpoint -------------------------------


def test_endpoint_requires_the_dedicated_monitor_worker_secret(user_id):
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[]):
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "wrong"})
    assert response.status_code == 401


def test_endpoint_rejects_the_cron_secret_it_is_a_different_credential(user_id):
    """The dedicated monitor-worker token must be independent of
    CRON_SECRET - rotating one must never require rotating the other."""
    cron_secret = os.environ.get("CRON_SECRET", "")
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "a-totally-different-secret"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[]):
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": cron_secret})
    assert response.status_code == 401


def test_endpoint_succeeds_with_the_correct_secret(user_id):
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[]):
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})
    assert response.status_code == 200


def test_endpoint_never_scans_or_places_a_new_entry(user_id):
    """The strongest available proof - directly patches the scanner, the
    candidate builder, entry-submission, and the literal broker BUY-order
    API, and asserts none of them are ever called."""
    entry_dict = {"lifecycle_state": "protection_failed"}
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "_user_needs_fast_monitor_pass", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value={"has_unresolved_ambiguous_submission": False, "has_incomplete_manual_resolution": False, "still_transitional": False, "entries_checked": 0, "still_transitional_count": 0}), \
         patch.object(pluto_app, "get_market_data") as mock_scanner, \
         patch.object(pluto_app, "build_strategy_intelligence") as mock_candidate_builder, \
         patch.object(pluto_app, "_submit_and_protect_entry") as mock_submit_entry, \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_buy_order:
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})

    assert response.status_code == 200
    mock_scanner.assert_not_called()
    mock_candidate_builder.assert_not_called()
    mock_submit_entry.assert_not_called()
    mock_buy_order.assert_not_called()


def test_endpoint_processes_a_user_with_autonomy_off_and_zero_local_state(user_id):
    """Same autonomy-independence requirement as the ~60s fast monitor -
    the continuous monitor must include every Webull-configured user
    regardless of autonomy mode or local state."""
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value={"entries_checked": 0, "still_transitional_count": 0}) as mock_monitor:
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})

    assert response.status_code == 200
    mock_monitor.assert_called_once_with(user_id)


def test_endpoint_records_both_heartbeat_signals(user_id):
    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[]):
        with pluto_app.app.test_client() as client:
            client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})

    status = pluto_app.get_continuous_monitor_heartbeat_status()
    assert status["last_request_received_at"]
    assert status["last_completed_at"]
    assert status["last_request_run_id"] == status["last_completed_run_id"]


def test_endpoint_rejects_overlapping_requests_with_409():
    """Belt-and-suspenders global lock - a second request arriving while
    the first's per-user loop is still "running" (simulated here by
    holding the lock manually) must be rejected immediately, not queued
    or allowed to duplicate work."""
    from scan_lock import continuous_monitor_tick_lock

    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}):
        with continuous_monitor_tick_lock():
            with pluto_app.app.test_client() as client:
                response = client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})
    assert response.status_code == 409


def test_endpoint_records_received_heartbeat_even_on_a_lock_conflict():
    """"Worker reached us" must still be true even when the tick itself
    was skipped due to an overlap - these are independent signals."""
    from scan_lock import continuous_monitor_tick_lock

    with patch.dict(os.environ, {"MONITOR_WORKER_SECRET": "worker-secret-xyz"}):
        with continuous_monitor_tick_lock():
            with pluto_app.app.test_client() as client:
                client.post("/api/autonomy/continuous-monitor-tick", headers={"X-Monitor-Worker-Secret": "worker-secret-xyz"})

    status = pluto_app.get_continuous_monitor_heartbeat_status()
    assert status["last_request_received_at"]
    # No completion recorded for THIS request - the lock conflict skipped
    # the reconciliation loop entirely.
    assert status.get("last_completed_run_id") != status.get("last_request_run_id")


def test_endpoint_is_registered_as_a_token_auth_path_not_session_auth():
    assert "/api/autonomy/continuous-monitor-tick" in pluto_app._TOKEN_AUTH_PATHS


def test_full_scan_alerts_when_the_continuous_monitor_is_unhealthy(user_id):
    with patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": False, "reason": "worker down"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "is_admin", return_value=True):
        pluto_app._alert_admins_continuous_monitor_unhealthy_if_needed()

    from alerts import load_manual_alerts
    alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "continuous_monitor_unhealthy"]
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "critical"
