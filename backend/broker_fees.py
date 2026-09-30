"""Fees exactly as the broker reports them on an order.

Webull orders carry `fees`: a list of {"type", "actual_value",
"receivable_value"} (e.g. SEC_FEE, FINRA_FEE on sells) and `commission`
(an object; empty on zero-commission trades). Shape confirmed from Webull
sandbox order history on 2026-09-30 (tests/fixtures/). An empty list with an
empty commission means the broker reported no fees - that is 0.0, not unknown.
A payload without these fields means unknown (None) - never guessed."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def from_order(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{"total", "items"} for one order, or None when the broker payload has
    no fee fields at all."""
    if not isinstance(order, dict) or ("fees" not in order and "commission" not in order):
        return None
    items: List[Dict[str, Any]] = []
    for fee in order.get("fees") or []:
        if isinstance(fee, dict):
            amount = _number(fee.get("actual_value", fee.get("receivable_value")))
            if amount is not None:
                items.append({"type": str(fee.get("type") or "FEE"), "amount": amount})
    commission = order.get("commission")
    if isinstance(commission, dict) and commission:
        amount = _number(commission.get("actual_value", commission.get("amount", commission.get("value"))))
        if amount is not None:
            items.append({"type": "COMMISSION", "amount": amount})
    elif _number(commission) is not None:
        items.append({"type": "COMMISSION", "amount": _number(commission)})
    return {"total": round(sum(i["amount"] for i in items), 4), "items": items}


def combine(per_order: List[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Sum across orders; None if ANY order's fees are unknown."""
    if not per_order or any(entry is None for entry in per_order):
        return None
    items = [item for entry in per_order for item in entry["items"]]
    return {"total": round(sum(entry["total"] for entry in per_order), 4), "items": items}
