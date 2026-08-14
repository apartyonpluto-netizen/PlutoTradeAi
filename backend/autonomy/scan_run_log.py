from __future__ import annotations

"""Durable, append-only, versioned per-user record of every autonomous
scan TICK - not to be confused with research_log.py (one record per
CANDIDATE evaluated) or overnight_orders.py (one record per order
attempt). This is one level up: one record per (user, scheduled cron
tick), covering users who were fully processed, skipped (wrong mode, no
Webull configured, ambiguous-submission freeze, etc.), or failed outright
- including users the caller never even attempted a new-entry scan for.

Exists specifically because "the cron job returned HTTP 200" does not
prove any given user's account was actually scanned - the endpoint can
(and does) skip or fail individual users while still returning 200
overall. Without a durable per-user record, that distinction is
invisible to the user themselves. See app.py's api_autonomy_cron_trigger
for every call site.

SCAN_RUN_LOG_SCHEMA_VERSION is bumped whenever a field is added, renamed,
or reinterpreted."""

import contextlib
import fcntl
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

SCAN_RUN_LOG_SCHEMA_VERSION = 1

# Records are kept per-user forever by default in overnight_orders.py and
# research_log.py, but a scan-run record is written on EVERY cron tick for
# EVERY registered user regardless of activity (even OFF-mode users with
# nothing to do) - unbounded growth here is a real disk concern in a way
# the other two logs aren't. Trimmed to the most recent N on every write.
MAX_RECORDS_PER_USER = 2000

# Secret-VALUE redaction (not just field-name pattern matching) - scrubs
# the actual current values of every secret this process holds, wherever
# they appear in free-text error strings, not only inside recognizable
# "key": "value" JSON shapes. Deliberately a different mechanism from
# integrations/webull.py's _SENSITIVE_LOG_FIELD (which redacts by FIELD
# NAME in structured SDK log lines) - an exception's str() is often plain
# English or a repr, not JSON, so field-name matching alone would miss a
# secret embedded in something like "auth failed for key abc123...".
_ENV_SECRET_KEYS = ("CRON_SECRET", "MONITOR_WORKER_SECRET", "CREDENTIAL_ENCRYPTION_KEY", "FLASK_SECRET_KEY")
# Matches "field_name": "value" where field_name looks sensitive - same
# pattern family as integrations/webull.py's own filter, duplicated here
# (not imported) so this module has no dependency on the webull
# integration and stays usable for redacting errors that never touched
# Webull at all (e.g. a settings-store read failure).
_SENSITIVE_JSON_FIELD = re.compile(
    r'("(?:[a-z0-9_-]*(?:app.?key|signature|secret|token|authorization|password)[a-z0-9_-]*)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)
_MIN_REDACTABLE_SECRET_LENGTH = 8  # avoid scrubbing short/common substrings that happen to match a trivial secret


def redact_secret_values(text: Optional[str], extra_secrets: Optional[Sequence[str]] = None) -> Optional[str]:
    """Best-effort redaction of known secret VALUES from free text -
    never raises (a redaction bug must not block writing the record, and
    must not itself leak the secret via a traceback), degrading to a
    generic placeholder if redaction itself fails."""
    if not text:
        return text
    try:
        redacted = text
        secrets_to_scrub = [os.environ.get(key, "") for key in _ENV_SECRET_KEYS]
        if extra_secrets:
            secrets_to_scrub.extend(extra_secrets)
        for secret in secrets_to_scrub:
            secret = (secret or "").strip()
            if secret and len(secret) >= _MIN_REDACTABLE_SECRET_LENGTH:
                redacted = redacted.replace(secret, "***REDACTED***")
        redacted = _SENSITIVE_JSON_FIELD.sub(r"\1***REDACTED***\2", redacted)
        return redacted
    except Exception:  # noqa: BLE001 - redaction failing must not block the write, or leak via traceback
        return "***REDACTION FAILED - original error text withheld***"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "scan_runs.json"


@contextlib.contextmanager
def _locked(path: Path):
    """Same exclusive-lock-around-read-modify-write pattern as
    research_log.py and closed_trades.py - concurrent cron ticks
    (a retry, an overlapping fire) must not lose a record."""
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


def record_scan_run(user_id: str, record: Dict[str, Any], *, extra_redact_secrets: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Appends ONE scan-tick record for this user. `record["error"]`, if
    present, is redacted automatically - callers should pass the RAW
    error text and never pre-redact it themselves (redacting here, once,
    in one place, is what makes "every error is redacted" a structural
    guarantee rather than a convention every call site has to remember)."""
    path = _log_file(user_id)
    stamped = {**record, "schema_version": SCAN_RUN_LOG_SCHEMA_VERSION, "logged_at": _now_iso()}
    if stamped.get("error"):
        stamped["error"] = redact_secret_values(str(stamped["error"]), extra_redact_secrets)
    with _locked(path):
        records = _read(user_id)
        records.append(stamped)
        if len(records) > MAX_RECORDS_PER_USER:
            records = records[-MAX_RECORDS_PER_USER:]
        _atomic_write(path, records)
    return stamped


def list_scan_runs(user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Newest-first, matching closed_trades.py's own convention - a
    dashboard reviewing "did my account get scanned" wants the most
    recent ticks first, not the oldest."""
    records = list(reversed(_read(user_id)))
    if limit is not None:
        records = records[:limit]
    return records
