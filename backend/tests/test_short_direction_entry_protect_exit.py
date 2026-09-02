from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import record_overnight_order
from webull_stop_orders import record_exit_order

"""Direction-aware short-selling support, added 2026-09-02 after the
margin sandbox account was discovered (find_individual_margin_account)
and every order shape it needs was verified live via preview_raw_order
(a SELL to open, a BUY-side STOP_LOSS to cover, a BUY-side LIMIT to
cover) before being wired into real placement code.

entry["direction"] == "short" is the single source of truth every
function below reads - no new parameter was threaded through the whole
entry/protect/exit call chain, so every existing long-side test in this
suite is untouched and still exercises exactly the code paths it always
did (entry.get("direction") == "short" is simply False/absent for every
entry that predates this field).

Deliberately narrower than the long side in one respect, matching a real
prior reviewer instruction already in this codebase
(_reconcile_both_legs_filled_emergency's own docstring): Webull's
short-position sign convention in get_account_positions has never been
empirically observed, so _check_position_absent_while_stuck is not yet
extended to shorts - see its own test below."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-margin-1"
TICKER = "AAPL"
TRADING_DAY = "2026-09-02"


def _order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


# --- Entry placement: SELL to open ------------------------------------------


def test_submit_and_protect_entry_opens_a_short_with_a_sell(user_id):
    entry: dict = {"direction": "short"}
    with patch.object(pluto_app.webull_api, "place_stock_order", side_effect=RuntimeError("stop after placement, not testing the poll loop")) as mock_place:
        pluto_app._submit_and_protect_entry(
            user_id, CREDS, ACCOUNT_ID, TICKER, requested_quantity=5,
            limit_price=200.0, stop_price=210.0, target_price=180.0, trading_day=TRADING_DAY, entry=entry,
        )
    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["side"] == "SELL"
    assert entry["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE  # the placement itself raised - see side_effect


def test_submit_and_protect_entry_still_opens_a_long_with_a_buy_when_direction_is_absent(user_id):
    # No direction field at all (every pre-2026-09-02 entry) must keep
    # behaving exactly as before.
    entry: dict = {}
    with patch.object(pluto_app.webull_api, "place_stock_order", side_effect=RuntimeError("stop after placement")) as mock_place:
        pluto_app._submit_and_protect_entry(
            user_id, CREDS, ACCOUNT_ID, TICKER, requested_quantity=5,
            limit_price=200.0, stop_price=190.0, target_price=220.0, trading_day=TRADING_DAY, entry=entry,
        )
    assert mock_place.call_args.kwargs["side"] == "BUY"


# --- Protective leg: a BUY-side stop (a "buy-stop") -------------------------


def test_reconcile_protective_leg_quantity_places_a_buy_side_stop_for_a_short(user_id):
    entry: dict = {"direction": "short"}
    with patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        pluto_app._reconcile_protective_leg_quantity(
            user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", target_quantity=5.0, leg_price=210.0,
        )
    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["side"] == "BUY"
    assert entry["stop_leg_quantity"] == 5.0


def test_reconcile_protective_leg_quantity_still_places_a_sell_side_stop_for_a_long(user_id):
    entry: dict = {}
    with patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place:
        pluto_app._reconcile_protective_leg_quantity(
            user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, "stop", target_quantity=5.0, leg_price=190.0,
        )
    assert mock_place.call_args.kwargs["side"] == "SELL"


# --- Target exit: covers with a BUY when price falls to/past target --------


def _short_active_entry(entry_client_order_id: str, stop_id: str, **extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "direction": "short",
        "stop": 210.0,
        "target": 180.0,
        "trading_day": TRADING_DAY,
        "quantity": 5,
        "filled_quantity": 5.0,
        "average_entry_fill_price": 200.0,
        "stop_client_order_id": stop_id,
        "stop_leg_quantity": 5.0,
        "stop_leg_attempt": 1,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=5.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="2026-09-02T14:00:00+00:00")
    entry["entry_order_terminal"] = True
    entry.update(extra)
    return entry


def test_short_target_exit_price_above_target_is_not_yet_reached(user_id):
    entry = _short_active_entry("pt-short-entry-a", "stop-cid-short-a")
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("SUBMITTED", 5, 0)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=185.0), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        result = pluto_app._check_and_execute_target_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)
    assert result is False
    mock_cancel.assert_not_called()


def test_short_target_exit_happy_path_covers_with_a_buy_and_records_profit(user_id):
    stop_id = "stop-cid-short-b"
    entry = _short_active_entry("pt-short-entry-b", stop_id)
    buy_id = ol.deterministic_client_order_id(user_id, TICKER, TRADING_DAY, "target_exit", attempt=1)
    fresh_price = 179.0  # below target (180) - reached for a short
    cancelled: set = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == stop_id:
            return _order_detail("CANCELLED", 5, 0) if client_order_id in cancelled else _order_detail("SUBMITTED", 5, 0)
        if client_order_id == buy_id:
            return _order_detail("FILLED", 5, 5, average_price=fresh_price)
        return _order_detail("UNKNOWN", 0, 0)

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": buy_id}) as mock_buy, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=fresh_price), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "time"):
        result = pluto_app._check_and_execute_target_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert result is True
    mock_buy.assert_called_once()
    assert mock_buy.call_args.kwargs["side"] == "BUY"
    # Marketable limit sits ABOVE fresh_price for a covering buy - the
    # mirror of a long's sell limit sitting below it.
    assert mock_buy.call_args.kwargs["limit_price"] > fresh_price

    assert entry["lifecycle_state"] == ol.CLOSED
    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["side"] == "SELL"
    assert record["exit_type"] == "target"
    # Profit: shorted at 200, covered at 179 -> (200-179)*5 = 105.
    assert record["net_realized_pnl"] == 105.0
    assert record["pnl_status"] == "complete"


# --- Passive exit: the stop leg fills (the short's loss side) --------------


def test_short_stop_fill_closes_the_trade_and_records_a_loss(user_id):
    stop_id = "stop-cid-short-c"
    entry = _short_active_entry("pt-short-entry-c", stop_id)
    record_overnight_order(user_id, entry)
    record_exit_order(user_id, TICKER, stop_id, "stop")
    details = {stop_id: _order_detail("FILLED", 5, 5, average_price=215.0)}

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=lambda a, s, acc, cid: details.get(cid, _order_detail("UNKNOWN", 0, 0))), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=190.0):  # above target - target exit correctly declines
        exited = pluto_app._reconcile_position_exit(user_id, CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.CLOSED
    closed = list_closed_trades(user_id)
    assert len(closed) == 1
    record = closed[0]
    assert record["side"] == "SELL"
    assert record["exit_type"] == "stop"
    # Loss: shorted at 200, covered (stopped out) at 215 -> (200-215)*5 = -75.
    assert record["net_realized_pnl"] == -75.0
    assert record["close_reason"] == "stop_filled"


# --- realized_risk_dollars stays positive for a short -----------------------


def test_realized_risk_dollars_is_positive_for_a_short_with_stop_above_entry(user_id):
    entry_client_order_id = "pt-short-risk-1"
    entry: dict = {"direction": "short", "stop_client_order_id": None, "target_client_order_id": None}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == entry_client_order_id:
            return _order_detail("FILLED", 5, 5, average_price=200.0)
        return _order_detail("SUBMITTED", 5, 0)  # the freshly-placed stop leg, not yet confirmed active

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as mock_place_stop, \
         patch.object(pluto_app, "time"):
        pluto_app._reconcile_entry_fill_and_protection(
            user_id=user_id, creds=CREDS, account_id=ACCOUNT_ID, ticker=TICKER,
            entry_client_order_id=entry_client_order_id, limit_price=200.0, stop_price=210.0, target_price=180.0,
            trading_day=TRADING_DAY, entry=entry,
        )
    # 5 shares * (stop 210 - limit 200) = 50, not -50.
    assert entry["realized_risk_dollars"] == 50.0
    assert mock_place_stop.call_args.kwargs["side"] == "BUY"


# --- position-absent-while-stuck deliberately does not act on a short ------


def test_check_position_absent_while_stuck_returns_false_for_a_short_without_calling_the_broker(user_id):
    entry = {"direction": "short"}
    with patch.object(pluto_app.webull_api, "get_account_positions") as mock_positions:
        result = pluto_app._check_position_absent_while_stuck(user_id, CREDS, ACCOUNT_ID, TICKER, entry)
    assert result is False
    mock_positions.assert_not_called()
