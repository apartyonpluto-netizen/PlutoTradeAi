from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from brains import charting_brain, extended_hours_brain, strategy_brain

"""Found live 2026-08-28: Yahoo Finance was actively rate-limiting every
request from this app's Render deployment - the direct cause of
candidates_found being 0 on every autonomous scan (see
integrations/alpaca_data.py's module docstring for the full incident
writeup). These three brains/ modules are the other half of the
candidate-discovery critical path alongside market_scanner.py (see
test_market_scanner_alpaca.py) - each now calls Alpaca's Market Data API
instead of yfinance for its per-ticker OHLCV fetch. These tests prove two
things for each module: the correct period/interval get passed through to
alpaca_data, and a flat DataFrame response (the shape alpaca_data.
get_bars_single actually returns) flows all the way through each module's
existing MultiIndex-tolerant normalization to a real, non-"insufficient
data" result - not just that insufficient-data handling still works,
which a shallower test could pass while the real integration was broken."""


def _daily_frame(n: int, start_price: float = 100.0) -> pd.DataFrame:
    prices = start_price + np.cumsum(np.random.default_rng(7).normal(0, 0.5, n))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices + 1.0,
            "Low": prices - 1.0,
            "Close": prices,
            "Volume": [1_000_000] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
    )


def _intraday_frame(n: int, start_price: float = 100.0) -> pd.DataFrame:
    prices = start_price + np.cumsum(np.random.default_rng(11).normal(0, 0.1, n))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices + 0.2,
            "Low": prices - 0.2,
            "Close": prices,
            "Volume": [10_000] * n,
        },
        index=pd.date_range("2026-08-27", periods=n, freq="5min", tz="UTC"),
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


# --- strategy_brain -----------------------------------------------------


def test_strategy_brain_fetch_ohlcv_calls_alpaca_with_correct_period_interval():
    with patch.object(strategy_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()) as mock_get:
        strategy_brain._fetch_ohlcv("aapl")

    assert mock_get.call_count == 2
    first_call, second_call = mock_get.call_args_list
    assert first_call.args == ("AAPL",)
    assert first_call.kwargs == {"period": "9mo", "interval": "1d"}
    assert second_call.kwargs == {"period": "5d", "interval": "5m"}


def test_strategy_brain_degrades_gracefully_on_insufficient_data():
    with patch.object(strategy_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    assert result["insufficient_data"] is True
    assert result["strategy_confidence"] == 0


def test_strategy_brain_produces_a_real_result_from_a_realistic_alpaca_response():
    daily = _daily_frame(120)
    intraday = _intraday_frame(60)

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    assert result.get("insufficient_data") is not True
    assert result["best_strategy"] in strategy_brain.SUPPORTED_STRATEGIES
    assert isinstance(result["strategy_confidence"], int)


# --- charting_brain ------------------------------------------------------


def test_charting_brain_fetch_calls_alpaca_with_correct_period_interval():
    with patch.object(charting_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()) as mock_get:
        charting_brain.build_chart_levels("msft")

    assert mock_get.call_count == 2
    first_call, second_call = mock_get.call_args_list
    assert first_call.kwargs == {"period": "9mo", "interval": "1d"}
    assert second_call.kwargs == {"period": "5d", "interval": "5m"}


def test_charting_brain_degrades_gracefully_on_insufficient_data():
    with patch.object(charting_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()):
        result = charting_brain.build_chart_levels("MSFT")

    assert result["insufficient_data"] is True


def test_charting_brain_produces_a_real_result_from_a_realistic_alpaca_response():
    daily = _daily_frame(120)
    intraday = _intraday_frame(60)

    with patch.object(charting_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]):
        result = charting_brain.build_chart_levels("MSFT")

    assert result.get("insufficient_data") is not True


# --- extended_hours_brain -------------------------------------------------


def test_extended_hours_brain_fetch_calls_alpaca_with_correct_period_interval():
    with patch.object(extended_hours_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()) as mock_get:
        extended_hours_brain.build_extended_hours_intelligence("nvda")

    assert mock_get.call_count == 2
    first_call, second_call = mock_get.call_args_list
    assert first_call.kwargs == {"period": "2d", "interval": "5m"}
    assert second_call.kwargs == {"period": "5d", "interval": "1d"}


def test_extended_hours_brain_degrades_gracefully_when_alpaca_returns_no_data():
    with patch.object(extended_hours_brain.alpaca_data, "get_bars_single", return_value=_empty_frame()):
        result = extended_hours_brain.build_extended_hours_intelligence("NVDA")

    assert result["insufficient_data"] is True


def test_extended_hours_brain_produces_a_real_result_from_a_realistic_alpaca_response():
    intraday = _intraday_frame(60)
    daily = _daily_frame(10)

    with patch.object(extended_hours_brain.alpaca_data, "get_bars_single", side_effect=[intraday, daily]):
        result = extended_hours_brain.build_extended_hours_intelligence("NVDA")

    assert result.get("insufficient_data") is not True
    assert "gap_percent" in result
