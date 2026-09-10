from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
from watchlist import add_stock

"""_build_page_context must not present a CALL/PUT opportunity as tradeable
when its chart breakout/breakdown levels didn't produce a usable
protective structure (a level came back 0, or the levels are on the wrong
side of entry). This is the upstream half of the 2026-09-04 orphan-cascade
fix: _run_autonomous_trade_scan_locked already rejects such a candidate
before any broker call (skip_category="unprotectable_levels"), but it
should never have looked actionable on the dashboard / mission queue
either. The ticker stays visible with its real confidence - just shown as
WAIT until real levels exist."""


def _strategy(recommendation="CALL", confidence=80):
    return {
        "strategy_confidence": confidence,
        "recommendation": recommendation,
        "best_strategy": "Trend Continuation",
        "why_this_strategy_fits": "clean setup",
    }


def _run(user_id, strategy, chart):
    add_stock(user_id, {"ticker": "AAPL"})
    with patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: strategy), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: chart), \
         patch.object(pluto_app, "get_options_data_for_ticker", return_value={}), \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        context = pluto_app._build_page_context(include_options=False)
    opps = context["upcoming_opportunities"]
    assert len(opps) == 1
    return opps[0], context["mission_queue"]


def test_a_call_with_clean_levels_stays_actionable(user_id):
    opp, queue = _run(
        user_id, _strategy("CALL"),
        {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]},
    )
    assert opp["recommendation"] == "CALL"
    assert opp["levels_unavailable"] is False
    assert opp["stop"] > 0 and opp["target"] > 0 and opp["ideal_entry"] > 0
    assert queue[0]["recommendation"] == "CALL"


def test_a_call_with_a_zero_breakdown_level_is_downgraded_to_wait(user_id):
    # breakdown_level 0 -> the CALL stop (breakdown * 0.997) zeroes out.
    opp, queue = _run(
        user_id, _strategy("CALL", confidence=88),
        {"breakout_level": 110.0, "breakdown_level": 0.0},
    )
    assert opp["recommendation"] == "WAIT"
    assert opp["levels_unavailable"] is True
    assert opp["confidence"] == 88  # still visible with its real score
    assert "aren't available" in opp["trade_thesis"]
    assert queue[0]["recommendation"] == "WAIT"


def test_a_call_with_a_zero_breakout_level_is_downgraded(user_id):
    opp, _ = _run(user_id, _strategy("CALL"), {"breakout_level": 0.0, "breakdown_level": 90.0})
    assert opp["recommendation"] == "WAIT"
    assert opp["levels_unavailable"] is True


def test_a_put_with_inverted_levels_is_downgraded(user_id):
    # For a PUT: entry = breakdown*0.999, stop = breakout*1.003, target =
    # breakdown*0.98. If breakout < breakdown the stop lands below entry -
    # not a stop at all.
    opp, _ = _run(user_id, _strategy("PUT"), {"breakout_level": 50.0, "breakdown_level": 100.0})
    assert opp["recommendation"] == "WAIT"
    assert opp["levels_unavailable"] is True


def test_a_put_with_clean_levels_stays_actionable(user_id):
    opp, _ = _run(user_id, _strategy("PUT"), {"breakout_level": 110.0, "breakdown_level": 90.0})
    assert opp["recommendation"] == "PUT"
    assert opp["levels_unavailable"] is False
    assert opp["target"] < opp["ideal_entry"] < opp["stop"]


def test_a_wait_recommendation_with_zero_levels_is_left_alone(user_id):
    # The guard only applies to actionable CALL/PUT calls - a WAIT stays
    # WAIT and is not marked levels_unavailable.
    opp, _ = _run(user_id, _strategy("WAIT"), {"breakout_level": 0.0, "breakdown_level": 0.0})
    assert opp["recommendation"] == "WAIT"
    assert opp["levels_unavailable"] is False
