from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from webull.core.exception.exceptions import ServerException

from integrations import webull as webull_api

"""Every read call in integrations/webull.py (accounts, balance, positions,
order detail, open orders, order history) had NO retry logic at all until
this session: the vendored SDK raises a raw ServerException for ANY non-2xx
response rather than returning a checkable status_code (see the note above
REPEAT_ORDER_ERROR_CODE in webull.py) - a single transient 429 previously
propagated straight out as an unhandled exception. Only order PLACEMENT
already retried on 429 (test_webull_order_placement.py). This file covers
the same retry-then-succeed / exhaust-and-raise behavior for every read
path, added as part of hardening against Webull's (still uncharacterized)
rate limits - see integrations/webull.py's _call_with_429_retry."""

RATE_LIMITED = ServerException("RATE_LIMITED", "too fast", http_status=429, request_id="r1")


def _response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_get_paper_accounts_retries_on_429_then_succeeds():
    client = MagicMock()
    client.account_v2.get_account_list.side_effect = [RATE_LIMITED, _response([{"account_id": "a1"}])]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_paper_accounts("key", "secret")
    assert result == [{"account_id": "a1"}]
    assert client.account_v2.get_account_list.call_count == 2


def test_get_paper_accounts_exhausts_retries_and_raises_value_error():
    client = MagicMock()
    client.account_v2.get_account_list.side_effect = [RATE_LIMITED, RATE_LIMITED, RATE_LIMITED]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        with pytest.raises(ValueError, match="Webull API error"):
            webull_api.get_paper_accounts("key", "secret")
    assert client.account_v2.get_account_list.call_count == 3


def test_get_account_balance_retries_on_429_then_succeeds():
    client = MagicMock()
    client.account_v2.get_account_balance.side_effect = [RATE_LIMITED, _response({"total_net_liquidation_value": 100.0})]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_account_balance("key", "secret", "acct1")
    assert result["total_net_liquidation_value"] == 100.0
    assert client.account_v2.get_account_balance.call_count == 2


def test_get_account_positions_retries_on_429_then_succeeds():
    client = MagicMock()
    client.account_v2.get_account_position.side_effect = [RATE_LIMITED, _response([{"symbol": "AAPL"}])]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_account_positions("key", "secret", "acct1")
    assert result == [{"symbol": "AAPL"}]
    assert client.account_v2.get_account_position.call_count == 2


def test_get_order_detail_retries_on_429_then_succeeds():
    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = [RATE_LIMITED, _response({"orders": [{"status": "FILLED"}]})]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_order_detail("key", "secret", "acct1", "cid-1")
    assert result == {"orders": [{"status": "FILLED"}]}
    assert client.order_v2.get_order_detail.call_count == 2


def test_get_order_detail_exhausts_retries_and_raises_ambiguous():
    client = MagicMock()
    client.order_v2.get_order_detail.side_effect = [RATE_LIMITED, RATE_LIMITED, RATE_LIMITED]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        # A rate-limit failure is an infrastructure issue, not a conclusive
        # broker answer about the order - must stay Ambiguous, never Definite.
        with pytest.raises(webull_api.AmbiguousOrderSubmission, match="Webull API error"):
            webull_api.get_order_detail("key", "secret", "acct1", "cid-1")
    assert client.order_v2.get_order_detail.call_count == 3


def _open_orders_page(orders):
    return _response({"orders": orders})


def test_get_open_orders_retries_a_429_mid_pagination_then_completes():
    client = MagicMock()
    full_page = [{"order_id": f"o{i}", "client_order_id": f"c{i}"} for i in range(webull_api.OPEN_ORDERS_PAGE_SIZE)]
    short_page = [{"order_id": "o-last", "client_order_id": "c-last"}]
    client.order_v2.get_order_open.side_effect = [RATE_LIMITED, _open_orders_page(full_page), _open_orders_page(short_page)]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_open_orders("key", "secret", "acct1")
    assert len(result) == len(full_page) + 1
    assert client.order_v2.get_order_open.call_count == 3


def test_get_order_history_retries_a_429_mid_pagination_then_completes():
    client = MagicMock()
    # A single-item page is already SHORTER than ORDER_HISTORY_PAGE_SIZE, so
    # pagination correctly stops here without requesting a second page -
    # 2 calls total (the 429 retry, then this one successful short page).
    page = [{"order_id": "h1"}]
    client.order_v2.get_order_history.side_effect = [RATE_LIMITED, _response({"orders": page})]
    with patch.object(webull_api, "_get_trade_client", return_value=client), patch.object(webull_api.time, "sleep"):
        result = webull_api.get_order_history("key", "secret", "acct1")
    assert result == page
    assert client.order_v2.get_order_history.call_count == 2
