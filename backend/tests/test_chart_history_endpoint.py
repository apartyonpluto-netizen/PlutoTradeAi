from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd

import auth
import app as pluto_app

"""GET /api/chart-history/<ticker> and get_chart_history_for_ticker
(2026-09-04) - raw OHLC candles for the real dashboard chart, sourced from
the same alpaca_data.get_bars_single call charting_brain.py's
build_chart_levels already uses internally. Mocks alpaca_data.get_bars_single
directly, the same level test_chart-level-adjacent code would mock at."""


def _bars_df(rows, freq="D") -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close", "Volume"])


def test_get_chart_history_for_ticker_returns_real_candles():
    df = _bars_df(
        [
            [100.0, 101.0, 99.0, 100.5, 1_000_000],
            [100.5, 102.0, 100.0, 101.5, 1_200_000],
        ]
    )
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df):
        payload = pluto_app.get_chart_history_for_ticker("spy")
    assert payload["ticker"] == "SPY"
    assert payload["error"] == ""
    assert len(payload["candles"]) == 2
    assert payload["candles"][0]["close"] == 100.5
    assert payload["candles"][1]["high"] == 102.0
    assert payload["candles"][0]["date"] == "2026-08-01"


def test_get_chart_history_for_ticker_drops_nan_close_rows_not_send_invalid_json():
    df = _bars_df(
        [
            [100.0, 101.0, 99.0, 100.5, 1_000_000],
            [100.5, 102.0, 100.0, float("nan"), 1_200_000],
        ]
    )
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df):
        payload = pluto_app.get_chart_history_for_ticker("SPY")
    assert len(payload["candles"]) == 1
    for candle in payload["candles"]:
        assert not math.isnan(candle["close"])


def test_get_chart_history_for_ticker_empty_dataframe_reports_error_not_crash():
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=pd.DataFrame()):
        payload = pluto_app.get_chart_history_for_ticker("ZZZZ")
    assert payload["candles"] == []
    assert payload["error"]


def test_get_chart_history_for_ticker_broker_failure_reports_error_not_crash():
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", side_effect=RuntimeError("rate limited")):
        payload = pluto_app.get_chart_history_for_ticker("SPY")
    assert payload["candles"] == []
    assert "rate limited" in payload["error"]


def test_get_chart_history_for_ticker_caches_and_does_not_refetch(user_id):
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY")
        pluto_app.get_chart_history_for_ticker("SPY")
    mock_fetch.assert_called_once()


def test_chart_history_route_returns_real_payload(user_id):
    real_user = auth.register_user(f"charthistory-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(real_user["id"])
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = real_user["id"]
            response = client.get("/api/chart-history/SPY")
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ticker"] == "SPY"
    assert len(payload["candles"]) == 1


# --- ?interval= (2026-09-04, refined same-day from a coarser "range"
# concept to real per-candle granularity per direct feedback: "from 1min
# to daily... the hourly, 15min, 30min, 10min and even the 5min one") ----


def test_default_interval_is_1d_and_matches_original_behavior():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        payload = pluto_app.get_chart_history_for_ticker("SPY")
    assert payload["interval"] == "1d"
    mock_fetch.assert_called_once_with("SPY", period="9mo", interval="1d")
    assert payload["candles"][0]["date"] == "2026-08-01"  # bare date, not a timestamp


def test_1m_interval_requests_one_day_lookback_of_1min_bars():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="1min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        payload = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="1m")
    assert payload["interval"] == "1m"
    mock_fetch.assert_called_once_with("SPY", period="1d", interval="1m")
    # Intraday candles carry a real timestamp, not just a bare date, so
    # multiple same-day bars plot as distinct points.
    assert "T" in payload["candles"][0]["date"]


def test_5m_interval_requests_five_day_lookback():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="5min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY", chart_interval="5m")
    mock_fetch.assert_called_once_with("SPY", period="5d", interval="5m")


def test_10m_interval_requests_five_day_lookback():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="10min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY", chart_interval="10m")
    mock_fetch.assert_called_once_with("SPY", period="5d", interval="10m")


def test_15m_interval_requests_one_month_lookback():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="15min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY", chart_interval="15m")
    mock_fetch.assert_called_once_with("SPY", period="1mo", interval="15m")


def test_30m_interval_requests_one_month_lookback():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="30min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY", chart_interval="30m")
    mock_fetch.assert_called_once_with("SPY", period="1mo", interval="30m")


def test_1h_interval_requests_nine_month_lookback():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="h")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        pluto_app.get_chart_history_for_ticker("SPY", chart_interval="1h")
    mock_fetch.assert_called_once_with("SPY", period="9mo", interval="1h")


def test_interval_is_never_uppercased_1m_and_1mo_style_month_are_distinct():
    """A stray uppercase "M" must never be silently treated as lowercase
    "m" (minute) or vice versa - the old range-based design used "1M" to
    mean one MONTH of daily bars; the real interval "1m" means one-MINUTE
    bars. Passing the old uppercase spelling must fall back to the
    default, never be coerced into the minute interval."""
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        payload = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="1M")
    assert payload["interval"] == "1d"  # fell back to the default, not "1m"
    mock_fetch.assert_called_once_with("SPY", period="9mo", interval="1d")


def test_unrecognized_interval_falls_back_to_the_default_rather_than_erroring():
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        payload = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="3y")
    assert payload["interval"] == "1d"
    mock_fetch.assert_called_once_with("SPY", period="9mo", interval="1d")


def test_different_intervals_for_the_same_ticker_are_cached_independently():
    daily_df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]])
    intraday_df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="5min")
    pluto_app.CHART_HISTORY_CACHE.clear()

    def _fake_fetch(ticker, period, interval):
        return intraday_df if interval == "5m" else daily_df

    with patch.object(pluto_app.alpaca_data, "get_bars_single", side_effect=_fake_fetch) as mock_fetch:
        daily = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="1d")
        five_min = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="5m")
        # Re-requesting either interval again must hit the cache, not
        # refetch - a shared cache key across intervals would otherwise
        # serve the wrong interval's candles.
        daily_again = pluto_app.get_chart_history_for_ticker("SPY", chart_interval="1d")

    assert mock_fetch.call_count == 2
    assert daily["interval"] == "1d"
    assert five_min["interval"] == "5m"
    assert daily_again == daily


def test_chart_history_route_accepts_interval_query_param(user_id):
    real_user = auth.register_user(f"charthistoryinterval-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(real_user["id"])
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000_000]], freq="15min")
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = real_user["id"]
            response = client.get("/api/chart-history/SPY?interval=15m")
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["interval"] == "15m"
    mock_fetch.assert_called_once_with("SPY", period="1mo", interval="15m")
