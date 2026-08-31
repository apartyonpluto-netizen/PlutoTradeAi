from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import list_closed_trades

"""_check_and_execute_target_exit - the target leg's real exit mechanism
since 2026-08-31 (see its own docstring and _reconcile_protective_leg_quantity's
for the full evidence trail: a real SLB entry's take-profit leg was
rejected by Webull as an attempted position reversal, and a follow-up
preview_order diagnostic ruled out every broker-native combo/bracket
order_type this account supports). Unlike every other reconciliation
function in this file, this one actively CANCELS a real resting order and
PLACES a new one rather than only reading broker state - these tests exist
specifically to prove that safety-critical sequencing holds under failure,
not just the happy path already covered indirectly by
test_full_autonomous_trade_lifecycle.py."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
TICKER = "NVDA"
TRADING_DAY = "2026-08-31"
STOP_ID = "stop-cid-1"
TARGET_PRICE = 110.0
QUANTITY = 10.0


def _exit_order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _active_entry(**extra) -> dict:
    entry: dict = {
        "ticker": TICKER,
        "stop": 95.0,
        "target": TARGET_PRICE,
        "trading_day": TRADING_DAY,
        "quantity": QUANTITY,
        "filled_quantity": QUANTITY,
        "average_entry_fill_price": 100.0,
        "stop_client_order_id": STOP_ID,
        "stop_leg_quantity": QUANTITY,
        "stop_leg_attempt": 1,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-entry-target-exit")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=QUANTITY)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at="2026-08-31T14:00:00+00:00")
    entry["entry_order_terminal"] = True
    entry.update(extra)
    return entry


def _call(entry, **kwargs):
    return pluto_app._check_and_execute_target_exit(
        "user-1", CREDS, ACCOUNT_ID, TICKER, TRADING_DAY, entry, **kwargs
    )


# --- fail-closed guards: no side effects, no order touched -------------------


def test_no_target_price_configured_is_a_noop():
    entry = _active_entry(target=0)
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        assert _call(entry) is False
    mock_detail.assert_not_called()


def test_a_legacy_broker_side_target_order_is_left_to_the_normal_exit_path():
    entry = _active_entry(target_client_order_id="target-cid-legacy")
    with patch.object(pluto_app.webull_api, "get_order_detail") as mock_detail:
        assert _call(entry) is False
    mock_detail.assert_not_called()


def test_no_stop_client_order_id_fails_closed():
    entry = _active_entry(stop_client_order_id=None)
    with patch.object(pluto_app.alpaca_data, "get_latest_trade_price") as mock_price:
        assert _call(entry) is False
    mock_price.assert_not_called()


def test_stop_already_fully_filled_defers_to_the_normal_stop_exit_path():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("FILLED", QUANTITY, QUANTITY)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price") as mock_price, \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        assert _call(entry) is False
    mock_price.assert_not_called()
    mock_cancel.assert_not_called()


def test_stop_partially_filled_defers_to_the_normal_stop_exit_path():
    # Even a PARTIAL stop fill means this is a stop-exit in progress, not a
    # target-price condition - racing a target-exit attempt on top of that
    # is exactly what _reconcile_both_legs_filled_emergency exists to catch
    # if it were allowed to happen; this function must not let it.
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("PARTIAL FILLED", QUANTITY, 3)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price") as mock_price:
        assert _call(entry) is False
    mock_price.assert_not_called()


def test_stop_not_actively_resting_for_some_other_reason_is_left_alone():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("CANCELLED", QUANTITY, 0)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price") as mock_price:
        assert _call(entry) is False
    mock_price.assert_not_called()


def test_no_fresh_price_available_fails_closed():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("SUBMITTED", QUANTITY, 0)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=None), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        assert _call(entry) is False
    mock_cancel.assert_not_called()


def test_price_below_target_is_not_yet_reached():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("SUBMITTED", QUANTITY, 0)), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE - 5), \
         patch.object(pluto_app.webull_api, "cancel_order") as mock_cancel:
        assert _call(entry) is False
    mock_cancel.assert_not_called()


def test_price_exactly_at_target_does_trigger():
    entry = _active_entry()
    sell_id = ol.deterministic_client_order_id("user-1", TICKER, TRADING_DAY, "target_exit", attempt=1)

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID:
            return _exit_order_detail("CANCELLED", QUANTITY, 0) if client_order_id in cancelled else _exit_order_detail("SUBMITTED", QUANTITY, 0)
        return _exit_order_detail("FILLED", QUANTITY, QUANTITY, average_price=TARGET_PRICE)

    cancelled: set = set()
    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=lambda *a: cancelled.add(a[-1])), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": sell_id}) as mock_sell, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "time"):
        assert _call(entry) is True
    mock_sell.assert_called_once()
    assert mock_sell.call_args.kwargs["side"] == "SELL"


# --- the real cancel-then-sell sequence, and its failure modes ---------------


def test_happy_path_cancels_stop_places_sell_and_records_a_closed_trade():
    entry = _active_entry()
    sell_id = ol.deterministic_client_order_id("user-1", TICKER, TRADING_DAY, "target_exit", attempt=1)
    fresh_price = TARGET_PRICE + 1.0
    cancelled: set = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID:
            return _exit_order_detail("CANCELLED", QUANTITY, 0) if client_order_id in cancelled else _exit_order_detail("SUBMITTED", QUANTITY, 0)
        if client_order_id == sell_id:
            return _exit_order_detail("FILLED", QUANTITY, QUANTITY, average_price=fresh_price)
        return _exit_order_detail("UNKNOWN", 0, 0)

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel) as mock_cancel, \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": sell_id}) as mock_sell, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=fresh_price), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "time"):
        result = _call(entry)

    assert result is True
    mock_cancel.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, STOP_ID)
    mock_sell.assert_called_once()
    assert mock_sell.call_args.kwargs["quantity"] == QUANTITY
    assert mock_sell.call_args.kwargs["client_order_id"] == sell_id
    # Marketable limit, meaningfully below the fresh price, never a bare
    # market order - see TARGET_EXIT_SLIPPAGE_TOLERANCE's own comment.
    assert mock_sell.call_args.kwargs["limit_price"] < fresh_price

    assert entry["lifecycle_state"] == ol.CLOSED
    closed = list_closed_trades("user-1")
    assert len(closed) == 1
    assert closed[0]["exit_type"] == "target"
    assert closed[0]["close_reason"] == "target_exit_executed"
    assert closed[0]["net_realized_pnl"] > 0


def test_stop_cancel_call_itself_failing_raises_and_places_no_sell():
    entry = _active_entry()
    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_exit_order_detail("SUBMITTED", QUANTITY, 0)), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=RuntimeError("network error")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_sell, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1):
        try:
            _call(entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "could not cancel the stop leg" in str(error)
    mock_sell.assert_not_called()
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # unchanged - nothing was touched


def test_stop_fills_during_the_cancel_race_defers_rather_than_double_exits():
    # The stop executed between this function's own initial "is it still
    # resting" check and the cancel landing - a genuine stop-exit, not a
    # target-exit. Nothing to undo (a fill is final); must not proceed to
    # place a second, conflicting sell.
    entry = _active_entry()
    call_count = {"n": 0}

    def _get_detail_then_filled(app_key, app_secret, account_id, client_order_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _exit_order_detail("SUBMITTED", QUANTITY, 0)  # initial "still resting" check
        return _exit_order_detail("FILLED", QUANTITY, QUANTITY, average_price=95.0)  # post-cancel recheck

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail_then_filled), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value=None) as mock_cancel, \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_sell, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1):
        assert _call(entry) is False

    mock_cancel.assert_called_once()
    mock_sell.assert_not_called()
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # left for the normal stop-exit path to pick up


def test_stop_cancellation_not_yet_confirmed_raises_for_a_retry():
    entry = _active_entry()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        return _exit_order_detail("SUBMITTED", QUANTITY, 0)  # still shows active even after "cancelling"

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", return_value=None), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_sell, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1):
        try:
            _call(entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "not yet confirmed" in str(error)
    mock_sell.assert_not_called()


def test_sell_placement_failure_restores_a_fallback_stop_and_raises():
    entry = _active_entry()
    fallback_stop_id = ol.deterministic_client_order_id("user-1", TICKER, TRADING_DAY, "stop", attempt=2)
    cancelled: set = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID:
            return _exit_order_detail("CANCELLED", QUANTITY, 0) if client_order_id in cancelled else _exit_order_detail("SUBMITTED", QUANTITY, 0)
        return _exit_order_detail("UNKNOWN", 0, 0)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=lambda *a: cancelled.add(a[-1])), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=RuntimeError("sell rejected")), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": fallback_stop_id}) as mock_restore, \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        try:
            _call(entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "sell order failed" in str(error)
            assert "restored the stop leg as a fallback" in str(error)

    mock_restore.assert_called_once()
    assert entry["stop_client_order_id"] == fallback_stop_id  # protection actually restored, not just attempted
    assert entry["stop_leg_quantity"] == QUANTITY


def test_sell_placement_and_fallback_stop_restoration_both_failing_alerts_critical():
    entry = _active_entry()
    cancelled: set = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID:
            return _exit_order_detail("CANCELLED", QUANTITY, 0) if client_order_id in cancelled else _exit_order_detail("SUBMITTED", QUANTITY, 0)
        return _exit_order_detail("UNKNOWN", 0, 0)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=lambda *a: cancelled.add(a[-1])), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=RuntimeError("sell rejected")), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=RuntimeError("stop rejected too")), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "add_manual_alert") as mock_alert:
        try:
            _call(entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "fallback stop restoration failed" in str(error)

    # add_manual_alert(user_id, payload) - payload is the second positional arg.
    alert_payload = mock_alert.call_args.args[1]
    assert alert_payload["type"] == "target_exit_left_position_unprotected"
    assert alert_payload["priority"] == "critical"
    # Never silently left pointed at a cancelled, no-longer-real order id -
    # the fallback placement failed too, so this stays exactly what it was.
    assert entry.get("stop_client_order_id") == STOP_ID


def test_sell_placed_but_not_yet_filled_raises_and_keeps_the_position_flagged_unprotected():
    entry = _active_entry()
    sell_id = ol.deterministic_client_order_id("user-1", TICKER, TRADING_DAY, "target_exit", attempt=1)
    cancelled: set = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == STOP_ID:
            return _exit_order_detail("CANCELLED", QUANTITY, 0) if client_order_id in cancelled else _exit_order_detail("SUBMITTED", QUANTITY, 0)
        if client_order_id == sell_id:
            return _exit_order_detail("SUBMITTED", QUANTITY, 0)  # placed, but not filled yet
        return _exit_order_detail("UNKNOWN", 0, 0)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=lambda *a: cancelled.add(a[-1])), \
         patch.object(pluto_app.webull_api, "place_stock_order", return_value={"client_order_id": sell_id}), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=TARGET_PRICE + 1), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "time"):
        try:
            _call(entry)
            assert False, "expected a RuntimeError"
        except RuntimeError as error:
            assert "has not filled yet" in str(error)
            assert "UNPROTECTED" in str(error)
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE  # state unchanged - the caller's own retry tracking applies
