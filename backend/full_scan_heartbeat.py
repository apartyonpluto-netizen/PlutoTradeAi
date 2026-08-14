from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
_HEARTBEAT_FILE = DATA_DIR / "full_scan_heartbeat.json"

# GLOBAL, not per-user - mirrors fast_monitor_heartbeat.py exactly, but
# tracks the FULL 5-minute autonomous-scan cron job (/api/autonomy/cron-trigger)
# instead of the faster reconciliation-only one. Having BOTH schedulers
# record their own independent heartbeat, and CROSS-checking each other
# (see _alert_admins_fast_monitor_unhealthy_if_needed and
# _alert_admins_full_scan_unhealthy_if_needed in app.py), is what makes
# either scheduler stopping detectable by the OTHER one - a single
# scheduler alerting only about itself can never notice its own silence.


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
    run_id = uuid.uuid4().hex
    data = _read()
    data["last_started_at"] = _now_iso()
    data["last_started_run_id"] = run_id
    _atomic_write(data)
    return run_id


def record_run_completed(run_id: str, *, ran_for_users: int, failures_by_account: Dict[str, str]) -> None:
    """No-op if a NEWER run has since started - same reasoning as
    fast_monitor_heartbeat.record_run_completed: a stale run's late
    completion must never overwrite a more recent run's own started
    bookkeeping."""
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
    data["last_ran_for_users"] = ran_for_users
    data["last_failures_by_account"] = failures_by_account
    _atomic_write(data)


def get_heartbeat_status() -> Dict[str, Any]:
    return _read()
