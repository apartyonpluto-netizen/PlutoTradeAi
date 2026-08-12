from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from alerts import load_manual_alerts
from autonomy.overnight_orders import list_overnight_orders

CREDS = {"app_key": "key", "app_secret": "secret"}


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    # A minimal stand-in for the real fill/protection flow (already covered
    # by test_submit_and_protect_entry.py) - just enough to reach a terminal
    # lifecycle_state without touching webull_api at all, so this test stays
    # focused on what happens AFTER submission succeeds: recording it.
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _run_scan_with_mocks(user_id, opportunities, record_overnight_order_mock):
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(
             pluto_app.webull_api,
             "get_account_balance",
             return_value={
                 "total_net_liquidation_value": 100000.0,
                 "total_day_profit_loss": 0.0,
                 "account_currency_assets": [{"buying_power": "1000000"}],
             },
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry) as mock_submit, \
         patch.object(pluto_app, "record_overnight_order", side_effect=record_overnight_order_mock) as mock_record, \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result, mock_submit, mock_record


def _candidates():
    # Deliberately wide stops (deep relative to entry) so risk-based sizing
    # - not buying power - binds each candidate to a small slice of the
    # $100,000 virtual balance, leaving plenty of room for the SECOND
    # candidate too. A tight stop close to the 5% default risk_percent_of_balance
    # would size the first candidate to consume the ENTIRE balance by
    # itself (risk_qty x entry_price collapses to ~100% of balance whenever
    # stop distance / entry price approaches risk_percent_of_balance),
    # which would make "candidate 2 never reached" ambiguous between "the
    # circuit breaker worked" and "it was skipped for being unaffordable".
    return [
        {
            "ticker": "AAPL",
            "recommendation": "CALL",
            "confidence": 80,
            "ideal_entry": 100.0,
            "stop": 50.0,
            "target": 110.0,
        },
        {
            "ticker": "MSFT",
            "recommendation": "CALL",
            "confidence": 75,
            "ideal_entry": 200.0,
            "stop": 100.0,
            "target": 220.0,
        },
    ]


def test_scan_places_both_candidates_when_recording_never_fails(user_id):
    # Sanity check for the mocking harness itself, before proving the
    # failure case below - both candidates should place normally.
    result, mock_submit, mock_record = _run_scan_with_mocks(user_id, _candidates(), record_overnight_order_mock=lambda user_id, entry: entry)
    assert result["placed_count"] == 2
    assert mock_submit.call_count == 2
    assert mock_record.call_count == 2


