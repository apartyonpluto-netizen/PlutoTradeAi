"""Entries are on disk before their order reaches the broker, so a worker
that dies mid-submission leaves a record the monitor can resume with the
real planned stop/target - not an orphan re-imported with stop=0.

Found live 2026-09-29: INTC, MSTR and MRVL were filled with real stops,
lost their local records, and came back via orphan discovery as
PROTECTION_FAILED with stop=0/target=0, holding every position slot."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-cash"
MARGIN_ACCOUNT_ID = "acct-margin"


def _filled(quantity: float) -> dict:
    return {"orders": [{"status": "FILLED", "total_quantity": str(quantity), "filled_quantity": str(quantity), "order_id": "X"}]}


def _entry() -> dict:
    return {"ticker": "AAPL", "quantity": 10, "limit_price": 100.0, "stop": 95.0, "target": 110.0, "status": "pending"}


def _submit(user_id, entry, place_side_effect=None):
    with patch.object(pluto_app.webull_api, "place_stock_order", side_effect=place_side_effect, return_value={"client_order_id": "x"}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_filled(10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        return pluto_app._submit_and_protect_entry(
            user_id=user_id, creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            requested_quantity=10, limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-09-29", entry=entry,
        )


def test_entry_is_on_disk_with_planned_levels_when_the_order_is_sent(user_id):
    seen_at_submission = []

    def _capture(**_kwargs):
        seen_at_submission.extend(list_overnight_orders(user_id))
        return {"client_order_id": "x"}

    _submit(user_id, _entry(), place_side_effect=_capture)

    assert len(seen_at_submission) == 1
    record = seen_at_submission[0]
    assert record["lifecycle_state"] == ol.ENTRY_SUBMITTED
    assert record["entry_client_order_id"] == ol.deterministic_client_order_id(user_id, "AAPL", "2026-09-29", "entry", attempt=1)
    assert (record["stop"], record["target"]) == (95.0, 110.0)
    assert record["account_id"] == ACCOUNT_ID
    assert record["record_id"]


class _WorkerKilled(BaseException):
    """Stands in for SIGTERM/OOM mid-request - not an Exception, so nothing catches it."""


def test_worker_dying_after_submission_leaves_a_resumable_record_not_an_orphan(user_id):
    with pytest.raises(_WorkerKilled):
        _submit(user_id, _entry(), place_side_effect=_WorkerKilled())

    records = list_overnight_orders(user_id)
    assert len(records) == 1
    assert records[0]["lifecycle_state"] in ol.MONITOR_RESUMABLE_STATES
    assert records[0]["stop"] == 95.0

    # Orphan discovery must now treat the broker's order as known.
    broker_row = {"client_order_id": records[0]["entry_client_order_id"], "symbol": "AAPL", "side": "BUY", "total_quantity": "10", "limit_price": "100"}
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[broker_row]), \
         patch.object(pluto_app, "_trading_day_key", return_value="2026-09-29"):
        assert pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID) == 0


def test_a_failed_write_blocks_submission(user_id):
    entry = _entry()
    with patch.object(pluto_app, "record_overnight_order", side_effect=OSError("disk not mounted")), \
         patch.object(pluto_app.webull_api, "place_stock_order") as place:
        result = pluto_app._submit_and_protect_entry(
            user_id=user_id, creds=CREDS, account_id=ACCOUNT_ID, ticker="AAPL",
            requested_quantity=10, limit_price=100.0, stop_price=95.0, target_price=110.0,
            trading_day="2026-09-29", entry=entry,
        )
    place.assert_not_called()
    assert result["lifecycle_state"] == ol.ENTRY_FAILED
    assert "refusing to submit" in result["error"]


def test_the_final_record_replaces_the_write_ahead_copy_in_place(user_id):
    entry = _entry()
    _submit(user_id, entry)
    write_ahead_logged_at = list_overnight_orders(user_id)[0]["logged_at"]

    entry["status"] = "placed"
    record_overnight_order(user_id, entry)  # what the scan loop does after submission

    records = list_overnight_orders(user_id)
    assert len(records) == 1
    assert records[0]["status"] == "placed"
    assert records[0]["lifecycle_state"] != ol.ENTRY_SUBMITTED  # the post-submission outcome, not the write-ahead copy
    assert records[0]["logged_at"] == write_ahead_logged_at


def test_a_same_day_retry_gets_its_own_record(user_id):
    first, second = _entry(), _entry()
    _submit(user_id, first)
    _submit(user_id, second)
    records = list_overnight_orders(user_id)
    assert len(records) == 2
    assert records[0]["entry_client_order_id"] == records[1]["entry_client_order_id"]
    assert records[0]["record_id"] != records[1]["record_id"]


def test_option_entry_is_also_written_ahead(user_id):
    seen = []

    def _capture(**_kwargs):
        seen.extend(list_overnight_orders(user_id))
        return {"client_order_id": "x"}

    contract = {"option_symbol": "AAPL261016C00200000", "strike": 200.0, "expiration_date": "2026-10-16", "option_type": "CALL"}
    with patch.object(pluto_app.webull_api, "place_option_order", side_effect=_capture), \
         patch.object(pluto_app, "_poll_option_fill", side_effect=lambda *a, **k: a[-1]):
        pluto_app._submit_and_confirm_option_entry(
            user_id, CREDS, MARGIN_ACCOUNT_ID, "AAPL", contract, 1, 2.5, "2026-09-29", {"ticker": "AAPL", "status": "pending"},
        )
    assert len(seen) == 1
    assert seen[0]["instrument_type"] == "OPTION"
    assert seen[0]["account_id"] == MARGIN_ACCOUNT_ID


def test_margin_pass_skips_records_with_no_account_id(user_id):
    """An unstamped record predates margin trading; checking it against the
    margin account found no position and flagged a held cash position absent."""
    legacy = {"ticker": "INTC", "quantity": 2, "stop": 0, "target": 0, "entry_client_order_id": "ptlegacy", "trading_day": "2026-09-21"}
    ol.initialize(legacy, ol.ENTRY_SUBMITTED)
    record_overnight_order(user_id, legacy)

    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as reconcile:
        pluto_app._monitor_transitional_orders(user_id, CREDS, MARGIN_ACCOUNT_ID, include_unstamped=False)
    reconcile.assert_not_called()

    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as reconcile:
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)
    assert reconcile.call_count == 1


def test_orphan_discovery_stamps_the_account_it_found_the_order_in(user_id):
    orphan_id = ol.deterministic_client_order_id(user_id, "AAPL", pluto_app._trading_day_key(), "entry", attempt=1)
    row = {"client_order_id": orphan_id, "symbol": "AAPL", "side": "BUY", "total_quantity": "10", "limit_price": "100"}
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[row]):
        assert pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID) == 1
    assert list_overnight_orders(user_id)[0]["account_id"] == ACCOUNT_ID


def test_order_history_window_includes_today():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    client = MagicMock()
    client.order_v2.get_order_history.return_value.status_code = 200
    client.order_v2.get_order_history.return_value.json.return_value = []
    with patch.object(pluto_app.webull_api, "_get_trade_client", return_value=client):
        pluto_app.webull_api.get_order_history("key", "secret", "acct")
    end_date = client.order_v2.get_order_history.call_args.kwargs["end_date"]
    assert end_date == (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


def test_a_monitor_pass_for_one_account_never_deletes_the_other_accounts_records(user_id):
    """The data-loss bug behind every orphan since 2026-09-04: each account's
    pass wrote back only its own filtered records, deleting the rest."""
    cash = {"ticker": "AAPL", "quantity": 10, "stop": 95.0, "target": 110.0, "entry_client_order_id": "ptcash", "trading_day": "2026-09-29", "account_id": ACCOUNT_ID}
    margin = {"ticker": "TSLA", "quantity": 3, "stop": 260.0, "target": 230.0, "entry_client_order_id": "ptmargin", "trading_day": "2026-09-29", "account_id": MARGIN_ACCOUNT_ID}
    legacy = {"ticker": "INTC", "quantity": 2, "stop": 0, "target": 0, "entry_client_order_id": "ptlegacy", "trading_day": "2026-09-21"}
    for record in (cash, margin, legacy):
        ol.initialize(record, ol.ENTRY_SUBMITTED)
        record_overnight_order(user_id, record)

    with patch.object(pluto_app, "_reconcile_entry_fill_and_protection"):
        pluto_app._monitor_transitional_orders(user_id, CREDS, MARGIN_ACCOUNT_ID, include_unstamped=False)
        pluto_app._monitor_transitional_orders(user_id, CREDS, ACCOUNT_ID)

    by_id = {r["entry_client_order_id"]: r for r in list_overnight_orders(user_id)}
    assert set(by_id) == {"ptcash", "ptmargin", "ptlegacy"}
    assert by_id["ptmargin"].get("monitor_last_attempt_at")  # the margin pass's own update was kept too
    assert by_id["ptcash"].get("monitor_last_attempt_at")
