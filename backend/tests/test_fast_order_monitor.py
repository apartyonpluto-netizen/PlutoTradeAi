from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _discriminating_order_detail(entry_client_order_id="pt-entry-1"):
    """get_order_detail is called for BOTH the entry AND the stop/target
    legs during protection confirmation - a single blanket status can't
    correctly satisfy both (the entry must read FILLED; a protective leg
    must read SUBMITTED/PARTIAL FILLED to count as genuinely active - see
    _protective_leg_is_active). This tells them apart by client_order_id
    so a test can assert PROTECTION_CONFIRMED_ACTIVE specifically, not
    just tolerate PROTECTION_FAILED as an acceptable alternative."""

    def _detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == entry_client_order_id:
            return _order_detail("FILLED", 10, 10)
        return _order_detail("SUBMITTED", 10, 0)

    return _detail


def _transitional_entry(state: str, entry_client_order_id="pt-entry-1", **extra) -> dict:
    entry: dict = {
        "ticker": "AAPL",
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": "2026-08-11",
        "planned_risk_dollars": 50.0,
        "quantity": 10,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    if state != ol.ENTRY_SUBMITTED:
        if state in (ol.ENTRY_PARTIALLY_FILLED,):
            ol.transition(entry, ol.ENTRY_PARTIALLY_FILLED, filled_quantity=4.0)
        elif state == ol.ENTRY_FILLED:
            ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
            entry["entry_order_terminal"] = True
        elif state == ol.PROTECTION_PENDING:
            ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
            ol.transition(entry, ol.PROTECTION_PENDING)
            entry["entry_order_terminal"] = True
        elif state == ol.PROTECTION_FAILED:
            ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
            ol.transition(entry, ol.PROTECTION_PENDING)
            ol.transition(entry, ol.PROTECTION_FAILED, error="could not confirm protection active")
            entry["entry_order_terminal"] = True
        elif state == ol.PROTECTION_CONFIRMED_ACTIVE:
            ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
            ol.transition(entry, ol.PROTECTION_PENDING)
            ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="now")
            entry["entry_order_terminal"] = True
            entry["stop_client_order_id"] = "stop-id"
            entry["stop_leg_quantity"] = 10.0
            entry["stop_leg_attempt"] = 1
            entry["target_client_order_id"] = "target-id"
            entry["target_leg_quantity"] = 10.0
            entry["target_leg_attempt"] = 1
    entry.update(extra)
    return entry


# --- _poll_fill_and_protect: resuming from a non-fresh state -----------------


