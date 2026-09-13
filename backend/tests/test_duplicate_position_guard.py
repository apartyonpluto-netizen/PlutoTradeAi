from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import replace_overnight_orders

"""Real gap found 2026-09-12 while auditing for duplicate protection:
already_placed_today (the scan's only prior same-ticker guard) is scoped
to TODAY's trading day only - a multi-day swing hold opened Monday and
still open Wednesday would silently fall through it, and a fresh bullish
signal on Wednesday would let the scan place a second, entirely
independent entry in a ticker it already holds. tickers_with_open_positions
closes this: any overnight_orders record still short of a terminal
lifecycle state (ol.is_transitional), regardless of which day it was
opened, now excludes that ticker from qualifying - covering equity AND
option entries (both store the underlying ticker in this same field),
without needing to parse get_account_positions' still-unconfirmed OPTION
row shape."""

CREDS = {"app_key": "key", "app_secret": "secret"}
CASH_ACCOUNT_ID = "acct-cash-1"


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"fake-cid-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _run_scan(opportunities, user_id):
    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
        stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
        stack.enter_context(patch.object(pluto_app, "get_anthropic_api_key", return_value=""))
        stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": CASH_ACCOUNT_ID}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": CASH_ACCOUNT_ID}))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        stack.enter_context(patch.object(pluto_app.alpaca_data, "get_latest_trade_price", side_effect=lambda t: 100.0))
        stack.enter_context(patch.object(
            pluto_app.webull_api, "get_account_balance",
            return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
        ))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": None, "source_time": None, "fetch_time": None, "age_seconds": None,
            "status": "unavailable", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        mock_submit = stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry))
        stack.enter_context(patch.object(pluto_app, "select_option_contract", return_value=None))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result, mock_submit


def _call(ticker, *, confidence=82):
    return {
        "ticker": ticker, "recommendation": "CALL", "confidence": confidence,
        "ideal_entry": 100.0, "stop": 95.0, "target": 110.0, "strategy": "Breakout",
    }


def _seed_order(user_id, ticker, lifecycle_state, *, days_ago=0, status="placed"):
    logged_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    replace_overnight_orders(user_id, [{
        "ticker": ticker,
        "status": status,
        "lifecycle_state": lifecycle_state,
        "entry_client_order_id": f"seed-{ticker}",
        "trading_day": "2026-09-08",
        "logged_at": logged_at,
    }])


def _skipped(result, ticker):
    matches = [e for e in result["skipped"] if e.get("ticker") == ticker]
    assert len(matches) == 1, f"expected one skipped record for {ticker}, got {len(matches)}"
    return matches[0]


def test_a_position_still_open_from_an_earlier_day_blocks_a_fresh_entry(user_id):
    # The actual bug: opened Monday (4 days ago), still transitional
    # (never closed) - a fresh signal today must not pyramid into it.
    _seed_order(user_id, "NVDA", ol.PROTECTION_CONFIRMED_ACTIVE, days_ago=4)
    result, mock_submit = _run_scan([_call("NVDA")], user_id)

    assert result["placed_count"] == 0
    mock_submit.assert_not_called()
    rec = _skipped(result, "NVDA")
    assert rec["skip_category"] == "already_holds_position"
    assert "open" in rec["reason_skipped"]


def test_a_closed_position_from_an_earlier_day_does_not_block_a_fresh_entry(user_id):
    # The position from Monday already fully closed (stop/target hit) -
    # nothing left to pyramid into, so today's fresh signal must place
    # normally.
    _seed_order(user_id, "NVDA", ol.CLOSED, days_ago=4)
    result, mock_submit = _run_scan([_call("NVDA")], user_id)

    assert result["placed_count"] == 1
    mock_submit.assert_called_once()


def test_a_transitional_order_from_today_is_categorized_as_holds_position_not_placed_today(user_id):
    # Still-open takes priority over the older, less specific
    # already_placed_today label, even on the same day.
    _seed_order(user_id, "NVDA", ol.ENTRY_FILLED, days_ago=0)
    result, _ = _run_scan([_call("NVDA")], user_id)
    assert _skipped(result, "NVDA")["skip_category"] == "already_holds_position"


def test_a_terminal_order_from_today_is_categorized_as_already_placed_today(user_id):
    # Closed (or otherwise terminal) same-day - the narrower, correct
    # label for "you already fully round-tripped this ticker today."
    _seed_order(user_id, "NVDA", ol.CLOSED, days_ago=0)
    result, mock_submit = _run_scan([_call("NVDA")], user_id)

    mock_submit.assert_not_called()
    rec = _skipped(result, "NVDA")
    assert rec["skip_category"] == "already_placed_today"
    assert "already placed today" in rec["reason_skipped"]


def test_an_unrelated_ticker_with_no_prior_orders_places_normally(user_id):
    _seed_order(user_id, "NVDA", ol.PROTECTION_CONFIRMED_ACTIVE, days_ago=4)
    result, mock_submit = _run_scan([_call("AMD")], user_id)

    assert result["placed_count"] == 1
    mock_submit.assert_called_once()
    assert [e["ticker"] for e in result["placed"]] == ["AMD"]


def test_the_duplicate_position_skip_is_surfaced_in_research_log_with_signal_snapshot(user_id):
    from autonomy.research_log import list_research_decisions

    _seed_order(user_id, "NVDA", ol.PROTECTION_CONFIRMED_ACTIVE, days_ago=4)
    _run_scan([_call("NVDA")], user_id)

    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    assert records["NVDA"]["decision"] == "skipped"
    assert records["NVDA"]["skip_category"] == "already_holds_position"
