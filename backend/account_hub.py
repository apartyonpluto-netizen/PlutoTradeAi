from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"

STATUS_NOT_CONNECTED = "Not Connected"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_platform(platform: str) -> str:
    normalized = (platform or "").strip().lower()
    if normalized in {"morgan stanley e*trade", "etrade", "e*trade"}:
        return "etrade"
    if normalized == "webull":
        return "webull"
    if normalized == "tradingview":
        return "tradingview"
    raise ValueError("Unsupported platform.")


def _default_accounts() -> List[Dict[str, Any]]:
    return [
        {
            "platform": "etrade",
            "display_name": "Morgan Stanley E*TRADE",
            "status": STATUS_NOT_CONNECTED,
            "purpose": "real trade execution",
            "last_sync": "",
            "permissions": ["read_balances", "read_positions", "place_orders_with_approval"],
            "paper_mode": False,
            "trading_enabled": False,
            "webhook_url": "",
            "account_type": "Brokerage",
            "approval_mode": True,
        },
        {
            "platform": "webull",
            "display_name": "Webull",
            "status": "Paper Mode",
            "purpose": "paper trade / insurance lane",
            "last_sync": "",
            "permissions": ["paper_trade", "market_data"],
            "paper_mode": True,
            "trading_enabled": False,
            "webhook_url": "",
            "risk_simulation_enabled": True,
        },
        {
            "platform": "tradingview",
            "display_name": "TradingView",
            "status": STATUS_NOT_CONNECTED,
            "purpose": "charting, analytics, webhook alerts",
            "last_sync": "",
            "permissions": ["signal_ingest", "alert_webhooks"],
            "paper_mode": False,
            "trading_enabled": False,
            "webhook_url": "",
            "alert_status": "Idle",
            "last_signal_received": "",
        },
    ]


def _coerce_account(raw: Dict[str, Any]) -> Dict[str, Any]:
    account = dict(raw)
    account["platform"] = _normalize_platform(str(account.get("platform", "")))
    account.setdefault("display_name", account["platform"].title())
    account.setdefault("status", STATUS_NOT_CONNECTED)
    account.setdefault("purpose", "")
    account.setdefault("last_sync", "")
    account.setdefault("permissions", [])
    account.setdefault("paper_mode", False)
    account.setdefault("trading_enabled", False)
    account.setdefault("webhook_url", "")
    if account["platform"] == "etrade":
        account.setdefault("account_type", "Brokerage")
        account.setdefault("approval_mode", True)
        if account["status"] == STATUS_NOT_CONNECTED:
            account["trading_enabled"] = False
    if account["platform"] == "webull":
        account.setdefault("risk_simulation_enabled", True)
        account["paper_mode"] = bool(account.get("paper_mode", True))
        account["trading_enabled"] = False
    if account["platform"] == "tradingview":
        account.setdefault("alert_status", "Idle")
        account.setdefault("last_signal_received", "")
        account["trading_enabled"] = False
    return account


def _ensure_accounts_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ACCOUNTS_FILE.exists():
        return
    ACCOUNTS_FILE.write_text(json.dumps(_default_accounts(), indent=2), encoding="utf-8")


def _load_accounts() -> List[Dict[str, Any]]:
    _ensure_accounts_file()
    try:
        payload = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_accounts()
        _save_accounts(payload)
    if not isinstance(payload, list):
        payload = _default_accounts()
        _save_accounts(payload)
    accounts = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            accounts.append(_coerce_account(row))
        except ValueError:
            continue
    return _hydrate_missing_accounts(accounts)


def _save_accounts(accounts: List[Dict[str, Any]]) -> None:
    _ensure_accounts_file()
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


