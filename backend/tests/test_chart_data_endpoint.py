"""GET /api/chart-data/<ticker> — cached Alpaca candles plus local trade marks."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

import auth
import app as pluto_app
from chart_data import build_chart_payload, collect_trade_marks


def _bars_df(rows) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close", "Volume"])


def _login(client, user_id: str) -> None:
    real_user = auth.register_user(f"chartdata-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(real_user["id"])
    with client.session_transaction() as sess:
        sess["user_id"] = real_user["id"]


def test_collect_trade_marks_uses_entry_exit_stop_and_target_and_skips_missing():
    marks = collect_trade_marks(
        "aapl",
        closed_trades=[
            {
                "ticker": "AAPL",
                "average_entry_price": 180.25,
                "average_exit_price": 188.5,
                "exit_timestamp": "2026-08-04T15:00:00+00:00",
                "logged_at": "2026-08-01T14:30:00+00:00",
                "stop": 176.0,
                "target": 190.0,
            },
            {"ticker": "MSFT", "average_entry_price": 400, "average_exit_price": 410, "stop": 390, "target": 420},
        ],
        overnight_orders=[
            {"ticker": "AAPL", "status": "skipped", "side": "BUY", "limit_price": 1, "stop": 1, "target": 2},
            {"ticker": "AAPL", "status": "placed", "side": "BUY", "limit_price": 181, "stop": 176.0, "target": 191.5},
        ],
        paper_trades=[{"ticker": "AAPL", "entry_price": 179.5, "exit_price": None, "opened_at": "2026-08-02"}],
    )
    kinds = {(mark["kind"], mark["price"]) for mark in marks}
    assert ("entry", 180.25) in kinds
    assert ("exit", 188.5) in kinds
    assert ("stop", 176.0) in kinds
    assert ("target", 190.0) in kinds
    assert ("target", 191.5) in kinds
    assert ("entry", 179.5) in kinds
    assert all(mark["kind"] != "exit" or mark["price"] != 0 for mark in marks)
    assert all(item[0] != "entry" or item[1] != 400 for item in kinds)
    assert all(mark["price"] != 1 for mark in marks)


def test_collect_trade_marks_reads_stop_and_target_from_trade_plan_numbers():
    marks = collect_trade_marks(
        "NVDA",
        overnight_orders=[
            {
                "ticker": "NVDA",
                "status": "placed",
                "side": "BUY",
                "limit_price": 120,
                "logged_at": "2026-09-01T13:00:00Z",
                "trade_plan": {"numbers": {"stop_price": 114.5, "target_price": 132}},
            }
        ],
    )
    by_kind = {mark["kind"]: mark for mark in marks}
    assert by_kind["entry"]["price"] == 120
    assert by_kind["stop"]["price"] == 114.5
    assert by_kind["target"]["price"] == 132
    assert by_kind["entry"]["time"] == "2026-09-01"


def test_sell_orders_mark_exit_not_entry():
    marks = collect_trade_marks(
        "TSLA",
        overnight_orders=[{"ticker": "TSLA", "status": "placed", "side": "SELL", "limit_price": 250.5, "logged_at": "2026-08-10"}],
    )
    assert [mark["kind"] for mark in marks] == ["exit"]
    assert marks[0]["price"] == 250.5


def test_build_chart_payload_rejects_bad_ticker_without_calling_history():
    def explode(_ticker, _interval):
        raise AssertionError("history should not be loaded")

    try:
        build_chart_payload("not a ticker", "1d", explode)
    except ValueError as error:
        assert "Ticker" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_chart_data_route_returns_candles_and_marks(user_id):
    df = _bars_df([[100.0, 102.0, 99.0, 101.25, 1_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    closed = [{"ticker": "SPY", "average_entry_price": 100.5, "average_exit_price": 103.0, "stop": 98.0, "target": 106.0, "exit_timestamp": "2026-08-02"}]
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch, \
         patch.object(pluto_app, "list_closed_trades", return_value=closed), \
         patch.object(pluto_app, "list_overnight_orders", return_value=[]), \
         patch.object(pluto_app, "list_paper_trades", return_value=[]):
        with pluto_app.app.test_client() as client:
            _login(client, user_id)
            response = client.get("/api/chart-data/spy?interval=1d")
            again = client.get("/api/chart-data/SPY?interval=1d")
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ticker"] == "SPY"
    assert payload["candles"][0]["close"] == 101.25
    kinds = {mark["kind"] for mark in payload["marks"]}
    assert kinds == {"entry", "exit", "stop", "target"}
    assert again.status_code == 200
    mock_fetch.assert_called_once()


def test_chart_data_route_reuses_chart_history_cache(user_id):
    df = _bars_df([[10.0, 11.0, 9.5, 10.5, 100]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as mock_fetch, \
         patch.object(pluto_app, "list_closed_trades", return_value=[]), \
         patch.object(pluto_app, "list_overnight_orders", return_value=[]), \
         patch.object(pluto_app, "list_paper_trades", return_value=[]):
        with pluto_app.app.test_client() as client:
            _login(client, user_id)
            history = client.get("/api/chart-history/AAPL")
            chart = client.get("/api/chart-data/AAPL")
    assert history.status_code == 200
    assert chart.status_code == 200
    assert chart.get_json()["data"]["candles"] == history.get_json()["data"]["candles"]
    mock_fetch.assert_called_once()


def test_chart_data_route_requires_login():
    with pluto_app.app.test_client() as client:
        response = client.get("/api/chart-data/AAPL")
    assert response.status_code == 401


def test_chart_data_route_rejects_bad_ticker(user_id):
    with pluto_app.app.test_client() as client:
        _login(client, user_id)
        response = client.get("/api/chart-data/$$$")
    assert response.status_code == 400
