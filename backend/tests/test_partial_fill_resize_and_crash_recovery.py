from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order
from webull_stop_orders import get_exit_orders

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "AAPL"
TRADING_DAY = "2026-08-11"
ENTRY_CLIENT_ORDER_ID = "pt-entry-1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _fresh_entry(**extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": TRADING_DAY,
        "planned_risk_dollars": 500.0,
        "quantity": 10,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=ENTRY_CLIENT_ORDER_ID)
    entry.update(extra)
    return entry


class _StatefulBroker:
    """A minimal in-memory broker double for the entry order plus every
    protective leg ever placed, keyed by client_order_id - realistic
    enough to exercise the REAL cancel-confirm-replace and
    confirm-both-legs-active logic across multiple simulated monitor
    ticks, not just a single canned response. cancel_order marks a leg
    CANCELLED for real (not just a call assertion) so a re-check via
    get_order_detail after cancelling correctly reflects it - the same
    "stateful, not stateless" requirement this session's other
    sibling-cancellation tests were rebuilt around."""

    def __init__(self, entry_status: str, entry_total: float, entry_filled: float):
        self.entry_status = entry_status
        self.entry_total = entry_total
        self.entry_filled = entry_filled
        self.legs: dict[str, dict] = {}  # client_order_id -> {status, total_quantity, filled_quantity}
        self.place_stop_calls = []
        self.place_target_calls = []
        self.cancel_calls = []

    def get_order_detail(self, app_key, app_secret, account_id, client_order_id):
        if client_order_id == ENTRY_CLIENT_ORDER_ID:
            return _order_detail(self.entry_status, self.entry_total, self.entry_filled)
        leg = self.legs.get(client_order_id)
        if leg is None:
            return _order_detail("UNKNOWN", 0, 0)
        return _order_detail(leg["status"], leg["total_quantity"], leg["filled_quantity"])

    def cancel_order(self, app_key, app_secret, account_id, client_order_id):
        self.cancel_calls.append(client_order_id)
        if client_order_id in self.legs:
            self.legs[client_order_id]["status"] = "CANCELLED"

    def place_stop_loss_order(self, *, app_key, app_secret, account_id, symbol, quantity, stop_price, client_order_id):
        self.place_stop_calls.append(client_order_id)
        self.legs[client_order_id] = {"status": "SUBMITTED", "total_quantity": quantity, "filled_quantity": 0}
        return {"client_order_id": client_order_id}

    def place_take_profit_order(self, *, app_key, app_secret, account_id, symbol, quantity, target_price, trading_session, client_order_id):
        self.place_target_calls.append(client_order_id)
        self.legs[client_order_id] = {"status": "SUBMITTED", "total_quantity": quantity, "filled_quantity": 0}
        return {"client_order_id": client_order_id}


@contextlib.contextmanager
def _patched(broker):
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=broker.place_stop_loss_order), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=broker.place_take_profit_order), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        yield


# --- Required test: 4-of-10 filled+protected, then all 10 filled ------------


def test_partial_fill_then_full_fill_resizes_protection_without_duplicate_exit_orders(user_id):
    entry = _fresh_entry()
    record_overnight_order(user_id, entry)

    # Tick 1: entry order shows 4 of 10 filled so far.
    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=4)
    with _patched(broker):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert stored["filled_quantity"] == 4.0
    assert stored["stop_leg_quantity"] == 4.0
    assert stored["target_leg_quantity"] == 4.0
    stop_id_v1 = stored["stop_client_order_id"]
    target_id_v1 = stored["target_client_order_id"]
    assert stop_id_v1 == ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    assert target_id_v1 == ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "target", attempt=1)

    exit_orders_after_tick_1 = get_exit_orders(user_id, TICKER)
    assert {o["id"] for o in exit_orders_after_tick_1} == {stop_id_v1, target_id_v1}
    assert len(exit_orders_after_tick_1) == 2  # exactly one live stop, one live target - no extras yet

    # Tick 2: the remaining 6 shares fill - the entry is now fully FILLED.
    # Reuse the SAME broker (it remembers the v1 legs it already placed and
    # cancelled), just advance its entry-order state.
    broker.entry_status = "FILLED"
    broker.entry_filled = 10
    with _patched(broker):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert stored["filled_quantity"] == 10.0
    assert stored["entry_order_terminal"] is True
    assert stored["stop_leg_quantity"] == 10.0
    assert stored["target_leg_quantity"] == 10.0
    stop_id_v2 = stored["stop_client_order_id"]
    target_id_v2 = stored["target_client_order_id"]
    assert stop_id_v2 == ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=2)
    assert target_id_v2 == ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "target", attempt=2)
    assert stop_id_v2 != stop_id_v1
    assert target_id_v2 != target_id_v1

    # The v1 legs were genuinely cancelled at the broker (not just dropped
    # from tracking) before the v2 legs were placed.
    assert stop_id_v1 in broker.cancel_calls
    assert target_id_v1 in broker.cancel_calls
    assert broker.legs[stop_id_v1]["status"] == "CANCELLED"
    assert broker.legs[target_id_v1]["status"] == "CANCELLED"
    assert broker.legs[stop_id_v2]["total_quantity"] == 10.0
    assert broker.legs[target_id_v2]["total_quantity"] == 10.0

    # The crux of the required proof: exactly ONE live stop and ONE live
    # target are tracked after the resize - the v1 legs are gone from
    # tracking, not just superseded/left dangling alongside the v2 ones.
    exit_orders_after_tick_2 = get_exit_orders(user_id, TICKER)
    assert len(exit_orders_after_tick_2) == 2
    assert {o["id"] for o in exit_orders_after_tick_2} == {stop_id_v2, target_id_v2}
    assert stop_id_v1 not in {o["id"] for o in exit_orders_after_tick_2}
    assert target_id_v1 not in {o["id"] for o in exit_orders_after_tick_2}


