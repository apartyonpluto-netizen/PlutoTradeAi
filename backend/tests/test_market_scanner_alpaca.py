from __future__ import annotations

from unittest.mock import patch

import pandas as pd

import market_scanner

"""Found live 2026-08-28: Yahoo Finance was actively rate-limiting every
request from this app's Render deployment ("Scanner timed out - Yahoo
Finance is rate limiting.", 0 rows, every single scan) - the direct cause
of candidates_found being 0 on every autonomous scan (see
integrations/alpaca_data.py's module docstring for the full incident
writeup). market_scanner.py now calls Alpaca's Market Data API instead of
yfinance. These tests replace test_market_scanner_thread_bound.py, whose
entire premise (asserting a bounded `threads` kwarg on a yf.download call)
no longer applies - market_scanner.py doesn't import yfinance at all
anymore."""


def _frame(prices, volumes, freq="D"):
    return pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": volumes},
        index=pd.date_range("2026-08-01", periods=len(prices), freq=freq, tz="UTC"),
    )


def test_scan_market_calls_alpaca_with_the_right_periods_and_intervals():
    with patch.object(market_scanner.alpaca_data, "get_bars", return_value={}) as mock_get_bars:
        market_scanner.scan_market(tickers=["AAPL", "MSFT"])

    assert mock_get_bars.call_count == 2
    first_call, second_call = mock_get_bars.call_args_list
    assert first_call.kwargs == {"period": "1mo", "interval": "1d"}
    assert second_call.kwargs == {"period": "1d", "interval": "5m"}
    assert first_call.args[0] == ["AAPL", "MSFT"]


def test_scan_market_builds_a_row_from_alpaca_bars():
    daily_prices = [100.0] * 19 + [110.0]  # previous close 100, current 110 via intraday below
    daily = {"AAPL": _frame(daily_prices, [1_000_000] * 20)}
    intraday = {"AAPL": _frame([111.0, 112.0], [50_000, 60_000], freq="5min")}

    with patch.object(market_scanner.alpaca_data, "get_bars", side_effect=[daily, intraday]):
        results, errors, _ = market_scanner.scan_market(tickers=["AAPL"])

    assert errors == []
    assert len(results) == 1
    row = results[0]
    assert row["ticker"] == "AAPL"
    assert row["price"] == 112.0  # latest intraday close wins over daily
    assert row["volume"] == 110_000  # summed intraday volume
    assert row["percent_change"] > 0
    assert row["relative_volume"] > 0
    assert 1 <= row["scanner_score"] <= 99
    assert row["status"] in {"Hot", "Watch", "Quiet"}


def test_scan_market_records_an_error_for_a_ticker_alpaca_has_no_data_for():
    with patch.object(market_scanner.alpaca_data, "get_bars", side_effect=[{}, {}]):
        results, errors, _ = market_scanner.scan_market(tickers=["ZZZZ"])

    assert results == []
    assert errors == ["ZZZZ: no market data returned."]


def test_scan_market_reports_a_clear_error_when_alpaca_get_bars_raises():
    with patch.object(market_scanner.alpaca_data, "get_bars", side_effect=ValueError("ALPACA_API_KEY_ID not configured")):
        results, errors, _ = market_scanner.scan_market(tickers=["AAPL"])

    assert results == []
    assert len(errors) == 1
    assert "Scanner fetch failed" in errors[0]
    assert "ALPACA_API_KEY_ID" in errors[0]


def test_scan_market_marks_on_watchlist_tickers_correctly():
    daily = {"AAPL": _frame([100.0] * 5, [1000] * 5)}
    with patch.object(market_scanner.alpaca_data, "get_bars", side_effect=[daily, {}]):
        results, _errors, _ = market_scanner.scan_market(tickers=["AAPL"], watchlist_tickers=["aapl"])

    assert results[0]["on_watchlist"] is True
