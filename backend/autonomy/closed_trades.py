from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import event_journal

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
        if not stamped.get("environment"):
            stamped.update(_environment_for(user_id, stamped))
        index = next((i for i, existing in enumerate(records) if existing.get("trade_id") == trade_id), None)
        previous = records[index] if index is not None else None
        if index is not None:
            records[index] = stamped
        else:
            records.append(stamped)
        _atomic_write(path, records)
    if previous != stamped:
        _journal_close(user_id, stamped, revised=previous is not None)
    return stamped


def _environment_for(user_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """The environment of the order this trade closed; else the current one."""
    try:
        import broker_env
    except ImportError:  # pragma: no cover
        from .. import broker_env  # type: ignore
    from .overnight_orders import list_overnight_orders

    coid = record.get("entry_client_order_id") or record.get("trade_id")
    matches = [o for o in list_overnight_orders(user_id) if o.get("entry_client_order_id") == coid]
    match = next((o for o in matches if o.get("environment")), matches[0] if matches else None)
    if match is not None:
        environment, inferred = broker_env.environment_of(match)
        out = {"broker": match.get("broker") or "webull", "environment": environment}
        if inferred:
            out["environment_inferred"] = True
        return out
    return broker_env.current()


def _journal_close(user_id: str, record: Dict[str, Any], *, revised: bool) -> None:
    """position.closed (or position.close_revised on a re-reconciliation that
    changed the record). The correlation id comes from the order record
    whose entry this trade closed."""
    try:
        correlation_id = record.get("correlation_id")
        account_id = record.get("account_id")
        if not correlation_id:
            from .overnight_orders import list_overnight_orders

            coid = record.get("entry_client_order_id") or record.get("trade_id")
            matches = [o for o in list_overnight_orders(user_id) if o.get("entry_client_order_id") == coid]
            match = next((o for o in matches if o.get("lifecycle_state") != "entry_failed"), matches[0] if matches else None)
            if match is not None:
                correlation_id = event_journal.correlation_id_for(match)
                account_id = account_id or match.get("account_id")
        event_journal.emit(
            user_id, "position.close_revised" if revised else "position.closed",
            correlation_id=correlation_id or f"coid-{record.get('trade_id')}", ticker=record.get("ticker"),
            account_id=account_id, source="closed_trades", at=record.get("exit_timestamp"),
            data={
                "trade_id": record.get("trade_id"), "close_reason": record.get("close_reason"),
                "exit_type": record.get("exit_type"), "instrument_type": record.get("instrument_type"),
                "average_entry_price": record.get("average_entry_price"), "average_exit_price": record.get("average_exit_price"),
                "exited_quantity": record.get("exited_quantity"), "net_realized_pnl": record.get("net_realized_pnl"),
                "pnl_status": record.get("pnl_status"), "fees": record.get("fees"),
            },
        )
    except Exception:  # noqa: BLE001 - journaling must never affect trade recording
        pass
