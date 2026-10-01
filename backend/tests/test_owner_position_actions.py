"""Owner Protect / Close for positions held at the broker with no stop.
Broker calls are mocked; the live switch is never armed here."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "k", "app_secret": "s"}
POSITION = [{"symbol": "INTC", "quantity": "2", "cost_price": "117.23", "last_price": "115.93"}]


def _orphan(user_id):
    entry = {"record_id": "r-intc", "ticker": "INTC", "account_id": "cash-1", "quantity": 2, "filled_quantity": 2, "limit_price": 117.23,
             "status": "placed", "side": "BUY", "orphan_recovered": True, "entry_order_terminal": True, "stop": 0, "target": 0,
             "trading_day": "2026-09-21", "environment": "sandbox"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="cid-intc")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=2)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED)
    record_overnight_order(user_id, entry)


@pytest.fixture
def client(user_id):
    user = auth.register_user(f"own-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    _orphan(user["id"])
    c = pluto_app.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user["id"]
    c.uid = user["id"]
    return c


def _broker():
    return [patch.object(pluto_app, "get_webull_credentials", return_value=CREDS),
            patch.object(pluto_app.webull_api, "get_account_positions", return_value=POSITION)]


def _with(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def test_protect_saves_the_owners_stop_and_the_monitor_then_protects_it(client):
    response = _with(_broker(), lambda: client.post("/api/broker/positions/r-intc/protect",
                                                     json={"stop_price": 110, "confirm_symbol": "intc"}))
    assert response.status_code == 200, response.get_json()
    record = list_overnight_orders(client.uid)[0]
    assert record["stop"] == 110.0 and record["owner_protection_set_by"] == client.uid
    # The monitor no longer skips it: it goes through the normal protection path with the owner's stop.
    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as protect, \
         patch.object(pluto_app, "_check_position_absent_while_stuck", return_value=False), \
         patch.object(pluto_app, "_alert_unprotected_orphan_needs_action") as skipped_alert:
        pluto_app._monitor_transitional_orders(client.uid, CREDS, "cash-1")
    skipped_alert.assert_not_called()
    assert protect.call_args.kwargs["stop_price"] == 110.0


@pytest.mark.parametrize("body,needle", [
    ({"stop_price": 120, "confirm_symbol": "INTC"}, "trigger immediately"),
    ({"stop_price": 110, "confirm_symbol": "MSFT"}, "Type INTC"),
    ({"stop_price": 0, "confirm_symbol": "INTC"}, "Enter a stop"),
])
def test_unsafe_or_unconfirmed_requests_are_refused(client, body, needle):
    response = _with(_broker(), lambda: client.post("/api/broker/positions/r-intc/protect", json=body))
    assert response.status_code == 400 and needle in response.get_json()["error"]["message"]
    assert list_overnight_orders(client.uid)[0]["stop"] == 0


def test_close_sends_a_marketable_limit_for_the_brokers_quantity(client):
    patches = _broker() + [patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"),
                           patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "close-1"})]
    place = patches[-1]
    response = _with(patches, lambda: (client.post("/api/broker/positions/r-intc/close", json={"confirm_symbol": "INTC"}),
                                       pluto_app.webull_api.place_stock_order.call_args))
    resp, call = response
    assert resp.status_code == 200, resp.get_json()
    args = call.args
    assert args[3:7] == ("INTC", "SELL", 2.0, round(115.93 * 0.99, 2)) and call.kwargs["opens_position"] is False
    assert "not yet a fill" in resp.get_json()["data"]["next"]
    assert list_overnight_orders(client.uid)[0]["owner_close"]["client_order_id"] == "close-1"


def test_close_needs_market_hours_and_nothing_works_in_live(client, monkeypatch):
    with patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"):
        assert client.post("/api/broker/positions/r-intc/close", json={"confirm_symbol": "INTC"}).status_code == 400
    monkeypatch.setattr(pluto_app.webull_api, "is_live_trading_armed", lambda: True)
    response = _with(_broker(), lambda: client.post("/api/broker/positions/r-intc/protect", json={"stop_price": 110, "confirm_symbol": "INTC"}))
    assert response.status_code == 400 and "sandbox only" in response.get_json()["error"]["message"]


def test_nothing_happens_when_the_broker_no_longer_holds_it(client):
    patches = [patch.object(pluto_app, "get_webull_credentials", return_value=CREDS),
               patch.object(pluto_app.webull_api, "get_account_positions", return_value=[])]
    response = _with(patches, lambda: client.post("/api/broker/positions/r-intc/protect", json={"stop_price": 110, "confirm_symbol": "INTC"}))
    assert response.status_code == 400 and "no INTC position" in response.get_json()["error"]["message"]
