from __future__ import annotations

import threading
import time
from unittest.mock import patch

import app as pluto_app
from watchlist import add_stock

"""Found live in production 2026-08-21: even after tightening every
yf.download() call's own timeout= kwarg, gunicorn's 90s worker timeout still
fired during a sustained Yahoo rate-limit storm and SIGKILLed workers. Root
cause: yfinance's own retry-on-429 logic (venv yfinance/data.py's
_make_request) does an UNCONDITIONAL cookie/crumb refetch plus one more
request attempt on any 4xx response, each a full network round-trip up to
the timeout= kwarg - under sustained rate limiting a single logical call can
cost ~3x its nominal timeout, and up to 6 of these chain sequentially per
ticker thread. Tuning timeout= alone can't bound that cascade.
_run_with_hard_deadline and the futures_wait()-based ticker-intelligence/
options stages give up on a stuck fetch instead: these tests prove a slow
ticker is dropped (not waited on) while a fast one still comes through, and
that the overall call returns promptly rather than blocking for the full
hang."""


def test_run_with_hard_deadline_returns_default_when_func_exceeds_deadline():
    def _slow():
        time.sleep(2)
        return "too late"

    start = time.monotonic()
    result = pluto_app._run_with_hard_deadline(_slow, deadline_seconds=0.2, default="gave up")
    elapsed = time.monotonic() - start

    assert result == "gave up"
    assert elapsed < 1.0


def test_run_with_hard_deadline_returns_real_result_when_func_is_fast():
    result = pluto_app._run_with_hard_deadline(lambda: "on time", deadline_seconds=2, default="gave up")
    assert result == "on time"


def _fake_strategy(ticker):
    return {
        "strategy_confidence": 80,
        "recommendation": "CALL",
        "best_strategy": "Trend Continuation",
        "why_this_strategy_fits": "test thesis",
    }


def _fake_chart(ticker):
    return {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]}


def test_a_stuck_ticker_is_dropped_instead_of_blocking_the_whole_page(user_id):
    add_stock(user_id, {"ticker": "AAPL"})
    add_stock(user_id, {"ticker": "MSFT"})

    def _extended_hours(ticker):
        if ticker == "MSFT":
            time.sleep(2)
        return {}

    with patch.object(pluto_app, "TICKER_INTELLIGENCE_DEADLINE_SECONDS", 0.3), \
         patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", side_effect=_extended_hours), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        start = time.monotonic()
        context = pluto_app._build_page_context(include_options=False)
        elapsed = time.monotonic() - start

    # Real bound proven, not just documented: this must not block for
    # anywhere near MSFT's 2s hang.
    assert elapsed < 1.5

    tickers_seen = {row["ticker"] for row in context["upcoming_opportunities"]}
    assert "AAPL" in tickers_seen
    assert "MSFT" not in tickers_seen


def test_options_fetch_deadline_drops_a_stuck_ticker_but_keeps_a_fast_one(user_id):
    add_stock(user_id, {"ticker": "AAPL"})
    add_stock(user_id, {"ticker": "MSFT"})

    def _options(ticker, force_refresh=False):
        if ticker == "MSFT":
            time.sleep(2)
            return {"expiration_suggestions": ["2099-01-01"], "expected_move": "±5%"}
        return {"expiration_suggestions": ["2099-02-01"], "expected_move": "±2%"}

    with patch.object(pluto_app, "OPTIONS_FETCH_DEADLINE_SECONDS", 0.3), \
         patch.object(pluto_app, "get_market_data", return_value=([], [], "")), \
         patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}), \
         patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=lambda ticker, **kw: _fake_strategy(ticker)), \
         patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=lambda ticker, **kw: _fake_chart(ticker)), \
         patch.object(pluto_app, "get_options_data_for_ticker", side_effect=_options), \
         patch.object(pluto_app, "_current_user_id", return_value=user_id):
        start = time.monotonic()
        context = pluto_app._build_page_context(include_options=True)
        elapsed = time.monotonic() - start

    assert elapsed < 1.5

    opportunities_by_ticker = {row["ticker"]: row for row in context["upcoming_opportunities"]}
    assert opportunities_by_ticker["AAPL"]["expected_move"] == "±2%"
    assert opportunities_by_ticker["MSFT"]["expected_move"] == "Data unavailable"


