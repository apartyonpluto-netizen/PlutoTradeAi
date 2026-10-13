from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import webhook_throttle


def test_token_limit_returns_a_wait(monkeypatch):
    monkeypatch.setattr(webhook_throttle, "TOKEN_LIMIT", 2)
    assert webhook_throttle.retry_after("token-a", "203.0.113.5") is None
    assert webhook_throttle.retry_after("token-a", "203.0.113.6") is None
    wait = webhook_throttle.retry_after("token-a", "203.0.113.7")
    assert isinstance(wait, int) and wait >= 1


def test_a_different_token_is_not_blocked_by_the_first(monkeypatch):
    monkeypatch.setattr(webhook_throttle, "TOKEN_LIMIT", 1)
    assert webhook_throttle.retry_after("one", "203.0.113.8") is None
    assert webhook_throttle.retry_after("two", "203.0.113.9") is None


def test_webhook_route_returns_429_and_does_not_store(monkeypatch):
    monkeypatch.setattr(webhook_throttle, "TOKEN_LIMIT", 1)
    with pluto_app.app.test_client() as client, \
         patch.object(pluto_app, "find_user_by_webhook_token", return_value={"id": "user-1"}), \
         patch.object(pluto_app, "verify_tradingview_token", return_value=True), \
         patch.object(pluto_app, "save_alert", return_value={"id": "a"}) as mock_save, \
         patch.object(pluto_app, "record_tradingview_signal", return_value={"ok": True}):
        first = client.post("/api/tradingview/webhook?token=abc", json={"ticker": "AAPL"})
        second = client.post("/api/tradingview/webhook?token=abc", json={"ticker": "MSFT"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert mock_save.call_count == 1
