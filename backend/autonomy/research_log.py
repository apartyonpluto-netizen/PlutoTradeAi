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
written under which schema rather than silently misreading old ones.

Storage is JSONL (one JSON object per line) appended under the same flock
the previous full-file rewrite used. A leading `[` means an older JSON
array; the next append rewrites that file as JSONL once. The log is capped
(MAX_RECORDS or MAX_BYTES, whichever binds first) and only rewritten when
a new record would pass the cap — ordinary appends do not load a growing
file into a pretty-printed rewrite.
"""

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

RESEARCH_LOG_SCHEMA_VERSION = 2
MAX_RECORDS = 5000
MAX_BYTES = 8 * 1024 * 1024


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


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _parse_records(text: str) -> List[Dict[str, Any]]:
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    records: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


def _read(user_id: str) -> List[Dict[str, Any]]:
    return _parse_records(_read_text(_log_file(user_id)))


def _atomic_write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    body = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
    tmp_path.write_text(body, encoding="utf-8")
    os.replace(tmp_path, path)


def _fits(records: List[Dict[str, Any]]) -> bool:
    if len(records) > MAX_RECORDS:
        return False
    size = sum(len(json.dumps(record, separators=(",", ":"))) + 1 for record in records)
    return size <= MAX_BYTES


def _trim(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the newest records that fit both caps. Append order is oldest-first."""
    kept = list(records)
    while kept and not _fits(kept):
        kept.pop(0)
    return kept


def record_research_decision(user_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Appends ONE candidate-evaluation record. Ordinary writes append a
    single line. The file is rewritten only to migrate an old JSON array
    or to drop the oldest lines once the cap is crossed."""
    path = _log_file(user_id)
    stamped = {**record, "schema_version": RESEARCH_LOG_SCHEMA_VERSION, "logged_at": _now_iso()}
    line = json.dumps(stamped, separators=(",", ":")) + "\n"
    with _locked(path):
        text = _read_text(path)
        stripped = text.lstrip()
        is_legacy_array = stripped.startswith("[")
        records = _parse_records(text)
        records.append(stamped)
        if is_legacy_array or not _fits(records):
            _atomic_write_jsonl(path, _trim(records))
        else:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
    return stamped


def list_research_decisions(user_id: str) -> List[Dict[str, Any]]:
    """Oldest-first (append order). Reads either JSONL or a legacy JSON
    array so a file written before the JSONL change still loads."""
    path = _log_file(user_id)
    with _locked(path):
        return _parse_records(_read_text(path))