def _hydrate_missing_accounts(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexed = {row["platform"]: row for row in accounts if row.get("platform")}
    for default_row in _default_accounts():
        platform = default_row["platform"]
        if platform not in indexed:
            indexed[platform] = default_row
            continue
        merged = {**default_row, **indexed[platform]}
        indexed[platform] = _coerce_account(merged)
    ordered = [indexed["etrade"], indexed["webull"], indexed["tradingview"]]
    _save_accounts(ordered)
    return ordered


def _find_account(accounts: List[Dict[str, Any]], platform: str) -> Tuple[Dict[str, Any], int]:
    for index, account in enumerate(accounts):
        if account.get("platform") == platform:
            return account, index
    raise ValueError("Platform account record not found.")


def _generate_webhook_url() -> str:
    token = uuid.uuid4().hex[:14]
    return f"/api/tradingview/webhook?token={token}"


def get_accounts() -> List[Dict[str, Any]]:
    return _load_accounts()


def connect_account(platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts()
    account, index = _find_account(accounts, normalized_platform)

    if normalized_platform == "etrade":
        account["status"] = "Sandbox"
        account["approval_mode"] = True
        account["trading_enabled"] = False
    elif normalized_platform == "webull":
        account["status"] = "Paper Mode"
        account["paper_mode"] = True
        account["risk_simulation_enabled"] = True
        account["trading_enabled"] = False
    elif normalized_platform == "tradingview":
        account["status"] = "Webhook Ready"
        if not account.get("webhook_url"):
            account["webhook_url"] = _generate_webhook_url()
        account["alert_status"] = "Listening"
        account["trading_enabled"] = False

    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(accounts)
    return account


def disconnect_account(platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts()
    account, index = _find_account(accounts, normalized_platform)
    account["status"] = STATUS_NOT_CONNECTED
    account["trading_enabled"] = False
    account["last_sync"] = ""

    if normalized_platform == "webull":
        account["paper_mode"] = True
        account["risk_simulation_enabled"] = True
    if normalized_platform == "tradingview":
        account["webhook_url"] = ""
        account["alert_status"] = "Idle"
        account["last_signal_received"] = ""

    accounts[index] = account
    _save_accounts(accounts)
    return account


def test_account(platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts()
    account, index = _find_account(accounts, normalized_platform)

    if account.get("status") == STATUS_NOT_CONNECTED:
        raise ValueError("Connect this account before testing.")

    account["last_sync"] = _now_iso()
    if normalized_platform == "etrade":
        account["status"] = "Connected"
    if normalized_platform == "tradingview":
        account["alert_status"] = "Webhook Ready"

    accounts[index] = account
    _save_accounts(accounts)
    return account


def update_trading_enabled(platform: str, trading_enabled: bool) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    if normalized_platform != "etrade":
        raise ValueError("Trading enabled toggle is only available for E*TRADE.")

    accounts = _load_accounts()
    account, index = _find_account(accounts, normalized_platform)
    if account.get("status") != "Connected":
        account["trading_enabled"] = False
        raise ValueError("E*TRADE live trading requires Connected status and approval mode.")

    account["approval_mode"] = True
    account["trading_enabled"] = bool(trading_enabled)
    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(accounts)
    return account


def ensure_tradingview_webhook(platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    if normalized_platform != "tradingview":
        raise ValueError("Webhook URL can only be generated for TradingView.")

    accounts = _load_accounts()
    account, index = _find_account(accounts, normalized_platform)
    if not account.get("webhook_url"):
        account["webhook_url"] = _generate_webhook_url()
    account["status"] = "Webhook Ready"
    account["alert_status"] = "Listening"
    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(accounts)
    return account


def record_tradingview_signal(payload: Dict[str, Any]) -> Dict[str, Any]:
    accounts = _load_accounts()
    account, index = _find_account(accounts, "tradingview")
    account["status"] = account.get("status", "Webhook Ready")
    if account["status"] == STATUS_NOT_CONNECTED:
        account["status"] = "Webhook Ready"
    if not account.get("webhook_url"):
        account["webhook_url"] = _generate_webhook_url()
    account["alert_status"] = "Signal Received"
    account["last_signal_received"] = _now_iso()
    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(accounts)
    return {
        "ok": True,
        "signal_received": True,
        "platform": "tradingview",
        "payload": payload,
        "never_auto_execute": True,
        "guardrail": "TradingView signals are informational and require router approval before any order action.",
    }
