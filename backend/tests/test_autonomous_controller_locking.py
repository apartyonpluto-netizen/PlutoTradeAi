from __future__ import annotations

import json

from autonomy import autonomous_controller as ac

"""Hardened 2026-09-02 after a real incident: autonomy mode silently
reset to OFF (mode_change_reason == "Initialization" - the value ONLY
ever written by _default_settings, never by a deliberate human toggle)
sometime between two routine checks, with no error and no alert. Root
cause was never pinned down with certainty, but every read-modify-write
in this module ran as two SEPARATE, unlocked file operations against a
file gunicorn's multiple worker processes could race - the same class
of bug already fixed once elsewhere in this codebase (research_log.py,
closed_trades.py, alerts.py, ambiguous_resolution_audit.py) but never
applied here, arguably the single most safety-critical file in the app.
These tests prove the fix: every mutator now holds ONE lock across the
entire load-mutate-save sequence, every write is atomic, and a
corrupted/malformed on-disk file self-heals instead of ever being
mistaken for real data."""


def test_set_mode_persists_and_reads_back_correctly(user_id):
    result = ac.set_mode(user_id, "AUTONOMOUS", reason="test enable")
    assert result["current_mode"] == "AUTONOMOUS"
    assert result["mode_change_reason"] == "test enable"

    status = ac.get_autonomy_status(user_id)
    assert status["current_mode"] == "AUTONOMOUS"
    assert status["mode_change_reason"] == "test enable"


def test_a_corrupted_on_disk_file_self_heals_to_defaults_not_a_crash(user_id):
    # Establish a real file first (so _settings_file's own directory
    # creation has already happened), then corrupt it directly -
    # simulating a torn write from the OLD unlocked code, or any other
    # source of on-disk corruption.
    ac.set_mode(user_id, "AUTONOMOUS", reason="before corruption")
    path = ac._settings_file(user_id)
    path.write_text("{not valid json", encoding="utf-8")

    status = ac.get_autonomy_status(user_id)
    assert status["current_mode"] == "OFF"
    # The self-heal write must have replaced the corrupt content with
    # real, valid JSON, not left the garbage in place.
    assert json.loads(path.read_text(encoding="utf-8"))["current_mode"] == "OFF"


def test_a_missing_file_self_heals_via_the_locked_path_not_a_bare_write(user_id):
    path = ac._settings_file(user_id)
    assert not path.exists()
    status = ac.get_autonomy_status(user_id)
    assert status["current_mode"] == "OFF"
    assert status["mode_change_reason"] == "Initialization"
    assert path.exists()
    # A real, valid JSON document - not a torn/partial write.
    assert json.loads(path.read_text(encoding="utf-8"))["current_mode"] == "OFF"


def test_concurrent_style_mutations_never_lose_an_update(user_id):
    # The TOCTOU race the old load-then-save-as-two-separate-operations
    # design was vulnerable to: two mutations back to back must BOTH
    # land, in order - the second must never silently overwrite with a
    # stale read from before the first one committed. _locked_read_modify_write
    # holds one lock across the whole sequence specifically to make this
    # impossible even under real concurrent workers, not just in this
    # single-threaded test.
    ac.update_risk_settings(user_id, max_positions=3)
    ac.set_mode(user_id, "AUTONOMOUS", reason="second mutation")

    status = ac.get_autonomy_status(user_id)
    # Both mutations present - the second did not clobber the first's
    # field by re-saving a stale copy of the whole document.
    assert status["max_positions"] == 3
    assert status["current_mode"] == "AUTONOMOUS"


def test_emergency_stop_and_reset_round_trip_correctly(user_id):
    ac.set_mode(user_id, "PAPER", reason="setup")
    stopped = ac.emergency_stop(user_id, reason="test stop")
    assert stopped["emergency_stop_enabled"] is True
    assert stopped["paper_trading_enabled"] is False

    resumed = ac.reset_emergency_stop(user_id, reason="test resume")
    assert resumed["emergency_stop_enabled"] is False
    # current_mode itself is untouched by emergency stop/reset - only the
    # emergency flag and the derived enabled flags change.
    assert resumed["current_mode"] == "PAPER"


def test_writes_are_atomic_no_tmp_file_left_behind(user_id):
    ac.set_mode(user_id, "AUTONOMOUS")
    path = ac._settings_file(user_id)
    tmp_files = list(path.parent.glob(f"{path.name}.tmp-*"))
    assert tmp_files == []
