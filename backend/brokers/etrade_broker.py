from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .base_broker import BaseBroker


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ETradeBroker(BaseBroker):
    def __init__(self) -> None:
        super().__init__(name="etrade", execution_enabled=False, paper_mode=False)

    def connect(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "not_connected",
            "message": "E*TRADE execution is disabled by default until approval workflow is configured.",
            "connected_at": None,
            "timestamp": _now_iso(),
        }

    def disconnect(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "not_connected",
            "message": "E*TRADE disconnected.",
            "timestamp": _now_iso(),
        }

    def test_connection(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "not_connected",
            "message": "E*TRADE test blocked because execution lane is disabled.",
            "timestamp": _now_iso(),
        }

    def get_account_status(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "not_connected",
            "execution_enabled": False,
            "approval_required": True,
            "paper_mode": False,
            "timestamp": _now_iso(),
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        return []

    def place_order(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "accepted": False,
            "executed": False,
            "reason": "Execution disabled: live E*TRADE routing is not enabled.",
            "order_preview": order_payload,
            "timestamp": _now_iso(),
        }

    def paper_trade(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "accepted": False,
            "executed": False,
            "reason": "E*TRADE paper trade is not configured in this foundation mode.",
            "order_preview": order_payload,
            "timestamp": _now_iso(),
        }

