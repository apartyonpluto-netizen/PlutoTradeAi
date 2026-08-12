from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _audit_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "ambiguous_resolution_audit.json"


@contextlib.contextmanager
def _locked(path: Path):
    """Same exclusive-lock-around-read-modify-write pattern as
    alerts.py's _locked - gunicorn's multiple WORKER PROCESSES (not
    threads) could otherwise race two concurrent audit appends into a lost
    update, which would be an unacceptable gap in a trail whose entire
    purpose is to be a complete, reliable record of every resolution
    decision. Locks a dedicated sidecar file, not the data file itself, so
    the lock survives the atomic temp-file-then-rename _record below
    performs while it's held."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def list_ambiguous_resolution_audit(user_id: str) -> List[Dict[str, Any]]:
    """Read-only, newest-last. There is deliberately no update or delete
    function anywhere in this module - the audit trail is append-only for
    its entire lifetime. Correcting a mistaken record means appending a
    NEW record that explains the correction (and references the original
    by its id), never editing or removing what's already there."""
    path = _audit_file(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def record_ambiguous_resolution_audit(user_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Appends one immutable record - a manual admin decision to release or
    link an entry stuck in UNKNOWN_SUBMISSION_STATE (see
    _resolve_ambiguous_submission in app.py) is exactly the kind of action
    that must always be reconstructible after the fact: who did it, when,
    what evidence they had in front of them, why, and what state the entry
    was in before and after. Stamps a unique id and returns the full
    stored record (including that id) so the caller can reference it (e.g.
    in a follow-up alert) without a second read."""
    path = _audit_file(user_id)
    stamped = {**record, "id": uuid.uuid4().hex}
    with _locked(path):
        records = list_ambiguous_resolution_audit(user_id)
        records.append(stamped)
        tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        tmp_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    return stamped
