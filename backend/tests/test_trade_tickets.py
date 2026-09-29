"""APPROVAL mode: the scan proposes trade tickets instead of submitting;
approval binds to a ticket version and is re-checked against the broker
under the account lock before anything is sent."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import order_lifecycle as ol
from alerts import load_manual_alerts
from autonomy import trade_tickets as tt
from autonomy.autonomous_controller import set_mode
from autonomy.overnight_orders import list_overnight_orders
from autonomy.research_log import list_research_decisions

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _proposal(ticker="AAPL", quantity=10, **extra):
    base = {"trading_day": "2026-09-29", "ticker": ticker, "instrument_type": "EQUITY", "direction": "long", "quantity": quantity,
            "limit_price": 100.0, "stop": 95.0, "target": 110.0, "account_id": ACCOUNT_ID, "strategy": "Breakout", "confidence": 80}
    base.update(extra)
    return base


# --- store ---------------------------------------------------------------------


def test_identical_terms_refresh_the_open_ticket_and_changed_terms_supersede_it(user_id):
    first, outcome = tt.create_or_refresh_ticket(user_id, _proposal())
    assert outcome == "created" and first["status"] == tt.AWAITING_APPROVAL
    again, outcome = tt.create_or_refresh_ticket(user_id, _proposal())
    assert outcome == "refreshed" and again["ticket_id"] == first["ticket_id"] and again["seen_count"] == 2
    bigger, outcome = tt.create_or_refresh_ticket(user_id, _proposal(quantity=12))
    assert outcome == "superseded" and bigger["ticket_id"] != first["ticket_id"]
    by_id = {t["ticket_id"]: t for t in tt.list_tickets(user_id)}
    assert by_id[first["ticket_id"]]["status"] == tt.SUPERSEDED
    assert by_id[first["ticket_id"]]["superseded_by"] == bigger["ticket_id"]
    assert bigger["supersedes"] == first["ticket_id"]
    assert len(tt.open_tickets(user_id)) == 1


def test_claim_is_atomic_and_bound_to_the_version(user_id):
    ticket, _ = tt.create_or_refresh_ticket(user_id, _proposal())
    with pytest.raises(tt.TicketConflict, match="changed since you viewed it"):
        tt.claim_for_approval(user_id, ticket["ticket_id"], "stale-version", approved_by="u")
    claimed = tt.claim_for_approval(user_id, ticket["ticket_id"], ticket["version"], approved_by="u")
    assert claimed["status"] == tt.APPROVED and claimed["approved_version"] == ticket["version"]
    with pytest.raises(tt.TicketConflict, match="already being submitted"):
        tt.claim_for_approval(user_id, ticket["ticket_id"], ticket["version"], approved_by="u")
    # A new proposal for the same ticker never disturbs a ticket mid-submission.
    same, outcome = tt.create_or_refresh_ticket(user_id, _proposal(quantity=99))
    assert outcome == "in_progress" and same["ticket_id"] == ticket["ticket_id"]


def test_an_undecided_ticket_expires_and_cannot_be_approved(user_id):
    now = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
    ticket, _ = tt.create_or_refresh_ticket(user_id, _proposal(), now=now)
    later = now + timedelta(seconds=tt.TICKET_TTL_SECONDS + 1)
    assert tt.list_tickets(user_id, now=later)[0]["status"] == tt.EXPIRED
    with pytest.raises(tt.TicketConflict, match="expired"):
        tt.claim_for_approval(user_id, ticket["ticket_id"], ticket["version"], approved_by="u", now=later)


def test_declining_records_the_ticker_for_that_day_only(user_id):
    ticket, _ = tt.create_or_refresh_ticket(user_id, _proposal())
    declined = tt.decline_ticket(user_id, ticket["ticket_id"], ticket["version"], reason="earnings tonight", declined_by="u")
    assert declined["status"] == tt.DECLINED and declined["decline_reason"] == "earnings tonight"
    assert tt.declined_tickers_today(user_id, "2026-09-29") == {"AAPL"}
    assert tt.declined_tickers_today(user_id, "2026-09-30") == set()
    with pytest.raises(tt.TicketConflict):
        tt.claim_for_approval(user_id, ticket["ticket_id"], ticket["version"], approved_by="u")


# --- scan integration ---------------------------------------------------------


def _fake_submit(user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate():
    return {"ticker": "AAPL", "recommendation": "CALL", "confidence": 80, "strategy": "Breakout",
            "ideal_entry": 100.0, "stop": 95.0, "target": 110.0, "trade_thesis": "Breakout over resistance."}


def _scan_patches(candidates):
    return [
        patch.object(pluto_app, "get_webull_credentials", return_value=CREDS),
        patch.object(pluto_app, "is_webull_configured", return_value=True),
        patch.object(pluto_app, "get_anthropic_api_key", return_value=""),
        patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]),
        patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]),
        patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}),
        patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None),
        patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"),
        patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]),
        patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]),
        patch.object(pluto_app.webull_api, "get_order_history", return_value=[]),
        patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0),
        patch.object(pluto_app.webull_api, "get_account_balance",
                     return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                   "account_currency_assets": [{"buying_power": "1000000"}]}),
        patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": candidates}),
        patch.object(pluto_app, "time"),
    ]


def _run_scan(user_id, candidates, dry_run=False):
    patches = _scan_patches(candidates)
    submit = patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit)
    for p in patches:
        p.start()
    mock_submit = submit.start()
    try:
        result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=dry_run)
    finally:
        submit.stop()
        for p in patches:
            p.stop()
    return result, mock_submit


def test_approval_mode_proposes_a_ticket_instead_of_submitting(user_id):
    set_mode(user_id, "APPROVAL")
    result, submit = _run_scan(user_id, [_candidate()])
    submit.assert_not_called()
    assert result["placed_count"] == 0 and result["proposed_count"] == 1
    proposed = result["proposed"][0]
    assert proposed["status"] == "awaiting_approval" and proposed["ticket_outcome"] == "created"

    tickets = tt.list_tickets(user_id)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket["status"] == tt.AWAITING_APPROVAL
    assert (ticket["ticker"], ticket["quantity"], ticket["limit_price"], ticket["stop"], ticket["target"]) == ("AAPL", proposed["quantity"], 100.0, 95.0, 110.0)
    assert ticket["trade_plan"]["decision"] in ("trade", "watch")
    assert ticket["trade_plan"]["numbers"]["quantity"] == proposed["quantity"]
    assert any(a.get("type") == "trade_ticket_awaiting_approval" for a in load_manual_alerts(user_id))
    assert [r["decision"] for r in list_research_decisions(user_id) if r["ticker"] == "AAPL"] == ["proposed"]
    assert list_overnight_orders(user_id) == []  # a ticket is not an order


def test_the_same_setup_on_the_next_scan_refreshes_the_ticket(user_id):
    set_mode(user_id, "APPROVAL")
    _run_scan(user_id, [_candidate()])
    _run_scan(user_id, [_candidate()])
    tickets = tt.list_tickets(user_id)
    assert len(tickets) == 1 and tickets[0]["seen_count"] == 2


def test_a_declined_ticker_is_not_proposed_again_that_day(user_id):
    set_mode(user_id, "APPROVAL")
    _run_scan(user_id, [_candidate()])
    ticket = tt.list_tickets(user_id)[0]
    tt.decline_ticket(user_id, ticket["ticket_id"], ticket["version"], reason="no", declined_by=user_id)
    result, _ = _run_scan(user_id, [_candidate()])
    assert result["proposed_count"] == 0
    assert result["skipped"][0]["skip_category"] == "declined_today"
    assert len(tt.list_tickets(user_id)) == 1


def test_autonomous_mode_and_previews_never_create_tickets(user_id):
    set_mode(user_id, "AUTONOMOUS")
    result, submit = _run_scan(user_id, [_candidate()])
    assert result["placed_count"] == 1 and submit.call_count == 1
    set_mode(user_id, "APPROVAL")
    preview_candidate = {**_candidate(), "ticker": "MSFT"}  # AAPL is now held, so it would be skipped
    result, submit = _run_scan(user_id, [preview_candidate], dry_run=True)
    assert result["placed"][0]["status"] == "preview"
    assert tt.list_tickets(user_id) == []


# --- approval endpoint ---------------------------------------------------------


def _registered_user(suffix: str) -> str:
    user = auth.register_user(f"ticket-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _approve(client, ticket, fresh_price=100.0, session="CORE", positions=None, disagreement=False):
    cross = {"price": fresh_price, "disagreement": disagreement, "provider_status": [
        {"provider": "alpaca", "price": fresh_price}, {"provider": "webull", "price": fresh_price}]}
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value=session), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=positions or []), \
         patch.object(pluto_app.webull_api, "get_account_balance",
                      return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                    "account_currency_assets": [{"buying_power": "1000000"}]}), \
         patch.object(pluto_app.market_data_aggregator, "get_cross_checked_price", return_value=cross), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit) as submit, \
         patch.object(pluto_app, "time"):
        response = client.post(f"/api/trade-tickets/{ticket['ticket_id']}/approve", json={"version": ticket["version"]})
    return response, submit


def _ticket_for(user_id):
    ticket, _ = tt.create_or_refresh_ticket(user_id, _proposal(trading_day=pluto_app._trading_day_key(), recommendation="CALL"))
    return ticket


def test_approving_rechecks_then_submits_through_the_scans_own_path(user_id):
    uid = _registered_user(user_id[:8])
    ticket = _ticket_for(uid)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response, submit = _approve(client, ticket)
    assert response.status_code == 200, response.get_json()
    submit.assert_called_once()
    kwargs = submit.call_args.kwargs
    assert (kwargs["ticker"], kwargs["requested_quantity"], kwargs["limit_price"], kwargs["stop_price"], kwargs["target_price"]) == ("AAPL", 10, 100.0, 95.0, 110.0)
    stored = tt.get_ticket(uid, ticket["ticket_id"])
    assert stored["status"] == tt.SUBMITTED and stored["submission"]["status"] == "placed"
    assert stored["recheck"]["ok"] is True and stored["recheck"]["fresh_price"] == 100.0
    orders = list_overnight_orders(uid)
    assert orders[0]["source"] == "approved_ticket" and orders[0]["ticket_id"] == ticket["ticket_id"]
    assert orders[0]["status"] == "placed"


def test_price_drift_since_the_ticket_refuses_submission(user_id):
    uid = _registered_user(user_id[:8] + "d")
    ticket = _ticket_for(uid)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response, submit = _approve(client, ticket, fresh_price=110.0)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "recheck_failed"
    submit.assert_not_called()
    stored = tt.get_ticket(uid, ticket["ticket_id"])
    assert stored["status"] == tt.RECHECK_FAILED
    assert any("moved 10.0%" in r for r in stored["recheck"]["reasons"])
    assert list_overnight_orders(uid) == []


def test_outside_core_hours_and_full_position_slots_refuse(user_id):
    uid = _registered_user(user_id[:8] + "h")
    ticket = _ticket_for(uid)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response, submit = _approve(client, ticket, session="NIGHT")
    assert response.status_code == 409 and "CORE trading hours" in response.get_json()["error"]["message"]
    submit.assert_not_called()

    ticket2 = _ticket_for(uid)  # a fresh ticket (the first is RECHECK_FAILED)
    held = [{"symbol": t, "quantity": "1"} for t in ("MSFT", "NVDA", "AMD")]
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response, submit = _approve(client, ticket2, positions=held)
    assert response.status_code == 409 and "max positions reached (3/3" in response.get_json()["error"]["message"]
    submit.assert_not_called()


def test_a_stale_version_and_a_second_approval_are_refused(user_id):
    uid = _registered_user(user_id[:8] + "v")
    ticket = _ticket_for(uid)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
             patch.object(pluto_app, "is_webull_configured", return_value=True):
            stale = client.post(f"/api/trade-tickets/{ticket['ticket_id']}/approve", json={"version": "old"})
        assert stale.status_code == 409 and stale.get_json()["error"]["code"] == "ticket_conflict"
        first, submit = _approve(client, ticket)
        assert first.status_code == 200
        second, submit_again = _approve(client, ticket)
    assert second.status_code == 409
    submit_again.assert_not_called()
    assert submit.call_count == 1


def test_decline_endpoint_and_listing(user_id):
    uid = _registered_user(user_id[:8] + "l")
    ticket = _ticket_for(uid)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        listed = client.get("/api/trade-tickets").get_json()["data"]
        assert listed["awaiting_count"] == 1 and listed["tickets"][0]["ticket_id"] == ticket["ticket_id"]
        declined = client.post(f"/api/trade-tickets/{ticket['ticket_id']}/decline", json={"version": ticket["version"], "reason": "not today"})
        assert declined.status_code == 200
        assert client.get("/api/trade-tickets").get_json()["data"]["awaiting_count"] == 0
    assert tt.get_ticket(uid, ticket["ticket_id"])["status"] == tt.DECLINED


def test_cron_runs_the_full_scan_for_approval_mode_users(user_id):
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "APPROVAL", "approval_required_status": True}), \
         patch.object(pluto_app, "_run_autonomous_trade_scan", return_value={"placed": [], "skipped": [], "proposed": [{"ticker": "AAPL"}],
                                                                              "placed_count": 0, "skipped_count": 0, "proposed_count": 1,
                                                                              "candidates_found": 1, "candidates_qualifying": 1,
                                                                              "entries_allowed": True, "new_entries_blocked_reason": ""}) as scan, \
         patch.object(pluto_app, "time"):
        with pluto_app.app.test_client() as client:
            response = client.post("/api/autonomy/cron-trigger", headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")})
    assert response.status_code == 200
    scan.assert_called_once()
    from autonomy.scan_run_log import list_scan_runs
    assert "1 ticket(s) awaiting your approval" in list_scan_runs(user_id)[0]["reason"]
