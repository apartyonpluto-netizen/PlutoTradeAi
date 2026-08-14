from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import record_overnight_order
from webull_stop_orders import get_exit_orders, record_exit_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "AAPL"
TRADING_DAY = "2026-08-11"
ENTRY_CLIENT_ORDER_ID = "pt-exit-1"
STOP_ID = "stop-cid-1"
TARGET_ID = "target-cid-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _protected_entry(user_id: str, **extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": TRADING_DAY,
        "planned_risk_dollars": 500.0,
        "quantity": 10,
        "logged_at": "2026-08-11T14:00:00+00:00",
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=ENTRY_CLIENT_ORDER_ID)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="2026-08-11T14:01:00+00:00")
    entry["entry_order_terminal"] = True
    entry["stop_client_order_id"] = STOP_ID
    entry["stop_leg_quantity"] = 10.0
    entry["stop_leg_attempt"] = 1
    entry["target_client_order_id"] = TARGET_ID
    entry["target_leg_quantity"] = 10.0
    entry["target_leg_attempt"] = 1
    # As _reconcile_entry_fill_and_protection would have already stamped
    # this from the entry's own broker-reported average fill price by the
    # time protection is confirmed - see order_lifecycle.summarize_fill.
    entry["average_entry_fill_price"] = 100.0
    entry.update(extra)
    record_exit_order(user_id, TICKER, STOP_ID, "stop")
    record_exit_order(user_id, TICKER, TARGET_ID, "take_profit")
    return entry


def _by_id(details: dict):
    def _detail(app_key, app_secret, account_id, client_order_id):
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))
    return _detail


# --- No exit yet --------------------------------------------------------------


def test_neither_leg_exited_is_a_noop(user_id):
    entry = _protected_entry(user_id)
    details = {STOP_ID: _order_detail("SUBMITTED", 10, 0), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_by_id(details)):
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert exited is False
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert list_closed_trades(user_id) == []


# --- Full exit via each leg ----------------------------------------------------


def test_stop_fill_closes_the_trade_cancels_target_and_records_pnl(user_id):
    entry = _protected_entry(user_id)
    cancelled = set()
    details = {STOP_ID: _order_detail("FILLED", 10, 10, average_price=95.0), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == TARGET_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel) as mock_cancel:
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, TARGET_ID)
    assert entry["lifecycle_state"] == ol.CLOSED

    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["trade_id"] == ENTRY_CLIENT_ORDER_ID
    assert record["ticker"] == TICKER
    assert record["exit_type"] == "stop"
    assert record["exited_quantity"] == 10.0
    assert record["average_exit_price"] == 95.0
    assert record["average_entry_price"] == 100.0
    # Loss: sold 10 shares at 95 vs bought at 100 -> -5/share * 10 = -50.
    assert record["net_realized_pnl"] == -50.0
    assert record["close_reason"] == "stop_filled"
    assert record["pnl_status"] == "complete"

    # The cancelled sibling (target) is no longer tracked. The exited leg
    # itself (stop, now FILLED - terminal, nothing left to cancel) is left
    # tracked here; the broader position-absence sweep
    # (_reconcile_closed_ticker_exit_orders) is what eventually clears a
    # terminal leg's tracking entry, not this function.
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert tracked_ids == {STOP_ID}


def test_target_fill_closes_the_trade_cancels_stop_and_records_pnl(user_id):
    entry = _protected_entry(user_id)
    cancelled = set()
    details = {TARGET_ID: _order_detail("FILLED", 10, 10, average_price=110.0), STOP_ID: _order_detail("SUBMITTED", 10, 0)}

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel) as mock_cancel:
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, STOP_ID)
    assert entry["lifecycle_state"] == ol.CLOSED

    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["exit_type"] == "target"
    assert record["average_exit_price"] == 110.0
    # Gain: sold 10 shares at 110 vs bought at 100 -> +10/share * 10 = +100.
    assert record["net_realized_pnl"] == 100.0
    assert record["close_reason"] == "target_filled"
    assert record["pnl_status"] == "complete"


# --- Missing fill price leaves P&L incomplete, never estimated ------------------


