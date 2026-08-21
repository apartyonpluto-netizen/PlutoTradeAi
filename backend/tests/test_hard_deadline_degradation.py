from __future__ import annotations

import time
from unittest.mock import patch

import app as pluto_app
from watchlist import add_stock

"""Found live in production 2026-08-21: even after tightening every
yf.download() call's own timeout= kwarg, gunicorn's 90s worker timeout still
fired during a sustained Yahoo rate-limit storm and SIGKILLed workers. Root
cause: yfinance's own retry-on-429 logic (venv yfinance/data.py's
_make_request) does an UNCONDITIONAL cookie/crumb refetch plus one more
request attempt on any 4xx response, each a full network round-trip up to
the timeout= kwarg - under sustained rate limiting a single logical call can
cost ~3x its nominal timeout, and up to 6 of these chain sequentially per
ticker thread. Tuning timeout= alone can't bound that cascade.
_run_with_hard_deadline and the futures_wait()-based ticker-intelligence/
options stages give up on a stuck fetch instead: these tests prove a slow
ticker is dropped (not waited on) while a fast one still comes through, and
that the overall call returns promptly rather than blocking for the full
hang."""


def test_run_with_hard_deadline_returns_default_when_func_exceeds_deadline():
    def _slow():
        time.sleep(2)
        return "too late"

    start = time.monotonic()
    result = pluto_app._run_with_hard_deadline(_slow, deadline_seconds=0.2, default="gave up")
    elapsed = time.monotonic() - start

    assert result == "gave up"
    assert elapsed < 1.0


def test_run_with_hard_deadline_returns_real_result_when_func_is_fast():
    result = pluto_app._run_with_hard_deadline(lambda: "on time", deadline_seconds=2, default="gave up")
    assert result == "on time"


def _fake_strategy(ticker):
    return {
        "strategy_confidence": 80,
        "recommendation": "CALL",
        "best_strategy": "Trend Continuation",
        "why_this_strategy_fits": "test thesis",
    }


def _fake_chart(ticker):
    return {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]}


def test_a_stuck_ticker_is_dropped_instead_of_blocking_the_whole_page(user_id):
    add_stock(user_id, {"ticker": "AAPL"})
    add_stock(user_id, {"ticker": "MSFT"})

    def _extended_hours(ticker):
        if ticker == "MSFT":
            time.sleep(2)
        return {}

    with patch.object(pluto_app, "TICKER_INTELLIGENCE_DEADLINE_SECONDS", 0.3), \
         patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", side_effect=_extended_hours), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        start = time.monotonic()
        context = pluto_app._build_page_context(include_options=False)
        elapsed = time.monotonic() - start

    # Real bound proven, not just documented: this must not block for
    # anywhere near MSFT's 2s hang.
    assert elapsed < 1.5

    tickers_seen = {row["ticker"] for row in context["upcoming_opportunities"]}
    assert "AAPL" in tickers_seen
    assert "MSFT" not in tickers_seen


def test_options_fetch_deadline_drops_a_stuck_ticker_but_keeps_a_fast_one(user_id):
    add_stock(user_id, {"ticker": "AAPL"})
    add_stock(user_id, {"ticker": "MSFT"})

    def _options(ticker, force_refresh=False):
        if ticker == "MSFT":
            time.sleep(2)
            return {"expiration_suggestions": ["2099-01-01"], "expected_move": "±5%"}
        return {"expiration_suggestions": ["2099-02-01"], "expected_move": "±2%"}

    with patch.object(pluto_app, "OPTIONS_FETCH_DEADLINE_SECONDS", 0.3), \
         patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "get_options_data_for_ticker", side_effect=_options), \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        start = time.monotonic()
        context = pluto_app._build_page_context(include_options=True)
        elapsed = time.monotonic() - start

    assert elapsed < 1.5

    opportunities_by_ticker = {row["ticker"]: row for row in context["upcoming_opportunities"]}
    assert opportunities_by_ticker["AAPL"]["expected_move"] == "±2%"
    assert opportunities_by_ticker["MSFT"]["expected_move"] == "Data unavailable"
