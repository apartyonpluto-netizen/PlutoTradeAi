"""One positions read per account per monitor pass. Found live 2026-09-29:
every PROTECTION_FAILED record made its own get_account_positions call
(8 identical reads per ~10s tick), Webull answered with 429s, and the
back-off sleep turned 1.3s ticks into 10-23s ones."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
HELD = [{"symbol": "AAPL", "quantity": "10"}, {"symbol": "MSFT", "quantity": "5"}]


def test_positions_are_read_once_per_account_inside_a_pass():
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=HELD) as positions:
        with pluto_app._monitor_pass_snapshot_cache():
            assert pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "AAPL") == 10.0
            assert pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "MSFT") == 5.0
            assert pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "TSLA") == 0.0
            assert positions.call_count == 1
            pluto_app._position_quantity_at_broker(CREDS, "acct-margin", "AAPL")
            assert positions.call_count == 2  # a different account is a different read


def test_outside_a_pass_every_call_reads_the_broker_fresh():
    with patch.object(pluto_app.webull_api, "get_account_positions", return_value=HELD) as positions:
        pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "AAPL")
        pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "AAPL")
    assert positions.call_count == 2


def test_the_cache_is_cleared_even_when_the_pass_raises():
    with pytest.raises(RuntimeError):
        with pluto_app._monitor_pass_snapshot_cache():
            raise RuntimeError("boom")
    assert getattr(pluto_app._monitor_pass_cache, "positions", None) is None


def test_a_failed_read_is_not_cached_as_evidence():
    with patch.object(pluto_app.webull_api, "get_account_positions", side_effect=[ValueError("429"), HELD]) as positions:
        with pluto_app._monitor_pass_snapshot_cache():
            assert pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "AAPL") is None
            assert pluto_app._position_quantity_at_broker(CREDS, ACCOUNT_ID, "AAPL") == 10.0
    assert positions.call_count == 2


def _stuck_entry(ticker: str) -> dict:
    entry = {"ticker": ticker, "limit_price": 100.0, "stop": 95.0, "target": 110.0, "trading_day": "2026-09-29",
             "quantity": 10, "filled_quantity": 10.0, "entry_order_terminal": True, "account_id": ACCOUNT_ID}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"pt-{ticker.lower()}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="stop rejected")
    return entry


def test_a_whole_fast_monitor_pass_reads_positions_once(user_id):
    for ticker in ("AAPL", "MSFT", "NVDA"):
        record_overnight_order(user_id, _stuck_entry(ticker))
    held = [{"symbol": t, "quantity": "10"} for t in ("AAPL", "MSFT", "NVDA")]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=held) as positions, \
         patch.object(pluto_app, "_discover_orphaned_broker_entries", return_value=0), \
         patch.object(pluto_app, "_reconcile_unknown_submissions", return_value=False), \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False), \
         patch.object(pluto_app, "_reconcile_entry_fill_and_protection") as reconcile:
        pluto_app._run_fast_order_monitor(user_id)
    assert reconcile.call_count == 3  # every record was still checked
    assert positions.call_count == 1  # exit-order pass + three absent checks: one read
