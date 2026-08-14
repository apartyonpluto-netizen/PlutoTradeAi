from __future__ import annotations

"""Continuous order-monitor WORKER - Option A of the review's proposed
architecture (explicit reviewer decision, see conversation history).

This is a SEPARATE, independently-deployed process - a Render Background
Worker service, NOT part of the Flask web service (backend/app.py) and
NOT started by it. It has NO disk access and NO Webull credentials by
design: it is a supervised SCHEDULER only, calling the web service's own
authenticated /api/autonomy/continuous-monitor-tick endpoint on a tight,
strictly SEQUENTIAL loop (never firing the next request before the
previous one returns or times out - see run_loop below) so that endpoint
can perform reconciliation using the disk/credentials it already owns.

Run directly: `python continuous_monitor_worker.py`
Or as a module: `python -m continuous_monitor_worker`

Required environment variables:
    MONITOR_ENDPOINT_URL     Full URL of the continuous-monitor-tick
                             endpoint, e.g. https://plutotrade.example.com/api/autonomy/continuous-monitor-tick
    MONITOR_WORKER_SECRET    The dedicated secret this worker sends as the
                             X-Monitor-Worker-Secret header - INTENTIONALLY
                             separate from CRON_SECRET (see the endpoint's
                             own docstring in app.py for why).

Optional environment variables:
    MONITOR_INTERVAL_SECONDS       Loop interval in seconds (default 10 -
                                    start here per the review; only reduce
                                    toward 5 once sandbox evidence supports
                                    it - see the module docstring's own
                                    note on Webull rate limits).
    MONITOR_REQUEST_TIMEOUT_SECONDS
                                    HTTP request timeout in seconds -
                                    defaults to 80% of the interval,
                                    always strictly shorter than it, so a
                                    hung request can never itself cause
                                    request overlap.
    MONITOR_JITTER_FRACTION        Fraction of the interval to jitter by,
                                    +/- (default 0.15) - avoids every
                                    deployment/restart hitting the
                                    endpoint in lockstep if several are
                                    ever running (e.g. during a staged
                                    rollout).
    MONITOR_MAX_BACKOFF_SECONDS    Cap for the exponential backoff applied
                                    after consecutive failures/429s
                                    (default 120).

Nothing in this file places trades, reads market data, or touches
Webull/credentials/the persistent disk directly - see the endpoint's own
docstring in app.py for the structural guarantee that the SERVER side
never scans for candidates or places entries either."""

import logging
import os
import random
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("continuous_monitor_worker")

DEFAULT_INTERVAL_SECONDS = 10.0
DEFAULT_JITTER_FRACTION = 0.15
DEFAULT_MAX_BACKOFF_SECONDS = 120.0
DEFAULT_REQUEST_TIMEOUT_FRACTION = 0.8  # of the interval - always strictly shorter than the loop interval


@dataclass
class TickOutcome:
    """The classified result of ONE call to the continuous-monitor-tick
    endpoint - kept as a small typed value rather than a bare bool so
    run_loop's backoff decision and logging both read cleanly off it."""
    ok: bool
    status_code: Optional[int]
    rate_limited: bool
    error: Optional[str]


def perform_tick(session: requests.Session, endpoint_url: str, secret: str, timeout_seconds: float) -> TickOutcome:
    """ONE HTTP POST to the continuous-monitor-tick endpoint - no retry
    logic here, that's run_loop's job (this function's contract is just
    "make the call, classify what happened"). A 409 (the endpoint's own
    overlapping-request guard, or this worker's own request racing itself
    somehow) is treated as an ORDINARY, non-error outcome - the tick was
    simply skipped this round, not a failure worth backing off for."""
    try:
        response = session.post(
            endpoint_url,
            headers={"X-Monitor-Worker-Secret": secret},
            timeout=timeout_seconds,
        )
    except requests.exceptions.Timeout:
        return TickOutcome(ok=False, status_code=None, rate_limited=False, error="request timed out")
    except requests.exceptions.RequestException as error:
        return TickOutcome(ok=False, status_code=None, rate_limited=False, error=f"connection error: {error}")

    if response.status_code == 429:
        return TickOutcome(ok=False, status_code=429, rate_limited=True, error="rate limited (429)")
    if response.status_code == 409:
        # The endpoint's own overlapping-tick guard (or, in principle,
        # this worker's own request racing a slow-to-return previous
        # one, which the sequential loop below is specifically designed
        # to prevent) - benign, not a failure, no backoff warranted.
        return TickOutcome(ok=True, status_code=409, rate_limited=False, error=None)
    if response.status_code == 401:
        # Never worth retrying quickly - a bad/rotated secret won't fix
        # itself on the next tick. Still backs off (see run_loop) so a
        # misconfigured worker doesn't spam auth failures every 10s
        # forever, but this is surfaced distinctly in logs since it needs
        # a human to fix the deployment config, not a transient outage.
        return TickOutcome(ok=False, status_code=401, rate_limited=False, error="unauthorized - check MONITOR_WORKER_SECRET")
    if response.status_code >= 500:
        return TickOutcome(ok=False, status_code=response.status_code, rate_limited=False, error=f"server error {response.status_code}")
    if response.status_code != 200:
        return TickOutcome(ok=False, status_code=response.status_code, rate_limited=False, error=f"unexpected status {response.status_code}")

    return TickOutcome(ok=True, status_code=200, rate_limited=False, error=None)


