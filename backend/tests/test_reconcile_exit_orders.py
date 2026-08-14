from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order
from webull_stop_orders import record_exit_order, tracked_tickers

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _leg_detail(status: str) -> dict:
    return {"orders": [{"status": status, "total_quantity": "10", "filled_quantity": "10" if status == "FILLED" else "0", "order_id": "X"}]}


# --- sibling-leg cancellation -------------------------------------------------
# Verifies the property explicitly called out for review: when either exit
# leg (stop-loss or take-profit) fills and the position closes, the OTHER
# resting leg must be cancelled - not left resting against shares that no
# longer exist, where it could later fire and open an accidental short.
# Position absence ALONE is deliberately not enough evidence (see issue:
# determine closure using position data plus stop/target order status) -
# every test below also corroborates via each tracked leg's own status.


def test_sibling_leg_is_cancelled_once_the_position_closes(user_id):
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")
    cancelled_for_real: set = set()

    def _detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == "target-id-1":
            return _leg_detail("FILLED")
        return _leg_detail("CANCELLED" if client_order_id in cancelled_for_real else "SUBMITTED")

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled_for_real.add(client_order_id)
        return {"ok": True}

    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel) as mock_cancel:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    cancelled_ids = {c.args[-1] for c in mock_cancel.call_args_list}
    assert cancelled_ids == {"stop-id-1"}  # only the stale sibling is CANCELLED...
    assert tracked_tickers(user_id) == []  # ...but both are done tracking: the filled one needed no cancel


def test_untouched_ticker_with_an_open_position_is_left_completely_alone(user_id):
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 10, "last_price": 100.0}]), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_cancel.assert_not_called()
    assert set(tracked_tickers(user_id)) == {"AAPL"}


def test_only_the_closed_tickers_legs_are_cancelled_others_are_untouched(user_id):
    record_exit_order(user_id, "AAPL", "aapl-stop", "stop")
    record_exit_order(user_id, "AAPL", "aapl-target", "take_profit")
    record_exit_order(user_id, "MSFT", "msft-stop", "stop")
    record_exit_order(user_id, "MSFT", "msft-target", "take_profit")
    cancelled_for_real: set = set()

    def _detail(app_key, app_secret, account_id, client_order_id):
        # aapl-target filled and closed the AAPL position; MSFT's legs are
        # never even looked up since MSFT is still an open position.
        if client_order_id == "aapl-target":
            return _leg_detail("FILLED")
        return _leg_detail("CANCELLED" if client_order_id in cancelled_for_real else "SUBMITTED")

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled_for_real.add(client_order_id)
        return {"ok": True}

    # AAPL closed, MSFT is still an open position.
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "MSFT", "quantity": 5, "last_price": 50.0}]), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_detail) as mock_detail, \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel) as mock_cancel:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    cancelled_ids = {c.args[-1] for c in mock_cancel.call_args_list}
    assert cancelled_ids == {"aapl-stop"}
    assert set(tracked_tickers(user_id)) == {"MSFT"}
    msft_lookups = [c for c in mock_detail.call_args_list if c.args[-1] in ("msft-stop", "msft-target")]
    assert msft_lookups == []


def test_a_ticker_with_no_tracked_exit_orders_is_a_no_op(user_id):
    # Nothing tracked at all for this user - must not raise or do anything
    # surprising just because a position closed.
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_cancel.assert_not_called()


def test_a_failed_cancel_retains_tracking_and_alerts_instead_of_dropping_it(user_id):
    # The property explicitly required: never remove protective-order
    # tracking merely because a cancellation call was ATTEMPTED - only
    # once its terminal broker status is CONFIRMED. A failed cancel must
    # leave the leg tracked (so it gets retried next pass) and alert.
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")

    def _detail(app_key, app_secret, account_id, client_order_id):
        return _leg_detail("FILLED" if client_order_id == "target-id-1" else "SUBMITTED")

    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=RuntimeError("broker unreachable")) as mock_cancel, \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, "stop-id-1")
    # stop-id-1's cancel failed - still tracked. target-id-1 was the
    # filled leg (nothing to cancel) - no longer needs tracking.
    tracked_ids = {o["id"] for o in pluto_app.get_exit_orders(user_id, "AAPL")}
    assert tracked_ids == {"stop-id-1"}
    alert_types = [c.args[1].get("type") for c in mock_alert.call_args_list]
    assert "exit_order_cancel_failed" in alert_types


