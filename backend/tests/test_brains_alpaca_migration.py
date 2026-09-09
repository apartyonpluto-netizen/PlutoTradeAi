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


# --- market_context.current_price: real-time price, not the stale daily
# close (2026-09-04, root-caused via backend/scripts/diag_mstr_ema.py - a
# real candidate whose EMA stack sat wildly far from its own quoted entry
# price, traced back to current_price being the last DAILY bar's close,
# not a fresh quote) ---------------------------------------------------


def test_current_price_uses_the_real_time_quote_when_available():
    daily = _daily_frame(120, start_price=100.0)  # last daily close ~100
    intraday = _intraday_frame(60, start_price=100.0)

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]), \
         patch.object(strategy_brain.alpaca_data, "get_latest_trade_price", return_value=142.50):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    market_context = result["market_context"]
    assert market_context["current_price"] == 142.50
    assert market_context["price_source"] == "realtime"


def test_current_price_falls_back_to_daily_close_when_realtime_returns_none():
    daily = _daily_frame(120)
    intraday = _intraday_frame(60)
    expected_close = round(float(daily["Close"].iloc[-1]), 2)

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]), \
         patch.object(strategy_brain.alpaca_data, "get_latest_trade_price", return_value=None):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    market_context = result["market_context"]
    assert market_context["current_price"] == expected_close
    assert market_context["price_source"] == "daily_close"


def test_current_price_falls_back_to_daily_close_when_realtime_raises():
    daily = _daily_frame(120)
    intraday = _intraday_frame(60)
    expected_close = round(float(daily["Close"].iloc[-1]), 2)

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]), \
         patch.object(strategy_brain.alpaca_data, "get_latest_trade_price", side_effect=RuntimeError("rate limited")):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    market_context = result["market_context"]
    assert market_context["current_price"] == expected_close
    assert market_context["price_source"] == "daily_close"


def test_current_price_falls_back_when_realtime_is_zero_or_negative():
    """A zero/negative price is never a valid real-time quote - treated
    the same as "couldn't get one", not blindly trusted."""
    daily = _daily_frame(120)
    intraday = _intraday_frame(60)
    expected_close = round(float(daily["Close"].iloc[-1]), 2)

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]), \
         patch.object(strategy_brain.alpaca_data, "get_latest_trade_price", return_value=0.0):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    assert result["market_context"]["current_price"] == expected_close
    assert result["market_context"]["price_source"] == "daily_close"


def test_backtest_mode_never_calls_get_latest_trade_price():
    """Critical look-ahead-bias guard: backtest_engine.py evaluates a PAST
    day using a historically-sliced `daily` frame - substituting TODAY's
    real-time price into that evaluation would leak future information
    into a historical decision, not fix anything."""
    daily = _daily_frame(120)
    empty_intraday = _empty_frame()

    with patch.object(strategy_brain.alpaca_data, "get_latest_trade_price") as mock_realtime:
        result = strategy_brain.build_strategy_intelligence(
            "AAPL", daily=daily, intraday=empty_intraday, backtest_mode=True
        )

    mock_realtime.assert_not_called()
    assert result["market_context"]["price_source"] == "daily_close"


def test_day_change_percent_reflects_the_realtime_price_not_the_stale_one():
    """The whole point: every downstream signal reads current_price, so a
    fresher price must actually change what's computed from it, not just
    display differently."""
    daily = _daily_frame(120, start_price=100.0)
    intraday = _intraday_frame(60, start_price=100.0)
    previous_close = float(daily["Close"].iloc[-2])

    with patch.object(strategy_brain.alpaca_data, "get_bars_single", side_effect=[daily, intraday]), \
         patch.object(strategy_brain.alpaca_data, "get_latest_trade_price", return_value=142.50):
        result = strategy_brain.build_strategy_intelligence("AAPL")

    expected_day_change = round((142.50 - previous_close) / previous_close * 100, 2)
    assert result["market_context"]["day_change_percent"] == expected_day_change


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
