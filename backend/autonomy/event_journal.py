"""Event journal: one append-only line per thing that happened to a trade
idea, all sharing the correlation_id minted when the scan first evaluated
it - so "what happened to this idea" is one lookup, from signal to exit.

    signal.evaluated    the scan evaluated a candidate (decision + reason)
    plan.built          a trade plan was computed (verdict, size, risk)
    ticket.<status>     a ticket was proposed / approved / declined / ...
    order.<state>       an order record durably reached a lifecycle state
    position.closed     a closed position was recorded (reason, P&L)

What this is NOT: a copy of the order records. overnight_orders.json stays
the source of truth for an order; the journal is the index that ties it to
the signal, plan and ticket that produced it. Order events are emitted only
AFTER the record itself is written (see overnight_orders.py), so the journal
never claims a transition the records don't hold. A crash between the two
writes can drop that one event - trace() re-derives lifecycle steps from the
records, so a trace is still complete; only the live feed misses it.

Every event is an observation of something that really happened in this
process. Nothing here is simulated; `env` says whether the order side was
the broker sandbox or a live account.

JSONL per user, appended under an flock, rotated by size. A write failure is
logged and counted, never raised - journaling must not affect trading."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 5_000_000
ROTATED_FILES_KEPT = 10  # ~55 MB at most per user; weeks of scans
MAX_DATA_TEXT = 300

logger = logging.getLogger(__name__)

# Lifecycle state -> the stage the observatory draws it in.
STAGE_BY_ORDER_STATE = {
    "entry_submitted": "order",
    "unknown_submission_state": "order",
    "entry_failed": "order",
    "entry_partially_filled": "fill",
    "entry_filled": "fill",
    "protection_pending": "protection",
    "protection_confirmed_active": "protection",
    "protection_failed": "protection",
    "closed": "exit",
    "manually_resolved_no_order": "reconcile",
    "manual_link_in_progress": "reconcile",
}
STAGE_ORDER = ("signal", "plan", "ticket", "order", "fill", "protection", "reconcile", "exit")

_SECRET_MARKERS = ("secret", "token", "password", "app_key", "api_key", "authorization", "credential")

_failures = {"count": 0, "last_error": None, "last_at": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "event_journal.jsonl"


def _rotated(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def new_correlation_id() -> str:
    return "c-" + uuid.uuid4().hex[:20]


def correlation_id_for(record: Dict[str, Any]) -> Optional[str]:
    """The record's own correlation_id, or a stable stand-in derived from its
    ids for records written before correlation ids existed (so their events
    still group, and group the same way on every call)."""
    if not isinstance(record, dict):
        return None
    if record.get("correlation_id"):
        return str(record["correlation_id"])
    if record.get("record_id"):
        return f"rec-{record['record_id']}"
    if record.get("entry_client_order_id"):
        return f"coid-{record['entry_client_order_id']}"
    return None


def current_env() -> str:
    """Where orders go right now: "live" only when live trading is armed."""
    try:
        from integrations import webull as webull_api
    except ImportError:  # pragma: no cover - package-relative import path
        try:
            from ..integrations import webull as webull_api  # type: ignore
        except ImportError:
            return "unknown"
    try:
        return "live" if webull_api.is_live_trading_armed() else "sandbox"
    except Exception:  # noqa: BLE001
        return "unknown"


def _clean(value: Any, depth: int = 0) -> Any:
    """Small, JSON-safe, secret-free copy of an event payload."""
    if depth > 3:
        return None
    if isinstance(value, dict):
        cleaned = {}
        for key, item in list(value.items())[:40]:
            name = str(key)
            if any(marker in name.lower() for marker in _SECRET_MARKERS):
                continue
            cleaned[name] = _clean(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value if len(value) <= MAX_DATA_TEXT else value[:MAX_DATA_TEXT] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_DATA_TEXT]


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < MAX_FILE_BYTES:
            return
    except OSError:
        return
    oldest = _rotated(path, ROTATED_FILES_KEPT)
    if oldest.exists():
        oldest.unlink()
    for index in range(ROTATED_FILES_KEPT - 1, 0, -1):
        source = _rotated(path, index)
        if source.exists():
            os.replace(source, _rotated(path, index + 1))
    os.replace(path, _rotated(path, 1))


def _append(user_id: str, events: List[Dict[str, Any]]) -> None:
    path = _journal_file(user_id)
    lines = "".join(json.dumps(event, separators=(",", ":"), default=str) + "\n" for event in events)
    with _locked(path):
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(lines)
            handle.flush()
            os.fsync(handle.fileno())


def build_event(
    event_type: str,
    *,
    correlation_id: Optional[str],
    ticker: Optional[str] = None,
    account_id: Optional[str] = None,
    source: str = "",
    at: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    env: Optional[str] = None,
) -> Dict[str, Any]:
    stage = event_type.split(".", 1)[0]
    if stage == "order":
        stage = STAGE_BY_ORDER_STATE.get(event_type.split(".", 1)[1], "order")
    elif stage == "position":
        stage = "exit"
    logged_at = _now_iso()
    return {
        "v": SCHEMA_VERSION,
        "event_id": "ev-" + uuid.uuid4().hex[:20],
        "type": event_type,
        "stage": stage,
        "correlation_id": correlation_id,
        "at": at or logged_at,
        "logged_at": logged_at,
        "ticker": str(ticker).upper() if ticker else None,
        "account_id": str(account_id) if account_id else None,
        "source": source,
        "env": env or current_env(),
        "data": _clean(data or {}),
    }


def emit(user_id: str, event_type: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Appends one event. Never raises."""
    return (emit_many(user_id, [build_event(event_type, **kwargs)]) or [None])[0]


