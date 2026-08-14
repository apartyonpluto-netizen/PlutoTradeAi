from __future__ import annotations

from pathlib import Path

import fast_monitor_heartbeat as heartbeat


def _isolate_heartbeat_file(tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
    """The heartbeat store is a single GLOBAL file (not per-user), so every
    test must point it at its own tmp_path - otherwise tests running in the
    same pytest session would all read/write the same file and pollute each
    other's assertions."""
    monkeypatch.setattr(heartbeat, "_HEARTBEAT_FILE", tmp_path / "fast_monitor_heartbeat.json")


def test_get_heartbeat_status_is_empty_when_never_run(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    assert heartbeat.get_heartbeat_status() == {}


def test_record_run_started_populates_started_fields_only(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = heartbeat.record_run_started()
    status = heartbeat.get_heartbeat_status()
    assert status["last_started_run_id"] == run_id
    assert status["last_started_at"]
    assert "last_completed_run_id" not in status
    assert "last_completed_at" not in status


def test_record_run_completed_populates_completion_fields(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = heartbeat.record_run_started()
    heartbeat.record_run_completed(
        run_id,
        entries_checked=7,
        still_transitional=2,
        failures_by_account={"acct-1": "timeout"},
    )
    status = heartbeat.get_heartbeat_status()
    assert status["last_completed_run_id"] == run_id
    assert status["last_completed_at"]
    assert status["last_duration_seconds"] is not None
    assert status["last_duration_seconds"] >= 0
    assert status["last_entries_checked"] == 7
    assert status["last_still_transitional"] == 2
    assert status["last_failures_by_account"] == {"acct-1": "timeout"}


def test_record_run_completed_is_a_noop_for_a_stale_run_id(tmp_path, monkeypatch):
    """A slow run finishing AFTER a newer run has already started must not
    overwrite the newer run's own started bookkeeping - otherwise the
    heartbeat would falsely claim the monitor is healthy/idle when a more
    recent invocation is actually still in flight (or itself hung)."""
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    stale_run_id = heartbeat.record_run_started()
    newer_run_id = heartbeat.record_run_started()
    assert stale_run_id != newer_run_id

    heartbeat.record_run_completed(
        stale_run_id,
        entries_checked=99,
        still_transitional=99,
        failures_by_account={},
    )

    status = heartbeat.get_heartbeat_status()
    assert status["last_started_run_id"] == newer_run_id
    assert "last_completed_run_id" not in status
    assert "last_completed_at" not in status


def test_record_run_completed_for_the_current_run_after_a_stale_one_still_works(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    stale_run_id = heartbeat.record_run_started()
    newer_run_id = heartbeat.record_run_started()

    heartbeat.record_run_completed(stale_run_id, entries_checked=1, still_transitional=1, failures_by_account={})
    heartbeat.record_run_completed(newer_run_id, entries_checked=5, still_transitional=0, failures_by_account={})

    status = heartbeat.get_heartbeat_status()
    assert status["last_completed_run_id"] == newer_run_id
    assert status["last_entries_checked"] == 5
