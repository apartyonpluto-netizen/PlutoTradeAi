from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy import autonomous_controller as controller
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

"""Real options entry/exit lifecycle (2026-09-03) - _submit_and_confirm_option_entry/
_reconcile_option_entry_fill/_check_and_execute_option_exit. Mirrors
test_short_direction_entry_protect_exit.py's structure and mocking level
(webull_api calls, never the SDK client itself) - the order shape these
functions build was confirmed live against the sandbox before being
adopted (see integrations/webull.py's _build_option_order docstring)."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-margin-1"
TICKER = "ADBE"
TRADING_DAY = "2026-09-03"

CONTRACT = {
    "option_symbol": "ADBE260918C00280000",
    "strike": 280.0,
    "expiration_date": "2026-09-18",
    "option_type": "CALL",
}


def _order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _snapshot_row(symbol: str, bid: float) -> list:
    return [{"symbol": symbol, "bid": str(bid), "ask": str(bid + 0.10)}]


def _active_option_entry(entry_client_order_id: str, **extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "instrument_type": "OPTION",
        "option_symbol": CONTRACT["option_symbol"],
        "strike": CONTRACT["strike"],
        "expiration_date": CONTRACT["expiration_date"],
        "option_type": CONTRACT["option_type"],
        "quantity": 2,
        "contracts": 2.0,
        "premium_paid_per_contract": 5.0,
        "premium_paid_is_estimated": False,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=2.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    entry["entry_order_terminal"] = True
    entry.update(extra)
    return entry


# --- Entry placement: BUY_TO_OPEN --------------------------------------------


def test_submit_and_confirm_option_entry_places_a_buy(user_id):
    entry: dict = {}
    with patch.object(pluto_app.webull_api, "place_option_order", side_effect=RuntimeError("stop after placement, not testing the poll loop")) as mock_place:
        pluto_app._submit_and_confirm_option_entry(
            user_id, CREDS, ACCOUNT_ID, TICKER, CONTRACT, quantity=2, limit_price=5.0, trading_day=TRADING_DAY, entry=entry,
        )
    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["side"] == "BUY"
    assert mock_place.call_args.kwargs["option_type"] == "CALL"
    assert mock_place.call_args.kwargs["strike_price"] == 280.0
    assert mock_place.call_args.kwargs["expiration_date"] == "2026-09-18"
    assert entry["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE  # the placement itself raised - see side_effect
    assert entry["instrument_type"] == "OPTION"


def test_submit_and_confirm_option_entry_definite_rejection_fails_the_entry(user_id):
    entry: dict = {}
    rejection = pluto_app.webull_api.DefiniteOrderRejection("no buying power")
    with patch.object(pluto_app.webull_api, "place_option_order", side_effect=rejection):
        pluto_app._submit_and_confirm_option_entry(
            user_id, CREDS, ACCOUNT_ID, TICKER, CONTRACT, quantity=2, limit_price=5.0, trading_day=TRADING_DAY, entry=entry,
        )
    assert entry["lifecycle_state"] == ol.ENTRY_FAILED
    assert "no buying power" in entry["error"]


def test_submit_and_confirm_option_entry_happy_path_fills_and_confirms_active(user_id):
    entry: dict = {}
    entry_client_order_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "option_entry", attempt=1)
    with patch.object(pluto_app.webull_api, "place_option_order", return_value={"client_order_id": entry_client_order_id}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2, average_price=5.05)), \
         patch.object(pluto_app, "time"):
        pluto_app._submit_and_confirm_option_entry(
            user_id, CREDS, ACCOUNT_ID, TICKER, CONTRACT, quantity=2, limit_price=5.0, trading_day=TRADING_DAY, entry=entry,
        )
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["premium_paid_per_contract"] == 5.05
    assert entry["premium_paid_is_estimated"] is False
    assert entry["contracts"] == 2.0


def test_reconcile_option_entry_fill_estimates_premium_from_limit_price_when_no_average_reported(user_id):
    entry: dict = {"limit_price": 5.0}
    entry_client_order_id = "opt-entry-est"
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2)):
        pluto_app._reconcile_option_entry_fill(user_id, CREDS, ACCOUNT_ID, entry_client_order_id, entry)
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["premium_paid_per_contract"] == 5.0
    assert entry["premium_paid_is_estimated"] is True


def test_reconcile_option_entry_fill_zero_fill_at_terminal_status_fails(user_id):
    entry: dict = {"limit_price": 5.0}
    entry_client_order_id = "opt-entry-zero"
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("CANCELLED", 2, 0)):
        pluto_app._reconcile_option_entry_fill(user_id, CREDS, ACCOUNT_ID, entry_client_order_id, entry)
    assert entry["lifecycle_state"] == ol.ENTRY_FAILED


# --- Exit: premium-percentage target/stop + expiration safety net -----------


def test_option_exit_not_yet_triggered_returns_false(user_id):
    entry = _active_option_entry("opt-entry-a")
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=_snapshot_row(CONTRACT["option_symbol"], 5.10)), \
         patch.object(pluto_app.webull_api, "place_option_order") as mock_place:
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_place.assert_not_called()


def test_option_exit_no_snapshot_data_fails_closed(user_id):
    entry = _active_option_entry("opt-entry-b")
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=[]):
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False


def test_option_exit_target_reached_closes_with_profit(user_id):
    entry = _active_option_entry("opt-entry-c")
    sell_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "option_exit", attempt=1)
    # premium_paid=5.0, default target_gain_percent=50% -> target value 7.5.
    # bid=8.0 clears it.
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=_snapshot_row(CONTRACT["option_symbol"], 8.0)), \
         patch.object(pluto_app.webull_api, "place_option_order", return_value={"client_order_id": sell_id}) as mock_sell, \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2, average_price=8.0)), \
         patch.object(pluto_app, "time"):
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is True
    assert mock_sell.call_args.kwargs["side"] == "SELL"
    assert entry["lifecycle_state"] == ol.CLOSED
    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["exit_type"] == "option_target_reached"
    assert record["instrument_type"] == "OPTION"
    # (8.0 - 5.0) * 2 contracts * 100 multiplier = 600.
    assert record["net_realized_pnl"] == 600.0
    assert record["pnl_status"] == "complete"


def test_option_exit_stop_reached_closes_with_loss(user_id):
    entry = _active_option_entry("opt-entry-d")
    sell_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "option_exit", attempt=1)
    # premium_paid=5.0, default stop_loss_percent=50% -> stop value 2.5.
    # bid=2.0 breaches it.
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=_snapshot_row(CONTRACT["option_symbol"], 2.0)), \
         patch.object(pluto_app.webull_api, "place_option_order", return_value={"client_order_id": sell_id}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2, average_price=2.0)), \
         patch.object(pluto_app, "time"):
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is True
    record = list_closed_trades(user_id)[0]
    assert record["exit_type"] == "option_stop_reached"
    # (2.0 - 5.0) * 2 * 100 = -600.
    assert record["net_realized_pnl"] == -600.0


def test_option_exit_expiration_safety_net_closes_regardless_of_pnl(user_id):
    # bid stays flat (neither target nor stop reached), but "today" is
    # within the default 3-day close-before-expiration window.
    entry = _active_option_entry("opt-entry-e")
    sell_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "option_exit", attempt=1)
    fixed_now = datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)  # 2 days before 2026-09-18 expiration
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=_snapshot_row(CONTRACT["option_symbol"], 5.0)), \
         patch.object(pluto_app.webull_api, "place_option_order", return_value={"client_order_id": sell_id}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2, average_price=5.0)), \
         patch.object(pluto_app, "_now_utc", return_value=fixed_now), \
         patch.object(pluto_app, "time"):
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is True
    record = list_closed_trades(user_id)[0]
    assert record["exit_type"] == "option_expiration_safety_close"


def test_option_exit_thresholds_are_read_from_the_users_real_risk_settings(user_id):
    controller.update_risk_settings(user_id, option_target_gain_percent=10.0)
    entry = _active_option_entry("opt-entry-f")
    sell_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "option_exit", attempt=1)
    # premium_paid=5.0, target_gain_percent=10% -> target value 5.5. bid=5.6 clears it,
    # which would NOT have cleared the default 50% target (7.5).
    with patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=_snapshot_row(CONTRACT["option_symbol"], 5.6)), \
         patch.object(pluto_app.webull_api, "place_option_order", return_value={"client_order_id": sell_id}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 2, 2, average_price=5.6)), \
         patch.object(pluto_app, "time"):
        result = pluto_app._check_and_execute_option_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is True
    record = list_closed_trades(user_id)[0]
    assert record["exit_type"] == "option_target_reached"


# --- Monitor wiring: instrument_type routes to the right functions ----------


def test_monitor_routes_a_protection_confirmed_active_option_entry_to_the_option_exit_check(user_id):
    entry = _active_option_entry("opt-entry-monitor-a")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "_check_and_execute_option_exit") as mock_option_exit, \
         patch.object(pluto_app, "_reconcile_position_exit") as mock_equity_exit, \
         patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as mock_equity_fill:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_option_exit.assert_called_once()
    mock_equity_exit.assert_not_called()
    mock_equity_fill.assert_not_called()


def test_monitor_routes_a_submitted_option_entry_to_the_option_fill_reconciler(user_id):
    entry: dict = {"ticker": TICKER, "instrument_type": "OPTION", "trading_day": TRADING_DAY}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="opt-entry-monitor-b")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "_reconcile_option_entry_fill") as mock_option_fill, \
         patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as mock_equity_fill:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_option_fill.assert_called_once()
    mock_equity_fill.assert_not_called()


def test_monitor_still_routes_an_equity_entry_to_the_equity_functions_not_the_option_ones(user_id):
    entry: dict = {"ticker": TICKER, "trading_day": TRADING_DAY}  # no instrument_type - every pre-2026-09-03 entry
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="eq-entry-monitor-a")
    record_overnight_order(user_id, entry)
    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as mock_equity_fill, \
         patch.object(pluto_app, "_reconcile_option_entry_fill") as mock_option_fill:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    mock_equity_fill.assert_called_once()
    mock_option_fill.assert_not_called()
