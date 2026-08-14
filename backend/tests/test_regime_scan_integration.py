from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
import regime

CREDS = {"app_key": "key", "app_secret": "secret"}


def _fake_submit_and_protect_entry(
    user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry
):
    # Minimal stand-in for the real fill/protection flow (already covered
    # elsewhere) - just enough to reach a terminal lifecycle_state, so this
    # file stays focused on whether the shadow regime block can (it must
    # not) influence anything in the real path.
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="fake-cid")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate(ticker="AAPL", confidence=80, strategy="Trend Reversal"):
    # Deliberately wide stop, same reasoning as test_scan_disk_failure.py's
    # own _candidates() helper - keeps risk-based sizing from consuming the
    # whole virtual balance on one candidate.
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": confidence,
        "ideal_entry": 100.0,
        "stop": 50.0,
        "target": 110.0,
        "strategy": strategy,
    }


def _snapshot(vix_level, status="fresh", used_stale_cache=False, age_seconds=30.0):
    now = datetime.now(timezone.utc)
    return {
        "vix_level": vix_level,
        "source_time": now,
        "fetch_time": now,
        "age_seconds": age_seconds,
        "status": status,
        "used_stale_cache": used_stale_cache,
    }


def _run_scan(
    user_id, opportunities, vix_snapshot, record_overnight_order_mock=None, get_llm_verdict_return=None,
    ai_confidence_threshold=55, get_settings_side_effect=None, record_research_decision_mock=None,
):
    if record_overnight_order_mock is None:
        record_overnight_order_mock = lambda user_id, entry: entry
    if record_research_decision_mock is None:
        record_research_decision_mock = lambda user_id, record: record
    with ExitStack() as stack:
        stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
        stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
        stack.enter_context(
            patch.object(pluto_app, "get_anthropic_api_key", return_value="" if get_llm_verdict_return is None else "fake-key")
        )
        stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]))
        stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}))
        stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
        stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
        stack.enter_context(
            patch.object(
                pluto_app.webull_api,
                "get_account_balance",
                return_value={
                    "total_net_liquidation_value": 100000.0,
                    "total_day_profit_loss": 0.0,
                    "account_currency_assets": [{"buying_power": "1000000"}],
                },
            )
        )
        stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}))
        stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value=vix_snapshot))
        if get_settings_side_effect is not None:
            stack.enter_context(patch.object(pluto_app, "get_settings", side_effect=get_settings_side_effect))
        else:
            stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": ai_confidence_threshold}))
        stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry))
        stack.enter_context(patch.object(pluto_app, "record_overnight_order", side_effect=record_overnight_order_mock))
        stack.enter_context(patch.object(pluto_app, "record_research_decision", side_effect=record_research_decision_mock))
        stack.enter_context(patch.object(pluto_app, "time"))
        if get_llm_verdict_return is not None:
            stack.enter_context(patch.object(pluto_app, "get_llm_verdict", return_value=get_llm_verdict_return))
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)
    return result


# --- the live decision path must be completely unaffected by regime -------------


def test_extreme_vix_cannot_change_whether_an_order_is_submitted(user_id):
    """confidence=60 clears the 55-point floor on raw technical score;
    a crisis-level VIX (-15 proposed) would push a SHADOW-adjusted score
    to 45, below the floor - but the real decision must ignore that
    entirely and still place the trade."""
    result = _run_scan(user_id, [_candidate(confidence=60)], _snapshot(vix_level=55.0))
    assert result["placed_count"] == 1
    assert result["skipped_count"] == 0
    placed_entry = result["placed"][0]
    assert placed_entry["status"] == "placed"
    assert placed_entry["confidence"] == 60  # untouched raw confidence


def test_extreme_vix_does_not_change_quantity_or_sizing(user_id):
    baseline = _run_scan(user_id, [_candidate(confidence=80)], _snapshot(vix_level=10.0))
    extreme = _run_scan(user_id, [_candidate(confidence=80)], _snapshot(vix_level=55.0))
    assert baseline["placed"][0]["quantity"] == extreme["placed"][0]["quantity"]
    assert baseline["placed_count"] == extreme["placed_count"] == 1


def test_regime_shadow_fetch_raising_does_not_block_the_real_scan(user_id):
    """Even get_vix_snapshot itself raising outright (a real bug, not just
    an ordinary "unavailable" result) must not prevent the real order from
    being evaluated and placed - see the try/except around the once-per-tick
    fetch in _run_autonomous_trade_scan_locked."""
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
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [_candidate(confidence=80)]}), \
         patch.object(pluto_app, "get_vix_snapshot", side_effect=RuntimeError("boom")), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry), \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    assert result["placed_count"] == 1
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["vix_status"] == "unavailable"
    assert shadow["proposed_adjustment"] == 0


