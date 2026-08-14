from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
_HEARTBEAT_FILE = DATA_DIR / "continuous_monitor_heartbeat.json"

# GLOBAL, not per-user - one heartbeat for the whole continuous-monitor
# endpoint, mirroring fast_monitor_heartbeat.py / full_scan_heartbeat.py.
#
# TWO separate signals, not one - the review explicitly asked for
# "heartbeats from both sides: worker is alive; endpoint completed
# reconciliation":
#   - record_request_received() is stamped the MOMENT an authenticated
#     request arrives at the endpoint, BEFORE any reconciliation work
#     starts. The worker process itself has NO disk access (see the
#     Option A design - it only calls this endpoint, it never touches
#     PLUTO_DATA_DIR or user credentials directly), so it cannot persist
#     its own independent "I am alive" heartbeat anywhere this app can
#     read. A fresh last_request_received_at is the closest available
#     proxy for worker-liveness without giving the worker storage it
#     structurally isn't supposed to have: it proves the worker process
#     is running, reached this service over the network, and
#     authenticated successfully - independent of whether the
#     reconciliation work that follows succeeds, hangs, or errors.
#   - record_reconciliation_completed() is stamped after the per-user
#     loop finishes - proves the ENDPOINT's own logic didn't hang or die
#     mid-request, a genuinely different failure mode from "the worker
#     never called us at all".
# A gap between these two timestamps (a recent request_received but a
# stale reconciliation_completed) specifically points at the endpoint's
# own reconciliation logic, not the worker - see
# _continuous_monitor_health_status in app.py.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> Dict[str, Any]:
    if not _HEARTBEAT_FILE.exists():
        return {}
    try:
        data = json.loads(_HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(data: Dict[str, Any]) -> None:
    _HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _HEARTBEAT_FILE.with_name(f"{_HEARTBEAT_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, _HEARTBEAT_FILE)


def record_request_received() -> str:
    """Called at the very top of the continuous-monitor endpoint, right
    after auth succeeds and BEFORE any reconciliation work - see module
    docstring. Returns a run_id the SAME request must pass to
    record_reconciliation_completed."""
    run_id = uuid.uuid4().hex
    data = _read()
    data["last_request_received_at"] = _now_iso()
    data["last_request_run_id"] = run_id
    _atomic_write(data)
    return run_id


def record_reconciliation_completed(run_id: str, *, entries_checked: int, still_transitional: int, failures_by_account: Dict[str, str]) -> None:
    """No-op if a NEWER request has since arrived - same reasoning as
    every other heartbeat module this session: a stale request's late
    completion must never overwrite a more recent request's own
    "received" bookkeeping."""
    data = _read()
    if data.get("last_request_run_id") != run_id:
        return
    now = _now_iso()
    received_at = data.get("last_request_received_at")
    duration_seconds = None
    if received_at:
        try:
            duration_seconds = (datetime.fromisoformat(now) - datetime.fromisoformat(received_at)).total_seconds()
        except ValueError:
            duration_seconds = None
    data["last_completed_at"] = now
    data["last_completed_run_id"] = run_id
    data["last_duration_seconds"] = duration_seconds
    data["last_entries_checked"] = entries_checked
    data["last_still_transitional"] = still_transitional
    data["last_failures_by_account"] = failures_by_account
    _atomic_write(data)


def get_heartbeat_status() -> Dict[str, Any]:
    return _read()
