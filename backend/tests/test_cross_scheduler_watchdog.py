from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as pluto_app


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


# --- _full_scan_health_status --------------------------------------------------


def test_full_scan_never_run_is_unhealthy():
    with patch.object(pluto_app, "get_full_scan_heartbeat_status", return_value={}):
        status = pluto_app._full_scan_health_status()
    assert status["healthy"] is False
    assert "never run" in status["reason"]


def test_full_scan_recent_clean_completion_is_healthy():
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=30)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=20)),
    }
    with patch.object(pluto_app, "get_full_scan_heartbeat_status", return_value=heartbeat):
        status = pluto_app._full_scan_health_status()
    assert status["healthy"] is True


def test_full_scan_stale_completion_is_unhealthy():
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=1200)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=1190)),
    }
    with patch.object(pluto_app, "get_full_scan_heartbeat_status", return_value=heartbeat):
        status = pluto_app._full_scan_health_status()
    assert status["healthy"] is False


# --- cross-check alerting -------------------------------------------------------


def test_fast_monitor_trigger_alerts_when_the_full_scan_is_unhealthy(user_id):
    with patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": False, "reason": "never run"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "is_admin", return_value=True):
        pluto_app._alert_admins_full_scan_unhealthy_if_needed()

    from alerts import load_manual_alerts
    alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "full_scan_unhealthy"]
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "critical"


def test_full_scan_trigger_alerts_when_the_fast_monitor_is_unhealthy(user_id):
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": False, "reason": "never run"}), \
         patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "is_admin", return_value=True):
        pluto_app._alert_admins_fast_monitor_unhealthy_if_needed()

    from alerts import load_manual_alerts
    alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "fast_monitor_unhealthy"]
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "critical"


def test_fast_monitor_trigger_endpoint_checks_full_scan_health_once_per_tick(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[]), \
         patch.object(pluto_app, "_alert_admins_full_scan_unhealthy_if_needed") as mock_check:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    assert response.status_code == 200
    mock_check.assert_called_once()


def test_cron_trigger_endpoint_records_the_full_scan_heartbeat(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[]), \
         patch.object(pluto_app, "record_full_scan_run_started", return_value="run-xyz") as mock_started, \
         patch.object(pluto_app, "record_full_scan_run_completed") as mock_completed:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/cron-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    assert response.status_code == 200
    mock_started.assert_called_once()
    mock_completed.assert_called_once()
    assert mock_completed.call_args.args[0] == "run-xyz"
    assert mock_completed.call_args.kwargs["ran_for_users"] == 0


# --- external unauthenticated health endpoint -----------------------------------


def test_monitor_health_endpoint_requires_no_authentication():
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 4.2}), \
         patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 30.0}), \
         patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 3.0}):
        with pluto_app.app.test_client() as client:
            response = client.get("/api/autonomy/monitor-health")  # no session, no X-Cron-Secret
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["healthy"] is True


def test_monitor_health_endpoint_returns_503_when_any_scheduler_is_unhealthy():
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 4.2}), \
         patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": False, "reason": "never run", "age_seconds": None}), \
         patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 3.0}):
        with pluto_app.app.test_client() as client:
            response = client.get("/api/autonomy/monitor-health")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["data"]["healthy"] is False


def test_monitor_health_endpoint_returns_503_when_the_continuous_monitor_alone_is_unhealthy():
    """Once deployed, the continuous monitor is the PRIMARY safety
    mechanism - it going stale must flip overall_healthy even while the
    two slower schedulers are fine."""
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 4.2}), \
         patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 30.0}), \
         patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": False, "reason": "worker down", "age_seconds": 120.0}):
        with pluto_app.app.test_client() as client:
            response = client.get("/api/autonomy/monitor-health")
    assert response.status_code == 503
    assert response.get_json()["data"]["healthy"] is False


def test_monitor_health_endpoint_response_is_minimal_only_two_fields():
    """Reviewer instruction: no per-scheduler breakdown, no reasons, no
    heartbeat internals, no user/account/error/path detail - just the two
    fields."""
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 4.2}), \
         patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 30.0}), \
         patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": True, "reason": "", "age_seconds": 3.0}):
        with pluto_app.app.test_client() as client:
            response = client.get("/api/autonomy/monitor-health")
    payload = response.get_json()["data"]
    assert set(payload.keys()) == {"healthy", "last_completed_age_seconds"}
    assert payload["last_completed_age_seconds"] == 30  # the WORST (largest) of the three ages, rounded


def test_monitor_health_endpoint_age_is_null_when_nothing_has_ever_run():
    with patch.object(pluto_app, "_fast_monitor_health_status", return_value={"healthy": False, "reason": "never run", "age_seconds": None}), \
         patch.object(pluto_app, "_full_scan_health_status", return_value={"healthy": False, "reason": "never run", "age_seconds": None}), \
         patch.object(pluto_app, "_continuous_monitor_health_status", return_value={"healthy": False, "reason": "never run", "age_seconds": None}):
        with pluto_app.app.test_client() as client:
            response = client.get("/api/autonomy/monitor-health")
    payload = response.get_json()["data"]
    assert payload["last_completed_age_seconds"] is None
