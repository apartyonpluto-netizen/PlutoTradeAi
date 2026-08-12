from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

# The hash chain's anchor for the first record - not a real hash of
# anything, just a fixed, recognizable sentinel "there was nothing before
# this" value so record #0's prev_hash is still checkable against something.
GENESIS_HASH = "0" * 64

# A manual resolution (_resolve_ambiguous_submission in app.py) is written
# as up to two chained audit records sharing one "resolution_id", not one:
# a "started" record BEFORE any state change or protective-order work
# begins, and exactly one of "completed" (everything succeeded) or
# "failed" (something didn't) after. This module doesn't enforce that
# pairing itself - it's phase-agnostic, generic hash-chained storage, same
# as before - but find_incomplete_resolutions below relies on callers
# consistently using these three values in a record's "phase" field.
RESOLUTION_PHASE_STARTED = "resolution_started"
RESOLUTION_PHASE_COMPLETED = "resolution_completed"
RESOLUTION_PHASE_FAILED = "resolution_failed"


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
    update, or into computing the hash chain's next link off a stale
    read. Locks a dedicated sidecar file, not the data file itself, so the
    lock survives the atomic temp-file-then-rename _record below performs
    while it's held."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _canonical_json(payload: Dict[str, Any]) -> str:
    """Deterministic serialization for hashing - sorted keys and fixed
    separators so the SAME logical content always hashes to the SAME
    value regardless of dict insertion order, which Python does not
    otherwise guarantee is stable across a read-modify-write round trip."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_record(record_without_hash: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record_without_hash).encode("utf-8")).hexdigest()


def list_ambiguous_resolution_audit(user_id: str) -> List[Dict[str, Any]]:
    """Read-only, oldest-first (matches the hash chain's own direction).
    There is deliberately no update or delete function anywhere in this
    module - every write goes through record_ambiguous_resolution_audit,
    which only ever appends. Correcting a mistaken record means appending
    a NEW record that explains the correction (and references the
    original by its id), never editing or removing what's already there.

    IMPORTANT: append-only-by-this-module's-own-API is NOT the same claim
    as immutable. This file is an ordinary, locked, atomically-written JSON
    file on disk - anyone with direct filesystem access (not going through
    this module) could still edit, truncate, or delete it outright. What
    this module actually provides is TAMPER-EVIDENT storage: each record
    is hash-chained to the one before it (see record_ambiguous_resolution_audit
    and verify_audit_chain), so an edit to any record's content, a
    reordering, or a deleted MIDDLE record is detectable by recomputing the
    chain - it is not prevented, and a deleted TAIL record (the most
    recent one, with nothing after it to reference what's missing) is not
    even detectable by a pure forward chain like this one. Calling this
    "immutable" would overstate the actual guarantee; true immutability
    would require write-once storage this app does not currently have
    (e.g. an external object-lock store), which is a real, separate
    infrastructure gap, not something a local JSON file can provide on its
    own regardless of how it's locked or written."""
    path = _audit_file(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def record_ambiguous_resolution_audit(user_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Appends one tamper-EVIDENT (not immutable - see list_ambiguous_resolution_audit's
    docstring for the exact distinction) record - a manual admin decision
    to release or link an entry stuck in UNKNOWN_SUBMISSION_STATE (see
    _resolve_ambiguous_submission in app.py) is exactly the kind of action
    that must always be reconstructible and verifiable after the fact: who
    did it, when, what evidence they had in front of them, why, and what
    state the entry was in before and after.

    Every record is chained to the one before it: `prev_hash` is the
    previous record's own `record_hash` (or GENESIS_HASH for the first
    record in this user's chain), and `record_hash` is computed over the
    record's own content INCLUDING that prev_hash - so altering any field
    of any earlier record, reordering records, or deleting one from the
    middle breaks the chain from that point forward, detectable by
    verify_audit_chain. Stamps a unique id, a sequence number, and both
    hash fields, then returns the full stored record so the caller can
    reference it (e.g. in a follow-up alert) without a second read."""
    path = _audit_file(user_id)
    with _locked(path):
        records = list_ambiguous_resolution_audit(user_id)
        prev_hash = records[-1]["record_hash"] if records else GENESIS_HASH
        stamped = {**record, "id": uuid.uuid4().hex, "seq": len(records), "prev_hash": prev_hash}
        stamped["record_hash"] = _hash_record(stamped)
        records.append(stamped)
        tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        tmp_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    return stamped


def verify_audit_chain(user_id: str) -> Dict[str, object]:
    """Walks the stored chain and recomputes each record's hash, confirming
    it matches both its own stored record_hash and links correctly to the
    next record's prev_hash. This DETECTS (does not prevent - see
    list_ambiguous_resolution_audit's docstring) any edit to a record's
    content, any reordering, or any deleted MIDDLE record made outside
    this module's own append-only write path (e.g. direct disk access).

    It does NOT detect truncation of the chain's TAIL - deleting the most
    recent record(s), with nothing left after them to reference what's
    missing, is invisible to a pure forward hash chain with no externally
    anchored checkpoint. That's a structural limit of this approach, not a
    bug in this check, and is exactly why this module's docstrings never
    claim true immutability.

    Returns {"valid": bool, "broken_at_seq": int | None, "checked": int}."""
    records = list_ambiguous_resolution_audit(user_id)
    expected_prev = GENESIS_HASH
    for index, record in enumerate(records):
        if record.get("prev_hash") != expected_prev:
            return {"valid": False, "broken_at_seq": index, "checked": index}
        record_copy = dict(record)
        stored_hash: Optional[str] = record_copy.pop("record_hash", None)
        if stored_hash is None or _hash_record(record_copy) != stored_hash:
            return {"valid": False, "broken_at_seq": index, "checked": index}
        expected_prev = stored_hash
    return {"valid": True, "broken_at_seq": None, "checked": len(records)}


def find_incomplete_resolutions(user_id: str) -> List[Dict[str, Any]]:
    """Every RESOLUTION_PHASE_STARTED record whose resolution_id has no
    matching RESOLUTION_PHASE_COMPLETED or RESOLUTION_PHASE_FAILED record
    anywhere else in this user's chain - a manual resolution transaction
    that was started but never durably confirmed to have reached either
    outcome, most commonly because the process crashed or was restarted
    somewhere in between.

    This IS the durable freeze marker _resolve_ambiguous_submission's
    phased transaction relies on (see app.py): rather than a SECOND file
    that would itself need its own consistency story (what persists it,
    what clears it, what happens if THAT write fails independently of the
    audit log), an orphaned started record in the SAME already-locked,
    already-atomically-written, already-tamper-evident log this module
    provides for every other purpose serves as the signal on its own.
    Trading must stay frozen for as long as this returns anything for a
    user, REGARDLESS of what lifecycle_state the affected entry itself
    currently shows - see _has_unresolved_ambiguous_submission_locally and
    _recover_incomplete_manual_resolutions in app.py, the two callers:
    the first to keep new entries blocked, the second to actually resume
    or close out the transaction."""
    records = list_ambiguous_resolution_audit(user_id)
    started = {r["resolution_id"]: r for r in records if r.get("phase") == RESOLUTION_PHASE_STARTED and r.get("resolution_id")}
    finished_ids = {
        r["resolution_id"]
        for r in records
        if r.get("phase") in (RESOLUTION_PHASE_COMPLETED, RESOLUTION_PHASE_FAILED) and r.get("resolution_id")
    }
    return [record for resolution_id, record in started.items() if resolution_id not in finished_ids]
