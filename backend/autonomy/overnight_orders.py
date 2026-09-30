from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import event_journal

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _orders_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "overnight_orders.json"


def _stamp_environment(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Broker + environment at creation (see broker_env.py)."""
    try:
        import broker_env
    except ImportError:  # pragma: no cover
        from .. import broker_env  # type: ignore
    return broker_env.stamp(entry)


def list_overnight_orders(user_id: str) -> List[Dict[str, Any]]:
    orders_file = _orders_file(user_id)
    if not orders_file.exists():
        return []
    try:
        payload = json.loads(orders_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _atomic_write(path: Path, orders: List[Dict[str, Any]]) -> None:
    """Temp-file-then-rename, same pattern as alerts.py and
    ambiguous_resolution_audit.py - a crash or raised exception mid-write
    leaves either the OLD complete file or the NEW complete file, never a
    truncated/corrupted one. This matters beyond just this file's own
    integrity: _resolve_ambiguous_submission's phased audit trail
    (resolution_started/completed/failed - see ambiguous_resolution_audit.py)
    depends on "the disk write either fully lands or doesn't happen at
    all" being true HERE too, not only in the audit log itself - a
    partially-written overnight_orders.json would otherwise be read back
    by list_overnight_orders' `except json.JSONDecodeError: return []` as
    "zero orders", silently losing every OTHER tracked entry along with
    the one actually being updated."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(orders, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def record_overnight_order(user_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Inserts a new record, or - when `entry` carries a record_id already on
    disk - replaces that record in place, keeping its original logged_at.

    The in-place replace is what makes write-ahead persistence work: an
    entry is recorded once BEFORE its order is sent to the broker (see
    app._persist_entry_before_submission) and again, same dict and same
    record_id, once the outcome is known. Keying on record_id rather than
    entry_client_order_id is deliberate - a same-day retry of a failed
    entry reuses the same deterministic client_order_id, and it must get
    its own record rather than overwrite the earlier attempt's history."""
    orders = list_overnight_orders(user_id)
    record_id = str(entry.get("record_id") or "")
    if record_id:
        for index, existing in enumerate(orders):
            if str(existing.get("record_id") or "") == record_id:
                merged = {**entry, "logged_at": existing.get("logged_at") or _now_iso()}
                for field in ("broker", "environment"):
                    if existing.get(field):
                        merged[field] = existing[field]
                orders[index] = merged
                _atomic_write(_orders_file(user_id), orders)
                event_journal.journal_record_changes(user_id, [existing], [merged])
                return merged
    entry = _stamp_environment({**entry, "logged_at": _now_iso()})
    orders.insert(0, entry)
    _atomic_write(_orders_file(user_id), orders)
    event_journal.journal_record_changes(user_id, [], [entry])
    return entry


def replace_overnight_orders(user_id: str, orders: List[Dict[str, Any]]) -> None:
    """Overwrites the full log - used to persist in-place updates (e.g. a
    stop-loss that failed at entry time later succeeding on retry) rather
    than appending a new entry. Writes atomically - see _atomic_write.

    Lifecycle steps that are new relative to what was on disk are journaled
    after the write (event_journal.py)."""
    before = list_overnight_orders(user_id)
    known = {str(o.get("record_id") or o.get("entry_client_order_id") or "") for o in before}
    for order in orders:
        key = str(order.get("record_id") or order.get("entry_client_order_id") or "")
        if not key or key not in known:
            _stamp_environment(order)  # new records only; stored legacy records are never rewritten
    _atomic_write(_orders_file(user_id), orders)
    event_journal.journal_record_changes(user_id, before, orders)
