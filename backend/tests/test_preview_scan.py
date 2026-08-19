from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import auth
import app as pluto_app
from autonomy.overnight_orders import list_overnight_orders
from autonomy.research_log import list_research_decisions

"""Built for Stage 3: the user asked why they had to hand-pick a ticker
instead of the agent doing its own research - the honest answer is the
agent already CAN discover its own candidates (proven in
test_full_autonomous_trade_lifecycle.py), so this adds a genuine preview
mode: dry_run=True runs the exact same discovery/threshold/sizing logic
against real market data and real account balance, but is structurally
incapable of touching the broker or persisting anything. These tests exist
to prove that guarantee, not just describe it."""

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"

RECONCILIATION_FUNCTION_NAMES = [
    "_reconcile_exit_orders", "_refresh_stop_confidence", "_discover_orphaned_broker_entries",
    "_reconcile_unknown_submissions", "_recover_incomplete_manual_resolutions", "_monitor_transitional_orders",
]


def _candidate(ticker="NVDA", confidence=82):
    return {
        "ticker": ticker,
        "recommendation": "CALL",
        "confidence": confidence,
        "ideal_entry": 100.0,
        "stop": 50.0,
        "target": 110.0,
        "strategy": "Trend Continuation",
        "trade_quality": "high",
    }


def _patch_common_scan_environment(stack: ExitStack, opportunities) -> None:
    stack.enter_context(patch.object(pluto_app, "get_webull_credentials", return_value=CREDS))
    stack.enter_context(patch.object(pluto_app, "is_webull_configured", return_value=True))
    stack.enter_context(patch.object(pluto_app, "get_anthropic_api_key", return_value=""))
    stack.enter_context(patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]))
    stack.enter_context(patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]))
    stack.enter_context(patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}))
    stack.enter_context(patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"))
    stack.enter_context(patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]))
    stack.enter_context(patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]))
    stack.enter_context(patch.object(pluto_app.webull_api, "get_order_history", return_value=[]))
    stack.enter_context(patch.object(pluto_app.webull_api, "get_account_balance", return_value={
        "total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
        "account_currency_assets": [{"buying_power": "1000000"}],
    }))
    stack.enter_context(patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": opportunities}))
    stack.enter_context(patch.object(pluto_app, "get_vix_snapshot", return_value={
        "vix_level": None, "source_time": None, "fetch_time": None,
        "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
    }))
    stack.enter_context(patch.object(pluto_app, "get_settings", return_value={"ai_confidence_threshold": 55}))
    stack.enter_context(patch.object(pluto_app, "time"))


def _run_preview(user_id, opportunities):
    with ExitStack() as stack:
        _patch_common_scan_environment(stack, opportunities)
        mocks = {
            "place_stock_order": stack.enter_context(patch.object(pluto_app.webull_api, "place_stock_order")),
            "place_stop_loss_order": stack.enter_context(patch.object(pluto_app.webull_api, "place_stop_loss_order")),
            "place_take_profit_order": stack.enter_context(patch.object(pluto_app.webull_api, "place_take_profit_order")),
            "get_order_detail": stack.enter_context(patch.object(pluto_app.webull_api, "get_order_detail")),
            "cancel_order": stack.enter_context(patch.object(pluto_app.webull_api, "cancel_order")),
            "record_overnight_order": stack.enter_context(patch.object(pluto_app, "record_overnight_order")),
            "record_research_decision": stack.enter_context(patch.object(pluto_app, "record_research_decision")),
        }
        reconciliation_calls = {
            name: stack.enter_context(patch.object(pluto_app, name)) for name in RECONCILIATION_FUNCTION_NAMES
        }
        result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=True)
    return result, mocks, reconciliation_calls


def test_dry_run_finds_the_agents_own_candidate_without_touching_the_broker(user_id):
    result, mocks, _ = _run_preview(user_id, [_candidate()])

    assert result["placed_count"] == 1, f"expected the agent's own candidate in the preview, got: {result}"
    preview_entry = result["placed"][0]
    assert preview_entry["ticker"] == "NVDA"
    assert preview_entry["confidence"] == 82
    assert preview_entry["quantity"] > 0
    assert preview_entry["status"] == "preview"
    assert preview_entry["stop_price"] == 50.0
    assert preview_entry["target_price"] == 110.0

    # The structural guarantee - not one broker-mutating call fired.
    mocks["place_stock_order"].assert_not_called()
    mocks["place_stop_loss_order"].assert_not_called()
    mocks["place_take_profit_order"].assert_not_called()
    mocks["cancel_order"].assert_not_called()


def test_dry_run_persists_nothing(user_id):
    result, mocks, _ = _run_preview(user_id, [_candidate()])
    assert result["placed_count"] == 1
    mocks["record_overnight_order"].assert_not_called()
    mocks["record_research_decision"].assert_not_called()
    assert list_overnight_orders(user_id) == []
    assert list_research_decisions(user_id) == []


def test_dry_run_skips_all_existing_position_reconciliation(user_id):
    """A preview must have zero side effects on anything already resting at
    the broker, not just on new entries - see the dry_run docstring."""
    _, _, reconciliation_calls = _run_preview(user_id, [_candidate()])
    for mock in reconciliation_calls.values():
        mock.assert_not_called()


def test_dry_run_skipped_candidate_is_also_never_persisted(user_id):
    """A candidate below the confidence floor must show up as skipped in the
    preview, exactly like a real run - just without ever writing it anywhere."""
    low_confidence = _candidate(confidence=10)
    result, mocks, _ = _run_preview(user_id, [low_confidence])
    assert result["placed_count"] == 0
    assert result["skipped_count"] == 1
    mocks["record_overnight_order"].assert_not_called()
    assert list_overnight_orders(user_id) == []


def test_dry_run_never_calls_submit_and_protect_entry(user_id):
    """Belt-and-suspenders on the single most important guarantee: the real
    fill/protection function itself must never even be invoked under
    dry_run, independent of what its own internals would have done."""
    with ExitStack() as stack:
        _patch_common_scan_environment(stack, [_candidate()])
        mock_submit = stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry"))
        pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=True)
    mock_submit.assert_not_called()


def test_a_real_run_after_a_preview_is_completely_unaffected(user_id):
    """The preview must leave zero residue - local_reservations, entry
    dicts, etc. are all local to that one dry_run call and must not leak
    into a subsequent REAL scan for the same user."""
    _run_preview(user_id, [_candidate()])
    assert list_overnight_orders(user_id) == []

    with ExitStack() as stack:
        _patch_common_scan_environment(stack, [_candidate()])
        mock_submit = stack.enter_context(patch.object(pluto_app, "_submit_and_protect_entry"))
        mock_submit.side_effect = lambda **kwargs: kwargs["entry"]
        real_result = pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=False)

    assert real_result["placed_count"] == 1
    mock_submit.assert_called_once()


def test_account_hub_page_renders_the_preview_scan_panel(user_id):
    """Catches a Jinja syntax error in the new panel that a pure API-level
    test would never see."""
    user = auth.register_user(f"previewscan-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        body = client.get("/account-hub").data.decode("utf-8")
    assert 'id="previewScanButton"' in body
    assert 'id="previewScanResult"' in body
