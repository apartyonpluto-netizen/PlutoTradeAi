from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol

"""Root-cause guard for the 2026-09-04 orphan cascade. Found live in
production: five autonomous entries (MU, PLTR, COIN, SLB, ADBE) went to
the broker with stop=0 / target=0 - their chart breakout/breakdown levels
had come back 0 from get_chart_levels_for_ticker, so the derived
stop/target zeroed out. The fills leaked through an ambiguous broker
response, the protective legs had no price to attach to, and all five
ended up as naked "orphan" positions the monitor then had to hunt down
and reconcile - which blocked every new autonomous entry for ~5 days.

_run_autonomous_trade_scan_locked now rejects any candidate without
usable protective levels (a level <= 0, OR levels on the wrong side of
entry for the direction) BEFORE any broker call, tagging it
skip_category="unprotectable_levels". An unprotectable order must never
leave the building."""

CREDS = {"app_key": "key", "app_secret": "secret"}
CASH_ACCOUNT_ID = "acct-cash-1"


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"fake-cid-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _run_scan(opportunities, user_id, *, dry_run=False):
    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
        stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
        stack.enter_context(patch.object(pluto_app, "get_anthropic_api_key", return_value=""))
        stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": CASH_ACCOUNT_ID}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": CASH_ACCOUNT_ID}))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        # Matches each candidate's own ideal_entry so the pre-submission
        # freshness/drift gate (see test_entry_price_drift_gate.py) never
        # trips for a candidate this test expects to actually place.
        stack.enter_context(patch.object(pluto_app.alpaca_data, "get_latest_trade_price", side_effect=lambda t: 100.0))
        stack.enter_context(patch.object(
            pluto_app.webull_api, "get_account_balance",
            return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
        ))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": None, "source_time": None, "fetch_time": None, "age_seconds": None,
            "status": "unavailable", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        mock_submit = stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry))
        mock_option = stack.enter_context(patch.object(pluto_app, "select_option_contract", return_value=None))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=dry_run)
    return result, mock_submit, mock_option


def _call(ticker, *, ideal_entry=100.0, stop=95.0, target=110.0, confidence=82):
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": confidence,
        "ideal_entry": ideal_entry,
        "stop": stop,
        "target": target,
        "strategy": "Breakout",
        "trade_quality": "A",
    }


def _put(ticker, *, ideal_entry=100.0, stop=105.0, target=90.0, confidence=82):
    return {
        "ticker": ticker,
        "recommendation": "PUT",
        "confidence": confidence,
        "ideal_entry": ideal_entry,
        "stop": stop,
        "target": target,
        "strategy": "Mean Reversion",
        "trade_quality": "A",
    }


def _skipped(result, ticker):
    matches = [e for e in result["skipped"] if e.get("ticker") == ticker]
    assert len(matches) == 1, f"expected one skipped record for {ticker}, got {len(matches)}"
    return matches[0]


def test_a_zero_stop_candidate_is_rejected_before_any_broker_call(user_id):
    result, mock_submit, mock_option = _run_scan([_call("MU", stop=0.0)], user_id)

    assert result["placed_count"] == 0
    mock_submit.assert_not_called()
    mock_option.assert_not_called()
    rec = _skipped(result, "MU")
    assert rec["skip_category"] == "unprotectable_levels"
    assert rec["was_qualifying"] is True


def test_a_zero_target_candidate_is_rejected(user_id):
    result, mock_submit, _ = _run_scan([_call("PLTR", target=0.0)], user_id)
    mock_submit.assert_not_called()
    assert _skipped(result, "PLTR")["skip_category"] == "unprotectable_levels"


def test_a_zero_entry_candidate_is_rejected(user_id):
    result, mock_submit, _ = _run_scan([_call("COIN", ideal_entry=0.0)], user_id)
    mock_submit.assert_not_called()
    assert _skipped(result, "COIN")["skip_category"] == "unprotectable_levels"


def test_a_call_with_the_stop_above_entry_is_rejected_as_inverted(user_id):
    # stop 105 > entry 100 for a long is not a stop at all - a rise is the
    # position working, so this would never protect anything.
    result, mock_submit, _ = _run_scan([_call("SLB", ideal_entry=100.0, stop=105.0, target=110.0)], user_id)
    mock_submit.assert_not_called()
    assert _skipped(result, "SLB")["skip_category"] == "unprotectable_levels"


def test_a_put_with_the_stop_below_entry_is_rejected_as_inverted(user_id):
    result, mock_submit, _ = _run_scan([_put("ADBE", ideal_entry=100.0, stop=95.0, target=90.0)], user_id)
    mock_submit.assert_not_called()
    assert _skipped(result, "ADBE")["skip_category"] == "unprotectable_levels"


def test_a_well_formed_candidate_still_places_normally(user_id):
    result, mock_submit, _ = _run_scan([_call("NVDA", ideal_entry=100.0, stop=95.0, target=110.0)], user_id)

    assert result["placed_count"] == 1
    mock_submit.assert_called_once()
    assert [e["ticker"] for e in result["placed"]] == ["NVDA"]


def test_the_rejection_is_surfaced_in_the_persisted_scan_run_summary(user_id):
    result, _, _ = _run_scan([_call("MU", stop=0.0), _call("PLTR", target=0.0)], user_id)

    summary = pluto_app._summarize_scan_result_for_run_log(result)
    assert summary["skip_categories"]["unprotectable_levels"] == 2
    assert "not submitted" in summary["reason"]
    assert "MU" in summary["reason"] and "PLTR" in summary["reason"]


def test_a_dry_run_preview_also_hides_an_unprotectable_candidate(user_id):
    result, mock_submit, _ = _run_scan([_call("MU", stop=0.0), _call("NVDA")], user_id, dry_run=True)

    mock_submit.assert_not_called()  # dry run never submits regardless
    assert [e["ticker"] for e in result["placed"]] == ["NVDA"]  # only the good one previews
    assert _skipped(result, "MU")["skip_category"] == "unprotectable_levels"