def test_disk_write_failure_recording_an_entry_halts_the_rest_of_the_scan(user_id):
    # record_overnight_order persists the entry AFTER _submit_and_protect_entry
    # has already placed real orders at the broker - if THAT write fails (disk
    # full, permission error), the scan must not silently swallow it and keep
    # placing more orders it then also can't record. It must propagate and
    # stop the candidate loop immediately.
    disk_full = OSError("No space left on device")
    submit_calls = []
    record_calls = []

    def _submit_spy(*args, **kwargs):
        submit_calls.append(1)
        return _fake_submit_and_protect_entry(*args, **kwargs)

    def _record_spy(user_id, entry):
        record_calls.append(1)
        raise disk_full

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(
             pluto_app.webull_api,
             "get_account_balance",
             return_value={
                 "total_net_liquidation_value": 100000.0,
                 "total_day_profit_loss": 0.0,
                 "account_currency_assets": [{"buying_power": "1000000"}],
             },
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": _candidates()}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_submit_spy), \
         patch.object(pluto_app, "record_overnight_order", side_effect=_record_spy), \
         patch.object(pluto_app, "time"):
        with pytest.raises(OSError, match="No space left on device"):
            pluto_app._run_autonomous_trade_scan_locked(user_id)

    # The exception propagated (not swallowed into a "failed" result), and
    # candidate 2 was never reached - the loop stopped at the first failure.
    assert len(submit_calls) == 1
    assert len(record_calls) == 1


def test_ambiguous_submission_circuit_breaker_halts_the_scan(user_id):
    # Uses the same end-to-end harness as the disk-write-failure test above
    # to prove the OTHER halt condition: candidate 1's entry submission
    # returns UNKNOWN_SUBMISSION_STATE (see _submit_and_protect_entry's
    # exception-type distinction), and candidate 2 must never be reached in
    # the same scan tick - the account's true committed capital is no longer
    # confidently known once that happens (see the circuit-breaker comment
    # in _run_autonomous_trade_scan_locked).
    submit_calls = []

    def _ambiguous_submit(
        user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
    ):
        submit_calls.append(ticker)
        ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
        ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="connection timed out")
        return entry

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(
             pluto_app.webull_api,
             "get_account_balance",
             return_value={
                 "total_net_liquidation_value": 100000.0,
                 "total_day_profit_loss": 0.0,
                 "account_currency_assets": [{"buying_power": "1000000"}],
             },
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": _candidates()}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_ambiguous_submit), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    # No exception this time (an ambiguous result is handled gracefully,
    # unlike a hard disk-write failure) - but still only one candidate ever
    # reached submission.
    assert submit_calls == ["AAPL"]
    assert result["placed_count"] == 0
    assert result["skipped_count"] == 1
    skipped_entry = result["skipped"][0]
    assert skipped_entry["ticker"] == "AAPL"
    assert skipped_entry["status"] == "unknown_submission_state"
    assert skipped_entry["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE

    alerts_fired = load_manual_alerts(user_id)
    unknown_alerts = [a for a in alerts_fired if a["type"] == "unknown_submission_state"]
    assert len(unknown_alerts) == 1
    assert unknown_alerts[0]["ticker"] == "AAPL"

    stored_orders = list_overnight_orders(user_id)
    assert len(stored_orders) == 1  # only candidate 1 was ever recorded
    assert stored_orders[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_persistent_ambiguity_blocks_new_entries_on_a_later_scan(user_id):
    # The most important correction from this round: an unresolved
    # UNKNOWN_SUBMISSION_STATE must block new entries on EVERY subsequent
    # scan, not just the tick that created it. Scan 1 produces the
    # ambiguous submission; scan 2 still can't find it anywhere (order
    # detail, open orders, positions all come up empty) and presents a
    # brand-new qualifying candidate (MSFT) - no new entry may be submitted.
    def _scan_mocks(opportunities, get_order_detail_mock):
        return (
            patch.object(pluto_app, "get_webull_credentials", return_value=CREDS),
            patch.object(pluto_app, "is_webull_configured", return_value=True),
            patch.object(pluto_app, "get_anthropic_api_key", return_value=""),
            patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]),
            patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]),
            patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}),
            patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"),
            patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]),  # no resulting position either
            patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]),  # not resting as an open order either
            patch.object(
                pluto_app.webull_api,
                "get_account_balance",
                return_value={
                    "total_net_liquidation_value": 100000.0,
                    "total_day_profit_loss": 0.0,
                    "account_currency_assets": [{"buying_power": "1000000"}],
                },
            ),
            patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}),
            patch.object(pluto_app.webull_api, "get_order_detail", side_effect=get_order_detail_mock),
            patch.object(pluto_app, "time"),
        )

    # --- Scan 1: produces the ambiguous submission -------------------------
    def _ambiguous_submit(
        user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
    ):
        ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
        ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="connection timed out")
        return entry

    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_ambiguous_submit))
        for cm in _scan_mocks([_candidates()[0]], get_order_detail_mock=RuntimeError("not reached in scan 1")):
            stack.enter_context(cm)
        scan_1_result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    assert scan_1_result["placed_count"] == 0
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE

    # --- Scan 2: still can't find it anywhere; a NEW candidate qualifies ---
    new_candidate = {
        "ticker": "MSFT",
        "recommendation": "CALL",
        "confidence": 75,
        "ideal_entry": 200.0,
        "stop": 100.0,
        "target": 220.0,
    }
    submit_calls_scan_2 = []

    def _submit_spy(*args, **kwargs):
        submit_calls_scan_2.append(1)
        return _fake_submit_and_protect_entry(*args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_submit_spy))
        for cm in _scan_mocks(
            [new_candidate], get_order_detail_mock=pluto_app.webull_api.AmbiguousOrderSubmission("still unreachable")
        ):
            stack.enter_context(cm)
        scan_2_result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    # No new entry was ever attempted, even though MSFT would otherwise
    # have qualified on its own merits.
    assert submit_calls_scan_2 == []
    assert scan_2_result["placed_count"] == 0
    msft_skip = next(item for item in scan_2_result["skipped"] if item.get("ticker") == "MSFT")
    assert "unresolved" in msft_skip["reason_skipped"]

    # The original ambiguous entry is still exactly where it was - reserved
    # and unresolved, not silently dropped or falsely resolved.
    stored = list_overnight_orders(user_id)
    aapl_record = next(order for order in stored if order.get("ticker") == "AAPL")
    assert aapl_record["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
