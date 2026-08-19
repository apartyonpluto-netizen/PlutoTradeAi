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
    the scheduler has stopped calling the fast-monitor-trigger endpoint.
    Well past FAST_MONITOR_HEARTBEAT_STALE_SECONDS (5400s, recalibrated
    this session against real GitHub Actions scheduling gaps - see that
    constant's own comment), not just past the old 600s value. Forces
    "inside the scheduler's active window" so this test is deterministic
    regardless of when it actually runs - see the window-aware tests
    below for the overnight/weekend behavior itself."""
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=6000)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=5990)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat), \
         patch.object(pluto_app, "_within_scheduled_trigger_window", return_value=True):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "no completed fast-monitor run" in status["reason"]


def test_a_stale_completed_run_outside_the_trigger_window_is_still_healthy():
    """The exact bug found live in production this session: a gap that
    would be unhealthy DURING the scheduler's active window (5990s, well
    past the 5400s intraday threshold) must NOT be flagged outside it -
    an overnight/weekend gap of that size is entirely expected, since
    there was no scheduled trigger to be silent FROM."""
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(seconds=6000)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=5990)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat), \
         patch.object(pluto_app, "_within_scheduled_trigger_window", return_value=False):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is True


def test_a_multi_day_gap_outside_the_trigger_window_is_still_eventually_unhealthy():
    """The wider outside-window tolerance is not infinite - a scheduler
    that's been genuinely disabled/broken for days, not just quiet
    overnight, must still eventually be flagged. Past
    FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS (4 days)."""
    heartbeat = {
        "last_started_run_id": "run-1",
        "last_started_at": _iso(-timedelta(days=5)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(days=5)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat), \
         patch.object(pluto_app, "_within_scheduled_trigger_window", return_value=False):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False


def test_a_hung_run_is_unhealthy_even_outside_the_trigger_window():
    """Hung-run detection is deliberately NOT window-aware - a run that
    started but never completed means OUR code got stuck, not that the
    external scheduler simply hasn't fired recently, so it's flagged
    regardless of what time it's checked. Well past the tight intraday
    threshold (5400s), not the wide outside-window one."""
    heartbeat = {
        "last_started_run_id": "run-2",
        "last_started_at": _iso(-timedelta(seconds=6000)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=5950)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat), \
         patch.object(pluto_app, "_within_scheduled_trigger_window", return_value=False):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never completed" in status["reason"]


def test_never_run_at_all_is_unhealthy_even_outside_the_trigger_window():
    """"Never run even once" is worth surfacing regardless of what time
    of day/week it's checked - it may indicate a genuine
    misconfiguration, not just an unlucky check time."""
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value={}), \
         patch.object(pluto_app, "_within_scheduled_trigger_window", return_value=False):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never run" in status["reason"]


def test_started_long_ago_and_never_completed_is_unhealthy_hung():
    """A run started, and it's been long enough that it can no longer
    plausibly still be in flight - a crash mid-run, or a request that never
    returned. Well past FAST_MONITOR_HEARTBEAT_STALE_SECONDS (5400s)."""
    heartbeat = {
        "last_started_run_id": "run-2",
        "last_started_at": _iso(-timedelta(seconds=6000)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=5950)),
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
        "last_started_at": _iso(-timedelta(seconds=6000)),
        "last_completed_run_id": "run-1",
        "last_completed_at": _iso(-timedelta(seconds=10)),
    }
    with patch.object(pluto_app, "get_fast_monitor_heartbeat_status", return_value=heartbeat):
        status = pluto_app._fast_monitor_health_status()
    assert status["healthy"] is False
    assert "never completed" in status["reason"]
