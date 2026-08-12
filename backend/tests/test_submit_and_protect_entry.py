from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _run(user_id, ticker, requested_quantity, limit_price, stop_price, target_price, fill_detail, planned_risk_dollars=None):
    entry: dict = {}
    if planned_risk_dollars is not None:
        entry["planned_risk_dollars"] = planned_risk_dollars

    with patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-id"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=fill_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        return pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker=ticker,
            requested_quantity=requested_quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            target_price=target_price,
            trading_day="2026-08-11",
            entry=entry,
        )


def test_full_fill_realized_risk_equals_planned_risk(user_id):
    # Requested 10, filled all 10 - realized risk should exactly match what
    # was planned at the same quantity and prices.
    result = _run(
        user_id,
        "AAPL",
        requested_quantity=10,
        limit_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        fill_detail=_order_detail("FILLED", 10, 10),
        planned_risk_dollars=50.0,  # 10 shares x $5 risk-per-share
    )
    assert result["filled_quantity"] == 10.0
    assert result["realized_risk_dollars"] == 50.0  # 10 x (100 - 95)
    assert "realized_risk_exceeds_planned" not in result


def test_partial_fill_realized_risk_is_smaller_than_planned():
    # Requested 10, only 4 actually filled - realized risk must reflect the
    # smaller ACTUAL position, not the originally planned quantity.
    result = _run(
        "user-partial",
        "AAPL",
        requested_quantity=10,
        limit_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        fill_detail=_order_detail("PARTIAL FILLED", 10, 4),
        planned_risk_dollars=50.0,  # planned for the full 10 shares
    )
    assert result["filled_quantity"] == 4.0
    assert result["realized_risk_dollars"] == 20.0  # 4 x (100 - 95), not 50
    assert result["realized_risk_dollars"] < result["planned_risk_dollars"]
    assert "realized_risk_exceeds_planned" not in result


def test_protective_orders_sized_to_actual_filled_quantity_not_requested(user_id):
    # The core partial-fill safety property: protection must cover exactly
    # what's actually held, not what was originally requested - sizing a
    # stop for 10 shares when only 4 filled would either fail at the broker
    # (insufficient position) or, worse, misrepresent the real exposure.
    with patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-id"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("PARTIAL FILLED", 10, 4)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}) as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._submit_and_protect_entry(
            user_id="user-sizing",
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry={},
        )
    assert mock_stop.call_args.kwargs["quantity"] == 4.0
    assert mock_target.call_args.kwargs["quantity"] == 4.0


def test_realized_risk_flagged_when_it_exceeds_planned(user_id):
    # Shouldn't normally happen (filled_quantity <= requested_quantity always
    # means realized risk <= planned risk at the same prices), but if the
    # caller's planned figure was computed differently, the flag must still
    # fire rather than silently accepting a worse-than-planned outcome.
    result = _run(
        user_id,
        "AAPL",
        requested_quantity=10,
        limit_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        fill_detail=_order_detail("FILLED", 10, 10),
        planned_risk_dollars=10.0,  # artificially low planned figure to trigger the flag
    )
    assert result["realized_risk_dollars"] == 50.0
    assert result["realized_risk_exceeds_planned"] is True


def test_realized_risk_exceeding_planned_fires_a_high_priority_alert():
    # A flag nobody consumes isn't a control - confirms the exceeds-planned
    # case actually produces a real, user-visible alert, not just a silent
    # dict key nothing ever reads.
    with patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-id"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        pluto_app._submit_and_protect_entry(
            user_id="user-alert-test",
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry={"planned_risk_dollars": 10.0},  # artificially low to force the exceeds-planned case
        )
    alert_calls = [call for call in mock_alert.call_args_list if call.args[1].get("type") == "realized_risk_exceeds_planned"]
    assert len(alert_calls) == 1
    assert alert_calls[0].args[1]["ticker"] == "AAPL"


def test_never_filled_within_poll_window_has_no_realized_risk(user_id):
    # Stays SUBMITTED (0 filled) for every poll attempt - no fill happened,
    # so there's nothing to recalculate risk against yet.
    result = _run(
        user_id,
        "AAPL",
        requested_quantity=10,
        limit_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        fill_detail=_order_detail("SUBMITTED", 10, 0),
    )
    assert result["lifecycle_state"] == ol.ENTRY_SUBMITTED
    assert "realized_risk_dollars" not in result


# --- ambiguous submission: UNKNOWN_SUBMISSION_STATE -------------------------


