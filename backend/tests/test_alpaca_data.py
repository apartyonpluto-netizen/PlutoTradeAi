from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from integrations import alpaca_data

"""Found live 2026-08-28: Yahoo Finance is actively rate-limiting requests
from this app's Render deployment ("Scanner timed out - Yahoo Finance is
rate limiting.", 0 rows, every scan) - the direct cause of candidates_found
being 0 on every autonomous scan, not a confidence-threshold miss but a
total absence of scanner input data. This module replaces yfinance with
Alpaca's Market Data API on the candidate-discovery critical path (see its
own module docstring for exactly which call sites and why others were
deliberately left on yfinance). These tests prove the ACTUAL shape
callers depend on: a flat (never MultiIndex) DataFrame with the exact
Open/High/Low/Close/Volume columns yfinance already produced, so none of
market_scanner.py's or the three brains/ modules' existing downstream
normalization logic needs to change."""


def _fake_response(status_code: int, json_payload: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_payload or {}
    response.text = text
    return response


def test_is_configured_reflects_both_env_vars(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert alpaca_data.is_configured() is False

    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    assert alpaca_data.is_configured() is False, "secret alone missing - still not configured"

    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    assert alpaca_data.is_configured() is True


def test_get_bars_returns_flat_dataframe_with_yfinance_compatible_columns(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    payload = {
        "bars": {
            "AAPL": [
                {"t": "2026-08-27T14:30:00Z", "o": 190.0, "h": 191.5, "l": 189.5, "c": 191.0, "v": 1000},
                {"t": "2026-08-28T14:30:00Z", "o": 191.0, "h": 192.0, "l": 190.5, "c": 191.8, "v": 1200},
            ]
        },
        "next_page_token": None,
    }

    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(200, payload)) as mock_get:
        result = alpaca_data.get_bars(["aapl"], period="1mo", interval="1d")

    assert set(result.keys()) == {"AAPL"}
    frame = result["AAPL"]
    assert not isinstance(frame.columns, pd.MultiIndex), "downstream code branches on this - must stay flat"
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame["Close"].tolist() == [191.0, 191.8]
    assert frame.index.is_monotonic_increasing

    # Confirms the free-tier feed and a real symbol/timeframe made it into
    # the actual request - a silent default to the wrong feed would 401/403
    # against a Basic-plan key instead of just quietly returning less data,
    # but asserting it directly here catches the mistake without needing a
    # real key.
    called_kwargs = mock_get.call_args.kwargs
    assert called_kwargs["params"]["feed"] == "iex"
    assert called_kwargs["params"]["symbols"] == "AAPL"
    assert called_kwargs["params"]["timeframe"] == "1Day"
    assert called_kwargs["headers"]["APCA-API-KEY-ID"] == "key123"
    assert called_kwargs["headers"]["APCA-API-SECRET-KEY"] == "secret456"


def test_get_bars_follows_pagination_until_no_next_page_token(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    page_one = _fake_response(
        200,
        {
            "bars": {"AAPL": [{"t": "2026-08-27T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]},
            "next_page_token": "page2",
        },
    )
    page_two = _fake_response(
        200,
        {
            "bars": {"AAPL": [{"t": "2026-08-28T14:30:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}]},
            "next_page_token": None,
        },
    )

    with patch.object(alpaca_data.requests, "get", side_effect=[page_one, page_two]) as mock_get:
        result = alpaca_data.get_bars(["AAPL"], period="1mo", interval="1d")

    assert mock_get.call_count == 2
    assert result["AAPL"]["Close"].tolist() == [1.0, 2.0]
    # Second call must actually carry the page token forward, not repeat
    # the first request.
    assert mock_get.call_args_list[1].kwargs["params"]["page_token"] == "page2"


def test_get_bars_retries_once_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    rate_limited = _fake_response(429, {})
    success = _fake_response(200, {"bars": {"AAPL": []}, "next_page_token": None})

    with patch.object(alpaca_data.requests, "get", side_effect=[rate_limited, success]), patch.object(
        alpaca_data.time, "sleep"
    ):
        result = alpaca_data.get_bars(["AAPL"], period="1mo", interval="1d")

    assert list(result.keys()) == ["AAPL"]
    assert result["AAPL"].empty


def test_get_bars_raises_a_clear_value_error_on_non_429_http_error(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(401, {}, text="unauthorized")):
        with pytest.raises(ValueError, match="401"):
            alpaca_data.get_bars(["AAPL"], period="1mo", interval="1d")


def test_get_bars_raises_without_credentials_configured(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="ALPACA_API_KEY_ID"):
        alpaca_data.get_bars(["AAPL"], period="1mo", interval="1d")


def test_get_bars_single_unwraps_the_dict_for_one_symbol(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    payload = {
        "bars": {"MSFT": [{"t": "2026-08-28T14:30:00Z", "o": 5, "h": 6, "l": 4, "c": 5.5, "v": 10}]},
        "next_page_token": None,
    }
    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(200, payload)):
        frame = alpaca_data.get_bars_single("msft", period="9mo", interval="1d")

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame["Close"].iloc[0] == 5.5


def test_get_bars_single_returns_empty_frame_for_a_symbol_with_no_data(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")

    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(200, {"bars": {}, "next_page_token": None})):
        frame = alpaca_data.get_bars_single("ZZZZ", period="9mo", interval="1d")

    assert frame.empty
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_unsupported_period_raises_immediately_not_a_bad_request_to_alpaca(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get") as mock_get:
        with pytest.raises(ValueError, match="Unsupported period"):
            alpaca_data.get_bars(["AAPL"], period="3y", interval="1d")
    mock_get.assert_not_called()


def test_unsupported_interval_raises_immediately_not_a_bad_request_to_alpaca(monkeypatch):
    # "1h" was the original example here, but it's a genuinely supported
    # interval as of 2026-09-04 (added alongside the dashboard chart's own
    # interval selector - see _INTERVAL_TO_ALPACA_TIMEFRAME) - "3h" is not
    # in that allowlist and still exercises the same fail-closed behavior.
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get") as mock_get:
        with pytest.raises(ValueError, match="Unsupported interval"):
            alpaca_data.get_bars(["AAPL"], period="1mo", interval="3h")
    mock_get.assert_not_called()


# --- get_latest_trade_price -------------------------------------------------

"""Found live 2026-08-28: an entry's ideal_entry/limit_price is computed
from get_bars' up-to-15-minutes-stale chart data at scan time, but the
order can be submitted to Webull minutes later - for a fast-moving
momentum candidate, that gap was enough real price drift to trip Webull's
own OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE ("the order price is too
deviated") rejection. get_latest_trade_price exists to catch this BEFORE
submission with a genuinely real-time price (feed=iex is real-time, NOT
the same 15-min-embargoed data get_bars returns)."""


def test_get_latest_trade_price_returns_the_price(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    payload = {"symbol": "AAPL", "trade": {"t": "2026-08-28T17:00:00Z", "p": 191.42, "s": 100}}
    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(200, payload)) as mock_get:
        price = alpaca_data.get_latest_trade_price("aapl")

    assert price == 191.42
    called = mock_get.call_args
    assert called.args[0] == "https://data.alpaca.markets/v2/stocks/AAPL/trades/latest"
    assert called.kwargs["params"] == {"feed": "iex"}
    assert called.kwargs["headers"]["APCA-API-KEY-ID"] == "key123"


def test_get_latest_trade_price_returns_none_on_a_non_200_response(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(429, {})):
        assert alpaca_data.get_latest_trade_price("AAPL") is None


def test_get_latest_trade_price_returns_none_on_a_malformed_response(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get", return_value=_fake_response(200, {"symbol": "AAPL"})):
        assert alpaca_data.get_latest_trade_price("AAPL") is None


def test_get_latest_trade_price_returns_none_on_a_request_exception(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get", side_effect=alpaca_data.requests.exceptions.ConnectionError("refused")):
        assert alpaca_data.get_latest_trade_price("AAPL") is None


def test_get_latest_trade_price_returns_none_without_credentials_configured(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    # Unlike get_bars (which raises so a scan's own error list surfaces the
    # cause), this fails closed silently by design - a caller here is about
    # to size/submit a real order and must treat "couldn't confirm" as
    # "skip," not crash the whole entry-submission loop over one ticker.
    assert alpaca_data.get_latest_trade_price("AAPL") is None


def test_get_latest_trade_price_returns_none_for_an_empty_symbol(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret456")
    with patch.object(alpaca_data.requests, "get") as mock_get:
        assert alpaca_data.get_latest_trade_price("") is None
    mock_get.assert_not_called()
