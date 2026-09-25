"""Sentry is opt-in and must never leak credentials, bodies or identity."""

import observability


def test_init_is_a_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "_initialised", False)
    assert observability.init_sentry("web") is False


def test_cron_checkin_is_a_noop_when_not_initialised(monkeypatch):
    monkeypatch.setattr(observability, "_initialised", False)
    assert observability.cron_checkin("x", "* * * * *", "ok") is None


def test_scrub_event_removes_secrets_bodies_and_identity():
    event = {
        "request": {
            "url": "https://x/api/webhook",
            "data": {"password": "hunter2"},
            "cookies": {"session": "abc"},
            "query_string": "token=abc",
            "headers": {"X-Cron-Secret": "s3", "Authorization": "Bearer z", "Accept": "*/*"},
            "env": {"REMOTE_ADDR": "1.2.3.4"},
        },
        "user": {"id": 7, "username": "captain"},
        "extra": {"webull_app_secret": "shh", "ticker": "TSLA", "nested": {"account_number": "123"}},
    }
    out = observability.scrub_event(event)
    assert "data" not in out["request"] and "cookies" not in out["request"] and "query_string" not in out["request"]
    assert out["request"]["headers"]["X-Cron-Secret"] == "[redacted]"
    assert out["request"]["headers"]["Authorization"] == "[redacted]"
    assert out["request"]["headers"]["Accept"] == "*/*"
    assert out["request"]["env"] == {}
    assert "user" not in out
    assert out["extra"]["webull_app_secret"] == "[redacted]"
    assert out["extra"]["nested"]["account_number"] == "[redacted]"
    assert out["extra"]["ticker"] == "TSLA"