def test_resume_from_entry_filled_skips_fill_poll_and_goes_straight_to_protection():
    entry = _transitional_entry(ol.ENTRY_FILLED)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("ACTIVE", 10, 10)) as mock_detail, \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    assert result["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    mock_stop.assert_called_once()
    # The target is never placed as a broker order (app-monitored since
    # 2026-08-31 - see _reconcile_protective_leg_quantity's own comment).
    mock_target.assert_not_called()
    assert mock_stop.call_args.kwargs["quantity"] == 10.0
    # The ENTRY's own fill was never re-polled - only the stop leg -
    # get_order_detail was called for confirmation, never re-checking the
    # entry_client_order_id itself for a fill that's already known.
    entry_lookup_calls = [c for c in mock_detail.call_args_list if c.args[-1] == "pt-entry-1"]
    assert entry_lookup_calls == []


def test_resume_from_protection_pending_does_not_attempt_an_invalid_self_transition():
    # PROTECTION_PENDING -> PROTECTION_PENDING is not a valid transition -
    # resuming from exactly this state must not blow up.
    entry = _transitional_entry(ol.PROTECTION_PENDING)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("ACTIVE", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    assert result["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)


def test_resume_from_protection_failed_retries_placement_and_can_confirm():
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_discriminating_order_detail()), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    mock_stop.assert_called_once()
    mock_target.assert_not_called()  # app-monitored, never a broker order - see _reconcile_protective_leg_quantity
    assert result["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE


def test_resume_uses_the_same_deterministic_client_order_ids_as_a_fresh_attempt():
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    expected_stop_id = ol.deterministic_client_order_id("u1", "AAPL", "2026-08-11", "stop", attempt=1)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("ACTIVE", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    assert mock_stop.call_args.kwargs["client_order_id"] == expected_stop_id
    # The target is never placed as a broker order (app-monitored since
    # 2026-08-31 - see _reconcile_protective_leg_quantity's own comment) -
    # no deterministic id to check for it here.
    mock_target.assert_not_called()


def test_resume_does_not_recompute_realized_risk_or_refire_the_alert():
    entry = _transitional_entry(ol.PROTECTION_FAILED, realized_risk_dollars=999.0, realized_risk_exceeds_planned=True)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("ACTIVE", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    assert result["realized_risk_dollars"] == 999.0  # untouched, not recomputed
    realized_risk_alerts = [c for c in mock_alert.call_args_list if c.args[1].get("type") == "realized_risk_exceeds_planned"]
    assert realized_risk_alerts == []


def test_resume_from_entry_filled_computes_realized_risk_if_it_was_never_set():
    # The narrow window: a crash between confirming ENTRY_FILLED and this
    # function computing realized risk on the SAME original pass must not
    # PERMANENTLY skip that computation on resume.
    entry = _transitional_entry(ol.ENTRY_FILLED)
    assert "realized_risk_dollars" not in entry
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("ACTIVE", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    assert result["realized_risk_dollars"] == 50.0  # 10 x (100 - 95)


def test_resume_with_zero_stored_filled_quantity_fails_closed_without_attempting_placement():
    entry = _transitional_entry(ol.PROTECTION_FAILED, filled_quantity=0.0)
    with patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_stop:
        result = pluto_app._poll_fill_and_protect(
            user_id="u1", creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            entry_client_order_id="pt-entry-1", limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-08-11", entry=entry,
        )
    mock_stop.assert_not_called()
    assert result["lifecycle_state"] == ol.PROTECTION_FAILED  # left exactly where it was


def test_malformed_broker_response_fails_closed_without_advancing_or_protecting(user_id):
    """get_order_detail returning a response with no "orders" key at all
    (a genuinely malformed/incomplete broker reply, not a normal empty
    result) must not be misread as any real status. summarize_fill falls
    back to status "UNKNOWN" for this shape, which _entry_fill_is_final
    correctly does NOT treat as terminal - so this must leave the entry
    exactly where it was (no fill advance, no placement attempt) and be
    picked up by the monitor's own failed-attempt tracking, not silently
    treated as a normal "nothing happened yet" tick."""
    entry = _transitional_entry(ol.ENTRY_SUBMITTED)
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value={"malformed": "no orders key"}), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_stop.assert_not_called()
    mock_target.assert_not_called()
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.ENTRY_SUBMITTED  # not falsely advanced
    assert stored.get("filled_quantity") in (None, 0, 0.0)
    # Counted as a no-progress attempt (not a raised error, just an
    # inconclusive read) - still starts the stuck timer, same as any other
    # stall, rather than being silently treated as a fully normal tick.
    assert stored.get("monitor_first_failure_at")
    assert stored.get("monitor_attempt_count") == 1
    assert stored["monitor_last_attempt_at"]


# --- _monitor_transitional_orders --------------------------------------------


def _clean_protection_mocks(entry_client_order_id="pt-entry-1"):
    return dict(
        get_order_detail=patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_discriminating_order_detail(entry_client_order_id)),
        place_stop_loss_order=patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}),
        place_take_profit_order=patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}),
    )


def test_monitor_resumes_an_entry_submitted_order_all_the_way_to_protection(user_id):
    entry = _transitional_entry(ol.ENTRY_SUBMITTED)
    record_overnight_order(user_id, entry)
    mocks = _clean_protection_mocks()
    with mocks["get_order_detail"], mocks["place_stop_loss_order"], mocks["place_take_profit_order"], \
         patch.object(pluto_app, "time"), patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)


def test_monitor_resumes_a_protection_failed_order(user_id):
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    record_overnight_order(user_id, entry)
    mocks = _clean_protection_mocks()
    with mocks["get_order_detail"], mocks["place_stop_loss_order"], mocks["place_take_profit_order"], \
         patch.object(pluto_app, "time"), patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE


