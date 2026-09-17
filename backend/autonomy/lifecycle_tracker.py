from __future__ import annotations

"""Tier 1 reporting, same boundary as performance_report.py/
outcomes_analysis.py - answers the specific question the 2026-09-11
readiness assessment turned on ("would you call the agent functional for
real money trading now?" -> no, because zero trades have ever completed
the full intended lifecycle cleanly - found -> entered -> filled ->
protected -> exited by stop/target) and keeps answering it as new trades
close, instead of that being something reconstructed by hand each time.
See [[production-trading-state]].

"Clean" is deliberately narrow and joins TWO independent sources rather
than trusting either alone:
  - closed_trades.py's close_reason/pnl_status - was the exit actually a
    stop/target (or, for options, the automated pre-expiration safety
    close) firing on its own, with both fill prices known? A manual
    reconciliation (close_reason="manual_reconciliation_position_absent")
    is a real, sometimes-necessary outcome, but it is a human catching a
    problem, not the system completing its own job.
  - overnight_orders.py's lifecycle_history/position_absent_unexplained -
    did the SAME entry ever pass through a failure or human-intervention
    state on its way there? A trade can still close via a genuine
    stop/target fill after first sitting in PROTECTION_FAILED for an hour
    (see the 2026-09-10 reconciliation fix) - that is a recovered trade,
    not a clean one, and only the overnight_orders side can tell the two
    apart since closed_trades.py never recorded the detour.

Never called from the scan - this is evidence a human (or the next
Claude session) reads to decide whether the 2026-09-11 "no" has become a
"yes" yet, not a decision itself."""

from typing import Any, Dict, List, Optional

import order_lifecycle as ol

from .closed_trades import list_closed_trades
from .overnight_orders import list_overnight_orders

# UTC timestamp of b6fef01, the last of the five reliability fixes that
# shipped 2026-09-10/12 (Fix A c532f6c, reconciliation af7bf2f, Fix B
# faaa0ee, churn guard 70a7e64, duplicate-position guard b6fef01) - see
# [[production-trading-state]]. A trade entered before this line predates
# fixes that directly address the orphan-cascade failure mode, so counting
# it against (or for) the CURRENT system's clean rate would misrepresent
# what's actually being measured. Excluded from the "since fixes" figures
# below, not from the all-time ones.
FIXES_COMPLETE_AT = "2026-09-13T01:17:47+00:00"

# The number of consecutive/cumulative clean trades since the fixes that
# the 2026-09-11 assessment implied as a reasonable next evidence bar
# (10-15 - see [[production-trading-state]] and the Paper to AUM plan's
# Gate 1). A target, not a pass/fail threshold enforced anywhere in code.
READINESS_TARGET_CLEAN_TRADES = 12

# A close_reason that means the position exited through its OWN resting
# stop/target order actually filling, or (options) the automated
# pre-expiration safety close - see the four record_closed_trade call
# sites in app.py for the complete set of values ever written.
CLEAN_CLOSE_REASONS = {
    "target_exit_executed",
    "stop_filled",
    "target_filled",
    "option_target_reached",
    "option_stop_reached",
    "option_expiration_safety_close",
}

# lifecycle_history states a clean run may visit. Anything else appearing
# even once - PROTECTION_FAILED, UNKNOWN_SUBMISSION_STATE,
# MANUAL_LINK_IN_PROGRESS, MANUALLY_RESOLVED_NO_ORDER - means the entry
# needed a failure path or human intervention at some point, even if it
# eventually reached CLOSED.
CLEAN_LIFECYCLE_STATES = {
    ol.ENTRY_SUBMITTED,
    ol.ENTRY_PARTIALLY_FILLED,
    ol.ENTRY_FILLED,
    ol.PROTECTION_PENDING,
    ol.PROTECTION_CONFIRMED_ACTIVE,
    ol.CLOSED,
}


