from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
ALERTS_FILE = BASE_DIR / "data" / "alerts.json"
DISMISSED_FILE = BASE_DIR / "data" / "dismissed_alerts.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_files() -> None:
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ALERTS_FILE.exists():
        ALERTS_FILE.write_text("[]", encoding="utf-8")
    if not DISMISSED_FILE.exists():
        DISMISSED_FILE.write_text("[]", encoding="utf-8")


def _load(path: Path) -> List[Dict[str, str]]:
    _ensure_files()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = []
    return payload if isinstance(payload, list) else []


def _save(path: Path, rows: List[Dict[str, str]]) -> None:
    _ensure_files()
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _id(alert_type: str, ticker: str, message: str) -> str:
    digest = hashlib.sha1(f"{alert_type}|{ticker}|{message}".encode("utf-8")).hexdigest()[:12]
    return f"{alert_type}-{ticker or 'global'}-{digest}"


def add_manual_alert(payload: Dict[str, str]) -> Dict[str, str]:
    message = (payload.get("message", "") or "").strip()
    if not message:
        raise ValueError("Alert message is required.")
    ticker = (payload.get("ticker", "") or "").strip().upper()
    alert_type = (payload.get("type", "System") or "System").strip()
    alert = {
        "id": _id(alert_type, ticker, message),
        "type": alert_type,
        "ticker": ticker,
        "message": message,
        "created_at": _now_iso(),
    }
    rows = _load(ALERTS_FILE)
    if not any(item.get("id") == alert["id"] for item in rows):
        rows.insert(0, alert)
        _save(ALERTS_FILE, rows)
    return alert


def dismiss_alert(alert_id: str) -> None:
    value = (alert_id or "").strip()
    if not value:
        raise ValueError("Alert ID is required.")
    dismissed = _load(DISMISSED_FILE)
    if any(item.get("id") == value for item in dismissed):
        return
    dismissed.append({"id": value, "dismissed_at": _now_iso()})
    _save(DISMISSED_FILE, dismissed)


def dismiss_alerts(alert_ids: Sequence[str]) -> int:
    cleaned = [value.strip() for value in alert_ids if value and value.strip()]
    if not cleaned:
        raise ValueError("At least one alert ID is required.")
    dismissed = _load(DISMISSED_FILE)
    existing = {item.get("id", "") for item in dismissed}
    created = 0
    for alert_id in cleaned:
        if alert_id in existing:
            continue
        dismissed.append({"id": alert_id, "dismissed_at": _now_iso()})
        existing.add(alert_id)
        created += 1
    if created:
        _save(DISMISSED_FILE, dismissed)
    return created


def _dismissed_ids() -> set[str]:
    return {item.get("id", "") for item in _load(DISMISSED_FILE)}


def build_system_alerts(
    scanner_rows: Sequence[Dict[str, object]],
    suggestions: Sequence[Dict[str, str]],
    mission_alerts: Sequence[Dict[str, str]] | None = None,
) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []
    for row in scanner_rows:
        if float(row.get("relative_volume", 0)) < 1.7:
            continue
        ticker = str(row.get("ticker", ""))
        message = f"{ticker} volume spike detected at {row.get('relative_volume')}x relative volume."
        alerts.append(
            {
                "id": _id("Market Scanner", ticker, message),
                "type": "Market Scanner",
                "ticker": ticker,
                "message": message,
                "created_at": _now_iso(),
            }
        )
    for row in suggestions:
        ticker = row.get("ticker", "")
        message = row.get("reason", row.get("notes", "Scanner suggestion ready"))
        alerts.append(
            {
                "id": _id("Watchlist", ticker, message),
                "type": "Watchlist",
                "ticker": ticker,
                "message": message,
                "created_at": _now_iso(),
            }
        )
    for row in mission_alerts or []:
        ticker = row.get("ticker", "")
        message = row.get("message", "Upcoming opportunity requires review")
        alerts.append(
            {
                "id": _id("Mission Control", ticker, message),
                "type": "Mission Control",
                "ticker": ticker,
                "message": message,
                "created_at": _now_iso(),
            }
        )
    return alerts


def get_alerts_snapshot(system_alerts: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    dismissed = _dismissed_ids()
    manual = [row for row in _load(ALERTS_FILE) if row.get("id") not in dismissed]
    seen = {row.get("id") for row in manual}
    merged = list(manual)
    for row in system_alerts:
        if row.get("id") in dismissed or row.get("id") in seen:
            continue
        merged.append(row)
        seen.add(row.get("id"))
    merged.sort(key=lambda row: row.get("created_at", ""), reverse=True)
    return merged[:80]
