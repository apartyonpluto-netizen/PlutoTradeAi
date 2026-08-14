from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "AAPL"
ORPHAN_ID = "pt-orphan-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _orphan_entry(**extra) -> dict:
    entry: dict = {"ticker": TICKER, "quantity": 10, "limit_price": 100.0, "stop": 0, "target": 0, "trading_day": "2026-08-12", "orphan_recovered": True}
    ol.initialize(entry, ol.UNKNOWN_SUBMISSION_STATE, entry_client_order_id=ORPHAN_ID)
    ol.transition(entry, ol.ENTRY_SUBMITTED, error=None)  # matches the caller's own transition before dispatch
    entry.update(extra)
    return entry


def test_zero_fill_already_terminal_resolves_cleanly_no_alert(user_id):
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)

    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    mock_cancel.assert_not_called()  # already terminal - nothing to cancel
    from alerts import load_manual_alerts
    assert load_manual_alerts(user_id) == []  # no risk was ever taken - nothing urgent to alert about


def test_zero_fill_still_resting_is_cancelled_outright(user_id):
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 10, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)

    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, ORPHAN_ID)
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    from alerts import load_manual_alerts
    alerts = load_manual_alerts(user_id)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "orphan_entry_cancelled_unfilled"
    assert alerts[0]["priority"] == "critical"


def test_zero_fill_cancel_failure_stays_unknown_submission_state(user_id):
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 10, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=RuntimeError("broker unreachable")):
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert "could not cancel" in result["last_reconciliation_error"].lower()


def test_partial_fill_cancels_unfilled_remainder_and_freezes_never_claims_protected(user_id):
    """4 of 10 shares filled, order still resting for the other 6 - must
    cancel the remainder immediately and freeze/alert on the filled
    portion, never describing it as protected."""
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("PARTIAL FILLED", 10, 4)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)

    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, ORPHAN_ID)
    assert result["lifecycle_state"] == ol.PROTECTION_FAILED
    assert result["filled_quantity"] == 4.0
    assert result["entry_order_terminal"] is True
    from alerts import load_manual_alerts
    alerts = load_manual_alerts(user_id)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "orphan_entry_filled_unprotected"
    assert alerts[0]["priority"] == "critical"
    message = alerts[0]["message"].lower()
    assert "unprotected" in message
    assert "protected" not in message.replace("unprotected", "")  # never describes it as merely "protected"


def test_full_fill_needs_no_remainder_cancel_still_freezes(user_id):
    """All 10 shares filled - the entry order is already broker-terminal
    (FILLED), so there's no unfilled remainder to cancel, but the
    freeze/alert must still fire."""
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)

    mock_cancel.assert_not_called()  # FILLED is already broker-terminal - nothing left to cancel
    assert result["lifecycle_state"] == ol.PROTECTION_FAILED
    assert result["filled_quantity"] == 10.0
    from alerts import load_manual_alerts
    assert len(load_manual_alerts(user_id)) == 1


def test_fill_status_lookup_failure_stays_unknown_submission_state(user_id):
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")):
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_partial_fill_freeze_survives_even_if_remainder_cancel_fails(user_id):
    """Best-effort - the filled portion is real risk regardless of
    whether cancelling the remainder succeeds; must still freeze/alert."""
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("PARTIAL FILLED", 10, 4)), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=RuntimeError("broker unreachable")):
        result = pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)
    assert result["lifecycle_state"] == ol.PROTECTION_FAILED
    from alerts import load_manual_alerts
    assert len(load_manual_alerts(user_id)) == 1


def test_never_places_a_stop_or_target_order_for_an_orphan(user_id):
    """The core safety property - no code path here may ever call
    place_stop_loss_order/place_take_profit_order, since that would mean
    inventing a price this app never actually planned."""
    entry = _orphan_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("PARTIAL FILLED", 10, 4)), \
         patch.object(pluto_app.webull_api, "cancel_order"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order") as mock_target:
        pluto_app._resolve_orphan_recovered_entry(user_id, CREDS, ACCOUNT_ID, entry)
    mock_stop.assert_not_called()
    mock_target.assert_not_called()


# --- Wiring into _reconcile_unknown_submission -----------------------------------


def test_reconcile_unknown_submission_routes_orphans_to_the_emergency_policy(user_id):
    entry = {"ticker": TICKER, "quantity": 10, "limit_price": 100.0, "stop": 0, "target": 0, "trading_day": "2026-08-12", "orphan_recovered": True}
    ol.initialize(entry, ol.UNKNOWN_SUBMISSION_STATE, entry_client_order_id=ORPHAN_ID)

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 10, 0)), \
         patch.object(pluto_app, "_resolve_orphan_recovered_entry", wraps=pluto_app._resolve_orphan_recovered_entry) as mock_resolve, \
         patch.object(pluto_app, "_poll_fill_and_protect") as mock_poll:
        pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)

    mock_resolve.assert_called_once()
    mock_poll.assert_not_called()  # never the NORMAL fill/protect flow for an orphan


def test_reconcile_unknown_submission_uses_normal_flow_for_a_non_orphan(user_id):
    """A regular (non-orphan) UNKNOWN_SUBMISSION_STATE entry - the
    ordinary ambiguous-submission case with a real planned stop/target -
    must still go through the NORMAL _poll_fill_and_protect flow, not the
    orphan emergency policy."""
    entry = {"ticker": TICKER, "quantity": 10, "limit_price": 100.0, "stop": 95.0, "target": 110.0, "trading_day": "2026-08-12"}
    ol.initialize(entry, ol.UNKNOWN_SUBMISSION_STATE, entry_client_order_id="pt-normal-1")

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 10, 0)), \
         patch.object(pluto_app, "_resolve_orphan_recovered_entry") as mock_orphan_resolve, \
         patch.object(pluto_app, "_poll_fill_and_protect", return_value=entry) as mock_poll:
        pluto_app._reconcile_unknown_submission(user_id, CREDS, ACCOUNT_ID, entry)

    mock_orphan_resolve.assert_not_called()
    mock_poll.assert_called_once()
