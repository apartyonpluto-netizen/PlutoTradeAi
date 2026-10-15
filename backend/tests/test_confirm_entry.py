from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import confirm_entry


def _body(**overrides):
    payload = {
        "act": "enter",
        "uid": "user-1",
        "sid": "sugg-1",
        "px": 100.0,
        "sym": "AAPL",
        "qty": 1,
        "lim": 100.0,
        "stop": 95.0,
        "tgt": 110.0,
        "dir": "long",
        "day": "2026-10-15",
        "acct": "acct-1",
        "strat": "Momentum",
        "conf": 80,
    }
    payload.update(overrides)
    return payload


def test_expired_token_is_refused_and_cannot_be_replayed():
    issued = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    token = confirm_entry.issue_token(_body(), now=issued)
    later = issued + timedelta(seconds=confirm_entry.ttl_seconds() + 5)
    with pytest.raises(confirm_entry.TokenError) as first:
        confirm_entry.redeem(token, now=later, current_price=100.0)
    assert first.value.code == "expired"
    with pytest.raises(confirm_entry.TokenError) as second:
        confirm_entry.redeem(token, now=later, current_price=100.0)
    assert second.value.code == "replay"


def test_valid_token_works_once():
    now = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    token = confirm_entry.issue_token(_body(), now=now)
    body = confirm_entry.redeem(token, now=now + timedelta(seconds=30), current_price=100.1)
    assert body["act"] == "enter"
    assert body["sym"] == "AAPL"
    with pytest.raises(confirm_entry.TokenError) as replay:
        confirm_entry.redeem(token, now=now + timedelta(seconds=40), current_price=100.1)
    assert replay.value.code == "replay"


def test_price_move_consumes_the_token():
    now = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    token = confirm_entry.issue_token(_body(), now=now)
    with pytest.raises(confirm_entry.TokenError) as moved:
        confirm_entry.redeem(token, now=now, current_price=110.0)
    assert moved.value.code == "price_moved"
    with pytest.raises(confirm_entry.TokenError) as replay:
        confirm_entry.redeem(token, now=now, current_price=100.0)
    assert replay.value.code == "replay"


def test_quiet_hours_and_daily_cap(monkeypatch):
    moment = datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc)
    assert confirm_entry.in_quiet_hours(moment, window="20:00-08:00", tz_name="America/New_York") is True
    assert confirm_entry.in_quiet_hours(moment, window="09:00-10:00", tz_name="America/New_York") is False
    monkeypatch.setenv("PLUTO_SETUP_ALERTS_PER_DAY", "1")
    monkeypatch.setenv("PLUTO_ALERT_QUIET_HOURS", "")
    first = confirm_entry.delivery("user-cap", "2026-10-15", moment)
    second = confirm_entry.delivery("user-cap", "2026-10-15", moment)
    assert first["send"] is True
    assert second["skip_category"] == "alert_cap"
