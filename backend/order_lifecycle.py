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
# The broker's true response to an entry submission is unknown - e.g. a
# timeout or dropped connection after the request was sent, where the order
# may have been accepted anyway. Deliberately distinct from both
# ENTRY_SUBMITTED (which means the placement call itself definitely
# succeeded) and ENTRY_FAILED (which means it definitely didn't) - see
# _submit_and_protect_entry's docstring in app.py for how the two exception
# types are told apart, and _reconcile_unknown_submission for how this
# eventually gets resolved.
UNKNOWN_SUBMISSION_STATE = "unknown_submission_state"
# A human administrator manually determined - via mandatory fresh broker
# checks, not a broker response - that no order or position exists for an
# entry stuck in UNKNOWN_SUBMISSION_STATE, and released its capital
# reservation. Deliberately its OWN state, never collapsed into
# ENTRY_FAILED: ENTRY_FAILED specifically means the BROKER told this app
# the order failed (a ValueError/DefiniteOrderRejection from a real
# response) - conflating a human's evidence-based judgment call with that
# would misrepresent the actual source and strength of the claim to anyone
# reading the record later. See _resolve_ambiguous_submission in app.py -
# it preserves the entry's original ambiguity fields (error,
# first_definite_rejection_at, definite_rejection_count) AND records the
# evidence snapshot, administrator, and reason directly on the entry
# itself, not only in the separate audit log.
MANUALLY_RESOLVED_NO_ORDER = "manually_resolved_no_order"
# The non-terminal, in-progress marker for a manual "link" resolution
# (_resolve_ambiguous_submission in app.py) between the moment it's
# persisted - deliberately BEFORE any polling or protective-order
# placement begins - and the moment fill-confirmation/protection
# concludes. Distinct from ENTRY_SUBMITTED so this account's new-entry
# freeze (see FROZEN_STATES) stays keyed to an explicit, purpose-specific
# state rather than overloading a state that also means "a normal
# autonomous entry was just placed" - and so restart recovery
# (_recover_incomplete_manual_resolutions) can tell "an admin's manual
# link is mid-flight" apart from every other reason an entry might be
# sitting in ENTRY_SUBMITTED.
MANUAL_LINK_IN_PROGRESS = "manual_link_in_progress"

ALL_STATES = {
    ENTRY_SUBMITTED,
    ENTRY_PARTIALLY_FILLED,
    ENTRY_FILLED,
    PROTECTION_PENDING,
    PROTECTION_CONFIRMED_ACTIVE,
    ENTRY_FAILED,
    PROTECTION_FAILED,
    CLOSED,
    UNKNOWN_SUBMISSION_STATE,
    MANUALLY_RESOLVED_NO_ORDER,
    MANUAL_LINK_IN_PROGRESS,
}

# States that must block every NEW autonomous entry for the account (see
# _has_unresolved_ambiguous_submission_locally in app.py) - not just
# UNKNOWN_SUBMISSION_STATE itself, but also a manual link resolution
# that's still mid-flight, since that entry's real fill/protection
# outcome isn't knowable yet either. Note this is necessary but not
# SUFFICIENT for the freeze predicate in app.py - a resolution whose
# audit trail never durably confirmed completion (see
# ambiguous_resolution_audit.find_incomplete_resolutions) must keep
# blocking new entries even after the affected entry's own
# lifecycle_state has already moved past everything in this set.
FROZEN_STATES = {UNKNOWN_SUBMISSION_STATE, MANUAL_LINK_IN_PROGRESS}

