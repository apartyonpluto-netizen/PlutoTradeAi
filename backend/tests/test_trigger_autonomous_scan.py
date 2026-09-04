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


def _set_urls(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.setenv("FAST_MONITOR_TRIGGER_URL", "https://example.com/api/autonomy/fast-monitor-trigger")
    monkeypatch.setenv("CRON_SECRET", "my-secret")


def test_main_returns_zero_and_posts_to_both_endpoints_on_success(monkeypatch):
    # FAST_MONITOR_TRIGGER_URL added 2026-09-04 (render.yaml) - main() now
    # posts to BOTH endpoints every run, regardless of the other's outcome,
    # so this asserts on call_args_list (both calls), not call_args (only
    # the last one) - a single-call assertion would silently only check
    # whichever endpoint happens to be posted to second.
    _set_urls(monkeypatch)

    with patch.object(trigger.requests, "post", return_value=_mock_response(200, "ok")) as mock_post:
        exit_code = trigger.main()

    assert exit_code == 0
    assert mock_post.call_count == 2
    urls_called = {call.args[0] for call in mock_post.call_args_list}
    assert urls_called == {
        "https://example.com/api/autonomy/cron-trigger",
        "https://example.com/api/autonomy/fast-monitor-trigger",
    }
    for call in mock_post.call_args_list:
        assert call.kwargs["headers"] == {"X-Cron-Secret": "my-secret"}
        assert call.kwargs["timeout"] == trigger.REQUEST_TIMEOUT_SECONDS


def test_main_returns_nonzero_if_either_endpoint_fails(monkeypatch):
    # Both endpoints are still attempted (one failing must not hide whether
    # the other is healthy - see main()'s own comment), but ANY failure
    # makes the overall exit code nonzero.
    _set_urls(monkeypatch)

    def _side_effect(url, **kwargs):
        return _mock_response(200, "ok") if "cron-trigger" in url else _mock_response(401, "unauthorized")

    with patch.object(trigger.requests, "post", side_effect=_side_effect) as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    assert mock_post.call_count == 2


def test_main_returns_nonzero_on_a_request_exception(monkeypatch):
    _set_urls(monkeypatch)

    with patch.object(trigger.requests, "post", side_effect=requests.exceptions.ConnectionError("refused")):
        exit_code = trigger.main()

    assert exit_code == 1


def test_main_refuses_to_run_without_cron_trigger_url(monkeypatch):
    monkeypatch.delenv("CRON_TRIGGER_URL", raising=False)
    monkeypatch.setenv("FAST_MONITOR_TRIGGER_URL", "https://example.com/api/autonomy/fast-monitor-trigger")
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post") as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    mock_post.assert_not_called()


def test_main_refuses_to_run_without_fast_monitor_url(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.delenv("FAST_MONITOR_TRIGGER_URL", raising=False)
    monkeypatch.setenv("CRON_SECRET", "my-secret")

    with patch.object(trigger.requests, "post") as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    mock_post.assert_not_called()


def test_main_refuses_to_run_without_secret(monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_URL", "https://example.com/api/autonomy/cron-trigger")
    monkeypatch.setenv("FAST_MONITOR_TRIGGER_URL", "https://example.com/api/autonomy/fast-monitor-trigger")
    monkeypatch.delenv("CRON_SECRET", raising=False)

    with patch.object(trigger.requests, "post") as mock_post:
        exit_code = trigger.main()

    assert exit_code == 1
    mock_post.assert_not_called()