def test_well_formed_broker_rejection_is_a_definite_entry_failed(user_id):
    # _place_order_with_retry (integrations/webull.py) parses the real
    # ServerException fields and raises DefiniteOrderRejection only for a
    # well-formed, PARSED broker rejection - a definite, "the broker
    # rejected this" answer, not an ambiguous one.
    entry: dict = {}
    with patch.object(
             pluto_app.webull_api, "place_stock_order",
             side_effect=pluto_app.webull_api.DefiniteOrderRejection("Webull API error: bad quantity"),
         ), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry=entry,
        )
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    assert "bad quantity" in result["error"]


def test_ambiguous_placement_exception_goes_to_unknown_submission_state_not_entry_failed(user_id):
    # AmbiguousOrderSubmission - webull.py's explicit classification for a
    # network failure, auth/rate-limit/server error, or unparseable
    # response - means the broker's true response was lost, not that it
    # rejected the order. Must not be conflated with a definite rejection.
    entry: dict = {}
    with patch.object(
             pluto_app.webull_api, "place_stock_order",
             side_effect=pluto_app.webull_api.AmbiguousOrderSubmission("connection timed out"),
         ), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry=entry,
        )
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert "connection timed out" in result["error"]
    assert result.get("entry_client_order_id")  # still recorded - needed for reconciliation lookup


def test_unclassified_exception_type_during_placement_also_defaults_to_unknown_submission_state(user_id):
    # Fail-safe default: even an exception type this classification scheme
    # doesn't recognize at all (a raw TimeoutError here, never touching
    # webull.py's classifier because place_stock_order itself is mocked)
    # must still be treated as ambiguous, never silently definite.
    entry: dict = {}
    with patch.object(pluto_app.webull_api, "place_stock_order", side_effect=TimeoutError("connection timed out")), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry=entry,
        )
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- reconciling a stuck UNKNOWN_SUBMISSION_STATE entry ---------------------


def _unknown_entry(entry_client_order_id="pt-reconcile-id", **extra) -> dict:
    entry: dict = {
        "ticker": "AAPL",
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": "2026-08-11",
        "planned_risk_dollars": 50.0,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    entry.update(extra)
    return entry


def test_reconciliation_still_unreachable_leaves_state_unknown(user_id):
    entry = _unknown_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=TimeoutError("still unreachable")):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert result["last_reconciliation_error"] == "still unreachable"


def test_reconciliation_finds_the_order_and_resumes_fill_tracking(user_id):
    # The broker DOES have a record of it - the ambiguity resolves in favor
    # of "it went through", and fill polling/protection resumes exactly as
    # if the original placement call had returned normally.
    entry = _unknown_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    assert result["filled_quantity"] == 10.0
    assert result["realized_risk_dollars"] == 50.0


def test_reconciliation_first_definite_rejection_sighting_stays_ambiguous(user_id):
    # A well-formed, PARSED "no such order" rejection is NOT immediately
    # conclusive on its own - Webull hasn't published a read-after-write
    # consistency guarantee for order lookups, so a "not found" moments
    # after an ambiguous submission could just be replication lag. The
    # FIRST sighting only records itself and stays UNKNOWN_SUBMISSION_STATE.
    entry = _unknown_entry()
    with patch.object(
        pluto_app.webull_api, "get_order_detail",
        side_effect=pluto_app.webull_api.DefiniteOrderRejection("ORDER_NOT_FOUND"),
    ):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert result["definite_rejection_count"] == 1
    assert result["first_definite_rejection_at"]


def test_reconciliation_repeated_sightings_before_grace_period_stay_ambiguous(user_id):
    # Enough CONFIRMATIONS (3) but not enough ELAPSED TIME since the first
    # sighting - both conditions must hold, not just the count.
    entry = _unknown_entry(
        definite_rejection_count=2,
        first_definite_rejection_at=(pluto_app._now_utc() - pluto_app.timedelta(seconds=30)).isoformat(),
    )
    with patch.object(
        pluto_app.webull_api, "get_order_detail",
        side_effect=pluto_app.webull_api.DefiniteOrderRejection("ORDER_NOT_FOUND"),
    ):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert result["definite_rejection_count"] == 3


def test_reconciliation_resolves_to_entry_failed_once_both_conditions_are_met(user_id):
    # Enough confirmations AND enough elapsed time since the FIRST sighting -
    # only then does the ambiguity resolve and the reservation release.
    entry = _unknown_entry(
        definite_rejection_count=2,
        first_definite_rejection_at=(
            pluto_app._now_utc() - pluto_app.timedelta(seconds=pluto_app.UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS + 1)
        ).isoformat(),
    )
    with patch.object(
        pluto_app.webull_api, "get_order_detail",
        side_effect=pluto_app.webull_api.DefiniteOrderRejection("ORDER_NOT_FOUND"),
    ):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    assert "ORDER_NOT_FOUND" in result["error"]
    assert "3x" in result["error"]


