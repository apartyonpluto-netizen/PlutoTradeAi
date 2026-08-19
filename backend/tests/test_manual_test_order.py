from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be
    approved, which a bare fixture user_id string alone does not satisfy."""
    user = auth.register_user(f"manualtest-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _logged_in_client(user_id: str):
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client


def _quote(price: float):
    return [{"ticker": "AAPL", "price": price}], [], "2026-01-01T00:00:00+00:00"


# --- placement safety rails --------------------------------------------------------


def test_place_test_order_rejects_a_limit_price_too_close_to_market(user_id):
    registered_user_id = _registered_user(user_id[:8] + "a")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 280.0})  # only ~10% below market

    assert response.status_code == 400
    mock_place.assert_not_called()


def test_place_test_order_accepts_a_limit_price_comfortably_below_market(user_id):
    registered_user_id = _registered_user(user_id[:8] + "b")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"order_id": "abc"}) as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 200.0})  # ~35% below market

    assert response.status_code == 200
    mock_place.assert_called_once()
    call_kwargs = mock_place.call_args.kwargs
    assert call_kwargs["side"] == "BUY"
    assert call_kwargs["quantity"] == 1
    assert call_kwargs["limit_price"] == 200.0


def test_place_test_order_rejects_quantity_above_the_cap(user_id):
    registered_user_id = _registered_user(user_id[:8] + "c")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post(
            "/api/webull/place-test-order",
            json={"ticker": "AAPL", "quantity": pluto_app.MANUAL_TEST_ORDER_MAX_QUANTITY + 1, "limit_price": 1.0},
        )

    assert response.status_code == 400
    mock_place.assert_not_called()


def test_place_test_order_rejects_zero_or_negative_quantity(user_id):
    registered_user_id = _registered_user(user_id[:8] + "d")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True):
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 0, "limit_price": 200.0})
    assert response.status_code == 400


def test_place_test_order_hardcodes_buy_side_ignoring_any_client_supplied_side(user_id):
    """side is never read from the request payload at all - confirms the
    endpoint can't be used to place a SELL (short-opening) order no
    matter what a caller sends."""
    registered_user_id = _registered_user(user_id[:8] + "e")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"order_id": "abc"}) as mock_place:
        client = _logged_in_client(registered_user_id)
        client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 200.0, "side": "SELL"})
    assert mock_place.call_args.kwargs["side"] == "BUY"


def test_place_test_order_never_places_a_stop_or_target(user_id):
    registered_user_id = _registered_user(user_id[:8] + "f")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"order_id": "abc"}), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target:
        client = _logged_in_client(registered_user_id)
        client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 200.0})
    mock_stop.assert_not_called()
    mock_target.assert_not_called()


def test_place_test_order_rejects_when_no_market_price_is_available(user_id):
    registered_user_id = _registered_user(user_id[:8] + "g")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=([], ["no data"], "")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "ZZZZQQQ", "quantity": 1, "limit_price": 1.0})
    assert response.status_code == 400
    mock_place.assert_not_called()


def test_place_test_order_records_a_durable_entry_with_the_manual_source_marker(user_id):
    registered_user_id = _registered_user(user_id[:8] + "h")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"order_id": "abc"}):
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 200.0})

    assert response.status_code == 200
    orders = list_overnight_orders(registered_user_id)
    assert len(orders) == 1
    assert orders[0]["source"] == "manual_test_order"
    assert orders[0]["status"] == "placed"
    assert orders[0]["entry_client_order_id"].startswith("manualtest")


def test_place_test_order_failure_is_recorded_and_surfaced_cleanly(user_id):
    registered_user_id = _registered_user(user_id[:8] + "i")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "scan_market", return_value=_quote(310.0)), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=RuntimeError("broker rejected")):
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/place-test-order", json={"ticker": "AAPL", "quantity": 1, "limit_price": 200.0})

    assert response.status_code == 400
    orders = list_overnight_orders(registered_user_id)
    assert orders[0]["status"] == "failed"
    assert orders[0]["source"] == "manual_test_order"


# --- cancellation: ownership/type guard + zero-fill confirmation -------------------


def test_cancel_test_order_refuses_an_order_not_created_by_this_tool(user_id):
    """A regular autonomous entry (no source="manual_test_order" marker)
    must not be cancellable through this endpoint - it is deliberately
    NOT a general-purpose cancel capability."""
    registered_user_id = _registered_user(user_id[:8] + "j")
    record_overnight_order(registered_user_id, {
        "ticker": "MSFT", "entry_client_order_id": "pt-real-entry-123", "account_id": "acct-1", "status": "placed",
    })
    with patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/cancel-test-order", json={"client_order_id": "pt-real-entry-123"})
    assert response.status_code == 400
    mock_cancel.assert_not_called()


def test_cancel_test_order_refuses_an_unknown_client_order_id(user_id):
    registered_user_id = _registered_user(user_id[:8] + "k")
    client = _logged_in_client(registered_user_id)
    response = client.post("/api/webull/cancel-test-order", json={"client_order_id": "nonexistent"})
    assert response.status_code == 400


def test_cancel_test_order_confirms_zero_fill_and_updates_the_record(user_id):
    registered_user_id = _registered_user(user_id[:8] + "l")
    record_overnight_order(registered_user_id, {
        "ticker": "AAPL", "entry_client_order_id": "manualtestabc123", "account_id": "acct-1",
        "status": "placed", "source": "manual_test_order",
    })
    order_detail = {"orders": [{"status": "CANCELLED", "total_quantity": "1", "filled_quantity": "0"}]}
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value={"ok": True}) as mock_cancel, \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=order_detail):
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/cancel-test-order", json={"client_order_id": "manualtestabc123"})

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["zero_fill_confirmed"] is True
    assert payload["filled_quantity"] == 0
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], "acct-1", "manualtestabc123")

    orders = list_overnight_orders(registered_user_id)
    updated = next(o for o in orders if o["entry_client_order_id"] == "manualtestabc123")
    assert updated["status"] == "cancelled"
    assert updated["cancel_confirmed_filled_quantity"] == 0


def test_cancel_test_order_honestly_reports_a_nonzero_fill_if_it_ever_happened():
    """Even though the placement-side price guard should make this
    impossible in practice, the cancellation-confirmation logic itself
    must report the TRUE broker state, never assume zero fill just
    because a cancel call succeeded - a cancel can legitimately race a
    fill."""
    import uuid
    registered_user_id_seed = uuid.uuid4().hex[:8]
    user = auth.register_user(f"manualtest-{registered_user_id_seed}m", "TestPassword123!")
    auth.approve_user(user["id"])
    registered_user_id = user["id"]

    record_overnight_order(registered_user_id, {
        "ticker": "AAPL", "entry_client_order_id": "manualtestrace1", "account_id": "acct-1",
        "status": "placed", "source": "manual_test_order",
    })
    order_detail = {"orders": [{"status": "PARTIALLY_FILLED", "total_quantity": "1", "filled_quantity": "1"}]}
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value={"ok": True}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=order_detail):
        client = _logged_in_client(registered_user_id)
        response = client.post("/api/webull/cancel-test-order", json={"client_order_id": "manualtestrace1"})

    payload = response.get_json()["data"]
    assert payload["zero_fill_confirmed"] is False
    assert payload["filled_quantity"] == 1