# Every state an ORDINARY (non-ambiguous-submission, non-manual-link)
# entry can be sitting in when the process dies mid-flight, up through
# (but not including) a first successful protection confirmation.
# Deliberately excludes CLOSED/ENTRY_FAILED (nothing left to do) and
# excludes FROZEN_STATES above (those have their OWN dedicated
# reconciliation, with their own audit/evidence requirements - see
# _reconcile_unknown_submissions and _recover_incomplete_manual_resolutions
# in app.py). See app.py's _monitor_transitional_orders, the fast
# per-order monitor that resumes every entry in one of these states.
#
# NOTE: PROTECTION_CONFIRMED_ACTIVE is intentionally NOT here even though
# it also needs ongoing monitoring - see FILL_MONITORING_STATES below for
# why that's a separate, broader set.
MONITOR_RESUMABLE_STATES = {
    ENTRY_SUBMITTED,
    ENTRY_PARTIALLY_FILLED,
    ENTRY_FILLED,
    PROTECTION_PENDING,
    PROTECTION_FAILED,
}

# MONITOR_RESUMABLE_STATES plus PROTECTION_CONFIRMED_ACTIVE - the broader
# set of states where an entry might STILL need broker-side reconciliation
# even though its FIRST protection confirmation already succeeded. Two
# independent reasons an entry at PROTECTION_CONFIRMED_ACTIVE can still
# need attention, both checked by app.py's fast monitor:
#   - the underlying entry order might not be broker-terminal yet (a day
#     limit order can keep filling MORE shares after an earlier partial
#     fill was already protected) - see entry["entry_order_terminal"] and
#     _reconcile_entry_fill_and_protection, which resizes protection to
#     match the broker's latest cumulative fill. VALID_TRANSITIONS
#     deliberately allows PROTECTION_CONFIRMED_ACTIVE -> PROTECTION_PENDING
#     for exactly this resize case - "a transition to PROTECTION_PENDING
#     must not stop fill monitoring" is what this state's continued
#     membership here is for;
#   - the position may have EXITED (a stop or target leg filled) without
#     this app having noticed yet - see _reconcile_position_exit, which
#     confirms which leg (if either) actually executed directly from its
#     own broker status, never by inferring exit from the position simply
#     no longer appearing in the broker's positions list.
FILL_MONITORING_STATES = MONITOR_RESUMABLE_STATES | {PROTECTION_CONFIRMED_ACTIVE}

# Terminal states need no further monitoring. Everything else is
# "transitional" - the set the fast monitor and restart-recovery scan both
# look for (see monitor scan logic in app.py / order_monitor.py).
# UNKNOWN_SUBMISSION_STATE is deliberately NOT terminal - an order left
# there is exactly what needs monitoring most, not less.
# MANUALLY_RESOLVED_NO_ORDER IS terminal - a human, backed by fresh
# evidence, already concluded there is nothing left to monitor.
TERMINAL_STATES = {ENTRY_FAILED, CLOSED, MANUALLY_RESOLVED_NO_ORDER}

VALID_TRANSITIONS: Dict[str, set] = {
    ENTRY_SUBMITTED: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, ENTRY_FAILED, UNKNOWN_SUBMISSION_STATE},
    ENTRY_PARTIALLY_FILLED: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, PROTECTION_PENDING, ENTRY_FAILED},
    ENTRY_FILLED: {PROTECTION_PENDING},
    PROTECTION_PENDING: {PROTECTION_CONFIRMED_ACTIVE, PROTECTION_FAILED},
    PROTECTION_CONFIRMED_ACTIVE: {CLOSED, PROTECTION_PENDING},
    PROTECTION_FAILED: {PROTECTION_PENDING, CLOSED},
    ENTRY_FAILED: set(),
    CLOSED: set(),
    MANUALLY_RESOLVED_NO_ORDER: set(),
    # Reconciliation (_reconcile_unknown_submission) has three AUTOMATIC
    # outcomes: confirms the order reached the broker and is still live,
    # resuming normal fill tracking from ENTRY_SUBMITTED; confirms - via a
    # well-formed DefiniteOrderRejection, or a successful lookup whose
    # status is CANCELLED/FAILED - that it definitely never went through
    # (or didn't survive), resolving straight to ENTRY_FAILED without
    # passing back through ENTRY_SUBMITTED first; or still can't tell, and
    # self-loops to record another audit-trail entry without forcing a
    # resolution. MANUALLY_RESOLVED_NO_ORDER and MANUAL_LINK_IN_PROGRESS
    # are the two HUMAN outcomes - see _resolve_ambiguous_submission in
    # app.py, reachable only through that function's mandatory-fresh-
    # evidence gate, never automatically.
    UNKNOWN_SUBMISSION_STATE: {
        ENTRY_SUBMITTED,
        ENTRY_FAILED,
        UNKNOWN_SUBMISSION_STATE,
        MANUALLY_RESOLVED_NO_ORDER,
        MANUAL_LINK_IN_PROGRESS,
    },
    # Mirrors ENTRY_SUBMITTED's own first-jump target set (minus
    # UNKNOWN_SUBMISSION_STATE, which a manual link resumption never goes
    # back into) - _poll_fill_and_protect's first transition call, invoked
    # directly against an entry sitting in MANUAL_LINK_IN_PROGRESS (see
    # _resolve_ambiguous_submission and _recover_incomplete_manual_resolutions
    # in app.py), lands on exactly one of these three. If nothing fills
    # within the bounded poll window, _poll_fill_and_protect returns
    # without transitioning at all - the entry stays MANUAL_LINK_IN_PROGRESS
    # (still frozen, still transitional), ready for a later scan's restart
    # recovery to poll again.
    MANUAL_LINK_IN_PROGRESS: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, ENTRY_FAILED},
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


