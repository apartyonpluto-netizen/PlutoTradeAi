"""One daily-loss figure for the home meter and the scan's new-entry halt.

Both must use the broker's total_day_profit_loss. When the margin account
balance is in hand, its day P/L is added to the cash account's: option and
short positions live there, and a cash-only figure would ignore them. The
percent is taken from the same equity the scan already sizes against (virtual
net account value when a seed is recorded, otherwise the cash account's net
liquidation value).

Closed-trade sums ("Today after fees" on the home bar) are a different
number and are not this cap.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _day_pnl(balance: Optional[Dict[str, Any]]) -> float:
    if not balance:
        return 0.0
    try:
        return float(balance.get("total_day_profit_loss") or 0)
    except (TypeError, ValueError):
        return 0.0


def combined_day_pnl(*balances: Optional[Dict[str, Any]]) -> float:
    """Sum of broker total_day_profit_loss. None balances contribute nothing,
    so a missing margin account does not change the cash figure."""
    return round(sum(_day_pnl(balance) for balance in balances), 2)


def evaluate(day_pnl: float, equity: float, limit_percent: float) -> Dict[str, Any]:
    """halted is true when day_pnl is at or below -equity * limit_percent / 100.

    limit_percent <= 0 disables the halt. equity <= 0 with a positive limit
    does not invent a cap (there is no balance to take a percent of).
    """
    try:
        day_pnl = float(day_pnl)
        equity = float(equity)
        limit_percent = float(limit_percent)
    except (TypeError, ValueError):
        day_pnl, equity, limit_percent = 0.0, 0.0, 0.0
    cap = equity * (limit_percent / 100.0) if limit_percent > 0 and equity > 0 else 0.0
    loss = abs(day_pnl) if day_pnl < 0 else 0.0
    used = round(loss / cap * 100.0, 1) if cap else 0.0
    halted = bool(cap) and day_pnl <= -cap
    message = ""
    if halted:
        message = (
            f"Daily loss limit reached (today's P/L ${day_pnl:.2f} vs -${cap:.2f} limit, "
            f"{limit_percent:.1f}% of ${equity:,.2f} balance). No new trades until tomorrow."
        )
    return {
        "day_pnl": round(day_pnl, 2),
        "equity": round(equity, 2),
        "limit_percent": limit_percent,
        "cap_dollars": round(cap, 2),
        "loss_dollars": round(loss, 2),
        "used_percent": used,
        "halted": halted,
        "message": message,
        "source": "broker_total_day_profit_loss",
    }
