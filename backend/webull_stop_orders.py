from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _store_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "webull_active_stops.json"


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive lock around an entire read-modify-write cycle, same
    pattern as alerts.py/closed_trades.py/ambiguous_resolution_audit.py -
    _write's temp-file-then-rename alone only prevents a TORN/corrupt file
    on a crash mid-write; it does nothing to stop two concurrent gunicorn
    WORKER PROCESSES from each reading the same starting state, mutating
    their own in-memory copy independently (e.g. one process's resize
    dropping the old leg while another process's sibling-cancellation
    sweep is simultaneously adding a replacement), and the second write
    silently clobbering the first's change - a lost update, not a
    corruption, so _write's atomicity alone can't catch it. Locks a
    dedicated sidecar file (path + ".lock"), not the JSON data file
    itself, so this lock's file descriptor is never invalidated by
    _write's os.replace() of a DIFFERENT temp file into the data file's
    path while the lock is held."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read(user_id: str) -> Dict[str, List[Dict[str, str]]]:
    store_file = _store_file(user_id)
    if not store_file.exists():
        return {}
    try:
        data = json.loads(store_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(user_id: str, data: Dict[str, List[Dict[str, str]]]) -> None:
    """Temp-file-then-rename, same pattern as overnight_orders.py and
    ambiguous_resolution_audit.py - a crash or raised exception mid-write
    leaves either the OLD complete file or the NEW complete file, never a
    truncated/corrupted one, which _read's `except json.JSONDecodeError:
    return {}` would otherwise silently misread as "nothing tracked" -
    losing track of a genuinely resting protective order, exactly the
    kind of gap _reconcile_exit_orders and _monitor_transitional_orders
    exist to close, not reintroduce at the storage layer."""
    path = _store_file(user_id)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def record_exit_order(user_id: str, ticker: str, order_id: str, order_type: str) -> None:
    """A position can have more than one resting broker-side exit order at
    once - a STOP_LOSS and a take-profit LIMIT riding together as a manual
    bracket (Webull's OpenAPI OTOCO combo type would link them so one fill
    auto-cancels the other, but that combo wire format hasn't been verified
    here, so the app reconciles stale legs itself instead - see
    _reconcile_exit_orders in app.py). Track every resting order, tagged by
    type, so closing the position by any route can find and cancel all of
    them instead of leaving a stale one behind that could later sell shares
    that are no longer held."""
    path = _store_file(user_id)
    with _locked(path):
        data = _read(user_id)
        ticker = ticker.strip().upper()
        data.setdefault(ticker, [])
        if not any(order["id"] == order_id for order in data[ticker]):
            data[ticker].append({"id": order_id, "type": order_type})
        _write(user_id, data)


def get_exit_orders(user_id: str, ticker: str) -> List[Dict[str, str]]:
    """Read-only - every tracked exit order for a ticker, WITHOUT removing
    anything. Callers that need to inspect (e.g. confirm each leg's
    current broker status) before deciding whether it's actually safe to
    stop tracking one must use this, not pop_exit_orders/pop_exit_orders_by_type
    - popping first and only THEN finding out a cancel failed would lose
    track of a genuinely still-resting order (see issue: never remove
    protective-order tracking after a cancellation failure - retain each
    unresolved leg until its terminal broker status is confirmed)."""
    ticker = ticker.strip().upper()
    return list(_read(user_id).get(ticker, []))


def pop_exit_order_by_id(user_id: str, ticker: str, order_id: str) -> bool:
    """Removes exactly ONE tracked order by its id, leaving every other
    tracked order for this ticker (including another of the SAME type,
    e.g. a brand-new replacement leg just added under a different id
    during a resize) completely untouched. pop_exit_orders_by_type would
    remove ALL orders of that type for the ticker - wrong here, since a
    resize's cancel-confirm-replace sequence can have both the OLD and
    NEW client_order_id briefly tracked at once and must only drop the
    old one. Returns True if something was actually removed."""
    path = _store_file(user_id)
    with _locked(path):
        data = _read(user_id)
        ticker = ticker.strip().upper()
        orders = data.get(ticker, [])
        remaining = [order for order in orders if order.get("id") != order_id]
        if len(remaining) == len(orders):
            return False  # nothing matched - not an error, just a no-op
        if remaining:
            data[ticker] = remaining
        else:
            data.pop(ticker, None)
        _write(user_id, data)
        return True


def pop_exit_orders(user_id: str, ticker: str) -> List[Dict[str, str]]:
    """Returns and clears every tracked exit order (stop-loss and/or
    take-profit) for a ticker - call this whenever the position is found to
    be closed, whether closed manually or because one of the legs already
    filled at the broker, so the caller can cancel whatever's left resting."""
    path = _store_file(user_id)
    with _locked(path):
        data = _read(user_id)
        ticker = ticker.strip().upper()
        order_ids = data.pop(ticker, [])
        if order_ids:
            _write(user_id, data)
        return order_ids


def pop_exit_orders_by_type(user_id: str, ticker: str, order_type: str) -> List[Dict[str, str]]:
    """Like pop_exit_orders, but only removes/returns orders of one leg type
    - used when refreshing a single leg (e.g. re-pricing the stop after a
    confidence drop) so a resting take-profit order for the same ticker is
    left completely untouched."""
    path = _store_file(user_id)
    with _locked(path):
        data = _read(user_id)
        ticker = ticker.strip().upper()
        all_orders = data.get(ticker, [])
        matching = [order for order in all_orders if order.get("type") == order_type]
        if not matching:
            return []
        remaining = [order for order in all_orders if order.get("type") != order_type]
        if remaining:
            data[ticker] = remaining
        else:
            data.pop(ticker, None)
        _write(user_id, data)
        return matching


def tracked_tickers(user_id: str) -> List[str]:
    """Every ticker that currently has at least one resting exit order
    tracked - used to spot stale legs for positions that closed on their own."""
    return list(_read(user_id).keys())
