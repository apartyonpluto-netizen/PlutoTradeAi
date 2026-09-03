from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

"""_check_and_rearm_dead_stop - closes a real, live gap confirmed
empirically 2026-09-03: a resting STOP_LOSS order's DAY time-in-force
DOES eventually get cancelled by the broker (confirmed live - still
SUBMITTED at the exact market-close boundary, CANCELLED about an hour
later), and nothing previously re-checked a stop's own status once an
entry reached PROTECTION_CONFIRMED_ACTIVE with entry_order_terminal=True
(the normal state for the overwhelming majority of healthy positions).
Found live watching ADBE's real stop go SUBMITTED -> CANCELLED overnight
while the position was still held."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-cash-1"
TICKER = "ADBE"
TRADING_DAY = "2026-09-03"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _active_entry(stop_id: str, **extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "stop": 264.88,
        "target": 300.0,
        "quantity": 1,
        "filled_quantity": 1.0,
        "average_entry_fill_price": 270.0,
        "stop_client_order_id": stop_id,
        "stop_leg_quantity": 1.0,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="entry-cid-a")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=1.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    entry["entry_order_terminal"] = True
    entry.update(extra)
    return entry


def test_still_resting_stop_is_left_alone(user_id):
    entry = _active_entry("stop-a")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 1, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions") as mock_positions, \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_positions.assert_not_called()  # short-circuited before even checking the position - nothing to rearm
    mock_place.assert_not_called()


def test_a_genuinely_filled_stop_is_left_alone_its_a_real_exit_not_a_rearm(user_id):
    entry = _active_entry("stop-b")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 1, 1)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_place.assert_not_called()


def test_dead_stop_with_position_still_held_places_a_fresh_stop(user_id):
    entry = _active_entry("stop-c")
    new_stop_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "stop_rearm", attempt=1)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 1, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": 1}]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": new_stop_id}) as mock_place:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is True
    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["stop_price"] == 264.88
    assert mock_place.call_args.kwargs["quantity"] == 1.0
    assert entry["stop_client_order_id"] == new_stop_id
    assert entry["stop_rearm_reason"].startswith("previous stop showed CANCELLED")


def test_dead_stop_with_position_also_gone_is_not_a_rearm_case(user_id):
    # Position is genuinely absent too - _check_position_absent_while_active's
    # job, not this function's.
    entry = _active_entry("stop-d")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 1, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_place.assert_not_called()


def test_dead_stop_outside_core_hours_retries_later_without_placing(user_id):
    entry = _active_entry("stop-e")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 1, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": 1}]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="NIGHT"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_place.assert_not_called()


def test_short_direction_is_never_rearmed(user_id):
    entry = _active_entry("stop-f", direction="short")
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        result = pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_detail.assert_not_called()


def test_rearm_placement_failure_raises_with_a_clear_unprotected_message(user_id):
    entry = _active_entry("stop-g")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 1, 0)), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": TICKER, "quantity": 1}]), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("broker down")):
        try:
            pluto_app._check_and_rearm_dead_stop(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "genuinely UNPROTECTED" in str(error)


# --- Monitor wiring -----------------------------------------------------


def test_monitor_calls_rearm_check_for_a_terminal_active_entry_with_no_exit(user_id):
    entry = _active_entry("stop-h")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "_reconcile_position_exit", return_value=False), \
         patch.object(pluto_app, "_check_and_rearm_dead_stop") as mock_rearm, \
         patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as mock_fill:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_rearm.assert_called_once()
    mock_fill.assert_not_called()  # entry_order_terminal is True - the fill-growth path must not also fire


def test_monitor_does_not_call_rearm_check_when_the_entry_is_not_yet_order_terminal(user_id):
    entry = _active_entry("stop-i", entry_order_terminal=False)
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "_reconcile_position_exit", return_value=False), \
         patch.object(pluto_app, "_check_and_rearm_dead_stop") as mock_rearm, \
         patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as mock_fill:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_rearm.assert_not_called()
    mock_fill.assert_called_once()
