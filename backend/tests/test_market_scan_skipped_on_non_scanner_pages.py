from __future__ import annotations

from unittest.mock import Mock, patch

import auth
import app as pluto_app

"""Found live in production: /daily-digest and /performance were each
paying an unnecessary ~20s tax on every load from get_market_data() (the
CORE_SCAN_UNIVERSE yfinance fetch, up to its own 20s hard deadline) even
though neither page's template reads scanner_rows. Auditing every other
page route that calls _build_page_context() turned up nine more routes
with the exact same problem - confirmed by grepping each template for
scanner_rows/upcoming_opportunities/mission_queue/options_tickers and
finding zero matches, i.e. none of them render anything derived from the
market scan or the per-ticker opportunities pipeline. Two of the worst
offenders (/account-hub, /trade-journal) are almost certainly the
most-visited pages in the whole app.

These tests prove get_market_data is never called on any of these pages,
the same way the digest/performance regression tests do - not just that
the fix was documented, but that it actually skips the call."""


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be approved."""
    user = auth.register_user(f"scanpage-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _assert_page_never_scans(client, path: str) -> None:
    mock_scan = Mock(return_value=([], [], ""))
    with patch.object(pluto_app, "get_market_data", mock_scan):
        response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    mock_scan.assert_not_called()


def test_admin_page_never_triggers_the_market_scan(user_id):
    admin_id = _registered_user(user_id[:8] + "admin")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        with patch.object(pluto_app, "is_admin", return_value=True):
            _assert_page_never_scans(client, "/admin")


def test_admin_user_activity_page_never_triggers_the_market_scan(user_id):
    admin_id = _registered_user(user_id[:8] + "adminview")
    target_id = _registered_user(user_id[:8] + "target")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        with patch.object(pluto_app, "is_admin", return_value=True):
            _assert_page_never_scans(client, f"/admin/users/{target_id}")


def test_settings_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "settings")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/settings")


def test_account_hub_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "achub")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/account-hub")


def test_notifications_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "notif")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/notifications")


def test_trade_journal_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "journal")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/trade-journal")


def test_neural_engine_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "neural")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/neural-engine")


def test_backtest_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "backtest")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/backtest")


def test_candle_brain_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "candle")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/candle-brain")


def test_pattern_brain_page_never_triggers_the_market_scan(user_id):
    registered_user_id = _registered_user(user_id[:8] + "pattern")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        _assert_page_never_scans(client, "/pattern-brain")
