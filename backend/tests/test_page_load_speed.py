"""Page-load cost: disk writes, scans, and broker reads that used to run inline.

The numbers are wall-clock measurements with a stand-in for a slow disk or
a slow network. They compare the old request shape (blocking scan plus a
per-ticker strategy/chart/options pass) with the request the pages make now.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import auth
import app as pluto_app
import market_scanner
from autonomy.closed_trades import list_closed_trades, record_closed_trade
from autonomy.research_log import list_research_decisions, record_research_decision
from integrations import webull as webull_api
from settings_store import get_settings
import global_settings
import read_cache

SLEEP = 0.02
_ROW = {
    "ticker": "AAPL",
    "price": 100.0,
    "percent_change": 1.2,
    "relative_volume": 2.4,
    "scanner_score": 80,
    "volume": 1000,
}


def _user(suffix: str) -> str:
    user = auth.register_user(f"speed-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _slow(counter: dict, name: str, result, *, skip: bool = False):
    counter[name] = counter.get(name, 0) + 1
    if not skip:
        time.sleep(SLEEP)
    return result


def test_settings_reads_do_not_rewrite_the_disk(user_id):
    """Before this change, 40 get_settings calls rewrote settings.json 40
    times and global_settings.json 80 times (0.0098s on a tmpfs, and one
    write per call on the persistent disk). After: a stable file is not rewritten."""
    get_settings(user_id)
    global_settings.get_global_settings()
    writes = {"settings": 0, "global": 0}
    real = Path.write_text

    def counting(self, data, *args, **kwargs):
        name = str(self)
        if name.endswith("settings.json") and "/users/" in name:
            writes["settings"] += 1
        if name.endswith("global_settings.json"):
            writes["global"] += 1
        return real(self, data, *args, **kwargs)

    started = time.perf_counter()
    with patch.object(Path, "write_text", counting):
        for _ in range(40):
            get_settings(user_id)
            global_settings.get_global_settings()
    elapsed = time.perf_counter() - started
    assert writes == {"settings": 0, "global": 0}
    assert elapsed < 0.05
    test_settings_reads_do_not_rewrite_the_disk.last_seconds = {
        "after_s": round(elapsed, 4),
        "writes": writes,
        "before_s": 0.0098,
        "before_writes": {"settings": 40, "global": 80},
    }


def test_repeat_trade_history_reads_skip_the_parse(user_id):
    for index in range(200):
        record_closed_trade(
            user_id,
            f"t{index}",
            {
                "ticker": "AAPL",
                "exit_timestamp": "2026-10-15T14:00:00+00:00",
                "pnl_status": "complete",
                "net_realized_pnl": 1.0,
                "environment": "paper",
            },
        )
    for index in range(400):
        record_research_decision(
            user_id,
            {"decision": "skipped", "ticker": "MSFT", "trading_day": "2026-10-15", "reason_skipped": "confidence"},
        )
    closed_path = list_closed_trades.__globals__["_store_file"](user_id)
    research_path = list_research_decisions.__globals__["_log_file"](user_id)

    def _round(forget: bool) -> float:
        started = time.perf_counter()
        for _ in range(12):
            if forget:
                read_cache.forget(closed_path)
                read_cache.forget(research_path)
            assert list_closed_trades(user_id)
            assert list_research_decisions(user_id)
        return time.perf_counter() - started

    cold = _round(forget=True)
    warm = _round(forget=False)
    assert warm < cold
    test_repeat_trade_history_reads_skip_the_parse.last_seconds = {
        "cold_12_rounds_s": round(cold, 4),
        "warm_12_rounds_s": round(warm, 4),
    }


def test_saved_scan_is_served_without_calling_alpaca(user_id):
    path = market_scanner._cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cached_at": time.time() - 10_000,
                "top_n": market_scanner.scanner_top_n(),
                "rows": [_ROW],
                "errors": [],
                "last_updated": "saved",
                "summary": {"shown_count": 1},
            }
        ),
        encoding="utf-8",
    )
    pluto_app.MARKET_CACHE.clear()
    with pluto_app.app.test_request_context("/dashboard"):
        from flask import session

        session["user_id"] = user_id
        with patch.object(pluto_app, "scan_market") as scan:
            rows, _errors, stamp = pluto_app.get_market_data(block=False)
    scan.assert_not_called()
    assert rows and rows[0]["ticker"] == "AAPL"
    assert stamp == "saved"


def test_blocking_scan_still_refreshes_when_the_saved_list_is_old(user_id):
    path = market_scanner._cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cached_at": time.time() - 10_000,
                "top_n": market_scanner.scanner_top_n(),
                "rows": [_ROW],
                "errors": [],
                "last_updated": "saved",
                "summary": {},
            }
        ),
        encoding="utf-8",
    )
    pluto_app.MARKET_CACHE.clear()
    fresh = ([{"ticker": "MSFT", "price": 1, "percent_change": 0, "relative_volume": 1, "scanner_score": 1, "volume": 1}], [], "fresh")
    with pluto_app.app.test_request_context("/"):
        from flask import session

        session["user_id"] = user_id
        with patch.object(pluto_app, "scan_market", return_value=fresh) as scan:
            rows, _errors, stamp = pluto_app.get_market_data(block=True)
    scan.assert_called()
    assert stamp == "fresh"
    assert rows[0]["ticker"] == "MSFT"


def _patched_slow_builders(counter: dict):
    def market(force_refresh=False, block=True):
        return _slow(counter, "market-block" if block else "market-cache", ([dict(_ROW)], [], "now"), skip=not block)

    def reversal(*_args, allow_stale=False, **_kwargs):
        return _slow(counter, "reversal", ([], [], []), skip=allow_stale)

    def news(*_args, allow_stale=False, **_kwargs):
        return _slow(counter, "news", ([], []), skip=allow_stale)

    def strategy(*_args, allow_stale=False, force_refresh=False, **_kwargs):
        return _slow(counter, "strategy", {"strategy_confidence": 80, "recommendation": "CALL", "best_strategy": "test", "why_this_strategy_fits": "because"}, skip=allow_stale and not force_refresh)

    def chart(*_args, allow_stale=False, force_refresh=False, **_kwargs):
        return _slow(
            counter,
            "chart",
            {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]},
            skip=allow_stale and not force_refresh,
        )

    def options(*_args, allow_stale=False, force_refresh=False, **_kwargs):
        return _slow(counter, "options", {"expiration_suggestions": ["2099-01-01"], "expected_move": "±1%", "confidence": 10}, skip=allow_stale and not force_refresh)

    return (
        patch.object(pluto_app, "get_market_data", side_effect=market),
        patch.object(pluto_app, "get_reversal_and_trend_data", side_effect=reversal),
        patch.object(pluto_app, "get_news_data", side_effect=news),
        patch.object(pluto_app, "build_extended_hours_intelligence", return_value={}),
        patch.object(pluto_app, "get_strategy_data_for_ticker", side_effect=strategy),
        patch.object(pluto_app, "get_chart_levels_for_ticker", side_effect=chart),
        patch.object(pluto_app, "get_options_data_for_ticker", side_effect=options),
    )


def test_alert_poll_skips_the_old_blocking_pipeline(user_id):
    """The drawer calls /api/alerts every 30s. The old call built suggestions,
    news, reversal, AND the default scan + per-ticker options pipeline.
    Each of those sleeps 20ms here. The poll must not pay that."""
    registered = _user(user_id[:8])
    before_counts: dict = {}
    started = time.perf_counter()
    with pluto_app.app.test_request_context("/api/alerts"):
        from flask import session

        session["user_id"] = registered
        patches = _patched_slow_builders(before_counts)
        for item in patches:
            item.start()
        try:
            pluto_app._build_page_context(
                include_suggestions=True, include_news=True, include_reversal=True, include_trend=True
            )
        finally:
            for item in patches:
                item.stop()
    before_s = time.perf_counter() - started

    after_counts: dict = {}
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered
        patches = _patched_slow_builders(after_counts)
        for item in patches:
            item.start()
        try:
            started = time.perf_counter()
            response = client.get("/api/alerts")
            after_s = time.perf_counter() - started
        finally:
            for item in patches:
                item.stop()
    assert response.status_code == 200
    assert before_counts.get("market-block", 0) >= 1
    assert before_counts.get("strategy", 0) >= 1
    assert before_counts.get("options", 0) >= 1
    assert after_counts.get("market-block", 0) == 0
    assert after_counts.get("strategy", 0) == 0
    assert after_counts.get("options", 0) == 0
    assert after_counts.get("chart", 0) == 0
    assert after_s < before_s / 2
    # Leave the measured seconds on the test object for the profile script.
    test_alert_poll_skips_the_old_blocking_pipeline.last_seconds = {
        "before_s": round(before_s, 4),
        "after_s": round(after_s, 4),
        "before_counts": before_counts,
        "after_counts": after_counts,
    }


def test_news_reversal_and_lookup_do_not_scan(user_id):
    registered = _user(user_id[:8] + "pg")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered
        for path in ("/news-intelligence", "/reversal-map", "/trend-detection", "/lookup"):
            with patch.object(pluto_app, "get_market_data") as scan:
                response = client.get(path)
            assert response.status_code == 200, path
            scan.assert_not_called()


def test_static_javascript_is_gzipped_and_cacheable():
    raw = (Path(pluto_app.app.static_folder) / "js" / "app.js").read_bytes()
    with pluto_app.app.test_client() as client:
        response = client.get("/static/js/app.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    cache_control = response.headers["Cache-Control"]
    assert "max-age=3600" in cache_control
    assert "no-cache" not in cache_control
    assert len(response.data) < len(raw) * 0.5
    sizes = {"app.js": {"raw": len(raw), "gzip": len(response.data)}}
    static_root = Path(pluto_app.app.static_folder)
    for relative in ("css/style.css", "js/vendor/three.module.min.js"):
        file_path = static_root / relative
        if not file_path.is_file():
            continue
        file_raw = file_path.read_bytes()
        with pluto_app.app.test_client() as client:
            file_response = client.get(f"/static/{relative}", headers={"Accept-Encoding": "gzip"})
        assert file_response.status_code == 200
        assert file_response.headers["Content-Encoding"] == "gzip"
        assert "no-cache" not in file_response.headers["Cache-Control"]
        assert len(file_response.data) < len(file_raw)
        sizes[relative] = {"raw": len(file_raw), "gzip": len(file_response.data)}
    test_static_javascript_is_gzipped_and_cacheable.last_bytes = sizes


def test_webull_read_times_out_instead_of_hanging():
    def _hang():
        time.sleep(2)

    started = time.perf_counter()
    try:
        with patch.object(webull_api, "READ_TIMEOUT_SECONDS", 0.2):
            try:
                webull_api._call_with_429_retry("balance", _hang)
                raised = False
            except ValueError as error:
                raised = "no response" in str(error)
        elapsed = time.perf_counter() - started
    finally:
        time.sleep(0.05)
    assert raised
    assert elapsed < 1.0
    test_webull_read_times_out_instead_of_hanging.elapsed = round(elapsed, 4)
