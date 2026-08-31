from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders
from scan_lock import ScanAlreadyRunningError

"""Stage 3 of the staged sandbox validation plan (see conversation history):
ONE real, genuinely fillable share, with REAL stop-loss/take-profit
protection - proving the full entry -> fill -> protection pipeline works
against a real broker fill, via the SAME _submit_and_protect_entry path the
autonomous scan itself uses. These tests drive the real, unmocked function -
only the outer broker boundary is faked - mirroring
test_full_autonomous_trade_lifecycle.py's own approach, not a simplified
reimplementation that could silently diverge from it."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _registered_user(username_suffix: str) -> str:
    user = auth.register_user(f"stage3-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _logged_in_client(user_id: str):
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client


def _quote(price: float):
    return [{"ticker": "AAPL", "price": price}], [], "2026-01-01T00:00:00+00:00"


def _entry_order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _exit_order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


# --- input validation, before anything touches the broker ---------------------------


def test_rejects_missing_stop_or_target_price(user_id):
    registered_user_id = _registered_user(user_id[:8] + "a")
    with patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-stage3-order", json={"ticker": "AAPL", "target_price": 105.0})
    assert response.status_code == 400
    mock_place.assert_not_called()


def test_rejects_non_positive_stop_or_target_price(user_id):
    registered_user_id = _registered_user(user_id[:8] + "b")
    with patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 0, "target_price": 105.0}
        )
    assert response.status_code == 400
    mock_place.assert_not_called()


def test_rejects_when_not_webull_configured(user_id):
    registered_user_id = _registered_user(user_id[:8] + "c")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "is_webull_configured", return_value=False), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 105.0}
        )
    assert response.status_code == 400
    mock_place.assert_not_called()


def test_rejects_outside_core_trading_hours(user_id):
    """place_stop_loss_order only accepts CORE - see _new_entries_allowed's
    own docstring for why the autonomous scan enforces this, and Stage 3
    must too since it exists specifically to prove real protection lands."""
    registered_user_id = _registered_user(user_id[:8] + "d")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 105.0}
        )
    assert response.status_code == 400
    assert "CORE" in response.get_json()["error"]["message"]
    mock_place.assert_not_called()


def test_rejects_bad_stop_target_ordering_against_the_computed_entry_price(user_id):
    """market price 100 -> computed entry ~100.50. A stop ABOVE the entry
    price must be rejected regardless of what the target is."""
    registered_user_id = _registered_user(user_id[:8] + "e")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(100.0)), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 101.0, "target_price": 110.0}
        )
    assert response.status_code == 400
    mock_place.assert_not_called()


def test_rejects_when_no_market_quote_is_available(user_id):
    registered_user_id = _registered_user(user_id[:8] + "f")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=([], ["no data"], "")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 105.0}
        )
    assert response.status_code == 400
    mock_place.assert_not_called()


# --- entry price computation ---------------------------------------------------------


def test_entry_limit_price_is_computed_above_market_not_trusted_from_the_client(user_id):
    """The opposite of Stage 2: a marketable price, not one guaranteed to
    never fill - and the client cannot influence it at all (no limit_price
    field is even accepted in the payload)."""
    registered_user_id = _registered_user(user_id[:8] + "g")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(100.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-cid"}) as mock_place, \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_entry_order_detail("SUBMITTED", 1, 0)), \
         patch.object(pluto_app, "time"):
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order",
            json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 110.0, "limit_price": 1.0},
        )

    assert response.status_code == 200
    call_kwargs = mock_place.call_args.kwargs
    assert call_kwargs["side"] == "BUY"
    assert call_kwargs["quantity"] == pluto_app.STAGE3_ENTRY_QUANTITY == 1
    expected_limit_price = round(100.0 * (1 + pluto_app.STAGE3_MARKETABLE_PREMIUM_ABOVE_MARKET), 2)
    assert call_kwargs["limit_price"] == expected_limit_price
    # A client-supplied limit_price must never override the computed one.
    assert call_kwargs["limit_price"] != 1.0


# --- full real fill + protection chain, end to end ------------------------------------


def _place_stage3_order(user_id, ticker="AAPL", stop_price=95.0, target_price=110.0, market_price=100.0):
    placed_quantity: dict = {}
    protective_ids: dict = {}

    def _fake_place_stock_order(**kwargs):
        placed_quantity["value"] = kwargs["quantity"]
        return {"client_order_id": "entry-cid"}

    def _fake_place_stop_loss_order(**kwargs):
        protective_ids["stop"] = kwargs["client_order_id"]
        return {"client_order_id": kwargs["client_order_id"]}

    def _fake_place_take_profit_order(**kwargs):
        protective_ids["target"] = kwargs["client_order_id"]
        return {"client_order_id": kwargs["client_order_id"]}

    def _fake_get_order_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id in (protective_ids.get("stop"), protective_ids.get("target")):
            return _exit_order_detail("SUBMITTED", 0, 0)
        quantity = placed_quantity.get("value", 0)
        return _entry_order_detail("FILLED", quantity, quantity, average_price=market_price)

    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(market_price)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=_fake_place_stock_order), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_fake_get_order_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=_fake_place_stop_loss_order), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=_fake_place_take_profit_order) as mock_target, \
         patch.object(pluto_app, "time"):
        client = _logged_in_client(user_id)
        response = client.post(
            "/api/webull/place-stage3-order",
            json={"ticker": ticker, "stop_price": stop_price, "target_price": target_price},
        )
    mock_target.assert_not_called()  # app-monitored, never a broker order - see _reconcile_protective_leg_quantity
    return response


def test_stage3_order_fills_and_gets_real_protection_end_to_end(user_id):
    registered_user_id = _registered_user(user_id[:8] + "h")
    response = _place_stage3_order(registered_user_id)

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["quantity"] == 1
    assert body["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert body["stop_client_order_id"]
    # The target is never placed as a broker order (app-monitored since
    # 2026-08-31 - see _reconcile_protective_leg_quantity's own comment).
    assert not body.get("target_client_order_id")
    assert body["display_status"] == "Filled & protected"

    matches = [order for order in list_overnight_orders(registered_user_id) if order.get("ticker") == "AAPL"]
    assert len(matches) == 1
    recorded = matches[0]
    assert recorded["source"] == "stage3_test_order"
    assert recorded["status"] == "placed"
    assert recorded["quantity"] == 1


def test_stage3_order_is_never_touchable_by_the_stage2_cancel_endpoint(user_id):
    """api_webull_cancel_test_order (Stage 2) only ever touches
    source == "manual_test_order" - a Stage 3 order's distinct source must
    keep it structurally out of reach of that endpoint."""
    registered_user_id = _registered_user(user_id[:8] + "i")
    response = _place_stage3_order(registered_user_id)
    entry_client_order_id = response.get_json()["entry_client_order_id"]

    client = _logged_in_client(registered_user_id)
    cancel_response = client.post("/api/webull/cancel-test-order", json={"client_order_id": entry_client_order_id})
    assert cancel_response.status_code == 400
    assert "No manual test order found" in cancel_response.get_json()["error"]["message"]


def test_stage3_order_is_closeable_via_the_existing_generic_close_position_endpoint(user_id):
    """Stage 3 deliberately does not build its own exit endpoint - it
    reuses api_close_webull_position, which looks up the real broker
    position by ticker regardless of source."""
    registered_user_id = _registered_user(user_id[:8] + "j")
    _place_stage3_order(registered_user_id)

    with patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 1, "last_price": 101.0}]), \
         patch.object(pluto_app, "pop_exit_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"order_id": "sell-1"}) as mock_sell:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/trade-journal/close-position", json={"ticker": "AAPL"})

    assert response.status_code == 200
    mock_sell.assert_called_once()
    assert mock_sell.call_args.kwargs["side"] == "SELL"


def test_stage3_order_failure_is_recorded_and_surfaced_as_an_error(user_id):
    registered_user_id = _registered_user(user_id[:8] + "k")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(100.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=pluto_app.webull_api.DefiniteOrderRejection("rejected")):
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 110.0}
        )

    assert response.status_code == 400
    matches = [order for order in list_overnight_orders(registered_user_id) if order.get("ticker") == "AAPL"]
    assert len(matches) == 1
    assert matches[0]["status"] == "failed"
    assert matches[0]["source"] == "stage3_test_order"


def test_stage3_order_refuses_to_race_a_concurrent_scan_for_the_same_user(user_id):
    """Held under the same per-user scan_lock as the autonomous scan -
    ScanAlreadyRunningError must surface as a clean 409, not a silent
    double-submission."""
    registered_user_id = _registered_user(user_id[:8] + "l")
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(100.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "user_scan_lock", side_effect=ScanAlreadyRunningError("already running")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-stage3-order", json={"ticker": "AAPL", "stop_price": 95.0, "target_price": 110.0}
        )

    assert response.status_code == 409
    mock_place.assert_not_called()


# --- account-hub display -------------------------------------------------------------


def test_stage3_order_appears_in_account_hub_with_closeable_flag(user_id):
    registered_user_id = _registered_user(user_id[:8] + "m")
    _place_stage3_order(registered_user_id)

    with patch.object(pluto_app, "get_accounts", return_value=[]), \
         patch.object(pluto_app, "is_anthropic_configured", return_value=False), \
         patch.object(pluto_app, "_get_live_webull_balance", return_value={}), \
         patch.object(pluto_app, "scan_market", return_value=([], [], "")):
        client = _logged_in_client(registered_user_id)
        response = client.get("/account-hub")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "AAPL" in body
    assert "Stage 3" in body
    assert "close-webull-position" in body
