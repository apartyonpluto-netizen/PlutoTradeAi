from __future__ import annotations

from pathlib import Path

import continuous_monitor_heartbeat as cmh


def _isolate_heartbeat_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cmh, "_HEARTBEAT_FILE", tmp_path / "continuous_monitor_heartbeat.json")


def test_get_heartbeat_status_is_empty_when_never_run(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    assert cmh.get_heartbeat_status() == {}


def test_record_request_received_populates_received_fields_only(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = cmh.record_request_received()
    status = cmh.get_heartbeat_status()
    assert status["last_request_run_id"] == run_id
    assert status["last_request_received_at"]
    assert "last_completed_run_id" not in status


def test_record_reconciliation_completed_populates_completion_fields(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = cmh.record_request_received()
    cmh.record_reconciliation_completed(run_id, entries_checked=5, still_transitional=2, failures_by_account={"acct-1": "timeout"})
    status = cmh.get_heartbeat_status()
    assert status["last_completed_run_id"] == run_id
    assert status["last_entries_checked"] == 5
    assert status["last_still_transitional"] == 2
    assert status["last_failures_by_account"] == {"acct-1": "timeout"}
    assert status["last_duration_seconds"] >= 0


def test_record_reconciliation_completed_is_a_noop_for_a_stale_run_id(tmp_path, monkeypatch):
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    stale_run_id = cmh.record_request_received()
    newer_run_id = cmh.record_request_received()
    cmh.record_reconciliation_completed(stale_run_id, entries_checked=99, still_transitional=99, failures_by_account={})
    status = cmh.get_heartbeat_status()
    assert status["last_request_run_id"] == newer_run_id
    assert "last_completed_run_id" not in status


def test_a_lock_skip_still_records_received_without_completing(tmp_path, monkeypatch):
    """Models the endpoint's own 409-lock-conflict path: record_request_received
    is called unconditionally, but record_reconciliation_completed is
    never reached - "worker reached us" must still be true even though no
    reconciliation happened this request."""
    _isolate_heartbeat_file(tmp_path, monkeypatch)
    run_id = cmh.record_request_received()
    status = cmh.get_heartbeat_status()
    assert status["last_request_run_id"] == run_id
    assert "last_completed_run_id" not in status
