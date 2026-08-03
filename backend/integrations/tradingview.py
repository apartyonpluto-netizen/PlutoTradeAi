from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
ALERTS_FILE = DATA_DIR / "tradingview_alerts.json"


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ALERTS_FILE.exists():
        ALERTS_FILE.write_text("[]", encoding="utf-8")


def _read_alerts() -> List[Dict[str, Any]]:
    _ensure_storage()
    try:
        payload = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    if not isinstance(payload, list):
        payload = []
    return [item for item in payload if isinstance(item, dict)]


def _write_alerts(alerts: List[Dict[str, Any]]) -> None:
    _ensure_storage()
    ALERTS_FILE.write_text(json.dumps(alerts, indent=2), encoding="utf-8")


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


def save_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_alert_payload(payload)
    alerts = _read_alerts()
    alerts.insert(0, normalized)
    _write_alerts(alerts[:500])
    return normalized


def get_latest_alert() -> Dict[str, Any]:
    alerts = _read_alerts()
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


def get_tradingview_status() -> Dict[str, Any]:
    alerts = _read_alerts()
    return {
        "webhook_ready": True,
        "signals_received": len(alerts),
        "latest_alert": get_latest_alert(),
        "execution_enabled": False,
        "approval_required": True,
        "emergency_kill_switch_placeholder": True,
        "storage_file": str(ALERTS_FILE),
        "timestamp": _now_iso(),
    }