def test_monitor_does_not_touch_frozen_states(user_id):
    entry = _transitional_entry(ol.ENTRY_SUBMITTED)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_detail.assert_not_called()
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_monitor_does_not_touch_terminal_states(user_id):
    entry = _transitional_entry(ol.ENTRY_SUBMITTED)
    ol.transition(entry, ol.ENTRY_FAILED, error="rejected")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_detail.assert_not_called()


def test_monitor_one_bad_entry_does_not_block_another(user_id):
    # _reconcile_entry_fill_and_protection is itself already extremely
    # defensive (every broker call inside it is individually
    # try/excepted, so a mocked broker failure alone can't make it raise)
    # - to prove _monitor_transitional_orders' OWN best-effort isolation,
    # inject the failure at the level it actually has to defend against:
    # the function it calls per entry raising outright (a genuine bug, an
    # unexpected exception type, anything not already caught inside).
    entry_a = _transitional_entry(ol.PROTECTION_FAILED, entry_client_order_id="pt-a")
    entry_b = _transitional_entry(ol.PROTECTION_FAILED, entry_client_order_id="pt-b")
    record_overnight_order(user_id, entry_a)
    record_overnight_order(user_id, entry_b)

    def _reconcile_side_effect(*, entry_client_order_id, entry, **kwargs):
        if entry_client_order_id == "pt-a":
            raise RuntimeError("still broken")
        ol.transition(entry, ol.PROTECTION_PENDING)  # PROTECTION_FAILED -> PROTECTION_CONFIRMED_ACTIVE isn't itself a valid transition
        ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="now")
        return entry

    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection", side_effect=_reconcile_side_effect):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    orders = {o["entry_client_order_id"]: o for o in list_overnight_orders(user_id)}
    assert orders["pt-a"]["lifecycle_state"] == ol.PROTECTION_FAILED  # untouched by the crash
    assert orders["pt-b"]["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # resolved despite "a" failing


def test_monitor_stamps_stuck_since_when_no_forward_progress_is_made(user_id):
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    record_overnight_order(user_id, entry)
    # Every leg placement fails again - no forward progress this tick.
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("still down")), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=RuntimeError("still down")), \
         patch.object(pluto_app, "time"), patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_FAILED  # no progress - same state as before
    assert stored.get("monitor_first_failure_at")


def test_monitor_clears_stuck_since_once_progress_resumes(user_id):
    entry = _transitional_entry(ol.PROTECTION_FAILED, monitor_first_failure_at=datetime.now(timezone.utc).isoformat())
    record_overnight_order(user_id, entry)
    mocks = _clean_protection_mocks()
    with mocks["get_order_detail"], mocks["place_stop_loss_order"], mocks["place_take_profit_order"], \
         patch.object(pluto_app, "time"), patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert stored.get("monitor_first_failure_at") is None


# --- stuck-transitional-order freeze -----------------------------------------


def test_has_stuck_transitional_orders_false_with_nothing_stuck(user_id):
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    record_overnight_order(user_id, entry)
    assert pluto_app._has_stuck_transitional_orders_locally(user_id) is False


def test_has_stuck_transitional_orders_false_under_the_threshold(user_id):
    recent = datetime.now(timezone.utc) - timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS - 60)
    entry = _transitional_entry(ol.PROTECTION_FAILED, monitor_first_failure_at=recent.isoformat())
    record_overnight_order(user_id, entry)
    assert pluto_app._has_stuck_transitional_orders_locally(user_id) is False


def test_has_stuck_transitional_orders_true_past_the_threshold(user_id):
    old = datetime.now(timezone.utc) - timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS + 60)
    entry = _transitional_entry(ol.PROTECTION_FAILED, monitor_first_failure_at=old.isoformat())
    record_overnight_order(user_id, entry)
    assert pluto_app._has_stuck_transitional_orders_locally(user_id) is True


