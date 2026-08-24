from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.closed_trades import record_closed_trade
from autonomy.daily_digest import build_daily_digest
from autonomy.overnight_orders import record_overnight_order
from autonomy.scan_run_log import record_scan_run

"""The legitimate version of the "Chief of Staff" idea from the X post that
prompted this: a read-only triage summary over data this app already
records, not a new agent and not a new decision path. These tests prove the
digest surfaces the things that actually need a human (protection gaps,
ambiguous exits, failed scans, unhealthy monitors) and stays quiet
otherwise, and that its time window and open-position filtering are
correct."""

NOW = datetime(2026, 8, 22, 18, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_empty_account_has_a_quiet_all_clear_headline(user_id):
    digest = build_daily_digest(user_id, now=NOW)
    assert digest["headline"] == "Nothing needs your attention right now."
    assert digest["attention_items"] == []
    assert digest["open_positions"] == []
    assert digest["closed_trades"]["count"] == 0
    assert digest["closed_trades"]["total_pnl"] is None


def test_protection_gap_becomes_the_headline(user_id):
    record_overnight_order(user_id, {
        "ticker": "NVDA",
        "lifecycle_state": ol.PROTECTION_PENDING,
        "stop_protection_gap": True,
    })
    digest = build_daily_digest(user_id, now=NOW)
    assert "NVDA" in digest["headline"]
    assert "protection gap" in digest["headline"]
    assert digest["attention_items"][0]["severity"] == "critical"


def test_ambiguous_exit_becomes_the_headline(user_id):
    record_overnight_order(user_id, {
        "ticker": "TSLA",
        "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE,
        "ambiguous_exit_unresolved": True,
    })
    digest = build_daily_digest(user_id, now=NOW)
    assert "TSLA" in digest["headline"]
    assert "ambiguous exit" in digest["headline"]


def test_failed_scan_runs_in_window_are_surfaced_with_the_real_error(user_id):
    record_scan_run(user_id, {
        "actual_start_time": _iso(NOW - timedelta(hours=2)),
        "status": "failed",
        "error": "Connect Webull in Account Hub before running the trade scan.",
        "trigger_source": "cron-trigger",
    })
    digest = build_daily_digest(user_id, now=NOW)
    assert digest["scan_activity"]["failed_scans"] == 1
    assert "Connect Webull" in digest["headline"]


def test_scan_runs_outside_the_window_are_excluded(user_id):
    record_scan_run(user_id, {
        "actual_start_time": _iso(NOW - timedelta(hours=48)),
        "status": "failed",
        "error": "stale failure from two days ago",
        "trigger_source": "cron-trigger",
    })
    digest = build_daily_digest(user_id, now=NOW, window_hours=24)
    assert digest["scan_activity"]["failed_scans"] == 0
    assert digest["scan_activity"]["total_scans"] == 0
    assert digest["headline"] == "Nothing needs your attention right now."


def test_unhealthy_monitor_is_surfaced(user_id):
    digest = build_daily_digest(
        user_id,
        now=NOW,
        monitor_heartbeat={"fast_monitor_healthy": False, "full_scan_healthy": True, "continuous_monitor_healthy": True},
    )
    assert "fast monitor" in digest["headline"]


def test_open_positions_excludes_test_orders_and_terminal_states(user_id):
    record_overnight_order(user_id, {"ticker": "AAPL", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE})
    record_overnight_order(user_id, {"ticker": "MSFT", "lifecycle_state": ol.CLOSED})
    record_overnight_order(user_id, {"ticker": "SPY", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE, "source": "stage3_test_order"})
    record_overnight_order(user_id, {"ticker": "QQQ", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE, "source": "manual_test_order"})

    digest = build_daily_digest(user_id, now=NOW)
    tickers = {p["ticker"] for p in digest["open_positions"]}
    assert tickers == {"AAPL"}


def test_closed_trades_summary_computes_wins_losses_and_total_pnl(user_id):
    record_closed_trade(user_id, "trade-1", {
        "ticker": "NVDA", "exit_type": "target", "net_realized_pnl": 150.0,
        "exit_timestamp": _iso(NOW - timedelta(hours=1)),
    })
    record_closed_trade(user_id, "trade-2", {
        "ticker": "AMD", "exit_type": "stop", "net_realized_pnl": -40.0,
        "exit_timestamp": _iso(NOW - timedelta(hours=3)),
    })
    digest = build_daily_digest(user_id, now=NOW)
    assert digest["closed_trades"]["count"] == 2
    assert digest["closed_trades"]["wins"] == 1
    assert digest["closed_trades"]["losses"] == 1
    assert digest["closed_trades"]["total_pnl"] == 110.0


def test_closed_trades_outside_window_are_excluded(user_id):
    record_closed_trade(user_id, "trade-old", {
        "ticker": "NVDA", "exit_type": "target", "net_realized_pnl": 500.0,
        "exit_timestamp": _iso(NOW - timedelta(hours=30)),
    })
    digest = build_daily_digest(user_id, now=NOW, window_hours=24)
    assert digest["closed_trades"]["count"] == 0
    assert digest["closed_trades"]["total_pnl"] is None


def test_scan_activity_sums_across_multiple_runs_in_window(user_id):
    record_scan_run(user_id, {
        "actual_start_time": _iso(NOW - timedelta(hours=1)),
        "status": "processed",
        "candidates_found": 3, "candidates_qualifying": 1,
        "orders_outcomes": {"placed": 1, "failed": 0, "unknown_submission_state": 0},
    })
    record_scan_run(user_id, {
        "actual_start_time": _iso(NOW - timedelta(hours=5)),
        "status": "processed",
        "candidates_found": 2, "candidates_qualifying": 0,
        "orders_outcomes": {"placed": 0, "failed": 0, "unknown_submission_state": 0},
    })
    digest = build_daily_digest(user_id, now=NOW)
    assert digest["scan_activity"]["total_scans"] == 2
    assert digest["scan_activity"]["candidates_found"] == 5
    assert digest["scan_activity"]["candidates_qualifying"] == 1
    assert digest["scan_activity"]["orders_placed"] == 1


# --- page rendering --------------------------------------------------------------------


def _registered_user(username_suffix: str) -> str:
    user = auth.register_user(f"dailydigest-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_daily_digest_page_renders(user_id):
    """Catches a Jinja syntax error a pure unit test would never see."""
    registered_user_id = _registered_user(user_id[:8])
    record_overnight_order(registered_user_id, {"ticker": "NVDA", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE})
    record_closed_trade(registered_user_id, "trade-1", {
        "ticker": "AMD", "exit_type": "stop", "net_realized_pnl": -25.0,
        "exit_timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/daily-digest")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Daily Digest" in body
    assert "NVDA" in body
    assert "AMD" in body


def test_daily_digest_page_never_triggers_the_market_scan(user_id):
    """Found live in production: this page took ~21s to load because
    get_market_data() (the CORE_SCAN_UNIVERSE yfinance fetch, up to its
    own 20s hard deadline) ran unconditionally regardless of
    include_opportunities, for scanner_rows this page never reads -
    exactly the kind of cost that has caused real 502s under load
    elsewhere in this app. Proves the fix actually skips the call."""
    registered_user_id = _registered_user(user_id[:8] + "scan")
    mock_scan = Mock(return_value=([], [], ""))
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        with patch.object(pluto_app, "get_market_data", mock_scan):
            response = client.get("/daily-digest")
    assert response.status_code == 200
    mock_scan.assert_not_called()


def test_daily_digest_link_appears_in_nav(user_id):
    registered_user_id = _registered_user(user_id[:8] + "nav")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        body = client.get("/account-hub").data.decode("utf-8")
    assert 'href="/daily-digest"' in body