def test_missing_exit_fill_price_leaves_pnl_incomplete_not_estimated(user_id):
    """The exited leg's broker response has NO recognized fill-price field
    this poll - P&L must be left None/incomplete, never silently computed
    from entry.get("stop")/entry.get("target") (the app's own PLANNED
    price for that leg) as a stand-in. The trade is still closed - the
    exit itself is conclusive regardless of whether its price was
    reported - only the P&L figure is marked incomplete."""
    entry = _protected_entry(user_id)
    cancelled = set()
    # FILLED, but with NO avg_filled_price key at all.
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == TARGET_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.CLOSED  # the exit itself is still conclusive and closes the trade

    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["average_exit_price"] is None
    assert record["average_entry_price"] == 100.0  # this one WAS known - only the exit leg's price is missing
    assert record["gross_realized_pnl"] is None
    assert record["net_realized_pnl"] is None
    assert record["pnl_status"] == "incomplete_missing_fill_price"


def test_missing_entry_fill_price_also_leaves_pnl_incomplete(user_id):
    entry = _protected_entry(user_id, average_entry_fill_price=None)
    cancelled = set()
    details = {STOP_ID: _order_detail("FILLED", 10, 10, average_price=95.0), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == TARGET_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    record = list_closed_trades(user_id)[0]
    assert record["average_entry_price"] is None
    assert record["average_exit_price"] == 95.0  # this one WAS known
    assert record["net_realized_pnl"] is None
    assert record["pnl_status"] == "incomplete_missing_fill_price"


# --- Partial exit ---------------------------------------------------------------


def test_partial_stop_fill_preserves_and_reprotects_the_remainder(user_id):
    """4 of 10 shares exit through the stop - the remaining 6 must stay
    tracked and protected (resized target), NOT closed out entirely."""
    entry = _protected_entry(user_id)
    broker_legs = {
        STOP_ID: {"status": "PARTIAL FILLED", "total_quantity": 10, "filled_quantity": 4},
        TARGET_ID: {"status": "SUBMITTED", "total_quantity": 10, "filled_quantity": 0},
    }

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        leg = broker_legs.get(client_order_id)
        if leg is None:
            return _order_detail("UNKNOWN", 0, 0)
        return _order_detail(leg["status"], leg["total_quantity"], leg["filled_quantity"])

    def _cancel(app_key, app_secret, account_id, client_order_id):
        broker_legs[client_order_id]["status"] = "CANCELLED"

    def _place_target(*, app_key, app_secret, account_id, symbol, quantity, target_price, trading_session, client_order_id):
        broker_legs[client_order_id] = {"status": "SUBMITTED", "total_quantity": quantity, "filled_quantity": 0}
        return {"client_order_id": client_order_id}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=_place_target), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # NOT closed - still an open remainder
    assert entry["filled_quantity"] == 6.0
    assert entry["target_leg_quantity"] == 6.0
    assert list_closed_trades(user_id) == []  # no full exit yet - nothing to record as closed

    # Old target leg is gone from tracking; a new, resized one replaces it.
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert TARGET_ID not in tracked_ids
    new_target_id = entry["target_client_order_id"]
    assert new_target_id != TARGET_ID
    assert new_target_id in tracked_ids
    assert STOP_ID in tracked_ids  # the exited leg's own order is untouched - broker manages its remainder


# --- Ambiguous double-fill -------------------------------------------------------


def test_both_legs_filled_raises_freezes_and_never_places_a_corrective_order(user_id):
    """Automatic corrective trading is disabled entirely (see
    _reconcile_both_legs_filled_emergency's docstring) - even an
    apparent short must NOT trigger an automatic BUY. Must instead:
    freeze new entries immediately, persist durable evidence, fire a
    critical alert, and leave the trade open (not falsely closed)."""
    entry = _protected_entry(user_id)
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("FILLED", 10, 10)}
    short_position = {"symbol": TICKER, "quantity": -10, "last_price": 96.0}
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_by_id(details)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel, \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[short_position]), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_buy:
        try:
            pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
            assert False, "expected an ambiguous double-fill to raise"
        except RuntimeError:
            pass

    mock_cancel.assert_not_called()
    mock_buy.assert_not_called()  # NEVER an automatic corrective order, even for an apparent short
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # untouched, not falsely closed
    assert list_closed_trades(user_id) == []

    # Immediate freeze - not deferred to the 30-minute stuck timer. The
    # freeze CHECK functions read persisted storage, so persist the
    # mutated entry first (as the real caller, _monitor_transitional_orders,
    # would via replace_overnight_orders after this call returns).
    assert entry["ambiguous_exit_unresolved"] is True
    record_overnight_order(user_id, entry)
    assert pluto_app._has_active_protection_gap_locally(user_id) is True
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    # Durable evidence, including the broker position snapshot.
    evidence = entry["ambiguous_exit_evidence"]
    assert evidence["broker_position_snapshot"]["quantity"] == -10
    assert evidence["recorded_at"]

    from alerts import load_manual_alerts
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical"]
    assert len(critical_alerts) == 1
    assert "disabled" in critical_alerts[0]["message"].lower()
    assert "no order has been placed" in critical_alerts[0]["message"].lower()