def test_reconciliation_ambiguous_result_in_between_does_not_reset_the_streak(user_id):
    # An AmbiguousOrderSubmission (or any other lookup failure) neither
    # confirms nor contradicts a prior definite "not found" sighting - the
    # streak/timestamp must survive it untouched.
    entry = _unknown_entry(
        definite_rejection_count=2,
        first_definite_rejection_at=(
            pluto_app._now_utc() - pluto_app.timedelta(seconds=pluto_app.UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS + 1)
        ).isoformat(),
    )
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=TimeoutError("noise")):
        mid_result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert mid_result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert mid_result["definite_rejection_count"] == 2  # unchanged by the ambiguous attempt

    with patch.object(
        pluto_app.webull_api, "get_order_detail",
        side_effect=pluto_app.webull_api.DefiniteOrderRejection("ORDER_NOT_FOUND"),
    ):
        final_result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert final_result["lifecycle_state"] == ol.ENTRY_FAILED


# --- _parse_trusted_past_timestamp: reject corrupt/future/naive values -----


def test_parse_trusted_past_timestamp_missing_returns_default():
    now = pluto_app._now_utc()
    assert pluto_app._parse_trusted_past_timestamp(None, now=now, default=now) == now
    assert pluto_app._parse_trusted_past_timestamp("", now=now, default=now) == now


def test_parse_trusted_past_timestamp_unparseable_returns_default():
    now = pluto_app._now_utc()
    assert pluto_app._parse_trusted_past_timestamp("not-a-timestamp", now=now, default=now) == now


def test_parse_trusted_past_timestamp_naive_datetime_returns_default():
    # _now_utc().isoformat() always writes a timezone-aware string - a
    # naive one didn't come from this code path and can't be safely
    # compared against an aware `now` (mixing naive/aware raises TypeError
    # on subtraction), so it's treated as corrupt rather than assumed UTC.
    now = pluto_app._now_utc()
    naive = datetime(2026, 1, 1, 12, 0, 0).isoformat()  # no tzinfo
    assert pluto_app._parse_trusted_past_timestamp(naive, now=now, default=now) == now


def test_parse_trusted_past_timestamp_future_value_returns_default():
    # A first-rejection timestamp later than "now" is nonsensical (clock
    # skew or a corrupted/tampered value) and must not be trusted to anchor
    # elapsed-time math.
    now = pluto_app._now_utc()
    future = (now + pluto_app.timedelta(hours=1)).isoformat()
    assert pluto_app._parse_trusted_past_timestamp(future, now=now, default=now) == now


def test_parse_trusted_past_timestamp_valid_past_value_is_trusted():
    now = pluto_app._now_utc()
    past = (now - pluto_app.timedelta(seconds=100)).isoformat()
    result = pluto_app._parse_trusted_past_timestamp(past, now=now, default=now)
    assert result != now
    assert (now - result).total_seconds() == pytest.approx(100, abs=1)


def test_reconciliation_corrupt_future_timestamp_does_not_shortcut_the_grace_period(user_id):
    # Even with the confirmation COUNT already at the threshold, a corrupt
    # (future) stored first_definite_rejection_at must not let elapsed time
    # be computed against it - the grace period restarts fresh instead of
    # being satisfied by bad data.
    entry = _unknown_entry(
        definite_rejection_count=5,  # already well past MIN_DEFINITE_REJECTION_CONFIRMATIONS
        first_definite_rejection_at=(pluto_app._now_utc() + pluto_app.timedelta(hours=1)).isoformat(),  # in the future
    )
    with patch.object(
        pluto_app.webull_api, "get_order_detail",
        side_effect=pluto_app.webull_api.DefiniteOrderRejection("ORDER_NOT_FOUND"),
    ):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE  # not resolved despite the high count


def test_reconciliation_ambiguous_submission_that_partially_filled_then_cancelled(user_id):
    # The scenario the user explicitly asked to cover: an entry left
    # UNKNOWN_SUBMISSION_STATE by a timeout turns out to have actually
    # partially filled (4 of 10 shares) before the remainder was cancelled -
    # those 4 shares are a real, held position and must be protected, not
    # discarded just because the order's overall status is CANCELLED.
    entry = _unknown_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 4)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}) as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["filled_quantity"] == 4.0
    assert result["unfilled_remainder_status"] == "CANCELLED"
    assert result["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    # Protection is sized to what actually filled (4), not the original
    # requested quantity (10) and not zero.
    assert mock_stop.call_args.kwargs["quantity"] == 4.0
    assert mock_target.call_args.kwargs["quantity"] == 4.0


def test_reconciliation_finds_a_cancelled_order_does_not_resume_it_as_active(user_id):
    # The broker HAS a record of the order, but its status is a definite
    # negative outcome discovered just now - must resolve to ENTRY_FAILED
    # directly, not be transitioned to ENTRY_SUBMITTED and resumed as though
    # it were still live.
    entry = _unknown_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 0)):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    assert "cancelled" in result["error"].lower()


