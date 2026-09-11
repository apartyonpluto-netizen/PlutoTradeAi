from __future__ import annotations

from unittest.mock import patch

from integrations import market_data_aggregator as aggregator

"""integrations/market_data_aggregator.py: cross-checks Alpaca's real-time
price against Webull's own equity snapshot before a real order submission
(see the module's own docstring for the full rationale). These tests lock
in its fail-closed-on-genuine-disagreement, fail-OPEN-on-missing-second-
opinion behavior - the core safety property the module exists for."""

CREDS = {"app_key": "key", "app_secret": "secret"}


def test_agreement_within_tolerance_returns_the_alpaca_price():
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[{"symbol": "AAPL", "price": "100.9"}]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] == 100.0
    assert result["disagreement"] is False


def test_disagreement_beyond_tolerance_fails_closed():
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[{"symbol": "AAPL", "price": "112.0"}]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] is None
    assert result["disagreement"] is True
    statuses = {p["provider"]: p["price"] for p in result["provider_status"]}
    assert statuses == {"alpaca": 100.0, "webull": 112.0}


def test_disagreement_exactly_at_the_tolerance_boundary_is_not_disagreement():
    # Strictly greater-than the tolerance, not greater-or-equal - matches
    # _price_has_drifted_too_far's own boundary convention in app.py.
    tolerance = aggregator.PRICE_SOURCE_DISAGREEMENT_TOLERANCE_PERCENT
    webull_price = 100.0 * (1 + tolerance / 100.0)
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[{"symbol": "AAPL", "price": webull_price}]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["disagreement"] is False
    assert result["price"] == 100.0


def test_alpaca_unavailable_returns_none_regardless_of_webull():
    # Matches today's existing behavior exactly - Alpaca is the source of
    # record; losing it fails closed no matter what Webull says.
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=None), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[{"symbol": "AAPL", "price": "100.0"}]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] is None
    assert result["disagreement"] is False


def test_webull_erroring_falls_back_to_alpaca_alone_not_disagreement():
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", side_effect=ValueError("HTTP 500")):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] == 100.0
    assert result["disagreement"] is False


def test_webull_empty_snapshot_falls_back_to_alpaca_alone():
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] == 100.0
    assert result["disagreement"] is False


def test_webull_snapshot_missing_the_price_field_falls_back_to_alpaca_alone():
    # The exact case get_equity_snapshot's own docstring warns about - a
    # wrong/missing field name must degrade to "unavailable", never a
    # confident (and wrong) read.
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot", return_value=[{"symbol": "AAPL", "bid": "99.9"}]):
        result = aggregator.get_cross_checked_price("AAPL", CREDS)
    assert result["price"] == 100.0
    assert result["disagreement"] is False


def test_no_webull_credentials_falls_back_to_alpaca_alone_without_calling_the_broker():
    with patch.object(aggregator.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(aggregator.webull_api, "get_equity_snapshot") as mock_snapshot:
        result = aggregator.get_cross_checked_price("AAPL", None)
    assert result["price"] == 100.0
    assert result["disagreement"] is False
    mock_snapshot.assert_not_called()
