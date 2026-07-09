from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
ACCOUNTS_FILE = BASE_DIR / "data" / "accounts.json"
STATUS_NOT_CONNECTED = "Not Connected"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value in {"etrade", "e*trade", "morgan stanley e*trade"}:
        return "etrade"
    if value == "webull":
        return "webull"
    if value == "tradingview":
        return "tradingview"
    raise ValueError("Unsupported platform.")


def _default_accounts() -> List[Dict[str, Any]]:
    return [
        {
            "platform": "tradingview",
            "display_name": "TradingView",
            "status": STATUS_NOT_CONNECTED,
            "purpose": "Signal ingestion and webhook analytics",
            "last_sync": "",
            "permissions": ["signal_ingest", "alert_webhooks"],
            "trading_enabled": False,
            "paper_mode": False,
            "webhook_url": "",
            "alert_status": "Idle",
        },
        {
            "platform": "etrade",
            "display_name": "Morgan Stanley E*TRADE",
            "status": STATUS_NOT_CONNECTED,
            "purpose": "Approved live execution lane",
            "last_sync": "",
            "permissions": ["read_balances", "read_positions", "place_orders_with_approval"],
            "trading_enabled": False,
            "paper_mode": False,
        },
        {
            "platform": "webull",
            "display_name": "Webull",
            "status": "Paper Mode",
            "purpose": "Paper trading insurance lane",
            "last_sync": "",
            "permissions": ["paper_trade", "market_data"],
            "trading_enabled": False,
            "paper_mode": True,
        },
    ]


def _ensure_file() -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ACCOUNTS_FILE.exists():
        return
    ACCOUNTS_FILE.write_text(json.dumps(_default_accounts(), indent=2), encoding="utf-8")


def _load() -> List[Dict[str, Any]]:
    _ensure_file()
    try:
        payload = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_accounts()
        _save(payload)
    if not isinstance(payload, list):
        payload = _default_accounts()
        _save(payload)
    indexed = {row["platform"]: row for row in payload if isinstance(row, dict) and row.get("platform")}
    merged: List[Dict[str, Any]] = []
    for default_row in _default_accounts():
        row = {**default_row, **indexed.get(default_row["platform"], {})}
        merged.append(row)
    return merged


def _save(rows: List[Dict[str, Any]]) -> None:
    _ensure_file()
    ACCOUNTS_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _find(rows: List[Dict[str, Any]], platform: str) -> int:
    for index, row in enumerate(rows):
        if row.get("platform") == platform:
            return index
    raise ValueError("Account record not found.")


def get_accounts() -> List[Dict[str, Any]]:
    return _load()


def connect_account(platform: str) -> Dict[str, Any]:
    platform_key = _normalize_platform(platform)
    rows = _load()
    index = _find(rows, platform_key)
    account = rows[index]
    if platform_key == "tradingview":
        account["status"] = "Webhook Ready"
        account["alert_status"] = "Listening"
        if not account.get("webhook_url"):
            account["webhook_url"] = f"/api/tradingview/webhook?token={uuid.uuid4().hex[:14]}"
    elif platform_key == "etrade":
        account["status"] = "Sandbox"
        account["trading_enabled"] = False
    elif platform_key == "webull":
        account["status"] = "Paper Mode"
        account["paper_mode"] = True
        account["trading_enabled"] = False
    account["last_sync"] = _now_iso()
    rows[index] = account
    _save(rows)
    return account


def disconnect_account(platform: str) -> Dict[str, Any]:
    platform_key = _normalize_platform(platform)
    rows = _load()
    index = _find(rows, platform_key)
    account = rows[index]
    account["status"] = STATUS_NOT_CONNECTED if platform_key != "webull" else "Paper Mode"
    account["trading_enabled"] = False
    account["last_sync"] = ""
    if platform_key == "tradingview":
        account["alert_status"] = "Idle"
    rows[index] = account
    _save(rows)
    return account


def test_account(platform: str) -> Dict[str, Any]:
    platform_key = _normalize_platform(platform)
    rows = _load()
    index = _find(rows, platform_key)
    account = rows[index]
    if platform_key == "etrade":
        account["status"] = "Connected"
    if platform_key == "tradingview":
        account["status"] = "Webhook Ready"
        account["alert_status"] = "Webhook Ready"
    account["last_sync"] = _now_iso()
    rows[index] = account
    _save(rows)
    return account


def update_trading_enabled(platform: str, trading_enabled: bool) -> Dict[str, Any]:
    platform_key = _normalize_platform(platform)
    if platform_key != "etrade":
        raise ValueError("Trading enable toggle is only available for E*TRADE.")
    rows = _load()
    index = _find(rows, platform_key)
    account = rows[index]
    if account.get("status") != "Connected":
        raise ValueError("Run Test Connection after connect before enabling live trading.")
    account["trading_enabled"] = bool(trading_enabled)
    account["last_sync"] = _now_iso()
    rows[index] = account
    _save(rows)
    return account
