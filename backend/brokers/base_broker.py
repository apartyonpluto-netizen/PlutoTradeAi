from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseBroker(ABC):
    def __init__(self, name: str, execution_enabled: bool = False, paper_mode: bool = True) -> None:
        self.name = name
        self.execution_enabled = execution_enabled
        self.paper_mode = paper_mode

    @abstractmethod
    def connect(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_account_status(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def paper_trade(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def safety_profile(self) -> Dict[str, Any]:
        return {
            "broker": self.name,
            "execution_enabled": self.execution_enabled,
            "paper_mode": self.paper_mode,
            "approval_required": True,
            "live_trading_default_off": True,
            "emergency_kill_switch_placeholder": True,
            "as_of": _now_iso(),
        }

