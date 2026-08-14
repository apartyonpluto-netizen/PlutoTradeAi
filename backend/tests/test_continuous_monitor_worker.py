from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import continuous_monitor_worker as worker


# --- backoff / jitter math -------------------------------------------------------


def test_compute_backoff_seconds_zero_on_success():
    assert worker.compute_backoff_seconds(0, base_interval=10, max_backoff_seconds=120) == 0.0


def test_compute_backoff_seconds_doubles_and_caps():
    assert worker.compute_backoff_seconds(1, base_interval=10, max_backoff_seconds=120) == 10.0
    assert worker.compute_backoff_seconds(2, base_interval=10, max_backoff_seconds=120) == 20.0
    assert worker.compute_backoff_seconds(3, base_interval=10, max_backoff_seconds=120) == 40.0
    assert worker.compute_backoff_seconds(10, base_interval=10, max_backoff_seconds=120) == 120.0  # capped


def test_compute_jittered_sleep_stays_within_bounds_and_never_negative():
    for _ in range(200):
        value = worker.compute_jittered_sleep(10.0, jitter_fraction=0.15)
        assert 8.5 <= value <= 11.5
        assert value >= 0.0


def test_compute_jittered_sleep_zero_fraction_is_exact():
    assert worker.compute_jittered_sleep(10.0, jitter_fraction=0.0) == 10.0


# --- perform_tick classification --------------------------------------------------


def _mock_session(status_code: int | None = 200, raise_exc: Exception | None = None):
    session = MagicMock()
    if raise_exc is not None:
        session.post.side_effect = raise_exc
    else:
        response = MagicMock()
        response.status_code = status_code
        session.post.return_value = response
    return session


