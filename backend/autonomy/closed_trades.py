from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _store_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "closed_trades.json"


@contextlib.contextmanager
def _locked(path: Path):
    """Same exclusive-lock-around-read-modify-write pattern as alerts.py
    and ambiguous_resolution_audit.py - gunicorn's multiple WORKER
    PROCESSES could otherwise race two concurrent closed-trade writes
    (e.g. a monitor tick and a restart-recovery pass both concluding the
    same position closed) into a lost update or a duplicate record
    despite the upsert-by-trade_id logic below, which only works if reads
    and writes are serialized against each other."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read(user_id: str) -> List[Dict[str, Any]]:
    path = _store_file(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _atomic_write(path: Path, records: List[Dict[str, Any]]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def list_closed_trades(user_id: str) -> List[Dict[str, Any]]:
    """Newest-first - every durably closed trade for this user. Read-only;
    see record_closed_trade for the only way to write one."""
    return list(reversed(_read(user_id)))


def get_closed_trade(user_id: str, trade_id: str) -> Optional[Dict[str, Any]]:
    for record in _read(user_id):
        if record.get("trade_id") == trade_id:
            return record
    return None


def record_closed_trade(user_id: str, trade_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Durably records one fully-closed position - UPSERT by trade_id, not
    append-only: this is deliberately idempotent, not just
    duplicate-tolerant. trade_id is expected to be the entry's own
    entry_client_order_id (already deterministic and unique per entry -
    see order_lifecycle.deterministic_client_order_id) - restart recovery
    that re-runs the SAME exit reconciliation after a crash (see
    _reconcile_position_exit in app.py) must UPDATE the same record, never
    create a second one, so "closed trade logging is idempotent across
    restart" holds structurally, not just by convention.

    Every write is a full read-modify-write under the SAME exclusive lock
    (see _locked) and an atomic temp-file-then-rename (see _atomic_write) -
    a crash mid-write leaves either the OLD or the NEW complete file,
    never a truncated one that could silently drop every OTHER user's
    already-recorded closed trades.

    Returns the stored record (including trade_id) so the caller can
    reference it (e.g. in a follow-up alert) without a second read."""
    if not trade_id:
        raise ValueError("trade_id is required.")
    path = _store_file(user_id)
    with _locked(path):
        records = _read(user_id)
        stamped = {**record, "trade_id": trade_id}
        index = next((i for i, existing in enumerate(records) if existing.get("trade_id") == trade_id), None)
        if index is not None:
            records[index] = stamped
        else:
            records.append(stamped)
        _atomic_write(path, records)
    return stamped
