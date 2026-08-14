from __future__ import annotations

from pathlib import Path

import full_scan_heartbeat as heartbeat


def _isolate_heartbeat_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(heartbeat, "_HEARTBEAT_FILE", tmp_path / "full_scan_heartbeat.json")


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


def test_record_run_completed_populates_completion_fields(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = heartbeat.record_run_started()
    heartbeat.record_run_completed(run_id, ran_for_users=4, failures_by_account={"acct-1": "timeout"})
    status = heartbeat.get_heartbeat_status()
    assert status["last_completed_run_id"] == run_id
    assert status["last_ran_for_users"] == 4
    assert status["last_failures_by_account"] == {"acct-1": "timeout"}
    assert status["last_duration_seconds"] >= 0


def test_record_run_completed_is_a_noop_for_a_stale_run_id(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    stale_run_id = heartbeat.record_run_started()
    newer_run_id = heartbeat.record_run_started()
    heartbeat.record_run_completed(stale_run_id, ran_for_users=99, failures_by_account={})
    status = heartbeat.get_heartbeat_status()
    assert status["last_started_run_id"] == newer_run_id
    assert "last_completed_run_id" not in status
