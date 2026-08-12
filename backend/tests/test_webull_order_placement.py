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
    # 429 is an infrastructure/rate-limit failure, not the broker's trading
    # engine evaluating and rejecting this specific order - exhausting
    # retries must raise AmbiguousOrderSubmission, never a definite failure.
    rate_limited = ServerException("RATE_LIMITED", "too fast", http_status=429, request_id="r1")
    client = _mock_client_with(place_order_side_effect=[rate_limited, rate_limited, rate_limited])
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        with pytest.raises(webull_api.AmbiguousOrderSubmission, match="Webull API error"):
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
    # It still doesn't RETRY a well-formed, non-429 rejection - but with no
    # error code on the confirmed allowlist yet (see
    # _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES), an unrecognized code like
    # this must classify as ambiguous, not definite, even though it "looks"
    # rejection-shaped (real error_code, non-infrastructure HTTP status).
    bad_request = ServerException("OAUTH_OPENAPI_PARAM_ERR", "invalid quantity", http_status=400, request_id="r3")
    client = _mock_client_with(place_order_side_effect=[bad_request])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission, match="OAUTH_OPENAPI_PARAM_ERR"):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")
    assert client.order_v2.place_order.call_count == 1


def test_confirmed_error_code_on_the_allowlist_classifies_as_definite(monkeypatch):
    # Proves the ALLOWLIST MECHANISM itself works, without claiming any
    # specific code is actually confirmed today - temporarily adds a
    # made-up code to simulate what happens once a real one is confirmed
    # live and added to _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES.
    monkeypatch.setattr(webull_api, "_CONFIRMED_DEFINITE_REJECTION_ERROR_CODES", frozenset({"TEST_CONFIRMED_REJECTION"}))
    rejection = ServerException("TEST_CONFIRMED_REJECTION", "invalid quantity", http_status=400, request_id="r3b")
    client = _mock_client_with(place_order_side_effect=[rejection])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.DefiniteOrderRejection, match="TEST_CONFIRMED_REJECTION"):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


def test_allowlisted_code_still_ambiguous_if_http_status_is_infrastructure_shaped(monkeypatch):
    # Belt-and-suspenders: even a CONFIRMED code must not classify as
    # definite if it arrives with an infrastructure/auth/rate-limit HTTP
    # status - that combination is itself suspicious, not a stronger signal.
    monkeypatch.setattr(webull_api, "_CONFIRMED_DEFINITE_REJECTION_ERROR_CODES", frozenset({"TEST_CONFIRMED_REJECTION"}))
    rejection = ServerException("TEST_CONFIRMED_REJECTION", "trouble", http_status=503, request_id="r3c")
    client = _mock_client_with(place_order_side_effect=[rejection])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


@pytest.mark.parametrize("http_status", [401, 403, 429, 500, 502, 503, 504])
def test_server_errors_with_ambiguous_http_status_never_classify_as_definite(http_status):
    # An unrecognized code with an infrastructure/auth/rate-limit HTTP
    # status is ambiguous today regardless (nothing is on the allowlist
    # yet) - covered for its own sake since this is the common real-world
    # case this whole classification scheme exists to keep safe.
    error = ServerException("SOME_CODE", "server trouble", http_status=http_status, request_id="r4")
    client = _mock_client_with(place_order_side_effect=[error])
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


def test_unparseable_response_body_is_ambiguous_even_with_a_non_ambiguous_http_status():
    # error_code.SDK_UNKNOWN_SERVER_ERROR is the SDK's own sentinel for "the
    # response body could not be parsed" - there's no genuine PARSED
    # rejection to act on here, so this must be ambiguous regardless of the
    # HTTP status looking otherwise definite-shaped.
    from webull.core.exception import error_code

    error = ServerException(error_code.SDK_UNKNOWN_SERVER_ERROR, "", http_status=400, request_id="r5")
    client = _mock_client_with(place_order_side_effect=[error])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


