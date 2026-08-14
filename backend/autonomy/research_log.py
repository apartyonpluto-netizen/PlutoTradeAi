from __future__ import annotations

"""Durable, append-only, versioned research-decision log.

Deliberately SEPARATE from overnight_orders.py (the user-facing trade
journal) and closed_trades.py (realized outcomes): this log exists so a
future analysis pass (e.g. backtesting regime.py's VIX shadow mapping
before it could ever be considered for activation) can query EVERY
candidate the autonomous scan evaluated in a session - trades taken,
trades skipped for any reason, candidates that never cleared the
confidence floor, and candidates rejected for risk/buying-power reasons -
not just the subset that happened to reach order submission. A dataset
built only from placed/near-placed orders is subject to survivorship and
selection bias; this log exists specifically to avoid that.

This is a research artifact, not an order record - it is intentionally
never rendered in the primary trade-journal UI (see templates/trade_journal.html,
which reads overnight_orders.py instead).

RESEARCH_LOG_SCHEMA_VERSION is bumped whenever a field is added, renamed,
or reinterpreted, so a later analysis pass can tell which records were
written under which schema rather than silently misreading old ones."""

import contextlib
import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

RESEARCH_LOG_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "research_decisions.json"


@contextlib.contextmanager
def _locked(path: Path):
    """Same exclusive-lock-around-read-modify-write pattern as
    closed_trades.py, alerts.py, and ambiguous_resolution_audit.py -
    gunicorn's multiple WORKER PROCESSES could otherwise race two
    concurrent writes (e.g. two scan ticks overlapping briefly during a
    deploy) into a lost record - exactly the kind of silent gap this log
    exists to prevent, so it gets the same locking discipline as the
    trading-critical stores, not the lighter-weight pattern
    overnight_orders.py uses."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read(user_id: str) -> List[Dict[str, Any]]:
    path = _log_file(user_id)
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


def record_research_decision(user_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Appends ONE candidate-evaluation record - never edits or removes an
    existing one. Every caller in app.py's autonomous scan is expected to
    call this once per opportunity/candidate it evaluates, regardless of
    how early that candidate was rejected (confidence floor, position
    limit, zero-quantity sizing, LLM veto, or successful submission) -
    see _run_autonomous_trade_scan_locked's own comments at each call
    site for why every branch is covered."""
    path = _log_file(user_id)
    stamped = {**record, "schema_version": RESEARCH_LOG_SCHEMA_VERSION, "logged_at": _now_iso()}
    with _locked(path):
        records = _read(user_id)
        records.append(stamped)
        _atomic_write(path, records)
    return stamped


def list_research_decisions(user_id: str) -> List[Dict[str, Any]]:
    """Oldest-first (append order) - read-only, for future analysis
    tooling (e.g. backtesting regime.py's shadow mapping against
    autonomy/closed_trades.py outcomes once enough history exists)."""
    return _read(user_id)
