from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
from watchlist import add_stock

"""Found while investigating the recurring OOM crashes: _build_page_context
unconditionally fetched a full options chain (the heaviest, most
rate-limit-risky call in the whole function - several real Yahoo requests
per ticker) for every caller, including the unattended autonomous scan,
which runs every ~5 minutes and never reads the options fields it produces
(options_expirations/expected_move) - candidate selection and sizing come
entirely from strategy/chart data instead. include_options=False lets a
caller skip that fetch; these tests prove it's actually skipped, not just
documented as skipped."""


def _fake_strategy(ticker):
    return {
        "strategy_confidence": 80,
        "recommendation": "CALL",
        "best_strategy": "Trend Continuation",
        "why_this_strategy_fits": "test thesis",
    }


def _fake_chart(ticker):
    return {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]}


def _run_build_page_context(user_id, *, include_options):
    add_stock(user_id, {"ticker": "AAPL"})
    with patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "get_options_data_for_ticker") as mock_options, \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        mock_options.return_value = {"expiration_suggestions": ["2099-01-01"], "expected_move": "±5%"}
        context = pluto_app._build_page_context(include_options=include_options)
    return context, mock_options


def test_include_options_false_never_calls_the_options_fetch(user_id):
    context, mock_options = _run_build_page_context(user_id, include_options=False)
    mock_options.assert_not_called()
    # The candidate itself must still be fully populated from strategy/chart -
    # skipping options must not silently break candidate discovery.
    assert len(context["upcoming_opportunities"]) == 1
    opportunity = context["upcoming_opportunities"][0]
    assert opportunity["ticker"] == "AAPL"
    assert opportunity["confidence"] == 80
    assert opportunity["recommendation"] == "CALL"
    # Options fields degrade to their documented "unavailable" defaults,
    # never silently omitted or crashing.
    assert opportunity["expected_move"] == "Data unavailable"
    assert opportunity["options_expirations"]["aggressive"] == "Data unavailable"


def test_include_options_true_still_calls_the_options_fetch_and_populates_it(user_id):
    context, mock_options = _run_build_page_context(user_id, include_options=True)
    mock_options.assert_called_once()
    opportunity = context["upcoming_opportunities"][0]
    assert opportunity["expected_move"] == "±5%"
    assert opportunity["options_expirations"]["aggressive"] == "2099-01-01"


def test_include_opportunities_false_skips_the_whole_pipeline_including_options(user_id):
    context, mock_options = _run_build_page_context(user_id, include_options=True)
    # Sanity check on the fixture itself - AAPL should have produced a
    # candidate with the default include_opportunities=True.
    assert context["upcoming_opportunities"]

    add_stock(user_id, {"ticker": "MSFT"})
    with patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "get_options_data_for_ticker") as mock_options_off, \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        context_off = pluto_app._build_page_context(include_opportunities=False)
    mock_options_off.assert_not_called()
    assert context_off["upcoming_opportunities"] == []
