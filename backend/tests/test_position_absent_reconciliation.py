from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

"""Found live 2026-08-31: after the stale-cancelled-stop replacement fix
(d9ae01e) correctly detected and cleared a dead stop leg, replacing it hit
a genuinely new broker rejection - OPENAPI_GENERATE_NEW_SHORT_POSITION -
revealing the real SLB position had already been closed at the broker by
some means this app's own tracked orders never explained (the stop showed
CANCELLED, not FILLED). Confirmed live via the trade-journal page: 0 open
Webull sandbox positions. _check_and_execute_target_exit never had a
chance to run (the entry was stuck in PROTECTION_FAILED, never
PROTECTION_CONFIRMED_ACTIVE), and _reconcile_closed_ticker_exit_orders'
own "position absence alone is never sufficient evidence" discipline means
this app correctly refuses to auto-close and invent a P&L here - these
tests cover the detection (stop retrying, flag, alert) and the admin-only,
always-re-verified-fresh resolution path instead."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "SLB"
TRADING_DAY = "2026-08-31"
STOP_ID = "stop-cid-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _stuck_entry(**extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "limit_price": 58.18,
        "stop": 54.95,
        "target": 60.0,
        "trading_day": TRADING_DAY,
        "quantity": 30,
        "filled_quantity": 30.0,
        "average_entry_fill_price": 58.10,
        "entry_order_terminal": True,
        "stop_client_order_id": STOP_ID,
        "stop_leg_quantity": None,
        "stop_leg_attempt": 2,
        "strategy": "Breakout",
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-entry-position-absent")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=30.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="could not confirm protection active within 5 attempts (stop_confirmed=False, target_confirmed=True)")
    entry.update(extra)
    return entry


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-pos-absent-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _register_target_user(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"target-pos-absent-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-pos-absent-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


# --- _check_position_absent_while_stuck ---------------------------------------


def test_position_still_held_does_not_flag(user_id):
    entry = _stuck_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": "30"}]), \
         patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        result = pluto_app._check_position_absent_while_stuck(user_id, CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    assert "position_absent_unexplained" not in entry
    mock_detail.assert_not_called()  # no need to check the stop leg if the position is still there


def test_position_absent_and_stop_cancelled_flags_and_alerts():
    entry = _stuck_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        result = pluto_app._check_position_absent_while_stuck("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is True
    assert entry["position_absent_unexplained"] is True
    assert entry["position_absent_evidence"]["stop_status"] == "CANCELLED"
    assert entry["position_absent_evidence"]["live_quantity"] == 0.0
    alert_payload = mock_alert.call_args.args[1]
    assert alert_payload["type"] == "position_absent_while_stuck"
    assert alert_payload["priority"] == "critical"


def test_position_absent_but_stop_filled_defers_not_this_functions_job():
    # A FILLED leg DOES explain the exit - this function's whole point is
    # only acting when NOTHING explains it.
    entry = _stuck_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 30, 30)):
        result = pluto_app._check_position_absent_while_stuck("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    assert "position_absent_unexplained" not in entry


def test_already_flagged_short_circuits_without_new_broker_calls():
    entry = _stuck_entry(position_absent_unexplained=True)
    with patch.object(pluto_app.webull_api, "get_account_positions") as mock_positions:
        result = pluto_app._check_position_absent_while_stuck("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is True
    mock_positions.assert_not_called()


def test_positions_lookup_failure_is_inconclusive_not_a_flag():
    entry = _stuck_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", side_effect=RuntimeError("network error")):
        result = pluto_app._check_position_absent_while_stuck("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    assert "position_absent_unexplained" not in entry


# --- wiring into the monitor: no more pointless stop-replacement attempts -----


def test_monitor_skips_resize_once_position_confirmed_absent(user_id):
    entry = _stuck_entry()
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place_stop, \
         patch.object(pluto_app, "time"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    mock_place_stop.assert_not_called()
    stored = list_overnight_orders(user_id)[0]
    assert stored["position_absent_unexplained"] is True
    assert stored["lifecycle_state"] == ol.PROTECTION_FAILED  # still stuck - genuinely unresolved, not silently closed


# --- the admin-only resolution route -------------------------------------------


def test_resolve_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8] + "z")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.post("/api/admin/reconcile-position-absent", json={})
    assert response.status_code == 403


def test_resolve_refuses_an_entry_that_is_not_flagged(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    target_id = _register_target_user(user_id[:8] + "a")
    entry = _stuck_entry()  # never flagged
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.post(
            "/api/admin/reconcile-position-absent",
            json={"user_id": target_id, "entry_client_order_id": "pt-entry-position-absent", "reason": "x", "confirmation": TICKER},
        )
    assert response.status_code == 400
    assert "not currently flagged" in response.get_json()["error"]["message"]


def test_resolve_requires_matching_confirmation(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    target_id = _register_target_user(user_id[:8] + "b")
    entry = _stuck_entry(position_absent_unexplained=True)
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.post(
            "/api/admin/reconcile-position-absent",
            json={"user_id": target_id, "entry_client_order_id": "pt-entry-position-absent", "reason": "reviewed manually", "confirmation": "WRONG"},
        )
    assert response.status_code == 400
    assert "Confirmation text" in response.get_json()["error"]["message"]


def test_resolve_refuses_if_the_fresh_recheck_finds_the_position_reappeared(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    target_id = _register_target_user(user_id[:8] + "c")
    entry = _stuck_entry(position_absent_unexplained=True)
    record_overnight_order(target_id, entry)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": "30"}]):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/reconcile-position-absent",
                json={"user_id": target_id, "entry_client_order_id": "pt-entry-position-absent", "reason": "reviewed manually", "confirmation": TICKER},
            )
    assert response.status_code == 400
    assert "not actually absent" in response.get_json()["error"]["message"]
    assert list_overnight_orders(target_id)[0]["lifecycle_state"] == ol.PROTECTION_FAILED  # untouched


def test_resolve_refuses_if_the_fresh_recheck_finds_the_stop_now_filled(user_id):
    admin_id = _make_admin(user_id[:8] + "d")
    target_id = _register_target_user(user_id[:8] + "d")
    entry = _stuck_entry(position_absent_unexplained=True)
    record_overnight_order(target_id, entry)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 30, 30)):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/reconcile-position-absent",
                json={"user_id": target_id, "entry_client_order_id": "pt-entry-position-absent", "reason": "reviewed manually", "confirmation": TICKER},
            )
    assert response.status_code == 400
    assert "DOES explain the exit" in response.get_json()["error"]["message"]


def test_resolve_end_to_end_closes_with_unknown_pnl_and_the_admins_reason(user_id):
    admin_id = _make_admin(user_id[:8] + "e")
    target_id = _register_target_user(user_id[:8] + "e")
    entry = _stuck_entry(position_absent_unexplained=True)
    record_overnight_order(target_id, entry)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/reconcile-position-absent",
                json={
                    "user_id": target_id, "entry_client_order_id": "pt-entry-position-absent",
                    "reason": "Confirmed via real Webull sandbox UI - position was manually closed outside this app.",
                    "confirmation": TICKER,
                },
            )

    assert response.status_code == 200
    body = response.get_json()["data"]["entry"]
    assert body["lifecycle_state"] == ol.CLOSED

    stored = list_overnight_orders(target_id)[0]
    assert stored["lifecycle_state"] == ol.CLOSED

    closed = list_closed_trades(target_id)
    assert len(closed) == 1
    assert closed[0]["pnl_status"] == "unknown_manual_reconciliation"
    assert closed[0]["gross_realized_pnl"] is None
    assert closed[0]["net_realized_pnl"] is None
    assert closed[0]["close_reason"] == "manual_reconciliation_position_absent"
    assert closed[0]["resolved_by_admin"] == admin_id
    assert "Confirmed via real Webull sandbox UI" in closed[0]["resolution_reason"]


# --- _check_position_absent_while_active ---------------------------------------
#
# Found live 2026-09-03: a real PLTR position was closed directly at the
# broker (not through either tracked leg filling) while PROTECTION_CONFIRMED_ACTIVE -
# the stop showed CANCELLED, not FILLED, so neither _reconcile_position_exit's
# fill-based detection nor _reconcile_closed_ticker_exit_orders' broader
# sweep (which also requires a FILLED leg) could ever explain it, leaving
# the entry reporting "still open" forever with no path to record its
# real close. Same detection discipline as the STUCK counterpart above,
# but deliberately does NOT freeze new entries - the broker confirms zero
# shares held, so there is no exposure left to protect.


def _active_entry(**extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "stop": 54.95,
        "target": 60.0,
        "trading_day": TRADING_DAY,
        "quantity": 30,
        "filled_quantity": 30.0,
        "average_entry_fill_price": 58.10,
        "stop_client_order_id": STOP_ID,
        "stop_leg_quantity": 30.0,
        "strategy": "Breakout",
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-entry-active-position-absent")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=30.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="2026-09-03T14:00:00+00:00")
    entry.update(extra)
    return entry


def test_active_position_still_held_does_not_flag():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": "30"}]), \
         patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        result = pluto_app._check_position_absent_while_active("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    assert "position_absent_unexplained" not in entry
    mock_detail.assert_not_called()


def test_active_position_absent_and_stop_cancelled_flags_and_alerts_but_does_not_freeze():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        result = pluto_app._check_position_absent_while_active("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is True
    assert entry["position_absent_unexplained"] is True
    assert entry["position_absent_evidence"]["stop_status"] == "CANCELLED"
    alert_payload = mock_alert.call_args.args[1]
    assert alert_payload["type"] == "position_absent_while_active"
    # Deliberately "normal", not "critical" - no exposure is actually at
    # risk (the broker confirms zero shares), unlike the STUCK counterpart.
    assert alert_payload["priority"] == "normal"


def test_active_position_absent_but_stop_filled_defers():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 30, 30)):
        result = pluto_app._check_position_absent_while_active("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    assert "position_absent_unexplained" not in entry


def test_active_already_flagged_short_circuits():
    entry = _active_entry(position_absent_unexplained=True)
    with patch.object(pluto_app.webull_api, "get_account_positions") as mock_positions:
        result = pluto_app._check_position_absent_while_active("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is True
    mock_positions.assert_not_called()


def test_active_short_direction_is_not_yet_supported():
    # Same unconfirmed-sign-convention reason as the STUCK counterpart.
    entry = _active_entry(direction="short")
    with patch.object(pluto_app.webull_api, "get_account_positions") as mock_positions:
        result = pluto_app._check_position_absent_while_active("user-1", CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    mock_positions.assert_not_called()


def test_reconcile_position_exit_flags_but_does_not_close_or_freeze(user_id):
    entry = _active_entry()
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=None):
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is False  # never auto-closes
    assert entry["position_absent_unexplained"] is True
    # Still PROTECTION_CONFIRMED_ACTIVE, not bounced into any frozen state -
    # this is a pure bookkeeping flag, not a safety condition.
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE


# --- the resolution route now also accepts PROTECTION_CONFIRMED_ACTIVE ---------


def test_resolve_accepts_a_flagged_active_entry_using_its_own_account_id(user_id):
    admin_id = _make_admin(user_id[:8] + "f")
    target_id = _register_target_user(user_id[:8] + "f")
    entry = _active_entry(position_absent_unexplained=True, account_id="acct-margin-should-be-used")
    record_overnight_order(target_id, entry)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts") as mock_accounts, \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]) as mock_positions, \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)) as mock_detail:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/reconcile-position-absent",
                json={
                    "user_id": target_id, "entry_client_order_id": "pt-entry-active-position-absent",
                    "reason": "Confirmed the user closed this manually outside the app.",
                    "confirmation": TICKER,
                },
            )

    assert response.status_code == 200
    body = response.get_json()["data"]["entry"]
    assert body["lifecycle_state"] == ol.CLOSED
    # Re-verified against the entry's OWN stored account_id - never
    # re-derived the cash account via get_paper_accounts.
    mock_accounts.assert_not_called()
    mock_positions.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], "acct-margin-should-be-used")
    mock_detail.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], "acct-margin-should-be-used", STOP_ID)

    closed = list_closed_trades(target_id)
    assert len(closed) == 1
    assert closed[0]["side"] == "BUY"


def test_resolve_records_sell_side_for_a_flagged_short_entry(user_id):
    admin_id = _make_admin(user_id[:8] + "g")
    target_id = _register_target_user(user_id[:8] + "g")
    entry = _active_entry(position_absent_unexplained=True, account_id=ACCOUNT_ID, direction="short")
    record_overnight_order(target_id, entry)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 30, 0)):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/reconcile-position-absent",
                json={
                    "user_id": target_id, "entry_client_order_id": "pt-entry-active-position-absent",
                    "reason": "Confirmed the user covered this short manually outside the app.",
                    "confirmation": TICKER,
                },
            )

    assert response.status_code == 200
    closed = list_closed_trades(target_id)
    assert len(closed) == 1
    assert closed[0]["side"] == "SELL"
