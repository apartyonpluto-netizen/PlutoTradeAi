from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .base_broker import BaseBroker


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebullBroker(BaseBroker):
    def __init__(self) -> None:
        super().__init__(name="webull", execution_enabled=False, paper_mode=True)

    def connect(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "paper_mode",
            "message": "Webull paper mode enabled.",
            "connected_at": _now_iso(),
            "timestamp": _now_iso(),
        }

    def disconnect(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "not_connected",
            "message": "Webull disconnected (paper mode available when reconnecting).",
            "timestamp": _now_iso(),
        }

    def test_connection(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "paper_mode",
            "message": "Paper-trade lane healthy.",
            "timestamp": _now_iso(),
        }

    def get_account_status(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "status": "paper_mode",
            "execution_enabled": False,
            "paper_mode": True,
            "approval_required": True,
            "timestamp": _now_iso(),
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        return []

    def place_order(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "accepted": False,
            "executed": False,
            "reason": "Live execution disabled. Use paper_trade for simulation.",
            "order_preview": order_payload,
            "timestamp": _now_iso(),
        }

    def paper_trade(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "accepted": True,
            "executed": False,
            "status": "simulated",
            "message": "Paper trade accepted for simulation only.",
            "order_preview": order_payload,
            "timestamp": _now_iso(),
        }

