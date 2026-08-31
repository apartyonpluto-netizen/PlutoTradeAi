from __future__ import annotations

import contextlib
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

"""Found live 2026-08-31: a real SLB entry's stop leg ended up CANCELLED at
the broker (by some means outside this app's own resize/exit paths - never
confirmed, only the resulting state) while its tracked stop_leg_quantity
never changed. _reconcile_protective_leg_quantity's own "already correctly
sized" short-circuit compares quantity only, never broker status - so
nothing ever re-placed it. The entry polled the SAME dead stop order every
single monitor pass (203+ attempts, 4+ hours) without ever attempting a
replacement, correctly detecting "not confirmed active" each time via
_confirm_and_finalize_protection but never acting on WHY. These tests
prove the actual fix: _confirm_and_finalize_protection now distinguishes a
genuinely CONFIRMED-dead leg (a real, successful lookup that came back
CANCELLED/FAILED) from a merely unconfirmed one, and clears
stop_leg_quantity in that case so the next pass's resize actually replaces
it - self-healing within two monitor ticks, never touching the tracked
client_order_id itself until a real replacement is durably placed."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "SLB"
TRADING_DAY = "2026-08-31"
ENTRY_CLIENT_ORDER_ID = "pt-entry-stale-stop"
STOP_ID_V1 = "stop-cid-v1"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _stuck_entry() -> dict:
    entry: dict = {
        "ticker": TICKER,
        "limit_price": 58.18,
        "stop": 54.95,
        "target": 60.0,
        "trading_day": TRADING_DAY,
        "planned_risk_dollars": 500.0,
        "quantity": 30,
        "filled_quantity": 30.0,
        "average_entry_fill_price": 58.10,
        "entry_order_terminal": True,
        "stop_client_order_id": STOP_ID_V1,
        "stop_leg_quantity": 30.0,
        "stop_leg_attempt": 1,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=ENTRY_CLIENT_ORDER_ID)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=30.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(
        entry, ol.PROTECTION_FAILED,
        error="could not confirm protection active within 5 attempts (stop_confirmed=False, target_confirmed=True)",
    )
    return entry


class _StatefulBroker:
    def __init__(self, entry_status: str, entry_total: float, entry_filled: float, stop_status: str):
        self.entry_status = entry_status
        self.entry_total = entry_total
        self.entry_filled = entry_filled
        self.legs: dict[str, dict] = {STOP_ID_V1: {"status": stop_status, "total_quantity": 30, "filled_quantity": 0}}
        self.place_stop_calls: list[str] = []

    def get_order_detail(self, app_key, app_secret, account_id, client_order_id):
        if client_order_id == ENTRY_CLIENT_ORDER_ID:
            return _order_detail(self.entry_status, self.entry_total, self.entry_filled)
        leg = self.legs.get(client_order_id)
        if leg is None:
            return _order_detail("UNKNOWN", 0, 0)
        return _order_detail(leg["status"], leg["total_quantity"], leg["filled_quantity"])

    def place_stop_loss_order(self, *, app_key, app_secret, account_id, symbol, quantity, stop_price, client_order_id):
        self.place_stop_calls.append(client_order_id)
        self.legs[client_order_id] = {"status": "SUBMITTED", "total_quantity": quantity, "filled_quantity": 0}
        return {"client_order_id": client_order_id}

    def place_take_profit_order(self, **kwargs):
        raise AssertionError("the target leg must never be placed as a broker order")


@contextlib.contextmanager
def _patched(broker):
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=broker.get_order_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=broker.place_stop_loss_order), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=broker.place_take_profit_order), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=None), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        yield


def test_a_confirmed_cancelled_stop_is_replaced_within_two_monitor_ticks(user_id):
    entry = _stuck_entry()
    record_overnight_order(user_id, entry)
    broker = _StatefulBroker(entry_status="FILLED", entry_total=30, entry_filled=30, stop_status="CANCELLED")

    # Tick 1: confirms the stop is genuinely dead (a real, successful
    # lookup returning CANCELLED) and clears stop_leg_quantity - but does
    # NOT yet replace it (this tick's own resize already ran before that
    # detection happened).
    with _patched(broker):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    assert broker.place_stop_calls == []
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_FAILED
    assert stored.get("stop_leg_quantity") is None
    assert stored["stop_client_order_id"] == STOP_ID_V1  # old id retained until a replacement is durably placed
    assert "stop leg found CANCELLED/FAILED" in stored["error"]

    # Tick 2: stop_leg_quantity no longer matches (None != 30), so resize
    # now actually places a fresh stop - and this time confirmation finds
    # it genuinely active.
    with _patched(broker):
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    assert len(broker.place_stop_calls) == 1
    new_stop_id = broker.place_stop_calls[0]
    assert new_stop_id != STOP_ID_V1
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert stored["stop_client_order_id"] == new_stop_id
    assert stored["stop_leg_quantity"] == 30.0


def test_a_stop_that_merely_could_not_be_looked_up_is_left_alone_not_replaced():
    # The critical distinction this fix depends on: a transient lookup
    # FAILURE (network blip, timeout) must NOT be treated the same as a
    # confirmed CANCELLED/FAILED status - that would mean replacing a stop
    # that might still be perfectly healthy, based on nothing but a failed
    # read. Only a real, successful broker answer of CANCELLED/FAILED
    # triggers a replacement.
    entry = _stuck_entry()
    ol.transition(entry, ol.PROTECTION_PENDING)  # _confirm_and_finalize_protection's own documented precondition
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("connection reset")), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place, \
         patch.object(pluto_app, "time"):
        pluto_app._confirm_and_finalize_protection(
            "user-1", CREDS, ACCOUNT_ID, TICKER, entry, filled_quantity=30.0, stop_price=54.95, target_price=60.0,
        )

    mock_place.assert_not_called()
    assert entry["lifecycle_state"] == ol.PROTECTION_FAILED
    assert entry["stop_leg_quantity"] == 30.0  # untouched - never confirmed dead, just unconfirmed


def test_a_genuinely_active_stop_is_never_touched():
    entry = _stuck_entry()
    ol.transition(entry, ol.PROTECTION_PENDING)  # _confirm_and_finalize_protection's own documented precondition
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 30, 0)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place, \
         patch.object(pluto_app, "time"):
        pluto_app._confirm_and_finalize_protection(
            "user-1", CREDS, ACCOUNT_ID, TICKER, entry, filled_quantity=30.0, stop_price=54.95, target_price=60.0,
        )

    mock_place.assert_not_called()
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["stop_leg_quantity"] == 30.0
    assert entry["stop_client_order_id"] == STOP_ID_V1