# --- Crash boundaries during protective-leg resizing -------------------------


def test_resize_retains_old_leg_tracking_when_the_new_placement_crashes(user_id):
    """Simulates a crash (or a broker rejection) AFTER the old leg is
    cancelled-and-confirmed but BEFORE the new, larger leg is durably
    placed. The old leg's tracking must NOT have been dropped yet at this
    point (see _reconcile_protective_leg_quantity: pop_exit_order_by_id is
    only called after the new leg succeeds), so the position is never left
    with zero tracked stop-loss protection even mid-resize."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=10)
    broker.legs[stop_id_v1] = {"status": "SUBMITTED", "total_quantity": 4.0, "filled_quantity": 0}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("broker rejected the new order")):
        try:
            pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
            assert False, "expected the placement failure to raise"
        except RuntimeError:
            pass

    # Old leg was genuinely cancelled at the broker...
    assert stop_id_v1 in broker.cancel_calls
    # ...but tracking was NOT dropped, since the replacement never landed -
    # the app still believes (and must keep alerting/retrying for) this id.
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert stop_id_v1 in tracked_ids
    assert entry.get("stop_order_error")
    # entry bookkeeping for the leg is unchanged - no half-applied resize.
    assert entry["stop_client_order_id"] == stop_id_v1
    assert entry["stop_leg_quantity"] == 4.0


def test_resize_replacement_failure_after_cancel_fires_a_critical_alert(user_id):
    """Distinct from an ordinary no-progress stall (which only becomes
    visible after MONITOR_STUCK_FREEZE_SECONDS) - a leg that's genuinely
    unprotected RIGHT NOW because its cancelled predecessor's replacement
    failed to place must alert immediately, at critical priority."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=10)
    broker.legs[stop_id_v1] = {"status": "SUBMITTED", "total_quantity": 4.0, "filled_quantity": 0}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("broker rejected the new order")):
        try:
            pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
        except RuntimeError:
            pass

    from alerts import load_manual_alerts
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical"]
    assert len(critical_alerts) == 1
    assert critical_alerts[0]["type"] == "resize_replacement_failed_after_cancel"
    assert "unprotected" in critical_alerts[0]["message"].lower()

    # Immediate, persisted freeze signal - not merely a critical alert.
    assert entry["stop_protection_gap"] is True
    from autonomy.overnight_orders import record_overnight_order
    record_overnight_order(user_id, entry)
    assert pluto_app._has_active_protection_gap_locally(user_id) is True
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


