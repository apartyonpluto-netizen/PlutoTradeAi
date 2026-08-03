from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Paper-trading OpenAPI apps authenticate against a separate sandbox host from
# the live-trading API - api.webull.com rejects sandbox credentials with a
# generic 401 "invalid credentials" error that gives no hint the real problem
# is the endpoint, not the key/secret.
_REGION_ID = "us"
_SANDBOX_ENDPOINT = "api.sandbox.webull.com"


def _get_credentials() -> tuple[str, str]:
    app_key = os.environ.get("WEBULL_APP_KEY", "").strip()
    app_secret = os.environ.get("WEBULL_APP_SECRET", "").strip()
    return app_key, app_secret


def is_configured() -> bool:
    app_key, app_secret = _get_credentials()
    return bool(app_key and app_secret)


def _get_trade_client():
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient

    app_key, app_secret = _get_credentials()
    if not app_key or not app_secret:
        raise ValueError("Webull API credentials are not configured (WEBULL_APP_KEY / WEBULL_APP_SECRET).")

    api_client = ApiClient(app_key, app_secret, _REGION_ID)
    api_client.add_endpoint(_REGION_ID, _SANDBOX_ENDPOINT)
    return TradeClient(api_client)


def get_paper_accounts() -> List[Dict[str, Any]]:
    trade_client = _get_trade_client()
    response = trade_client.account_v2.get_account_list()
    if response.status_code != 200:
        raise ValueError(f"Webull API error (accounts): HTTP {response.status_code}")
    accounts = response.json()
    return accounts if isinstance(accounts, list) else []


def find_individual_cash_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for account in accounts:
        if account.get("account_class") == "INDIVIDUAL_CASH":
            return account
    return accounts[0] if accounts else None


def get_account_balance(account_id: str) -> Dict[str, Any]:
    trade_client = _get_trade_client()
    response = trade_client.account_v2.get_account_balance(account_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (balance): HTTP {response.status_code}")
    return response.json()


def get_account_positions(account_id: str) -> List[Dict[str, Any]]:
    trade_client = _get_trade_client()
    response = trade_client.account_v2.get_account_position(account_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (positions): HTTP {response.status_code}")
    positions = response.json()
    return positions if isinstance(positions, list) else []
