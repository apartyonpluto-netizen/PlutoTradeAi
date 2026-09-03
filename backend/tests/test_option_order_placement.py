from __future__ import annotations

from unittest.mock import MagicMock, patch

from webull.core.exception.exceptions import ServerException

from integrations import webull as webull_api

"""Real options trading v1 (2026-09-03) - integrations/webull.py's new
get_option_contracts/get_option_snapshot/preview_option_order/
place_option_order/cancel_option_order functions. The order shape built by
_build_option_order was confirmed live against the real sandbox via
preview_option (both a BUY CALL and a BUY PUT accepted, see the function's
own docstring) before being adopted here - these tests lock that confirmed
shape in, they don't re-derive it."""


def _success_response(payload=None):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload if payload is not None else {"order_id": "OPT-ORDER-1"}
    return response


def test_build_option_order_matches_the_shape_confirmed_live_against_the_sandbox():
    order = webull_api._build_option_order(
        "ADBE", "CALL", 420.0, "2026-09-18", "BUY", 1, 5.0, "DAY", "fixed-id",
    )
    assert order["option_strategy"] == "SINGLE"
    assert order["order_type"] == "LIMIT"
    assert order["side"] == "BUY"
    assert order["quantity"] == "1"
    assert order["limit_price"] == "5.0"
    assert order["client_order_id"] == "fixed-id"
    assert len(order["legs"]) == 1
    leg = order["legs"][0]
    assert leg["symbol"] == "ADBE"  # underlying ticker, NOT the resolved option_symbol
    assert leg["strike_price"] == "420.0"
    assert leg["option_expire_date"] == "2026-09-18"
    assert leg["option_type"] == "CALL"
    assert leg["instrument_type"] == "OPTION"
    assert leg["market"] == "US"
    assert leg["side"] == "BUY"
    assert leg["quantity"] == "1"


def test_preview_option_order_calls_preview_option_not_preview_order():
    client = MagicMock()
    client.order_v2.preview_option.return_value = _success_response({"estimated_cost": "500"})
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.preview_option_order(
            "key", "secret", "acct1", "ADBE", "CALL", 420.0, "2026-09-18", "BUY", 1, 5.0,
        )
    assert result == {"estimated_cost": "500"}
    client.order_v2.preview_option.assert_called_once()
    client.order_v2.place_order.assert_not_called()
    client.order_v2.place_option.assert_not_called()


def test_place_option_order_calls_place_option_not_place_order():
    client = MagicMock()
    client.order_v2.place_option.return_value = _success_response({"order_id": "OPT-1"})
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.place_option_order(
            "key", "secret", "acct1", "ADBE", "CALL", 420.0, "2026-09-18", "BUY", 1, 5.0,
            client_order_id="fixed-opt-id",
        )
    assert result["order_id"] == "OPT-1"
    assert result["client_order_id"] == "fixed-opt-id"
    assert result["idempotent_replay"] is False
    client.order_v2.place_option.assert_called_once()
    client.order_v2.place_order.assert_not_called()


def test_place_option_order_repeat_client_order_id_is_treated_as_idempotent_replay():
    """Same real, confirmed-live behavior place_stock_order already relies
    on (see test_webull_order_placement.py) - a repeated client_order_id
    comes back as HTTP 417 REPEAT_ORDER_ERROR_CODE, resolved by looking up
    the existing order rather than raised as a new failure. Options orders
    go through the exact same _place_order_with_retry, just pointed at
    place_option instead of place_order."""
    repeat_error = ServerException(
        webull_api.REPEAT_ORDER_ERROR_CODE, "Please do not place an order repeatedly", http_status=417, request_id="r1",
    )
    client = MagicMock()
    client.order_v2.place_option.side_effect = repeat_error
    detail_response = MagicMock()
    detail_response.status_code = 200
    detail_response.json.return_value = {"order_id": "OPT-EXISTING"}
    client.order_v2.get_order_detail.return_value = detail_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.place_option_order(
            "key", "secret", "acct1", "ADBE", "CALL", 420.0, "2026-09-18", "BUY", 1, 5.0,
            client_order_id="dup-id",
        )
    assert result["order_id"] == "OPT-EXISTING"
    assert result["idempotent_replay"] is True


def test_cancel_option_order_calls_cancel_option_not_cancel_order():
    client = MagicMock()
    client.order_v2.cancel_option.return_value = _success_response({"status": "cancelled"})
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.cancel_option_order("key", "secret", "acct1", "some-id")
    assert result == {"status": "cancelled"}
    client.order_v2.cancel_option.assert_called_once_with("acct1", "some-id")
    client.order_v2.cancel_order.assert_not_called()


def test_get_option_contracts_returns_the_list_from_a_dict_payload():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"symbol": "ADBE260918C00420000", "strike_price": "420"}]}
    data_client = MagicMock()
    data_client.instrument.get_option_contracts.return_value = response
    with patch.object(webull_api, "_get_data_client", return_value=data_client):
        contracts = webull_api.get_option_contracts(
            "key", "secret", "ADBE", option_type="CALL", start_date="2026-09-10", end_date="2026-09-24",
        )
    assert contracts == [{"symbol": "ADBE260918C00420000", "strike_price": "420"}]
    data_client.instrument.get_option_contracts.assert_called_once()
    _, kwargs = data_client.instrument.get_option_contracts.call_args
    assert kwargs["underlying_symbols"] == "ADBE"
    assert kwargs["status"] == "LISTING"
    assert kwargs["option_type"] == "CALL"


def test_get_option_contracts_returns_empty_list_on_unrecognized_shape():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"unexpected": "shape"}
    data_client = MagicMock()
    data_client.instrument.get_option_contracts.return_value = response
    with patch.object(webull_api, "_get_data_client", return_value=data_client):
        contracts = webull_api.get_option_contracts("key", "secret", "ADBE")
    assert contracts == []


def test_get_option_snapshot_joins_symbols_and_returns_json():
    # Real shape confirmed live 2026-09-03: a LIST of per-symbol dicts, not
    # a dict keyed by symbol - see get_option_snapshot's own docstring.
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [{"symbol": "ADBE260918C00420000", "bid": "0.04", "ask": "0.07"}]
    data_client = MagicMock()
    data_client.option_market_data.get_option_snapshot.return_value = response
    with patch.object(webull_api, "_get_data_client", return_value=data_client):
        snapshot = webull_api.get_option_snapshot("key", "secret", ["ADBE260918C00420000", "ADBE260918P00320000"])
    assert snapshot == [{"symbol": "ADBE260918C00420000", "bid": "0.04", "ask": "0.07"}]
    data_client.option_market_data.get_option_snapshot.assert_called_once_with(
        "ADBE260918C00420000,ADBE260918P00320000", category="US_OPTION",
    )


def test_get_option_snapshot_returns_empty_list_for_no_symbols_without_calling_the_broker():
    data_client = MagicMock()
    with patch.object(webull_api, "_get_data_client", return_value=data_client):
        snapshot = webull_api.get_option_snapshot("key", "secret", [])
    assert snapshot == []
    data_client.option_market_data.get_option_snapshot.assert_not_called()
