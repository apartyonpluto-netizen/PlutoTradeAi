"""Trade tickets: what the scan proposes in APPROVAL mode instead of
submitting. A human approves or declines each one; approval is bound to a
specific version of the ticket's material terms, and every approval is
re-checked against the broker immediately before submission (app.py).

Per-user JSON file, read-modify-write under an flock, atomic writes - the
same discipline as overnight_orders.py. Every mutation goes through the
lock, so two approvals of the same ticket (double-click, two tabs, two
gunicorn workers) can never both claim it.

Statuses:
    AWAITING_APPROVAL  proposed by the scan, waiting for a decision
    APPROVED           claimed by an approval; recheck + submission running
    SUBMITTED          an order was sent (see `submission` for the outcome)
    RECHECK_FAILED     approved, but the pre-submission recheck refused
    DECLINED           declined by the user (its ticker is not re-proposed
                       for the rest of that trading day)
    EXPIRED            never decided in time
    SUPERSEDED         a later scan proposed different terms for the ticker
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from . import event_journal

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
SUBMITTED = "SUBMITTED"
RECHECK_FAILED = "RECHECK_FAILED"
DECLINED = "DECLINED"
EXPIRED = "EXPIRED"
SUPERSEDED = "SUPERSEDED"
OPEN_STATUSES = {AWAITING_APPROVAL, APPROVED}

# A scan-time price goes stale quickly; an undecided ticket lapses rather
# than waiting for someone to approve yesterday's setup. The next scan
# proposes it again if it still qualifies.
TICKET_TTL_SECONDS = 20 * 60
MAX_TICKETS_PER_USER = 500

MATERIAL_FIELDS = (
    "ticker", "instrument_type", "direction", "quantity", "limit_price", "stop", "target", "account_id", "environment",
    "option_symbol", "strike", "expiration_date", "option_type",
)


class TicketConflict(Exception):
    """The ticket is not in a state where this action applies (already
    decided, changed since it was viewed, expired, or unknown)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tickets_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "trade_tickets.json"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write(path: Path, tickets: List[Dict[str, Any]]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(tickets[:MAX_TICKETS_PER_USER], indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _statuses(tickets: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {str(t.get("ticket_id")): t.get("status") for t in tickets}


def _journal_status_changes(user_id: str, before: Dict[str, Any], tickets: List[Dict[str, Any]]) -> None:
    """ticket.<status> for every ticket whose status differs from `before`
    (new tickets included). Called after the write, under the lock."""
    events = []
    for ticket in tickets:
        status = ticket.get("status")
        if not status or before.get(str(ticket.get("ticket_id"))) == status:
            continue
        recheck = ticket.get("recheck") or {}
        submission = ticket.get("submission") or {}
        events.append(event_journal.build_event(
            f"ticket.{str(status).lower()}",
            correlation_id=ticket.get("correlation_id") or f"tkt-{ticket.get('ticket_id')}",
            ticker=ticket.get("ticker"), account_id=ticket.get("account_id"), source="trade_tickets",
            data={
                "ticket_id": ticket.get("ticket_id"), "version": ticket.get("version"),
                "instrument_type": ticket.get("instrument_type"), "direction": ticket.get("direction"),
                "quantity": ticket.get("quantity"), "limit_price": ticket.get("limit_price"),
                "option_symbol": ticket.get("option_symbol"), "supersedes": ticket.get("supersedes"),
                "superseded_by": ticket.get("superseded_by"), "decline_reason": ticket.get("decline_reason"),
                "recheck_reasons": recheck.get("reasons") if status == RECHECK_FAILED else None,
                "submission_status": submission.get("status"), "record_id": submission.get("record_id"),
                "entry_client_order_id": submission.get("entry_client_order_id"),
            },
        ))
    event_journal.emit_many(user_id, events)


def material_terms_version(ticket: Dict[str, Any]) -> str:
    """Approval binds to these terms. Any change means a new version, and an
    approval carrying the old version is refused."""
    material = {field: ticket.get(field) for field in MATERIAL_FIELDS}
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _expire_in_place(tickets: List[Dict[str, Any]], now: datetime) -> bool:
    """Marks lapsed AWAITING_APPROVAL tickets EXPIRED. Returns True if any changed."""
    changed = False
    for ticket in tickets:
        if ticket.get("status") != AWAITING_APPROVAL:
            continue
        expires_at = _parse(ticket.get("expires_at"))
        if expires_at is not None and now >= expires_at:
            ticket["status"] = EXPIRED
            ticket["expired_at"] = _iso(now)
            changed = True
    return changed


def list_tickets(user_id: str, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Newest first. Lapsed tickets are expired (and persisted) on the way out."""
    now = now or _now()
    path = _tickets_file(user_id)
    with _locked(path):
        tickets = _read(path)
        before = _statuses(tickets)
        if _expire_in_place(tickets, now):
            _write(path, tickets)
            _journal_status_changes(user_id, before, tickets)
    return tickets


def get_ticket(user_id: str, ticket_id: str) -> Optional[Dict[str, Any]]:
    return next((t for t in list_tickets(user_id) if t.get("ticket_id") == ticket_id), None)


def open_tickets(user_id: str) -> List[Dict[str, Any]]:
    return [t for t in list_tickets(user_id) if t.get("status") in OPEN_STATUSES]


def create_or_refresh_ticket(user_id: str, proposal: Dict[str, Any], *, now: Optional[datetime] = None) -> Tuple[Dict[str, Any], str]:
    """Records a scan proposal. Returns (ticket, outcome) where outcome is:
        "created"     no open ticket for this ticker - a new one
        "refreshed"   an open ticket with identical material terms - kept,
                      last_seen_at bumped, expiry extended
        "superseded"  an open ticket with different terms - it is marked
                      SUPERSEDED and a new ticket replaces it
    An APPROVED ticket (mid-submission) is never touched: the proposal is
    dropped with outcome "in_progress"."""
    now = now or _now()
    path = _tickets_file(user_id)
    version = material_terms_version(proposal)
    ticker = str(proposal.get("ticker", "")).upper()
    with _locked(path):
        tickets = _read(path)
        before = _statuses(tickets)
        _expire_in_place(tickets, now)
        existing = next(
            (t for t in tickets if t.get("status") in OPEN_STATUSES and str(t.get("ticker", "")).upper() == ticker),
            None,
        )
        if existing is not None and existing.get("status") == APPROVED:
            _write(path, tickets)
            _journal_status_changes(user_id, before, tickets)
            return existing, "in_progress"
        if existing is not None and existing.get("version") == version:
            existing["last_seen_at"] = _iso(now)
            existing["expires_at"] = _iso(now + timedelta(seconds=TICKET_TTL_SECONDS))
            existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
            _write(path, tickets)
            _journal_status_changes(user_id, before, tickets)
            return existing, "refreshed"
        ticket = {
            **proposal,
            "ticket_id": uuid.uuid4().hex,
            "version": version,
            "status": AWAITING_APPROVAL,
            "created_at": _iso(now),
            "last_seen_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=TICKET_TTL_SECONDS)),
            "seen_count": 1,
        }
        outcome = "created"
        if existing is not None:
            existing["status"] = SUPERSEDED
            existing["superseded_at"] = _iso(now)
            existing["superseded_by"] = ticket["ticket_id"]
            ticket["supersedes"] = existing["ticket_id"]
            outcome = "superseded"
        tickets.insert(0, ticket)
        _write(path, tickets)
        _journal_status_changes(user_id, before, tickets)
        return ticket, outcome


def claim_for_approval(user_id: str, ticket_id: str, version: str, approved_by: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Atomically moves AWAITING_APPROVAL -> APPROVED. Refuses (TicketConflict)
    if the ticket is unknown, already decided, expired, or `version` is not
    the version the approver saw."""
    now = now or _now()
    path = _tickets_file(user_id)
    with _locked(path):
        tickets = _read(path)
        before = _statuses(tickets)
        _expire_in_place(tickets, now)
        ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
        if ticket is None:
            raise TicketConflict("No such ticket.")
        if ticket.get("status") == APPROVED:
            raise TicketConflict("This ticket is already being submitted.")
        if ticket.get("status") != AWAITING_APPROVAL:
            raise TicketConflict(f"This ticket is {str(ticket.get('status', '')).lower().replace('_', ' ')} - it can no longer be approved.")
        if str(version or "") != str(ticket.get("version") or ""):
            raise TicketConflict("The ticket's terms changed since you viewed it - review the new version before approving.")
        ticket["status"] = APPROVED
        ticket["approved_at"] = _iso(now)
        ticket["approved_by"] = approved_by
        ticket["approved_version"] = ticket.get("version")
        _write(path, tickets)
        _journal_status_changes(user_id, before, tickets)
        return dict(ticket)


def update_ticket(user_id: str, ticket_id: str, mutate: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    path = _tickets_file(user_id)
    with _locked(path):
        tickets = _read(path)
        ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
        if ticket is None:
            raise TicketConflict("No such ticket.")
        before = _statuses(tickets)
        mutate(ticket)
        _write(path, tickets)
        _journal_status_changes(user_id, before, tickets)
        return dict(ticket)


def decline_ticket(user_id: str, ticket_id: str, version: str, reason: str, declined_by: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    path = _tickets_file(user_id)
    with _locked(path):
        tickets = _read(path)
        before = _statuses(tickets)
        _expire_in_place(tickets, now)
        ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
        if ticket is None:
            raise TicketConflict("No such ticket.")
        if ticket.get("status") != AWAITING_APPROVAL:
            raise TicketConflict(f"This ticket is {str(ticket.get('status', '')).lower().replace('_', ' ')} - it can no longer be declined.")
        if str(version or "") != str(ticket.get("version") or ""):
            raise TicketConflict("The ticket's terms changed since you viewed it - review the new version first.")
        ticket["status"] = DECLINED
        ticket["declined_at"] = _iso(now)
        ticket["declined_by"] = declined_by
        ticket["decline_reason"] = (reason or "").strip()[:500]
        _write(path, tickets)
        _journal_status_changes(user_id, before, tickets)
        return dict(ticket)


def declined_tickers_today(user_id: str, trading_day: str) -> Set[str]:
    """Tickers the user declined on `trading_day` - the scan does not
    propose them again that day."""
    return {
        str(t.get("ticker", "")).upper()
        for t in list_tickets(user_id)
        if t.get("status") == DECLINED and t.get("trading_day") == trading_day and t.get("ticker")
    }
