from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as pluto_app


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_never_run_is_unhealthy():
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value={}):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never run" in status["reason"]


def test_recent_clean_completion_is_healthy():
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=30)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=20)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is True
    assert status["reason"] == ""


def test_stale_completed_run_is_unhealthy():
    """The most recent run finished cleanly, but that was a long time ago -
    the scheduler has stopped calling the fast-monitor-trigger endpoint."""
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=900)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=890)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "no completed fast-monitor run" in status["reason"]


def test_started_long_ago_and_never_completed_is_unhealthy_hung():
    """A run started, and it's been long enough that it can no longer
    plausibly still be in flight - a crash mid-run, or a request that never
    returned."""
    heartbeat = {
        "last_started_run_id": "run-2",
        "last_started_at": _iso(-timedelta(seconds=900)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=850)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never completed" in status["reason"]


def test_started_recently_and_still_in_flight_is_not_yet_unhealthy():
    """A run that started a few seconds ago and hasn't completed yet is
    plausibly just still running - it should not be flagged unhealthy
    before it's had a fair chance to finish."""
    heartbeat = {
        "last_started_run_id": "run-2",
        "last_started_at": _iso(-timedelta(seconds=5)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=120)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is True


def test_never_completed_at_all_uses_started_time_as_reference():
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=5)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is True


def test_a_stale_runs_late_completion_does_not_clobber_a_newer_runs_hang_verdict():
    """Mirrors fast_monitor_heartbeat.record_run_completed's own no-op-for-
    stale-run-id guarantee at the health-check layer: last_completed_run_id
    belonging to an OLDER run than last_started_run_id must still be judged
    against the NEWER run's start time, not treated as evidence of recent
    health."""
    heartbeat = {
        "last_started_run_id": "run-2",
        "last_started_at": _iso(-timedelta(seconds=900)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=10)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never completed" in status["reason"]
