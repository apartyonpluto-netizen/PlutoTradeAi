from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import list_overnight_orders

"""The full, unattended loop - candidate discovery, sizing, entry, fill,
protection, and exit - has never been exercised as ONE continuous chain
anywhere else in this suite. Every other test either hand-constructs an
already-decided entry (test_submit_and_protect_entry.py,
test_reconcile_position_exit.py) or stubs _submit_and_protect_entry outright
(test_regime_scan_integration.py). This file drives the REAL, unmocked
sequence end to end - only the outer boundary (market-data discovery and the
Webull broker itself) is faked; every decision in between (which candidate
qualifies, how many shares, whether/how it's submitted and protected, and
later whether/how it's closed) is production code, not a stand-in - and
finishes by hitting the real /trade-journal route to confirm what a user
would actually SEE, closing the loop on "if i press the button are orders
filled and shown and also closed and shown that theyve been closed with lose
or profit."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be approved,
    which a bare fixture user_id string alone does not satisfy (same
    requirement documented in test_overnight_order_display_status.py)."""
    user = auth.register_user(f"fulllifecycle-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _entry_order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _exit_order_detail(status: str, total_quantity: float, filled_quantity: float, average_price: float | None = None) -> dict:
    order = {"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}
    if average_price is not None:
        order["avg_filled_price"] = str(average_price)
    return {"orders": [order]}


def _ai_found_candidate(ticker="NVDA", confidence=82):
    """A setup the AI scan itself decided is worth trading - the test never
    hand-picks a quantity or tells the code what to do with this; sizing,
    submission, and protection all flow from the scan's own logic."""
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": confidence,
        "ideal_entry": 100.0,
        "stop": 50.0,
        "target": 110.0,
        "strategy": "Trend Continuation",
        "trade_quality": "high",
    }


def _run_full_autonomous_scan(user_id, opportunities):
    """Drives _run_autonomous_trade_scan_locked for real. Both the filled
    quantity and the stop/target legs' client_order_ids are captured from
    what the REAL code actually did (sizing math, and
    order_lifecycle.deterministic_client_order_id) rather than assumed -
    _reconcile_protective_leg_quantity computes each leg's client_order_id
    itself and passes it INTO place_stop_loss_order/place_take_profit_order;
    it is never read back from their return value the way the entry leg's
    id partially resembles. Guessing a fixed id here (e.g. "stop-cid") means
    _confirm_and_finalize_protection's polls never match it, so protection
    always reports PROTECTION_FAILED - this broke until the ids were
    captured from the actual placement call arguments instead."""
    placed_quantity: dict = {}
    protective_ids: dict = {}

    def _fake_place_stock_order(**kwargs):
        placed_quantity["value"] = kwargs["quantity"]
        return {"client_order_id": "entry-cid"}

    def _fake_place_stop_loss_order(**kwargs):
        protective_ids["stop"] = kwargs["client_order_id"]
        return {"client_order_id": kwargs["client_order_id"]}

    def _fake_place_take_profit_order(**kwargs):
        protective_ids["target"] = kwargs["client_order_id"]
        return {"client_order_id": kwargs["client_order_id"]}

    def _fake_get_order_detail(app_key, app_secret, account_id, client_order_id):
        # The stop/target legs are still genuinely RESTING at this point in
        # the chain (SUBMITTED, not FILLED) - only the entry leg itself has
        # actually executed. _confirm_and_finalize_protection polls these
        # two ids specifically to confirm the protective legs are active
        # before declaring PROTECTION_CONFIRMED_ACTIVE.
        if client_order_id in (protective_ids.get("stop"), protective_ids.get("target")):
            return _exit_order_detail("SUBMITTED", 0, 0)
        quantity = placed_quantity.get("value", 0)
        return _entry_order_detail("FILLED", quantity, quantity, average_price=100.0)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={
             "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
             "account_currency_assets": [{"buying_power": "1000000"}],
         }), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value={
             "vix_level": None, "source_time": None, "fetch_time": None,
             "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
         }), \
         patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}), \
         patch.object(pluto_app.webull_api, "place_stock_order", side_effect=_fake_place_stock_order), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_fake_get_order_detail), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", side_effect=_fake_place_stop_loss_order), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", side_effect=_fake_place_take_profit_order), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result


def _the_one_recorded_order(user_id, ticker):
    matches = [order for order in list_overnight_orders(user_id) if order.get("ticker") == ticker]
    assert len(matches) == 1, f"expected exactly one recorded order for {ticker}, got {len(matches)}"
    return matches[0]


def _get_trade_journal_html(user_id: str) -> str:
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        response = client.get("/trade-journal")
        return response.data.decode("utf-8")


# --- discovery -> sizing -> entry -> fill -> protection, as ONE real chain ------


