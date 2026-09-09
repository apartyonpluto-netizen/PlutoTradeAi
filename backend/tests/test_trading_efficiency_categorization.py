from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol

"""Trading-efficiency funnel visibility (2026-09-09). The user is weighing
moving to live trading but wants to see the autonomous agent trade more
*efficiently* first - previously there was no view anywhere of how many
candidates the scanner finds vs. how many convert, or a categorized
breakdown of why the rest get dropped. This adds a `skip_category` string
constant at every skip/place site in _run_autonomous_trade_scan_locked's
per-candidate loop (plus `instrument_type` and, for the options path,
`option_contract_found`), and extends _summarize_scan_result_for_run_log
to tally those into `skip_categories`/`option_stats` for
autonomy/efficiency_report.py to sum across a window. These tests prove
two things: the aggregation math in isolation, and that the categories are
actually stamped by production code during a real scan - not just
asserted in a hand-built dict."""

CREDS = {"app_key": "key", "app_secret": "secret"}
MARGIN_ACCOUNT_ID = "acct-margin-1"
CASH_ACCOUNT_ID = "acct-cash-1"
OPTION_SYMBOL = "NVDA260918C00105000"


# --- _summarize_scan_result_for_run_log: skip_categories/option_stats aggregation, in isolation ---


def test_skip_categories_tallies_the_skip_category_field_across_skipped_records():
    scan_result = {
        "candidates_found": 3,
        "candidates_qualifying": 2,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {"ticker": "NVDA", "reason_skipped": "sized to 0 shares", "skip_category": "sizing_too_small"},
            {"ticker": "AMD", "reason_skipped": "LLM vetoed", "skip_category": "llm_veto"},
            {"ticker": "TSLA", "reason_skipped": "LLM vetoed too", "skip_category": "llm_veto"},
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert summary["skip_categories"] == {"sizing_too_small": 1, "llm_veto": 2}


def test_skip_categories_ignores_entries_with_no_skip_category_field():
    """A record written before this feature existed (or a skip site that
    genuinely has no category, like a plain pre-qualification miss) simply
    contributes nothing here - not a KeyError, not a fake "unknown" bucket
    the frontend has to special-case."""
    scan_result = {
        "candidates_found": 1,
        "candidates_qualifying": 0,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {"ticker": "SPY", "reason_skipped": "confidence 40 below 65 threshold"},
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert summary["skip_categories"] == {}


def test_option_stats_reflects_the_scan_results_option_counters():
    scan_result = {
        "candidates_found": 2,
        "candidates_qualifying": 2,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [],
        "option_attempted": 3,
        "option_contract_found": 1,
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert summary["option_stats"] == {"attempted": 3, "contract_found": 1}


def test_option_stats_defaults_to_zero_when_absent():
    """A scan_result from before these counters existed (or a tick where
    the options block was never reached at all - e.g. no qualifying
    candidates) must not raise, and must report a real zero, not None."""
    scan_result = {
        "candidates_found": 0,
        "candidates_qualifying": 0,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert summary["option_stats"] == {"attempted": 0, "contract_found": 0}


# --- real scan: skip_category/instrument_type actually stamped by production code, cash-account paths ---


ZERO_QTY_SENTINEL_PRICE = 987654.0


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"fake-cid-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _mixed_opportunities():
    # One candidate per equity-path skip category this test asserts on,
    # plus one that reaches a real placement - mirrors
    # test_research_log_scan_integration.py's own proven "one of each"
    # fixture shape.
    return [
        {  # placed
            "ticker": "AAPL", "recommendation": "CALL", "confidence": 90,
            "ideal_entry": 100.0, "stop": 50.0, "target": 110.0, "strategy": "Trend Reversal",
        },
        {  # below the confidence floor - never qualifies at all
            "ticker": "SLOW", "recommendation": "CALL", "confidence": 20,
            "ideal_entry": 50.0, "stop": 25.0, "target": 60.0, "strategy": "Momentum",
        },
        {  # not a bullish setup - filtered before qualifying too
            "ticker": "BEAR", "recommendation": "WAIT", "confidence": 90,
            "ideal_entry": 50.0, "stop": 25.0, "target": 60.0, "strategy": "Mean Reversion",
        },
        {  # qualifies technically, but sizing rejects it
            "ticker": "HUGE", "recommendation": "CALL", "confidence": 85,
            "ideal_entry": ZERO_QTY_SENTINEL_PRICE, "stop": ZERO_QTY_SENTINEL_PRICE - 1.0, "target": ZERO_QTY_SENTINEL_PRICE + 1.0,
            "strategy": "Breakout",
        },
        {  # qualifies and sizes, but the LLM step vetoes it
            "ticker": "VETO", "recommendation": "CALL", "confidence": 88,
            "ideal_entry": 60.0, "stop": 30.0, "target": 70.0, "strategy": "Trend Reversal",
        },
    ]


def _run_mixed_scan(user_id, opportunities):
    original_sizing_fn = pluto_app._compute_position_quantity

    def _sizing_side_effect(**kwargs):
        if kwargs.get("entry_price") == ZERO_QTY_SENTINEL_PRICE:
            return {"quantity": 0, "reason": "test-forced zero quantity (risk/buying-power)", "constraints": {}, "binding_constraints": []}
        return original_sizing_fn(**kwargs)

    def _llm_verdict_side_effect(opp, api_key):
        if opp.get("ticker") == "VETO":
            return {"available": True, "verdict": "veto", "confidence_adjustment": -30, "reasoning": "test-forced veto"}
        return {"available": False, "reason": "no key"}

    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
        stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
        stack.enter_context(patch.object(pluto_app, "get_anthropic_api_key", return_value="fake-key"))
        stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": CASH_ACCOUNT_ID}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": CASH_ACCOUNT_ID}))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        stack.enter_context(patch.object(
            pluto_app.alpaca_data, "get_latest_trade_price",
            side_effect=lambda ticker: {
                "AAPL": 100.0, "SLOW": 50.0, "BEAR": 50.0, "HUGE": ZERO_QTY_SENTINEL_PRICE, "VETO": 60.0,
            }.get(ticker, 100.0),
        ))
        stack.enter_context(patch.object(
            pluto_app.webull_api, "get_account_balance",
            return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
        ))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": 18.0, "source_time": None, "fetch_time": None, "age_seconds": 30.0,
            "status": "fresh", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        stack.enter_context(patch.object(pluto_app, "_compute_position_quantity", side_effect=_sizing_side_effect))
        stack.enter_context(patch.object(pluto_app, "get_llm_verdict", side_effect=_llm_verdict_side_effect))
        stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result


def _by_ticker(records, ticker):
    matches = [r for r in records if r.get("ticker") == ticker]
    assert len(matches) == 1, f"expected exactly one record for {ticker}, got {len(matches)}"
    return matches[0]


def test_real_scan_stamps_the_right_skip_category_at_each_equity_skip_site(user_id):
    result = _run_mixed_scan(user_id, _mixed_opportunities())

    huge = _by_ticker(result["skipped"], "HUGE")
    assert huge["skip_category"] == "sizing_too_small"
    assert huge["instrument_type"] == "EQUITY"

    veto = _by_ticker(result["skipped"], "VETO")
    assert veto["skip_category"] == "llm_veto"
    assert veto["instrument_type"] == "EQUITY"

    aapl = _by_ticker(result["placed"], "AAPL")
    assert aapl["instrument_type"] == "EQUITY"
    assert "skip_category" not in aapl  # placed, never skipped - no category to report

    summary = pluto_app._summarize_scan_result_for_run_log(result)
    assert summary["skip_categories"]["sizing_too_small"] == 1
    assert summary["skip_categories"]["llm_veto"] == 1
    # SLOW/BEAR never reached "qualifying" (below threshold / non-bullish) -
    # they're skipped for a different, pre-loop reason entirely, tallied
    # under their own categories, not folded into the equity-loop ones.
    assert summary["skip_categories"].get("confidence_threshold") == 1
    assert summary["skip_categories"].get("not_call_or_put") == 1


def test_real_scan_stamps_no_margin_account_skip_category(user_id):
    put_candidate = {
        "ticker": "SPY", "recommendation": "PUT", "confidence": 85,
        "ideal_entry": 400.0, "stop": 420.0, "target": 380.0, "strategy": "Trend Reversal",
    }
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
        stack.enter_context(patch.object(
            pluto_app.webull_api, "get_account_balance",
            return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
        ))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [put_candidate]}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": None, "source_time": None, "fetch_time": None, "age_seconds": None,
            "status": "unavailable", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    assert result["placed_count"] == 0
    skipped = _by_ticker(result["skipped"], "SPY")
    assert skipped["skip_category"] == "no_margin_account"
    # A PUT with no margin account never even reaches the options-attempt
    # block (that block requires margin_account_id) - the funnel counters
    # must reflect that honestly, not count a lookup that never happened.
    assert result["option_attempted"] == 0


