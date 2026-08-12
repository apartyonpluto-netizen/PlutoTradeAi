from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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
    orders = list_overnight_orders(user_id)
    entry = {**entry, "logged_at": _now_iso()}
    orders.insert(0, entry)
    _atomic_write(_orders_file(user_id), orders)
    return entry


def replace_overnight_orders(user_id: str, orders: List[Dict[str, Any]]) -> None:
    """Overwrites the full log - used to persist in-place updates (e.g. a
    stop-loss that failed at entry time later succeeding on retry) rather
    than appending a new entry. Writes atomically - see _atomic_write."""
    _atomic_write(_orders_file(user_id), orders)
