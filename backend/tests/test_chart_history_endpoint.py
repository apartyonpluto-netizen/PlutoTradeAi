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


def _bars_df(rows) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=len(rows), freq="D")
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
