from __future__ import annotations

import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

if __package__:
    from .integrations import webull as webull_api
else:
    from integrations import webull as webull_api

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

STATUS_NOT_CONNECTED = "Not Connected"


def _accounts_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    return USER_DATA_ROOT / user_id / "accounts.json"


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
            "status": STATUS_NOT_CONNECTED,
            "purpose": "paper trade / insurance lane (Webull OpenAPI sandbox)",
            "last_sync": "",
            "permissions": ["paper_trade", "market_data"],
            "paper_mode": True,
            "trading_enabled": False,
            "webhook_url": "",
            "risk_simulation_enabled": True,
            "account_number": "",
            "cash_balance": "",
            "buying_power": "",
            "net_liquidation_value": "",
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
        account.setdefault("account_number", "")
        account.setdefault("cash_balance", "")
        account.setdefault("buying_power", "")
        account.setdefault("net_liquidation_value", "")
        account["paper_mode"] = bool(account.get("paper_mode", True))
        account["trading_enabled"] = False
    if account["platform"] == "tradingview":
        account.setdefault("alert_status", "Idle")
        account.setdefault("last_signal_received", "")
        account["trading_enabled"] = False
    return account


def _ensure_accounts_file(user_id: str) -> Path:
    accounts_file = _accounts_file(user_id)
    accounts_file.parent.mkdir(parents=True, exist_ok=True)
    if not accounts_file.exists():
        accounts_file.write_text(json.dumps(_default_accounts(), indent=2), encoding="utf-8")
    return accounts_file


