from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

import trigger_autonomous_scan as trigger

"""Found live 2026-08-28: GitHub Actions' scheduled-workflow mechanism
proved unreliable for the every-5-minutes cadence this app's autonomous
scan needs - real run history showed runs landing hours apart instead of
every 5 minutes, then stopping entirely for 22+ hours (not a billing/quota
issue - GitHub's own Actions minutes usage showed 0/2000 consumed). This
script replaces it as the PRIMARY trigger, run by a Render Cron Job
(Render's own first-party scheduled-invocation mechanism, on the same
infrastructure as the web service itself) instead of a cross-platform
best-effort GitHub schedule. Unlike continuous_monitor_worker.py this is
a single request-then-exit script, not a loop - Render's Cron Job
scheduler itself handles "every N minutes"."""


def _mock_response(status_code: int, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


def test_main_returns_zero_and_sends_the_secret_header_on_success(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post", return_value=_mock_response(200, "ok")) as mock_post:
        exit_code = trigger.main()

    assert exit_code == 0
    call = mock_post.call_args
    assert call.args[0] == "https://example.com/api/autonomy/cron-trigger"
    assert call.kwargs["headers"] == {"X-Cron-Secret": "my-secret"}
    assert call.kwargs["timeout"] == trigger.REQUEST_TIMEOUT_SECONDS


def test_main_returns_nonzero_on_a_non_200_response(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post", return_value=_mock_response(401, "unauthorized")):
        exit_code = trigger.main()

    assert exit_code == 1


def test_main_returns_nonzero_on_a_request_exception(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post", side_effect=requests.exceptions.ConnectionError("refused")):
        exit_code = trigger.main()

    assert exit_code == 1


def test_main_refuses_to_run_without_url(monkeypatch):
    monkeypatch.delenv("CRON_TRIGGER_URL", raising=False)
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post") as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    mock_post.assert_not_called()


def test_main_refuses_to_run_without_secret(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.delenv("CRON_SECRET", raising=False)

    with patch.object(trigger.requests, "post") as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    mock_post.assert_not_called()
