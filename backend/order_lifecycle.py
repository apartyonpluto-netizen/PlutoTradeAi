from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

# An overnight_orders entry moves through these states from the moment an
# entry order is submitted to the moment the position is closed. "Protection
# attempted" (the old boolean stop_order_placed/take_profit_order_placed
# flags) is never treated as equivalent to "protection confirmed active" -
# that conflation was the actual gap: an entry could fill with a protective
# order call that returned success but was never confirmed still resting.
ENTRY_SUBMITTED = "entry_submitted"
ENTRY_PARTIALLY_FILLED = "entry_partially_filled"
ENTRY_FILLED = "entry_filled"
PROTECTION_PENDING = "protection_pending"
PROTECTION_CONFIRMED_ACTIVE = "protection_confirmed_active"
ENTRY_FAILED = "entry_failed"
PROTECTION_FAILED = "protection_failed"
CLOSED = "closed"

ALL_STATES = {
    ENTRY_SUBMITTED,
    ENTRY_PARTIALLY_FILLED,
    ENTRY_FILLED,
    PROTECTION_PENDING,
    PROTECTION_CONFIRMED_ACTIVE,
    ENTRY_FAILED,
    PROTECTION_FAILED,
    CLOSED,
}

# Terminal states need no further monitoring. Everything else is
# "transitional" - the set the fast monitor and restart-recovery scan both
# look for (see monitor scan logic in app.py / order_monitor.py).
TERMINAL_STATES = {ENTRY_FAILED, CLOSED}

VALID_TRANSITIONS: Dict[str, set] = {
    ENTRY_SUBMITTED: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, ENTRY_FAILED},
    ENTRY_PARTIALLY_FILLED: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, PROTECTION_PENDING, ENTRY_FAILED},
    ENTRY_FILLED: {PROTECTION_PENDING},
    PROTECTION_PENDING: {PROTECTION_CONFIRMED_ACTIVE, PROTECTION_FAILED},
    PROTECTION_CONFIRMED_ACTIVE: {CLOSED, PROTECTION_PENDING},
    PROTECTION_FAILED: {PROTECTION_PENDING, CLOSED},
    ENTRY_FAILED: set(),
    CLOSED: set(),
}


def validate_transition(from_state: str, to_state: str) -> None:
    if to_state not in VALID_TRANSITIONS.get(from_state, set()):
        raise ValueError(f"Invalid order lifecycle transition: {from_state} -> {to_state}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(entry: Dict[str, object], state: str = ENTRY_SUBMITTED, **fields: object) -> Dict[str, object]:
    """Sets a brand-new entry's starting state - distinct from transition()
    because there's no "from" state to validate against yet. Always call
    this once, right when the entry is created, before any transition()."""
    entry["lifecycle_state"] = state
    for key, value in fields.items():
        entry[key] = value
    entry["lifecycle_history"] = [{"state": state, "at": _now_iso()}]
    return entry


def transition(entry: Dict[str, object], to_state: str, **fields: object) -> Dict[str, object]:
    """Mutates and returns entry - advances its lifecycle_state, stamps a
    lifecycle_history audit trail entry, and merges in whatever else changed
    (filled_quantity, error text, etc). Raises ValueError rather than
    silently accepting an illegal jump (e.g. entry_submitted straight to
    protection_confirmed_active), or a call on an entry that was never
    initialize()'d - either is a caller bug, not something to paper over."""
    from_state = entry.get("lifecycle_state")
    if from_state is None:
        raise ValueError("Entry has no lifecycle_state - call initialize() before transition().")
    validate_transition(str(from_state), to_state)
    entry["lifecycle_state"] = to_state
    for key, value in fields.items():
        entry[key] = value
    history: List[Dict[str, object]] = entry.setdefault("lifecycle_history", [])
    history.append({"state": to_state, "at": _now_iso()})
    return entry


def is_transitional(entry: Dict[str, object]) -> bool:
    """True for any order still being actively monitored - covers both the
    fast per-order monitor's polling scope and restart recovery (an entry
    left in a transitional state when the process died is exactly what the
    next monitor scan needs to pick back up, with no separate recovery
    code path required)."""
    return str(entry.get("lifecycle_state", ENTRY_SUBMITTED)) not in TERMINAL_STATES


def deterministic_client_order_id(user_id: str, ticker: str, trading_day: str, leg: str, attempt: int = 1) -> str:
    """A stable id for one specific placement attempt of one specific leg
    (entry/stop/target) of one specific ticker on one specific trading day -
    NOT a fresh uuid4 per call. Confirmed live against the Webull sandbox
    this session: resubmitting the same client_order_id is rejected with
    HTTP 417 OAUTH_OPENAPI_TRADE_PLACE_ORDER_REPEAT rather than silently
    creating a second order, so reusing this id across a crash-and-retry of
    the same logical attempt turns Webull's own duplicate-order guard into
    this app's idempotency mechanism, instead of relying only on an
    already_placed_today check that a crash between placing and recording
    could slip past.

    attempt increments only when a caller deliberately starts a genuinely
    new placement (e.g. re-protecting after confirming the previous stop
    order was actually cancelled) - not on every monitor poll tick, which
    should keep reusing the same id while it's just checking status."""
    raw = f"{user_id}:{ticker.strip().upper()}:{trading_day}:{leg}:{attempt}"
    return "pt" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:30]


def summarize_fill(order_detail: Dict[str, object]) -> Dict[str, object]:
    """Extracts the fields this app actually needs from a Webull
    get_order_detail response - real shape confirmed live this session:
    {"orders": [{"status": "FILLED", "total_quantity": "1", "filled_quantity": "1", ...}]}.
    Returns zeros/UNKNOWN for a response with no matching leg rather than
    raising, since a not-yet-visible order is a normal transient state for a
    monitor poll, not an error."""
    orders = order_detail.get("orders") or []
    leg = orders[0] if orders else {}
    total_quantity = float(leg.get("total_quantity", 0) or 0)
    filled_quantity = float(leg.get("filled_quantity", 0) or 0)
    return {
        "status": str(leg.get("status", "UNKNOWN")),
        "total_quantity": total_quantity,
        "filled_quantity": filled_quantity,
        "order_id": leg.get("order_id", ""),
    }