def _load_accounts(user_id: str) -> List[Dict[str, Any]]:
    accounts_file = _ensure_accounts_file(user_id)
    try:
        payload = json.loads(accounts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_accounts()
        _save_accounts(user_id, payload)
    if not isinstance(payload, list):
        payload = _default_accounts()
        _save_accounts(user_id, payload)
    accounts = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            accounts.append(_coerce_account(row))
        except ValueError:
            continue
    return _hydrate_missing_accounts(user_id, accounts)


def _save_accounts(user_id: str, accounts: List[Dict[str, Any]]) -> None:
    accounts_file = _ensure_accounts_file(user_id)
    accounts_file.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


def _hydrate_missing_accounts(user_id: str, accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexed = {row["platform"]: row for row in accounts if row.get("platform")}
    for default_row in _default_accounts():
        platform = default_row["platform"]
        if platform not in indexed:
            indexed[platform] = default_row
            continue
        merged = {**default_row, **indexed[platform]}
        indexed[platform] = _coerce_account(merged)
    ordered = [indexed["etrade"], indexed["webull"], indexed["tradingview"]]
    _save_accounts(user_id, ordered)
    return ordered


def _find_account(accounts: List[Dict[str, Any]], platform: str) -> Tuple[Dict[str, Any], int]:
    for index, account in enumerate(accounts):
        if account.get("platform") == platform:
            return account, index
    raise ValueError("Platform account record not found.")


def _generate_webhook_url() -> str:
    token = secrets.token_hex(16)
    return f"/api/tradingview/webhook?token={token}"


def _extract_token(webhook_url: str) -> str:
    query = parse_qs(urlparse(webhook_url or "").query)
    return (query.get("token") or [""])[0]


def _sync_webull_account(account: Dict[str, Any]) -> None:
    """Connect/refresh a webull account record against the real Webull OpenAPI
    paper-trading sandbox. Raises ValueError (surfaced to the UI) if
    credentials aren't configured or the API call fails - never leaves the
    account silently marked as connected without a verified round trip."""
    if not webull_api.is_configured():
        raise ValueError("Webull API credentials are not configured on the server (WEBULL_APP_KEY / WEBULL_APP_SECRET).")

    accounts = webull_api.get_paper_accounts()
    cash_account = webull_api.find_individual_cash_account(accounts)
    if not cash_account:
        raise ValueError("No Webull paper trading account found for these credentials.")

    balance = webull_api.get_account_balance(cash_account["account_id"])
    account["status"] = "Connected"
    account["paper_mode"] = True
    account["risk_simulation_enabled"] = True
    account["trading_enabled"] = False
    account["account_number"] = cash_account.get("account_number", "")
    account["cash_balance"] = balance.get("total_cash_balance", "")
    account["buying_power"] = (balance.get("account_currency_assets") or [{}])[0].get("buying_power", "")
    account["net_liquidation_value"] = balance.get("total_net_liquidation_value", "")


def verify_tradingview_token(user_id: str, token: str) -> bool:
    """Constant-time check of an incoming webhook token against this user's stored one.

    Fails closed: no stored token or no provided token both reject.
    """
    accounts = _load_accounts(user_id)
    account, _ = _find_account(accounts, "tradingview")
    expected = _extract_token(account.get("webhook_url", ""))
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)


def get_accounts(user_id: str) -> List[Dict[str, Any]]:
    return _load_accounts(user_id)


def connect_account(user_id: str, platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts(user_id)
    account, index = _find_account(accounts, normalized_platform)

    if normalized_platform == "etrade":
        account["status"] = "Sandbox"
        account["approval_mode"] = True
        account["trading_enabled"] = False
    elif normalized_platform == "webull":
        _sync_webull_account(account)
    elif normalized_platform == "tradingview":
        account["status"] = "Webhook Ready"
        if not account.get("webhook_url"):
            account["webhook_url"] = _generate_webhook_url()
        account["alert_status"] = "Listening"
        account["trading_enabled"] = False

    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(user_id, accounts)
    return account


def disconnect_account(user_id: str, platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts(user_id)
    account, index = _find_account(accounts, normalized_platform)
    account["status"] = STATUS_NOT_CONNECTED
    account["trading_enabled"] = False
    account["last_sync"] = ""

    if normalized_platform == "webull":
        account["paper_mode"] = True
        account["risk_simulation_enabled"] = True
        account["account_number"] = ""
        account["cash_balance"] = ""
        account["buying_power"] = ""
        account["net_liquidation_value"] = ""
    if normalized_platform == "tradingview":
        account["webhook_url"] = ""
        account["alert_status"] = "Idle"
        account["last_signal_received"] = ""

    accounts[index] = account
    _save_accounts(user_id, accounts)
    return account


def test_account(user_id: str, platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    accounts = _load_accounts(user_id)
    account, index = _find_account(accounts, normalized_platform)

    if account.get("status") == STATUS_NOT_CONNECTED:
        raise ValueError("Connect this account before testing.")

    account["last_sync"] = _now_iso()
    if normalized_platform == "etrade":
        account["status"] = "Connected"
    if normalized_platform == "webull":
        _sync_webull_account(account)
    if normalized_platform == "tradingview":
        account["alert_status"] = "Webhook Ready"

    accounts[index] = account
    _save_accounts(user_id, accounts)
    return account


def update_trading_enabled(user_id: str, platform: str, trading_enabled: bool) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    if normalized_platform != "etrade":
        raise ValueError("Trading enabled toggle is only available for E*TRADE.")

    accounts = _load_accounts(user_id)
    account, index = _find_account(accounts, normalized_platform)
    if account.get("status") != "Connected":
        account["trading_enabled"] = False
        raise ValueError("E*TRADE live trading requires Connected status and approval mode.")

    account["approval_mode"] = True
    account["trading_enabled"] = bool(trading_enabled)
    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(user_id, accounts)
    return account


def ensure_tradingview_webhook(user_id: str, platform: str) -> Dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    if normalized_platform != "tradingview":
        raise ValueError("Webhook URL can only be generated for TradingView.")

    accounts = _load_accounts(user_id)
    account, index = _find_account(accounts, normalized_platform)
    if not account.get("webhook_url"):
        account["webhook_url"] = _generate_webhook_url()
    account["status"] = "Webhook Ready"
    account["alert_status"] = "Listening"
    account["last_sync"] = _now_iso()
    accounts[index] = account
    _save_accounts(user_id, accounts)
    return account


def record_tradingview_signal(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    accounts = _load_accounts(user_id)
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
    _save_accounts(user_id, accounts)
    return {
        "ok": True,
        "signal_received": True,
        "platform": "tradingview",
        "payload": payload,
        "never_auto_execute": True,
        "guardrail": "TradingView signals are informational and require router approval before any order action.",
    }