def test_client_exception_during_placement_is_ambiguous():
    # A network/SDK-level failure (timeout, dropped connection) never
    # proves the broker even received the request.
    from webull.core.exception.exceptions import ClientException

    client = MagicMock()
    client.order_v2.place_order.side_effect = ClientException("SDK.HttpError", "connection timed out")
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


def test_unexpected_exception_type_during_placement_defaults_to_ambiguous():
    # Fail-safe default: anything not explicitly classified as a definite
    # rejection must be ambiguous, never silently treated as a bare/unknown
    # failure a caller might misinterpret.
    client = MagicMock()
    client.order_v2.place_order.side_effect = TimeoutError("socket timed out")
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.place_stock_order("key", "secret", "acct1", "AAPL", "BUY", 10, 200.0, client_order_id="id-1")


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
    # Defensive coverage for a non-2xx Response OBJECT returned normally
    # (rather than the SDK raising ServerException, per get_response()'s
    # real behavior) - reachable only for a 2xx status that isn't literally
    # 200, but still must fail closed, not silently succeed.
    detail_response = MagicMock()
    detail_response.status_code = 404
    client = MagicMock()
    client.order_v2.get_order_detail.return_value = detail_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission, match="Webull API error"):
            webull_api.get_order_detail("key", "secret", "acct1", "missing-id")


def test_get_order_detail_unconfirmed_not_found_code_stays_ambiguous():
    # "ORDER_NOT_FOUND" is a plausible, illustrative code, not one
    # empirically confirmed against the live sandbox - with nothing on
    # _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES yet, this must stay
    # ambiguous rather than being trusted as conclusive proof the order
    # never existed (see _reconcile_unknown_submission's grace-period
    # handling, which depends on this never firing prematurely).
    rejection = ServerException("ORDER_NOT_FOUND", "no such order", http_status=404, request_id="r6")
    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = rejection
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission, match="ORDER_NOT_FOUND"):
            webull_api.get_order_detail("key", "secret", "acct1", "missing-id")


def test_get_order_detail_confirmed_rejection_code_is_definite(monkeypatch):
    monkeypatch.setattr(webull_api, "_CONFIRMED_DEFINITE_REJECTION_ERROR_CODES", frozenset({"TEST_CONFIRMED_REJECTION"}))
    rejection = ServerException("TEST_CONFIRMED_REJECTION", "no such order", http_status=404, request_id="r6b")
    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = rejection
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.DefiniteOrderRejection, match="TEST_CONFIRMED_REJECTION"):
            webull_api.get_order_detail("key", "secret", "acct1", "missing-id")


@pytest.mark.parametrize("http_status", [401, 403, 429, 500, 503])
def test_get_order_detail_ambiguous_http_status_stays_ambiguous(http_status):
    error = ServerException("SOME_CODE", "trouble", http_status=http_status, request_id="r7")
    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = error
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.get_order_detail("key", "secret", "acct1", "id-1")


def test_get_order_detail_client_exception_is_ambiguous():
    from webull.core.exception.exceptions import ClientException

    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = ClientException("SDK.HttpError", "connection reset")
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.AmbiguousOrderSubmission):
            webull_api.get_order_detail("key", "secret", "acct1", "id-1")


def test_get_open_orders_normalizes_dict_response():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": [{"order_id": "1", "client_order_id": "a"}, {"order_id": "2", "client_order_id": "b"}]}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert len(result) == 2


def test_get_open_orders_empty_list_is_a_legitimate_zero_orders_response():
    # The one real confirmed empty-account shape - "orders" key present,
    # explicitly an empty list.
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": []}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert result == []


def test_get_open_orders_missing_orders_key_fails_closed_not_zero_orders():
    # A MISSING "orders" key is a different, more specific failure than
    # "the account genuinely has zero open orders" - a malformed response
    # silently read as "zero orders" would under-count committed capital
    # and overstate available buying power (see _compute_committed_virtual_capital).
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="missing the 'orders' field"):
            webull_api.get_open_orders("key", "secret", "acct1")