def test_has_unresolved_ambiguous_submission_locally_includes_the_stuck_signal(user_id):
    old = datetime.now(timezone.utc) - timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS + 60)
    entry = _transitional_entry(ol.PROTECTION_FAILED, monitor_first_failure_at=old.isoformat())
    record_overnight_order(user_id, entry)
    # No UNKNOWN_SUBMISSION_STATE / MANUAL_LINK_IN_PROGRESS / incomplete
    # resolution anywhere - the stuck ordinary entry is the ONLY reason
    # this should read as frozen.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


# --- _run_fast_order_monitor --------------------------------------------------


def test_fast_monitor_raises_when_webull_not_configured(user_id):
    with pytest.raises(pluto_app.ValidationError, match="not configured"):
        pluto_app._run_fast_order_monitor(user_id)


def test_fast_monitor_runs_every_reconciliation_pass(user_id):
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "_reconcile_exit_orders") as mock_exit, \
         patch.object(pluto_app, "_reconcile_unknown_submissions", return_value=False) as mock_unknown, \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False) as mock_recover, \
         patch.object(pluto_app, "_monitor_transitional_orders", return_value=False) as mock_monitor:
        result = pluto_app._run_fast_order_monitor(user_id)
    mock_exit.assert_called_once()
    mock_unknown.assert_called_once()
    mock_recover.assert_called_once()
    mock_monitor.assert_called_once()
    assert result == {
        "has_unresolved_ambiguous_submission": False,
        "has_incomplete_manual_resolution": False,
        "still_transitional": False,
        "entries_checked": 0,
        "still_transitional_count": 0,
    }
    # Does NOT run the market-scan/new-candidate work of a full scan.
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "_reconcile_exit_orders"), \
         patch.object(pluto_app, "_reconcile_unknown_submissions", return_value=False), \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False), \
         patch.object(pluto_app, "_monitor_transitional_orders", return_value=False), \
         patch.object(pluto_app, "_build_page_context") as mock_context:
        pluto_app._run_fast_order_monitor(user_id)
    mock_context.assert_not_called()


def test_fast_monitor_never_scans_scores_or_places_a_new_entry(user_id):
    """_build_page_context not being called (above) is only a PROXY for
    "no dashboard-rendering-adjacent work ran" - it does not, on its own,
    prove the monitor can't discover or submit a brand new trade. This
    directly patches every function actually capable of that (the
    scanner, the candidate/setup scorer, the entry-submission function,
    the top-level scan entrypoints, and the literal broker BUY-order API
    itself) and asserts NONE of them are ever called during a fast-monitor
    pass - the strongest available proof of "reconciliation only, never
    new positions" for this function."""
    entry = _transitional_entry(ol.PROTECTION_FAILED)
    record_overnight_order(user_id, entry)
    mocks = _clean_protection_mocks()
    with mocks["get_order_detail"], mocks["place_stop_loss_order"], mocks["place_take_profit_order"], \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "time"), patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "get_market_data") as mock_scanner, \
         patch.object(pluto_app, "build_strategy_intelligence") as mock_candidate_builder, \
         patch.object(pluto_app, "_submit_and_protect_entry") as mock_submit_entry, \
         patch.object(pluto_app, "_run_autonomous_trade_scan") as mock_scan_entry, \
         patch.object(pluto_app, "_run_autonomous_trade_scan_locked") as mock_scan_locked, \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_buy_order:
        pluto_app._run_fast_order_monitor(user_id)

    mock_scanner.assert_not_called()
    mock_candidate_builder.assert_not_called()
    mock_submit_entry.assert_not_called()
    mock_scan_entry.assert_not_called()
    mock_scan_locked.assert_not_called()
    mock_buy_order.assert_not_called()  # the literal broker API that would open a new position


def test_fast_monitor_holds_the_scan_lock_and_conflicts_with_a_running_scan(user_id):
    from scan_lock import ScanAlreadyRunningError, user_scan_lock

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}):
        with user_scan_lock(user_id):
            with pytest.raises(ScanAlreadyRunningError):
                pluto_app._run_fast_order_monitor(user_id)


# --- _user_needs_fast_monitor_pass: autonomy-mode-independent gating --------


def test_user_needs_fast_monitor_pass_false_with_nothing_at_all(user_id):
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is False