def test_perform_tick_success():
    outcome = worker.perform_tick(_mock_session(200), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is True
    assert outcome.status_code == 200
    assert outcome.rate_limited is False


def test_perform_tick_sends_the_secret_header_and_respects_timeout():
    session = _mock_session(200)
    worker.perform_tick(session, "https://example.com/tick", "my-secret", 7.5)
    call_kwargs = session.post.call_args.kwargs
    assert call_kwargs["headers"]["X-Monitor-Worker-Secret"] == "my-secret"
    assert call_kwargs["timeout"] == 7.5


def test_perform_tick_429_is_rate_limited_and_not_ok():
    outcome = worker.perform_tick(_mock_session(429), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is False
    assert outcome.rate_limited is True
    assert outcome.status_code == 429


def test_perform_tick_409_lock_conflict_is_ok_not_a_failure():
    """The endpoint's own overlapping-tick guard - benign, must not
    trigger backoff."""
    outcome = worker.perform_tick(_mock_session(409), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is True
    assert outcome.status_code == 409


def test_perform_tick_401_is_unauthorized_and_not_ok():
    outcome = worker.perform_tick(_mock_session(401), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is False
    assert outcome.status_code == 401
    assert "secret" in outcome.error.lower() or "unauthorized" in outcome.error.lower()


def test_perform_tick_5xx_is_a_broker_outage_style_failure():
    outcome = worker.perform_tick(_mock_session(503), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is False
    assert outcome.status_code == 503


def test_perform_tick_timeout_is_a_failure_with_no_status_code():
    outcome = worker.perform_tick(_mock_session(raise_exc=requests.exceptions.Timeout()), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is False
    assert outcome.status_code is None
    assert "timed out" in outcome.error.lower()


def test_perform_tick_connection_error_is_a_failure():
    outcome = worker.perform_tick(_mock_session(raise_exc=requests.exceptions.ConnectionError("refused")), "https://example.com/tick", "secret", 8.0)
    assert outcome.ok is False
    assert outcome.status_code is None
    assert "connection error" in outcome.error.lower()


# --- run_loop: sequential behavior, backoff-on-failure, jitter-on-success --------


def test_run_loop_is_strictly_sequential_never_overlapping():
    """No tick starts until the previous one's perform_tick call has
    fully returned - proven by recording call ORDER across a mocked
    perform_tick and sleep_fn: every sleep must occur strictly BETWEEN
    two ticks, never overlapping one."""
    call_order = []

    def _fake_perform_tick(session, url, secret, timeout):
        call_order.append("tick")
        return worker.TickOutcome(ok=True, status_code=200, rate_limited=False, error=None)

    def _fake_sleep(seconds):
        call_order.append("sleep")

    with patch.object(worker, "perform_tick", side_effect=_fake_perform_tick):
        worker.run_loop(
            endpoint_url="https://example.com/tick", secret="secret",
            interval_seconds=10.0, max_iterations=3, sleep_fn=_fake_sleep,
        )

    assert call_order == ["tick", "sleep", "tick", "sleep", "tick", "sleep"]


def test_run_loop_backs_off_after_consecutive_failures():
    sleep_durations = []

    def _fake_perform_tick(session, url, secret, timeout):
        return worker.TickOutcome(ok=False, status_code=503, rate_limited=False, error="server error")

    def _fake_sleep(seconds):
        sleep_durations.append(seconds)

    with patch.object(worker, "perform_tick", side_effect=_fake_perform_tick):
        worker.run_loop(
            endpoint_url="https://example.com/tick", secret="secret",
            interval_seconds=10.0, jitter_fraction=0.0, max_backoff_seconds=120,
            max_iterations=3, sleep_fn=_fake_sleep,
        )

    # sleep = interval + backoff, backoff doubling each consecutive failure.
    assert sleep_durations == [10.0 + 10.0, 10.0 + 20.0, 10.0 + 40.0]


def test_run_loop_resets_backoff_after_a_success():
    sleep_durations = []
    outcomes = [
        worker.TickOutcome(ok=False, status_code=503, rate_limited=False, error="down"),
        worker.TickOutcome(ok=False, status_code=503, rate_limited=False, error="down"),
        worker.TickOutcome(ok=True, status_code=200, rate_limited=False, error=None),
        worker.TickOutcome(ok=False, status_code=503, rate_limited=False, error="down"),
    ]

    def _fake_perform_tick(session, url, secret, timeout):
        return outcomes.pop(0)

    def _fake_sleep(seconds):
        sleep_durations.append(seconds)

    with patch.object(worker, "perform_tick", side_effect=_fake_perform_tick):
        worker.run_loop(
            endpoint_url="https://example.com/tick", secret="secret",
            interval_seconds=10.0, jitter_fraction=0.0, max_backoff_seconds=120,
            max_iterations=4, sleep_fn=_fake_sleep,
        )

    assert sleep_durations == [20.0, 30.0, 10.0, 20.0]  # backoff resets to 0 after the success (index 2)


def test_run_loop_rejects_a_timeout_not_shorter_than_the_interval():
    with pytest.raises(ValueError):
        worker.run_loop(
            endpoint_url="https://example.com/tick", secret="secret",
            interval_seconds=10.0, request_timeout_seconds=10.0, max_iterations=1,
        )


def test_run_loop_defaults_the_timeout_to_a_fraction_of_the_interval():
    captured = {}

    def _fake_perform_tick(session, url, secret, timeout):
        captured["timeout"] = timeout
        return worker.TickOutcome(ok=True, status_code=200, rate_limited=False, error=None)

    with patch.object(worker, "perform_tick", side_effect=_fake_perform_tick):
        worker.run_loop(endpoint_url="https://example.com/tick", secret="secret", interval_seconds=10.0, max_iterations=1, sleep_fn=lambda s: None)

    assert captured["timeout"] < 10.0
    assert captured["timeout"] == pytest.approx(8.0)


def test_run_loop_stops_cleanly_on_shutdown_signal():
    def _fake_perform_tick(session, url, secret, timeout):
        return worker.TickOutcome(ok=True, status_code=200, rate_limited=False, error=None)

    def _raising_sleep(seconds):
        raise worker._ShutdownRequested()

    with patch.object(worker, "perform_tick", side_effect=_fake_perform_tick):
        # max_iterations=None (the production default) - must still stop
        # via the shutdown exception rather than looping forever.
        worker.run_loop(endpoint_url="https://example.com/tick", secret="secret", interval_seconds=10.0, sleep_fn=_raising_sleep)