def _disqualifying_reasons(entry: Optional[Dict[str, Any]], closed_trade: Dict[str, Any]) -> List[str]:
    """Every reason this trade does NOT count as clean - empty means clean.
    Deliberately collects all of them rather than short-circuiting on the
    first, so a human reading one row can see everything that happened,
    not just whichever check ran first."""
    reasons: List[str] = []

    close_reason = closed_trade.get("close_reason")
    if close_reason not in CLEAN_CLOSE_REASONS:
        reasons.append(f"close_reason {close_reason!r} was not a self-executed stop/target/expiration exit")

    if closed_trade.get("pnl_status") != "complete":
        reasons.append(f"pnl_status {closed_trade.get('pnl_status')!r} - a fill price was never confirmed")

    if entry is None:
        # No matching overnight_orders record - can't be ruled clean,
        # since lifecycle_history is exactly what would show a detour.
        # Fails closed rather than assuming the best about missing data.
        reasons.append("no matching overnight_orders entry - lifecycle history unknown")
        return reasons

    if entry.get("position_absent_unexplained"):
        reasons.append("flagged position_absent_unexplained at some point")

    history = entry.get("lifecycle_history") or []
    visited = {str(step.get("state")) for step in history if isinstance(step, dict)}
    bad_states = sorted(visited - CLEAN_LIFECYCLE_STATES)
    if bad_states:
        reasons.append(f"lifecycle_history touched {bad_states}")

    return reasons


def _is_since_fixes(entry_timestamp: object) -> bool:
    """String comparison, not datetime parsing - every timestamp on this
    path (order_lifecycle._now_iso, overnight_orders.record_overnight_order)
    is stamped datetime.now(timezone.utc).isoformat(), which sorts
    correctly as a string for any two values in that same fixed-offset
    ("+00:00") format. A timestamp that doesn't look like that (missing,
    malformed, or a legacy record predating this format) fails closed to
    False - excluded from the "since fixes" figures rather than guessed
    at, same discipline as pnl_status/average_price elsewhere in this
    codebase."""
    if not isinstance(entry_timestamp, str) or not entry_timestamp:
        return False
    return entry_timestamp >= FIXES_COMPLETE_AT


def build_lifecycle_summary(user_id: str) -> Dict[str, Any]:
    closed_trades = list_closed_trades(user_id)  # newest-first
    entries_by_trade_id = {
        str(entry.get("entry_client_order_id")): entry
        for entry in list_overnight_orders(user_id)
        if entry.get("entry_client_order_id")
    }

    trades: List[Dict[str, Any]] = []
    clean_count = 0
    since_fixes_total = 0
    since_fixes_clean = 0

    for trade in closed_trades:
        entry_client_order_id = trade.get("entry_client_order_id")
        entry = entries_by_trade_id.get(str(entry_client_order_id)) if entry_client_order_id else None
        reasons = _disqualifying_reasons(entry, trade)
        is_clean = not reasons
        since_fixes = _is_since_fixes(trade.get("entry_timestamp"))

        if is_clean:
            clean_count += 1
        if since_fixes:
            since_fixes_total += 1
            if is_clean:
                since_fixes_clean += 1

        trades.append({
            "trade_id": trade.get("trade_id") or entry_client_order_id,
            "ticker": trade.get("ticker"),
            "entry_timestamp": trade.get("entry_timestamp"),
            "exit_timestamp": trade.get("exit_timestamp"),
            "close_reason": trade.get("close_reason"),
            "net_realized_pnl": trade.get("net_realized_pnl"),
            "is_clean": is_clean,
            "is_since_fixes": since_fixes,
            "disqualifying_reasons": reasons,
        })

    total = len(closed_trades)
    return {
        "total_closed_trades": total,
        "clean_lifecycle_count": clean_count,
        "clean_lifecycle_rate_percent": round(clean_count / total * 100, 1) if total else None,
        "since_fixes_total": since_fixes_total,
        "since_fixes_clean_count": since_fixes_clean,
        "since_fixes_clean_rate_percent": (
            round(since_fixes_clean / since_fixes_total * 100, 1) if since_fixes_total else None
        ),
        "readiness_target_clean_trades": READINESS_TARGET_CLEAN_TRADES,
        "readiness_progress_percent": round(
            min(since_fixes_clean / READINESS_TARGET_CLEAN_TRADES, 1.0) * 100, 1
        ),
        "fixes_complete_at": FIXES_COMPLETE_AT,
        "trades": trades,
    }
