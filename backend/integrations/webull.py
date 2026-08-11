from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

# Paper-trading OpenAPI apps authenticate against a separate sandbox host from
# the live-trading API - api.webull.com rejects sandbox credentials with a
# generic 401 "invalid credentials" error that gives no hint the real problem
# is the endpoint, not the key/secret.
_REGION_ID = "us"
_SANDBOX_ENDPOINT = "api.sandbox.webull.com"

# The webull SDK's TradeClient logs every request's headers at INFO level,
# including x-signature (an HMAC derived from the App Secret) and x-app-key
# - both landed in plaintext in webull_trade_sdk.log. It also re-attaches a
# fresh file/stream handler to the shared "webull.core" logger on every
# single API call (one new ApiClient per call, and its default-handler check
# only looks at that fresh instance), so the file was growing unbounded with
# duplicate output too. _redact_webull_sdk_logging replaces that with one
# handler, configured once, that strips sensitive header values before they
# ever reach disk.
_SENSITIVE_LOG_FIELD = re.compile(
    r'("(?:[a-z0-9_-]*(?:app.?key|signature|secret|token|authorization|password)[a-z0-9_-]*)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)
_webull_sdk_logging_configured = False


class _RedactSensitiveWebullFields(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a logging filter break the request it's logging
            return True
        redacted = _SENSITIVE_LOG_FIELD.sub(r"\1***REDACTED***\2", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _redact_webull_sdk_logging() -> None:
    global _webull_sdk_logging_configured
    if _webull_sdk_logging_configured:
        return
    sdk_logger = logging.getLogger("webull.core")
    sdk_logger.setLevel(logging.INFO)
    handler = logging.FileHandler("webull_trade_sdk.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(thread)d %(asctime)s %(name)s %(levelname)s %(message)s"))
    # The filter goes on the HANDLER, not the logger. The SDK actually logs
    # through child loggers (e.g. "webull.core.http.initializer...") that
    # propagate up to this handler without re-running "webull.core"'s own
    # logger-level filters - only handler-level filters see every record
    # regardless of which descendant logger it originated from.
    handler.addFilter(_RedactSensitiveWebullFields())
    sdk_logger.addHandler(handler)
    _webull_sdk_logging_configured = True


def is_configured(app_key: str, app_secret: str) -> bool:
    return bool((app_key or "").strip() and (app_secret or "").strip())


def _get_trade_client(app_key: str, app_secret: str):
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient

    app_key = (app_key or "").strip()
    app_secret = (app_secret or "").strip()
    if not app_key or not app_secret:
        raise ValueError("Webull API credentials are not configured for this account.")

    _redact_webull_sdk_logging()
    api_client = ApiClient(app_key, app_secret, _REGION_ID)
    # Marks this instance as already having its logging configured, so
    # TradeClient._init_logger skips installing its own (duplicate,
    # unredacted) handler - _redact_webull_sdk_logging above is the only
    # handler that ever gets attached, once per process.
    api_client._stream_logger_set = True
    api_client._file_logger_set = True
    api_client.add_endpoint(_REGION_ID, _SANDBOX_ENDPOINT)
    return TradeClient(api_client)


def get_paper_accounts(app_key: str, app_secret: str) -> List[Dict[str, Any]]:
    trade_client = _get_trade_client(app_key, app_secret)
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


def get_account_balance(app_key: str, app_secret: str, account_id: str) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = trade_client.account_v2.get_account_balance(account_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (balance): HTTP {response.status_code}")
    return response.json()


def get_account_positions(app_key: str, app_secret: str, account_id: str) -> List[Dict[str, Any]]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = trade_client.account_v2.get_account_position(account_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (positions): HTTP {response.status_code}")
    positions = response.json()
    return positions if isinstance(positions, list) else []


def preview_stock_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    side: str,
    quantity: float,
    limit_price: float,
    trading_session: str = "CORE",
) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    order = {
        "combo_type": "NORMAL",
        "client_order_id": uuid.uuid4().hex,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(limit_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    response = trade_client.order_v2.preview_order(account_id, [order])
    if response.status_code != 200:
        raise ValueError(f"Webull API error (preview order): HTTP {response.status_code} {response.text}")
    return response.json()


def place_stock_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    side: str,
    quantity: float,
    limit_price: float,
    trading_session: str = "CORE",
) -> Dict[str, Any]:
    """Places a real (sandbox) DAY limit order. trading_session must be CORE
    (regular hours), ALL (extended hours), or NIGHT (Webull's 24-hour session -
    the only one accepted outside pre-market/after-hours windows, and it draws
    from a separate night_trading_buying_power pool rather than the account's
    regular buying power)."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(limit_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    # Batching several order placements back-to-back in one request (e.g. the
    # overnight scan) can trip Webull's own rate limiter (429) well before any
    # documented per-minute figure - retry with backoff rather than dropping
    # the order.
    last_response = None
    for attempt in range(3):
        response = trade_client.order_v2.place_order(account_id, [order])
        if response.status_code == 200:
            result = response.json()
            result["client_order_id"] = client_order_id
            return result
        last_response = response
        if response.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    raise ValueError(f"Webull API error (place order): HTTP {last_response.status_code} {last_response.text}")


def place_stop_loss_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    quantity: float,
    stop_price: float,
) -> Dict[str, Any]:
    """Places a real standalone stop-loss SELL order at the broker - once this
    is accepted, Webull itself watches the price and executes the exit, with
    no dependency on this app polling or a cron job firing in time. order_type
    must be the literal string "STOP_LOSS" (underscore, not a space - "STOP
    LOSS" is rejected with OAUTH_OPENAPI_PARAM_ERR, verified via preview_order
    before this was wired into live placement). support_trading_session is
    hardcoded to "CORE" - unlike LIMIT orders, STOP_LOSS rejects both "NIGHT"
    and "ALL" as invalid parameter values (confirmed live against the sandbox
    API), so stop-loss orders can only be placed while the core trading
    gateway is up (roughly 9:30-16:00 ET). Callers placing an entry outside
    those hours should expect this to fail and retry once CORE hours begin -
    see _place_missing_stop_orders in app.py."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "STOP_LOSS",
        "stop_price": str(stop_price),
        "quantity": str(quantity),
        "support_trading_session": "CORE",
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    last_response = None
    for attempt in range(3):
        response = trade_client.order_v2.place_order(account_id, [order])
        if response.status_code == 200:
            result = response.json()
            result["client_order_id"] = client_order_id
            return result
        last_response = response
        if response.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    raise ValueError(f"Webull API error (place stop-loss order): HTTP {last_response.status_code} {last_response.text}")


def place_take_profit_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    quantity: float,
    target_price: float,
    trading_session: str = "CORE",
) -> Dict[str, Any]:
    """Places a real take-profit SELL order at the broker - a plain LIMIT
    order resting above the current price, which Webull fills automatically
    once the market reaches it, same as any other limit order. Unlike
    STOP_LOSS, LIMIT accepts CORE/ALL/NIGHT normally (confirmed earlier
    against the sandbox API), so this has no trading-hours restriction. This
    order rides alongside the STOP_LOSS order placed at entry as an
    independent bracket rather than a true OTOCO combo - see the
    _reconcile_exit_orders note in app.py for how the stale-leg risk that
    creates is handled."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(target_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    last_response = None
    for attempt in range(3):
        response = trade_client.order_v2.place_order(account_id, [order])
        if response.status_code == 200:
            result = response.json()
            result["client_order_id"] = client_order_id
            return result
        last_response = response
        if response.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    raise ValueError(f"Webull API error (place take-profit order): HTTP {last_response.status_code} {last_response.text}")


def cancel_order(app_key: str, app_secret: str, account_id: str, client_order_id: str) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = trade_client.order_v2.cancel_order(account_id, client_order_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (cancel order): HTTP {response.status_code} {response.text}")
    return response.json()
