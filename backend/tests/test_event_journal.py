"""Event journal: one correlation id from the scan's evaluation of a
candidate through its plan, ticket, approval, order lifecycle and close."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import order_lifecycle as ol
from autonomy import event_journal as ej
from autonomy import trade_tickets as tt
from autonomy.autonomous_controller import set_mode
from autonomy.closed_trades import record_closed_trade
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order, replace_overnight_orders
from autonomy.research_log import list_research_decisions

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


# --- the journal itself ----------------------------------------------------------


def test_events_round_trip_newest_last_and_filter(user_id):
    ej.emit(user_id, "signal.evaluated", correlation_id="c-1", ticker="aapl", source="scan", data={"decision": "skipped"})
    ej.emit(user_id, "plan.built", correlation_id="c-1", ticker="AAPL", source="trade_planner")
    ej.emit(user_id, "signal.evaluated", correlation_id="c-2", ticker="MSFT", source="scan")
    events = ej.read_events(user_id)
    assert [e["type"] for e in events] == ["signal.evaluated", "plan.built", "signal.evaluated"]
    assert events[0]["ticker"] == "AAPL" and events[0]["stage"] == "signal" and events[1]["stage"] == "plan"
    assert events[0]["env"] == "sandbox"  # live trading is not armed in tests
    assert [e["correlation_id"] for e in ej.read_events(user_id, correlation_id="c-1")] == ["c-1", "c-1"]
    assert len(ej.read_events(user_id, ticker="msft")) == 1
    assert len(ej.read_events(user_id, types=["plan.built"])) == 1
    assert len(ej.read_events(user_id, since=events[1]["logged_at"])) == 1


def test_payloads_are_scrubbed_of_secrets_and_bounded(user_id):
    event = ej.emit(user_id, "signal.evaluated", correlation_id="c-1", data={
        "app_secret": "s3cret", "nested": {"access_token": "t", "ok": 1}, "reason": "x" * 5000,
    })
    raw = ej._journal_file(user_id).read_text()
    assert "s3cret" not in raw and "access_token" not in raw
    assert event["data"]["nested"] == {"ok": 1}
    assert len(event["data"]["reason"]) <= ej.MAX_DATA_TEXT + 1


def test_a_torn_final_line_is_skipped_not_fatal(user_id):
    ej.emit(user_id, "signal.evaluated", correlation_id="c-1")
    with open(ej._journal_file(user_id), "a", encoding="utf-8") as handle:
        handle.write('{"type": "order.entry_sub')  # crash mid-append
    assert [e["correlation_id"] for e in ej.read_events(user_id)] == ["c-1"]


def test_rotation_keeps_history_searchable_by_correlation_id(user_id, monkeypatch):
    monkeypatch.setattr(ej, "MAX_FILE_BYTES", 600)
    monkeypatch.setattr(ej, "ROTATED_FILES_KEPT", 2)
    ej.emit(user_id, "signal.evaluated", correlation_id="c-old")
    for i in range(20):
        ej.emit(user_id, "signal.evaluated", correlation_id=f"c-{i}")
    path = ej._journal_file(user_id)
    assert path.with_name(path.name + ".2").exists() and not path.with_name(path.name + ".3").exists()
    assert len(ej.read_events(user_id, correlation_id="c-19")) == 1
    # An older generation still on disk is searched too.
    rotated_ids = [json.loads(line)["correlation_id"] for line in path.with_name(path.name + ".2").read_text().splitlines()]
    newest = ej.read_events(user_id, correlation_id=rotated_ids[0])
    assert len(newest) == 1
    # Oldest generation beyond ROTATED_FILES_KEPT is gone - bounded, by design.
    assert ej.read_events(user_id, correlation_id="c-old") == []


def test_a_write_failure_is_counted_and_never_raised(user_id, monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(ej, "_append", _boom)
    before = ej.failure_stats()["count"]
    assert ej.emit(user_id, "signal.evaluated", correlation_id="c-1") is None
    assert ej.failure_stats()["count"] == before + 1 and "disk full" in ej.failure_stats()["last_error"]


# --- order records -> order.<state> events ---------------------------------------


def _order(record_id="r-1", correlation_id="c-trade", **extra):
    entry = {"record_id": record_id, "ticker": "AAPL", "account_id": ACCOUNT_ID, "quantity": 5, "limit_price": 100.0,
             "instrument_type": "EQUITY", "correlation_id": correlation_id, **extra}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"cid-{record_id}")
    return entry


def test_only_newly_persisted_lifecycle_steps_are_journaled(user_id):
    entry = _order()
    record_overnight_order(user_id, entry)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=5)
    record_overnight_order(user_id, entry)
    orders = list_overnight_orders(user_id)
    replace_overnight_orders(user_id, orders)  # nothing new - no events
    ol.transition(orders[0], ol.PROTECTION_PENDING)
    replace_overnight_orders(user_id, orders)
    types = [e["type"] for e in ej.read_events(user_id, correlation_id="c-trade")]
    assert types == ["order.entry_submitted", "order.entry_filled", "order.protection_pending"]
    filled = ej.read_events(user_id, types=["order.entry_filled"])[0]
    assert filled["stage"] == "fill" and filled["data"]["filled_quantity"] == 5
    assert filled["at"] == entry["lifecycle_history"][1]["at"]  # dated to the transition


def test_records_without_a_correlation_id_group_under_a_stable_stand_in(user_id):
    entry = _order(record_id="legacy-1", correlation_id=None)
    entry.pop("correlation_id")
    record_overnight_order(user_id, entry)
    assert ej.read_events(user_id)[0]["correlation_id"] == "rec-legacy-1"


def test_trace_rederives_steps_the_journal_never_saw(user_id):
    # Written straight to disk, as a record from before the journal existed.
    entry = _order(record_id="old-1", correlation_id=None)
    entry.pop("correlation_id")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=5)
    path = ej.USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "overnight_orders.json").write_text(json.dumps([entry]))
    trace = ej.trace(user_id, "rec-old-1", records=list_overnight_orders(user_id))
    assert [(e["type"], e["origin"]) for e in trace["events"]] == [("order.entry_submitted", "record"), ("order.entry_filled", "record")]
    assert trace["furthest_stage"] == "fill"


def test_a_closed_trade_joins_its_orders_chain(user_id):
    entry = _order(record_id="r-9", correlation_id="c-closed")
    record_overnight_order(user_id, entry)
    record_closed_trade(user_id, "cid-r-9", {"ticker": "AAPL", "entry_client_order_id": "cid-r-9", "close_reason": "stop_filled",
                                             "net_realized_pnl": -12.5, "pnl_status": "complete", "exit_timestamp": "2026-09-29T15:00:00+00:00"})
    closed = ej.read_events(user_id, types=["position.closed"])
    assert len(closed) == 1 and closed[0]["correlation_id"] == "c-closed" and closed[0]["stage"] == "exit"
    assert closed[0]["data"]["net_realized_pnl"] == -12.5
    # Re-recording the identical close (restart recovery) adds nothing.
    record_closed_trade(user_id, "cid-r-9", {"ticker": "AAPL", "entry_client_order_id": "cid-r-9", "close_reason": "stop_filled",
                                             "net_realized_pnl": -12.5, "pnl_status": "complete", "exit_timestamp": "2026-09-29T15:00:00+00:00"})
    assert len(ej.read_events(user_id, types=["position.closed", "position.close_revised"])) == 1


# --- the scan, tickets and approval, end to end ----------------------------------


def _fake_submit(user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry):
    entry["record_id"] = "rec-" + ticker
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"cid-{ticker}")
    record_overnight_order(user_id, entry)  # write-ahead, as the real path does
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate(ticker="AAPL"):
    return {"ticker": ticker, "recommendation": "CALL", "confidence": 80, "strategy": "Breakout",
            "ideal_entry": 100.0, "stop": 95.0, "target": 110.0}


def _broker_patches(candidates, positions=None):
    cross = {"price": 100.0, "disagreement": False, "provider_status": [{"provider": "alpaca", "price": 100.0}, {"provider": "webull", "price": 100.0}]}
    return [
        patch.object(pluto_app, "get_webull_credentials", return_value=CREDS),
        patch.object(pluto_app, "is_webull_configured", return_value=True),
        patch.object(pluto_app, "get_anthropic_api_key", return_value=""),
        patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]),
        patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]),
        patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}),
        patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None),
        patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"),
        patch.object(pluto_app.webull_api, "get_account_positions", return_value=positions or []),
        patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]),
        patch.object(pluto_app.webull_api, "get_order_history", return_value=[]),
        patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0),
        patch.object(pluto_app.market_data_aggregator, "get_cross_checked_price", return_value=cross),
        patch.object(pluto_app.webull_api, "get_account_balance",
                     return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                   "account_currency_assets": [{"buying_power": "1000000"}]}),
        patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": candidates}),
        patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit),
        patch.object(pluto_app, "time"),
    ]


def _with_patches(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def _scan(user_id, candidates, dry_run=False):
    return _with_patches(_broker_patches(candidates), lambda: pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=dry_run))


def _registered_user(suffix: str) -> str:
    user = auth.register_user(f"journal-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_autonomous_entry_is_one_chain_from_signal_to_protection(user_id):
    set_mode(user_id, "AUTONOMOUS")
    result = _scan(user_id, [_candidate()])
    assert result["placed_count"] == 1
    order = list_overnight_orders(user_id)[0]
    research = [r for r in list_research_decisions(user_id) if r["ticker"] == "AAPL"][0]
    correlation_id = order["correlation_id"]
    assert correlation_id.startswith("c-") and research["correlation_id"] == correlation_id

    trace = ej.trace(user_id, correlation_id, records=list_overnight_orders(user_id))
    types = [e["type"] for e in trace["events"]]
    assert types[:2] == ["signal.evaluated", "plan.built"]  # dated to the start of evaluation
    assert types[2:] == ["order.entry_submitted", "order.entry_filled", "order.protection_pending", "order.protection_confirmed_active"]
    assert trace["stages_reached"] == ["signal", "plan", "order", "fill", "protection"]
    assert all(e["origin"] == "journal" for e in trace["events"])  # nothing had to be re-derived
    assert trace["events"][0]["data"]["decision"] == "placed"


def test_skipped_candidates_get_their_own_ids_and_previews_write_nothing(user_id):
    set_mode(user_id, "AUTONOMOUS")
    _scan(user_id, [_candidate("AAPL")], dry_run=True)
    assert ej.read_events(user_id) == []
    _scan(user_id, [{**_candidate("MSFT"), "confidence": 10}])
    events = ej.read_events(user_id)
    assert [(e["type"], e["data"]["skip_category"]) for e in events] == [("signal.evaluated", "confidence_threshold")]
    assert ej.recent_chains(user_id) == []  # never got past signal evaluation


def test_approval_flow_is_one_chain_and_a_refresh_joins_it(user_id):
    uid = _registered_user(user_id[:8])
    set_mode(uid, "APPROVAL")
    _scan(uid, [_candidate()])
    _scan(uid, [_candidate()])  # same terms: refreshes the same ticket
    ticket = tt.list_tickets(uid)[0]
    correlation_id = ticket["correlation_id"]
    assert ticket["seen_count"] == 2
    assert [r["correlation_id"] for r in list_research_decisions(uid)] == [correlation_id, correlation_id]

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response = _with_patches(_broker_patches([]), lambda: client.post(
            f"/api/trade-tickets/{ticket['ticket_id']}/approve", json={"version": ticket["version"]}))
        assert response.status_code == 200, response.get_json()
        trace_response = client.get(f"/api/events/trace/{correlation_id}")
        chains = client.get("/api/events/chains").get_json()["data"]["chains"]
        missing = client.get("/api/events/trace/c-nope")

    assert trace_response.status_code == 200
    trace = trace_response.get_json()["data"]["trace"]
    types = [e["type"] for e in trace["events"]]
    assert types.count("signal.evaluated") == 2 and types.count("plan.built") == 2
    for expected in ("ticket.awaiting_approval", "ticket.approved", "order.entry_submitted", "order.entry_filled",
                     "order.protection_confirmed_active", "ticket.submitted"):
        assert expected in types, (expected, types)
    assert types.index("ticket.awaiting_approval") < types.index("ticket.approved") < types.index("order.entry_submitted")
    assert list_overnight_orders(uid)[0]["correlation_id"] == correlation_id
    assert chains[0]["correlation_id"] == correlation_id and chains[0]["furthest_stage"] == "protection"
    assert chains[0]["env"] == "sandbox"
    assert missing.status_code == 404


def test_events_are_per_user(user_id, other_user_id):
    ej.emit(user_id, "signal.evaluated", correlation_id="c-mine")
    assert ej.read_events(other_user_id) == []
    uid = _registered_user(other_user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        assert client.get("/api/events").get_json()["data"]["events"] == []
        assert client.get("/api/events/trace/c-mine").status_code == 404


@pytest.mark.parametrize("route", ["/api/events", "/api/events/chains", "/api/events/trace/c-1"])
def test_event_routes_require_login(route):
    with pluto_app.app.test_client() as client:
        assert client.get(route).status_code in (302, 401)
