from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol

"""Found live 2026-08-28: an entry's limit_price is computed from
get_bars' up-to-15-minutes-stale chart data at scan time (see
integrations/alpaca_data.py's own module docstring), but the order is
submitted to Webull possibly minutes later - for a real candidate this
session (WDAY, a +5.94% day move), that gap was enough real price drift
to trip Webull's own OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE ("the
order price is too deviated") rejection, which then had to be frozen and
manually reconciled via the ambiguous-submission workflow after the
fact. These tests prove the actual fix: a fresh, real-time price check
(get_latest_trade_price, genuinely real-time even on the free/IEX tier -
NOT the same 15-min-embargoed data get_bars returns) immediately before
submission, gating the order rather than letting a stale price reach the
broker."""

CREDS = {"app_key": "key", "app_secret": "secret"}


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate(ticker="AAPL", ideal_entry=100.0):
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": 80,
        "ideal_entry": ideal_entry,
        "stop": ideal_entry * 0.5,
        "target": ideal_entry * 1.1,
    }


# --- _price_has_drifted_too_far: pure function -----------------------------


def test_no_drift_at_all_is_not_too_far():
    assert pluto_app._price_has_drifted_too_far(100.0, 100.0) is False


def test_drift_within_the_default_threshold_is_not_too_far():
    # 1.5% move - under the 2.0% default threshold.
    assert pluto_app._price_has_drifted_too_far(100.0, 101.5) is False
    assert pluto_app._price_has_drifted_too_far(100.0, 98.5) is False


def test_drift_past_the_default_threshold_is_too_far():
    # 3% move - over the 2.0% default threshold, in either direction.
    assert pluto_app._price_has_drifted_too_far(100.0, 103.0) is True
    assert pluto_app._price_has_drifted_too_far(100.0, 97.0) is True


def test_drift_exactly_at_the_threshold_is_not_too_far():
    # Strictly greater-than, not greater-or-equal - the boundary itself is fine.
    assert pluto_app._price_has_drifted_too_far(100.0, 102.0) is False


def test_a_custom_threshold_is_respected():
    assert pluto_app._price_has_drifted_too_far(100.0, 105.0, max_deviation_percent=10.0) is False
    assert pluto_app._price_has_drifted_too_far(100.0, 105.0, max_deviation_percent=1.0) is True


def test_a_non_positive_scan_time_price_is_never_treated_as_drifted():
    # The caller's own limit_price <= 0 check already fails this closed
    # for a different, more specific reason before this would be reached -
    # this function itself must not raise a division-by-zero either way.
    assert pluto_app._price_has_drifted_too_far(0.0, 500.0) is False
    assert pluto_app._price_has_drifted_too_far(-5.0, 500.0) is False


# --- integration: the actual skip decision inside a real scan --------------


def _run_scan(user_id, opportunities, latest_trade_price_side_effect):
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", side_effect=latest_trade_price_side_effect) as mock_latest, \
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry) as mock_submit, \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result, mock_submit, mock_latest


def test_a_fresh_price_matching_the_scan_time_price_places_normally(user_id):
    result, mock_submit, mock_latest = _run_scan(user_id, [_candidate(ideal_entry=100.0)], lambda ticker: 100.0)
    assert result["placed_count"] == 1
    mock_submit.assert_called_once()
    mock_latest.assert_called_once_with("AAPL")


def test_a_price_that_drifted_too_far_is_skipped_not_submitted(user_id):
    # Real price is now $110 - a 10% move away from the $100 scan-time
    # entry, well past the 2% default threshold.
    result, mock_submit, _mock_latest = _run_scan(user_id, [_candidate(ideal_entry=100.0)], lambda ticker: 110.0)

    assert result["placed_count"] == 0
    mock_submit.assert_not_called()  # never even reached the broker
    assert result["skipped_count"] == 1
    skipped = result["skipped"][0]
    assert skipped["ticker"] == "AAPL"
    assert "drifted" in skipped["reason_skipped"]
    assert "10.0%" in skipped["reason_skipped"]
    assert skipped["was_qualifying"] is True


def test_an_unconfirmable_fresh_price_is_skipped_not_submitted_on_a_stale_price(user_id):
    # get_latest_trade_price returning None (request failed, feed down,
    # credentials unavailable) must fail closed exactly like a confirmed
    # large drift does - never "couldn't check, assume it's fine."
    result, mock_submit, _mock_latest = _run_scan(user_id, [_candidate(ideal_entry=100.0)], lambda ticker: None)

    assert result["placed_count"] == 0
    mock_submit.assert_not_called()
    skipped = result["skipped"][0]
    assert "could not confirm a fresh" in skipped["reason_skipped"]


def test_small_drift_under_the_threshold_still_places_normally(user_id):
    # $101 vs $100 scan-time entry - 1% move, comfortably under the 2%
    # default threshold, must not be treated as drift.
    result, mock_submit, _mock_latest = _run_scan(user_id, [_candidate(ideal_entry=100.0)], lambda ticker: 101.0)
    assert result["placed_count"] == 1
    mock_submit.assert_called_once()


def test_the_freshness_check_is_not_consulted_at_all_during_a_dry_run_preview(user_id):
    # Preview mode never calls the broker or reserves anything real - see
    # dry_run's own contract in _run_autonomous_trade_scan_locked. It must
    # not burn an extra market-data call checking freshness for a
    # candidate that was never going to be submitted anyway.
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price") as mock_latest, \
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [_candidate()]}), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=True)

    assert result["placed_count"] == 1
    assert result["placed"][0]["status"] == "preview"
    mock_latest.assert_not_called()