def test_an_unconfirmed_cancel_also_retains_tracking(user_id):
    # A cancel call that doesn't raise is not proof the order is actually
    # gone - the RE-CHECK afterward is what's authoritative. If the
    # re-check itself fails, or still shows the leg active, tracking must
    # still be retained.
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")

    def _detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == "target-id-1":
            return _leg_detail("FILLED")
        return _leg_detail("SUBMITTED")  # stop-id-1 still shows active even after "cancelling" it

    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value={"ok": True}), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    tracked_ids = {o["id"] for o in pluto_app.get_exit_orders(user_id, "AAPL")}
    assert tracked_ids == {"stop-id-1"}
    alert_types = [c.args[1].get("type") for c in mock_alert.call_args_list]
    assert "exit_order_cancel_unconfirmed" in alert_types


def test_position_absence_without_conclusive_evidence_does_not_falsely_close_the_trade(user_id):
    # Required test #10: position absence ALONE must never be read as
    # proof of an exit. Neither tracked leg shows FILLED here - nothing
    # explains why the position is gone - so nothing may be cancelled.
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")

    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_leg_detail("SUBMITTED")), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel, \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_cancel.assert_not_called()
    assert set(tracked_tickers(user_id)) == {"AAPL"}  # completely untouched
    alert_types = [c.args[1].get("type") for c in mock_alert.call_args_list]
    assert "unexplained_position_absence" in alert_types


def test_a_positions_lookup_failure_fails_safe_without_touching_anything(user_id):
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    with patch.object(pluto_app.webull_api, "get_account_positions", side_effect=RuntimeError("broker down")), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_cancel.assert_not_called()
    # Still tracked - nothing was assumed either way while the broker
    # couldn't even confirm what's currently open.
    assert set(tracked_tickers(user_id)) == {"AAPL"}


def test_sibling_cancellation_runs_from_the_fast_monitor_too(user_id):
    # The whole point of running _reconcile_exit_orders inside
    # _run_fast_order_monitor (not just the full 5-minute scan) is so a
    # stale sibling leg doesn't rest for up to 5 minutes after the other
    # leg fills.
    record_exit_order(user_id, "AAPL", "stop-id-1", "stop")
    record_exit_order(user_id, "AAPL", "target-id-1", "take_profit")
    cancelled_for_real: set = set()

    def _detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == "target-id-1":
            return _leg_detail("FILLED")
        return _leg_detail("CANCELLED" if client_order_id in cancelled_for_real else "SUBMITTED")

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled_for_real.add(client_order_id)
        return {"ok": True}

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "_reconcile_unknown_submissions", return_value=False), \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False), \
         patch.object(pluto_app, "_monitor_transitional_orders", return_value=False), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_detail), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        pluto_app._run_fast_order_monitor(user_id)
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, "stop-id-1")


# --- legacy stop-refresh retry (still live, used by _refresh_stop_confidence) -


def test_stop_refresh_retry_places_the_stop_once_conditions_allow_it(user_id):
    entry = {
        "ticker": "AAPL", "status": "placed", "side": "BUY", "quantity": 10,
        "stop": 95.0, "stop_order_placed": False,
    }
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 10, "last_price": 100.0}]), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-retry-id"}) as mock_stop, \
         patch.object(pluto_app, "time"):
        pluto_app._reconcile_exit_orders(user_id, CREDS, ACCOUNT_ID)
    mock_stop.assert_called_once()
    stored = list_overnight_orders(user_id)[0]
    assert stored["stop_order_placed"] is True
    assert set(tracked_tickers(user_id)) == {"AAPL"}
