"""Per-tick timing diagnostics for the continuous/fast monitor: accurate, and never able to break a tick."""

import logging
import time
from unittest.mock import MagicMock, patch

import app as pluto_app
from integrations import webull as webull_api
from webull.core.exception.exceptions import ServerException


def test_timed_records_stage_even_when_the_stage_raises():
    stages = {}
    try:
        pluto_app._timed(stages, "boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    except RuntimeError:
        pass
    assert "boom" in stages and stages["boom"] >= 0


def test_timed_returns_the_stage_result():
    stages = {}
    assert pluto_app._timed(stages, "add", lambda a, b: a + b, 2, 3) == 5
    assert "add" in stages


def test_call_stats_count_calls_and_429_backoff():
    webull_api.start_call_stats()
    responses = [ServerException("429", "rate limited", 429), "ok"]

    def call():
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(webull_api.time, "sleep") as sleep:
        assert webull_api._call_with_429_retry("test", call) == "ok"
    stats = webull_api.take_call_stats()
    assert stats["calls"] == 2 and stats["rate_limited"] == 1 and stats["backoff_seconds"] == 1.5
    sleep.assert_called_once_with(1.5)


def test_call_stats_are_inactive_unless_started():
    webull_api.take_call_stats()  # clear
    assert webull_api._call_with_429_retry("test", lambda: 7) == 7
    assert webull_api.take_call_stats() is None


def test_slow_tick_logs_a_warning_and_fast_tick_logs_info(caplog):
    stats = {"calls": 4, "seconds": 1.2, "rate_limited": 1, "backoff_seconds": 1.5}
    with caplog.at_level(logging.INFO, logger="plutotrade.app"):
        pluto_app._log_fast_monitor_timing("abcdef123456", time.perf_counter() - 25, {"orphan_discovery": 20.0}, stats)
        pluto_app._log_fast_monitor_timing("abcdef123456", time.perf_counter() - 2, {"orphan_discovery": 1.0}, stats)
    records = [r for r in caplog.records if "FAST_MONITOR_TIMING" in r.getMessage()]
    assert [r.levelno for r in records] == [logging.WARNING, logging.INFO]
    assert "orphan_discovery=20.00s" in records[0].getMessage() and "webull_429s=1" in records[0].getMessage()


def test_timing_log_never_raises():
    pluto_app._log_fast_monitor_timing("u", 0.0, None, None)  # bad inputs must not propagate


def test_tick_logs_timing_even_when_it_fails(user_id):
    with patch.object(pluto_app, "is_webull_configured", return_value=False), \
         patch.object(pluto_app, "get_webull_credentials", return_value={"app_key": "k", "app_secret": "s"}), \
         patch.object(pluto_app, "_log_fast_monitor_timing") as log:
        try:
            pluto_app._run_fast_order_monitor(user_id)
        except pluto_app.ValidationError:
            pass
    log.assert_called_once()


def test_memory_profiling_is_off_by_default_and_opt_in():
    """tracemalloc(10) slowed allocation-heavy code ~30x in a benchmark; it must not run unless asked for."""
    assert pluto_app._memory_profiling_requested({}) is False
    assert pluto_app._memory_profiling_requested({"PLUTO_MEMORY_PROFILING": ""}) is False
    assert pluto_app._memory_profiling_requested({"PLUTO_MEMORY_PROFILING": "0"}) is False
    assert pluto_app._memory_profiling_requested({"PLUTO_MEMORY_PROFILING": "1"}) is True
    assert pluto_app._memory_profiling_requested({"PLUTO_MEMORY_PROFILING": " On "}) is True