def test_real_scan_stamps_price_drift_skip_category(user_id):
    candidate = {
        "ticker": "WDAY", "recommendation": "CALL", "confidence": 80,
        "ideal_entry": 100.0, "stop": 50.0, "target": 110.0, "strategy": "Momentum",
    }
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
        # Real price is 10% away from the $100 scan-time entry - well past
        # the 2% default drift threshold (see test_entry_price_drift_gate.py).
        stack.enter_context(patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=110.0))
        stack.enter_context(patch.object(
            pluto_app.webull_api, "get_account_balance",
            return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
        ))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [candidate]}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": None, "source_time": None, "fetch_time": None, "age_seconds": None,
            "status": "unavailable", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    assert result["placed_count"] == 0
    skipped = _by_ticker(result["skipped"], "WDAY")
    assert skipped["skip_category"] == "price_drift"
    assert skipped["instrument_type"] == "EQUITY"


# --- real scan: options-path funnel counters and per-record stamps ---------------


def _option_scan_accounts_and_balances():
    accounts = [
        {"account_id": CASH_ACCOUNT_ID, "account_class": "INDIVIDUAL_CASH"},
        {"account_id": MARGIN_ACCOUNT_ID, "account_class": "INDIVIDUAL_MARGIN"},
    ]
    balances = {
        CASH_ACCOUNT_ID: {
            "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "1000000"}],
        },
        MARGIN_ACCOUNT_ID: {
            "total_net_liquidation_value": 1000000.0, "total_day_profit_loss": 0.0,
            "account_currency_assets": [{"buying_power": "4000000", "option_buying_power": "1000000"}],
        },
    }
    return accounts, balances


