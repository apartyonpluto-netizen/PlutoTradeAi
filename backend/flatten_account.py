"""Close everything: cancel open orders, then flatten positions.

This is the code path behind the confirmed Stop button. Nothing in here
runs unless a caller passes the exact confirmation phrase and then calls
run. Tests drive it with a fake broker. Do not call run against a live
account from a script or a test.

Equity closes are marketable DAY limits 1% through the last price, the same
cushion manual closes already use. An option is closed only when the
position row itself carries the contract fields the order API requires.
Anything else is reported as not closed, never guessed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

CONFIRM_PHRASE = "CLOSE EVERYTHING"
CLOSE_THROUGH = 0.01


def confirmed(phrase: str) -> bool:
    return str(phrase or "") == CONFIRM_PHRASE


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


def _instrument(row: Dict[str, Any]) -> str:
    return "OPTION" if str(row.get("instrument_type") or "").upper() == "OPTION" else "EQUITY"


def plan(positions: List[Dict[str, Any]], open_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What to cancel and what to close. No broker calls."""
    cancels: List[Dict[str, Any]] = []
    closes: List[Dict[str, Any]] = []
    errors: List[str] = []

    for order in open_orders or []:
        client_order_id = str(order.get("client_order_id") or "")
        if not client_order_id:
            errors.append("open order has no client_order_id and was not cancelled")
            continue
        cancels.append({
            "client_order_id": client_order_id,
            "symbol": str(order.get("symbol") or order.get("ticker") or ""),
            "instrument": _instrument(order),
            "account_id": str(order.get("account_id") or ""),
        })

    for position in positions or []:
        quantity = _float(position.get("quantity"))
        if quantity == 0:
            continue
        symbol = str(position.get("symbol") or position.get("ticker") or "")
        last = _float(position.get("last_price") or position.get("last") or position.get("market_price"))
        if last <= 0:
            errors.append(f"{symbol or 'position'}: no last price, not closed")
            continue
        short = quantity < 0 or str(position.get("side") or "").upper() in {"SHORT", "SELL"}
        side = "BUY" if short else "SELL"
        limit = round(last * (1 + CLOSE_THROUGH) if side == "BUY" else last * (1 - CLOSE_THROUGH), 2)
        if limit <= 0:
            errors.append(f"{symbol}: closing limit rounded to nothing, not closed")
            continue
        row: Dict[str, Any] = {
            "symbol": symbol,
            "quantity": abs(quantity),
            "side": side,
            "limit_price": limit,
            "instrument": _instrument(position),
            "account_id": str(position.get("account_id") or ""),
        }
        if row["instrument"] == "OPTION":
            contract = _option_contract(position)
            if contract is None:
                errors.append(f"{symbol}: option is missing strike, type, or expiration, so it was not closed")
                continue
            row.update(contract)
        closes.append(row)

    return {"cancels": cancels, "closes": closes, "errors": errors}