def test_regime_shadow_per_candidate_block_raising_does_not_block_the_real_scan(user_id):
    """A bug scoped to the PER-CANDIDATE shadow block (the once-per-tick
    fetch/mapping succeed structurally, but the mapping result is
    malformed in a way that only breaks when a candidate tries to read
    it) must also not prevent the real order from being placed - see the
    try/except around the per-candidate shadow block, distinct from the
    one around the once-per-tick fetch."""
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
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [_candidate(confidence=80)]}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value=_snapshot(vix_level=18.0)), \
         patch.object(pluto_app, "compute_shadow_adjustment", return_value=object()), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry), \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    assert result["placed_count"] == 1
    assert result["placed"][0]["regime_shadow"].get("error")


def test_raw_confidence_on_the_real_entry_is_never_mutated_by_regime(user_id):
    result = _run_scan(user_id, [_candidate(confidence=80)], _snapshot(vix_level=35.0))
    assert result["placed"][0]["confidence"] == 80


def test_llm_step_still_uses_raw_confidence_not_a_regime_adjusted_base(user_id):
    """The real decision path (LLM adjustment) must be computed exactly as
    it was before the regime feature existed - based on raw technical
    confidence, never a regime-shadowed value."""
    fake_llm_verdict = {"available": True, "verdict": "confirm", "confidence_adjustment": 3, "reasoning": "solid setup"}
    result = _run_scan(
        user_id, [_candidate(confidence=80)], _snapshot(vix_level=22.0),
        get_llm_verdict_return=fake_llm_verdict,
    )
    assert result["placed_count"] == 1
    entry = result["placed"][0]
    assert entry["llm_adjusted_confidence"] == 83  # 80 + 3, NOT (80 - 5) + 3


def test_no_regime_value_can_bypass_hard_risk_controls(user_id, monkeypatch):
    """The deployment kill switch (a hard risk control) must still block
    every new entry even when the shadow regime signal is maximally
    permissive (VIX very low, zero proposed adjustment)."""
    monkeypatch.setenv("PLUTO_DISABLE_NEW_ENTRIES", "true")
    result = _run_scan(user_id, [_candidate(confidence=95)], _snapshot(vix_level=5.0))
    assert result["placed_count"] == 0
    for item in result["skipped"]:
        assert "kill switch" in item["reason_skipped"]


# --- shadow record content ---------------------------------------------------------


def test_shadow_record_marks_mode_and_never_sets_entry_status_from_it(user_id):
    result = _run_scan(user_id, [_candidate(confidence=60)], _snapshot(vix_level=55.0))
    entry = result["placed"][0]
    assert entry["regime_shadow"]["regime_mode"] == "shadow"
    assert entry["status"] == "placed"  # never "skipped" due to regime, however extreme


def test_stale_or_missing_vix_is_neutral_and_clearly_labeled(user_id):
    unavailable_snapshot = _snapshot(vix_level=None, status="unavailable", age_seconds=None)
    result = _run_scan(user_id, [_candidate(confidence=80)], unavailable_snapshot)
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["vix_status"] == "unavailable"
    assert shadow["proposed_adjustment"] == 0
    assert shadow["shadow_adjusted_confidence"] == 80


def test_stale_fallback_is_labeled_stale_not_fresh_and_flagged(user_id):
    stale_snapshot = _snapshot(vix_level=32.0, status="stale", used_stale_cache=True)
    result = _run_scan(user_id, [_candidate(confidence=80)], stale_snapshot)
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["vix_status"] == "stale"
    assert shadow["vix_used_stale_cache"] is True


def test_the_global_floor_is_used_when_the_user_has_not_raised_their_own_threshold(user_id):
    """With ai_confidence_threshold == OVERNIGHT_MIN_CONFIDENCE (55), the
    effective threshold is just that global floor - the baseline case."""
    result = _run_scan(user_id, [_candidate(confidence=60)], _snapshot(vix_level=30.0), ai_confidence_threshold=55)  # 60 - 15 = 45
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["actual_decision_threshold"] == pluto_app.OVERNIGHT_MIN_CONFIDENCE == 55
    assert shadow["shadow_adjusted_confidence"] == 45
    assert shadow["shadow_crosses_threshold"] is False
    assert shadow["raw_crosses_threshold"] is True
    assert shadow["would_change_decision"] is True