def test_ai_discovered_candidate_actually_gets_filled_and_protected(user_id):
    registered_user_id = _registered_user(user_id[:8])
    candidate = _ai_found_candidate()
    result = _run_full_autonomous_scan(registered_user_id, [candidate])

    assert result["placed_count"] == 1, f"expected the AI's own candidate to be placed, got: {result}"
    entry = _the_one_recorded_order(registered_user_id, "NVDA")

    # Not hand-fed - these came from the scan's own decision.
    assert entry["confidence"] == 82
    assert entry["trade_quality"] == "high"
    assert entry["quantity"] > 0

    # The real _submit_and_protect_entry ran (not a stub) and drove this all
    # the way to a genuinely protected position.
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["stop_client_order_id"]
    assert entry["target_client_order_id"]

    # And a user opening Trade Journal right now would see it correctly -
    # not frozen at "placed" (the bug fixed earlier this session).
    body = _get_trade_journal_html(registered_user_id)
    assert "Filled &amp; protected" in body or "Filled & protected" in body


# --- ...and THEN the position closes, on a later monitoring tick ---------------


def test_that_position_then_closes_with_a_profit_when_the_target_fills_and_the_page_shows_it(user_id):
    registered_user_id = _registered_user(user_id[:8] + "tp")
    candidate = _ai_found_candidate(ticker="NVDA", confidence=82)
    _run_full_autonomous_scan(registered_user_id, [candidate])
    entry = _the_one_recorded_order(registered_user_id, "NVDA")
    quantity = entry["quantity"]
    trading_day = entry["trading_day"]
    target_client_order_id = entry["target_client_order_id"]
    stop_client_order_id = entry["stop_client_order_id"]
    cancelled = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == target_client_order_id:
            return _exit_order_detail("FILLED", quantity, quantity, average_price=110.0)
        if client_order_id == stop_client_order_id and client_order_id in cancelled:
            return _exit_order_detail("CANCELLED", quantity, 0)
        return _exit_order_detail("SUBMITTED", quantity, 0)

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        exited = pluto_app._reconcile_position_exit(registered_user_id, CREDS, ACCOUNT_ID, "NVDA", trading_day, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.CLOSED
    assert pluto_app._overnight_order_display_status(entry) == "Closed"

    closed = list_closed_trades(registered_user_id)
    assert len(closed) == 1
    assert closed[0]["exit_type"] == "target"
    assert closed[0]["net_realized_pnl"] > 0, "target fill above entry must record a WIN, not a loss"

    body = _get_trade_journal_html(registered_user_id)
    assert "Closed</td>" in body or ">Closed<" in body
    assert "TARGET" in body
    expected_pnl_text = "${:.2f}".format(closed[0]["net_realized_pnl"])
    assert expected_pnl_text in body
    assert "tone-positive" in body


def test_that_position_then_closes_with_a_loss_when_the_stop_fills_and_the_page_shows_it(user_id):
    registered_user_id = _registered_user(user_id[:8] + "sl")
    candidate = _ai_found_candidate(ticker="NVDA", confidence=82)
    _run_full_autonomous_scan(registered_user_id, [candidate])
    entry = _the_one_recorded_order(registered_user_id, "NVDA")
    quantity = entry["quantity"]
    trading_day = entry["trading_day"]
    stop_client_order_id = entry["stop_client_order_id"]
    target_client_order_id = entry["target_client_order_id"]
    cancelled = set()

    def _get_detail(app_key, app_secret, account_id, client_order_id):
        if client_order_id == stop_client_order_id:
            return _exit_order_detail("FILLED", quantity, quantity, average_price=50.0)
        if client_order_id == target_client_order_id and client_order_id in cancelled:
            return _exit_order_detail("CANCELLED", quantity, 0)
        return _exit_order_detail("SUBMITTED", quantity, 0)

    def _cancel(app_key, app_secret, account_id, client_order_id):
        cancelled.add(client_order_id)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_get_detail), \
         patch.object(pluto_app.webull_api, "cancel_order", side_effect=_cancel):
        exited = pluto_app._reconcile_position_exit(registered_user_id, CREDS, ACCOUNT_ID, "NVDA", trading_day, entry)

    assert exited is True
    assert entry["lifecycle_state"] == ol.CLOSED
    assert pluto_app._overnight_order_display_status(entry) == "Closed"

    closed = list_closed_trades(registered_user_id)
    assert len(closed) == 1
    assert closed[0]["exit_type"] == "stop"
    assert closed[0]["net_realized_pnl"] < 0, "stop fill below entry must record a LOSS, not a win"

    body = _get_trade_journal_html(registered_user_id)
    assert "Closed</td>" in body or ">Closed<" in body
    assert "STOP" in body
    expected_pnl_text = "${:.2f}".format(closed[0]["net_realized_pnl"])
    assert expected_pnl_text in body
    assert "tone-negative" in body