def test_both_legs_filled_position_lookup_failure_still_freezes_and_alerts_critical(user_id):
    entry = _protected_entry(user_id)
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("FILLED", 10, 10)}
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_by_id(details)), \
         patch.object(pluto_app.webull_api, "get_account_positions", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_buy:
        try:
            pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
            assert False, "expected this to still raise"
        except RuntimeError:
            pass
    mock_buy.assert_not_called()
    assert entry["ambiguous_exit_unresolved"] is True
    assert entry["ambiguous_exit_evidence"]["position_lookup_error"] == "broker unreachable"
    from alerts import load_manual_alerts
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical"]
    assert len(critical_alerts) == 1


def test_both_legs_filled_keeps_reconciling_with_fresh_evidence_every_tick(user_id):
    """Not a one-shot dead end - calling this again (as the monitor would
    on its next ~60s pass) must refresh the evidence, not just leave the
    first tick's snapshot stale."""
    entry = _protected_entry(user_id)
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("FILLED", 10, 10)}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_by_id(details)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": -10}]):
        for _ in range(3):
            try:
                pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
            except RuntimeError:
                pass

    assert entry["ambiguous_exit_unresolved"] is True
    from alerts import load_manual_alerts
    # add_manual_alert's own content-hash dedup collapses identical
    # repeated alerts to one - proves the SAME condition doesn't spam,
    # while the entry itself was genuinely re-examined 3 times (no
    # exception escaped the loop above).
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical"]
    assert len(critical_alerts) == 1


def test_a_prior_ambiguous_flag_clears_once_a_later_tick_shows_a_conclusive_single_leg(user_id):
    """A transient double-read on one tick must not permanently freeze the
    account if a later tick's fresh evidence is conclusive after all."""
    entry = _protected_entry(user_id)
    entry["ambiguous_exit_unresolved"] = True  # as if a prior tick flagged this
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}
    cancelled = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == TARGET_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.CLOSED
    assert entry.get("ambiguous_exit_unresolved") is None  # cleared - this tick's evidence was conclusive


# --- Idempotent closed-trade recording across a simulated restart ---------------


def test_closed_trade_recording_is_idempotent_if_reconciled_twice(user_id):
    """Models a crash between record_closed_trade landing and the CLOSED
    lifecycle transition being durably persisted back to overnight_orders
    storage (that write happens in the CALLER, _monitor_transitional_orders,
    after this function returns) - the next tick re-reconciles the SAME
    exit from the same starting entry dict. Must UPSERT the same closed
    trade record, never create a second one."""
    entry = _protected_entry(user_id)
    cancelled = set()
    details = {STOP_ID: _order_detail("FILLED", 10, 10), TARGET_ID: _order_detail("SUBMITTED", 10, 0)}

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == TARGET_ID and client_order_id in cancelled:
            return _order_detail("CANCELLED", 10, 0)
        return details.get(client_order_id, _order_detail("UNKNOWN", 0, 0))

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
        assert len(list_closed_trades(user_id)) == 1

        # Simulate the crash: re-run reconciliation from the SAME starting
        # entry dict (as a fresh _monitor_transitional_orders pass would
        # after a restart finds this entry still PROTECTION_CONFIRMED_ACTIVE
        # in storage, because the CLOSED write never landed last time).
        entry_retry = _protected_entry(user_id)
        entry_retry["lifecycle_state"] = ol.PROTECTION_CONFIRMED_ACTIVE
        pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry_retry)

    closed = list_closed_trades(user_id)
    assert len(closed) == 1  # still exactly one record - upserted, not duplicated
    assert closed[0]["trade_id"] == ENTRY_CLIENT_ORDER_ID