def test_reconciliation_finds_a_failed_order_does_not_resume_it_as_active(user_id):
    entry = _unknown_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FAILED", 10, 0)):
        result = pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.ENTRY_FAILED


def test_normal_entry_partially_filled_then_cancelled_still_gets_protected(user_id):
    # Not an ambiguous-submission scenario - a perfectly normal, cleanly
    # submitted entry whose broker-side status ends up CANCELLED after
    # partially filling (e.g. the remainder didn't fill by session close).
    # This is the SAME underlying bug the reconciliation-specific test
    # covers, but exercised through the everyday _submit_and_protect_entry
    # path - the fix lives in the shared _poll_fill_and_protect, not
    # something reconciliation-only.
    with patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-id"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 3)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}) as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry={},
        )
    assert result["filled_quantity"] == 3.0
    assert result["unfilled_remainder_status"] == "CANCELLED"
    assert result["lifecycle_state"] != ol.ENTRY_FAILED  # the 3 filled shares must not be discarded as a no-op
    assert mock_stop.call_args.kwargs["quantity"] == 3.0
    assert mock_target.call_args.kwargs["quantity"] == 3.0


def test_normal_entry_truly_zero_fill_cancelled_still_resolves_entry_failed(user_id):
    # The original, un-regressed case: genuinely zero shares filled before
    # cancellation is still a true no-position outcome.
    with patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": "entry-id"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 0)), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id,
            creds=CREDS,
            account_id=ACCOUNT_ID,
            ticker="AAPL",
            requested_quantity=10,
            limit_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            trading_day="2026-08-11",
            entry={},
        )
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    assert "unfilled_remainder_status" not in result


# --- scan-level reconciliation pass: _reconcile_unknown_submissions --------


def test_scan_level_reconciliation_is_a_no_op_with_nothing_pending(user_id):
    record_overnight_order(user_id, {"ticker": "AAPL", "status": "placed", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE})
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_lookup:
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)
    mock_lookup.assert_not_called()
    assert still_unresolved is False


def test_scan_level_reconciliation_persists_the_resolved_state_in_place(user_id):
    entry = _unknown_entry(entry_client_order_id="pt-scan-reconcile")
    record_overnight_order(user_id, entry)
    stored = list_overnight_orders(user_id)
    assert stored[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=TimeoutError("still unreachable")):
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)

    stored_after = list_overnight_orders(user_id)
    assert stored_after[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert stored_after[0]["last_reconciliation_error"] == "still unreachable"
    assert still_unresolved is True


def test_scan_level_reconciliation_returns_false_once_resolved(user_id):
    entry = _unknown_entry(entry_client_order_id="pt-scan-resolves")
    record_overnight_order(user_id, entry)

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 0)):
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)

    assert still_unresolved is False
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.ENTRY_FAILED


def test_scan_level_reconciliation_one_bad_record_does_not_block_the_others(user_id):
    # Exercises _reconcile_unknown_submissions' OWN try/except around each
    # call - not _reconcile_unknown_submission's internal one (a lookup
    # failure there is the ordinary "still ambiguous" path, already covered
    # above). This simulates a bug INSIDE reconciliation itself for one
    # record (e.g. a malformed stored entry) crashing outright.
    good = _unknown_entry(entry_client_order_id="pt-good")
    good["ticker"] = "AAPL"
    bad = _unknown_entry(entry_client_order_id="pt-bad")
    bad["ticker"] = "MSFT"
    record_overnight_order(user_id, bad)
    record_overnight_order(user_id, good)

    def _reconcile_side_effect(user_id_arg, creds_arg, account_id_arg, entry):
        if entry.get("ticker") == "MSFT":
            raise RuntimeError("boom - simulated crash reconciling this one record")
        ol.transition(entry, ol.ENTRY_SUBMITTED, error=None)
        return entry

    with patch.object(pluto_app, "_reconcile_unknown_submission", side_effect=_reconcile_side_effect):
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)
    # MSFT's crash left it still UNKNOWN_SUBMISSION_STATE - overall result
    # must reflect that, even though AAPL resolved fine.
    assert still_unresolved is True

    stored = {order["ticker"]: order for order in list_overnight_orders(user_id)}
    assert stored["AAPL"]["lifecycle_state"] == ol.ENTRY_SUBMITTED  # resolved despite the other record's crash
    assert stored["MSFT"]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE  # untouched by the crash, not corrupted