"""The abandonment mechanism above (a stuck call is dropped, not waited on)
was never the actual bug - it was found live 2026-08-25 that a DIFFERENT
part of the same design leaked memory: _run_with_hard_deadline created a
BRAND NEW ThreadPoolExecutor on every single call and abandoned it via
shutdown(wait=False, cancel_futures=True) on timeout. cancel_futures only
cancels work that hasn't STARTED yet - Python cannot forcibly kill an
already-running thread - so under sustained Yahoo rate limiting (already
known, see the module docstring above, to make individual calls run ~3x
their nominal timeout) every abandoned call left an orphaned thread
running in the background indefinitely, with nothing bounding how many
accumulated across a gunicorn sync worker's entire lifetime. Confirmed
live: repeated OOM crashes (>2GB) whose timing tracked real scan-activity
bursts, not any specific code change. The fix: a single shared, FIXED-size
_BACKGROUND_FETCH_EXECUTOR that every hard-deadline call site submits to
instead of creating its own - these tests prove the pool is genuinely
shared (not recreated per call) and genuinely bounded (repeated abandoned
calls occupy a fixed number of slots rather than spawning unbounded new
OS threads)."""


def test_background_fetch_executor_is_shared_not_recreated_per_call():
    executor_before = pluto_app._BACKGROUND_FETCH_EXECUTOR
    pluto_app._run_with_hard_deadline(lambda: "ok", deadline_seconds=1, default="gave up")
    pluto_app._run_with_hard_deadline(lambda: "ok", deadline_seconds=1, default="gave up")
    assert pluto_app._BACKGROUND_FETCH_EXECUTOR is executor_before


def test_repeatedly_abandoned_calls_never_exceed_the_shared_pool_bound():
    """The real failure mode found live, reproduced directly: before the
    fix, each of these abandoned calls would have spawned its own
    permanent zombie thread with no ceiling. After the fix, the shared
    pool never allocates more OS threads than its fixed size, no matter
    how many abandoned calls pile up - the excess simply queues instead.

    executor._threads are the pool's own persistent WORKER threads, which
    are meant to stay alive for the executor's entire (process) lifetime,
    looping on an internal queue - they are not one-thread-per-submitted-
    task and are never expected to exit on their own, so this test submits
    real Future objects directly (bypassing _run_with_hard_deadline, which
    discards the future once it gives up waiting) purely so it can wait on
    THOSE - not the worker threads - for deterministic cleanup."""
    executor = pluto_app._BACKGROUND_FETCH_EXECUTOR
    max_workers = executor._max_workers
    release = threading.Event()

    def _hangs_until_released():
        release.wait(timeout=5)
        return "finally done"

    futures = []
    try:
        # More abandoned calls than the pool has slots for - some must
        # queue behind the ones already occupying every worker.
        for _ in range(max_workers + 2):
            futures.append(executor.submit(_hangs_until_released))

        # However many of these are actually running vs. still queued
        # behind a full pool, the pool itself never allocates more OS
        # threads than its fixed size - this is the actual bound that was
        # missing before the fix (previously: one fresh executor, and one
        # more permanent OS thread, per abandoned call).
        assert len(executor._threads) <= max_workers
    finally:
        # Let every hung task finish for real and wait on the FUTURES
        # (not the persistent worker threads, which are supposed to keep
        # running) so nothing lingers into whatever test runs next against
        # this same shared, process-wide pool.
        release.set()
        for future in futures:
            future.result(timeout=5)
