from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

_DATA_BASE_URL = "https://data.alpaca.markets/v2"
_TRADING_BASE_URL = "https://paper-api.alpaca.markets/v2"


def _get_credentials() -> tuple[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    api_secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    return api_key, api_secret


def is_configured() -> bool:
    api_key, api_secret = _get_credentials()
    return bool(api_key and api_secret)


def _headers() -> Dict[str, str]:
    api_key, api_secret = _get_credentials()
    if not api_key or not api_secret:
        raise ValueError("Alpaca API credentials are not configured (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY).")
    return {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}


def get_latest_quote(symbol: str) -> Dict[str, Any]:
    response = requests.get(
        f"{_DATA_BASE_URL}/stocks/{symbol.upper()}/quotes/latest",
        headers=_headers(),
        params={"feed": "iex"},
        timeout=10,
    )
    if response.status_code != 200:
        raise ValueError(f"Alpaca API error (quote): HTTP {response.status_code} {response.text}")
    return response.json().get("quote", {})


def get_bars(symbol: str, timeframe: str = "1Day", limit: int = 100) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{_DATA_BASE_URL}/stocks/{symbol.upper()}/bars",
        headers=_headers(),
        params={"timeframe": timeframe, "limit": limit, "feed": "iex"},
        timeout=10,
    )
    if response.status_code != 200:
        raise ValueError(f"Alpaca API error (bars): HTTP {response.status_code} {response.text}")
    return response.json().get("bars", [])


def get_news(symbols: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": limit}
    if symbols:
        params["symbols"] = ",".join(symbol.upper() for symbol in symbols)
    response = requests.get("https://data.alpaca.markets/v1beta1/news", headers=_headers(), params=params, timeout=10)
    if response.status_code != 200:
        raise ValueError(f"Alpaca API error (news): HTTP {response.status_code} {response.text}")
    return response.json().get("news", [])
