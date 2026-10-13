"""The check that must pass before a live opening order is allowed.

Live stays armed only by the two existing environment switches. This gate
is the extra refusal: even with those switches on, an opening order is
rejected until all of the following are true for that account.

  * at least 30 clean closed trades (the lifecycle tracker's definition)
  * those clean trades are net positive after fees (and modeled paper costs)
  * max drawdown of that equity curve is under the configured limit
  * at least one clean option round trip (entry through an automated exit)

Exits are not blocked. Sandbox orders are not blocked: paper is how the
30 trades get recorded.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from autonomy.closed_trades import list_closed_trades
from autonomy.lifecycle_tracker import build_lifecycle_summary
from paper_costs import performance_net
from webull_credentials import get_virtual_starting_balance

CLEAN_TRADES_REQUIRED = 30
DEFAULT_MAX_DRAWDOWN_PERCENT = 15.0
DRAWDOWN_ENV = "PLUTO_GO_LIVE_MAX_DRAWDOWN_PERCENT"

_bound_user: ContextVar[Optional[str]] = ContextVar("go_live_user", default=None)


class GoLiveNotReady(Exception):
    """Raised before a live opening order is sent. Nothing was placed."""


def bind(user_id: Optional[str]):
    return _bound_user.set(user_id)


def reset(token) -> None:
    _bound_user.reset(token)


def max_drawdown_limit() -> float:
    raw = os.environ.get(DRAWDOWN_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_DRAWDOWN_PERCENT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_DRAWDOWN_PERCENT
    return value if value > 0 else DEFAULT_MAX_DRAWDOWN_PERCENT


def _clean_trades(user_id: str) -> List[Dict[str, Any]]:
    summary = build_lifecycle_summary(user_id)
    clean_ids = {str(row.get("trade_id")) for row in summary.get("trades") or [] if row.get("is_clean")}
    trades = []
    for trade in list_closed_trades(user_id):
        trade_id = str(trade.get("trade_id") or trade.get("entry_client_order_id") or "")
        if trade_id in clean_ids:
            trades.append(trade)
    return trades


def _equity_drawdown(trades_newest_first: List[Dict[str, Any]], start_equity: float) -> float:
    """Max peak-to-trough percent. Trades are newest-first; the curve is walked oldest-first."""
    equity = start_equity if start_equity > 0 else 0.0
    peak = equity
    worst = 0.0
    for trade in reversed(trades_newest_first):
        if trade.get("pnl_status") not in (None, "", "complete"):
            continue
        net = performance_net(trade)
        if net is None:
            continue
        equity += net
        if equity > peak:
            peak = equity
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return round(worst, 2)


def _options_cycle(trades: List[Dict[str, Any]]) -> bool:
    option_reasons = {"option_target_reached", "option_stop_reached", "option_expiration_safety_close"}
    for trade in trades:
        if str(trade.get("instrument_type") or "").upper() != "OPTION":
            continue
        if trade.get("close_reason") in option_reasons and trade.get("pnl_status") in (None, "", "complete"):
            return True
    return False


def evaluate(user_id: str) -> Dict[str, Any]:
    clean = _clean_trades(user_id)
    nets = [performance_net(trade) for trade in clean if trade.get("pnl_status") in (None, "", "complete")]
    known = [net for net in nets if net is not None]
    total = round(sum(known), 2) if known else 0.0
    start = get_virtual_starting_balance(user_id)
    drawdown = _equity_drawdown(clean, start)
    limit = max_drawdown_limit()
    clean_progress = round(min(100.0, len(clean) / CLEAN_TRADES_REQUIRED * 100.0), 1)
    drawdown_progress = round(min(100.0, drawdown / limit * 100.0), 1) if limit else 0.0
    checks = [
        {
            "id": "clean_trades",
            "label": "Clean closed trades",
            "target": CLEAN_TRADES_REQUIRED,
            "actual": len(clean),
            "passed": len(clean) >= CLEAN_TRADES_REQUIRED,
            "detail": f"{len(clean)} of {CLEAN_TRADES_REQUIRED} clean round trips",
            "progress_percent": clean_progress,
        },
        {
            "id": "positive_after_fees",
            "label": "Positive after fees",
            "target": 0.01,
            "actual": total,
            "passed": total > 0,
            "detail": f"clean trades net ${total:,.2f} after fees" if known else "no complete clean trades yet",
            "progress_percent": 100.0 if total > 0 else 0.0,
        },
        {
            "id": "max_drawdown",
            "label": "Max drawdown",
            "target": limit,
            "actual": drawdown,
            "passed": drawdown < limit,
            "detail": f"{drawdown:.1f}% drawdown, limit {limit:g}%",
            "progress_percent": drawdown_progress,
        },
        {
            "id": "options_cycle",
            "label": "Options cycle",
            "target": 1,
            "actual": 1 if _options_cycle(clean) else 0,
            "passed": _options_cycle(clean),
            "detail": "one clean option entry-to-exit is on record" if _options_cycle(clean) else "no clean option round trip yet",
            "progress_percent": 100.0 if _options_cycle(clean) else 0.0,
        },
    ]
    ready = all(check["passed"] for check in checks)
    blockers = [check["detail"] for check in checks if not check["passed"]]
    return {
        "ready": ready,
        "checks": checks,
        "summary": "Go-live gate passed." if ready else "Go-live gate is not passed: " + "; ".join(blockers),
        "clean_trades": len(clean),
        "net_after_fees": total,
        "max_drawdown_percent": drawdown,
        "max_drawdown_limit_percent": limit,
        "options_cycle_complete": _options_cycle(clean),
    }


def refuse_if_not_ready() -> None:
    """Called from the live opening-order path. Sandbox returns immediately.
    No bound account fails closed: an opening live order with no user is refused.
    """
    from integrations.webull import is_live_trading_armed

    if not is_live_trading_armed():
        return
    user_id = _bound_user.get()
    if not user_id:
        raise GoLiveNotReady("Live opening orders are refused until a go-live check can be tied to an account.")
    report = evaluate(user_id)
    if not report["ready"]:
        raise GoLiveNotReady(report["summary"])