# Candidate field names for a leg's average/actual FILL PRICE, tried in
# order against a get_order_detail response. NONE of these has been
# empirically confirmed present in a real Webull response this session -
# the only shape "confirmed live" so far (see summarize_fill's docstring)
# is status/total_quantity/filled_quantity/order_id, with no price field
# observed. This list is a best-effort extraction attempt, not a verified
# contract: summarize_fill treats a response with none of these keys as
# "fill price unknown" (average_price=None), which is the CORRECT and
# REQUIRED behavior either way - a missing/unconfirmed field name and a
# broker response that genuinely omits the price are indistinguishable
# from here, and both must fail closed into "unknown", never fall back to
# a proposed/planned price silently standing in as if it were real.
_FILL_PRICE_FIELD_CANDIDATES = (
    "avg_filled_price",
    "avgFilledPrice",
    "filled_price",
    "avg_price",
    "average_price",
    "averagePrice",
)


def summarize_fill(order_detail: Dict[str, object]) -> Dict[str, object]:
    """Extracts the fields this app actually needs from a Webull
    get_order_detail response - real shape confirmed live this session:
    {"orders": [{"status": "FILLED", "total_quantity": "1", "filled_quantity": "1", ...}]}.
    Returns zeros/UNKNOWN for a response with no matching leg rather than
    raising, since a not-yet-visible order is a normal transient state for a
    monitor poll, not an error.

    average_price is best-effort (see _FILL_PRICE_FIELD_CANDIDATES) and
    None whenever no candidate field is present - callers computing
    realized P&L MUST treat None as "unknown", never substitute a planned/
    proposed price (limit_price, stop, target) as if it were the real
    fill price. See _reconcile_position_exit in app.py, which marks a
    closed trade's P&L incomplete rather than estimating it when this is
    None for either leg."""
    orders = order_detail.get("orders") or []
    leg = orders[0] if orders else {}
    total_quantity = float(leg.get("total_quantity", 0) or 0)
    filled_quantity = float(leg.get("filled_quantity", 0) or 0)
    average_price: Optional[float] = None
    for field_name in _FILL_PRICE_FIELD_CANDIDATES:
        raw_value = leg.get(field_name)
        if raw_value in (None, "", "0", 0):
            continue
        try:
            average_price = float(raw_value)
        except (TypeError, ValueError):
            continue
        break
    return {
        "status": str(leg.get("status", "UNKNOWN")),
        "total_quantity": total_quantity,
        "filled_quantity": filled_quantity,
        "order_id": leg.get("order_id", ""),
        "average_price": average_price,
    }
