from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders

"""The full, unattended loop for a REAL options trade, end to end -
discovery (select_option_contract against the real, unmocked selector
logic), sizing against the margin account's own option_buying_power, entry
(BUY_TO_OPEN), and fill/protection confirmation - driven through the REAL,
unmocked _run_autonomous_trade_scan_locked, mirroring
test_short_direction_full_scan_integration.py's own proven harness pattern.
Only the outer boundary (market-data discovery and the Webull broker
itself) is faked; every decision in between - that options are tried
BEFORE the equity path, that a real contract is selected via the real
options_selector module, that it sizes against the margin account's real
option_buying_power, and that it opens with a BUY_TO_OPEN against the
margin account specifically - is production code, not a stand-in."""

CREDS = {"app_key": "key", "app_secret": "secret"}
CASH_ACCOUNT_ID = "acct-cash-1"
MARGIN_ACCOUNT_ID = "acct-margin-1"
OPTION_SYMBOL = "NVDA260918C00105000"


def _registered_user(username_suffix: str) -> str:
    user = auth.register_user(f"optionfullscan-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _ai_found_call_candidate(ticker="NVDA", confidence=82):
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": confidence,
        "ideal_entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "strategy": "Breakout Continuation",
        "trade_quality": "high",
    }


def _run_full_autonomous_scan(user_id, opportunities, *, include_margin_account=True, option_contracts=None, option_snapshot=None):
    placed_option: dict = {}
    accounts = [{"account_id": CASH_ACCOUNT_ID, "account_class": "INDIVIDUAL_CASH"}]
    balances_by_account = {
        CASH_ACCOUNT_ID: {
            "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "1000000"}],
        },
    }
    if include_margin_account:
        accounts.append({"account_id": MARGIN_ACCOUNT_ID, "account_class": "INDIVIDUAL_MARGIN"})
        balances_by_account[MARGIN_ACCOUNT_ID] = {
            "total_net_liquidation_value": 1000000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "4000000", "option_buying_power": "1000000"}],
        }

    def _fake_get_account_balance(app_key, app_secret, account_id):
        return balances_by_account[account_id]

    placed_equity: dict = {}

    def _fake_place_option_order(**kwargs):
        placed_option["symbol"] = kwargs["symbol"]
        placed_option["option_type"] = kwargs["option_type"]
        placed_option["strike_price"] = kwargs["strike_price"]
        placed_option["side"] = kwargs["side"]
        placed_option["quantity"] = kwargs["quantity"]
        placed_option["account_id"] = kwargs["account_id"]
        return {"client_order_id": kwargs.get("client_order_id") or "opt-entry-cid"}

    def _fake_place_stock_order(**kwargs):
        placed_equity["quantity"] = kwargs["quantity"]
        placed_equity["side"] = kwargs["side"]
        placed_equity["account_id"] = kwargs["account_id"]
        return {"client_order_id": kwargs.get("client_order_id") or "eq-entry-cid"}

    def _fake_get_order_detail(app_key, app_secret, account_id, client_order_id):
        quantity = placed_option.get("quantity") or placed_equity.get("quantity") or 0
        return _order_detail("FILLED", quantity, quantity, average_price=5.0)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(pluto_app.webull_api, "get_account_balance", side_effect=_fake_get_account_balance), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value={
             "vix_level": None, "source_time": None, "fetch_time": None,
             "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
         }), \
         patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}), \
         patch.object(pluto_app.webull_api, "get_option_contracts", return_value=option_contracts if option_contracts is not None else []), \
         patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=option_snapshot if option_snapshot is not None else []), \
         patch.object(pluto_app.webull_api, "place_option_order", side_effect=_fake_place_option_order), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=_fake_place_stock_order) as mock_equity_place, \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_fake_get_order_detail), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result, placed_option, mock_equity_place


def _the_one_recorded_order(user_id, ticker):
    matches = [order for order in list_overnight_orders(user_id) if order.get("ticker") == ticker]
    assert len(matches) == 1, f"expected exactly one recorded order for {ticker}, got {len(matches)}"
    return matches[0]


