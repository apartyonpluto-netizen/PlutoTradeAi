"""Sandbox and live are separate books: records are stamped with their
environment, decisions only use the current environment's records, and each
record is reconciled against its OWN account. No broker call is real here -
the live switch is monkeypatched and every broker function is mocked."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import app as pluto_app
import broker_env
import order_lifecycle as ol
from autonomy import overnight_orders
from autonomy.closed_trades import list_closed_trades, record_closed_trade
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order, replace_overnight_orders
from autonomy.performance_report import build_performance_report

CREDS = {"app_key": "k", "app_secret": "s"}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(pluto_app.webull_api, "is_live_trading_armed", lambda: True)


def _entry(ticker="AAPL", account="cash-1", state=ol.PROTECTION_CONFIRMED_ACTIVE, **extra):
    entry = {"record_id": f"r-{ticker}-{account}", "ticker": ticker, "account_id": account, "status": "placed", "side": "BUY",
             "quantity": 5, "limit_price": 100.0, "stop": 95.0, "target": 110.0, "trading_day": "2026-09-29", **extra}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"cid-{ticker}-{account}")
    if state != ol.ENTRY_SUBMITTED:
        ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=5)
        ol.transition(entry, ol.PROTECTION_PENDING)
        if state == ol.PROTECTION_CONFIRMED_ACTIVE:
            ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _write_legacy(user_id, records):
    path = overnight_orders._orders_file(user_id)
    path.write_text(json.dumps(records))


def test_new_records_are_stamped_and_legacy_records_are_never_rewritten(user_id):
    legacy = _entry("MSTR")
    _write_legacy(user_id, [legacy])
    record_overnight_order(user_id, _entry("AAPL"))
    by_ticker = {o["ticker"]: o for o in list_overnight_orders(user_id)}
    assert by_ticker["AAPL"]["environment"] == "sandbox" and by_ticker["AAPL"]["broker"] == "webull"
    assert "environment" not in by_ticker["MSTR"]
    replace_overnight_orders(user_id, list_overnight_orders(user_id))
    assert "environment" not in {o["ticker"]: o for o in list_overnight_orders(user_id)}["MSTR"]
    assert broker_env.environment_of(by_ticker["MSTR"]) == ("sandbox", True)


def test_an_update_keeps_the_environment_the_record_was_created_in(user_id, live):
    entry = _entry("AAPL")
    entry["environment"] = "sandbox"
    record_overnight_order(user_id, entry)
    entry.pop("environment")
    ol.transition(entry, ol.CLOSED, close_reason="stop_filled")
    record_overnight_order(user_id, entry)
    assert list_overnight_orders(user_id)[0]["environment"] == "sandbox"


def test_with_live_armed_sandbox_records_do_not_block_or_get_reconciled(user_id, live):
    _write_legacy(user_id, [_entry("MSTR", state=ol.ENTRY_SUBMITTED), {**_entry("INTC"), "lifecycle_state": ol.UNKNOWN_SUBMISSION_STATE}])
    assert not pluto_app._has_unresolved_ambiguous_submission_locally(user_id)
    assert pluto_app._tickers_with_open_positions(broker_env.only_current(list_overnight_orders(user_id))) == set()
    assert not pluto_app._user_needs_fast_monitor_pass(user_id)
    with patch.object(pluto_app, "_reconcile_unknown_submission") as reconcile:
        assert pluto_app._reconcile_unknown_submissions(user_id, CREDS, "live-acct") is False
    reconcile.assert_not_called()
    new = record_overnight_order(user_id, _entry("NVDA", account="live-acct"))
    assert new["environment"] == "live"


def test_ambiguous_submissions_are_reconciled_against_their_own_account(user_id):
    record_overnight_order(user_id, {**_entry("AMD", account="margin-9"), "lifecycle_state": ol.UNKNOWN_SUBMISSION_STATE})
    with patch.object(pluto_app, "_reconcile_unknown_submission") as reconcile:
        pluto_app._reconcile_unknown_submissions(user_id, CREDS, "cash-1")
    assert reconcile.call_args.args[2] == "margin-9"


def test_a_stop_is_never_placed_on_one_account_for_another_accounts_record(user_id):
    record_overnight_order(user_id, {**_entry("AMD", account="margin-9"), "stop_order_placed": False})
    with patch.object(pluto_app, "_positions_for_pass", return_value=[{"symbol": "AMD", "quantity": 5}]), \
         patch.object(pluto_app, "webull_tracked_tickers", return_value=[]), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order") as place_stop, \
         patch.object(pluto_app, "time"):
        pluto_app._reconcile_exit_orders(user_id, CREDS, "cash-1")
    place_stop.assert_not_called()


def test_performance_is_reported_per_environment(user_id, live):
    _write_legacy(user_id, [_entry("MSTR")])
    record_closed_trade(user_id, "cid-MSTR-cash-1", {"ticker": "MSTR", "entry_client_order_id": "cid-MSTR-cash-1", "net_realized_pnl": 50.0,
                                                    "pnl_status": "complete"})
    closed = list_closed_trades(user_id)[0]
    assert closed["environment"] == "sandbox" and closed["environment_inferred"] is True
    live_report = build_performance_report(user_id)
    assert live_report["environment"] == "live" and live_report["total_closed_trades"] == 0
    assert live_report["other_environment_trades"] == 1
    sandbox_report = build_performance_report(user_id, environment="sandbox")
    assert sandbox_report["total_closed_trades"] == 1 and sandbox_report["environment_inferred_count"] == 1


def test_a_sandbox_ticket_cannot_be_submitted_once_orders_go_live(user_id, live):
    ticket = {"trading_day": pluto_app._trading_day_key(), "ticker": "AAPL", "quantity": 1, "limit_price": 10.0, "account_id": "a",
              "environment": "sandbox"}
    with patch.object(pluto_app, "_new_entries_allowed", return_value=True), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", side_effect=RuntimeError("no broker in tests")), \
         patch.object(pluto_app.market_data_aggregator, "get_cross_checked_price", return_value={"price": 10.0, "disagreement": False, "provider_status": []}):
        result = pluto_app._recheck_ticket_before_submission(user_id, CREDS, ticket)
    assert not result["ok"] and any("sandbox environment" in reason for reason in result["reasons"])
