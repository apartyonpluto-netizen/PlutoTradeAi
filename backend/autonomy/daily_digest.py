from __future__ import annotations

"""A single, human-readable "what happened, what needs me" summary - the
legitimate version of the "Chief of Staff" idea (routes work, checks every
handoff, brings the one call that needs a person): a read-only triage layer
over data this app already records (scan_run_log.py, overnight_orders.py,
closed_trades.py), not a new agent and not a new trading decision path.
Nothing here ever writes anything or influences _run_autonomous_trade_scan_locked -
same "reporting only" boundary as performance_report.py (Tier 1 of the
"make autonomy learn" roadmap)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import order_lifecycle as ol

from .closed_trades import list_closed_trades
from .overnight_orders import list_overnight_orders
from .scan_run_log import list_scan_runs

DEFAULT_WINDOW_HOURS = 24


def _parse_timestamp(raw: object) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _within_window(raw_timestamp: object, since: datetime) -> bool:
    parsed = _parse_timestamp(raw_timestamp)
    return parsed is not None and parsed >= since


def _attention_items_from_open_positions(overnight_orders: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Only the two flags that already freeze new autonomous entries
    platform-wide (see _flag_ambiguous_exit_unresolved /
    _reconcile_protective_leg_quantity in app.py) - these are the "someone
    needs to look at this today" cases, not routine status."""
    items: List[Dict[str, str]] = []
    for order in overnight_orders:
        ticker = order.get("ticker", "?")
        if order.get("ambiguous_exit_unresolved"):
            items.append({"severity": "critical", "message": f"{ticker}: ambiguous exit needs manual review"})
        if order.get("stop_protection_gap") or order.get("target_protection_gap"):
            items.append({"severity": "critical", "message": f"{ticker}: protection gap needs manual review"})
    return items


def build_daily_digest(
    user_id: str,
    monitor_heartbeat: Optional[Dict[str, Any]] = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """monitor_heartbeat is passed in, not fetched here, because the
    fast/full/continuous monitor health checks (app.py's
    _fast_monitor_health_status and friends) are platform-wide, not
    per-user, and app.py already computes them elsewhere - this module
    stays free of any app.py import, matching every other autonomy/*.py
    module's layering."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    scan_runs = list_scan_runs(user_id)
    overnight_orders = list_overnight_orders(user_id)
    closed_trades = list_closed_trades(user_id)

    recent_scan_runs = [r for r in scan_runs if _within_window(r.get("actual_start_time"), since)]
    recent_closed_trades = [t for t in closed_trades if _within_window(t.get("exit_timestamp"), since)]

    attention_items = _attention_items_from_open_positions(overnight_orders)

    failed_runs = [r for r in recent_scan_runs if r.get("status") == "failed"]
    if failed_runs:
        most_recent_error = failed_runs[0].get("error") or failed_runs[0].get("reason") or "unknown error"
        attention_items.append(
            {
                "severity": "warning",
                "message": f"{len(failed_runs)} scan run(s) failed in the last {window_hours}h - most recent: {most_recent_error}",
            }
        )

    unhealthy_monitors = []
    if monitor_heartbeat:
        for key, label in (
            ("fast_monitor_healthy", "fast monitor"),
            ("full_scan_healthy", "full-scan monitor"),
            ("continuous_monitor_healthy", "continuous monitor"),
        ):
            if monitor_heartbeat.get(key) is False:
                unhealthy_monitors.append(label)
    if unhealthy_monitors:
        attention_items.append({"severity": "warning", "message": f"Unhealthy: {', '.join(unhealthy_monitors)}"})

    if attention_items:
        headline = attention_items[0]["message"]
    else:
        headline = "Nothing needs your attention right now."

    # Real autonomous entries only - source is only ever set for the manual
    # test tools (Stage 2's manual_test_order, Stage 3's stage3_test_order),
    # never for a real candidate _run_autonomous_trade_scan_locked placed.
    open_positions = [
        {
            "ticker": order.get("ticker"),
            "quantity": order.get("quantity"),
            "display_status": order.get("display_status"),
            "lifecycle_state": order.get("lifecycle_state"),
        }
        for order in overnight_orders
        if not order.get("source")
        and order.get("lifecycle_state")
        and order.get("lifecycle_state") not in ol.TERMINAL_STATES
    ]

    total_candidates_found = sum(r.get("candidates_found") or 0 for r in recent_scan_runs)
    total_candidates_qualifying = sum(r.get("candidates_qualifying") or 0 for r in recent_scan_runs)
    total_orders_placed = sum((r.get("orders_outcomes") or {}).get("placed", 0) for r in recent_scan_runs)

    known_pnl_trades = [t for t in recent_closed_trades if t.get("net_realized_pnl") is not None]
    total_pnl = sum(t["net_realized_pnl"] for t in known_pnl_trades)

    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "since": since.isoformat(),
        "headline": headline,
        "attention_items": attention_items,
        "scan_activity": {
            "total_scans": len(recent_scan_runs),
            "failed_scans": len(failed_runs),
            "candidates_found": total_candidates_found,
            "candidates_qualifying": total_candidates_qualifying,
            "orders_placed": total_orders_placed,
        },
        "open_positions": open_positions,
        "closed_trades": {
            "count": len(recent_closed_trades),
            "wins": sum(1 for t in known_pnl_trades if t["net_realized_pnl"] > 0),
            "losses": sum(1 for t in known_pnl_trades if t["net_realized_pnl"] < 0),
            "total_pnl": round(total_pnl, 2) if known_pnl_trades else None,
            "recent": [
                {
                    "ticker": t.get("ticker"),
                    "exit_type": t.get("exit_type"),
                    "net_realized_pnl": t.get("net_realized_pnl"),
                    "exit_timestamp": t.get("exit_timestamp"),
                }
                for t in recent_closed_trades
            ],
        },
    }
