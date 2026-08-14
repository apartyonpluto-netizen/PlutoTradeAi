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


class ContinuousMonitorTickAlreadyRunningError(PlutoTradeError):
    """Raised when a continuous-monitor-tick request arrives while a
    PRIOR tick's per-user loop is still running - belt-and-suspenders
    against overlapping requests at the ENDPOINT level, on top of (not
    instead of) the worker's own "never fire the next request before the
    previous one returns" discipline and the per-account
    user_scan_lock/ScanAlreadyRunningError that already makes any actual
    per-user DATA race structurally impossible regardless. This is GLOBAL
    (one lock, not per-user), since the entire per-user loop for one tick
    is the unit of work being protected against duplication, not any
    single account's data."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=409, error_code="continuous_monitor_tick_already_running", details={})


@contextmanager
def continuous_monitor_tick_lock() -> Iterator[None]:
    """GLOBAL (not per-user) OS-level file lock, non-blocking - same
    fcntl.flock mechanics as user_scan_lock, but scoped to the whole
    continuous-monitor-tick endpoint rather than one account. See
    ContinuousMonitorTickAlreadyRunningError's docstring for why this
    exists ON TOP OF, not instead of, the per-account locks the per-user
    reconciliation work inside the tick already holds."""
    lock_path = DATA_DIR / ".continuous_monitor_tick.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ContinuousMonitorTickAlreadyRunningError("A continuous-monitor tick is already running - skipping this request.")
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()