def test_a_users_own_raised_confidence_threshold_is_used_not_the_global_constant(user_id):
    """A user who raised their Account Hub 'AI confidence threshold' above
    OVERNIGHT_MIN_CONFIDENCE (e.g. to 80, the platform's own default) must
    have THAT value drive the shadow comparison - using only the 55
    constant here would make would_change_decision wrong for exactly this
    user, since production itself (via _build_page_context's own
    ai_confidence_threshold filter) never even shows this scan a candidate
    below 80 in the first place."""
    result = _run_scan(user_id, [_candidate(confidence=80)], _snapshot(vix_level=10.0), ai_confidence_threshold=80)
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["actual_decision_threshold"] == 80
    assert shadow["raw_crosses_threshold"] is True  # 80 >= 80
    assert shadow["shadow_adjusted_confidence"] == 80  # VIX=10 -> zero proposed adjustment
    assert shadow["shadow_crosses_threshold"] is True


def test_a_users_lowered_threshold_never_drops_below_the_global_floor(user_id):
    """A user who lowered their own setting below OVERNIGHT_MIN_CONFIDENCE
    must not make the shadow comparison MORE lenient than production
    actually is - the scan's own `qualifying` filter still enforces the
    55 floor regardless of a lower per-user setting, so the shadow's
    effective threshold must stay floored at 55 too."""
    result = _run_scan(user_id, [_candidate(confidence=60)], _snapshot(vix_level=10.0), ai_confidence_threshold=30)
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["actual_decision_threshold"] == pluto_app.OVERNIGHT_MIN_CONFIDENCE == 55


def test_get_settings_failure_falls_back_to_the_global_floor_without_crashing(user_id):
    """Even if reading the user's settings for the threshold blows up
    outright, the scan must still complete and default to the safe global
    floor - not crash, and not silently use an unbounded/wrong value."""
    result = _run_scan(
        user_id, [_candidate(confidence=60)], _snapshot(vix_level=10.0),
        get_settings_side_effect=RuntimeError("disk error"),
    )
    assert result["placed_count"] == 1
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["actual_decision_threshold"] == pluto_app.OVERNIGHT_MIN_CONFIDENCE


def test_would_change_decision_is_false_when_shadow_agrees_with_production(user_id):
    result = _run_scan(user_id, [_candidate(confidence=80)], _snapshot(vix_level=10.0))
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["would_change_decision"] is False


def test_strategy_and_mapping_version_are_recorded(user_id):
    result = _run_scan(user_id, [_candidate(confidence=80, strategy="Momentum Breakout")], _snapshot(vix_level=18.0))
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["strategy"] == "Momentum Breakout"
    assert shadow["mapping_version"] == regime.REGIME_MAPPING_VERSION
    assert "unvalidated" in shadow["mapping_version"]


def test_vix_metadata_and_eventual_outcome_can_be_joined_later(user_id):
    """The shadow record must carry both its own join keys (ticker,
    trading_day) AND the real entry_client_order_id once known, so a
    later query can join this record against autonomy/closed_trades.py's
    own trade_id (which IS entry_client_order_id - see that module's
    record_closed_trade docstring)."""
    result = _run_scan(user_id, [_candidate(ticker="AAPL", confidence=80)], _snapshot(vix_level=18.0))
    entry = result["placed"][0]
    shadow = entry["regime_shadow"]
    assert shadow["ticker"] == "AAPL"
    assert shadow["trading_day"] == entry["trading_day"]
    assert shadow["entry_client_order_id"] == entry["entry_client_order_id"] == "fake-cid"


def test_vix_snapshot_timestamps_are_captured_on_the_shadow_record(user_id):
    now = datetime.now(timezone.utc)
    snapshot = _snapshot(vix_level=18.0)
    snapshot["source_time"] = now
    snapshot["fetch_time"] = now
    result = _run_scan(user_id, [_candidate(confidence=80)], snapshot)
    shadow = result["placed"][0]["regime_shadow"]
    assert shadow["vix_source_time"] == now.isoformat()
    assert shadow["vix_fetch_time"] == now.isoformat()
    assert shadow["vix_age_seconds"] == 30.0


def test_vix_is_fetched_once_per_scan_tick_not_once_per_candidate(user_id):
    candidates = [_candidate(ticker="AAPL", confidence=80), _candidate(ticker="MSFT", confidence=75)]
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
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": candidates}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value=_snapshot(vix_level=18.0)) as mock_vix, \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit_and_protect_entry), \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        result = pluto_app._run_autonomous_trade_scan_locked(user_id)

    assert result["placed_count"] == 2
    mock_vix.assert_called_once()


def test_vix_is_fetched_even_when_no_candidates_qualify(user_id):
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
         patch.object(
             pluto_app.webull_api, "get_account_balance",
             return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]},
         ), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": []}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value=_snapshot(vix_level=18.0)) as mock_vix, \
         patch.object(pluto_app, "time"):
        pluto_app._run_autonomous_trade_scan_locked(user_id)

    mock_vix.assert_called_once()
