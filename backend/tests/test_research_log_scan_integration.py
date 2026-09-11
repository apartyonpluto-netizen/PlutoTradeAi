from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.research_log import RESEARCH_LOG_SCHEMA_VERSION, list_research_decisions

CREDS = {"app_key": "key", "app_secret": "secret"}
ZERO_QTY_SENTINEL_PRICE = 987654.0


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"fake-cid-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _opportunities():
    # One of each category the durable research log must capture -
    # deliberately including candidates that NEVER reach order submission,
    # so a raw len(opportunities) == len(research log records) comparison
    # is a genuine proof of "nothing was silently dropped."
    return [
        {  # placed
            "ticker": "AAPL", "recommendation": "CALL", "confidence": 90,
            "ideal_entry": 100.0, "stop": 50.0, "target": 110.0, "strategy": "Trend Reversal",
            "market_context": {"ticker": "AAPL", "rsi": 61.0}, "strategies_evaluated": ["Trend Reversal", "Momentum"],
        },
        {  # below the confidence floor - never becomes "qualifying" at all
            "ticker": "SLOW", "recommendation": "CALL", "confidence": 20,
            "ideal_entry": 50.0, "stop": 25.0, "target": 60.0, "strategy": "Momentum",
            "market_context": {"ticker": "SLOW", "rsi": 44.0}, "strategies_evaluated": ["Momentum"],
        },
        {  # not a bullish setup - filtered before qualifying too
            "ticker": "BEAR", "recommendation": "WAIT", "confidence": 90,
            "ideal_entry": 50.0, "stop": 25.0, "target": 60.0, "strategy": "Mean Reversion",
            "market_context": {"ticker": "BEAR", "rsi": 30.0}, "strategies_evaluated": ["Mean Reversion"],
        },
        {  # qualifies technically, but sizing rejects it for risk/buying-power reasons
            "ticker": "HUGE", "recommendation": "CALL", "confidence": 85,
            "ideal_entry": ZERO_QTY_SENTINEL_PRICE, "stop": ZERO_QTY_SENTINEL_PRICE - 1.0, "target": ZERO_QTY_SENTINEL_PRICE + 1.0,
            "strategy": "Breakout",
            "market_context": {"ticker": "HUGE", "rsi": 70.0}, "strategies_evaluated": ["Breakout"],
        },
        {  # qualifies and sizes, but the LLM step vetoes it
            "ticker": "VETO", "recommendation": "CALL", "confidence": 88,
            "ideal_entry": 60.0, "stop": 30.0, "target": 70.0, "strategy": "Trend Reversal",
            "market_context": {"ticker": "VETO", "rsi": 66.0}, "strategies_evaluated": ["Trend Reversal"],
        },
    ]


def _run_scan_and_capture(user_id, opportunities):
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
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        # Matches each candidate's own ideal_entry so the new pre-submission
        # freshness check (see app.py's _price_has_drifted_too_far) never
        # trips for tickers this test's assertions expect to actually
        # reach submission - only AAPL genuinely gets that far (the others
        # are filtered by confidence/direction/sizing/LLM veto before this
        # would even matter), but mapping every ticker keeps this correct
        # regardless of which ones reach it.
        stack.enter_context(patch.object(
            pluto_app.alpaca_data, "get_latest_trade_price",
            side_effect=lambda ticker: {
                "AAPL": 100.0, "SLOW": 50.0, "BEAR": 50.0, "HUGE": ZERO_QTY_SENTINEL_PRICE, "VETO": 60.0,
            }.get(ticker, 100.0),
        ))
        stack.enter_context(
            patch.object(
                pluto_app.webull_api, "get_account_balance",
                return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
            )
        )
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
        # record_overnight_order and record_research_decision are NOT
        # mocked here - this test proves genuine durable persistence, not
        # just in-memory HTTP-response content.
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result


def test_every_evaluated_opportunity_gets_a_durable_research_record_no_survivorship_bias(user_id):
    opportunities = _opportunities()
    result = _run_scan_and_capture(user_id, opportunities)

    records = list_research_decisions(user_id)
    # The core anti-bias proof: every opportunity the scan looked at this
    # tick produced exactly one durable record, not just the ones that
    # happened to reach order submission.
    assert len(records) == len(opportunities)
    logged_tickers = {r["ticker"] for r in records}
    assert logged_tickers == {"AAPL", "SLOW", "BEAR", "HUGE", "VETO"}


