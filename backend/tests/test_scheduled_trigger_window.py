from __future__ import annotations

from datetime import datetime, timezone

import app as pluto_app

# The GitHub Actions schedulers fire 13:00-21:00 UTC, Monday-Friday - see
# app.py's own comment above _SCHEDULED_TRIGGER_WINDOW_START_UTC_HOUR for
# why. These tests pin exact, known datetimes rather than relying on
# real "now" so they're deterministic regardless of when they run -
# the exact bug this whole module exists to prevent (a threshold test
# that only passes/fails depending on the real wall-clock time it
# happened to run at).


def _dt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- _within_scheduled_trigger_window ------------------------------------------


def test_inside_window_on_a_weekday():
    # Wednesday 2026-08-19, 15:00 UTC - well inside 13:00-21:00
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 19, 15, 0)) is True


def test_at_the_window_start_boundary_is_inside():
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 19, 13, 0)) is True


def test_at_the_window_end_boundary_is_outside():
    # End hour is exclusive - 21:00 UTC itself is past the window, not in it.
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 19, 21, 0)) is False


def test_just_before_the_window_start_is_outside():
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 19, 12, 59)) is False


def test_overnight_is_outside():
    # 2026-08-19 01:24 UTC - the exact real production scenario this fix
    # was found from (both schedulers correctly silent, banner false-
    # alarmed anyway before this fix).
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 19, 1, 24)) is False


def test_saturday_is_outside_even_during_daytime_hours():
    # 2026-08-22 is a Saturday.
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 22, 15, 0)) is False


def test_sunday_is_outside_even_during_daytime_hours():
    # 2026-08-23 is a Sunday.
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 23, 15, 0)) is False


def test_friday_daytime_is_inside():
    # 2026-08-21 is a Friday.
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 21, 15, 0)) is True


def test_monday_daytime_is_inside():
    # 2026-08-24 is a Monday.
    assert pluto_app._within_scheduled_trigger_window(_dt(2026, 8, 24, 15, 0)) is True


def test_defaults_to_real_current_time_when_not_given_one():
    # Just a smoke test that the no-argument form doesn't crash and
    # returns a bool - the actual value depends on real wall-clock time,
    # which is exactly why every other test here pins an explicit datetime.
    assert isinstance(pluto_app._within_scheduled_trigger_window(), bool)


# --- _effective_heartbeat_stale_threshold --------------------------------------


def test_effective_threshold_is_the_tight_one_inside_the_window():
    threshold = pluto_app._effective_heartbeat_stale_threshold(5400, 345600, _dt(2026, 8, 19, 15, 0))
    assert threshold == 5400


def test_effective_threshold_is_the_wide_one_outside_the_window():
    threshold = pluto_app._effective_heartbeat_stale_threshold(5400, 345600, _dt(2026, 8, 19, 1, 24))
    assert threshold == 345600


def test_effective_threshold_outside_the_window_on_a_weekend():
    threshold = pluto_app._effective_heartbeat_stale_threshold(5400, 345600, _dt(2026, 8, 22, 15, 0))
    assert threshold == 345600


# --- the full weekend gap is comfortably covered by the outer bound -----------


def test_the_actual_friday_evening_to_monday_morning_gap_fits_within_the_outer_bound():
    """Empirical check that FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS /
    FULL_SCAN_HEARTBEAT_MAX_GAP_SECONDS (4 days) actually covers the
    longest realistic normal gap: Friday's last run to Monday's first
    one, with real margin - not just picked as a round number."""
    friday_last_run = _dt(2026, 8, 21, 21, 0)  # Friday, window close
    monday_first_run = _dt(2026, 8, 24, 13, 0)  # Monday, window open
    gap_seconds = (monday_first_run - friday_last_run).total_seconds()
    assert gap_seconds < pluto_app.FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS
    assert gap_seconds < pluto_app.FULL_SCAN_HEARTBEAT_MAX_GAP_SECONDS
    # And with meaningful margin, not just barely under the wire.
    assert pluto_app.FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS - gap_seconds > 3600 * 12
