from __future__ import annotations

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import record_overnight_order

"""Found live 2026-08-31: a real entry (SLB - one of the tickers added in
the same-day scan-universe expansion) tripped the "no forward progress for
30+ minutes" freeze, and there was no admin-visible way to see WHY -
_alert_if_entry_newly_stuck's own alert is deliberately a fixed, no-detail
message (see its docstring: content-hash dedup would spam a new alert every
~10s tick if the error text varied). This endpoint closes that gap without
touching the alert's own one-shot guarantee."""


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-stuck-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-stuck-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _register_target_user(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"target-stuck-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _stuck_entry(entry_client_order_id: str, *, ticker="SLB", stuck_since: str, attempt_count: int, error: str) -> dict:
    entry: dict = {
        "ticker": ticker,
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": "2026-08-31",
        "monitor_first_failure_at": stuck_since,
        "monitor_attempt_count": attempt_count,
        "monitor_last_error": error,
        "monitor_last_attempt_at": stuck_since,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    return entry


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/api/admin/stuck-monitor-entries")
    assert response.status_code == 403


def test_route_returns_a_stuck_entry_with_its_real_error_text(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    target_id = _register_target_user(user_id[:8] + "a")
    stuck_since = (pluto_app._now_utc() - pluto_app.timedelta(seconds=2000)).isoformat()
    entry = _stuck_entry(
        "pt-stuck-a", stuck_since=stuck_since, attempt_count=12,
        error="Webull API error: HTTP 500, Code: INTERNAL_ERROR, Msg: temporarily unavailable",
    )
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/stuck-monitor-entries")

    assert response.status_code == 200
    payload = response.get_json()
    matches = [item for item in payload["data"]["stuck"] if item.get("entry_client_order_id") == "pt-stuck-a"]
    assert len(matches) == 1
    match = matches[0]
    assert match["user_id"] == target_id
    assert match["ticker"] == "SLB"
    assert match["monitor_attempt_count"] == 12
    assert "temporarily unavailable" in match["monitor_last_error"]
    assert match["is_causing_freeze"] is True  # 2000s > MONITOR_STUCK_FREEZE_SECONDS (1800)


def test_route_surfaces_the_lifecycle_error_and_stop_leg_fields():
    # Found live 2026-08-31: a real entry stayed stuck with
    # monitor_last_error == None (a silent internal poll failure inside
    # _confirm_and_finalize_protection, which never raises - see its own
    # docstring), even though the real reason was fully described in the
    # entry's OWN "error" field from its PROTECTION_FAILED transition.
    # These fields close that second, more subtle opacity gap.
    admin_id = _make_admin("lifecycle-error-admin")
    target_id = _register_target_user("lifecycle-error-target")
    entry = _stuck_entry(
        "pt-stuck-lifecycle-error", stuck_since=(pluto_app._now_utc() - pluto_app.timedelta(seconds=2000)).isoformat(),
        attempt_count=185, error=None,
    )
    entry["error"] = "could not confirm protection active within 5 attempts (stop_confirmed=False, target_confirmed=True)"
    entry["stop_client_order_id"] = "stop-cid-real"
    entry["stop_leg_quantity"] = 30.0
    entry["filled_quantity"] = 30.0
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/stuck-monitor-entries")

    payload = response.get_json()
    matches = [item for item in payload["data"]["stuck"] if item.get("entry_client_order_id") == "pt-stuck-lifecycle-error"]
    assert len(matches) == 1
    match = matches[0]
    assert match["monitor_last_error"] is None  # the silent-failure case this test is specifically about
    assert "stop_confirmed=False" in match["lifecycle_error"]
    assert match["stop_client_order_id"] == "stop-cid-real"
    assert match["stop_leg_quantity"] == 30.0
    assert match["filled_quantity"] == 30.0


def test_route_flags_a_not_yet_stuck_entry_as_not_causing_the_freeze(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    target_id = _register_target_user(user_id[:8] + "b")
    stuck_since = (pluto_app._now_utc() - pluto_app.timedelta(seconds=60)).isoformat()
    entry = _stuck_entry("pt-stuck-b", stuck_since=stuck_since, attempt_count=1, error="a transient timeout")
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/stuck-monitor-entries")

    payload = response.get_json()
    matches = [item for item in payload["data"]["stuck"] if item.get("entry_client_order_id") == "pt-stuck-b"]
    assert len(matches) == 1
    assert matches[0]["is_causing_freeze"] is False


def test_route_omits_entries_that_have_never_failed_a_monitor_attempt(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    target_id = _register_target_user(user_id[:8] + "c")
    healthy_entry: dict = {
        "ticker": "AAPL", "limit_price": 100.0, "stop": 95.0, "target": 110.0, "trading_day": "2026-08-31",
    }
    ol.initialize(healthy_entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-stuck-c-healthy")
    record_overnight_order(target_id, healthy_entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/stuck-monitor-entries")

    payload = response.get_json()
    matches = [item for item in payload["data"]["stuck"] if item.get("entry_client_order_id") == "pt-stuck-c-healthy"]
    assert matches == []


def test_route_omits_an_entry_that_has_since_been_closed(user_id):
    # Same bug as _has_stuck_transitional_orders_locally, found the same
    # day: monitor_first_failure_at is never cleared by resolving an entry
    # through a different path (e.g. CLOSED via
    # _resolve_position_absent_reconciliation) - without excluding
    # terminal entries, this endpoint would list a fully-resolved entry as
    # "stuck" forever.
    admin_id = _make_admin(user_id[:8] + "d")
    target_id = _register_target_user(user_id[:8] + "d")
    entry = _stuck_entry(
        "pt-stuck-d-closed", stuck_since=(pluto_app._now_utc() - pluto_app.timedelta(seconds=2000)).isoformat(),
        attempt_count=50, error="some earlier error",
    )
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=30.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="could not confirm protection active")
    ol.transition(entry, ol.CLOSED, closed_trade_id="pt-stuck-d-closed", close_reason="manual_reconciliation_position_absent")
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/stuck-monitor-entries")

    payload = response.get_json()
    matches = [item for item in payload["data"]["stuck"] if item.get("entry_client_order_id") == "pt-stuck-d-closed"]
    assert matches == []