def test_get_open_orders_non_list_orders_field_fails_closed():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": "not-a-list"}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="not a list"):
            webull_api.get_open_orders("key", "secret", "acct1")


def test_get_open_orders_non_dict_row_fails_closed():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": [{"order_id": "a"}, "garbage-row"]}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="not a JSON object"):
            webull_api.get_open_orders("key", "secret", "acct1")


def test_get_open_orders_non_dict_payload_fails_closed():
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = ["not", "a", "dict"]
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="expected a JSON object"):
            webull_api.get_open_orders("key", "secret", "acct1")


def test_get_open_orders_row_missing_order_id_fails_closed():
    # Every row must carry a stable order_id - a row missing one (anywhere
    # in the page, not just the last row) would otherwise collide with
    # every OTHER such row during de-duplication (None == None) and be
    # silently dropped as a "duplicate" rather than counted.
    open_response = MagicMock()
    open_response.status_code = 200
    open_response.json.return_value = {"orders": [{"order_id": "a"}, {"client_order_id": "b-only"}]}
    client = MagicMock()
    client.order_v2.get_order_open.return_value = open_response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="stable order_id"):
            webull_api.get_open_orders("key", "secret", "acct1")


def _open_orders_page(orders):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"orders": orders}
    return response


def test_get_open_orders_walks_multiple_pages_until_a_short_page():
    page_size = webull_api.OPEN_ORDERS_PAGE_SIZE
    page_1 = [{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(page_size)]
    page_2 = [{"order_id": "o-last", "client_order_id": "c-last"}]  # short - signals the end
    client = MagicMock()
    client.order_v2.get_order_open.side_effect = [_open_orders_page(page_1), _open_orders_page(page_2)]
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert len(result) == page_size + 1
    assert client.order_v2.get_order_open.call_count == 2
    # The second call must have advanced the cursor using the last item of page 1.
    second_call_kwargs = client.order_v2.get_order_open.call_args_list[1].kwargs
    assert second_call_kwargs["last_order_id"] == f"o{page_size - 1}"
    assert second_call_kwargs["last_client_order_id"] == f"c{page_size - 1}"


def test_get_open_orders_raises_if_the_broker_ignores_pagination_instead_of_returning_partial_results():
    # get_order_open's own docstring says paging is only honored for Webull
    # HK - other regions may return the SAME full page every time regardless
    # of last_order_id/last_client_order_id. Must not loop forever on that -
    # and must not silently return the one page it did see either, since a
    # caller has no way to tell that result apart from a genuinely complete
    # one. Raising is the only safe response to a stalled cursor.
    page_size = webull_api.OPEN_ORDERS_PAGE_SIZE
    full_page = [{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(page_size)]
    client = MagicMock()
    client.order_v2.get_order_open.return_value = _open_orders_page(full_page)  # identical every call
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="cursor did not advance"):
            webull_api.get_open_orders("key", "secret", "acct1")


def test_get_open_orders_raises_instead_of_silently_returning_a_truncated_result_when_pages_keep_growing():
    # Belt-and-suspenders: even genuinely new, ever-advancing pages can't
    # make this run unbounded - _OPEN_ORDERS_MAX_PAGES caps it - but hitting
    # that cap must raise, not silently hand back a partial result a caller
    # would have no way to distinguish from a complete one.
    page_size = webull_api.OPEN_ORDERS_PAGE_SIZE

    def _endless_pages(account_id, page_size=None, last_order_id=None, last_client_order_id=None):
        start = int(last_order_id[1:]) + 1 if last_order_id else 0
        return _open_orders_page([{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(start, start + page_size)])

    client = MagicMock()
    client.order_v2.get_order_open.side_effect = _endless_pages
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="exhausted the .*-page limit"):
            webull_api.get_open_orders("key", "secret", "acct1")
    assert client.order_v2.get_order_open.call_count == webull_api._OPEN_ORDERS_MAX_PAGES


# --- get_order_history -------------------------------------------------------


def _order_history_response(orders):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"orders": orders}
    return response