def test_protection_gap_persists_across_repeated_failed_ticks_until_confirmed(user_id):
    """A single immediate in-function retry is not "enough retrying" on
    its own - the ~60s scheduler must keep retrying via its own normal
    resumable-state cadence, and the freeze must survive every one of
    those ticks failing, clearing ONLY once the leg is genuinely
    confirmed active - never merely because a placement attempt
    succeeded."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=10)
    broker.legs[stop_id_v1] = {"status": "SUBMITTED", "total_quantity": 4.0, "filled_quantity": 0}

    # Tick 1: cancel-confirm succeeds, replacement placement fails (even
    # after the one immediate retry) - gap flag set.
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("still down")):
        for _ in range(3):  # simulates 3 more scheduler ticks, all still failing
            try:
                pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
            except RuntimeError:
                pass
            assert entry["stop_protection_gap"] is True  # survives every failed tick, not just the first

    # A later tick where placement finally SUCCEEDS - gap must NOT clear
    # yet, since success-at-placement is not the same as confirmed active.
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=broker.place_stop_loss_order):
        pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
    assert entry["stop_protection_gap"] is True  # NOT cleared by placement success alone

    # Only _confirm_and_finalize_protection genuinely confirming the leg
    # active clears it.
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app, "time"):
        pluto_app._confirm_and_finalize_protection(user_id, CREDS, ACCOUNT_ID, TICKER, entry, 10.0, 95.0, 0)
    assert entry.get("stop_protection_gap") is None
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE


def test_resize_placement_immediate_retry_succeeds_on_the_second_attempt(user_id):
    """A transient placement failure (rate limit, brief network blip) must
    not be treated as a permanent one before at least a single immediate
    retry within the same call - this proves the retry actually happens,
    not just that ITS eventual failure is handled."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=10)
    broker.legs[stop_id_v1] = {"status": "SUBMITTED", "total_quantity": 4.0, "filled_quantity": 0}

    call_count = {"n": 0}
    real_place = broker.place_stop_loss_order

    def _flaky_place(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient rate limit")
        return real_place(*args, **kwargs)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=_flaky_place):
        pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)

    assert call_count["n"] == 2  # failed once, retried immediately, succeeded
    stop_id_v2 = entry["stop_client_order_id"]
    assert stop_id_v2 != stop_id_v1
    assert entry["stop_leg_quantity"] == 10.0
    assert entry.get("stop_order_error") is None
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert tracked_ids == {stop_id_v2}  # old id dropped, new one tracked - exactly one, no duplicate
    from alerts import load_manual_alerts
    assert [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical"] == []  # never needed to alert


def test_resize_recovers_cleanly_on_retry_after_a_prior_placement_crash(user_id):
    """Continuation of the crash above - the monitor's NEXT tick calls
    _reconcile_protective_leg_quantity again for the same leg. The old
    (already-cancelled) leg must be recognized as gone without attempting
    to cancel it a second time, and the retry must land exactly ONE new
    tracked leg - never a duplicate, and never permanently stuck just
    because the first attempt failed."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    broker = _StatefulBroker(entry_status="PARTIAL FILLED", entry_total=10, entry_filled=10)
    # The old leg is ALREADY cancelled - as it would be after the crashed
    # attempt above cancelled it before the placement itself failed.
    broker.legs[stop_id_v1] = {"status": "CANCELLED", "total_quantity": 4.0, "filled_quantity": 0}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=broker.cancel_order), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=broker.place_stop_loss_order):
        pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)

    # Already-cancelled, so it must not be cancelled again.
    assert stop_id_v1 not in broker.cancel_calls
    stop_id_v2 = entry["stop_client_order_id"]
    assert stop_id_v2 == ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=2)
    assert entry["stop_leg_quantity"] == 10.0
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert tracked_ids == {stop_id_v2}  # exactly one - the old id is gone, no duplicate


def test_resize_retains_tracking_when_the_old_legs_cancellation_call_itself_fails(user_id):
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 4, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=RuntimeError("broker unreachable")):
        try:
            pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
            assert False, "expected the cancel failure to raise"
        except RuntimeError:
            pass

    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert stop_id_v1 in tracked_ids
    assert entry["stop_client_order_id"] == stop_id_v1
    assert entry["stop_leg_quantity"] == 4.0


def test_resize_retains_tracking_when_cancellation_cannot_be_confirmed(user_id):
    """cancel_order itself does not raise, but the immediate re-check still
    shows the old leg active (the cancel hasn't actually taken effect at
    the broker yet) - must not proceed to place a new leg while the old
    one might still execute."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 4, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value=None) as mock_cancel, \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        try:
            pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
            assert False, "expected the unconfirmed cancellation to raise"
        except RuntimeError:
            pass

    mock_cancel.assert_called_once()
    mock_place.assert_not_called()  # never places a new leg while the old one's status is unresolved
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert stop_id_v1 in tracked_ids


def test_resize_aborts_and_alerts_if_the_old_leg_turns_out_already_filled(user_id):
    """The old leg executed before it could be cancelled - shares already
    left through it. Must NOT place a full-size replacement (that would
    over-protect an already-partially-exited position) - this is left for
    _reconcile_position_exit to pick up as a genuine exit instead."""
    entry = _fresh_entry()
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=4.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    entry["entry_order_terminal"] = False
    stop_id_v1 = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop", attempt=1)
    entry["stop_client_order_id"] = stop_id_v1
    entry["stop_leg_quantity"] = 4.0
    entry["stop_leg_attempt"] = 1
    from webull_stop_orders import record_exit_order
    record_exit_order(user_id, TICKER, stop_id_v1, "stop")

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 4, 4)), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel, \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        try:
            pluto_app._reconcile_protective_leg_quantity(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", 10.0, 95.0)
            assert False, "expected an already-filled old leg to raise"
        except RuntimeError:
            pass

    mock_cancel.assert_not_called()  # a filled order can't be cancelled - never attempted
    mock_place.assert_not_called()
    tracked_ids = {o["id"] for o in get_exit_orders(user_id, TICKER)}
    assert stop_id_v1 in tracked_ids  # still tracked - _reconcile_position_exit handles it as an exit next


# --- Continuous broker failure eventually freezes new entries ---------------


def test_continuous_broker_failure_through_the_monitor_eventually_freezes_new_entries(user_id):
    entry = _fresh_entry()
    record_overnight_order(user_id, entry)

    t0 = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t0):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    stored = list_overnight_orders(user_id)[0]
    first_failure_at = stored["monitor_first_failure_at"]
    assert first_failure_at
    assert stored["monitor_attempt_count"] == 1
    assert stored["lifecycle_state"] == ol.ENTRY_SUBMITTED  # no progress at all - broker was never reachable

    t1 = t0 + timedelta(seconds=600)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t1):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
        assert pluto_app._has_stuck_transitional_orders_locally(user_id) is False  # only 600s so far - not stuck yet
    stored = list_overnight_orders(user_id)[0]
    # The stall's START time is preserved across every subsequent failure -
    # this is what makes it a genuinely CONTINUOUS failure, not a series of
    # independent ones each resetting the clock.
    assert stored["monitor_first_failure_at"] == first_failure_at
    assert stored["monitor_attempt_count"] == 2
    assert "broker unreachable" in stored["monitor_last_error"]

    t2 = t0 + timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS + 60)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t2):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
        assert pluto_app._has_stuck_transitional_orders_locally(user_id) is True
    stored = list_overnight_orders(user_id)[0]
    assert stored["monitor_first_failure_at"] == first_failure_at
    assert stored["monitor_attempt_count"] == 3


