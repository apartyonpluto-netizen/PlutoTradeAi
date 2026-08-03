from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _alerts_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "tradingview_alerts.json"


def _read_alerts(user_id: str) -> List[Dict[str, Any]]:
    alerts_file = _alerts_file(user_id)
    if not alerts_file.exists():
        alerts_file.write_text("[]", encoding="utf-8")
    try:
        payload = json.loads(alerts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    if not isinstance(payload, list):
        payload = []
    return [item for item in payload if isinstance(item, dict)]


def _write_alerts(user_id: str, alerts: List[Dict[str, Any]]) -> None:
    _alerts_file(user_id).write_text(json.dumps(alerts, indent=2), encoding="utf-8")


def normalize_alert_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(payload.get("ticker", "") or payload.get("symbol", "")).strip().upper()
    return {
        "ticker": ticker,
        "signal": str(payload.get("signal", payload.get("side", "WAIT"))).strip().upper() or "WAIT",
        "price": payload.get("price", payload.get("close", "data unavailable")),
        "timeframe": str(payload.get("timeframe", "data unavailable")).strip() or "data unavailable",
        "strategy": str(payload.get("strategy", "TradingView Alert")).strip() or "TradingView Alert",
        "confidence": payload.get("confidence", payload.get("score", "data unavailable")),
        "raw_message": str(payload.get("message", "")).strip(),
        "received_at": _now_iso(),
    }


def save_alert(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_alert_payload(payload)
    alerts = _read_alerts(user_id)
    alerts.insert(0, normalized)
    _write_alerts(user_id, alerts[:500])
    return normalized


def get_latest_alert(user_id: str) -> Dict[str, Any]:
    alerts = _read_alerts(user_id)
    if alerts:
        return alerts[0]
    return {
        "ticker": "",
        "signal": "WAIT",
        "price": "data unavailable",
        "timeframe": "data unavailable",
        "strategy": "TradingView Alert",
        "confidence": "data unavailable",
        "raw_message": "",
        "received_at": "",
    }


def get_tradingview_status(user_id: str) -> Dict[str, Any]:
    alerts = _read_alerts(user_id)
    return {
        "webhook_ready": True,
        "signals_received": len(alerts),
        "latest_alert": get_latest_alert(user_id),
        "execution_enabled": False,
        "approval_required": True,
        "emergency_kill_switch_placeholder": True,
        "storage_file": str(_alerts_file(user_id)),
        "timestamp": _now_iso(),
    }