def _option_contract(position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    underlying = position.get("underlying_symbol") or position.get("underlying") or position.get("symbol")
    option_type = str(position.get("option_type") or position.get("call_put") or "").upper()
    strike = _float(position.get("strike_price") or position.get("strike"))
    expiration = str(position.get("expiration_date") or position.get("option_expire_date") or position.get("expiration") or "")
    if not underlying or option_type not in {"CALL", "PUT"} or strike <= 0 or not expiration:
        return None
    return {
        "underlying": str(underlying).upper(),
        "option_type": option_type,
        "strike_price": strike,
        "expiration_date": expiration,
    }


def execute(plan_result: Dict[str, Any], hooks: Dict[str, Callable[..., Any]]) -> Dict[str, Any]:
    """Runs a plan through caller-supplied hooks. One failure is recorded
    and the rest still run. Hooks are cancel_equity, cancel_option,
    close_equity, close_option."""
    cancelled: List[str] = []
    closed: List[str] = []
    errors = list(plan_result.get("errors") or [])
    for item in plan_result.get("cancels") or []:
        hook = hooks.get("cancel_option" if item.get("instrument") == "OPTION" else "cancel_equity")
        try:
            if hook is None:
                raise RuntimeError("no cancel hook")
            hook(item)
            cancelled.append(item["client_order_id"])
        except Exception as error:  # noqa: BLE001 - collect and keep going
            errors.append(f"cancel {item.get('client_order_id')}: {error}")
    for item in plan_result.get("closes") or []:
        hook = hooks.get("close_option" if item.get("instrument") == "OPTION" else "close_equity")
        try:
            if hook is None:
                raise RuntimeError("no close hook")
            hook(item)
            closed.append(str(item.get("symbol") or ""))
        except Exception as error:  # noqa: BLE001
            errors.append(f"close {item.get('symbol')}: {error}")
    return {
        "cancelled": cancelled,
        "closed": closed,
        "errors": errors,
        "planned_cancels": len(plan_result.get("cancels") or []),
        "planned_closes": len(plan_result.get("closes") or []),
    }


def run_for_user(user_id: str) -> Dict[str, Any]:
    """Broker path. Caller must already have checked the confirmation phrase
    and turned on the emergency stop. Refuses to run with empty credentials.
    """
    from integrations import webull as webull_api
    from webull_credentials import get_webull_credentials, is_webull_configured

    if not is_webull_configured(user_id):
        return {"ok": False, "error": "Webull is not connected for this account.", "cancelled": [], "closed": [], "errors": []}
    creds = get_webull_credentials(user_id)
    accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    account_ids = [str(account.get("account_id") or "") for account in accounts if account.get("account_id")]
    if not account_ids:
        return {"ok": False, "error": "No Webull account id was returned.", "cancelled": [], "closed": [], "errors": []}

    def _tagged(rows: List[Dict[str, Any]], account_id: str) -> List[Dict[str, Any]]:
        tagged = []
        for row in rows:
            if isinstance(row, dict):
                tagged.append({**row, "account_id": row.get("account_id") or account_id})
        return tagged

    open_orders: List[Dict[str, Any]] = []
    positions: List[Dict[str, Any]] = []
    for account_id in account_ids:
        open_orders.extend(_tagged(webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id), account_id))
        positions.extend(_tagged(webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id), account_id))

    cancel_plan = plan([], open_orders)

    def _cancel(item: Dict[str, Any], option: bool) -> None:
        if option:
            webull_api.cancel_option_order(creds["app_key"], creds["app_secret"], item["account_id"], item["client_order_id"])
        else:
            webull_api.cancel_order(creds["app_key"], creds["app_secret"], item["account_id"], item["client_order_id"])

    cancel_result = execute(cancel_plan, {
        "cancel_equity": lambda item: _cancel(item, False),
        "cancel_option": lambda item: _cancel(item, True),
        "close_equity": lambda item: None,
        "close_option": lambda item: None,
    })

    fresh_positions: List[Dict[str, Any]] = []
    for account_id in account_ids:
        fresh_positions.extend(_tagged(webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id), account_id))
    close_plan = plan(fresh_positions, [])

    def _close_equity(item: Dict[str, Any]) -> None:
        webull_api.place_stock_order(
            creds["app_key"], creds["app_secret"], item["account_id"], item["symbol"], item["side"],
            item["quantity"], item["limit_price"], trading_session="CORE", opens_position=False,
        )

    def _close_option(item: Dict[str, Any]) -> None:
        webull_api.place_option_order(
            creds["app_key"], creds["app_secret"], item["account_id"], item["underlying"], item["option_type"],
            item["strike_price"], item["expiration_date"], item["side"], item["quantity"], item["limit_price"],
            opens_position=False,
        )

    close_result = execute(close_plan, {
        "cancel_equity": lambda item: None,
        "cancel_option": lambda item: None,
        "close_equity": _close_equity,
        "close_option": _close_option,
    })
    errors = cancel_result["errors"] + close_result["errors"]
    return {
        "ok": not errors,
        "cancelled": cancel_result["cancelled"],
        "closed": close_result["closed"],
        "errors": errors,
    }
