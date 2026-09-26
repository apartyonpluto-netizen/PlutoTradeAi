"""Readiness report: pure logic plus the admin endpoint. Never arms live, never trades."""

from unittest.mock import patch

import pytest

import app as pluto_app
import readiness

HEALTHY = {"healthy": True, "reason": ""}
LIVE_ENV = {
    "PLUTO_WEBULL_TRADING_ENVIRONMENT": "live",
    "PLUTO_LIVE_TRADING_CONFIRMATION": "I_UNDERSTAND_THIS_PLACES_REAL_MONEY_ORDERS",
}
SECRETS = ("FLASK_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY", "CRON_SECRET", "MONITOR_WORKER_SECRET")


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUTO_DATA_DIR", str(tmp_path))
    for name in (*LIVE_ENV, "PLUTO_LIVE_MAX_ORDER_USD", "PLUTO_LIVE_MAX_DAILY_USD", "LIVE_TRADING_ENABLED", *SECRETS):
        monkeypatch.delenv(name, raising=False)


def _by_id(report):
    return {c["id"]: c for c in report["checks"]}


def _all_ok(monkeypatch):
    for name in SECRETS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("PLUTO_LIVE_MAX_ORDER_USD", "100")
    monkeypatch.setenv("PLUTO_LIVE_MAX_DAILY_USD", "300")
    return dict(
        fast_monitor=HEALTHY, full_scan=HEALTHY, continuous_monitor=HEALTHY,
        data_probe=lambda: 512.3, emergency_stop_enabled=False, webull_connected=True, sentry_active=True,
    )


def test_all_green_is_live_ready_but_not_armed(monkeypatch):
    report = readiness.build_report(**_all_ok(monkeypatch))
    assert report["live_armed"] is False
    assert report["sandbox_ready"] is True
    assert report["live_ready"] is True
    assert report["fail_count"] == 0
    assert "never arms live" in report["note"]


def test_rejected_alpaca_keys_fail_the_data_check(monkeypatch):
    kwargs = _all_ok(monkeypatch)

    def bad():
        raise RuntimeError("Alpaca rejected the API keys (HTTP 401).")

    kwargs["data_probe"] = bad
    report = readiness.build_report(**kwargs)
    check = _by_id(report)["market_data"]
    assert check["status"] == "fail" and "401" in check["detail"]
    assert report["sandbox_ready"] is False and report["live_ready"] is False


def test_unhealthy_heartbeat_blocks_live(monkeypatch):
    kwargs = _all_ok(monkeypatch)
    kwargs["full_scan"] = {"healthy": False, "reason": "the full scan has never run"}
    report = readiness.build_report(**kwargs)
    assert _by_id(report)["full_scan"]["status"] == "fail"
    assert "full_scan" in report["live_blockers"]


def test_armed_without_caps_is_a_failure(monkeypatch):
    kwargs = _all_ok(monkeypatch)
    monkeypatch.delenv("PLUTO_LIVE_MAX_ORDER_USD")
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    report = readiness.build_report(**kwargs)
    assert report["live_armed"] is True
    assert _by_id(report)["live_caps"]["status"] == "fail"
    assert _by_id(report)["mode"]["status"] == "warn"


def test_missing_secrets_are_named(monkeypatch):
    report = readiness.build_report()
    detail = _by_id(report)["secrets"]["detail"]
    assert "CRON_SECRET" in detail and "MONITOR_WORKER_SECRET" in detail


def test_emergency_stop_on_is_flagged(monkeypatch):
    kwargs = _all_ok(monkeypatch)
    kwargs["emergency_stop_enabled"] = True
    report = readiness.build_report(**kwargs)
    assert _by_id(report)["emergency_stop"]["status"] == "warn"
    assert report["live_ready"] is False


def test_legacy_flag_warns_but_does_not_arm(monkeypatch):
    kwargs = _all_ok(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    report = readiness.build_report(**kwargs)
    assert _by_id(report)["legacy_flag"]["status"] == "warn"
    assert report["live_armed"] is False


def test_skipped_checks_do_not_count_as_ready(monkeypatch):
    for name in SECRETS:
        monkeypatch.setenv(name, "x")
    report = readiness.build_report()  # nothing supplied
    assert report["live_ready"] is False
    assert "fast_monitor" in report["live_blockers"]


def _admin_id():
    import auth

    users = auth.list_all_users()
    admin = next((u for u in users if u.get("role") == "admin"), None)
    if admin:
        return admin["id"]
    user = auth.register_user("readiness-admin", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def test_endpoint_requires_admin(user_id):
    import auth

    _admin_id()
    plain = auth.register_user("readiness-plain", "TestPassword123!")
    auth.approve_user(plain["id"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain["id"]
        assert client.get("/api/admin/readiness").status_code == 403


def test_endpoint_returns_report_without_touching_network(user_id):
    admin_id = _admin_id()
    with patch.object(pluto_app.alpaca_data, "probe_latest_trade", return_value=500.0), \
         pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/readiness")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["live_armed"] is False
    assert {c["id"] for c in data["checks"]} >= {"mode", "live_caps", "market_data", "fast_monitor", "emergency_stop"}
