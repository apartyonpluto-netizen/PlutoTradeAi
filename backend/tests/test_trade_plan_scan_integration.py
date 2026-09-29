"""The trade plan is built inside the scan from the scan's own sizing, so the
plan's quantity is the quantity the scan actually submits, and every
evaluated candidate's plan lands in the research log."""

from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.research_log import list_research_decisions

CREDS = {"app_key": "key", "app_secret": "secret"}


def _fake_submit(user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate(stop_ratio=0.95, target_ratio=1.10):
    return {"ticker": "AAPL", "recommendation": "CALL", "confidence": 80, "strategy": "Breakout",
            "ideal_entry": 100.0, "stop": 100.0 * stop_ratio, "target": 100.0 * target_ratio}


def _scan(user_id, candidate, dry_run=False):
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0) as latest, \
         patch.object(pluto_app.webull_api, "get_account_balance",
                      return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                    "account_currency_assets": [{"buying_power": "1000000"}]}), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [candidate]}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit), \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=dry_run)
    return result, latest


def test_a_placed_entry_carries_a_plan_sized_exactly_like_the_order(user_id):
    result, _ = _scan(user_id, _candidate())
    assert result["placed_count"] == 1
    placed = result["placed"][0]
    plan = placed["trade_plan"]
    assert plan["planner_version"] == "trade_plan_v1"
    assert plan["numbers"]["quantity"] == placed["quantity"]
    assert plan["numbers"]["current_quote"] == 100.0
    assert plan["decision"] == "trade"


def test_the_research_log_records_the_plan(user_id):
    _scan(user_id, _candidate())
    logged = [r for r in list_research_decisions(user_id) if r.get("ticker") == "AAPL"]
    assert logged and logged[-1]["trade_plan"]["decision"] == "trade"
    assert logged[-1]["trade_plan"]["numbers"]["quantity"] >= 1


def test_the_plan_is_advisory_and_does_not_change_what_the_scan_places(user_id):
    """Reward-to-risk 0.2 makes the plan say "watch", but this version only
    records that - the scan's existing checks still decide."""
    result, _ = _scan(user_id, _candidate(stop_ratio=0.5, target_ratio=1.1))
    assert result["placed_count"] == 1
    assert result["placed"][0]["trade_plan"]["decision"] == "watch"


def test_a_preview_gets_a_plan_without_spending_a_market_data_call(user_id):
    result, latest = _scan(user_id, _candidate(), dry_run=True)
    plan = result["placed"][0]["trade_plan"]
    assert plan["decision"] == "trade"
    assert plan["numbers"]["current_quote"] is None
    latest.assert_not_called()