def test_user_needs_fast_monitor_pass_true_for_a_transitional_entry(user_id):
    record_overnight_order(user_id, _transitional_entry(ol.ENTRY_SUBMITTED))
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is True


def test_user_needs_fast_monitor_pass_true_for_a_confirmed_active_position(user_id):
    # PROTECTION_CONFIRMED_ACTIVE is deliberately non-terminal - a fully
    # protected, otherwise-quiet open position must still get exit-checked.
    record_overnight_order(user_id, _transitional_entry(ol.PROTECTION_CONFIRMED_ACTIVE))
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is True


def test_user_needs_fast_monitor_pass_false_for_a_closed_entry(user_id):
    entry = _transitional_entry(ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.CLOSED, close_reason="target_filled")
    record_overnight_order(user_id, entry)
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is False


def test_user_needs_fast_monitor_pass_true_for_a_tracked_exit_order_alone(user_id):
    from webull_stop_orders import record_exit_order

    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is True


def test_user_needs_fast_monitor_pass_true_for_an_incomplete_manual_resolution(user_id):
    from autonomy.ambiguous_resolution_audit import record_ambiguous_resolution_audit

    record_ambiguous_resolution_audit(user_id, {"phase": "resolution_started", "resolution_id": "res-1"})
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is True


# --- fast-monitor-trigger endpoint --------------------------------------------


def test_fast_monitor_trigger_requires_secret():
    with pluto_app.app.test_client() as client:
        response = client.post("/api/autonomy/fast-monitor-trigger")
    assert response.status_code == 401


def test_fast_monitor_trigger_skips_users_with_nothing_to_check(user_id):
    # No transitional entries, no tracked exits, no incomplete
    # resolutions - genuinely nothing for the fast monitor to do, so it
    # skips the broker round-trip entirely. Autonomy mode is irrelevant
    # to this decision (see the next two tests) - deliberately not even
    # mocked here.
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "_run_fast_order_monitor") as mock_monitor:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    assert response.status_code == 200
    mock_monitor.assert_not_called()
    assert response.get_json()["data"]["ran_for_users"] == 0


def test_fast_monitor_trigger_calls_the_monitor_for_users_with_something_to_check(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "_user_needs_fast_monitor_pass", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value={"still_transitional": True}) as mock_monitor:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    assert response.status_code == 200
    mock_monitor.assert_called_once_with(user_id)
    payload = response.get_json()
    assert payload["data"]["results"][0]["ok"] is True
    assert payload["data"]["results"][0]["still_transitional"] is True


def test_fast_monitor_trigger_processes_a_user_with_autonomy_off_but_an_open_position(user_id):
    # Issue #4, directly: "OFF prevents new entries only" - a user who
    # switched autonomy OFF but still has a real transitional entry (or a
    # fully-protected open position still needing exit-monitoring) must
    # still be processed by the fast monitor. No mocking of
    # _user_needs_fast_monitor_pass here - real state, real function.
    entry = _transitional_entry(ol.PROTECTION_CONFIRMED_ACTIVE)
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "_run_fast_order_monitor", return_value={"still_transitional": True}) as mock_monitor:
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    assert response.status_code == 200
    mock_monitor.assert_called_once_with(user_id)


def test_fast_monitor_trigger_treats_lock_conflict_as_a_benign_skip(user_id):
    from scan_lock import ScanAlreadyRunningError

    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "_user_needs_fast_monitor_pass", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", side_effect=ScanAlreadyRunningError("already running")):
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["data"]["results"][0]["ok"] is True
    assert payload["data"]["results"][0]["skipped"] == "scan_already_running"


def test_fast_monitor_trigger_one_users_failure_does_not_block_another():
    user_a, user_b = "fast-user-a", "fast-user-b"
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_a, user_b]), \
         patch.object(pluto_app, "_user_needs_fast_monitor_pass", return_value=True), \
         patch.object(pluto_app, "_run_fast_order_monitor", side_effect=[RuntimeError("boom"), {"still_transitional": False}]):
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["data"]["results"][0]["ok"] is False
    assert payload["data"]["results"][1]["ok"] is True
