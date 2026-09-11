from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import auth
import app as pluto_app
from autonomy.closed_trades import record_closed_trade
from autonomy.research_log import record_research_decision
from calibration_store import write_calibration

"""/agent-map - the in-app, live counterpart to docs/AGENT_ARCHITECTURE.md
and the published Agent Map artifact. Added 2026-09-11 directly in
response to being asked to see the map "within the application" so the
memory-layer work (signal_snapshot, real-outcomes calibration,
outcomes_analysis, the validated_brains gate) is continuously checkable
from inside the app, not just documented once. Reporting-only, same
boundary as /performance and /daily-digest - never touches a live
decision."""


def _registered_user(prefix: str) -> str:
    user = auth.register_user(f"{prefix}-{uuid.uuid4().hex[:10]}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_agent_map_page_renders_with_no_data_at_all(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    target_user_id = _registered_user("agentmap-empty")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        response = client.get("/agent-map")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Agent Map" in body
    assert "Idle" in body  # no records -> the status line reads idle, not active
    assert "Nothing evaluated yet" in body  # pulse feed's own empty state
    assert "No calibration data yet" in body
    assert "SHADOW ONLY" in body  # every brain unvalidated by default


def test_agent_map_page_shows_a_recent_record_with_signal_snapshot_as_fed(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    target_user_id = _registered_user("agentmap-fed")
    record_research_decision(target_user_id, {
        "ticker": "NVDA",
        "decision": "placed",
        "skip_category": None,
        "entry_client_order_id": "cid-1",
        "signal_snapshot": {"market_context": {"rsi_14": 55.0}, "strategies_evaluated": []},
    })
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        response = client.get("/agent-map")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "NVDA" in body
    assert "Entry placed" in body
    assert "no signal_snapshot" not in body  # this record IS fed - the caveat must not show
    # A record logged just now falls inside the activity window - the
    # status line reads Active with the real 1/1 fed count, not Idle.
    assert "Active" in body
    assert "1/1 recent signal snapshots populated" in body


def test_agent_map_page_shows_a_record_missing_signal_snapshot_as_missing(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    target_user_id = _registered_user("agentmap-missing")
    record_research_decision(target_user_id, {
        "ticker": "SLOW", "decision": "skipped", "skip_category": "confidence_threshold",
        "reason_skipped": "confidence 20 below 55 threshold",
        "signal_snapshot": None,
    })
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        response = client.get("/agent-map")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "SLOW" in body
    assert "no signal_snapshot" in body
    assert "0/1 recent signal snapshots populated" in body


def test_agent_map_page_shows_which_calibration_source_is_active(user_id):
    write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"Momentum": {"avg_return_percent": 5.0, "trade_count": 20, "trusted": True}},
        "real_strategy_stats": {"Momentum": {"avg_return_percent": -1.0, "trade_count": 16, "trusted": True}},
    })
    target_user_id = _registered_user("agentmap-cal")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        response = client.get("/agent-map")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Momentum" in body
    assert "REAL" in body  # real is trusted, so it's the active source, not BACKTEST


def test_agent_map_page_shows_outcomes_evidence_buckets(user_id):
    write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    target_user_id = _registered_user("agentmap-outcomes")
    record_research_decision(target_user_id, {
        "entry_client_order_id": "cid-2", "ticker": "AAPL",
        "signal_snapshot": {"market_context": {"rsi_14": 25.0}, "strategies_evaluated": []},
    })
    record_closed_trade(target_user_id, "cid-2", {
        "ticker": "AAPL", "strategy": "Momentum", "net_realized_pnl": 10.0,
        "pnl_status": "complete", "entry_client_order_id": "cid-2",
    })
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        response = client.get("/agent-map")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Oversold (&lt;30)" in body or "Oversold (<30)" in body


# --- pure helpers behind the radial diagram's pulse state -------------------


def test_is_recent_true_for_a_timestamp_just_inside_the_window():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert pluto_app._agent_map_is_recent(ts, timedelta(minutes=30)) is True


def test_is_recent_false_for_a_timestamp_outside_the_window():
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert pluto_app._agent_map_is_recent(ts, timedelta(minutes=30)) is False


def test_is_recent_false_for_an_empty_or_malformed_timestamp():
    assert pluto_app._agent_map_is_recent("", timedelta(minutes=30)) is False
    assert pluto_app._agent_map_is_recent("not-a-timestamp", timedelta(minutes=30)) is False


def test_feed_description_uses_reason_skipped_when_present():
    record = {"decision": "skipped", "reason_skipped": "confidence 20 below 55 threshold"}
    assert pluto_app._agent_map_feed_description(record) == "confidence 20 below 55 threshold"


def test_feed_description_for_a_placed_entry_ignores_reason_skipped():
    record = {"decision": "placed", "quantity": 10, "reason_skipped": None}
    assert "Entry placed" in pluto_app._agent_map_feed_description(record)


def test_feed_description_truncates_a_very_long_reason():
    record = {"decision": "skipped", "reason_skipped": "x" * 200}
    result = pluto_app._agent_map_feed_description(record)
    assert len(result) <= 110
    assert result.endswith("...")


def test_feed_description_falls_back_when_nothing_is_recorded():
    assert pluto_app._agent_map_feed_description({"decision": "skipped"}) == "Evaluated, no further detail recorded"


def test_agent_map_page_never_triggers_the_market_scan(user_id):
    target_user_id = _registered_user("agentmap-noscan")
    mock_scan = Mock(return_value=([], [], ""))
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = target_user_id
        with patch.object(pluto_app, "get_market_data", mock_scan):
            response = client.get("/agent-map")
    assert response.status_code == 200
    mock_scan.assert_not_called()
