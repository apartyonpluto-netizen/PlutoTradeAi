from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-cash-1"


def _orphan(user_id, ticker, **extra):
    entry = {
        "ticker": ticker,
        "account_id": ACCOUNT_ID,
        "trading_day": "2026-09-21",
        "stop": 0,
        "target": 0,
        "quantity": 2,
        "filled_quantity": 2,
        "orphan_recovered": True,
        "entry_order_terminal": True,
        "direction": "long",
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"entry-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=2)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="no stop")
    entry.update(extra)
    record_overnight_order(user_id, entry)
    return entry


def test_attaches_a_historical_stop_that_is_still_below_the_market(user_id):
    _orphan(user_id, "INTC")
    history = [{
        "symbol": "INTC", "side": "SELL", "status": "CANCELLED", "order_type": "STOP_LOSS",
        "filled_quantity": "0", "stop_price": "104.00", "place_time": "1789998695188",
    }]
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "INTC", "quantity": "2", "last_price": "115.93"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=history) as mock_history, \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop"}) as mock_place:
        report = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
        report_again = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    assert report[0]["action"] == "attached"
    assert mock_place.call_args.kwargs["stop_price"] == 104.0
    assert mock_place.call_args.kwargs["quantity"] == 2.0
    stored = list_overnight_orders(user_id)[0]
    assert stored["stop"] == 104.0
    assert stored["stop_client_order_id"]
    assert stored["stop_leg_quantity"] == 2.0
    assert mock_history.call_count == 1  # second pass uses the 15-minute cache
    assert report_again == []  # the record now has a stop id, so it is not placed again


def test_does_not_attach_a_historical_stop_that_is_through_the_market(user_id):
    _orphan(user_id, "INTC")
    history = [{
        "symbol": "INTC", "side": "SELL", "status": "CANCELLED", "order_type": "STOP_LOSS",
        "filled_quantity": "0", "stop_price": "104.00", "place_time": "10",
    }]
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "INTC", "quantity": "2", "last_price": "100"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=history), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        report = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    mock_place.assert_not_called()
    assert report[0]["action"] == "flagged"
    assert list_overnight_orders(user_id)[0]["naked_position"] is True


def test_working_stop_is_left_alone(user_id):
    _orphan(user_id, "MRVL", stop=80)
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "MRVL", "quantity": "4", "last_price": "90"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[{"symbol": "MRVL", "order_type": "STOP_LOSS"}]), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place, \
         patch.object(pluto_app.webull_api, "get_order_history") as mock_history:
        report = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    assert report == []
    mock_place.assert_not_called()
    mock_history.assert_not_called()


def test_outside_core_flags_and_does_not_place(user_id):
    _orphan(user_id, "MSTR", stop=300)
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "MSTR", "quantity": "5", "last_price": "350"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        report = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    mock_place.assert_not_called()
    assert "CORE" in report[0]["reason"]
    assert list_overnight_orders(user_id)[0]["naked_position"] is True


def test_no_known_price_is_flagged_every_pass_without_an_order(user_id):
    _orphan(user_id, "MRVL")
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "MRVL", "quantity": "4", "last_price": "90"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        first = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
        second = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    mock_place.assert_not_called()
    assert first[0]["action"] == "flagged"
    assert second[0]["action"] == "flagged"
    assert list_overnight_orders(user_id)[0]["naked_position"] is True


def test_option_symbols_are_skipped(user_id):
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL250620C00150000", "quantity": "1", "last_price": "3"}]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        report = pluto_app._review_naked_equity_positions(user_id, CREDS, ACCOUNT_ID)
    assert report == []
    mock_place.assert_not_called()
