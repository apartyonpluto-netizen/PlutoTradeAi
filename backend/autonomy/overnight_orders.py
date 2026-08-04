from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _orders_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "overnight_orders.json"


def list_overnight_orders(user_id: str) -> List[Dict[str, Any]]:
    orders_file = _orders_file(user_id)
    if not orders_file.exists():
        return []
    try:
        payload = json.loads(orders_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def record_overnight_order(user_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    orders = list_overnight_orders(user_id)
    entry = {**entry, "logged_at": _now_iso()}
    orders.insert(0, entry)
    _orders_file(user_id).write_text(json.dumps(orders, indent=2), encoding="utf-8")
    return entry
