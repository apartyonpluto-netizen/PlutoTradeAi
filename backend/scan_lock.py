from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.errors import PlutoTradeError

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


class ScanAlreadyRunningError(PlutoTradeError):
    """Raised when a scan is requested for a user who already has one in
    flight - two overlapping triggers (a retry, a manual click landing
    mid-cron-tick, GitHub Actions firing twice) must never both pass the
    risk/position checks and place independent orders. Subclasses
    PlutoTradeError so the manual run-scan endpoint's existing exception
    handling surfaces this as a friendly 409, not a scary 500."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=409, error_code="scan_already_running", details={})


@contextmanager
def user_scan_lock(user_id: str) -> Iterator[None]:
    """OS-level file lock (fcntl.flock), not an in-process threading.Lock -
    gunicorn runs multiple worker processes, so a lock that only lives in
    one process's memory would not stop a second worker from running the
    same user's scan concurrently. Non-blocking: a second caller finds the
    lock held and fails immediately with ScanAlreadyRunningError rather than
    queueing, so the cron-trigger endpoint's per-user loop can skip that
    user and keep moving instead of stalling the whole batch."""
    if not user_id:
        raise ValueError("user_id is required.")
    lock_dir = USER_DATA_ROOT / user_id
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".scan.lock"

    fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScanAlreadyRunningError(f"A scan is already running for user {user_id} - skipping this trigger.")
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()