def _run_option_scan(user_id, candidate, *, option_contracts, option_snapshot):
    accounts, balances = _option_scan_accounts_and_balances()

    def _fake_get_account_balance(app_key, app_secret, account_id):
        return balances[account_id]

    def _fake_place_option_order(**kwargs):
        return {"client_order_id": kwargs.get("client_order_id") or "opt-entry-cid"}

    def _fake_place_stock_order(**kwargs):
        return {"client_order_id": kwargs.get("client_order_id") or "eq-entry-cid"}

    def _fake_get_order_detail(app_key, app_secret, account_id, client_order_id):
        return {"orders": [{"status": "FILLED", "total_quantity": "1", "filled_quantity": "1", "order_id": "X", "avg_filled_price": "5.0"}]}

    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
        stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
        stack.enter_context(patch.object(pluto_app, "get_anthropic_api_key", return_value=""))
        stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        stack.enter_context(patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_balance", side_effect=_fake_get_account_balance))
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [candidate]}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
            "vix_level": None, "source_time": None, "fetch_time": None, "age_seconds": None,
            "status": "unavailable", "used_stale_cache": False,
        }))
        stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_option_contracts", return_value=option_contracts))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=option_snapshot))
        stack.enter_context(patch.object(pluto_app.webull_api, "place_option_order", side_effect=_fake_place_option_order))
        stack.enter_context(patch.object(pluto_app.webull_api, "place_stock_order", side_effect=_fake_place_stock_order))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_detail", side_effect=_fake_get_order_detail))
        stack.enter_context(patch.object(pluto_app, "time"))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result


def test_real_scan_stamps_option_contract_found_when_a_contract_is_available(user_id):
    candidate = {
        "ticker": "NVDA", "recommendation": "CALL", "confidence": 82,
        "ideal_entry": 100.0, "stop": 95.0, "target": 110.0, "strategy": "Breakout Continuation",
    }
    contracts = [{
        "symbol": OPTION_SYMBOL, "strike_price": "105", "expiration_date": "2026-09-18",
        "option_type": "CALL", "underlying_symbol": "NVDA",
    }]
    snapshot = [{"symbol": OPTION_SYMBOL, "bid": "4.90", "ask": "5.10", "delta": "0.45"}]

    result = _run_option_scan(user_id, candidate, option_contracts=contracts, option_snapshot=snapshot)

    assert result["placed_count"] == 1
    assert result["option_attempted"] == 1
    assert result["option_contract_found"] == 1
    placed = _by_ticker(result["placed"], "NVDA")
    assert placed["instrument_type"] == "OPTION"
    assert placed["option_contract_found"] is True

    summary = pluto_app._summarize_scan_result_for_run_log(result)
    assert summary["option_stats"] == {"attempted": 1, "contract_found": 1}


def test_real_scan_counts_an_attempt_without_a_contract_found_when_falling_back_to_equity(user_id):
    """The genuinely tricky case: select_option_contract was attempted and
    returned None, so this candidate falls straight through to the equity
    path with no options-specific placed/skipped record of its own (see
    the "additive, not a replacement" comment in
    _run_autonomous_trade_scan_locked) - the funnel counters are the ONLY
    place this attempt is visible at all."""
    candidate = {
        "ticker": "AMD", "recommendation": "CALL", "confidence": 82,
        "ideal_entry": 100.0, "stop": 95.0, "target": 110.0, "strategy": "Breakout Continuation",
    }

    result = _run_option_scan(user_id, candidate, option_contracts=[], option_snapshot=[])

    assert result["placed_count"] == 1  # the equity fallback still placed it
    assert result["option_attempted"] == 1
    assert result["option_contract_found"] == 0
    placed = _by_ticker(result["placed"], "AMD")
    assert placed["instrument_type"] == "EQUITY"
    assert "option_contract_found" not in placed

    summary = pluto_app._summarize_scan_result_for_run_log(result)
    assert summary["option_stats"] == {"attempted": 1, "contract_found": 0}
