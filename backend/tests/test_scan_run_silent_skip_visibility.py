from __future__ import annotations

from unittest.mock import patch

import auth
import app as pluto_app
from autonomy.autonomous_controller import update_risk_settings

"""Found while investigating why the real production account's Scan Run
History showed runs like "2 qualifying, 0 placed, 0 failed, 0 ambiguous"
with zero explanation anywhere of what happened to those 2 candidates. Root
cause: _summarize_scan_result_for_run_log's reason text only ever counted
candidates that reached _submit_and_protect_entry (placed/failed/
unknown_submission_state) - a candidate sized down to 0 shares, vetoed by
the optional LLM review, or crowded out by max_positions/no open slots is
recorded in the real `skipped` list with a real reason_skipped, but that
list was never consulted when building the persisted, human-facing summary.
was_qualifying=True now marks exactly these three skip sites (not a
candidate that simply never qualified in the first place - reporting every
one of those individually would bury the real signal in noise), and
_summarize_scan_result_for_run_log surfaces them."""


def _registered_user(username_suffix: str) -> str:
    user = auth.register_user(f"silentskip-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


# --- _summarize_scan_result_for_run_log in isolation ---------------------------------


def test_sizing_rejected_candidate_is_surfaced_in_the_reason_text():
    scan_result = {
        "candidates_found": 2,
        "candidates_qualifying": 2,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {
                "ticker": "NVDA",
                "reason_skipped": "risk budget too small for this stop distance",
                "was_qualifying": True,
            }
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "not submitted - NVDA (risk budget too small for this stop distance)" in summary["reason"]


def test_llm_vetoed_candidate_is_surfaced_in_the_reason_text():
    scan_result = {
        "candidates_found": 1,
        "candidates_qualifying": 1,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {
                "ticker": "TSLA",
                "status": "skipped",
                "reason_skipped": "LLM reasoning vetoed: thesis doesn't hold up",
                "was_qualifying": True,
            }
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "TSLA (LLM reasoning vetoed: thesis doesn't hold up)" in summary["reason"]


def test_candidate_crowded_out_by_max_positions_is_surfaced():
    scan_result = {
        "candidates_found": 3,
        "candidates_qualifying": 1,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {
                "ticker": "AAPL",
                "reason_skipped": "max_positions limit reached (3/3 open)",
                "was_qualifying": True,
            }
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "AAPL (max_positions limit reached (3/3 open))" in summary["reason"]


def test_a_candidate_that_never_qualified_at_all_is_not_surfaced():
    """Only candidates that PASSED confidence and were still dropped are
    worth calling out individually - otherwise every routine scan (most
    tickers never qualify) would bury the real signal in noise."""
    scan_result = {
        "candidates_found": 10,
        "candidates_qualifying": 0,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {"ticker": "SPY", "reason_skipped": "confidence 40 below 65 threshold"},
            {"ticker": "QQQ", "reason_skipped": "recommendation is PUT, only CALL/bullish setups auto-order tonight"},
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "not submitted" not in summary["reason"]


def test_entries_blocked_globally_is_not_duplicated_per_candidate():
    """When entries_allowed is False, new_entries_blocked_reason already
    covers it once, globally - a was_qualifying candidate whose own
    reason_skipped is just that same blocked reason must not be repeated a
    second time per-ticker."""
    scan_result = {
        "candidates_found": 1,
        "candidates_qualifying": 1,
        "entries_allowed": False,
        "new_entries_blocked_reason": "outside CORE trading hours",
        "placed": [],
        "skipped": [
            {"ticker": "MSFT", "reason_skipped": "outside CORE trading hours"},
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert summary["reason"].count("outside CORE trading hours") == 1
    assert "not submitted" not in summary["reason"]


def test_multiple_silently_skipped_candidates_are_all_listed():
    scan_result = {
        "candidates_found": 2,
        "candidates_qualifying": 2,
        "entries_allowed": True,
        "new_entries_blocked_reason": "",
        "placed": [],
        "skipped": [
            {"ticker": "NVDA", "reason_skipped": "sized to 0 shares", "was_qualifying": True},
            {"ticker": "AMD", "reason_skipped": "LLM reasoning vetoed: weak setup", "was_qualifying": True},
        ],
    }
    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "NVDA (sized to 0 shares)" in summary["reason"]
    assert "AMD (LLM reasoning vetoed: weak setup)" in summary["reason"]


# --- end to end, through the real scan -------------------------------------------------


def test_real_scan_surfaces_a_sizing_rejected_qualifying_candidate(user_id):
    """Drives the REAL _run_autonomous_trade_scan_locked with
    risk_percent_of_balance forced to 0 (risk disabled -> _compute_position_quantity
    fails closed to quantity 0, per its own docstring) - proving the tag
    actually gets set by production code, not just asserted in isolation."""
    registered_user_id = _registered_user(user_id[:8])
    update_risk_settings(registered_user_id, risk_percent_of_balance=0)
    candidate = {
        "ticker": "NVDA",
        "recommendation": "CALL",
        "confidence": 82,
        "ideal_entry": 100.0,
        "stop": 50.0,
        "target": 110.0,
        "strategy": "Trend Continuation",
        "trade_quality": "high",
    }

    with patch.object(pluto_app, "get_webull_credentials", return_value={"app_key": "key", "app_secret": "secret"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-1"}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": "acct-1"}), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={
             "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
             "account_currency_assets": [{"buying_power": "1000000"}],
         }), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": [candidate]}), \
         patch.object(pluto_app, "get_vix_snapshot", return_value={
             "vix_level": None, "source_time": None, "fetch_time": None,
             "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
         }), \
         patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}), \
         patch.object(pluto_app.webull_api, "place_stock_order") as mock_place, \
         patch.object(pluto_app, "time"):
        scan_result = pluto_app._run_autonomous_trade_scan_locked(registered_user_id)

    mock_place.assert_not_called()
    assert scan_result["placed_count"] == 0
    matches = [e for e in scan_result["skipped"] if e.get("ticker") == "NVDA"]
    assert len(matches) == 1
    assert matches[0]["was_qualifying"] is True

    summary = pluto_app._summarize_scan_result_for_run_log(scan_result)
    assert "NVDA" in summary["reason"]
    assert "not submitted" in summary["reason"]
