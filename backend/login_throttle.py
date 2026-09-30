"""Failed sign-in throttling, shared across gunicorn workers (a small JSON
file under PLUTO_DATA_DIR, read-modify-write under an flock).

After MAX_FAILURES failed attempts within WINDOW_SECONDS for the same
username, or for the same client address, further attempts are refused
until the window passes. A successful sign-in clears that username's
failures. Only failures are stored, as salted hashes of the key - never
passwords, and not the raw username or address."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
MAX_FAILURES = 8
WINDOW_SECONDS = 15 * 60


def _file() -> Path:
    path = DATA_DIR / "security"
    path.mkdir(parents=True, exist_ok=True)
    return path / "login_failures.json"


def _key(kind: str, value: str) -> str:
    salt = os.environ.get("FLASK_SECRET_KEY", "pluto")
    return kind + ":" + hashlib.sha256(f"{salt}|{kind}|{value.strip().lower()}".encode()).hexdigest()[:32]


@contextlib.contextmanager
def _locked():
    path = _file()
    with open(path.with_name(path.name + ".lock"), "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data: Dict[str, List[float]] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            now = time.time()
            data = {k: [t for t in v if now - t < WINDOW_SECONDS] for k, v in data.items()}
            data = {k: v for k, v in data.items() if v}
            yield data
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fail_open(fn):
    """A storage problem must never lock people out of signing in: the
    throttle is skipped (and logged) rather than failing the request."""
    import functools
    import logging

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:  # noqa: BLE001
            logging.getLogger(__name__).warning("login throttle unavailable: %s", error)
            return None
    return wrapper


@_fail_open
def blocked_for(username: str, address: str) -> Optional[int]:
    """Seconds until another attempt is allowed, or None if allowed now."""
    now = time.time()
    with _locked() as data:
        waits = []
        for key in (_key("user", username), _key("addr", address)):
            times = data.get(key) or []
            if len(times) >= MAX_FAILURES:
                waits.append(int(WINDOW_SECONDS - (now - times[-MAX_FAILURES])) + 1)
        return max(waits) if waits else None


@_fail_open
def record_failure(username: str, address: str) -> None:
    with _locked() as data:
        for key in (_key("user", username), _key("addr", address)):
            data.setdefault(key, []).append(time.time())


@_fail_open
def record_success(username: str) -> None:
    with _locked() as data:
        data.pop(_key("user", username), None)


def safe_next_path(value: str) -> Optional[str]:
    """A same-site path only: '/x' yes; '//host', '/\\host', 'http://...' no."""
    value = value or ""
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return None
    return value
