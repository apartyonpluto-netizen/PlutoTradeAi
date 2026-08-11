from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from webull.core.exception.exceptions import ServerException

from integrations import webull as webull_api


def _mock_client_with(place_order_side_effect=None, place_order_return=None, get_order_detail_return=None):
    client = MagicMock()
    if place_order_side_effect is not None:
        client.order_v2.place_order.side_effect = place_order_side_effect
    elif place_order_return is not None:
        client.order_v2.place_order.return_value = place_order_return
    if get_order_detail_return is not None:
        detail_response = MagicMock()
        detail_response.status_code = 200
        detail_response.json.return_value = get_order_detail_return
        client.order_v2.get_order_detail.return_value = detail_response
    return client


def _success_response(order_id="ORDER123"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"order_id": order_id}
    return response


def test_place_order_success_returns_result_with_client_order_id_and_not_a_replay():
    client = _mock_client_with(place_order_return=_success_response())
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.place_stock_order(
            "key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="fixed-id-123"
        )
    assert result["client_order_id"] == "fixed-id-123"
    assert result["idempotent_replay"] is False
    assert result["order_id"] == "ORDER123"
    placed_order = client.order_v2.place_order.call_args[0][1][0]
    assert placed_order["client_order_id"] == "fixed-id-123", "the exact supplied id must reach the broker, not a random one"


def test_place_order_without_explicit_id_still_generates_one():
    client = _mock_client_with(place_order_return=_success_response())
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0)
    assert result["client_order_id"]


def test_429_retries_then_succeeds():
    rate_limited = ServerException("RATE_LIMITED", "too fast", http_status=429, request_id="r1")
    client = _mock_client_with(place_order_side_effect=[rate_limited, _success_response()])
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")
    assert result["order_id"] == "ORDER123"
    assert client.order_v2.place_order.call_count == 2


def test_429_exhausts_retries_and_raises():
    rate_limited = ServerException("RATE_LIMITED", "too fast", http_status=429, request_id="r1")
    client = _mock_client_with(place_order_side_effect=[rate_limited, rate_limited, rate_limited])
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        with pytest.raises(ValueError, match="Webull API error"):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")
    assert client.order_v2.place_order.call_count == 3


def test_repeated_client_order_id_looks_up_existing_order_instead_of_failing():
    """Confirmed live against the sandbox this session: Webull rejects a
    reused client_order_id with HTTP 417 OAUTH_OPENAPI_TRADE_PLACE_ORDER_REPEAT.
    A caller retrying the same logical placement after a crash should get the
    existing order back, not a fresh failure."""
    repeat_error = ServerException(
        webull_api.REPEAT_ORDER_ERROR_CODE, "Please do not place an order repeatedly", http_status=417, request_id="r2"
    )
    existing_order_detail = {
        "client_order_id": "id-1",
        "orders": [{"status": "FILLED", "total_quantity": "10", "filled_quantity": "10", "order_id": "ORDER123"}],
    }
    client = _mock_client_with(place_order_side_effect=[repeat_error], get_order_detail_return=existing_order_detail)
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")
    assert result["idempotent_replay"] is True
    assert result["client_order_id"] == "id-1"
    assert result["orders"][0]["status"] == "FILLED"
    client.order_v2.get_order_detail.assert_called_once_with("acct1", "id-1")


def test_other_server_error_raises_immediately_without_retry():
    bad_request = ServerException("OAUTH_OPENAPI_PARAM_ERR", "invalid quantity", http_status=400, request_id="r3")
    client = _mock_client_with(place_order_side_effect=[bad_request])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="OAUTH_OPENAPI_PARAM_ERR"):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")
    assert client.order_v2.place_order.call_count == 1


def test_stop_loss_and_take_profit_also_pass_through_explicit_client_order_id():
    # Two distinct response mocks - place_order.json() must not return the
    # same shared dict across calls, or mutating result["client_order_id"]
    # in _place_order_with_retry would silently alias both results together.
    client = MagicMock()
    client.order_v2.place_order.side_effect = [_success_response(), _success_response()]
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        stop_result = webull_api.place_stop_loss_order("key", "secret", "acct1", "AAPL", 10, 190.0, client_order_id="stop-id")
        target_result = webull_api.place_take_profit_order(
            "key", "secret", "acct1", "AAPL", 10, 220.0, client_order_id="target-id"
        )
    assert stop_result["client_order_id"] == "stop-id"
    assert target_result["client_order_id"] == "target-id"


def test_get_order_detail_returns_the_response_json():
    detail_response = MagicMock()
    detail_response.status_code = 200
    detail_response.json.return_value = {"orders": [{"status": "FILLED"}]}
    client = MagicMock()
    client.order_v2.get_order_detail.return_value = detail_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_order_detail("key", "secret", "acct1", "id-1")
    assert result["orders"][0]["status"] == "FILLED"


def test_get_order_detail_raises_on_non_200():
    detail_response = MagicMock()
    detail_response.status_code = 404
    client = MagicMock()
    client.order_v2.get_order_detail.return_value = detail_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="Webull API error"):
            webull_api.get_order_detail("key", "secret", "acct1", "missing-id")


def test_get_open_orders_normalizes_dict_response():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": [{"client_order_id": "a"}, {"client_order_id": "b"}]}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert len(result) == 2


def test_get_open_orders_normalizes_empty_response():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert result == []