def emit_many(user_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not events or not user_id:
        return []
    try:
        _append(user_id, events)
        return events
    except Exception as error:  # noqa: BLE001 - journaling must never affect trading
        _failures["count"] += 1
        _failures["last_error"] = f"{type(error).__name__}: {str(error)[:200]}"
        _failures["last_at"] = _now_iso()
        logger.warning("event journal write failed for %d event(s): %s", len(events), _failures["last_error"])
        return []


def failure_stats() -> Dict[str, Any]:
    """Write failures in THIS process since it started."""
    return dict(_failures)


def _iter_lines(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line from a crash mid-append
                if isinstance(event, dict):
                    yield event
    except FileNotFoundError:
        return


def _files_oldest_first(user_id: str, include_rotated: bool) -> List[Path]:
    path = _journal_file(user_id)
    files = []
    if include_rotated:
        files.extend(p for p in (_rotated(path, i) for i in range(ROTATED_FILES_KEPT, 0, -1)) if p.exists())
    files.append(path)
    return files


def read_events(
    user_id: str,
    *,
    correlation_id: Optional[str] = None,
    since: Optional[str] = None,
    types: Optional[Iterable[str]] = None,
    ticker: Optional[str] = None,
    limit: int = 500,
    include_rotated: bool = False,
) -> List[Dict[str, Any]]:
    """Newest last. `since` compares against logged_at (ISO, UTC)."""
    wanted_types = set(types) if types else None
    wanted_ticker = str(ticker).upper() if ticker else None
    matched: List[Dict[str, Any]] = []
    for path in _files_oldest_first(user_id, include_rotated or bool(correlation_id)):
        for event in _iter_lines(path):
            if correlation_id and event.get("correlation_id") != correlation_id:
                continue
            if since and str(event.get("logged_at") or "") <= since:
                continue
            if wanted_types and event.get("type") not in wanted_types:
                continue
            if wanted_ticker and event.get("ticker") != wanted_ticker:
                continue
            matched.append(event)
    return matched[-limit:] if limit else matched


def lifecycle_events_from_record(record: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None, *, env: Optional[str] = None) -> List[Dict[str, Any]]:
    """order.<state> events for a record's lifecycle_history entries (all of
    them, or just `history` when given)."""
    events = []
    correlation_id = correlation_id_for(record)
    for step in history if history is not None else (record.get("lifecycle_history") or []):
        state = str((step or {}).get("state") or "")
        if not state:
            continue
        data: Dict[str, Any] = {
            "record_id": record.get("record_id"),
            "entry_client_order_id": record.get("entry_client_order_id"),
            "instrument_type": record.get("instrument_type"),
            "direction": record.get("direction"),
            "quantity": record.get("quantity"),
            "limit_price": record.get("limit_price"),
            "ticket_id": record.get("ticket_id"),
        }
        if state in ("entry_filled", "entry_partially_filled"):
            data["filled_quantity"] = record.get("filled_quantity")
        if state in ("entry_failed", "protection_failed", "unknown_submission_state"):
            data["error"] = record.get("error")
        if state == "closed":
            data.update({"close_reason": record.get("close_reason"), "closed_trade_id": record.get("closed_trade_id")})
        events.append(build_event(
            f"order.{state}", correlation_id=correlation_id, ticker=record.get("ticker"),
            account_id=record.get("account_id"), source="order_record", at=step.get("at"), data=data, env=env,
        ))
    return events


def _record_key(record: Dict[str, Any]) -> Optional[str]:
    return str(record.get("record_id") or record.get("entry_client_order_id") or "") or None


def journal_record_changes(user_id: str, before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> None:
    """Emits order.<state> for every lifecycle step present in `after` but
    not in `before` (both are full order-record lists). Never raises."""
    try:
        seen = {}
        for record in before or []:
            key = _record_key(record) if isinstance(record, dict) else None
            if key:
                seen[key] = len(record.get("lifecycle_history") or [])
        env = None
        events: List[Dict[str, Any]] = []
        for record in after or []:
            if not isinstance(record, dict):
                continue
            key = _record_key(record)
            history = record.get("lifecycle_history") or []
            if not key or not history:
                continue
            already = seen.get(key, 0)
            if len(history) <= already:
                continue
            env = env or current_env()
            events.extend(lifecycle_events_from_record(record, history[already:], env=env))
        emit_many(user_id, events)
    except Exception as error:  # noqa: BLE001
        logger.warning("event journal: could not diff order records: %s", error)


def trace(user_id: str, correlation_id: str, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Everything known about one trade idea, oldest first. Journal events
    plus lifecycle steps re-derived from the order records themselves (for
    records older than the journal, or a step whose event was lost), each
    marked with its origin."""
    events = [dict(event, origin="journal") for event in read_events(user_id, correlation_id=correlation_id, limit=0)]
    journaled = {(e.get("type"), e.get("at")) for e in events if str(e.get("type", "")).startswith("order.")}
    for record in records or []:
        if correlation_id_for(record) != correlation_id:
            continue
        for event in lifecycle_events_from_record(record, env="unknown"):
            if (event["type"], event["at"]) not in journaled:
                event["origin"] = "record"
                event["env"] = None
                events.append(event)
    events.sort(key=lambda e: (str(e.get("at") or ""), str(e.get("logged_at") or "")))
    stages = [stage for stage in STAGE_ORDER if any(e.get("stage") == stage for e in events)]
    return {
        "correlation_id": correlation_id,
        "ticker": next((e.get("ticker") for e in events if e.get("ticker")), None),
        "events": events,
        "stages_reached": stages,
        "furthest_stage": stages[-1] if stages else None,
        "latest": events[-1]["type"] if events else None,
    }


def recent_chains(user_id: str, *, limit: int = 30, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """The most recent trade ideas that got past signal evaluation (a plan,
    ticket or order exists), newest first, each summarized."""
    chains: Dict[str, Dict[str, Any]] = {}
    for event in read_events(user_id, since=since, limit=0):
        correlation_id = event.get("correlation_id")
        if not correlation_id:
            continue
        chain = chains.setdefault(correlation_id, {
            "correlation_id": correlation_id, "ticker": event.get("ticker"), "first_at": event.get("at"),
            "stages": set(), "events": 0, "env": None,
        })
        chain["stages"].add(event.get("stage"))
        chain["events"] += 1
        chain["last_at"] = event.get("logged_at")
        chain["latest"] = event.get("type")
        if event.get("stage") in ("order", "fill", "protection", "exit"):
            chain["env"] = event.get("env")
    summaries = []
    for chain in chains.values():
        if not chain["stages"] - {"signal"}:
            continue
        stages = [stage for stage in STAGE_ORDER if stage in chain["stages"]]
        summaries.append({**chain, "stages": stages, "furthest_stage": stages[-1]})
    summaries.sort(key=lambda c: str(c.get("last_at") or ""), reverse=True)
    return summaries[:limit]