def test_get_order_history_returns_orders():
    client = MagicMock()
    client.order_v2.get_order_history.return_value = _order_history_response(
        [{"order_id": "1", "client_order_id": "c1", "status": "FILLED"}]
    )
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_order_history("key", "secret", "acct1")
    assert len(result) == 1
    assert result[0]["status"] == "FILLED"
    # Confirms it actually passed a date range, not an unbounded query.
    call_kwargs = client.order_v2.get_order_history.call_args.kwargs
    assert call_kwargs["start_date"]
    assert call_kwargs["end_date"]


def test_get_order_history_raises_on_non_200():
    response = MagicMock()
    response.status_code = 500
    client = MagicMock()
    client.order_v2.get_order_history.return_value = response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="Webull API error"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_raises_on_missing_orders_field():
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}
    client.order_v2.get_order_history.return_value = response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="missing the 'orders' field"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_raises_on_malformed_orders_field():
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"orders": "not-a-list"}
    client.order_v2.get_order_history.return_value = response
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="not a list"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_raises_on_non_dict_row():
    client = MagicMock()
    client.order_v2.get_order_history.return_value = _order_history_response([{"order_id": "a"}, "garbage-row"])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="not a JSON object"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_row_missing_order_id_fails_closed():
    client = MagicMock()
    client.order_v2.get_order_history.return_value = _order_history_response(
        [{"order_id": "a"}, {"client_order_id": "b-only"}]
    )
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="stable order_id"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_walks_multiple_pages_until_a_short_page():
    page_size = webull_api.ORDER_HISTORY_PAGE_SIZE
    page_1 = [{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(page_size)]
    page_2 = [{"order_id": "o-last", "client_order_id": "c-last"}]  # short - signals the end
    client = MagicMock()
    client.order_v2.get_order_history.side_effect = [_order_history_response(page_1), _order_history_response(page_2)]
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_order_history("key", "secret", "acct1")
    assert len(result) == page_size + 1
    assert client.order_v2.get_order_history.call_count == 2
    second_call_kwargs = client.order_v2.get_order_history.call_args_list[1].kwargs
    assert second_call_kwargs["last_order_id"] == f"o{page_size - 1}"
    assert second_call_kwargs["last_client_order_id"] == f"c{page_size - 1}"


def test_get_order_history_raises_if_the_broker_ignores_pagination_instead_of_returning_partial_results():
    # A truncated "nothing found" here would wrongly justify releasing a
    # capital freeze - see the function's docstring. Must raise, not
    # silently return the one page it saw.
    page_size = webull_api.ORDER_HISTORY_PAGE_SIZE
    full_page = [{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(page_size)]
    client = MagicMock()
    client.order_v2.get_order_history.return_value = _order_history_response(full_page)  # identical every call
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="cursor did not advance"):
            webull_api.get_order_history("key", "secret", "acct1")


def test_get_order_history_raises_instead_of_silently_returning_a_truncated_result_when_pages_keep_growing():
    page_size = webull_api.ORDER_HISTORY_PAGE_SIZE

    def _endless_pages(account_id, page_size=None, start_date=None, end_date=None, last_order_id=None, last_client_order_id=None):
        start = int(last_order_id[1:]) + 1 if last_order_id else 0
        return _order_history_response([{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(start, start + page_size)])

    client = MagicMock()
    client.order_v2.get_order_history.side_effect = _endless_pages
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(ValueError, match="exhausted the .*-page limit"):
            webull_api.get_order_history("key", "secret", "acct1")
    assert client.order_v2.get_order_history.call_count == webull_api._ORDER_HISTORY_MAX_PAGES


def test_get_order_history_empty_orders_returns_empty_list():
    client = MagicMock()
    client.order_v2.get_order_history.return_value = _order_history_response([])
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        result = webull_api.get_order_history("key", "secret", "acct1")
    assert result == []
