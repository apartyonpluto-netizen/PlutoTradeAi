from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders

"""The full, unattended loop for a PUT/short candidate, end to end -
discovery, sizing against the MARGIN account's own buying power, entry
(SELL to open), fill, and protection (a BUY-side stop) - driven through
the REAL, unmocked _run_autonomous_trade_scan_locked, mirroring
test_full_autonomous_trade_lifecycle.py's own proven harness pattern but
with both a cash and a margin account present, and a PUT candidate
instead of CALL. Only the outer boundary (market-data discovery and the
Webull broker itself) is faked; every decision in between - that a PUT
candidate is now eligible to qualify, that it sizes against the margin
account's own numbers rather than the cash account's, that it opens
with a SELL against the margin account specifically, and that its
protective leg is a BUY-side stop - is production code, not a stand-in."""

CREDS = {"app_key": "key", "app_secret": "secret"}
CASH_ACCOUNT_ID = "acct-cash-1"
MARGIN_ACCOUNT_ID = "acct-margin-1"


def _registered_user(username_suffix: str) -> str:
    user = auth.register_user(f"shortfullscan-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _ai_found_put_candidate(ticker="NVDA", confidence=82):
    """A short setup the AI scan itself decided is worth trading - stop
    ABOVE entry, target BELOW entry, matching how _build_page_context's
    own upcoming_opportunities builder computes these for a PUT bias
    (confirmed already direction-aware before this feature was built)."""
    return {
        "ticker": ticker,
        "recommendation": "PUT",
        "confidence": confidence,
        "ideal_entry": 100.0,
        "stop": 110.0,
        "target": 90.0,
        "strategy": "Breakdown Continuation",
        "trade_quality": "high",
    }


def _run_full_autonomous_scan_with_margin_account(user_id, opportunities):
    placed_quantity: dict = {}
    protective_ids: dict = {}
    accounts = [
        {"account_id": CASH_ACCOUNT_ID, "account_class": "INDIVIDUAL_CASH"},
        {"account_id": MARGIN_ACCOUNT_ID, "account_class": "INDIVIDUAL_MARGIN"},
    ]
    balances_by_account = {
        CASH_ACCOUNT_ID: {
            "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "1000000"}],
        },
        MARGIN_ACCOUNT_ID: {
            "total_net_liquidation_value": 1000000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "4000000"}],
        },
    }

    def _fake_get_account_balance(app_key, app_secret, account_id):
        return balances_by_account[account_id]

    def _fake_place_stock_order(**kwargs):
        placed_quantity["value"] = kwargs["quantity"]
        placed_quantity["side"] = kwargs["side"]
        placed_quantity["account_id"] = kwargs["account_id"]
        return {"client_order_id": "entry-cid"}

    def _fake_place_stop_loss_order(**kwargs):
        protective_ids["stop"] = kwargs["client_order_id"]
        protective_ids["stop_side"] = kwargs.get("side")
        protective_ids["stop_account_id"] = kwargs["account_id"]
        return {"client_order_id": kwargs["client_order_id"]}

    def _fake_get_order_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == protective_ids.get("stop"):
            return _order_detail("SUBMITTED", 0, 0)
        quantity = placed_quantity.get("value", 0)
        return _order_detail("FILLED", quantity, quantity, average_price=100.0)

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
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=_fake_place_stock_order), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_fake_get_order_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=_fake_place_stop_loss_order), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result, protective_ids


def _the_one_recorded_order(user_id, ticker):
    matches = [order for order in list_overnight_orders(user_id) if order.get("ticker") == ticker]
    assert len(matches) == 1, f"expected exactly one recorded order for {ticker}, got {len(matches)}"
    return matches[0]


def test_a_put_candidate_opens_short_against_the_margin_account_and_gets_protected(user_id):
    registered_user_id = _registered_user(user_id[:8])
    candidate = _ai_found_put_candidate()
    result, protective_ids = _run_full_autonomous_scan_with_margin_account(registered_user_id, [candidate])

    assert result["placed_count"] == 1, f"expected the PUT candidate to be placed, got: {result}"
    entry = _the_one_recorded_order(registered_user_id, "NVDA")

    assert entry["direction"] == "short"
    assert entry["side"] == "SELL"
    assert entry["account_id"] == MARGIN_ACCOUNT_ID
    assert entry["quantity"] > 0

    # The real _submit_and_protect_entry ran against the MARGIN account,
    # not the cash one, and the protective leg is a BUY-side stop.
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert protective_ids["stop_side"] == "BUY"
    assert protective_ids["stop_account_id"] == MARGIN_ACCOUNT_ID


def test_a_put_candidate_is_skipped_cleanly_when_no_margin_account_exists(user_id):
    registered_user_id = _registered_user(user_id[:8] + "b")
    candidate = _ai_found_put_candidate(ticker="AMD")

    # Same harness, but only the cash account this time - no margin.
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": CASH_ACCOUNT_ID, "account_class": "INDIVIDUAL_CASH"}]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={
             "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
             "account_currency_assets": [{"buying_power": "1000000"}],
         }), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [candidate]}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value={
             "vix_level": None, "source_time": None, "fetch_time": None,
             "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
         }), \
         patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place, \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(registered_user_id)

    mock_place.assert_not_called()
    assert result["placed_count"] == 0
    skipped_reasons = [s.get("reason_skipped") for s in result.get("skipped", [])]
    assert any("no margin account available" in str(r) for r in skipped_reasons)