def compute_backoff_seconds(consecutive_failures: int, base_interval: float, max_backoff_seconds: float) -> float:
    """Exponential backoff, capped - 1 failure waits one extra base
    interval, 2 waits two, doubling from there, never exceeding
    max_backoff_seconds. consecutive_failures == 0 (the success path)
    always returns 0 - no backoff on top of the normal interval."""
    if consecutive_failures <= 0:
        return 0.0
    backoff = base_interval * (2 ** (consecutive_failures - 1))
    return min(backoff, max_backoff_seconds)


def compute_jittered_sleep(interval: float, jitter_fraction: float) -> float:
    """Returns interval +/- a random fraction of itself, never negative -
    spreads out otherwise-synchronized request bursts across however many
    instances of this worker might ever be running (e.g. during a staged
    rollout, or if Render briefly runs old+new versions during a deploy)."""
    if jitter_fraction <= 0:
        return interval
    jitter = interval * jitter_fraction
    return max(0.0, interval + random.uniform(-jitter, jitter))


class _ShutdownRequested(Exception):
    """Raised from the SIGTERM/SIGINT handler to unwind run_loop's sleep
    promptly instead of waiting out a long backoff/jittered interval -
    clean shutdown, not an abrupt kill mid-request."""


def run_loop(
    *,
    endpoint_url: str,
    secret: str,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    request_timeout_seconds: Optional[float] = None,
    jitter_fraction: float = DEFAULT_JITTER_FRACTION,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    max_iterations: Optional[int] = None,
    sleep_fn=time.sleep,
) -> None:
    """The worker's actual loop - STRICTLY SEQUENTIAL: perform_tick blocks
    until the request returns or times out, and only THEN does the next
    sleep/tick get scheduled. There is no concurrent/overlapping request
    possible from this loop's own structure, independent of the
    endpoint's own belt-and-suspenders lock.

    request_timeout_seconds defaults to DEFAULT_REQUEST_TIMEOUT_FRACTION
    of interval_seconds if not given - ALWAYS strictly shorter than the
    interval, so a request that times out still leaves room for this
    loop to sleep (however briefly) before the next tick, rather than
    the timeout alone consuming the entire interval.

    max_iterations exists for TESTS only (bounds the loop instead of
    running forever) - production callers (main() below) leave it None
    and rely on SIGTERM/SIGINT for shutdown instead."""
    if request_timeout_seconds is None:
        request_timeout_seconds = interval_seconds * DEFAULT_REQUEST_TIMEOUT_FRACTION
    if request_timeout_seconds >= interval_seconds:
        raise ValueError("request_timeout_seconds must be strictly shorter than interval_seconds")

    session = requests.Session()
    consecutive_failures = 0
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        outcome = perform_tick(session, endpoint_url, secret, request_timeout_seconds)

        if outcome.ok:
            if consecutive_failures > 0:
                logger.info("continuous monitor tick recovered after %d consecutive failures", consecutive_failures)
            consecutive_failures = 0
            logger.debug("continuous monitor tick ok (status=%s)", outcome.status_code)
        else:
            consecutive_failures += 1
            logger.warning(
                "continuous monitor tick failed (attempt %d, status=%s): %s",
                consecutive_failures, outcome.status_code, outcome.error,
            )

        backoff_seconds = compute_backoff_seconds(consecutive_failures, interval_seconds, max_backoff_seconds)
        sleep_seconds = compute_jittered_sleep(interval_seconds, jitter_fraction) + backoff_seconds

        try:
            sleep_fn(sleep_seconds)
        except _ShutdownRequested:
            logger.info("shutdown requested - exiting run loop cleanly")
            break

    session.close()


def _install_signal_handlers() -> None:
    def _handler(signum, frame):  # noqa: ARG001 - signal handler signature required by signal.signal
        raise _ShutdownRequested()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> int:
    logging.basicConfig(level=os.environ.get("MONITOR_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    endpoint_url = os.environ.get("MONITOR_ENDPOINT_URL", "").strip()
    secret = os.environ.get("MONITOR_WORKER_SECRET", "").strip()
    if not endpoint_url or not secret:
        logger.error("MONITOR_ENDPOINT_URL and MONITOR_WORKER_SECRET are both required - refusing to start")
        return 1

    try:
        interval_seconds = float(os.environ.get("MONITOR_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
        jitter_fraction = float(os.environ.get("MONITOR_JITTER_FRACTION", DEFAULT_JITTER_FRACTION))
        max_backoff_seconds = float(os.environ.get("MONITOR_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF_SECONDS))
        request_timeout_raw = os.environ.get("MONITOR_REQUEST_TIMEOUT_SECONDS")
        request_timeout_seconds = float(request_timeout_raw) if request_timeout_raw else None
    except ValueError as error:
        logger.error("invalid numeric environment variable: %s", error)
        return 1

    _install_signal_handlers()
    logger.info("continuous monitor worker starting - interval=%.1fs endpoint=%s", interval_seconds, endpoint_url)
    try:
        run_loop(
            endpoint_url=endpoint_url,
            secret=secret,
            interval_seconds=interval_seconds,
            request_timeout_seconds=request_timeout_seconds,
            jitter_fraction=jitter_fraction,
            max_backoff_seconds=max_backoff_seconds,
        )
    except _ShutdownRequested:
        pass
    logger.info("continuous monitor worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