def test_a_call_candidate_opens_a_real_option_trade_when_a_contract_is_available(user_id):
    registered_user_id = _registered_user(user_id[:8])
    candidate = _ai_found_call_candidate()
    contracts = [{
        "symbol": OPTION_SYMBOL, "strike_price": "105", "expiration_date": "2026-09-18",
        "option_type": "CALL", "underlying_symbol": "NVDA",
    }]
    snapshot = [{"symbol": OPTION_SYMBOL, "bid": "4.90", "ask": "5.10", "delta": "0.45"}]

    result, placed_option, mock_equity_place = _run_full_autonomous_scan(
        registered_user_id, [candidate], option_contracts=contracts, option_snapshot=snapshot,
    )

    assert result["placed_count"] == 1, f"expected the CALL candidate to open a real option trade, got: {result}"
    mock_equity_place.assert_not_called()  # options were viable - the equity fallback must never fire
    entry = _the_one_recorded_order(registered_user_id, "NVDA")

    assert entry["instrument_type"] == "OPTION"
    assert entry["option_symbol"] == OPTION_SYMBOL
    assert entry["option_type"] == "CALL"
    assert entry["strike"] == 105.0
    assert entry["account_id"] == MARGIN_ACCOUNT_ID
    assert entry["quantity"] > 0
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["premium_paid_per_contract"] == 5.0

    assert placed_option["side"] == "BUY"
    assert placed_option["option_type"] == "CALL"
    assert placed_option["account_id"] == MARGIN_ACCOUNT_ID


def test_falls_back_to_equity_when_no_option_contract_is_listed(user_id):
    registered_user_id = _registered_user(user_id[:8] + "b")
    candidate = _ai_found_call_candidate(ticker="AMD")

    result, placed_option, mock_equity_place = _run_full_autonomous_scan(
        registered_user_id, [candidate], option_contracts=[], option_snapshot=[],
    )

    assert result["placed_count"] == 1, f"expected the equity fallback to place a trade, got: {result}"
    mock_equity_place.assert_called_once()
    assert placed_option == {}  # the option path never placed anything
    entry = _the_one_recorded_order(registered_user_id, "AMD")
    assert entry.get("instrument_type") != "OPTION"
    assert entry["side"] == "BUY"


def test_falls_back_to_equity_when_no_margin_account_exists(user_id):
    registered_user_id = _registered_user(user_id[:8] + "c")
    candidate = _ai_found_call_candidate(ticker="MSFT")

    result, placed_option, mock_equity_place = _run_full_autonomous_scan(
        registered_user_id, [candidate], include_margin_account=False,
    )

    assert result["placed_count"] == 1
    mock_equity_place.assert_called_once()
    assert placed_option == {}
    entry = _the_one_recorded_order(registered_user_id, "MSFT")
    assert entry["account_id"] == CASH_ACCOUNT_ID


def test_falls_back_to_equity_when_the_option_snapshot_has_no_liquid_market(user_id):
    registered_user_id = _registered_user(user_id[:8] + "d")
    candidate = _ai_found_call_candidate(ticker="TSLA")
    contracts = [{
        "symbol": OPTION_SYMBOL, "strike_price": "105", "expiration_date": "2026-09-18",
        "option_type": "CALL", "underlying_symbol": "TSLA",
    }]
    # No bid at all - not a tradeable two-sided market, mirrors the real
    # deep-OTM ADBE contract found live during this feature's own
    # discovery pass.
    snapshot = [{"symbol": OPTION_SYMBOL, "bid": "0", "ask": "5.10"}]

    result, placed_option, mock_equity_place = _run_full_autonomous_scan(
        registered_user_id, [candidate], option_contracts=contracts, option_snapshot=snapshot,
    )

    assert result["placed_count"] == 1
    mock_equity_place.assert_called_once()
    assert placed_option == {}
