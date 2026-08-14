from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from autonomy.scan_run_log import (
    SCAN_RUN_LOG_SCHEMA_VERSION,
    list_scan_runs,
    record_scan_run,
    redact_secret_values,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


# --- record / list --------------------------------------------------------------


def test_record_and_list_round_trip(user_id):
    record_scan_run(user_id, {"status": "processed", "account_mode": "AUTONOMOUS"})
    runs = list_scan_runs(user_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "processed"
    assert runs[0]["account_mode"] == "AUTONOMOUS"


def test_every_record_is_stamped_with_schema_version_and_logged_at(user_id):
    record_scan_run(user_id, {"status": "skipped"})
    run = list_scan_runs(user_id)[0]
    assert run["schema_version"] == SCAN_RUN_LOG_SCHEMA_VERSION
    assert run.get("logged_at")


def test_list_is_newest_first(user_id):
    record_scan_run(user_id, {"status": "processed", "run_id": "first"})
    record_scan_run(user_id, {"status": "processed", "run_id": "second"})
    record_scan_run(user_id, {"status": "processed", "run_id": "third"})
    run_ids = [r["run_id"] for r in list_scan_runs(user_id)]
    assert run_ids == ["third", "second", "first"]


def test_list_respects_limit(user_id):
    for i in range(5):
        record_scan_run(user_id, {"status": "processed", "run_id": str(i)})
    assert len(list_scan_runs(user_id, limit=2)) == 2


def test_list_is_empty_for_a_user_with_no_records(user_id):
    assert list_scan_runs(user_id) == []


def test_a_users_records_are_isolated_from_another_users(user_id, other_user_id):
    record_scan_run(user_id, {"status": "processed"})
    record_scan_run(other_user_id, {"status": "failed"})
    assert [r["status"] for r in list_scan_runs(user_id)] == ["processed"]
    assert [r["status"] for r in list_scan_runs(other_user_id)] == ["failed"]


def test_records_are_capped_and_trimmed_to_the_most_recent(user_id, monkeypatch):
    import autonomy.scan_run_log as scan_run_log_module
    monkeypatch.setattr(scan_run_log_module, "MAX_RECORDS_PER_USER", 3)
    for i in range(5):
        record_scan_run(user_id, {"status": "processed", "run_id": str(i)})
    runs = list_scan_runs(user_id)
    assert len(runs) == 3
    assert [r["run_id"] for r in runs] == ["4", "3", "2"]  # oldest two dropped


# --- redaction --------------------------------------------------------------------


def test_redact_secret_values_scrubs_a_known_env_secret(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "super-secret-cron-value-123")
    text = "request failed: bad header X-Cron-Secret=super-secret-cron-value-123"
    redacted = redact_secret_values(text)
    assert "super-secret-cron-value-123" not in redacted
    assert "***REDACTED***" in redacted


def test_redact_secret_values_scrubs_extra_supplied_secrets():
    text = "auth error using app_key=abcdef1234567890"
    redacted = redact_secret_values(text, extra_secrets=["abcdef1234567890"])
    assert "abcdef1234567890" not in redacted


def test_redact_secret_values_scrubs_json_shaped_sensitive_fields():
    text = '{"app_key": "AKIA_LOOKS_LIKE_A_KEY", "note": "fine"}'
    redacted = redact_secret_values(text)
    assert "AKIA_LOOKS_LIKE_A_KEY" not in redacted
    assert '"note": "fine"' in redacted  # non-sensitive fields untouched


def test_redact_secret_values_ignores_short_substrings():
    # Deliberately short "secret" (e.g. an unset/empty env var or a tiny
    # test value) must not cause over-eager redaction of common text.
    text = "the year 2026 was fine"
    redacted = redact_secret_values(text, extra_secrets=["26"])
    assert redacted == text


def test_redact_secret_values_handles_none_and_empty():
    assert redact_secret_values(None) is None
    assert redact_secret_values("") == ""


def test_record_scan_run_redacts_the_error_field_automatically(user_id, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "another-super-secret-value-456")
    record_scan_run(user_id, {"status": "failed", "error": "boom: another-super-secret-value-456 leaked"})
    run = list_scan_runs(user_id)[0]
    assert "another-super-secret-value-456" not in run["error"]
    assert "***REDACTED***" in run["error"]


def test_record_scan_run_redacts_using_extra_secrets_argument(user_id):
    record_scan_run(
        user_id, {"status": "failed", "error": "webull rejected app_secret=zzz999xyz111"},
        extra_redact_secrets=["zzz999xyz111"],
    )
    run = list_scan_runs(user_id)[0]
    assert "zzz999xyz111" not in run["error"]


def test_record_scan_run_leaves_a_missing_error_field_alone(user_id):
    record_scan_run(user_id, {"status": "processed", "error": None})
    run = list_scan_runs(user_id)[0]
    assert run["error"] is None


# --- concurrency (real processes, matching gunicorn's multi-process model) --------


def _write_worker_command(user_id: str, run_id: str) -> list[str]:
    script = (
        "import sys; "
        "from autonomy.scan_run_log import record_scan_run; "
        "record_scan_run(sys.argv[1], {'status': 'processed', 'run_id': sys.argv[2]})"
    )
    return [sys.executable, "-c", script, user_id, run_id]


def test_concurrent_writes_across_real_processes_lose_no_records(user_id):
    """Same real-process concurrency proof as test_research_log.py and
    test_webull_stop_orders_locking.py - the actual threat model is
    multiple GUNICORN WORKER PROCESSES, not threads within one
    interpreter. A scan-run record silently lost to a lost update would
    be exactly the kind of gap this log exists to prevent."""
    run_ids = [f"run-{i}" for i in range(20)]
    commands = [_write_worker_command(user_id, run_id) for run_id in run_ids]
    env = dict(os.environ)
    processes = [subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for cmd in commands]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"worker process failed: {stderr.decode('utf-8', errors='replace')}"

    recorded_run_ids = {r["run_id"] for r in list_scan_runs(user_id)}
    assert recorded_run_ids == set(run_ids)