def test_a_placed_trade_is_recorded_as_placed_with_its_join_id(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    placed = records["AAPL"]
    assert placed["decision"] == "placed"
    assert placed["entry_client_order_id"] == "fake-cid-AAPL"
    assert placed["regime_shadow"]["entry_client_order_id"] == "fake-cid-AAPL"


def test_a_below_threshold_candidate_is_recorded_as_skipped(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    below_floor = records["SLOW"]
    assert below_floor["decision"] == "skipped"
    assert "threshold" in below_floor["reason_skipped"]
    assert below_floor["raw_confidence"] == 20


def test_a_non_bullish_recommendation_is_recorded_not_silently_dropped(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    non_call = records["BEAR"]
    assert non_call["decision"] == "skipped"
    assert non_call["recommendation"] == "WAIT"


def test_a_risk_buying_power_rejected_candidate_is_recorded_with_zero_quantity(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    rejected = records["HUGE"]
    assert rejected["decision"] == "skipped"
    assert rejected["quantity"] == 0
    assert "risk/buying-power" in rejected["reason_skipped"] or "zero quantity" in rejected["reason_skipped"]


def test_an_llm_vetoed_candidate_is_recorded_as_skipped_with_the_veto_reason(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    vetoed = records["VETO"]
    assert vetoed["decision"] == "skipped"
    assert "vetoed" in vetoed["reason_skipped"].lower()


def test_every_record_carries_a_regime_shadow_block_for_future_backtesting(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    for record in list_research_decisions(user_id):
        shadow = record.get("regime_shadow")
        assert isinstance(shadow, dict)
        assert shadow.get("regime_mode") == "shadow"


def test_records_are_versioned(user_id):
    _run_scan_and_capture(user_id, _opportunities())
    for record in list_research_decisions(user_id):
        assert record["schema_version"] == RESEARCH_LOG_SCHEMA_VERSION


def test_research_logging_failure_does_not_block_the_real_scan(user_id):
    with patch.object(pluto_app, "record_research_decision", side_effect=RuntimeError("disk full")):
        result = _run_scan_and_capture(user_id, [_opportunities()[0]])  # just the placed candidate
    assert result["placed_count"] == 1
    assert result["placed"][0]["status"] == "placed"


def test_skip_category_is_recorded_for_every_skip_reason(user_id):
    # skip_category was computed at every branch of the scan loop but
    # silently never reached this durable log before 2026-09-11 - this is
    # the regression test for that fix, one assertion per distinct branch.
    _run_scan_and_capture(user_id, _opportunities())
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    assert records["SLOW"]["skip_category"] == "confidence_threshold"
    assert records["BEAR"]["skip_category"] == "not_call_or_put"
    assert records["HUGE"]["skip_category"] == "sizing_too_small"
    assert records["VETO"]["skip_category"] == "llm_veto"
    assert records["AAPL"]["skip_category"] is None


def test_signal_snapshot_carries_strategy_brains_market_context_through_untouched(user_id):
    # The new "memory layer": signal_snapshot is a pure pass-through of
    # whatever strategy_brain already computed for this candidate (carried
    # on the opportunity dict by _build_page_context) - never recomputed or
    # re-fetched by the research log itself. Proven here by round-tripping
    # a distinct market_context/strategies_evaluated per ticker and
    # checking each logged record against its own opportunity's values,
    # not just checking the keys exist.
    opportunities = _opportunities()
    _run_scan_and_capture(user_id, opportunities)
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    for opp in opportunities:
        snapshot = records[opp["ticker"]]["signal_snapshot"]
        assert isinstance(snapshot, dict)
        assert snapshot["market_context"] == opp["market_context"]
        assert snapshot["strategies_evaluated"] == opp["strategies_evaluated"]


def test_signal_snapshot_is_shadow_only_and_never_read_back_into_the_scans_own_decisions(user_id):
    # Same shadow-mode guarantee regime_shadow already has: a candidate
    # whose signal_snapshot content looks maximally favorable (high RSI,
    # every strategy agreeing) must still be skipped/placed based only on
    # confidence/recommendation/sizing/LLM - never on signal_snapshot
    # itself, since nothing in the scan reads it back.
    opportunities = _opportunities()
    for opp in opportunities:
        opp["market_context"] = {"ticker": opp["ticker"], "rsi": 99.9, "looks_great": True}
        opp["strategies_evaluated"] = ["Everything Agrees"]
    result = _run_scan_and_capture(user_id, opportunities)
    records = {r["ticker"]: r for r in list_research_decisions(user_id)}
    # Same skip/placement outcome as the un-juiced fixture - the favorable
    # signal_snapshot content changed nothing about the real decision.
    assert result["placed_count"] == 1
    assert records["SLOW"]["decision"] == "skipped"
    assert records["BEAR"]["decision"] == "skipped"
    assert records["HUGE"]["decision"] == "skipped"
    assert records["VETO"]["decision"] == "skipped"
    assert records["AAPL"]["decision"] == "placed"
