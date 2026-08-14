from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
_HEARTBEAT_FILE = DATA_DIR / "fast_monitor_heartbeat.json"

# GLOBAL, not per-user - the fast-monitor-trigger endpoint is one process-
# wide cron invocation that sweeps every user in a single call, so its own
# health (is the scheduler calling it at all, is it completing, how long
# is it taking) is a single, system-wide fact, not something to fragment
# per account.


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


def record_run_started() -> str:
    """Called at the very start of a fast-monitor-trigger invocation -
    returns a run_id the SAME invocation must pass to record_run_completed.
    Recording BOTH a "started" and a "completed" heartbeat - not just one
    timestamp - is what makes a HUNG run (started but never finished: a
    crash mid-run, a request that never returns, an infinite retry loop)
    detectable as unhealthy too, not only a scheduler that stopped calling
    this endpoint at all. See get_heartbeat_status / is_stale."""
    run_id = uuid.uuid4().hex
    data = _read()
    data["last_started_at"] = _now_iso()
    data["last_started_run_id"] = run_id
    _atomic_write(data)
    return run_id


def record_run_completed(
    run_id: str,
    *,
    entries_checked: int,
    still_transitional: int,
    failures_by_account: Dict[str, str],
) -> None:
    """Records that the run identified by run_id (from record_run_started)
    finished. If a NEWER run has since started (this run_id no longer
    matches last_started_run_id - e.g. two overlapping invocations, or a
    very slow run finishing after a later one already started), this is a
    no-op: a stale run's completion must never overwrite a more recent
    run's own "started" bookkeeping, which would make the heartbeat lie
    about how recently the monitor actually last started."""
    data = _read()
    if data.get("last_started_run_id") != run_id:
        return
    now = _now_iso()
    started_at = data.get("last_started_at")
    duration_seconds = None
    if started_at:
        try:
            duration_seconds = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
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
    """Read-only snapshot of everything recorded above - last-started/
    last-completed timestamps, duration, counts, and per-account failures
    from the most recent run. Returns an empty dict if the fast monitor
    has NEVER run even once (the scheduler was never configured, or this
    is a brand-new deployment) - callers must treat that the same as
    "unhealthy", not silently skip the check."""
    return _read()
