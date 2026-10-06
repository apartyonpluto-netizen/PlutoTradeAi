from __future__ import annotations

from unittest.mock import patch

import pandas as pd

import market_scanner


def _frame(prices, volumes):
    return pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": volumes},
        index=pd.date_range("2026-08-01", periods=len(prices), freq="D", tz="UTC"),
    )


def _snap(price, prev_close, prev_volume):
    return {
        "latestTrade": {"p": price},
        "prevDailyBar": {"c": prev_close, "v": prev_volume},
        "dailyBar": {"c": price, "v": prev_volume},
    }


def test_nasdaq_scan_scores_only_liquid_survivors_and_explains_them(monkeypatch):
    monkeypatch.setenv("PLUTO_SCANNER_TOP_N", "2")
    monkeypatch.setenv("PLUTO_SCANNER_SNAPSHOT_GAP_SECONDS", "0")
    snapshots = {
        "LIQD": _snap(20, 19, 2_000_000),  # larger percent move
        "WIDE": _snap(50, 48, 1_000_000),
        "CHEAP": _snap(2, 2, 50_000_000),
        "THIN": _snap(40, 40, 1_000),
    }
    daily = {
        "LIQD": _frame([19.0] * 5, [2_000_000] * 5),
        "WIDE": _frame([48.0] * 5, [1_000_000] * 5),
    }
    intraday = {
        "LIQD": _frame([20.0], [100_000]),
        "WIDE": _frame([50.0], [100_000]),
    }

    def _bars(symbols, period, interval):
        assert set(symbols) == {"LIQD", "WIDE"}
        return daily if interval == "1d" else intraday

    with patch.object(market_scanner.nasdaq_universe, "load_symbols", return_value=(["CHEAP", "THIN", "WIDE", "LIQD"], "nasdaq")), \
         patch.object(market_scanner.alpaca_data, "get_snapshots", return_value=snapshots) as mock_snapshots, \
         patch.object(market_scanner.alpaca_data, "get_bars", side_effect=_bars):
        rows, errors, _stamp = market_scanner.scan_market(tickers=None, force_refresh=True)

    mock_snapshots.assert_called_once()
    assert errors == []
    assert {row["ticker"] for row in rows} == {"LIQD", "WIDE"}
    assert all(row["qualification_reason"] for row in rows)
    assert any("dollar volume" in row["qualification_reason"] for row in rows)
    assert rows.summary["universe_count"] == 4
    assert rows.summary["liquid_count"] == 2
    assert rows.summary["shown_count"] == 2
    assert rows.summary["top_n"] == 2


def test_nasdaq_scan_reuses_a_fresh_disk_cache(monkeypatch):
    monkeypatch.setenv("PLUTO_SCANNER_TOP_N", "48")
    snapshots = {"AAPL": _snap(100, 99, 1_000_000)}
    daily = {"AAPL": _frame([99.0] * 5, [1_000_000] * 5)}
    intraday = {"AAPL": _frame([100.0], [10_000])}

    def _bars(symbols, period, interval):
        return daily if interval == "1d" else intraday

    with patch.object(market_scanner.nasdaq_universe, "load_symbols", return_value=(["AAPL"], "nasdaq")), \
         patch.object(market_scanner.alpaca_data, "get_snapshots", return_value=snapshots) as mock_snapshots, \
         patch.object(market_scanner.alpaca_data, "get_bars", side_effect=_bars):
        first, _errors, _stamp = market_scanner.scan_market(tickers=None, force_refresh=True)
        second, _errors2, _stamp2 = market_scanner.scan_market(tickers=None, watchlist_tickers=["AAPL"], force_refresh=False)

    assert mock_snapshots.call_count == 1
    assert first[0]["ticker"] == "AAPL"
    assert second[0]["on_watchlist"] is True
    assert "qualification_reason" in second[0]


def test_explicit_ticker_list_does_not_use_snapshots():
    with patch.object(market_scanner.alpaca_data, "get_bars", return_value={}) as mock_bars, \
         patch.object(market_scanner.alpaca_data, "get_snapshots") as mock_snapshots:
        market_scanner.scan_market(tickers=["AAPL"])
    mock_bars.assert_called()
    mock_snapshots.assert_not_called()