def test_dismissing_the_stuck_monitor_alert_does_not_release_the_freeze(user_id):
    """The literal test the review explicitly required: a critical
    monitor_stuck_freeze alert fires once an entry crosses
    MONITOR_STUCK_FREEZE_SECONDS with no progress, and dismissing that
    alert from the notifications drawer must have ZERO effect on the
    actual freeze (_has_stuck_transitional_orders_locally /
    _has_unresolved_ambiguous_submission_locally) - dismissal only
    touches alerts.json's dismissed-ids list, which the freeze predicate
    never reads; only genuine forward progress on the entry (or manual
    intervention) can lift it."""
    entry = _fresh_entry()
    record_overnight_order(user_id, entry)

    t0 = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)
    t_stuck = t0 + timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS + 60)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t0):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t_stuck):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    assert pluto_app._has_stuck_transitional_orders_locally(user_id) is True
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    from alerts import dismiss_alert, get_alerts_snapshot, load_manual_alerts
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("priority") == "critical" and a.get("type") == "monitor_stuck_freeze"]
    assert len(critical_alerts) == 1
    alert_id = critical_alerts[0]["id"]

    dismiss_alert(user_id, alert_id)

    # The alert is gone from what the user actually sees (dismissed_ids
    # filters it out of get_alerts_snapshot, the drawer's own data
    # source) - dismiss_alert itself only writes a SEPARATE
    # dismissed_alerts.json, never touching alerts.json/overnight_orders.json.
    visible_after_dismiss = [a for a in get_alerts_snapshot(user_id, []) if a["id"] == alert_id]
    assert visible_after_dismiss == []

    # ...but the freeze itself is completely untouched - still frozen.
    with patch.object(pluto_app, "_now_utc", return_value=t_stuck):
        assert pluto_app._has_stuck_transitional_orders_locally(user_id) is True
        assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True
    stored = list_overnight_orders(user_id)[0]
    assert stored["monitor_first_failure_at"]  # still stamped - dismissal never touched overnight_orders.json


def test_stuck_monitor_alert_is_one_shot_across_repeated_ticks(user_id):
    """Mirrors _alert_admins_fast_monitor_unhealthy_if_needed's own
    one-shot guarantee - repeated monitor ticks while still stuck must not
    spam a new alert every pass."""
    entry = _fresh_entry()
    record_overnight_order(user_id, entry)
    t0 = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)
    t_stuck = t0 + timedelta(seconds=pluto_app.MONITOR_STUCK_FREEZE_SECONDS + 60)
    t_still_stuck = t_stuck + timedelta(seconds=120)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t0):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t_stuck):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("broker unreachable")), \
         patch.object(pluto_app, "_now_utc", return_value=t_still_stuck):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    from alerts import load_manual_alerts
    stuck_alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "monitor_stuck_freeze"]
    assert len(stuck_alerts) == 1  # not one per tick
