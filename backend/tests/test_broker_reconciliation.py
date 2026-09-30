"""Broker-vs-app comparison (read-only). Broker payloads are hand-built in
the shapes the Webull client returns; no broker is contacted."""

from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import auth
import broker_reconciliation as br
import order_lifecycle as ol


def _record(ticker, qty=5, account="cash", state=ol.PROTECTION_CONFIRMED_ACTIVE, **extra):
    return {"record_id": f"r-{ticker}", "ticker": ticker, "account_id": account, "quantity": qty, "filled_quantity": qty,
            "lifecycle_state": state, "environment": "sandbox", "stop_client_order_id": f"stop-{ticker}", **extra}


def _stop(ticker):
    return {"order_id": f"o-{ticker}", "client_order_id": f"stop-{ticker}", "symbol": ticker, "side": "SELL", "order_type": "STOP_LOSS"}


def _categories(report):
    return {(i["symbol"], i["category"]) for i in report["items"]}


def test_every_category_is_reported():
    records = [
        _record("AAPL"),                                   # matched, protected
        _record("MSFT", qty=5),                            # broker holds 3
        _record("MU"),                                     # broker holds none
        _record("NVDA"),                                   # no stop at broker
        _record("AMD", state=ol.CLOSED),                   # closed - ignored
        _record("TSLA", environment="live"),               # other environment - ignored
    ]
    positions = {"cash": [{"symbol": "AAPL", "quantity": "5"}, {"symbol": "MSFT", "quantity": "3"},
                          {"symbol": "NVDA", "quantity": "5"}, {"symbol": "INTC", "quantity": "10"}, {"symbol": "TSLA", "quantity": "1"}]}
    orders = {"cash": [_stop("AAPL"), _stop("MSFT")]}
    report = br.compare(records, positions, orders, environment="sandbox")
    assert _categories(report) == {
        ("AAPL", "matched"), ("MSFT", "quantity_mismatch"), ("MU", "record_only"), ("NVDA", "matched"),
        ("NVDA", "protection_missing_at_broker"), ("INTC", "broker_only"), ("TSLA", "broker_only"),
    }
    assert report["status"] == "differences" and report["differences"] == 5


def test_records_are_compared_only_with_their_own_account():
    records = [_record("AAPL", account="margin")]
    positions = {"cash": [{"symbol": "AAPL", "quantity": 5}], "margin": []}
    report = br.compare(records, positions, {}, environment="sandbox")
    assert _categories(report) == {("AAPL", "broker_only"), ("AAPL", "record_only")}


def test_option_positions_have_no_broker_stop_expectation():
    records = [_record("AAPL", instrument_type="OPTION", option_symbol="AAPL261016C00200000", qty=1)]
    report = br.compare(records, {"cash": [{"symbol": "AAPL261016C00200000", "quantity": 1}]}, {}, environment="sandbox")
    assert _categories(report) == {("AAPL261016C00200000", "matched")} and report["status"] == "ok"


def test_a_failed_read_is_disconnected_and_keeps_the_last_good_time(user_id):
    ok = br.run(user_id, accounts=["cash"], records=[], read_positions=lambda a: [], read_open_orders=lambda a: [])
    good_time = ok["last_success_at"]
    def boom(account):
        raise RuntimeError("HTTP 503")
    stored = br.run(user_id, accounts=["cash"], records=[], read_positions=boom, read_open_orders=lambda a: [])
    assert stored["latest"]["status"] == "disconnected" and stored["latest"]["differences"] is None
    assert "HTTP 503" in stored["latest"]["errors"][0]
    assert stored["last_success_at"] == good_time and stored["history"][0]["status"] == "disconnected"


def test_route_runs_read_only_calls_only(user_id):
    user = auth.register_user(f"recon-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    creds = {"app_key": "k", "app_secret": "s"}
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        with patch.object(pluto_app, "is_webull_configured", return_value=True), \
             patch.object(pluto_app, "get_webull_credentials", return_value=creds), \
             patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "cash-1", "account_class": "INDIVIDUAL_CASH"}]), \
             patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "INTC", "quantity": "10"}]), \
             patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
             patch.object(pluto_app.webull_api, "place_order", create=True) as place, \
             patch.object(pluto_app.webull_api, "cancel_order", create=True) as cancel:
            response = client.post("/api/broker/reconciliation")
        data = response.get_json()["data"]
        assert data["latest"]["status"] == "differences"
        assert data["latest"]["items"][0]["category"] == "broker_only"
        place.assert_not_called()
        cancel.assert_not_called()
        again = client.get("/api/broker/reconciliation").get_json()["data"]
        assert again["latest"]["run_id"] == data["latest"]["run_id"]


# --- replay: a real (sanitized) Webull sandbox payload, captured read-only -------


def _fixture():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / "webull_sandbox_cash_2026-09-29.json").read_text())


def test_replay_untracked_sandbox_positions_are_broker_only():
    payload = _fixture()
    report = br.compare([], {"cash": payload["positions"]}, {"cash": payload["open_orders"]}, environment="sandbox")
    assert _categories(report) == {("INTC", "broker_only"), ("MSTR", "broker_only"), ("MRVL", "broker_only")}


def test_replay_records_claiming_protection_are_flagged_when_the_broker_has_no_stops():
    payload = _fixture()
    held = {p["symbol"]: float(p["quantity"]) for p in payload["positions"]}
    records = [_record(symbol, qty=qty) for symbol, qty in held.items()] + [_record("MU", qty=3)]
    report = br.compare(records, {"cash": payload["positions"]}, {"cash": payload["open_orders"]}, environment="sandbox")
    categories = _categories(report)
    for symbol in held:
        assert (symbol, "matched") in categories and (symbol, "protection_missing_at_broker") in categories
    assert ("MU", "record_only") in categories
