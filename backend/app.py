from __future__ import annotations

import hmac
import json
import math
import os
import resource
import secrets as secrets_module
import sys
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait as futures_wait
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for

if __package__:
    from .auth import (
        admin_reset_user_password,
        approve_user,
        authenticate_user,
        delete_user_account,
        find_user_by_webhook_token,
        get_user_by_id,
        is_admin,
        list_all_user_ids,
        list_all_users,
        list_pending_users,
        public_user,
        register_user,
        reject_user,
        reset_password,
        set_user_role,
        set_user_suspended,
    )
    from .global_settings import get_global_settings, update_global_settings
    from .autonomy.autonomous_controller import (
        emergency_stop,
        get_autonomy_status,
        reset_emergency_stop,
        set_mode,
        update_risk_settings,
    )
    from .account_hub import (
        connect_account,
        disconnect_account,
        ensure_tradingview_webhook,
        get_accounts,
        record_tradingview_signal,
        test_account,
        update_trading_enabled,
        verify_tradingview_token,
    )
    from .alerts import (
        add_manual_alert,
        annotate_positions_with_exit_signal,
        build_exit_signal_alerts,
        build_system_alerts,
        dismiss_alert,
        get_alerts_snapshot,
        mark_alert_read,
        mark_all_read,
        unread_count,
    )
    from .analysis_lists import (
        MAX_TICKERS_PER_SECTION,
        add_focus_ticker,
        add_section_ticker,
        get_section_tickers,
        remove_section_ticker,
    )
    from .analytics import build_reversal_and_trend_payload, build_reversal_map, detect_early_trends, fetch_price_history
    from .candle_brain import analyze_candles
    from .core.errors import PlutoTradeError, ValidationError
    from .core.logger import get_logger, setup_logging
    from .brokers.etrade_broker import ETradeBroker
    from .brokers.webull_broker import WebullBroker
    from .brains.charting_brain import build_chart_levels
    from .brains.extended_hours_brain import build_extended_hours_intelligence
    from .brains.strategy_brain import build_strategy_intelligence
    from .brains.llm_reasoning import get_llm_verdict
    from .regime import compute_shadow_adjustment, get_vix_snapshot
    from .integrations.tradingview import get_tradingview_status, save_alert
    from .integrations import webull as webull_api
    from .integrations import alpaca_data
    from .autonomy.options_selector import select_option_contract
    from .webull_credentials import (
        get_webull_credentials,
        is_webull_configured,
        set_webull_credentials,
        get_virtual_net_account_value,
        get_virtual_starting_balance,
    )
    from .webull_stop_orders import (
        get_exit_orders,
        pop_exit_order_by_id,
        pop_exit_orders,
        pop_exit_orders_by_type,
        record_exit_order,
        tracked_tickers as webull_tracked_tickers,
    )
    from .autonomy.closed_trades import get_closed_trade, list_closed_trades, record_closed_trade
    from .autonomy.performance_report import build_performance_report
    from .autonomy.daily_digest import build_daily_digest
    from .fast_monitor_heartbeat import (
        get_heartbeat_status as get_fast_monitor_heartbeat_status,
        record_run_completed as record_fast_monitor_run_completed,
        record_run_started as record_fast_monitor_run_started,
    )
    from .full_scan_heartbeat import (
        get_heartbeat_status as get_full_scan_heartbeat_status,
        record_run_completed as record_full_scan_run_completed,
        record_run_started as record_full_scan_run_started,
    )
    from .continuous_monitor_heartbeat import (
        get_heartbeat_status as get_continuous_monitor_heartbeat_status,
        record_reconciliation_completed as record_continuous_monitor_reconciliation_completed,
        record_request_received as record_continuous_monitor_request_received,
    )
    from .scan_lock import (
        ContinuousMonitorTickAlreadyRunningError,
        ScanAlreadyRunningError,
        continuous_monitor_tick_lock,
        user_scan_lock,
    )
    from . import order_lifecycle as ol
    from .anthropic_credentials import get_anthropic_api_key, is_anthropic_configured, set_anthropic_api_key
    from .autonomy.overnight_orders import list_overnight_orders, record_overnight_order, replace_overnight_orders
    from .autonomy.research_log import record_research_decision
    from .autonomy.scan_run_log import list_scan_runs, record_scan_run
    from .autonomy.ambiguous_resolution_audit import (
        find_incomplete_resolutions,
        list_ambiguous_resolution_audit,
        record_ambiguous_resolution_audit,
        RESOLUTION_PHASE_COMPLETED,
        RESOLUTION_PHASE_FAILED,
        RESOLUTION_PHASE_STARTED,
    )
    from .backtest_engine import run_backtest
    from .calibration import get_calibration, start_calibration
    from .market_scanner import scan_market
    from .news.future_news import get_future_news_roadmap
    from .news.news_service import fetch_news_bundle
    from .news.x_news import (
        add_trusted_account,
        fetch_x_news_for_watchlist,
        get_trusted_accounts,
        lookup_x_user,
        remove_trusted_account,
    )
    from .neural.neural_engine import build_neural_status
    from .options.options_brain import build_options_research, get_full_option_chain, to_legacy_options_payload
    from .paper_trader import close_trade as close_paper_trade
    from .paper_trader import get_summary as get_paper_trade_summary
    from .paper_trader import list_trades as list_paper_trades
    from .paper_trader import open_trade as open_paper_trade
    from .pattern_brain import analyze_patterns
    from .settings_store import available_themes, get_settings, update_settings
    from .watchlist import (
        add_stock,
        build_watchlist_suggestions,
        delete_stock,
        dismiss_suggestion,
        get_watchlist,
        get_watchlist_tickers,
        list_dismissed_suggestions,
        search_watchlist,
        sort_watchlist,
        update_stock,
    )
else:
    from auth import (
        admin_reset_user_password,
        approve_user,
        authenticate_user,
        delete_user_account,
        find_user_by_webhook_token,
        get_user_by_id,
        is_admin,
        list_all_user_ids,
        list_all_users,
        list_pending_users,
        public_user,
        register_user,
        reject_user,
        reset_password,
        set_user_role,
        set_user_suspended,
    )
    from global_settings import get_global_settings, update_global_settings
    from autonomy.autonomous_controller import (
        emergency_stop,
        get_autonomy_status,
        reset_emergency_stop,
        set_mode,
        update_risk_settings,
    )
    from account_hub import (
        connect_account,
        disconnect_account,
        ensure_tradingview_webhook,
        get_accounts,
        record_tradingview_signal,
        test_account,
        update_trading_enabled,
        verify_tradingview_token,
    )
    from alerts import (
        add_manual_alert,
        annotate_positions_with_exit_signal,
        build_exit_signal_alerts,
        build_system_alerts,
        dismiss_alert,
        get_alerts_snapshot,
        mark_alert_read,
        mark_all_read,
        unread_count,
    )
    from analysis_lists import (
        MAX_TICKERS_PER_SECTION,
        add_focus_ticker,
        add_section_ticker,
        get_section_tickers,
        remove_section_ticker,
    )
    from analytics import build_reversal_and_trend_payload, build_reversal_map, detect_early_trends, fetch_price_history
    from candle_brain import analyze_candles
    from core.errors import PlutoTradeError, ValidationError
    from core.logger import get_logger, setup_logging
    from brokers.etrade_broker import ETradeBroker
    from brokers.webull_broker import WebullBroker
    from brains.charting_brain import build_chart_levels
    from brains.extended_hours_brain import build_extended_hours_intelligence
    from brains.strategy_brain import build_strategy_intelligence
    from brains.llm_reasoning import get_llm_verdict
    from regime import compute_shadow_adjustment, get_vix_snapshot
    from integrations.tradingview import get_tradingview_status, save_alert
    from integrations import webull as webull_api
    from integrations import alpaca_data
    from autonomy.options_selector import select_option_contract
    from webull_credentials import (
        get_webull_credentials,
        is_webull_configured,
        set_webull_credentials,
        get_virtual_net_account_value,
        get_virtual_starting_balance,
    )
    from webull_stop_orders import (
        get_exit_orders,
        pop_exit_order_by_id,
        pop_exit_orders,
        pop_exit_orders_by_type,
        record_exit_order,
        tracked_tickers as webull_tracked_tickers,
    )
    from autonomy.closed_trades import get_closed_trade, list_closed_trades, record_closed_trade
    from autonomy.performance_report import build_performance_report
    from autonomy.daily_digest import build_daily_digest
    from fast_monitor_heartbeat import (
        get_heartbeat_status as get_fast_monitor_heartbeat_status,
        record_run_completed as record_fast_monitor_run_completed,
        record_run_started as record_fast_monitor_run_started,
    )
    from full_scan_heartbeat import (
        get_heartbeat_status as get_full_scan_heartbeat_status,
        record_run_completed as record_full_scan_run_completed,
        record_run_started as record_full_scan_run_started,
    )
    from continuous_monitor_heartbeat import (
        get_heartbeat_status as get_continuous_monitor_heartbeat_status,
        record_reconciliation_completed as record_continuous_monitor_reconciliation_completed,
        record_request_received as record_continuous_monitor_request_received,
    )
    from scan_lock import (
        ContinuousMonitorTickAlreadyRunningError,
        ScanAlreadyRunningError,
        continuous_monitor_tick_lock,
        user_scan_lock,
    )
    import order_lifecycle as ol
    from anthropic_credentials import get_anthropic_api_key, is_anthropic_configured, set_anthropic_api_key
    from autonomy.overnight_orders import list_overnight_orders, record_overnight_order, replace_overnight_orders
    from autonomy.research_log import record_research_decision
    from autonomy.scan_run_log import list_scan_runs, record_scan_run
    from autonomy.ambiguous_resolution_audit import (
        find_incomplete_resolutions,
        list_ambiguous_resolution_audit,
        record_ambiguous_resolution_audit,
        RESOLUTION_PHASE_COMPLETED,
        RESOLUTION_PHASE_FAILED,
        RESOLUTION_PHASE_STARTED,
    )
    from backtest_engine import run_backtest
    from calibration import get_calibration, start_calibration
    from market_scanner import scan_market
    from news.future_news import get_future_news_roadmap
    from news.news_service import fetch_news_bundle
    from news.x_news import (
        add_trusted_account,
        fetch_x_news_for_watchlist,
        get_trusted_accounts,
        lookup_x_user,
        remove_trusted_account,
    )
    from neural.neural_engine import build_neural_status
    from options.options_brain import build_options_research, get_full_option_chain, to_legacy_options_payload
    from paper_trader import close_trade as close_paper_trade
    from paper_trader import get_summary as get_paper_trade_summary
    from paper_trader import list_trades as list_paper_trades
    from paper_trader import open_trade as open_paper_trade
    from pattern_brain import analyze_patterns
    from settings_store import available_themes, get_settings, update_settings
    from watchlist import (
        add_stock,
        build_watchlist_suggestions,
        delete_stock,
        dismiss_suggestion,
        get_watchlist,
        get_watchlist_tickers,
        list_dismissed_suggestions,
        search_watchlist,
        sort_watchlist,
        update_stock,
    )

app = Flask(__name__)
setup_logging()
logger = get_logger("app")

_DATA_DIR_FOR_SECRET = Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
_DATA_DIR_FOR_SECRET.mkdir(parents=True, exist_ok=True)
_SECRET_KEY_FILE = _DATA_DIR_FOR_SECRET / ".flask_secret_key"


def _resolve_secret_key() -> str:
    env_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    if _SECRET_KEY_FILE.exists():
        stored = _SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets_module.token_hex(32)
    _SECRET_KEY_FILE.write_text(generated, encoding="utf-8")
    return generated


app.secret_key = _resolve_secret_key()

# Off by default so local HTTP testing (http://localhost:...) keeps working -
# a Secure cookie is refused by the browser over plain HTTP, which would
# silently break login. Every PaaS host gives you HTTPS by default, so set
# FORCE_SECURE_COOKIES=1 once deployed there.
if os.environ.get("FORCE_SECURE_COOKIES", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# --- TEMPORARY memory-leak diagnostic instrumentation (2026-08-19) ---------
# Added while actively debugging recurring OOM crashes on Render. The
# trade-client-caching fix in integrations/webull.py cut the crash
# frequency from every 5-30 minutes down to roughly every 2 hours, but a
# residual, slower leak clearly remains, and an Explore-agent audit ruled
# out every module-level cache in this file (MARKET_CACHE and friends
# below) as unreachable from the hot ~10s continuous-monitor loop. With no
# shell/debugger access to the live deployment, this points tracemalloc
# directly at that loop instead and logs the biggest-growing allocation
# sites periodically via the normal logger, so they show up in Render's own
# log viewer with no extra tooling needed. REMOVE once the residual leak is
# found and fixed - not meant to be permanent. ru_maxrss's unit is
# platform-dependent (KB on Linux, where this actually runs in production;
# bytes on macOS) - the /1024 below is correct for the Linux deployment,
# not necessarily for local dev.
_MEMORY_PROFILING_ENABLED = (
    os.environ.get("PLUTO_MEMORY_PROFILING", "1").strip().lower() not in ("0", "false", "off")
    # tracemalloc instruments every allocation process-wide - real, measurable
    # overhead across the whole test suite for zero diagnostic value (tests
    # never loop the continuous-monitor-tick endpoint enough times to log
    # anything). Skip it under pytest - checking sys.modules rather than the
    # PYTEST_CURRENT_TEST env var, since that var is only set once a test is
    # actually RUNNING, not yet during collection when this module (and this
    # line) first gets imported; the pytest package itself is already in
    # sys.modules by then regardless.
    and "pytest" not in sys.modules
)
if _MEMORY_PROFILING_ENABLED:
    tracemalloc.start(10)
_memory_profile_baseline_snapshot = tracemalloc.take_snapshot() if _MEMORY_PROFILING_ENABLED else None
_memory_profile_tick_count = 0
_MEMORY_PROFILE_LOG_EVERY_N_TICKS = 30  # ~5 minutes at the worker's default 10s interval


def _maybe_log_memory_profile_snapshot() -> None:
    """Called once per continuous-monitor-tick request - logs a tracemalloc
    diff against the FIRST snapshot ever taken (process start) every
    _MEMORY_PROFILE_LOG_EVERY_N_TICKS calls, so the log shows exactly which
    allocation site is growing over time, not just that memory is growing
    overall. Never lets a profiling failure affect the real tick - this is
    diagnostic-only, wrapped defensively."""
    global _memory_profile_tick_count
    if not _MEMORY_PROFILING_ENABLED:
        return
    _memory_profile_tick_count += 1
    if _memory_profile_tick_count % _MEMORY_PROFILE_LOG_EVERY_N_TICKS != 0:
        return
    try:
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        logger.warning("MEMORY_PROFILE: tick=%d peak_rss_mb=%.1f", _memory_profile_tick_count, peak_rss_mb)
        current_snapshot = tracemalloc.take_snapshot()
        top_stats = current_snapshot.compare_to(_memory_profile_baseline_snapshot, "lineno")
        for stat in top_stats[:8]:
            logger.warning("MEMORY_PROFILE:   %s", stat)
    except Exception as error:  # noqa: BLE001 - diagnostic code must never affect the real tick
        logger.warning("MEMORY_PROFILE: snapshot failed: %s", error)
# --- end temporary memory-leak diagnostic instrumentation ------------------

# Curated, liquid scan universe. scan_market() fetches this via
# alpaca_data.get_bars' multi-symbol batched endpoint (two calls total -
# daily + intraday - regardless of list size, see market_scanner.py), and
# only the top-6-by-scanner-score names ever reach a deep LLM analysis
# call (see _resolve_analysis_tickers) - so growing this list widens
# top-of-funnel screening at zero added LLM cost and a fixed 2-request
# market-data cost.
#
# Expanded 2026-08-28 from the original 48-ticker Nasdaq-100-heavy list
# (found live: the app was structurally only ever going to find Nasdaq-100
# tech names, e.g. WDAY, regardless of what was actually moving elsewhere
# in the market that day) to add real sector breadth - financials,
# energy, industrials, healthcare/pharma, consumer, and communications
# names with none of that Nasdaq-100 overlap - plus a set of
# high-liquidity, high-beta momentum names and two broad-index ETFs
# (IWM, DIA) alongside the existing SPY/QQQ. Every addition is a
# large-cap, high-average-volume name capable of a reliable broker-side
# stop/limit fill - the same liquidity bar the original list was curated
# to, not a loosening of it. This trades the underlying equity long/short
# with a real stop-loss (CALL/PUT here is a directional-bias label, not a
# literal options contract - see the "only CALL/bullish setups auto-order"
# skip reason elsewhere in this file), so no options-chain liquidity is
# needed for any of these.
CORE_SCAN_UNIVERSE = [
    # Original Nasdaq-100-heavy core + SPY/QQQ (unchanged)
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "COST", "NFLX",
    "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTC", "QCOM", "TXN", "AMAT", "INTU",
    "ISRG", "BKNG", "VRTX", "REGN", "GILD", "MU", "LRCX", "KLAC", "PANW", "ADI",
    "MDLZ", "PYPL", "SNPS", "CDNS", "CRWD", "MRVL", "ABNB", "DXCM", "ORLY", "MNST",
    "CTAS", "PDD", "MELI", "WDAY", "ROP", "PLTR", "SPY", "QQQ",
    # Financials - zero prior representation
    "JPM", "BAC", "WFC", "GS", "MS", "SCHW", "V", "MA", "AXP",
    # Healthcare / pharma outside the Nasdaq-100 names already above
    "UNH", "LLY", "JNJ", "PFE", "MRK", "ABBV", "TMO",
    # Energy - zero prior representation
    "XOM", "CVX", "COP", "SLB",
    # Industrials - zero prior representation
    "BA", "CAT", "GE", "HON", "UPS", "LMT", "DE",
    # Consumer discretionary / staples
    "WMT", "HD", "DIS", "NKE", "SBUX", "MCD", "PG", "KO", "TGT",
    # Communications
    "CMCSA", "T", "VZ",
    # High-liquidity, high-beta momentum names - the retail/day-trading
    # volume this scanner's RVOL and day-move signals are built to catch
    "COIN", "MSTR", "SMCI", "ARM", "DKNG", "RIVN", "SOFI", "SNOW", "UBER", "ROKU", "SHOP", "AFRM",
    # Broad-index ETFs beyond SPY/QQQ
    "IWM", "DIA",
]
MARKET_CACHE: Dict[str, object] = {"rows": [], "errors": [], "last_updated": "", "expires_at": None}
ANALYTICS_CACHE: Dict[str, object] = {
    "ticker_key": (),
    "reversal_rows": [],
    "trend_rows": [],
    "errors": [],
    "expires_at": None,
}
NEWS_CACHE: Dict[str, object] = {"ticker_key": (), "rows": [], "errors": [], "expires_at": None}
CANDLE_CACHE: Dict[str, object] = {"ticker_key": (), "rows": [], "errors": [], "expires_at": None}
PATTERN_CACHE: Dict[str, object] = {"ticker_key": (), "rows": [], "errors": [], "expires_at": None}
OPTIONS_CACHE: Dict[str, Dict[str, object]] = {}
STRATEGY_CACHE: Dict[str, Dict[str, object]] = {}
CHART_LEVEL_CACHE: Dict[str, Dict[str, object]] = {}
# Raw OHLC history for the real dashboard chart (2026-09-04) - a sibling
# cache to CHART_LEVEL_CACHE, same TTL idiom, kept separate on purpose:
# single responsibility (candles vs. computed levels), and the two are
# fetched together by the frontend but don't need to invalidate together.
CHART_HISTORY_CACHE: Dict[str, Dict[str, object]] = {}
CACHE_SECONDS = 45
ANALYTICS_CACHE_SECONDS = 180
NEWS_CACHE_SECONDS = 120
PATTERN_CACHE_SECONDS = 180
OPTIONS_CACHE_SECONDS = 150
STRATEGY_CACHE_SECONDS = 120
CHART_LEVEL_CACHE_SECONDS = 120
CHART_HISTORY_CACHE_SECONDS = 120

NAV_ITEMS = [
    {"label": "Mission Briefing", "path": "/"},
    {"label": "Dashboard", "path": "/dashboard"},
    {"label": "Watchlist", "path": "/watchlist"},
    {"label": "Market Scanner", "path": "/market-scanner"},
    {"label": "Mission Control", "path": "/mission-control"},
    {"label": "Account Hub", "path": "/account-hub"},
    {"label": "Notifications", "path": "/notifications"},
    {"label": "Trade Journal", "path": "/trade-journal"},
    {"label": "Settings", "path": "/settings"},
    {"label": "Candle Brain", "path": "/candle-brain"},
    {"label": "Pattern Brain", "path": "/pattern-brain"},
    {"label": "Support & Resistance", "path": "/support-resistance"},
    {"label": "Volume Scanner", "path": "/volume-scanner"},
    {"label": "News Intelligence", "path": "/news-intelligence"},
    {"label": "AI Options", "path": "/options"},
    {"label": "Neural Engine", "path": "/neural-engine"},
]


def _api_timestamp() -> str:
    return _now_utc().isoformat()


def _api_success(data: Any, status_code: int = 200, **legacy_fields: Any):
    payload: Dict[str, Any] = {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": _api_timestamp(),
    }
    payload.update(legacy_fields)
    return jsonify(payload), status_code


def _api_failure(message: str, status_code: int = 400, error_code: str = "request_error", **legacy_fields: Any):
    payload: Dict[str, Any] = {
        "success": False,
        "data": None,
        "error": {"message": message, "code": error_code},
        "timestamp": _api_timestamp(),
    }
    payload.update(legacy_fields)
    return jsonify(payload), status_code


def _handle_api_exception(error: Exception):
    if isinstance(error, PlutoTradeError):
        logger.warning("API error: %s", error.message, extra={"code": error.error_code, "details": error.details})
        return _api_failure(error.message, status_code=error.status_code, error_code=error.error_code, ok=False)
    logger.exception("Unhandled API exception")
    return _api_failure(
        "Unexpected server error. Please retry or review logs.",
        status_code=500,
        error_code="internal_error",
        ok=False,
    )


def api_guard(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception as error:  # centralized API safety wrapper
            return _handle_api_exception(error)

    return wrapped


@app.before_request
def _log_request() -> None:
    logger.info("Request: %s %s", request.method, request.path)


@app.after_request
def _log_response(response):
    logger.info("Response: %s %s -> %s", request.method, request.path, response.status_code)
    return response


@app.route("/service-worker.js")
def service_worker():
    # Served from the root (not /static/) so its default scope covers the
    # whole app - a service worker's scope is limited to the directory it's
    # served from unless explicitly widened.
    return send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")


_PUBLIC_PATHS = {"/login", "/register", "/logout", "/forgot-password", "/service-worker.js", "/healthz", "/api/autonomy/monitor-health"}
_PUBLIC_PATH_PREFIXES = ("/static/",)
_TOKEN_AUTH_PATHS = {
    "/api/tradingview/webhook",
    "/api/autonomy/cron-trigger",
    "/api/autonomy/fast-monitor-trigger",
    "/api/autonomy/continuous-monitor-tick",
}


@app.before_request
def _require_login():
    path = request.path
    if path in _PUBLIC_PATHS or path in _TOKEN_AUTH_PATHS:
        return None
    if any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES):
        return None

    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if user and user.get("approved", True) and not user.get("suspended", False):
        return None

    session.pop("user_id", None)
    if path.startswith("/api/"):
        return _api_failure("Authentication required. Please log in.", status_code=401, error_code="unauthorized", ok=False)
    return redirect(url_for("login_page", next=path))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        if session.get("user_id") and get_user_by_id(session["user_id"]):
            return redirect(url_for("dashboard_page"))
        return render_template("login.html", error="", next_path=request.args.get("next", ""))

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    next_path = request.form.get("next", "") or ""
    user = authenticate_user(username, password)
    if not user:
        return render_template("login.html", error="Incorrect username or password.", next_path=next_path), 401
    if not user.get("approved", True):
        return render_template("login.html", error="Your account is still pending admin approval.", next_path=next_path), 403
    if user.get("suspended", False):
        return render_template("login.html", error="This account has been suspended.", next_path=next_path), 403

    session["user_id"] = user["id"]
    session.permanent = True
    target = next_path if next_path.startswith("/") else url_for("dashboard_page")
    return redirect(target)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    # Registration stays open for the very first account regardless of the
    # setting (there'd be no admin yet to have configured it, and no admin
    # to approve anyone in), and only blocks signups after that.
    registration_open = get_global_settings()["registration_open"] or not list_all_user_ids()
    if request.method == "GET":
        if session.get("user_id") and get_user_by_id(session["user_id"]):
            return redirect(url_for("dashboard_page"))
        if not registration_open:
            return render_template("register.html", error="Registration is currently closed."), 403
        return render_template("register.html", error="")

    if not registration_open:
        return render_template("register.html", error="Registration is currently closed."), 403

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if password != confirm_password:
        return render_template("register.html", error="Passwords do not match."), 400
    try:
        user = register_user(username, password)
    except ValueError as error:
        return render_template("register.html", error=str(error)), 400

    if not user.get("approved", True):
        return render_template(
            "login.html",
            error="Account created. An admin needs to approve it before you can sign in.",
            next_path="",
        )

    session["user_id"] = user["id"]
    session.permanent = True
    return redirect(url_for("dashboard_page"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "GET":
        return render_template("forgot_password.html", error="", success="")

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if password != confirm_password:
        return render_template("forgot_password.html", error="Passwords do not match.", success=""), 400
    try:
        reset_password(username, password)
    except ValueError as error:
        return render_template("forgot_password.html", error=str(error), success=""), 400

    return render_template("forgot_password.html", error="", success="Password updated. You can sign in now.")


@app.route("/healthz")
def healthz():
    """Unauthenticated liveness check for the hosting platform's health probe -
    intentionally does nothing but confirm the process is up and can render a
    response, no auth/DB/broker calls that could make a slow dependency look
    like a dead app."""
    return {"status": "ok"}, 200


@app.route("/logout", methods=["POST"])
def logout_page():
    session.pop("user_id", None)
    return redirect(url_for("login_page"))


def _current_user_id() -> str:
    """Guaranteed non-empty for any route reachable past the before_request auth gate."""
    return session.get("user_id", "")


def _require_admin():
    if not is_admin(_current_user_id()):
        return _api_failure("Admin access required.", status_code=403, error_code="forbidden", ok=False)
    return None


@app.route("/admin")
def admin_page():
    if not is_admin(_current_user_id()):
        return redirect(url_for("dashboard_page"))
    # include_market_scan=False, include_opportunities=False - admin.html
    # doesn't render scanner_rows/upcoming_opportunities/mission_queue, so
    # this page was paying the full market-scan + per-ticker
    # strategy/chart/options pipeline (confirmed unused by grepping the
    # template) for nothing. Same pattern as the /daily-digest and
    # /performance fix.
    context = _build_page_context(include_market_scan=False, include_opportunities=False)
    context["pending_users"] = list_pending_users()
    context["all_users"] = list_all_users()
    context["current_user_id"] = _current_user_id()
    context["global_settings"] = get_global_settings()
    context["calibration"] = get_calibration()
    pending_ambiguous: List[Dict[str, object]] = []
    for user in context["all_users"]:
        target_user_id = user.get("id", "")
        if not target_user_id:
            continue
        for order in list_overnight_orders(target_user_id):
            if order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
                pending_ambiguous.append({**order, "user_id": target_user_id, "username": user.get("username", "")})
    context["pending_ambiguous_submissions"] = pending_ambiguous
    return render_template("admin.html", **context)


@app.route("/admin/users/<user_id>")
def admin_user_activity_page(user_id: str):
    """Read-only view of another user's trading activity - no trade-affecting
    actions live here, so an admin can help debug a friend's setup without
    ever being able to place/close a trade on their behalf."""
    if not is_admin(_current_user_id()):
        return redirect(url_for("dashboard_page"))
    target_user = get_user_by_id(user_id)
    if not target_user:
        return redirect(url_for("admin_page"))
    # Base chrome (sidebar/topbar) reflects the admin's own session as usual;
    # only the page body below shows the target user's data.
    # include_market_scan=False, include_opportunities=False - same reason
    # as admin_page: admin_user_activity.html never renders scanner_rows or
    # opportunities data.
    context = _build_page_context(include_market_scan=False, include_opportunities=False)
    context.update(
        {
            "target_user": public_user(target_user),
            "accounts": get_accounts(user_id),
            "webull_balance": _get_live_webull_balance(user_id, force_refresh=True),
            "webull_positions": _get_live_webull_positions(user_id, force_refresh=True),
            "autonomy_status": get_autonomy_status(user_id),
            "paper_summary": get_paper_trade_summary(user_id),
        }
    )
    return render_template("admin_user_activity.html", **context)


@app.route("/api/admin/approve-user", methods=["POST"])
def api_admin_approve_user():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        approve_user(payload.get("user_id", ""))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="not_found", ok=False)
    return _api_success({}, ok=True)


@app.route("/api/admin/reject-user", methods=["POST"])
def api_admin_reject_user():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        reject_user(payload.get("user_id", ""))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="not_found", ok=False)
    return _api_success({}, ok=True)


@app.route("/api/admin/set-role", methods=["POST"])
def api_admin_set_role():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        user = set_user_role(payload.get("user_id", ""), payload.get("role", ""))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success(public_user(user), ok=True)


@app.route("/api/admin/set-suspended", methods=["POST"])
def api_admin_set_suspended():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        user = set_user_suspended(payload.get("user_id", ""), bool(payload.get("suspended", False)))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success(public_user(user), ok=True)


@app.route("/api/admin/delete-user", methods=["POST"])
def api_admin_delete_user():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        delete_user_account(payload.get("user_id", ""))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success({}, ok=True)


@app.route("/api/admin/reset-user-password", methods=["POST"])
def api_admin_reset_user_password():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        admin_reset_user_password(payload.get("user_id", ""), payload.get("new_password", ""))
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success({}, ok=True)


@app.route("/api/admin/recalibrate-strategies", methods=["POST"])
def api_admin_recalibrate_strategies():
    """Runs the walk-forward backtest across the scan universe in a
    background thread and measures each named strategy's real historical
    win rate, so strategy_brain's confidence scores can be nudged by
    evidence instead of trusting the hand-tuned formulas blindly. Can take
    several minutes - the route returns immediately, the UI polls
    /api/admin/calibration-status for progress."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    tickers = payload.get("tickers") or CORE_SCAN_UNIVERSE
    try:
        result = start_calibration(
            tickers,
            lookback_months=int(payload.get("lookback_months", 6)),
            hold_days=int(payload.get("hold_days", 5)),
            min_confidence=int(payload.get("min_confidence", 55)),
        )
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success(result, ok=True)


@app.route("/api/admin/calibration-status", methods=["GET"])
def api_admin_calibration_status():
    guard = _require_admin()
    if guard:
        return guard
    return _api_success(get_calibration(), ok=True)


@app.route("/api/admin/global-settings", methods=["GET", "POST"])
def api_admin_global_settings():
    guard = _require_admin()
    if guard:
        return guard
    if request.method == "GET":
        return _api_success(get_global_settings(), ok=True)
    payload = request.get_json(silent=True) or {}
    try:
        settings = update_global_settings(payload)
    except ValueError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success(settings, ok=True)


@app.route("/api/admin/ambiguous-submissions", methods=["GET"])
def api_admin_list_ambiguous_submissions():
    """Every entry, across every user, currently stuck in
    UNKNOWN_SUBMISSION_STATE - the admin-facing view of exactly what's
    freezing autonomous entries for each affected account, and the only
    entry point to _resolve_ambiguous_submission."""
    guard = _require_admin()
    if guard:
        return guard
    pending: List[Dict[str, object]] = []
    for user in list_all_users():
        target_user_id = user.get("id", "")
        if not target_user_id:
            continue
        for order in list_overnight_orders(target_user_id):
            if order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
                pending.append({**order, "user_id": target_user_id, "username": user.get("username", "")})
    return _api_success({"pending": pending}, ok=True, pending=pending)


@app.route("/api/admin/stuck-monitor-entries", methods=["GET"])
def api_admin_list_stuck_monitor_entries():
    """Every entry, across every user, that the fast/continuous monitor has
    made no forward progress on since monitor_first_failure_at was first
    stamped (see _record_monitor_attempt) - includes entries that haven't
    yet crossed MONITOR_STUCK_FREEZE_SECONDS as useful early-warning
    context, each flagged via is_causing_freeze for whether it's actually
    the reason _has_stuck_transitional_orders_locally is currently blocking
    new autonomous entries for that account.

    Found live 2026-08-31: _alert_if_entry_newly_stuck's own alert is
    DELIBERATELY a fixed, no-detail message (ticker + entry id only, no
    error text) so add_manual_alert's content-hash dedup keeps it one-shot
    per entry rather than re-firing (and spamming) every ~10s tick with a
    possibly-different error string - see that function's own docstring.
    That left no admin-visible way to see monitor_last_error, the actual
    reason recovery couldn't be confirmed - the exact same opacity gap
    _resolve_ambiguous_submission's evidence errors already had until they
    were fixed to surface the real per-check error text (see that
    endpoint's own history). This read-only endpoint closes the same gap
    for this different freeze - it does not resolve or unfreeze anything;
    recovery is still only genuine forward progress on the entry itself, or
    direct manual intervention, per _alert_if_entry_newly_stuck's contract."""
    guard = _require_admin()
    if guard:
        return guard
    now = _now_utc()
    stuck: List[Dict[str, object]] = []
    for user in list_all_users():
        target_user_id = user.get("id", "")
        if not target_user_id:
            continue
        for order in list_overnight_orders(target_user_id):
            # Same fix as _has_stuck_transitional_orders_locally (found
            # live 2026-09-01, same day) - monitor_first_failure_at is
            # never cleared by resolving an entry through a different path
            # (e.g. CLOSED via _resolve_position_absent_reconciliation), so
            # without this an entry stays listed here forever after it's
            # actually fully resolved.
            if not ol.is_transitional(order):
                continue
            stuck_since_raw = order.get("monitor_first_failure_at")
            if not stuck_since_raw:
                continue
            stuck_since = _parse_trusted_past_timestamp(stuck_since_raw, now=now, default=now)
            stuck_seconds = (now - stuck_since).total_seconds()
            stuck.append(
                {
                    "user_id": target_user_id,
                    "username": user.get("username", ""),
                    "ticker": order.get("ticker", ""),
                    "entry_client_order_id": order.get("entry_client_order_id", ""),
                    "lifecycle_state": order.get("lifecycle_state", ""),
                    "monitor_first_failure_at": stuck_since_raw,
                    "stuck_seconds": stuck_seconds,
                    "is_causing_freeze": stuck_seconds >= MONITOR_STUCK_FREEZE_SECONDS,
                    "monitor_attempt_count": order.get("monitor_attempt_count", 0),
                    "monitor_last_error": order.get("monitor_last_error"),
                    "monitor_last_attempt_at": order.get("monitor_last_attempt_at"),
                    # order["error"] is a DIFFERENT field than monitor_last_error -
                    # stamped by ol.transition itself (see e.g.
                    # _confirm_and_finalize_protection's own PROTECTION_FAILED
                    # transition), which can fail silently from
                    # monitor_last_error's perspective (no exception raised,
                    # just an internal poll that never confirmed) while still
                    # leaving a real, useful description here.
                    "lifecycle_error": order.get("error"),
                    "stop_client_order_id": order.get("stop_client_order_id"),
                    "stop_leg_quantity": order.get("stop_leg_quantity"),
                    "filled_quantity": order.get("filled_quantity"),
                }
            )
    return _api_success({"stuck": stuck}, ok=True, stuck=stuck)


@app.route("/api/admin/diagnostic/sandbox-accounts", methods=["GET"])
def api_admin_diagnostic_sandbox_accounts():
    """Read-only: the RAW list of every Webull sandbox account these API
    credentials expose, not just the single INDIVIDUAL_CASH one
    find_individual_cash_account picks out for every real trading code
    path in this app. Built to answer a real question live (2026-09-01):
    the entire autonomous PUT/short-entry path is blocked by
    OPENAPI_GENERATE_NEW_SHORT_POSITION because that one account's
    account_class is INDIVIDUAL_CASH - but that says nothing about
    whether these SAME credentials also expose a second, margin-class
    sandbox account this app has simply never looked at (every call site
    filters straight to the cash one). Small and permanent, matching
    order-detail's own precedent, rather than a one-off temporary
    diagnostic - "what accounts do these credentials actually have" is a
    reasonable thing to want to check again later."""
    guard = _require_admin()
    if guard:
        return guard
    target_user_id = str(request.args.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    try:
        accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
        return _api_failure(f"get_paper_accounts failed: {error}", status_code=502, error_code="broker_error", ok=False)
    # Best-effort balance per account - a single account's balance lookup
    # failing (e.g. a FUTURES/CRYPTO account this app's get_account_balance
    # was never written to parse) must not hide the other accounts' real
    # answers, so each failure is captured inline rather than aborting the
    # whole response.
    for account in accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue
        try:
            account["balance"] = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id)
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than hide
            account["balance_error"] = str(error)
    return _api_success({"accounts": accounts, "count": len(accounts)}, ok=True)


@app.route("/api/admin/diagnostic/preview-short-sell", methods=["POST"])
def api_admin_diagnostic_preview_short_sell():
    """TEMPORARY - remove once real short selling on the INDIVIDUAL_MARGIN
    sandbox account is either verified and adopted for real, or ruled
    out (same removal condition as the earlier combo-order diagnostic,
    d5c3c1a/38a0c8b).

    Found live 2026-09-01: these sandbox credentials expose a second,
    INDIVIDUAL_MARGIN account (see /api/admin/diagnostic/sandbox-accounts,
    3bcb336/40f9fe7) with real (paper) buying power, alongside the
    INDIVIDUAL_CASH one every real trading code path uses today - the one
    whose account_class rejected a short-generating order outright with
    OPENAPI_GENERATE_NEW_SHORT_POSITION. Before building the real
    entry/stop/target/monitor/P&L machinery a genuine PUT/short feature
    needs, this answers ONE narrow question with real broker evidence
    instead of an assumption: does Webull's own preview_order endpoint
    (validates the exact request shape and returns accept/reject like a
    real placement would, but never executes an order or touches
    capital - same mechanism already used once this session to rule out
    combo/bracket orders) accept a SELL order for a ticker with no
    existing long position, against the MARGIN account specifically.

    Body: {"symbol": "AAPL", "quantity": 1, "limit_price": 100.0}
    (all optional - sane small defaults below) and optional
    "account_id"/"user_id" overrides for testing against a specific
    account/user other than the live margin account discovered above."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    target_user_id = str(payload.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    account_id = str(payload.get("account_id", "") or "")
    if not account_id:
        try:
            accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
            return _api_failure(f"get_paper_accounts failed: {error}", status_code=502, error_code="broker_error", ok=False)
        margin_account = next((a for a in accounts if a.get("account_class") == "INDIVIDUAL_MARGIN"), None)
        if not margin_account:
            return _api_failure("No INDIVIDUAL_MARGIN account found on this user's sandbox credentials.", status_code=404, error_code="not_found", ok=False)
        account_id = margin_account["account_id"]

    symbol = str(payload.get("symbol", "AAPL") or "AAPL")
    quantity = float(payload.get("quantity", 1) or 1)
    limit_price = float(payload.get("limit_price", 100.0) or 100.0)
    try:
        result = webull_api.preview_stock_order(
            creds["app_key"], creds["app_secret"], account_id,
            symbol=symbol, side="SELL", quantity=quantity, limit_price=limit_price,
        )
    except Exception as error:  # noqa: BLE001 - diagnostic-only, the whole point is seeing what the broker actually said
        return _api_success({"classification": "rejected_or_errored", "detail": str(error), "account_id": account_id}, ok=True)
    return _api_success({"classification": "accepted", "raw": result, "account_id": account_id}, ok=True)


@app.route("/api/admin/diagnostic/preview-raw-order", methods=["POST"])
def api_admin_diagnostic_preview_raw_order():
    """TEMPORARY - same removal condition as preview-short-sell above.
    Verifies order SHAPES this app has never empirically confirmed before
    wiring them into real placement code for the short-selling feature -
    specifically a BUY-side STOP_LOSS (a "buy-stop", used to cover a
    short position on a rise - place_stop_loss_order today only ever
    places SELL-side) and a BUY-side LIMIT (the eventual short target-
    exit cover). Genuinely a dry-run preview (webull_api.preview_raw_order -
    same preview_order endpoint, never executes an order or touches
    capital) against an arbitrary, caller-constructed order dict, so it
    isn't limited to preview_stock_order's fixed LIMIT-only shape.

    Body: {"order": {...}} - the raw order dict to preview (account_id/
    user_id resolve the margin account the same way preview-short-sell
    does, unless "account_id" is also given directly)."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    if not isinstance(order, dict) or not order:
        return _api_failure("order (a non-empty object) is required.", status_code=400, error_code="invalid_request", ok=False)
    target_user_id = str(payload.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    account_id = str(payload.get("account_id", "") or "")
    if not account_id:
        try:
            accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
            return _api_failure(f"get_paper_accounts failed: {error}", status_code=502, error_code="broker_error", ok=False)
        margin_account = next((a for a in accounts if a.get("account_class") == "INDIVIDUAL_MARGIN"), None)
        if not margin_account:
            return _api_failure("No INDIVIDUAL_MARGIN account found on this user's sandbox credentials.", status_code=404, error_code="not_found", ok=False)
        account_id = margin_account["account_id"]

    try:
        result = webull_api.preview_raw_order(creds["app_key"], creds["app_secret"], account_id, order)
    except Exception as error:  # noqa: BLE001 - diagnostic-only, the whole point is seeing what the broker actually said
        return _api_success({"classification": "rejected_or_errored", "detail": str(error), "account_id": account_id}, ok=True)
    return _api_success({"classification": "accepted", "raw": result, "account_id": account_id}, ok=True)


@app.route("/api/admin/diagnostic/option-contracts", methods=["GET"])
def api_admin_diagnostic_option_contracts():
    """Read-only, permanent (same precedent as sandbox-accounts above -
    "what option contracts does the broker actually list for this ticker"
    is a reasonable thing to want to check again later, not a one-off).
    Built 2026-09-03 as the first empirical step of real options trading:
    confirms the REAL response shape of Webull's own option chain
    (DataClient.instrument.get_option_contracts) against the sandbox before
    any contract-selection logic is written to assume particular field
    names exist.

    Query params: ticker (required), option_type (CALL/PUT, optional),
    days_out (expiration window upper bound, default 21), user_id
    (optional, defaults to the caller)."""
    guard = _require_admin()
    if guard:
        return guard
    ticker = str(request.args.get("ticker", "") or "").strip().upper()
    if not ticker:
        return _api_failure("ticker is required.", status_code=400, error_code="invalid_request", ok=False)
    option_type = str(request.args.get("option_type", "") or "").strip().upper() or None
    try:
        days_out = int(request.args.get("days_out", 21))
    except (TypeError, ValueError):
        days_out = 21
    target_user_id = str(request.args.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    start_date = _now_utc().date().isoformat()
    end_date = (_now_utc().date() + timedelta(days=days_out)).isoformat()
    try:
        contracts = webull_api.get_option_contracts(
            creds["app_key"], creds["app_secret"], ticker,
            option_type=option_type, start_date=start_date, end_date=end_date,
        )
    except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
        return _api_failure(f"get_option_contracts failed: {error}", status_code=502, error_code="broker_error", ok=False)
    return _api_success({"ticker": ticker, "start_date": start_date, "end_date": end_date, "contracts": contracts, "count": len(contracts)}, ok=True)


@app.route("/api/admin/diagnostic/option-snapshot", methods=["GET"])
def api_admin_diagnostic_option_snapshot():
    """Read-only, permanent (same precedent as option-contracts above).
    Confirms the real DataClient.option_market_data.get_option_snapshot
    response shape (bid/ask/last field names) against the sandbox before
    autonomy/options_selector.py's liquidity check or any premium-based
    limit pricing assumes a particular key exists.

    Query params: symbols (comma-separated option contract symbols,
    required, e.g. ADBE260918C00420000), user_id (optional)."""
    guard = _require_admin()
    if guard:
        return guard
    symbols_raw = str(request.args.get("symbols", "") or "").strip()
    if not symbols_raw:
        return _api_failure("symbols is required.", status_code=400, error_code="invalid_request", ok=False)
    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]
    target_user_id = str(request.args.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    try:
        snapshot = webull_api.get_option_snapshot(creds["app_key"], creds["app_secret"], symbols)
    except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
        return _api_failure(f"get_option_snapshot failed: {error}", status_code=502, error_code="broker_error", ok=False)
    return _api_success({"symbols": symbols, "snapshot": snapshot}, ok=True)


@app.route("/api/admin/diagnostic/preview-raw-option-order", methods=["POST"])
def api_admin_diagnostic_preview_raw_option_order():
    """TEMPORARY - remove once every order shape real options trading
    needs (BUY_TO_OPEN a call, BUY_TO_OPEN a put, SELL_TO_CLOSE either) has
    been verified and adopted into real placement code, or ruled out - same
    removal condition and same dry-run-only guarantee as
    preview-raw-order above, calling webull_api.preview_raw_option_order
    (order_v2.preview_option) instead of preview_order.

    Body: {"order": {...}} - the raw order dict (including its own "legs")
    to preview. account_id/user_id resolve the margin account the same way
    preview-raw-order does, unless "account_id" is given directly - the
    margin account is the one carrying option_buying_power (confirmed live
    via /api/admin/diagnostic/sandbox-accounts)."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    if not isinstance(order, dict) or not order:
        return _api_failure("order (a non-empty object) is required.", status_code=400, error_code="invalid_request", ok=False)
    target_user_id = str(payload.get("user_id", "") or _current_user_id())
    creds = get_webull_credentials(target_user_id)
    account_id = str(payload.get("account_id", "") or "")
    if not account_id:
        try:
            accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
            return _api_failure(f"get_paper_accounts failed: {error}", status_code=502, error_code="broker_error", ok=False)
        margin_account = next((a for a in accounts if a.get("account_class") == "INDIVIDUAL_MARGIN"), None)
        if not margin_account:
            return _api_failure("No INDIVIDUAL_MARGIN account found on this user's sandbox credentials.", status_code=404, error_code="not_found", ok=False)
        account_id = margin_account["account_id"]

    try:
        result = webull_api.preview_raw_option_order(creds["app_key"], creds["app_secret"], account_id, order)
    except Exception as error:  # noqa: BLE001 - diagnostic-only, the whole point is seeing what the broker actually said
        return _api_success({"classification": "rejected_or_errored", "detail": str(error), "account_id": account_id}, ok=True)
    return _api_success({"classification": "accepted", "raw": result, "account_id": account_id}, ok=True)


@app.route("/api/admin/diagnostic/capital-snapshot", methods=["GET"])
def api_admin_diagnostic_capital_snapshot():
    """Read-only: the three individual broker calls _build_capital_snapshot
    combines (get_account_balance, get_account_positions, get_open_orders)
    for one account, each reported separately with its own success/error -
    not the combined, already-collapsed-to-None result the real scan sees
    when any ONE of them fails. Built live 2026-09-03: the scan's own
    reason text ("available buying power could not be determined -
    refusing to size a trade against stale or missing account data")
    correctly fails closed, but _build_capital_snapshot's own except
    block only ever logs a server-side warning - there was no way to see
    WHICH of the three calls was actually failing, or why, without live
    server/log access this session doesn't have. Permanent, matching
    order-detail's own precedent, not a one-off - "is the broker
    currently answering these three calls cleanly" is a reasonable thing
    to want to check again later."""
    guard = _require_admin()
    if guard:
        return guard
    target_user_id = str(request.args.get("user_id", "") or _current_user_id())
    account_id_param = str(request.args.get("account_id", "") or "")
    creds = get_webull_credentials(target_user_id)
    account_id = account_id_param
    if not account_id:
        try:
            sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
            return _api_failure(f"get_paper_accounts failed: {error}", status_code=502, error_code="broker_error", ok=False)
        cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
        if not cash_account:
            return _api_failure("No Webull sandbox account found for these credentials.", status_code=404, error_code="not_found", ok=False)
        account_id = cash_account["account_id"]

    def _try(label: str, call):
        try:
            return {label: {"ok": True, "result": call()}}
        except Exception as error:  # noqa: BLE001 - the whole point is seeing what actually failed
            return {label: {"ok": False, "error": str(error), "error_type": type(error).__name__}}

    result: Dict[str, object] = {"account_id": account_id}
    result.update(_try("balance", lambda: webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id)))
    result.update(_try("positions", lambda: webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)))
    result.update(_try("open_orders", lambda: webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id)))
    if not result["open_orders"]["ok"]:
        # TEMPORARY (see get_open_orders_raw_first_page's own docstring) -
        # only fetched when the validated call above already failed, to
        # see the actual malformed row without validation getting in the
        # way.
        result.update(_try("open_orders_raw_first_page", lambda: webull_api.get_open_orders_raw_first_page(creds["app_key"], creds["app_secret"], account_id)))
    return _api_success(result, ok=True)


@app.route("/api/admin/diagnostic/order-detail", methods=["GET"])
def api_admin_diagnostic_order_detail():
    """Read-only: the broker's own CURRENT, LIVE answer for one specific
    order (any client_order_id - entry, stop, target, or a target-exit
    sell), for one specific account. Small and permanent, not a
    remove-when-done diagnostic - "what does Webull actually say about
    order X right now" is a question that has come up repeatedly this
    session (the SLB take-profit rejection, the OPENAPI_PARAM_ERR
    "Order not present" classification, and now a stop leg that won't
    confirm as active) and always required either live shell access this
    session doesn't have, or a fresh temporary diagnostic + deploy cycle
    each time. This makes that a standing admin tool instead."""
    guard = _require_admin()
    if guard:
        return guard
    target_user_id = str(request.args.get("user_id", "") or _current_user_id())
    client_order_id = str(request.args.get("client_order_id", "") or "").strip()
    if not client_order_id:
        return _api_failure("client_order_id is required.", status_code=400, error_code="invalid_request", ok=False)

    creds = get_webull_credentials(target_user_id)
    if request.args.get("account_id"):
        account_id = str(request.args["account_id"])
    else:
        try:
            sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
            account_id = webull_api.find_individual_cash_account(sandbox_accounts)["account_id"]
        except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
            return _api_failure(f"Could not resolve the sandbox account: {error}", status_code=502, error_code="broker_error", ok=False)

    try:
        detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, client_order_id)
    except webull_api.DefiniteOrderRejection as error:
        return _api_success({"classification": "definite_rejection", "detail": str(error)}, ok=True)
    except Exception as error:  # noqa: BLE001 - diagnostic-only, report rather than crash
        return _api_failure(f"get_order_detail failed: {error}", status_code=502, error_code="broker_error", ok=False)
    try:
        summary = ol.summarize_fill(detail)
    except Exception as error:  # noqa: BLE001
        summary = {"summarize_fill_error": str(error)}
    return _api_success({"classification": "ok", "summary": summary, "raw": detail}, ok=True)


@app.route("/api/admin/ambiguous-submissions/resolve", methods=["POST"])
def api_admin_resolve_ambiguous_submission():
    """The only route that can move an entry out of UNKNOWN_SUBMISSION_STATE
    by manual admin action - see _resolve_ambiguous_submission for the
    mandatory-fresh-evidence, audit-everything logic this delegates to.
    Never trusts the admin's own claim about what they checked - the fresh
    checks happen server-side, right here, regardless of what the request
    body does or doesn't say about evidence."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        result = _resolve_ambiguous_submission(
            target_user_id=str(payload.get("user_id", "")),
            admin_user_id=_current_user_id(),
            entry_client_order_id=str(payload.get("entry_client_order_id", "")),
            action=str(payload.get("action", "")),
            reason=str(payload.get("reason", "")),
            confirmation=str(payload.get("confirmation", "")),
        )
    except ValidationError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success({"entry": result["entry"]}, ok=True, entry=result["entry"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _signed_money(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):,.2f}"


def _market_session(now_utc: datetime | None = None) -> str:
    eastern = (now_utc or _now_utc()).astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5:
        return "Closed"
    current_minutes = eastern.hour * 60 + eastern.minute
    return "Open" if 570 <= current_minutes < 960 else "Closed"


def _market_phase(now_utc: datetime | None = None) -> str:
    eastern = (now_utc or _now_utc()).astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5:
        return "Closed"
    current_minutes = eastern.hour * 60 + eastern.minute
    if 240 <= current_minutes < 570:
        return "Premarket"
    if 570 <= current_minutes < 960:
        return "Market"
    if 960 <= current_minutes < 1200:
        return "After Hours"
    return "Closed"


def _trading_day_key(now_utc: datetime | None = None) -> str:
    eastern = (now_utc or _now_utc()).astimezone(ZoneInfo("America/New_York"))
    return eastern.strftime("%Y-%m-%d")


def _mission_brief_should_show(settings: Dict[str, object]) -> bool:
    trading_day = _trading_day_key()
    last_viewed = str(settings.get("mission_brief_last_viewed_date", "") or "")
    force_show = bool(settings.get("show_mission_brief_again", False))
    return force_show or last_viewed != trading_day


def _dismiss_mission_brief() -> Dict[str, object]:
    return update_settings(
        _current_user_id(),
        {
            "mission_brief_last_viewed_date": _trading_day_key(),
            "show_mission_brief_again": False,
        },
    )


def _reset_mission_brief() -> Dict[str, object]:
    return update_settings(
        _current_user_id(),
        {
            "mission_brief_last_viewed_date": "",
            "show_mission_brief_again": True,
        },
    )


def _cache_is_fresh(cache: Dict[str, object]) -> bool:
    cache_expiry = cache.get("expires_at")
    return isinstance(cache_expiry, datetime) and cache_expiry > _now_utc()


def _ticker_key(tickers: List[str]) -> Tuple[str, ...]:
    return tuple(sorted({ticker.strip().upper() for ticker in tickers if ticker}))


# Overridable in tests so a "stuck" fetch can be simulated with a short
# sleep instead of a real multi-second one. See test_hard_deadline_degradation.py.
SCAN_MARKET_DEADLINE_SECONDS = 20
TICKER_INTELLIGENCE_DEADLINE_SECONDS = 30
OPTIONS_FETCH_DEADLINE_SECONDS = 30

# Shared, FIXED-size pool for every yfinance-backed background fetch that
# might get abandoned via a hard deadline (scan_market, per-ticker
# strategy/chart/extended-hours, options). Found live 2026-08-25: repeated
# OOM crashes (>2GB) whose timing tracked real scan-activity bursts, not
# any specific code change - traced to _run_with_hard_deadline (and the
# ticker-intelligence/options blocks using the same pattern) each creating
# a BRAND NEW ThreadPoolExecutor per call and abandoning it with
# shutdown(wait=False, cancel_futures=True) on timeout. cancel_futures
# only cancels work that hasn't STARTED yet - Python cannot forcibly kill
# an already-running thread, so under sustained Yahoo rate limiting
# (already known to make individual calls run ~3x their nominal timeout,
# see the comment below) every abandoned call left an orphaned thread
# running in the background indefinitely, with nothing bounding how many
# accumulated across overlapping requests over a gunicorn sync worker's
# entire lifetime. A single shared, bounded pool instead means an
# abandoned task keeps occupying one of a fixed number of slots rather
# than spawning an unbounded number of new OS threads - new work queues
# behind it instead of piling on more. Sized for one request's own worst
# case (6 intelligence tickers + 3 options + 1 spare for a bare
# _run_with_hard_deadline caller like scan_market) so normal traffic isn't
# artificially slowed; the bound only bites once zombies from earlier
# requests are actually occupying slots.
_BACKGROUND_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=10, thread_name_prefix="bg-fetch")


def _run_with_hard_deadline(func, args=(), kwargs=None, deadline_seconds=20, default=None):
    # Bounds a whole yfinance-backed call chain with an outer deadline,
    # because per-call timeout= kwargs alone can't bound it: yfinance's own
    # retry-on-429 logic (venv yfinance/data.py's _make_request) does an
    # UNCONDITIONAL cookie/crumb refetch plus one more request attempt on
    # any 4xx response including a rate limit, each a full network
    # round-trip up to that timeout - under sustained Yahoo rate limiting a
    # single logical call can cost ~3x its nominal timeout. Found live
    # 2026-08-21: gunicorn's own worker timeout (already raised to 90s)
    # still fired during a rate-limit storm despite tightened per-call
    # timeouts. If func hasn't returned within deadline_seconds it's
    # abandoned - its thread keeps running in the background since Python
    # can't forcibly kill a thread, but the caller stops waiting on it.
    # Submits to the shared _BACKGROUND_FETCH_EXECUTOR (see its own
    # docstring) rather than a fresh per-call executor, so an abandoned
    # call occupies one of a bounded number of slots instead of leaking an
    # unbounded new thread every time this fires under sustained rate
    # limiting.
    future = _BACKGROUND_FETCH_EXECUTOR.submit(func, *args, **(kwargs or {}))
    try:
        return future.result(timeout=deadline_seconds)
    except FuturesTimeoutError:
        return default


MACRO_TICKER_LABELS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "DIA": "DIA",
    "^VIX": "VIX",
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "GC=F": "GOLD",
    "CL=F": "OIL",
}
MACRO_TICKER_CACHE: Dict[str, object] = {"rows": [], "expires_at": None}
MACRO_TICKER_CACHE_SECONDS = 60


def get_macro_ticker_tape(force_refresh: bool = False) -> List[Dict[str, object]]:
    if not force_refresh and MACRO_TICKER_CACHE.get("rows") and _cache_is_fresh(MACRO_TICKER_CACHE):
        return MACRO_TICKER_CACHE["rows"]

    rows, _errors, _last_updated = scan_market(tickers=list(MACRO_TICKER_LABELS.keys()))
    for row in rows:
        row["display_label"] = MACRO_TICKER_LABELS.get(row["ticker"], row["ticker"])
    if rows:
        MACRO_TICKER_CACHE.update(
            {"rows": rows, "expires_at": _now_utc() + timedelta(seconds=MACRO_TICKER_CACHE_SECONDS)}
        )
        return rows
    return MACRO_TICKER_CACHE.get("rows", [])


def get_market_data(force_refresh: bool = False) -> Tuple[List[Dict[str, object]], List[str], str]:
    if not force_refresh and MARKET_CACHE.get("rows") and _cache_is_fresh(MARKET_CACHE):
        return MARKET_CACHE["rows"], MARKET_CACHE["errors"], MARKET_CACHE["last_updated"]

    watchlist_tickers = get_watchlist_tickers(_current_user_id())
    scan_universe = list(CORE_SCAN_UNIVERSE)
    rows, errors, last_updated = _run_with_hard_deadline(
        scan_market,
        kwargs={"tickers": scan_universe, "watchlist_tickers": watchlist_tickers},
        deadline_seconds=SCAN_MARKET_DEADLINE_SECONDS,
        default=([], ["Scanner timed out - Yahoo Finance is rate limiting."], _now_utc().isoformat()),
    )
    if not rows and MARKET_CACHE.get("rows"):
        stale_errors = list(MARKET_CACHE.get("errors", [])) + errors
        MARKET_CACHE.update(
            {
                "errors": stale_errors[:8],
                "expires_at": _now_utc() + timedelta(seconds=15),
            }
        )
        return MARKET_CACHE["rows"], MARKET_CACHE["errors"], MARKET_CACHE["last_updated"]

    MARKET_CACHE.update(
        {
            "rows": rows,
            "errors": errors,
            "last_updated": last_updated,
            "expires_at": _now_utc() + timedelta(seconds=CACHE_SECONDS),
        }
    )
    return rows, errors, last_updated


def get_reversal_and_trend_data(
    scanner_rows: List[Dict[str, object]],
    watchlist_tickers: List[str],
    force_refresh: bool = False,
    focus_ticker: str = "",
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[str]]:
    analysis_tickers = [ticker for ticker in watchlist_tickers if ticker in CORE_SCAN_UNIVERSE] or [
        row["ticker"] for row in scanner_rows[:8]
    ]
    if focus_ticker:
        analysis_tickers = [focus_ticker] + [ticker for ticker in analysis_tickers if ticker != focus_ticker]
    ticker_key = _ticker_key(analysis_tickers)
    if (
        not force_refresh
        and ticker_key == ANALYTICS_CACHE.get("ticker_key")
        and ANALYTICS_CACHE.get("reversal_rows")
        and _cache_is_fresh(ANALYTICS_CACHE)
    ):
        return ANALYTICS_CACHE["reversal_rows"], ANALYTICS_CACHE["trend_rows"], ANALYTICS_CACHE["errors"]

    reversal_rows, trend_rows, errors = build_reversal_and_trend_payload(tickers=analysis_tickers, scanner_rows=scanner_rows)
    ANALYTICS_CACHE.update(
        {
            "ticker_key": ticker_key,
            "reversal_rows": reversal_rows,
            "trend_rows": trend_rows,
            "errors": errors,
            "expires_at": _now_utc() + timedelta(seconds=ANALYTICS_CACHE_SECONDS),
        }
    )
    return reversal_rows, trend_rows, errors


def get_news_data(watchlist_tickers: List[str], force_refresh: bool = False, user_id: str = "") -> Tuple[List[Dict[str, object]], List[str]]:
    # Cache key includes user_id - the X provider's results depend on this
    # user's own trusted-accounts list, so without it two users with the same
    # watchlist could be served each other's cached news, including posts
    # sourced from each other's personally-curated trusted accounts.
    ticker_key = _ticker_key(watchlist_tickers)
    if (
        not force_refresh
        and ticker_key == NEWS_CACHE.get("ticker_key")
        and user_id == NEWS_CACHE.get("user_id")
        and NEWS_CACHE.get("rows")
        and _cache_is_fresh(NEWS_CACHE)
    ):
        return NEWS_CACHE["rows"], NEWS_CACHE["errors"]

    bundle = fetch_news_bundle(tickers=watchlist_tickers, limit=30, user_id=user_id)
    rows = bundle.get("items", [])
    errors = bundle.get("errors", [])
    NEWS_CACHE.update(
        {
            "ticker_key": ticker_key,
            "user_id": user_id,
            "rows": rows,
            "errors": errors,
            "expires_at": _now_utc() + timedelta(seconds=NEWS_CACHE_SECONDS),
        }
    )
    return rows, errors


def get_options_data_for_ticker(ticker: str, force_refresh: bool = False) -> Dict[str, object]:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    cached_item = OPTIONS_CACHE.get(normalized_ticker)
    if (
        not force_refresh
        and isinstance(cached_item, dict)
        and isinstance(cached_item.get("expires_at"), datetime)
        and cached_item["expires_at"] > _now_utc()
    ):
        return cached_item["payload"]

    payload = build_options_research(normalized_ticker)
    # A "data unavailable" result usually means Yahoo's flaky options endpoint
    # rate-limited this request, not that the ticker genuinely has no chain -
    # cache it briefly so a reload can retry instead of being stuck showing a
    # stale failure for the full TTL.
    is_unavailable = payload.get("confidence") == 0 and "unavailable" in str(payload.get("reason", "")).lower()
    ttl = 15 if is_unavailable else OPTIONS_CACHE_SECONDS
    OPTIONS_CACHE[normalized_ticker] = {
        "payload": payload,
        "expires_at": _now_utc() + timedelta(seconds=ttl),
    }
    return payload


def get_strategy_data_for_ticker(
    ticker: str, force_refresh: bool = False, extended_hours: Dict[str, object] | None = None
) -> Dict[str, object]:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    cached_item = STRATEGY_CACHE.get(normalized_ticker)
    if (
        not force_refresh
        and isinstance(cached_item, dict)
        and isinstance(cached_item.get("expires_at"), datetime)
        and cached_item["expires_at"] > _now_utc()
    ):
        return cached_item["payload"]

    payload = build_strategy_intelligence(normalized_ticker, extended_hours=extended_hours)
    STRATEGY_CACHE[normalized_ticker] = {
        "payload": payload,
        "expires_at": _now_utc() + timedelta(seconds=STRATEGY_CACHE_SECONDS),
    }
    return payload


def get_chart_levels_for_ticker(
    ticker: str, force_refresh: bool = False, extended_hours: Dict[str, object] | None = None
) -> Dict[str, object]:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    cached_item = CHART_LEVEL_CACHE.get(normalized_ticker)
    if (
        not force_refresh
        and isinstance(cached_item, dict)
        and isinstance(cached_item.get("expires_at"), datetime)
        and cached_item["expires_at"] > _now_utc()
    ):
        return cached_item["payload"]

    payload = build_chart_levels(normalized_ticker, extended_hours=extended_hours)
    CHART_LEVEL_CACHE[normalized_ticker] = {
        "payload": payload,
        "expires_at": _now_utc() + timedelta(seconds=CHART_LEVEL_CACHE_SECONDS),
    }
    return payload


def _build_chart_history_payload(ticker: str) -> Dict[str, object]:
    """Raw daily OHLC candles for the real dashboard chart (2026-09-04) -
    the SAME alpaca_data.get_bars_single call charting_brain.py's
    build_chart_levels already uses internally (real, Alpaca-sourced, not
    Yahoo - see that module's own migration comment), just exposed here as
    plain candles instead of computed levels. A dropped/NaN close (e.g. the
    most recent bar before today's session has fully settled - observed
    live this session) is excluded rather than sent to the frontend as
    NaN, which is not valid JSON and would break JSON.parse on the
    receiving end."""
    try:
        history = alpaca_data.get_bars_single(ticker, period="6mo", interval="1d")
    except Exception as error:  # noqa: BLE001 - a data-fetch failure is a normal, expected outcome for a bad/delisted ticker, not a crash
        return {"ticker": ticker, "candles": [], "error": str(error), "generated_at": _now_utc().isoformat()}
    if history.empty:
        return {"ticker": ticker, "candles": [], "error": "No historical data available.", "generated_at": _now_utc().isoformat()}

    candles: List[Dict[str, object]] = []
    for index, row in history.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        candles.append(
            {
                "date": index.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(close), 2),
                "volume": int(row.get("Volume", 0) or 0),
            }
        )
    return {"ticker": ticker, "candles": candles, "error": "", "generated_at": _now_utc().isoformat()}


def get_chart_history_for_ticker(ticker: str, force_refresh: bool = False) -> Dict[str, object]:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    cached_item = CHART_HISTORY_CACHE.get(normalized_ticker)
    if (
        not force_refresh
        and isinstance(cached_item, dict)
        and isinstance(cached_item.get("expires_at"), datetime)
        and cached_item["expires_at"] > _now_utc()
    ):
        return cached_item["payload"]

    payload = _build_chart_history_payload(normalized_ticker)
    CHART_HISTORY_CACHE[normalized_ticker] = {
        "payload": payload,
        "expires_at": _now_utc() + timedelta(seconds=CHART_HISTORY_CACHE_SECONDS),
    }
    return payload


def _resolve_analysis_tickers(watchlist_tickers: Sequence[str], scanner_rows: Sequence[Dict[str, object]], limit: int = 6) -> List[str]:
    from_watchlist = [ticker.strip().upper() for ticker in watchlist_tickers if ticker]
    from_scanner = [str(row.get("ticker", "")).strip().upper() for row in scanner_rows if row.get("ticker")]
    ordered: List[str] = []
    for ticker in from_watchlist + from_scanner:
        if ticker and ticker not in ordered:
            ordered.append(ticker)
        if len(ordered) >= limit:
            break
    return ordered


def _default_analysis_section_tickers(user_id: str) -> List[str]:
    """The starting-point ticker list for an Analysis section before a user
    has customized it - matches what these pages showed before per-section
    lists existed, so nobody's view goes blank on first load."""
    watchlist_tickers = get_watchlist_tickers(user_id)
    return watchlist_tickers[:6] or CORE_SCAN_UNIVERSE[:6]


# Every open viewer of an Analysis page polls every 5-10s, and each user can
# have their own custom ticker list per section - without a shared cache that
# fans out into a Yahoo Finance call per ticker per viewer per poll, which is
# exactly the kind of load that tripped the sandbox rate limit earlier. Keyed
# by (compute_kind, ticker) rather than by user or section, so two users (or
# two sections, for the reversal/trend pair) watching the same ticker share
# one fetch instead of duplicating it.
ANALYSIS_LIVE_CACHE: Dict[Tuple[str, str], Dict[str, object]] = {}
ANALYSIS_LIVE_CACHE_SECONDS = 8


def _cached_ticker_compute(compute_kind: str, ticker: str, compute_fn):
    key = (compute_kind, ticker)
    cached = ANALYSIS_LIVE_CACHE.get(key)
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("expires_at"), datetime)
        and cached["expires_at"] > _now_utc()
    ):
        return cached["payload"], cached.get("error")
    try:
        payload = compute_fn()
        error = None
    except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't break the whole section
        payload = None
        error = str(exc)
    ANALYSIS_LIVE_CACHE[key] = {
        "payload": payload,
        "error": error,
        "expires_at": _now_utc() + timedelta(seconds=ANALYSIS_LIVE_CACHE_SECONDS),
    }
    return payload, error


def _live_candle_row(ticker: str) -> Tuple[object | None, str | None]:
    return _cached_ticker_compute("candle", ticker, lambda: analyze_candles(ticker))


def _live_pattern_row(ticker: str) -> Tuple[object | None, str | None]:
    return _cached_ticker_compute("pattern", ticker, lambda: analyze_patterns(ticker))


def _live_reversal_and_trend(ticker: str, current_price: float | None) -> Tuple[object | None, str | None]:
    def _compute():
        history = fetch_price_history(ticker=ticker, period="3mo", interval="1d")
        reversal_row = build_reversal_map(ticker=ticker, history=history, current_price=current_price)
        trend_signals = detect_early_trends(
            history=history, support=float(reversal_row["support"]), resistance=float(reversal_row["resistance"])
        )
        return {
            "reversal": reversal_row,
            "trend": {"ticker": ticker, **trend_signals, "last_updated": reversal_row["last_updated"]},
        }

    return _cached_ticker_compute("reversal_trend", ticker, _compute)


ANALYSIS_SECTION_ROW_KEY = {
    "candle_brain": "candle",
    "pattern_brain": "pattern",
    "volume_intelligence": "trend",
    "support_resistance": "reversal",
}


def _build_section_payload(user_id: str, section: str, focus_ticker: str = "") -> Dict[str, object]:
    if section not in ANALYSIS_SECTION_ROW_KEY:
        raise ValidationError(f"Unknown analysis section: {section}")

    default_tickers = _default_analysis_section_tickers(user_id)
    focus_ticker = focus_ticker.strip().upper()
    if focus_ticker:
        tickers = add_focus_ticker(user_id, section, focus_ticker, default_tickers)
    else:
        tickers = get_section_tickers(user_id, section, default_tickers)

    scanner_rows, _, _ = get_market_data(force_refresh=False)
    scanner_price_lookup = {
        str(row.get("ticker", "")).upper(): float(row.get("price", 0) or 0) for row in scanner_rows if row.get("ticker")
    }

    rows: List[object] = []
    errors: List[str] = []

    if tickers:
        with ThreadPoolExecutor(max_workers=len(tickers)) as executor:
            if section == "candle_brain":
                results = executor.map(_live_candle_row, tickers)
            elif section == "pattern_brain":
                results = executor.map(_live_pattern_row, tickers)
            else:
                results = executor.map(
                    lambda ticker: _live_reversal_and_trend(ticker, scanner_price_lookup.get(ticker)), tickers
                )

            row_key = ANALYSIS_SECTION_ROW_KEY[section]
            for ticker, (payload, error) in zip(tickers, results):
                if error:
                    errors.append(f"{ticker}: {error}")
                    continue
                if payload is None:
                    continue
                rows.append(payload[row_key] if section in ("volume_intelligence", "support_resistance") else payload)

    suggestions = build_watchlist_suggestions(scanner_rows=scanner_rows, watchlist_tickers=tickers, limit=6)

    return {
        "section": section,
        "tickers": tickers,
        "max_tickers": MAX_TICKERS_PER_SECTION,
        "rows": rows,
        "errors": errors,
        "suggestions": suggestions,
        "last_updated": _now_utc().isoformat(),
    }


def _broker_framework_status() -> Dict[str, object]:
    etrade = ETradeBroker()
    webull = WebullBroker()
    return {
        "etrade": etrade.get_account_status(),
        "webull": webull.get_account_status(),
        "safety_defaults": {
            "live_trading_enabled": False,
            "options_execution_enabled": False,
            "etrade_execution_enabled": False,
            "webull_paper_mode_only": True,
            "approval_required": True,
            "emergency_kill_switch_placeholder": True,
        },
    }


def _build_neural_snapshot(
    *,
    scanner_rows: List[Dict[str, object]],
    watchlist_rows: List[Dict[str, str]],
    news_rows: List[Dict[str, object]],
) -> Dict[str, object]:
    tickers = [str(row.get("ticker", "")).strip().upper() for row in scanner_rows[:3]]
    tickers = [ticker for ticker in tickers if ticker]

    def _fetch_options(ticker: str) -> Dict[str, object] | None:
        try:
            return get_options_data_for_ticker(ticker)
        except ValueError:
            return None

    option_inputs: List[Dict[str, object]] = []
    if tickers:
        with ThreadPoolExecutor(max_workers=len(tickers)) as executor:
            option_inputs = [payload for payload in executor.map(_fetch_options, tickers) if payload is not None]
    return build_neural_status(
        scanner_rows=scanner_rows,
        watchlist_rows=watchlist_rows,
        news_items=news_rows,
        options_payloads=option_inputs,
    )


def _compute_status(scanner_rows: List[Dict[str, object]], scanner_errors: List[str]) -> Dict[str, object]:
    user_id = _current_user_id()
    settings = get_settings(user_id)
    accounts = get_accounts(user_id)
    top_tickers = [row["ticker"] for row in scanner_rows[:3]]
    confidence = sum(int(row.get("scanner_score", 0)) for row in scanner_rows[:5]) / max(1, len(scanner_rows[:5]))
    reversal_candidates = sum(1 for row in ANALYTICS_CACHE.get("trend_rows", []) if row.get("trend_reversal"))
    hot_count = sum(1 for row in scanner_rows if int(row.get("scanner_score", 0)) >= 75)

    sentiment = "Neutral"
    if scanner_rows:
        avg_change = sum(float(row.get("percent_change", 0)) for row in scanner_rows) / len(scanner_rows)
        if avg_change > 0.75:
            sentiment = "Bullish"
        elif avg_change < -0.75:
            sentiment = "Bearish"

    paper_connected = any(item.get("platform") == "webull" and item.get("status") != "Not Connected" for item in accounts)
    tradingview_status = get_tradingview_status(user_id)
    broker_statuses = _broker_framework_status()
    neural_status = _build_neural_snapshot(
        scanner_rows=scanner_rows,
        watchlist_rows=get_watchlist(user_id),
        news_rows=[],
    )

    phase = _market_phase()
    return {
        "market_status": phase if scanner_rows else "Monitoring",
        "ai_status": "Online",
        "current_time": datetime.now().strftime("%I:%M:%S %p"),
        "scanner_status": "Running" if not scanner_errors else "Degraded",
        "watchlist_status": "Synced",
        "account_status": "Connected" if any(a.get("status") != "Not Connected" for a in accounts) else "Not Connected",
        "market_sentiment": sentiment,
        "ai_confidence": f"{round(confidence)}%",
        "risk_level": "Low" if confidence >= 82 else "Moderate" if confidence >= 68 else "Elevated",
        "risk_score_10": round(max(0.0, min(10.0, 10 - (confidence / 10))), 1),
        "opportunity_count": hot_count,
        "reversal_count": reversal_candidates,
        "watch_today": top_tickers,
        "news_impact": "Moderate",
        "paper_connected": paper_connected,
        "live_trading_enabled": False,
        "settings": settings,
        "latest_alerts": [],
        "api_status": "Operational",
        "system_health": "Healthy" if not scanner_errors else "Degraded",
        "market_phase": phase,
        "broker_statuses": broker_statuses,
        "tradingview_status": tradingview_status,
        "tradingview_latest_alert": tradingview_status.get("latest_alert", {}),
        "neural_status": neural_status,
    }


def _build_page_context(
    *,
    include_suggestions: bool = False,
    include_reversal: bool = False,
    include_trend: bool = False,
    include_news: bool = False,
    include_trusted_accounts: bool = False,
    include_patterns: bool = False,
    include_opportunities: bool = True,
    include_options: bool = True,
    include_market_scan: bool = True,
    force_refresh: bool = False,
    focus_ticker: str = "",
) -> Dict[str, object]:
    focus_ticker = focus_ticker.strip().upper()
    user_id = _current_user_id()
    settings_payload = get_settings(user_id)
    watchlist = get_watchlist(user_id)
    watchlist_tickers = [row["ticker"] for row in watchlist]
    if include_market_scan:
        scanner_rows, scanner_errors, scanner_last_updated = get_market_data(force_refresh=force_refresh)
    else:
        scanner_rows, scanner_errors, scanner_last_updated = [], [], ""
    suggestions = (
        build_watchlist_suggestions(
            scanner_rows=scanner_rows,
            watchlist_tickers=watchlist_tickers,
            limit=8,
            dismissed_tickers=list_dismissed_suggestions(user_id),
        )
        if include_suggestions and settings_payload.get("auto_suggestions_enabled", True)
        else []
    )

    reversal_rows: List[Dict[str, object]] = []
    trend_rows: List[Dict[str, object]] = []
    trend_errors: List[str] = []
    if include_reversal or include_trend:
        reversal_rows, trend_rows, trend_errors = get_reversal_and_trend_data(
            scanner_rows=scanner_rows,
            watchlist_tickers=watchlist_tickers,
            force_refresh=force_refresh,
            focus_ticker=focus_ticker,
        )

    news_rows: List[Dict[str, object]] = []
    news_errors: List[str] = []
    if include_news:
        news_rows, news_errors = get_news_data(watchlist_tickers=watchlist_tickers, force_refresh=force_refresh, user_id=user_id)

    candle_rows: List[Dict[str, object]] = []
    pattern_rows: List[Dict[str, object]] = []
    pattern_errors: List[str] = []
    if include_patterns:
        analysis_tickers = watchlist_tickers[:6] or CORE_SCAN_UNIVERSE[:6]
        if focus_ticker:
            analysis_tickers = [focus_ticker] + [ticker for ticker in analysis_tickers if ticker != focus_ticker]
        ticker_key = _ticker_key(analysis_tickers)
        if (
            not force_refresh
            and ticker_key == CANDLE_CACHE.get("ticker_key")
            and _cache_is_fresh(CANDLE_CACHE)
            and _cache_is_fresh(PATTERN_CACHE)
        ):
            candle_rows = CANDLE_CACHE["rows"]
            pattern_rows = PATTERN_CACHE["rows"]
            pattern_errors = list(CANDLE_CACHE.get("errors", [])) + list(PATTERN_CACHE.get("errors", []))
        else:
            def _fetch_candle_and_pattern(ticker: str) -> Tuple[object | None, object | None, List[str]]:
                errors: List[str] = []
                candle_row = None
                pattern_row = None
                try:
                    candle_row = analyze_candles(ticker)
                except Exception as error:
                    errors.append(f"{ticker} candle: {error}")
                try:
                    pattern_row = analyze_patterns(ticker)
                except Exception as error:
                    errors.append(f"{ticker} pattern: {error}")
                return candle_row, pattern_row, errors

            candle_errors = []
            candle_rows = []
            pattern_rows = []
            with ThreadPoolExecutor(max_workers=max(1, len(analysis_tickers))) as executor:
                for candle_row, pattern_row, errors in executor.map(_fetch_candle_and_pattern, analysis_tickers):
                    if candle_row is not None:
                        candle_rows.append(candle_row)
                    if pattern_row is not None:
                        pattern_rows.append(pattern_row)
                    candle_errors.extend(errors)
            CANDLE_CACHE.update(
                {
                    "ticker_key": ticker_key,
                    "rows": candle_rows,
                    "errors": candle_errors,
                    "expires_at": _now_utc() + timedelta(seconds=PATTERN_CACHE_SECONDS),
                }
            )
            PATTERN_CACHE.update(
                {
                    "ticker_key": ticker_key,
                    "rows": pattern_rows,
                    "errors": candle_errors,
                    "expires_at": _now_utc() + timedelta(seconds=PATTERN_CACHE_SECONDS),
                }
            )
            pattern_errors = candle_errors

    cached_reversal = reversal_rows or (ANALYTICS_CACHE.get("reversal_rows", []) if _cache_is_fresh(ANALYTICS_CACHE) else [])
    cached_trend = trend_rows or (ANALYTICS_CACHE.get("trend_rows", []) if _cache_is_fresh(ANALYTICS_CACHE) else [])
    cached_news = news_rows or (
        NEWS_CACHE.get("rows", []) if _cache_is_fresh(NEWS_CACHE) and NEWS_CACHE.get("user_id") == user_id else []
    )

    # include_opportunities=False skips this entire block - a caller that
    # only wants reversal/trend/news/patterns (api_reversal_map,
    # api_trend_detection) was previously paying for the full
    # strategy+chart+options pipeline below on every call regardless, for
    # data it never even reads out of the returned context.
    intelligence_tickers = _resolve_analysis_tickers(watchlist_tickers, scanner_rows, limit=6) if include_opportunities else []
    scanner_map = {str(row.get("ticker", "")).upper(): row for row in scanner_rows}

    def _fetch_ticker_intelligence(ticker: str) -> Tuple[str, Dict[str, object], Dict[str, object], Dict[str, object]]:
        extended_hours = build_extended_hours_intelligence(ticker)
        strategy = get_strategy_data_for_ticker(ticker=ticker, force_refresh=force_refresh, extended_hours=extended_hours)
        chart = get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh, extended_hours=extended_hours)
        return ticker, extended_hours, strategy, chart

    extended_hours_map: Dict[str, Dict[str, object]] = {}
    strategy_map: Dict[str, Dict[str, object]] = {}
    chart_levels_map: Dict[str, Dict[str, object]] = {}
    if intelligence_tickers:
        # Each ticker's thread chains up to 6 sequential yf.download() calls
        # (this file's extended_hours + strategy + chart, 2 calls each).
        # executor.map() has no way to give up on a straggler without
        # blocking every ticker after it in iteration order, so a single
        # ticker stuck in yfinance's own 429-retry cascade (see
        # _run_with_hard_deadline's comment) could hang this whole stage
        # well past gunicorn's worker timeout. futures_wait() with an
        # overall deadline instead keeps whatever tickers finished in time
        # and abandons the rest - a page with fewer analyzed tickers beats
        # a killed worker. Submits to the shared _BACKGROUND_FETCH_EXECUTOR
        # (see its docstring) rather than a fresh per-call executor - an
        # abandoned ticker occupies one of a bounded number of slots
        # instead of leaking an unbounded new thread every time this
        # deadline fires under sustained rate limiting.
        future_to_ticker = {
            _BACKGROUND_FETCH_EXECUTOR.submit(_fetch_ticker_intelligence, ticker): ticker for ticker in intelligence_tickers
        }
        done, _not_done = futures_wait(future_to_ticker, timeout=TICKER_INTELLIGENCE_DEADLINE_SECONDS)
        for future in done:
            try:
                ticker, extended_hours, strategy, chart = future.result()
            except Exception:
                continue
            extended_hours_map[ticker] = extended_hours
            strategy_map[ticker] = strategy
            chart_levels_map[ticker] = chart

        # Options data alone fires several Yahoo requests per ticker (expiration
        # list + one option_chain() call per expiration) - the heaviest,
        # most rate-limit-risky part of this whole function - and its
        # output (options_expirations/expected_move below) is purely
        # DISPLAY data: nothing in _run_autonomous_trade_scan_locked's own
        # candidate/entry construction ever reads either field, only the
        # confidence/ideal_entry/stop/target that come from strategy/chart
        # instead. include_options=False (used by that scan, and by
        # extension its preview-scan dry_run sibling) skips this burst
        # entirely for a caller that doesn't need it, without changing what
        # a human looking at the dashboard sees. Running all tickers at
        # full concurrency stacks those into a burst large enough to trip
        # Yahoo's rate limiting, so this pool is capped well below the others.
        if include_options:
            options_map = {}
            # Shared _BACKGROUND_FETCH_EXECUTOR again (see its docstring) -
            # same abandoned-thread-leak reasoning as the intelligence pool
            # above, this time for the options fetch specifically.
            options_future_to_ticker = {
                _BACKGROUND_FETCH_EXECUTOR.submit(get_options_data_for_ticker, t, force_refresh=force_refresh): t
                for t in intelligence_tickers
            }
            options_done, _options_not_done = futures_wait(options_future_to_ticker, timeout=OPTIONS_FETCH_DEADLINE_SECONDS)
            for future in options_done:
                ticker = options_future_to_ticker[future]
                try:
                    options_map[ticker] = future.result()
                except Exception:
                    continue
        else:
            options_map = {}
    else:
        options_map = {}

    confidence_floor = int(settings_payload.get("ai_confidence_threshold", 55) or 55)
    upcoming_opportunities: List[Dict[str, object]] = []
    mission_queue: List[Dict[str, object]] = []
    for ticker in intelligence_tickers:
        strategy = strategy_map.get(ticker, {})
        chart = chart_levels_map.get(ticker, {})
        extended_hours = extended_hours_map.get(ticker, {})
        options_payload = options_map.get(ticker, {})
        scanner_row = scanner_map.get(ticker, {})
        if strategy.get("insufficient_data") or chart.get("insufficient_data"):
            continue
        confidence = int(strategy.get("strategy_confidence", 0) or 0)
        if confidence < confidence_floor:
            continue
        breakout_level = float(chart.get("breakout_level", 0) or 0)
        breakdown_level = float(chart.get("breakdown_level", 0) or 0)
        bias = str(strategy.get("recommendation", strategy.get("bias", "WAIT"))).upper()
        support = (chart.get("major_support_levels") or [breakdown_level])[0]
        resistance = (chart.get("major_resistance_levels") or [breakout_level])[0]
        current_price = float(scanner_row.get("price", strategy.get("market_context", {}).get("current_price", 0)) or 0)
        target = round(breakout_level * 1.02, 2) if bias == "CALL" else round(breakdown_level * 0.98, 2)
        stop = round(breakdown_level * 0.997, 2) if bias == "CALL" else round(breakout_level * 1.003, 2)
        trade_quality = "A" if confidence >= 85 else "B" if confidence >= 72 else "C"
        expiration_suggestions = options_payload.get("expiration_suggestions", ["Data unavailable"] * 3)
        upcoming_opportunities.append(
            {
                "ticker": ticker,
                "current_price": current_price,
                "recommendation": bias,
                "confidence": confidence,
                "trade_quality": trade_quality,
                "ideal_entry": round(breakout_level * 1.001, 2) if bias == "CALL" else round(breakdown_level * 0.999, 2),
                "support": support,
                "resistance": resistance,
                "breakout_level": breakout_level,
                "breakdown_level": breakdown_level,
                "target": target,
                "stop": stop,
                "expected_hold_time": strategy.get("expected_hold_time", "Unknown"),
                "expected_move": options_payload.get("expected_move", "Data unavailable"),
                "strategy": strategy.get("best_strategy", strategy.get("recommended_strategy", "Unknown")),
                "trade_thesis": strategy.get("why_this_strategy_fits", "Data unavailable"),
                "bull_case": f"Holds above support {support} and clears breakout {breakout_level}.",
                "bear_case": f"Fails below support {support} and invalidates at {strategy.get('what_invalidates_trade', strategy.get('invalidation_rule', 'n/a'))}.",
                "options_expirations": {
                    "aggressive": expiration_suggestions[0] if len(expiration_suggestions) > 0 else "Data unavailable",
                    "balanced": expiration_suggestions[1] if len(expiration_suggestions) > 1 else "Data unavailable",
                    "conservative": expiration_suggestions[2] if len(expiration_suggestions) > 2 else "Data unavailable",
                },
                "invalidation_rule": strategy.get("invalidation_rule", "Data unavailable"),
                "why_ai_likes_it": strategy.get("why_ai_likes_it", strategy.get("why_this_strategy_fits", "Data unavailable")),
                "risk_warning": strategy.get("risk_warning", "Data unavailable"),
                "risk_level": strategy.get("risk_level", "unknown"),
                "what_invalidates_trade": strategy.get("what_invalidates_trade", strategy.get("invalidation_rule", "n/a")),
                "why_support_matters": strategy.get("why_support_matters", "Data unavailable"),
                "why_resistance_matters": strategy.get("why_resistance_matters", "Data unavailable"),
                "why_volume_matters": strategy.get("why_volume_matters", "Data unavailable"),
                "why_trend_matters": strategy.get("why_trend_matters", "Data unavailable"),
                "why_news_matters": strategy.get("why_news_matters", "Data unavailable"),
                "data_source": strategy.get("data_source", "Data unavailable"),
                "data_quality": strategy.get("data_quality", "Data unavailable"),
                "last_updated": strategy.get("last_updated", strategy.get("generated_at", _now_utc())),
                "live_or_delayed": strategy.get("live_or_delayed", "Delayed"),
                "research_only": strategy.get("research_only", True),
                "disclaimer": strategy.get("disclaimer", "For research only."),
                "extended_hours": extended_hours,
            }
        )
        mission_queue.append(
            {
                "ticker": ticker,
                "waiting_price": round(breakout_level if bias == "CALL" else breakdown_level, 2),
                "strategy": strategy.get("best_strategy", strategy.get("recommended_strategy", "Unknown")),
                "confidence": confidence,
                "reason": strategy.get("why_this_strategy_fits", "Data unavailable"),
                "recommendation": bias,
            }
        )
    upcoming_opportunities.sort(key=lambda item: int(item.get("confidence", 0)), reverse=True)
    mission_queue.sort(key=lambda item: int(item.get("confidence", 0)), reverse=True)
    for idx, queue_item in enumerate(mission_queue[:3], start=1):
        queue_item["priority"] = f"Priority {idx}"

    system_alerts = build_system_alerts(
        suggestions=suggestions,
        scanner_rows=scanner_rows,
        reversal_rows=cached_reversal,
        trend_rows=cached_trend,
        news_rows=cached_news,
        opportunities=upcoming_opportunities,
        market_phase=_market_phase(),
        tradingview_alert=get_tradingview_status(user_id).get("latest_alert", {}),
    )
    live_positions = _get_live_webull_positions(user_id)
    if live_positions.get("connected") and not live_positions.get("error"):
        system_alerts += build_exit_signal_alerts(live_positions.get("positions", []), list_overnight_orders(user_id))
    alerts = get_alerts_snapshot(user_id, system_alerts)

    status_summary = _compute_status(scanner_rows, scanner_errors)
    status_summary["latest_alerts"] = alerts[:6]
    status_summary["neural_status"] = _build_neural_snapshot(
        scanner_rows=scanner_rows,
        watchlist_rows=watchlist,
        news_rows=cached_news,
    )
    status_summary["tradingview_status"] = get_tradingview_status(user_id)
    status_summary["tradingview_latest_alert"] = status_summary["tradingview_status"].get("latest_alert", {})
    status_summary["broker_statuses"] = _broker_framework_status()
    status_summary["autonomy_status"] = get_autonomy_status(user_id)
    status_summary["mission_brief_should_show"] = _mission_brief_should_show(settings_payload)
    status_summary["mission_brief_last_viewed_date"] = settings_payload.get("mission_brief_last_viewed_date", "")
    status_summary["mission_brief_today"] = _trading_day_key()

    context = {
        "watchlist": watchlist,
        "scanner_rows": scanner_rows,
        "scanner_errors": scanner_errors,
        "scanner_last_updated": scanner_last_updated,
        "suggestions": suggestions,
        "reversal_rows": reversal_rows,
        "trend_rows": trend_rows,
        "trend_errors": trend_errors,
        "news_rows": news_rows,
        "news_errors": news_errors,
        "alerts": alerts,
        "unread_alert_count": unread_count(alerts),
        "trusted_accounts": [],
        "status_summary": status_summary,
        "candle_rows": candle_rows,
        "pattern_rows": pattern_rows,
        "pattern_errors": pattern_errors,
        "settings_payload": settings_payload,
        "settings_themes": available_themes(),
        "future_news_roadmap": get_future_news_roadmap(),
        "focus_ticker": focus_ticker,
        "options_tickers": (
            [focus_ticker] + [t for t in sorted(set(watchlist_tickers + [row["ticker"] for row in scanner_rows])) if t != focus_ticker]
            if focus_ticker
            else sorted(set(watchlist_tickers + [row["ticker"] for row in scanner_rows]))
        ),
        "strategy_map": strategy_map,
        "chart_levels_map": chart_levels_map,
        "extended_hours_map": extended_hours_map,
        "upcoming_opportunities": upcoming_opportunities,
        "mission_queue": mission_queue[:3],
        "mission_brief_should_show": status_summary["mission_brief_should_show"],
        "autonomy_status": status_summary["autonomy_status"],
        # Drives the persistent freeze banner in base.html - a LOCAL-only
        # read (no broker calls), computed fresh on every page load,
        # deliberately independent of the alerts/notifications drawer's
        # read/dismissed state. Dismissing a notification must never make
        # this banner (or the actual entry freeze it reflects) go away -
        # only _resolve_ambiguous_submission can do that.
        "has_unresolved_ambiguous_submission": _has_unresolved_ambiguous_submission_locally(user_id),
        # Admin-only, system-wide variant of the same banner - see
        # _count_users_with_unresolved_ambiguous_submissions. Zero for a
        # non-admin viewer, so base.html's admin-wide banner block is a
        # simple truthiness check with no separate role check needed there.
        "admin_frozen_account_count": (
            _count_users_with_unresolved_ambiguous_submissions() if is_admin(user_id) else 0
        ),
        # Admin-only - both heartbeats are single GLOBAL, system-wide
        # resources (see fast_monitor_heartbeat.py / full_scan_heartbeat.py),
        # not per-user ones, so there's nothing meaningful to show a
        # non-admin viewer here. Local-only reads (no broker calls) of the
        # last recorded run for each scheduler.
        "fast_monitor_health": _fast_monitor_health_status() if is_admin(user_id) else None,
        "full_scan_health": _full_scan_health_status() if is_admin(user_id) else None,
    }
    if include_trusted_accounts:
        context["trusted_accounts"] = get_trusted_accounts(user_id)
    return context


@app.context_processor
def inject_nav():
    return {"nav_items": NAV_ITEMS}


@app.context_processor
def inject_current_user():
    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    return {"current_user": public_user(user) if user else None}


@app.route("/")
def mission_briefing_page() -> str:
    context = _build_page_context(
        include_suggestions=True,
        include_reversal=True,
        include_trend=True,
        include_news=True,
        include_trusted_accounts=True,
    )
    if not context.get("mission_brief_should_show") and request.args.get("show_brief") != "1":
        context["macro_ticker_rows"] = get_macro_ticker_tape()
        context["webull_balance"] = _get_live_webull_balance(_current_user_id())
        return render_template("dashboard.html", **context)
    return render_template("mission_briefing.html", **context)


@app.route("/mission-brief")
def mission_brief_manual_page() -> str:
    context = _build_page_context(include_suggestions=True, include_reversal=True, include_trend=True, include_news=True)
    return render_template("mission_briefing.html", **context)


@app.route("/dashboard")
@app.route("/mission-control")
def dashboard_page() -> str:
    context = _build_page_context(
        include_suggestions=True,
        include_reversal=True,
        include_trend=True,
        include_news=True,
        include_trusted_accounts=True,
    )
    context["macro_ticker_rows"] = get_macro_ticker_tape()
    context["accounts"] = get_accounts(_current_user_id())
    context["webull_balance"] = _get_live_webull_balance(_current_user_id())
    return render_template("dashboard.html", **context)


@app.route("/watchlist")
def watchlist_page() -> str:
    return render_template("watchlist.html", **_build_page_context(include_suggestions=True))


@app.route("/market-scanner")
@app.route("/scanner")
def scanner_page() -> str:
    return render_template("scanner.html", **_build_page_context())


@app.route("/reversal-map")
@app.route("/support-resistance")
def reversal_map_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=focus_ticker)
    context["analysis_section"] = "support_resistance"
    return render_template("reversal_map.html", **context)


@app.route("/trend-detection")
@app.route("/volume-scanner")
def trend_detection_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=focus_ticker)
    context["analysis_section"] = "volume_intelligence"
    return render_template("trend_detection.html", **context)


def _build_price_chart(ticker: str) -> Dict[str, object]:
    import yfinance as yf

    try:
        history = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)
    except Exception:
        return {"available": False}

    closes = history["Close"].dropna() if not history.empty and "Close" in history.columns else None
    if closes is None or len(closes) < 2:
        return {"available": False}

    width, height, pad = 640, 160, 12
    min_c, max_c = float(closes.min()), float(closes.max())
    span = (max_c - min_c) or 1.0
    n = len(closes)
    points = []
    for index, value in enumerate(closes):
        x = (index / (n - 1)) * width
        y = height - pad - (((float(value) - min_c) / span) * (height - 2 * pad))
        points.append(f"{x:.1f},{y:.1f}")
    points_str = " ".join(points)
    fill_points = f"0,{height - pad} {points_str} {width},{height - pad}"

    return {
        "available": True,
        "points": points_str,
        "fill_points": fill_points,
        "width": width,
        "height": height,
        "min_price": round(min_c, 2),
        "max_price": round(max_c, 2),
        "trend_up": float(closes.iloc[-1]) >= float(closes.iloc[0]),
    }


@app.route("/lookup")
@app.route("/lookup/<ticker>")
def ticker_lookup_page(ticker: str = "") -> str:
    symbol = (ticker or request.args.get("ticker", "")).strip().upper()
    context = _build_page_context(focus_ticker=symbol)
    context["searched_ticker"] = symbol
    if not symbol:
        return render_template("ticker_lookup.html", **context)

    quote_rows, quote_errors, _ = scan_market(tickers=[symbol])
    extended_hours = build_extended_hours_intelligence(symbol)
    strategy = get_strategy_data_for_ticker(symbol, extended_hours=extended_hours)
    chart_levels = get_chart_levels_for_ticker(symbol, extended_hours=extended_hours)

    try:
        options_payload = get_options_data_for_ticker(symbol)
    except ValueError:
        options_payload = None

    watchlist_tickers = get_watchlist_tickers(_current_user_id())

    context.update(
        {
            "quote": quote_rows[0] if quote_rows else None,
            "quote_errors": quote_errors,
            "strategy": strategy,
            "chart_levels": chart_levels,
            "chart": _build_price_chart(symbol),
            "options_payload": options_payload,
            "already_on_watchlist": symbol in {t.upper() for t in watchlist_tickers},
        }
    )
    return render_template("ticker_lookup.html", **context)


@app.route("/options")
def options_page() -> str:
    searched_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=searched_ticker)
    if searched_ticker:
        context["searched_ticker"] = searched_ticker
    return render_template("options.html", **context)


CHAIN_CACHE: Dict[str, Dict[str, object]] = {}
CHAIN_CACHE_SECONDS = 20


@app.route("/api/options/chain", methods=["GET"])
@api_guard
def api_options_chain():
    ticker = request.args.get("ticker", "").strip().upper()
    expiration = request.args.get("expiration", "").strip()
    if not ticker:
        raise ValidationError("Ticker is required.")

    cache_key = f"{ticker}:{expiration}"
    cached = CHAIN_CACHE.get(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("expires_at"), datetime) and cached["expires_at"] > _now_utc():
        payload = cached["payload"]
    else:
        payload = get_full_option_chain(ticker, expiration)
        CHAIN_CACHE[cache_key] = {"payload": payload, "expires_at": _now_utc() + timedelta(seconds=CHAIN_CACHE_SECONDS)}

    return _api_success(payload, **payload)


@app.route("/news-intelligence")
def news_intelligence_page() -> str:
    return render_template("news_intelligence.html", **_build_page_context(include_news=True))


@app.route("/settings")
def settings_page() -> str:
    # include_market_scan=False, include_opportunities=False - settings.html
    # never renders scanner_rows/opportunities data (confirmed by grepping
    # the template); this page was paying the full market-scan pipeline for
    # nothing every time someone opened Settings.
    return render_template(
        "settings.html",
        **_build_page_context(include_trusted_accounts=True, include_market_scan=False, include_opportunities=False),
    )


@app.route("/account-hub")
def account_hub_page() -> str:
    # include_market_scan=False, include_opportunities=False - Account Hub
    # is entirely account/order data (accounts, scan runs, test orders,
    # Stage 3, mode selector); it never renders scanner_rows or
    # opportunities data (confirmed by grepping the template). Almost
    # certainly the most-visited page in the app, so this was the biggest
    # real-world instance of the same unnecessary market-scan tax fixed on
    # /daily-digest and /performance.
    context = _build_page_context(include_market_scan=False, include_opportunities=False)
    user_id = _current_user_id()
    context["accounts"] = get_accounts(user_id)
    context["webull_configured"] = is_webull_configured(user_id)
    context["anthropic_configured"] = is_anthropic_configured(user_id)
    context["webull_balance"] = _get_live_webull_balance(user_id)
    # Durable per-tick record of whether THIS account was actually
    # scanned/reconciled by the cron-trigger scheduler - see
    # autonomy/scan_run_log.py's module docstring for why "the cron job
    # returned HTTP 200" doesn't by itself answer that question.
    context["scan_runs"] = list_scan_runs(user_id, limit=50)
    # Manual Test Order panel - see api_webull_place_test_order's own
    # docstring for what this tool is and its safety rails.
    context["manual_test_orders"] = [
        order for order in list_overnight_orders(user_id) if order.get("source") == "manual_test_order"
    ]
    # Stage 3 panel - see api_webull_place_stage3_order's own docstring for
    # what this tool is. display_status mirrors the Trade Journal's own
    # Overnight Orders table (_overnight_order_display_status) so a Stage 3
    # entry shows its real live lifecycle state, not a stale "placed" label.
    stage3_orders = [order for order in list_overnight_orders(user_id) if order.get("source") == "stage3_test_order"]
    for order in stage3_orders:
        order["display_status"] = _overnight_order_display_status(order)
        # Whether "Close Position" should be offered - any state with real
        # shares filled and not yet CLOSED. A stale button (the position
        # already closed by some other path) just fails safely with
        # api_close_webull_position's own clear "no open position" error -
        # simpler and more consistent with this app's other best-effort UI
        # than re-fetching live broker positions just for this check.
        order["stage3_closeable"] = order.get("lifecycle_state") in {
            ol.ENTRY_FILLED,
            ol.PROTECTION_PENDING,
            ol.PROTECTION_CONFIRMED_ACTIVE,
            ol.PROTECTION_FAILED,
        }
    context["stage3_orders"] = stage3_orders
    return render_template("account_hub.html", **context)


@app.route("/notifications")
def notifications_page() -> str:
    # notifications.html only renders `alerts` (always computed regardless
    # of these flags) - it never reads suggestions or news_rows, so both of
    # those True flags were dead weight, and the market-scan/opportunities
    # pipeline (on by default) was pure unused cost on top of that. alerts
    # itself is ephemeral per-request (not persisted/accumulated - see
    # get_alerts_snapshot), so skipping scanner-derived alert types here
    # only affects this one page's badge count, not what mission-control
    # (which still computes the full pipeline) shows.
    return render_template(
        "notifications.html", **_build_page_context(include_market_scan=False, include_opportunities=False)
    )


# The Overnight Orders table used to render order["status"] directly - a
# field set exactly ONCE at initial placement ("placed"/"failed"/"skipped"/
# "unknown_submission_state") and never updated again anywhere in the
# reconciliation code. Meanwhile order["lifecycle_state"] IS mutated live
# by _monitor_transitional_orders/_reconcile_entry_fill_and_protection/
# _reconcile_position_exit as the order actually fills, gets protected,
# and closes - but nothing ever showed it, so a user watching that table
# would see "placed" forever even for an order that filled, was protected,
# and later closed hours ago. This maps the real, current lifecycle_state
# to a human label for display - it does NOT touch order["status"] or
# order["lifecycle_state"] themselves, which other code (including this
# same route's own "todays_orders" filter below) still reads unchanged.
_LIFECYCLE_STATE_DISPLAY_LABELS = {
    ol.ENTRY_SUBMITTED: "Order submitted",
    ol.ENTRY_PARTIALLY_FILLED: "Partially filled",
    ol.ENTRY_FILLED: "Filled - protecting",
    ol.PROTECTION_PENDING: "Placing protection",
    ol.PROTECTION_CONFIRMED_ACTIVE: "Filled & protected",
    ol.PROTECTION_FAILED: "Protection failed",
    ol.CLOSED: "Closed",
    ol.ENTRY_FAILED: "Failed",
    ol.UNKNOWN_SUBMISSION_STATE: "Ambiguous - reconciling",
    ol.MANUALLY_RESOLVED_NO_ORDER: "Manually resolved",
    ol.MANUAL_LINK_IN_PROGRESS: "Manual resolution in progress",
}


def _overnight_order_display_status(order: Dict[str, object]) -> str:
    """What the Overnight Orders table's Status column should actually
    show for one order - see the module comment above this function for
    why order["status"] alone is stale. Ambiguous-exit and protection-gap
    freezes (see _flag_ambiguous_exit_unresolved /
    _reconcile_protective_leg_quantity) take priority over the raw
    lifecycle_state label even though PROTECTION_CONFIRMED_ACTIVE would
    otherwise map to "Filled & protected" - a frozen, needs-manual-review
    position must never display as if everything is fine."""
    if order.get("ambiguous_exit_unresolved"):
        return "Needs manual review (ambiguous exit)"
    if order.get("stop_protection_gap") or order.get("target_protection_gap"):
        return "Needs manual review (protection gap)"
    lifecycle_state = order.get("lifecycle_state")
    if lifecycle_state:
        return _LIFECYCLE_STATE_DISPLAY_LABELS.get(str(lifecycle_state), str(lifecycle_state))
    # No lifecycle_state at all means this candidate never reached order
    # submission (skipped below the confidence floor, sizing rejected it,
    # the LLM step vetoed it, etc.) - order["status"]/["reason_skipped"]
    # are the right, and only, fields for those - unchanged.
    return str(order.get("status") or "unknown")


@app.route("/trade-journal")
def trade_journal_page() -> str:
    # trade_journal.html never renders reversal_rows/trend_rows/
    # scanner_rows/opportunities data (confirmed by grepping the template) -
    # this page's own content is entirely paper_trades/overnight_orders/
    # closed_trades/webull_positions, all fetched below. Almost certainly
    # one of the most-visited pages in the app, so include_reversal/
    # include_trend here were pure unused cost, on top of the
    # market-scan/opportunities pipeline that's on by default.
    context = _build_page_context(include_market_scan=False, include_opportunities=False)
    user_id = _current_user_id()
    context["paper_trades"] = list_paper_trades(user_id)
    context["paper_trade_summary"] = get_paper_trade_summary(user_id)
    overnight_orders = list_overnight_orders(user_id)
    for order in overnight_orders:
        order["display_status"] = _overnight_order_display_status(order)
    context["overnight_orders"] = overnight_orders
    # Durably closed autonomous trades - realized P&L and exit details as
    # recorded by _reconcile_position_exit once CLOSED, not derived from
    # overnight_orders/webull_positions here. See autonomy/closed_trades.py.
    context["closed_trades"] = list_closed_trades(user_id)
    webull_positions = _get_live_webull_positions(user_id)
    if webull_positions.get("connected") and not webull_positions.get("error"):
        webull_positions = {
            **webull_positions,
            "positions": annotate_positions_with_exit_signal(webull_positions["positions"], overnight_orders),
        }
    context["webull_positions"] = webull_positions
    context["webull_balance"] = _get_live_webull_balance(user_id)

    today_key = _trading_day_key()

    def _order_trading_day(order: Dict[str, object]) -> str:
        try:
            return _trading_day_key(datetime.fromisoformat(str(order.get("logged_at", ""))))
        except ValueError:
            return ""

    todays_orders = [order for order in overnight_orders if order.get("status") == "placed" and _order_trading_day(order) == today_key]
    context["webull_today_summary"] = {
        "entries_today": sum(1 for order in todays_orders if order.get("side") == "BUY"),
        "closed_today": sum(1 for order in todays_orders if order.get("side") == "SELL"),
        "open_count": len(webull_positions.get("positions", [])) if webull_positions.get("connected") else 0,
    }
    return render_template("trade_journal.html", **context)


@app.route("/performance")
def performance_page() -> str:
    """Tier 1 of the "make autonomy learn" roadmap - human-readable
    performance reporting only (see autonomy/performance_report.py's own
    module docstring for why this is deliberately NOT automated behavior
    change). include_opportunities=False since this page never needs
    candidate/scan data, only the account's own closed-trade history.
    include_market_scan=False too - the CORE_SCAN_UNIVERSE fetch inside
    get_market_data was still running unconditionally even with every
    other include_* flag off, up to its own 20s hard deadline, entirely
    for scanner_rows this page never reads. Only the shared top-nav
    status widget is derived from it, which already degrades safely to
    "Monitoring"/"Healthy" on an empty scanner_rows list - the same state
    a real rate-limited scan already produces today."""
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    context["performance_report"] = build_performance_report(_current_user_id())
    return render_template("performance.html", **context)


@app.route("/daily-digest")
def daily_digest_page() -> str:
    """A single "what happened, what needs me" summary - the legitimate
    version of a "Chief of Staff" triage layer: read-only, pulled from data
    this app already records (scan_run_log.py, overnight_orders.py,
    closed_trades.py). See autonomy/daily_digest.py's own module docstring.
    include_opportunities=False for the same reason performance_page uses
    it - this page never needs candidate/scan data, only the account's own
    recorded history. include_market_scan=False for the same reason it was
    just added to performance_page: get_market_data ran unconditionally
    regardless of include_opportunities, paying up to its own 20s hard
    deadline for scanner_rows this page never reads - confirmed live, a
    real request to this page took ~21s for exactly that reason before
    this fix."""
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    context["daily_digest"] = build_daily_digest(_current_user_id(), monitor_heartbeat=_monitor_heartbeat_snapshot_for_scan_run())
    return render_template("daily_digest.html", **context)


@app.route("/api/trade-journal/refresh-positions", methods=["POST"])
@api_guard
def api_refresh_webull_positions():
    payload = _get_live_webull_positions(_current_user_id(), force_refresh=True)
    return _api_success(payload, **payload)


@app.route("/api/trade-journal/close-position", methods=["POST"])
@api_guard
def api_close_webull_position():
    """Sells a full open Webull sandbox position at its current last price
    (a marketable limit order) - the counterpart to the Take Profit/Stop
    Loss badges, which were previously just a static label with no action
    behind them."""
    payload = request.get_json(silent=True) or {}
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValidationError("Ticker is required.")

    user_id = _current_user_id()
    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("Connect Webull in Account Hub before closing a position.")

    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub first.")

    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for these credentials.")
    account_id = cash_account["account_id"]

    positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    position = next((p for p in positions if str(p.get("symbol", "")).upper() == ticker), None)
    if not position:
        raise ValidationError(f"No open position found for {ticker}.")

    quantity = float(position.get("quantity", 0) or 0)
    limit_price = float(position.get("last_price", 0) or 0)
    if quantity <= 0 or limit_price <= 0:
        raise ValidationError(f"Invalid quantity/price for {ticker}, cannot close.")

    # Cancel any resting protective stop-loss and/or take-profit order(s) for
    # this ticker before selling manually - otherwise a stale leg could later
    # fire against shares that no longer exist, which risks an accidental
    # short position.
    cancelled_exit_order_ids = []
    for exit_order in pop_exit_orders(user_id, ticker):
        try:
            webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, exit_order["id"])
            cancelled_exit_order_ids.append(exit_order["id"])
        except Exception:  # noqa: BLE001 - likely already filled/expired, don't block the manual close over it
            pass

    entry = {
        "ticker": ticker,
        "side": "SELL",
        "quantity": quantity,
        "limit_price": limit_price,
        "cancelled_exit_order_ids": cancelled_exit_order_ids,
        "reason": "Manual close from Trade Journal",
        "account_id": account_id,
        "status": "pending",
    }
    try:
        result = webull_api.place_stock_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            side="SELL",
            quantity=quantity,
            limit_price=limit_price,
            trading_session=_current_webull_trading_session(),
        )
        entry["status"] = "placed"
        entry["webull_response"] = result
    except Exception as error:  # noqa: BLE001 - surface the failure, don't crash the request
        entry["status"] = "failed"
        entry["error"] = str(error)
        record_overnight_order(user_id, entry)
        raise ValidationError(f"Failed to close {ticker}: {error}") from error

    record_overnight_order(user_id, entry)
    POSITIONS_CACHE.pop(user_id, None)
    BALANCE_CACHE.pop(user_id, None)
    return _api_success(entry, **entry)


# Built for staged sandbox validation ("Stage 2: one tiny entry and
# zero-fill cancellation" - see conversation history) and left as a
# permanent, reusable, audited tool per explicit user request, rather
# than a one-off script - so it's deliberately narrow, not a general
# "place any order" capability. See api_webull_place_test_order's own
# docstring for the full safety-rail reasoning.
MANUAL_TEST_ORDER_MAX_QUANTITY = 5
MANUAL_TEST_ORDER_MIN_DISCOUNT_FROM_MARKET = 0.20


@app.route("/api/webull/place-test-order", methods=["POST"])
@api_guard
def api_webull_place_test_order():
    """Places ONE small, manually-triggered BUY limit order against the
    caller's own real Webull sandbox account.

    Deliberately narrow and safety-railed, not a general "place any
    order" capability:
      - hardcoded side=BUY (this app is long/CALL-only everywhere else
        too - see brains/strategy_brain.py);
      - quantity capped at MANUAL_TEST_ORDER_MAX_QUANTITY;
      - limit_price is REJECTED server-side (never just trusted from the
        client) unless it's at least MANUAL_TEST_ORDER_MIN_DISCOUNT_FROM_MARKET
        below the CURRENT live market price - this is what makes
        "cannot plausibly fill" a structural guarantee of using this
        endpoint, not merely an instruction to whoever calls it;
      - never places a stop/target/bracket - entry only. Protection is a
        separate, later concern, not this tool's job;
      - durably recorded via record_overnight_order with
        source="manual_test_order", visible in the existing Trade
        Journal Overnight Orders table like everything else, and so
        api_webull_cancel_test_order below can verify a given
        client_order_id actually came from THIS tool before touching it."""
    payload = request.get_json(silent=True) or {}
    ticker = str(payload.get("ticker", "")).strip().upper()
    quantity = payload.get("quantity")
    limit_price = payload.get("limit_price")

    if not ticker:
        raise ValidationError("Ticker is required.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError("Quantity must be a whole number.")
    if not (0 < quantity <= MANUAL_TEST_ORDER_MAX_QUANTITY):
        raise ValidationError(f"Quantity must be between 1 and {MANUAL_TEST_ORDER_MAX_QUANTITY} shares for a manual test order.")
    try:
        limit_price = float(limit_price)
    except (TypeError, ValueError):
        raise ValidationError("Limit price must be a number.")
    if limit_price <= 0:
        raise ValidationError("Limit price must be positive.")

    user_id = _current_user_id()
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub first.")

    quote_rows, _quote_errors, _last_updated = scan_market(tickers=[ticker])
    quote_row = quote_rows[0] if quote_rows else None
    if not quote_row or not quote_row.get("price"):
        raise ValidationError(
            f"Could not fetch a current market price for {ticker} - refusing to place a test order without one "
            f"to validate the limit price against."
        )
    market_price = float(quote_row["price"])
    max_allowed_limit_price = round(market_price * (1 - MANUAL_TEST_ORDER_MIN_DISCOUNT_FROM_MARKET), 2)
    if limit_price > max_allowed_limit_price:
        raise ValidationError(
            f"Limit price ${limit_price:,.2f} is too close to the current market price (${market_price:,.2f}) for "
            f"a manual test order - must be at least {int(MANUAL_TEST_ORDER_MIN_DISCOUNT_FROM_MARKET * 100)}% below "
            f"market (${max_allowed_limit_price:,.2f} or lower) so it cannot plausibly fill."
        )

    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for these credentials.")
    account_id = cash_account["account_id"]

    client_order_id = f"manualtest{uuid.uuid4().hex[:24]}"
    entry = {
        "ticker": ticker,
        "side": "BUY",
        "quantity": quantity,
        "limit_price": limit_price,
        "market_price_at_placement": market_price,
        "account_id": account_id,
        "status": "pending",
        "source": "manual_test_order",
        "entry_client_order_id": client_order_id,
    }
    try:
        result = webull_api.place_stock_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            side="BUY",
            quantity=quantity,
            limit_price=limit_price,
            trading_session=_current_webull_trading_session(),
            client_order_id=client_order_id,
        )
        entry["status"] = "placed"
        entry["webull_response"] = result
    except Exception as error:  # noqa: BLE001 - surface the failure, don't crash the request
        entry["status"] = "failed"
        entry["error"] = str(error)
        record_overnight_order(user_id, entry)
        raise ValidationError(f"Failed to place test order for {ticker}: {error}") from error

    record_overnight_order(user_id, entry)
    return _api_success(entry, **entry)


@app.route("/api/webull/cancel-test-order", methods=["POST"])
@api_guard
def api_webull_cancel_test_order():
    """Cancels a resting order previously placed by
    api_webull_place_test_order above, and confirms via a FRESH broker
    read that it actually ended with zero shares filled - "zero-fill
    cancellation" verified, not assumed. Refuses to touch any order this
    endpoint didn't itself create (source != "manual_test_order") - this
    is deliberately NOT a general-purpose "cancel any order" capability."""
    payload = request.get_json(silent=True) or {}
    client_order_id = str(payload.get("client_order_id", "")).strip()
    if not client_order_id:
        raise ValidationError("client_order_id is required.")

    user_id = _current_user_id()
    orders = list_overnight_orders(user_id)
    record = next((o for o in orders if o.get("entry_client_order_id") == client_order_id), None)
    if not record or record.get("source") != "manual_test_order":
        raise ValidationError("No manual test order found with that client_order_id for this account.")

    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub first.")
    account_id = record.get("account_id")
    if not account_id:
        raise ValidationError("This test order has no recorded account_id - cannot look it up at the broker.")

    try:
        webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, client_order_id)
    except Exception as error:  # noqa: BLE001 - surface the failure, don't crash the request
        raise ValidationError(f"Failed to cancel test order {client_order_id}: {error}") from error

    order_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, client_order_id)
    fill = ol.summarize_fill(order_detail)

    record["status"] = "cancelled"
    record["cancel_confirmed_status"] = fill["status"]
    record["cancel_confirmed_filled_quantity"] = fill["filled_quantity"]
    replace_overnight_orders(user_id, orders)

    result = {
        "client_order_id": client_order_id,
        "broker_status": fill["status"],
        "filled_quantity": fill["filled_quantity"],
        "zero_fill_confirmed": fill["filled_quantity"] == 0,
    }
    return _api_success(result, **result)


# Stage 3 of the staged sandbox validation plan (see conversation history):
# ONE real, genuinely fillable share, with REAL stop-loss/take-profit
# protection placed and confirmed - the opposite of Stage 2 above, which
# deliberately guarantees a NON-fill. Where Stage 2 proves placement/
# cancellation works, Stage 3 proves the full entry -> fill -> protection
# pipeline works against a real broker fill, using the EXACT SAME
# _submit_and_protect_entry / _reconcile_entry_fill_and_protection path the
# autonomous scan itself uses - not a simplified reimplementation that could
# silently diverge from production behavior. This is a mechanics test, not a
# strategy test: stop/target come from the caller, not the AI.
STAGE3_ENTRY_QUANTITY = 1
STAGE3_MARKETABLE_PREMIUM_ABOVE_MARKET = 0.005


@app.route("/api/webull/place-stage3-order", methods=["POST"])
@api_guard
def api_webull_place_stage3_order():
    """Places STAGE3_ENTRY_QUANTITY (1) real, genuinely fillable BUY share
    against the caller's own real Webull sandbox account, then drives it
    through real stop-loss/take-profit placement and confirmation.

    Deliberately narrow, same spirit as api_webull_place_test_order (Stage
    2) above:
      - hardcoded side=BUY, hardcoded quantity=STAGE3_ENTRY_QUANTITY -
        "one real share", literally, no caller override;
      - the entry limit price is computed server-side from a fresh market
        quote, STAGE3_MARKETABLE_PREMIUM_ABOVE_MARKET above the current
        price so it's genuinely marketable - the opposite of Stage 2's
        deliberate below-market discount - never trusted from the client;
      - stop_price/target_price are supplied by the caller and required:
        this is a CONTROLLED test of whether the mechanism correctly
        protects whatever levels are given, not a strategy/sizing test,
        so unlike Stage 2 (entry only, no protection at all) both legs are
        mandatory here. Validated server-side for sane long-position
        ordering (stop < entry < target);
      - restricted to CORE trading hours for the same reason
        _new_entries_allowed gates the autonomous scan: place_stop_loss_order
        only accepts CORE, and this whole tool exists to prove real
        protection lands, so running it when a real stop can't even be
        placed would test the wrong thing;
      - held under the same per-user scan lock (scan_lock.py) as the
        autonomous scan, so this can't race a concurrent autonomous entry
        or a double-click of this same button - ScanAlreadyRunningError
        surfaces as a clean 409 via api_guard, same as the manual "Run
        Scan" button;
      - reuses _submit_and_protect_entry unmodified - the exact same
        placement, fill-polling, protection-sizing, and
        protection-confirmation path production trading uses;
      - recorded via record_overnight_order with source="stage3_test_order"
        (distinct from Stage 2's "manual_test_order"), visible in Account
        Hub's own Stage 3 panel and the regular Trade Journal;
      - "controlled exit" deliberately reuses the EXISTING, already-generic
        api_close_webull_position endpoint above rather than a new one -
        it already looks up the real broker position by ticker, cancels
        resting protective legs via pop_exit_orders (which
        _reconcile_protective_leg_quantity already records both legs into
        via record_exit_order), and sells at the current price. No new
        exit code path needed, and no risk of it silently diverging from
        the exit path every other position in this app already uses."""
    payload = request.get_json(silent=True) or {}
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValidationError("Ticker is required.")
    try:
        stop_price = float(payload.get("stop_price"))
    except (TypeError, ValueError):
        raise ValidationError("Stop price must be a number.")
    try:
        target_price = float(payload.get("target_price"))
    except (TypeError, ValueError):
        raise ValidationError("Target price must be a number.")
    if stop_price <= 0 or target_price <= 0:
        raise ValidationError(
            "Stop price and target price must both be positive - Stage 3 proves REAL protection, so both legs "
            "are required, unlike Stage 2's entry-only test."
        )

    if not _new_entries_allowed(_current_webull_trading_session()):
        raise ValidationError(
            "Stage 3 requires CORE trading hours - place_stop_loss_order only accepts CORE, and this tool exists "
            "to prove real protection actually lands, so it refuses to run when that couldn't happen anyway."
        )

    user_id = _current_user_id()
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub first.")

    quote_rows, _quote_errors, _last_updated = scan_market(tickers=[ticker])
    quote_row = quote_rows[0] if quote_rows else None
    if not quote_row or not quote_row.get("price"):
        raise ValidationError(
            f"Could not fetch a current market price for {ticker} - refusing to place a Stage 3 order without one."
        )
    market_price = float(quote_row["price"])
    limit_price = round(market_price * (1 + STAGE3_MARKETABLE_PREMIUM_ABOVE_MARKET), 2)

    if not (stop_price < limit_price < target_price):
        raise ValidationError(
            f"Stop price (${stop_price:,.2f}) must be below the entry price (${limit_price:,.2f}, computed "
            f"{STAGE3_MARKETABLE_PREMIUM_ABOVE_MARKET * 100:g}% above the current market price of "
            f"${market_price:,.2f}), which must be below the target price (${target_price:,.2f})."
        )

    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for these credentials.")
    account_id = cash_account["account_id"]

    trading_day = _trading_day_key()
    entry: Dict[str, object] = {
        "ticker": ticker,
        "side": "BUY",
        "quantity": STAGE3_ENTRY_QUANTITY,
        "limit_price": limit_price,
        "stop": stop_price,
        "target": target_price,
        "market_price_at_placement": market_price,
        "account_id": account_id,
        "status": "pending",
        "source": "stage3_test_order",
        "trading_day": trading_day,
    }

    with user_scan_lock(user_id):
        _submit_and_protect_entry(
            user_id=user_id,
            creds=creds,
            account_id=account_id,
            ticker=ticker,
            requested_quantity=STAGE3_ENTRY_QUANTITY,
            limit_price=limit_price,
            stop_price=stop_price,
            target_price=target_price,
            trading_day=trading_day,
            entry=entry,
        )

    # Same three-way status derivation _run_autonomous_trade_scan_locked
    # uses after calling _submit_and_protect_entry - see its own comment
    # for why UNKNOWN_SUBMISSION_STATE is a deliberately distinct outcome
    # from both "placed" and "failed".
    lifecycle_state = entry.get("lifecycle_state")
    if lifecycle_state == ol.UNKNOWN_SUBMISSION_STATE:
        entry["status"] = "unknown_submission_state"
        entry["error"] = entry.get("error", "order submission result could not be confirmed (ambiguous broker response)")
    else:
        entry["status"] = "failed" if lifecycle_state == ol.ENTRY_FAILED else "placed"
        if entry["status"] == "failed":
            entry["error"] = entry.get("error", "entry order failed")

    record_overnight_order(user_id, entry)
    POSITIONS_CACHE.pop(user_id, None)
    BALANCE_CACHE.pop(user_id, None)

    if entry["status"] == "failed":
        raise ValidationError(f"Stage 3 entry for {ticker} failed: {entry.get('error')}")

    entry["display_status"] = _overnight_order_display_status(entry)
    return _api_success(entry, **entry)


@app.route("/candle-brain")
def candle_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    # include_market_scan=False, include_opportunities=False - candle_brain.html
    # renders no server-side candle_rows/pattern_rows/scanner_rows/
    # opportunities data at all (it's fetched client-side by ticker); this
    # page was paying the full market-scan pipeline for nothing.
    context = _build_page_context(focus_ticker=focus_ticker, include_market_scan=False, include_opportunities=False)
    context["analysis_section"] = "candle_brain"
    return render_template("candle_brain.html", **context)


@app.route("/pattern-brain")
def pattern_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    # include_market_scan=False, include_opportunities=False - same reason
    # as candle_brain_page: this page renders no server-side scanner/
    # opportunities data.
    context = _build_page_context(focus_ticker=focus_ticker, include_market_scan=False, include_opportunities=False)
    context["analysis_section"] = "pattern_brain"
    return render_template("pattern_brain.html", **context)


@app.route("/neural-engine")
def neural_engine_page() -> str:
    # neural_engine.html only reads status_summary (always computed
    # regardless of these flags, and already degrades safely to
    # "Monitoring"/"Healthy" on empty scanner data) - it never renders
    # scanner_rows/opportunities directly.
    return render_template(
        "neural_engine.html", **_build_page_context(include_market_scan=False, include_opportunities=False)
    )


@app.route("/backtest")
def backtest_page() -> str:
    # backtest.html is fully client-side/JS-driven - it renders no
    # server-side scanner/opportunities context at all.
    return render_template("backtest.html", **_build_page_context(include_market_scan=False, include_opportunities=False))


@app.route("/api/backtest/run", methods=["POST"])
@api_guard
def api_backtest_run():
    payload = request.get_json(silent=True) or {}
    tickers = payload.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [part.strip() for part in tickers.split(",")]
    try:
        result = run_backtest(
            tickers=tickers,
            lookback_months=payload.get("lookback_months", 6),
            hold_days=payload.get("hold_days", 5),
            min_confidence=payload.get("min_confidence", 55),
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return _api_success(result, **result, ok=True)


@app.route("/api/watchlist", methods=["GET"])
@api_guard
def api_watchlist():
    rows = get_watchlist(_current_user_id())
    try:
        rows = search_watchlist(
            rows=rows,
            query=request.args.get("q", ""),
            category=request.args.get("category", ""),
            status=request.args.get("status", ""),
            min_score=request.args.get("min_score", ""),
            max_score=request.args.get("max_score", ""),
        )
        rows = sort_watchlist(
            rows=rows,
            sort_by=request.args.get("sort_by", "ticker"),
            direction=request.args.get("direction", "asc"),
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return _api_success({"watchlist": rows, "count": len(rows)}, watchlist=rows, count=len(rows), ok=True)


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    payload = request.get_json(silent=True) or {}
    try:
        row = add_stock(_current_user_id(), payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})


@app.route("/api/watchlist/dismiss-suggestion", methods=["POST"])
def api_watchlist_dismiss_suggestion():
    payload = request.get_json(silent=True) or {}
    try:
        dismiss_suggestion(_current_user_id(), payload.get("ticker", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})


@app.route("/api/watchlist/update", methods=["POST"])
def api_watchlist_update():
    payload = request.get_json(silent=True) or {}
    ticker = payload.get("ticker", "")
    try:
        row = update_stock(_current_user_id(), ticker=ticker, payload=payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})


@app.route("/api/watchlist/delete", methods=["POST"])
def api_watchlist_delete():
    payload = request.get_json(silent=True) or {}
    ticker = payload.get("ticker", "")
    try:
        delete_stock(_current_user_id(), ticker=ticker)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})


@app.route("/api/paper-trade/list", methods=["GET"])
@api_guard
def api_paper_trade_list():
    user_id = _current_user_id()
    trades = list_paper_trades(user_id)
    summary = get_paper_trade_summary(user_id)
    return _api_success({"trades": trades, "summary": summary}, trades=trades, summary=summary, ok=True)


@app.route("/api/paper-trade/execute", methods=["POST"])
@api_guard
def api_paper_trade_execute():
    payload = request.get_json(silent=True) or {}
    if not get_settings(_current_user_id()).get("paper_trading_enabled", True):
        raise ValidationError("Paper trading is disabled in Settings. Enable it to execute a paper trade.")
    try:
        trade = open_paper_trade(
            user_id=_current_user_id(),
            ticker=payload.get("ticker", ""),
            direction=payload.get("direction", ""),
            quantity=payload.get("quantity", 1),
            reason=payload.get("reason", ""),
            confidence=payload.get("confidence"),
            entry_price=payload.get("entry_price") or None,
            order_type=payload.get("order_type") or None,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return _api_success({"trade": trade}, trade=trade, ok=True)


@app.route("/api/paper-trade/close", methods=["POST"])
@api_guard
def api_paper_trade_close():
    payload = request.get_json(silent=True) or {}
    try:
        trade = close_paper_trade(_current_user_id(), payload.get("trade_id", ""), payload.get("exit_price") or None)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return _api_success({"trade": trade}, trade=trade, ok=True)


TICKER_SEARCH_CACHE: Dict[str, Dict[str, object]] = {}
TICKER_SEARCH_CACHE_SECONDS = 300


@app.route("/api/ticker-search", methods=["GET"])
@api_guard
def api_ticker_search():
    query = request.args.get("q", "").strip()
    if len(query) < 1:
        return _api_success({"results": []}, results=[], ok=True)

    cache_key = query.lower()
    cached = TICKER_SEARCH_CACHE.get(cache_key)
    if cached and cached.get("expires_at") and cached["expires_at"] > _now_utc():
        return _api_success({"results": cached["results"]}, results=cached["results"], ok=True)

    import yfinance as yf

    quotes: List[Dict[str, object]] = []
    for attempt in range(2):
        try:
            search = yf.Search(query, max_results=8)
            quotes = search.quotes or []
        except Exception as error:
            logger.warning("Ticker search failed for %r (attempt %d): %s", query, attempt, error)
            quotes = []
        if quotes:
            break

    results = [
        {
            "symbol": quote.get("symbol", ""),
            "name": quote.get("shortname") or quote.get("longname") or quote.get("symbol", ""),
            "exchange": quote.get("exchDisp", ""),
            "type": quote.get("quoteType", ""),
        }
        for quote in quotes
        if quote.get("symbol") and quote.get("quoteType") in {"EQUITY", "ETF", "INDEX"}
    ]
    if results:
        TICKER_SEARCH_CACHE[cache_key] = {
            "results": results,
            "expires_at": _now_utc() + timedelta(seconds=TICKER_SEARCH_CACHE_SECONDS),
        }
    return _api_success({"results": results}, results=results, ok=True)


@app.route("/api/suggestions", methods=["GET"])
def api_suggestions():
    context = _build_page_context(include_suggestions=True)
    return jsonify({"suggestions": context["suggestions"], "scanner_errors": context["scanner_errors"]})


@app.route("/api/news/x", methods=["GET"])
def api_news_x():
    context = _build_page_context(include_news=True, include_trusted_accounts=True)
    return jsonify(
        {
            "news": context["news_rows"],
            "errors": context["news_errors"],
            "trusted_accounts": context["trusted_accounts"],
            "never_auto_trade": True,
            "guardrail": "X data can support context but cannot be used as a standalone auto-trade trigger.",
        }
    )


@app.route("/api/alerts", methods=["GET", "POST"])
@api_guard
def api_alerts():
    context = _build_page_context(include_suggestions=True, include_news=True, include_reversal=True, include_trend=True)
    alerts = context["alerts"]
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        action = payload.get("action", "add")
        if action == "dismiss":
            try:
                dismiss_alert(_current_user_id(), payload.get("id", ""))
            except ValueError as error:
                raise ValidationError(str(error)) from error
            context = _build_page_context(include_suggestions=True, include_news=True)
            return _api_success(
                {"alerts": context["alerts"], "unread_count": context["unread_alert_count"]},
                alerts=context["alerts"],
                unread_count=context["unread_alert_count"],
                ok=True,
            )
        if action == "mark_read":
            try:
                mark_alert_read(_current_user_id(), payload.get("id", ""))
            except ValueError as error:
                raise ValidationError(str(error)) from error
            context = _build_page_context(include_suggestions=True, include_news=True)
            return _api_success(
                {"alerts": context["alerts"], "unread_count": context["unread_alert_count"]},
                alerts=context["alerts"],
                unread_count=context["unread_alert_count"],
                ok=True,
            )
        if action == "mark_all_read":
            mark_all_read(_current_user_id(), alerts)
            context = _build_page_context(include_suggestions=True, include_news=True)
            return _api_success(
                {"alerts": context["alerts"], "unread_count": context["unread_alert_count"]},
                alerts=context["alerts"],
                unread_count=context["unread_alert_count"],
                ok=True,
            )
        try:
            alert = add_manual_alert(_current_user_id(), payload)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        context = _build_page_context(include_suggestions=True, include_news=True)
        return _api_success(
            {"alert": alert, "alerts": context["alerts"], "unread_count": context["unread_alert_count"]},
            alert=alert,
            alerts=context["alerts"],
            unread_count=context["unread_alert_count"],
            ok=True,
        )
    return _api_success(
        {"alerts": alerts, "unread_count": context["unread_alert_count"]},
        alerts=alerts,
        unread_count=context["unread_alert_count"],
        ok=True,
    )


@app.route("/api/scanner", methods=["GET"])
@api_guard
def api_scanner():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    scanner_rows, scanner_errors, last_updated = get_market_data(force_refresh=force_refresh)
    return _api_success(
        {"rows": scanner_rows, "errors": scanner_errors, "last_updated": last_updated},
        rows=scanner_rows,
        errors=scanner_errors,
        last_updated=last_updated,
        ok=True,
    )


@app.route("/api/analysis/<section>/tickers", methods=["GET"])
@api_guard
def api_analysis_section_get(section: str):
    focus_ticker = request.args.get("focus", "").strip().upper()
    payload = _build_section_payload(_current_user_id(), section, focus_ticker=focus_ticker)
    return _api_success(payload, **payload, ok=True)


@app.route("/api/analysis/<section>/tickers", methods=["POST"])
@api_guard
def api_analysis_section_add(section: str):
    body = request.get_json(silent=True) or {}
    ticker = str(body.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValidationError("Ticker is required.")
    user_id = _current_user_id()
    try:
        add_section_ticker(user_id, section, ticker, _default_analysis_section_tickers(user_id))
    except ValueError as error:
        raise ValidationError(str(error)) from error
    payload = _build_section_payload(user_id, section)
    return _api_success(payload, **payload, ok=True)


@app.route("/api/analysis/<section>/tickers/<ticker>", methods=["DELETE"])
@api_guard
def api_analysis_section_remove(section: str, ticker: str):
    user_id = _current_user_id()
    try:
        remove_section_ticker(user_id, section, ticker, _default_analysis_section_tickers(user_id))
    except ValueError as error:
        raise ValidationError(str(error)) from error
    payload = _build_section_payload(user_id, section)
    return _api_success(payload, **payload, ok=True)


@app.route("/api/live-data-status", methods=["GET"])
@api_guard
def api_live_data_status():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    scanner_rows, scanner_errors, last_updated = get_market_data(force_refresh=force_refresh)
    connected = bool(scanner_rows)
    payload = {
        "provider": "Yahoo Finance",
        "connection_status": "🟢 Connected" if connected else "🔴 Offline",
        "last_update_time": last_updated or "Never",
        "symbols_loaded": len(scanner_rows),
        "market_session": _market_session(),
        "errors": scanner_errors,
    }
    return _api_success(
        payload,
        provider=payload["provider"],
        connection_status=payload["connection_status"],
        last_update_time=payload["last_update_time"],
        symbols_loaded=payload["symbols_loaded"],
        market_session=payload["market_session"],
        errors=payload["errors"],
        ok=True,
    )


@app.route("/api/options/<ticker>", methods=["GET"])
@api_guard
def api_options_ticker(ticker: str):
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    try:
        research_payload = get_options_data_for_ticker(ticker=ticker, force_refresh=force_refresh)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    legacy_payload = to_legacy_options_payload(research_payload)
    return _api_success(
        research_payload,
        options=legacy_payload,
        strategy_intelligence=get_strategy_data_for_ticker(ticker=ticker, force_refresh=force_refresh),
        chart_levels=get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh),
        ok=True,
    )


@app.route("/api/strategy/<ticker>", methods=["GET"])
@api_guard
def api_strategy_ticker(ticker: str):
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    payload = get_strategy_data_for_ticker(ticker=ticker, force_refresh=force_refresh)
    return _api_success(payload, strategy=payload, ok=True)


@app.route("/api/chart-levels/watchlist", methods=["GET"])
@api_guard
def api_chart_levels_watchlist():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    query_tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    tickers = query_tickers or get_watchlist_tickers(_current_user_id()) or [row["ticker"] for row in get_market_data(force_refresh=False)[0][:6]]
    rows = [get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh) for ticker in tickers]
    return _api_success({"rows": rows, "count": len(rows)}, rows=rows, count=len(rows), ok=True)


@app.route("/api/chart-levels/<ticker>", methods=["GET"])
@api_guard
def api_chart_levels_ticker(ticker: str):
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    payload = get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh)
    return _api_success(payload, chart_levels=payload, ok=True)


@app.route("/api/chart-history/<ticker>", methods=["GET"])
@api_guard
def api_chart_history_ticker(ticker: str):
    """Raw OHLC candles for the real dashboard chart (2026-09-04) - paired
    with /api/chart-levels/<ticker> above by the frontend (candles here,
    computed support/resistance/EMA/VWAP there), kept as two separate
    single-responsibility endpoints rather than one combined payload."""
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    payload = get_chart_history_for_ticker(ticker=ticker, force_refresh=force_refresh)
    return _api_success(payload, chart_history=payload, ok=True)


@app.route("/api/reversal-map", methods=["GET"])
def api_reversal_map():
    # Only reads reversal_rows/trend_errors below - include_opportunities=False
    # skips the entire strategy+chart+options pipeline this route was
    # previously paying for on every poll without ever touching its output.
    context = _build_page_context(include_reversal=True, include_trend=True, include_opportunities=False)
    return jsonify({"rows": context["reversal_rows"], "errors": context["trend_errors"]})


@app.route("/api/trend-detection", methods=["GET"])
def api_trend_detection():
    # Same reasoning as api_reversal_map just above - only reads
    # trend_rows/trend_errors, never upcoming_opportunities.
    context = _build_page_context(include_reversal=True, include_trend=True, include_opportunities=False)
    return jsonify({"rows": context["trend_rows"], "errors": context["trend_errors"]})


@app.route("/api/patterns", methods=["GET"])
def api_patterns():
    context = _build_page_context(include_patterns=True)
    return jsonify({"candles": context["candle_rows"], "patterns": context["pattern_rows"], "errors": context["pattern_errors"]})


@app.route("/api/trusted-accounts", methods=["GET", "POST", "DELETE"])
def api_trusted_accounts():
    user_id = _current_user_id()
    if request.method == "GET":
        return jsonify({"accounts": get_trusted_accounts(user_id)})

    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    try:
        if request.method == "POST":
            account = add_trusted_account(user_id=user_id, username=username)
            return jsonify({"ok": True, "account": account})
        remove_trusted_account(user_id=user_id, username=username)
        return jsonify({"ok": True})
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@app.route("/api/trusted-accounts/verify", methods=["POST"])
def api_trusted_accounts_verify():
    payload = request.get_json(silent=True) or {}
    result = lookup_x_user(payload.get("username", ""))
    return jsonify(result)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(
            {"settings": get_settings(_current_user_id()), "themes": available_themes(), "news_roadmap": get_future_news_roadmap()}
        )
    payload = request.get_json(silent=True) or {}
    settings = update_settings(_current_user_id(), payload)
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/mission-brief/status", methods=["GET"])
@api_guard
def api_mission_brief_status():
    settings = get_settings(_current_user_id())
    data = {
        "should_show": _mission_brief_should_show(settings),
        "last_viewed_date": settings.get("mission_brief_last_viewed_date", ""),
        "trading_day": _trading_day_key(),
    }
    return _api_success(data, ok=True, **data)


@app.route("/api/mission-brief/dismiss", methods=["POST"])
@api_guard
def api_mission_brief_dismiss():
    settings = _dismiss_mission_brief()
    data = {
        "ok": True,
        "dismissed": True,
        "last_viewed_date": settings.get("mission_brief_last_viewed_date", ""),
    }
    return _api_success(data, **data)


@app.route("/api/mission-brief/reset", methods=["POST"])
@api_guard
def api_mission_brief_reset():
    settings = _reset_mission_brief()
    data = {
        "ok": True,
        "show_mission_brief_again": settings.get("show_mission_brief_again", False),
    }
    return _api_success(data, **data)


@app.route("/api/autonomy/status", methods=["GET"])
@api_guard
def api_autonomy_status():
    payload = get_autonomy_status(_current_user_id())
    return _api_success(payload, autonomy=payload, ok=True)


@app.route("/api/autonomy/set-mode", methods=["POST"])
@api_guard
def api_autonomy_set_mode():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "OFF"))
    reason = str(payload.get("mode_change_reason", ""))
    result = set_mode(_current_user_id(), mode=mode, reason=reason)
    return _api_success(result, autonomy=result, ok=True)


@app.route("/api/autonomy/emergency-stop", methods=["POST"])
@api_guard
def api_autonomy_emergency_stop():
    payload = request.get_json(silent=True) or {}
    result = emergency_stop(_current_user_id(), reason=str(payload.get("mode_change_reason", "")))
    return _api_success(result, autonomy=result, ok=True)


@app.route("/api/autonomy/reset-stop", methods=["POST"])
@api_guard
def api_autonomy_reset_stop():
    payload = request.get_json(silent=True) or {}
    result = reset_emergency_stop(_current_user_id(), reason=str(payload.get("mode_change_reason", "")))
    return _api_success(result, autonomy=result, ok=True)


@app.route("/api/autonomy/risk-settings", methods=["POST"])
@api_guard
def api_autonomy_risk_settings():
    payload = request.get_json(silent=True) or {}
    try:
        result = update_risk_settings(
            _current_user_id(),
            daily_loss_limit_percent=payload.get("daily_loss_limit_percent"),
            risk_percent_of_balance=payload.get("risk_percent_of_balance"),
            max_positions=payload.get("max_positions"),
        )
    except (ValueError, TypeError) as error:
        raise ValidationError(str(error)) from error
    return _api_success(result, autonomy=result, ok=True)


OVERNIGHT_MIN_CONFIDENCE = 55  # matches the confidence floor the dashboard itself uses to call something a real opportunity vs WAIT
OVERNIGHT_MAX_ORDERS_PER_RUN = 5
OVERNIGHT_ORDER_QUANTITY = 1
CONFIDENCE_DEGRADATION_THRESHOLD = 15  # how many confidence points a held position can drop before its stop gets tightened

# How long _submit_and_protect_entry waits, synchronously, for an entry to
# fill and then for both protective legs to be confirmed resting, before
# giving up on THIS scan tick and leaving the order in whatever transitional
# lifecycle state it reached - the next scan tick's _monitor_transitional_orders
# picks it back up from there. This bound exists so one slow-to-fill ticker
# can't stall the rest of the candidate batch; it is not the only chance the
# order gets to be protected.
ENTRY_FILL_POLL_ATTEMPTS = 8
ENTRY_FILL_POLL_INTERVAL_SECONDS = 2.0
PROTECTION_CONFIRM_POLL_ATTEMPTS = 5
PROTECTION_CONFIRM_POLL_INTERVAL_SECONDS = 2.0

# _check_and_execute_target_exit's own marketable-limit sell, placed once a
# fresh price confirms the target has been reached - deliberately a LIMIT a
# small amount below the fresh price, not a bare MARKET order (MARKET has
# never been empirically verified against this sandbox, matching this
# file's own allowlist-not-assumption discipline elsewhere), close enough
# to all but guarantee an immediate fill while still bounding worst-case
# slippage on what should be a routine, already-past-target exit.
TARGET_EXIT_SLIPPAGE_TOLERANCE = 0.005

# _reconcile_unknown_submission: even a well-formed, parsed "order not
# found" response (webull_api.DefiniteOrderRejection) is not treated as
# immediately conclusive - Webull has not published a read-after-write
# consistency guarantee for order lookups, so a "not found" moments after
# an ambiguous submission could reflect broker-side replication lag rather
# than the order never having existed. The SAME definite absence must be
# confirmed this many separate times, spread across at least this many
# seconds since the FIRST such sighting, before the reservation is released
# and the entry resolves to ENTRY_FAILED. Sized off the scan cadence
# (~5 minutes) - 15 minutes and 3 confirmations means at least two full
# scan cycles' worth of independent "still not found" answers, not a
# single unlucky lookup. This ONLY mitigates read-after-write lag - it does
# NOT make an unverified error code authoritative. The grace period can
# only ever fire for a code already on
# webull_api._CONFIRMED_DEFINITE_REJECTION_ERROR_CODES (empirically
# confirmed, currently empty - see that constant's docstring), which is the
# thing actually establishing authority; this just guards against acting on
# a confirmed code's answer too soon.
UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS = 900
MIN_DEFINITE_REJECTION_CONFIRMATIONS = 3

POSITIONS_CACHE: Dict[str, Dict[str, object]] = {}
POSITIONS_CACHE_SECONDS = 30
BALANCE_CACHE: Dict[str, Dict[str, object]] = {}
BALANCE_CACHE_SECONDS = 30


def _get_live_webull_balance(user_id: str, force_refresh: bool = False) -> Dict[str, object]:
    """accounts.json only stores a balance snapshot from whenever the user last
    hit Connect/Test in Account Hub - it does not update after trades place, so
    anything reading it directly can show a stale net liq / cash / buying power.
    This fetches the current balance fresh (short-cached to avoid hammering
    Webull on every request)."""
    cached = BALANCE_CACHE.get(user_id)
    if (
        not force_refresh
        and isinstance(cached, dict)
        and isinstance(cached.get("expires_at"), datetime)
        and cached["expires_at"] > _now_utc()
    ):
        return cached["payload"]

    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        payload = {"connected": False, "balance": None, "error": ""}
    else:
        try:
            creds = get_webull_credentials(user_id)
            sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
            cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
            if not cash_account:
                raise ValueError("No Webull sandbox account found for these credentials.")
            balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], cash_account["account_id"])
            unrealized_pnl = float(balance.get("total_unrealized_profit_loss", 0) or 0)
            market_value = float(balance.get("total_market_value", 0) or 0)
            cost_basis = market_value - unrealized_pnl
            unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0
            day_pnl = float(balance.get("total_day_profit_loss", 0) or 0)
            real_net_liquidation_value = float(balance.get("total_net_liquidation_value", 0) or 0)
            virtual_net_account_value = get_virtual_net_account_value(user_id, real_net_liquidation_value)
            payload = {
                "connected": True,
                "error": "",
                "balance": {
                    "account_number": cash_account.get("account_number", ""),
                    "net_liquidation_value": (
                        round(virtual_net_account_value, 2) if virtual_net_account_value is not None else real_net_liquidation_value
                    ),
                    "real_net_liquidation_value": real_net_liquidation_value,
                    "virtual_starting_balance": get_virtual_starting_balance(user_id),
                    "is_virtual_balance": virtual_net_account_value is not None,
                    "cash_balance": balance.get("total_cash_balance", ""),
                    "buying_power": (balance.get("account_currency_assets") or [{}])[0].get("buying_power", ""),
                    "market_value": round(market_value, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "unrealized_pnl_percent": round(unrealized_pnl_percent, 2),
                    "unrealized_pnl_display": _signed_money(unrealized_pnl),
                    "day_pnl": round(day_pnl, 2),
                    "day_pnl_display": _signed_money(day_pnl),
                },
            }
        except Exception as error:  # noqa: BLE001 - a flaky Webull call shouldn't break the page
            payload = {"connected": True, "balance": None, "error": str(error)}

    BALANCE_CACHE[user_id] = {"payload": payload, "expires_at": _now_utc() + timedelta(seconds=BALANCE_CACHE_SECONDS)}
    return payload


def _get_live_webull_positions(user_id: str, force_refresh: bool = False) -> Dict[str, object]:
    """Real (sandbox) open positions from Webull, distinct from the local
    simulated paper_trader.py trades - the two are separate systems and only
    this one reflects orders actually placed through the autonomy pipeline."""
    cached = POSITIONS_CACHE.get(user_id)
    if (
        not force_refresh
        and isinstance(cached, dict)
        and isinstance(cached.get("expires_at"), datetime)
        and cached["expires_at"] > _now_utc()
    ):
        return cached["payload"]

    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        payload = {"connected": False, "positions": [], "error": ""}
    else:
        try:
            creds = get_webull_credentials(user_id)
            sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
            cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
            if not cash_account:
                raise ValueError("No Webull sandbox account found for these credentials.")
            positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], cash_account["account_id"])
            for position in positions:
                pnl = float(position.get("unrealized_profit_loss", 0) or 0)
                rate = float(position.get("unrealized_profit_loss_rate", 0) or 0)
                position["pnl_display"] = f"{'-' if pnl < 0 else ''}${abs(pnl):.2f} ({rate * 100:.2f}%)"
            payload = {"connected": True, "positions": positions, "error": ""}
        except Exception as error:  # noqa: BLE001 - a flaky Webull call shouldn't break the page
            payload = {"connected": True, "positions": [], "error": str(error)}

    POSITIONS_CACHE[user_id] = {"payload": payload, "expires_at": _now_utc() + timedelta(seconds=POSITIONS_CACHE_SECONDS)}
    return payload


def _current_webull_trading_session() -> str:
    """Webull only accepts orders under the session type that's actually active
    right now - CORE during 9:30-16:00 ET, ALL during pre/after-market, NIGHT
    from 20:00-04:00 ET (and NIGHT draws from a separate buying-power pool)."""
    now_et = datetime.now(ZoneInfo("America/New_York")).time()
    if datetime.strptime("09:30", "%H:%M").time() <= now_et <= datetime.strptime("16:00", "%H:%M").time():
        return "CORE"
    if now_et >= datetime.strptime("20:00", "%H:%M").time() or now_et < datetime.strptime("04:00", "%H:%M").time():
        return "NIGHT"
    return "ALL"


@app.route("/api/autonomy/overnight-orders", methods=["GET"])
@api_guard
def api_autonomy_overnight_orders():
    orders = list_overnight_orders(_current_user_id())
    return _api_success({"orders": orders}, orders=orders, ok=True)


def _reconcile_closed_ticker_exit_orders(user_id: str, creds: Dict[str, str], account_id: str, ticker: str) -> None:
    """For a ticker whose POSITION is absent from the broker's current
    positions list - this ALONE is never sufficient evidence of what
    happened (a position row can be absent for reasons other than "one of
    MY tracked exit legs closed it": broker-side lag, a position closed by
    some other route entirely, etc). "Determine closure using position
    data plus stop/target order status - not position absence alone":
    this corroborates with each TRACKED exit order's own broker status
    before touching anything.

    Only cancels (and only ever REMOVES tracking of) an order once
    ANOTHER tracked order for this ticker is confirmed FILLED - that's
    what actually explains why the position is gone - AND, for the leg
    being cancelled, the cancellation itself is DURABLY CONFIRMED
    afterward, never merely attempted. A failed cancel, or a cancel whose
    result can't be confirmed, alerts and leaves tracking exactly as it
    was - "never remove protective-order tracking after a cancellation
    failure; retain each unresolved leg until its terminal broker status
    is confirmed."

    If NO tracked leg for this ticker shows a confirmed FILLED status,
    this is exactly the "position absence without conclusive order
    evidence" case - alerts, and deliberately does NOT cancel anything,
    rather than infer an exit merely because the position disappeared.

    This is a secondary, ticker-centric safety net - the PRIMARY,
    entry-centric exit-detection mechanism is _reconcile_position_exit
    (called by _monitor_transitional_orders for every
    PROTECTION_CONFIRMED_ACTIVE entry), which reaches the same
    leg-status-first conclusion earlier and also records the closed
    trade / realized P&L. This function mainly still matters for
    coverage no lifecycle-tracked entry explains (legacy tracking, a
    ticker traded outside the lifecycle-state path, etc)."""
    tracked = get_exit_orders(user_id, ticker)
    if not tracked:
        return

    statuses: Dict[str, Optional[str]] = {}
    for order in tracked:
        order_id = order.get("id")
        if not order_id:
            continue
        try:
            detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, order_id)
            statuses[order_id] = ol.summarize_fill(detail)["status"]
        except Exception:  # noqa: BLE001 - inconclusive for THIS leg, never treated as evidence either way
            statuses[order_id] = None

    if not any(status == "FILLED" for status in statuses.values()):
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "unexplained_position_absence",
                    "ticker": ticker,
                    "message": (
                        f"{ticker}: this app is tracking {len(tracked)} resting exit order(s) for a position "
                        "that no longer appears in the broker's open positions, but none of the tracked orders "
                        "show a confirmed FILLED status that would explain it. Leaving tracking untouched - "
                        "review manually."
                    ),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return

    for order in tracked:
        order_id = order.get("id")
        status = statuses.get(order_id)

        if status is None:
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "exit_order_status_unconfirmed",
                        "ticker": ticker,
                        "message": f"{ticker}: could not confirm the status of tracked order {order_id} - retaining tracking, will retry.",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue  # retain - never drop tracking on an inconclusive check

        if status == "FILLED":
            # This is the leg that actually executed - nothing to cancel;
            # safe to stop tracking it (it's done, not "unresolved").
            pop_exit_order_by_id(user_id, ticker, order_id)
            continue

        if not _protective_leg_is_active(status):
            # Already CANCELLED/FAILED at the broker on its own - safe to
            # drop tracking; nothing was cancelled BY this pass.
            pop_exit_order_by_id(user_id, ticker, order_id)
            continue

        try:
            webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, order_id)
        except Exception as error:  # noqa: BLE001
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "exit_order_cancel_failed",
                        "ticker": ticker,
                        "message": f"{ticker}: failed to cancel stale exit order {order_id} after the position closed ({error}). Retaining tracking, will retry.",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue  # NEVER pop tracking on a failed cancel attempt

        try:
            recheck_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, order_id)
            recheck_status = ol.summarize_fill(recheck_detail)["status"]
        except Exception:  # noqa: BLE001
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "exit_order_cancel_unconfirmed",
                        "ticker": ticker,
                        "message": f"{ticker}: cancelled exit order {order_id} but could not confirm the cancellation - retaining tracking, will retry.",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue  # NEVER pop tracking until the cancellation is confirmed

        if _protective_leg_is_active(recheck_status):
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "exit_order_cancel_unconfirmed",
                        "ticker": ticker,
                        "message": f"{ticker}: exit order {order_id} still shows active ({recheck_status}) after a cancel attempt - retaining tracking, will retry.",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue  # NEVER pop tracking until confirmed terminal

        # Confirmed CANCELLED/FAILED/FILLED (a race, but still terminal
        # either way) - durably gone, safe to drop tracking now.
        pop_exit_order_by_id(user_id, ticker, order_id)


def _reconcile_exit_orders(user_id: str, creds: Dict[str, str], account_id: str) -> None:
    """Runs at the start of every scan/monitor tick to keep resting
    broker-side exit orders in sync with reality. The stop-loss and
    take-profit legs are two independent orders (not a true OTOCO bracket
    - see the note on place_take_profit_order), which creates two gaps
    this closes:

    1. An entry bought outside CORE hours couldn't get its STOP_LOSS order
       attached yet (STOP_LOSS only accepts the CORE session, confirmed live
       against the sandbox - NIGHT and ALL are both rejected). Retry it here;
       once CORE hours are live the retry succeeds and the gap closes itself.
    2. One leg already filled at the broker on its own (e.g. price hit the
       take-profit limit, or the stop triggered) and closed the position -
       the other leg is now stale, resting against shares that no longer
       exist. See _reconcile_closed_ticker_exit_orders for exactly how this
       is confirmed (position absence alone is NOT sufficient - it's
       corroborated against each tracked leg's own broker status) and how
       tracking is retained, not dropped, on any unconfirmed step."""
    try:
        open_tickers = {
            str(position.get("symbol", "")).upper()
            for position in webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
        }
    except Exception:  # noqa: BLE001 - best-effort reconciliation, don't block the scan over a flaky positions call
        return

    for ticker in webull_tracked_tickers(user_id):
        if ticker in open_tickers:
            continue
        try:
            _reconcile_closed_ticker_exit_orders(user_id, creds, account_id, ticker)
        except Exception:  # noqa: BLE001 - one ticker's reconciliation failing must not block the others
            continue

    if not open_tickers:
        return

    orders = list_overnight_orders(user_id)
    changed = False
    handled_tickers = set()
    for order in orders:
        ticker = str(order.get("ticker", "")).upper()
        if (
            ticker in handled_tickers
            or order.get("status") != "placed"
            or order.get("side") != "BUY"
            or ticker not in open_tickers
        ):
            continue
        handled_tickers.add(ticker)
        quantity = order.get("quantity", OVERNIGHT_ORDER_QUANTITY)

        if order.get("stop_order_placed") is False:
            stop_price = float(order.get("stop") or 0)
            if stop_price > 0:
                try:
                    time.sleep(1.0)
                    stop_result = webull_api.place_stop_loss_order(
                        app_key=creds["app_key"],
                        app_secret=creds["app_secret"],
                        account_id=account_id,
                        symbol=ticker,
                        quantity=quantity,
                        stop_price=stop_price,
                    )
                    record_exit_order(user_id, ticker, stop_result["client_order_id"], "stop")
                    order["stop_order_placed"] = True
                    order["stop_order_error"] = None
                except Exception as stop_error:  # noqa: BLE001 - still not placeable (e.g. still outside CORE hours) - try again next run
                    order["stop_order_error"] = str(stop_error)
                changed = True

        if order.get("take_profit_order_placed") is False:
            target_price = float(order.get("target") or 0)
            if target_price > 0:
                try:
                    time.sleep(1.0)
                    take_profit_result = webull_api.place_take_profit_order(
                        app_key=creds["app_key"],
                        app_secret=creds["app_secret"],
                        account_id=account_id,
                        symbol=ticker,
                        quantity=quantity,
                        target_price=target_price,
                        trading_session=_current_webull_trading_session(),
                    )
                    record_exit_order(user_id, ticker, take_profit_result["client_order_id"], "take_profit")
                    order["take_profit_order_placed"] = True
                    order["take_profit_order_error"] = None
                except Exception as take_profit_error:  # noqa: BLE001 - try again next run
                    order["take_profit_order_error"] = str(take_profit_error)
                changed = True

    if changed:
        replace_overnight_orders(user_id, orders)


# Pure trading-math helpers, deliberately factored out of _run_autonomous_trade_scan
# and _refresh_stop_confidence so the arithmetic that actually moves money can be
# unit-tested directly without mocking Webull, market data, or anything else.


def _is_daily_loss_limit_hit(day_pnl: float, current_balance: float, daily_loss_limit_percent: float) -> bool:
    """daily_loss_limit_percent <= 0 means the limit is disabled - never blocks."""
    if daily_loss_limit_percent <= 0:
        return False
    daily_loss_limit = current_balance * (daily_loss_limit_percent / 100)
    return day_pnl <= -daily_loss_limit


def _available_position_slots(max_positions: int, open_position_count: int, default_max_orders: int) -> int:
    """max_positions <= 0 means uncapped - falls back to the per-run batch cap
    (OVERNIGHT_MAX_ORDERS_PER_RUN) instead of an unlimited number of new orders
    in one scan."""
    if max_positions <= 0:
        return default_max_orders
    return max(0, max_positions - open_position_count)


def _compute_risk_budget(current_balance: float, risk_percent_of_balance: float) -> float:
    """The dollar amount you're willing to LOSE if this trade's stop is hit -
    equity x risk_percent_of_balance. Not a spend cap (see
    _compute_position_quantity for why that distinction is the whole point).
    risk_percent_of_balance <= 0 means risk-based sizing is disabled (returns
    0, which _compute_position_quantity treats as "no risk-based constraint" -
    affordability and the exposure cap, if set, still apply)."""
    if risk_percent_of_balance <= 0:
        return 0.0
    return current_balance * (risk_percent_of_balance / 100)


def _compute_position_exposure_cap(current_balance: float, max_position_exposure_percent: float) -> float:
    """Optional hard ceiling on total dollars committed to a single position,
    independent of stop distance - guards against a position sizing itself
    very large purely because its stop happens to be extremely tight.
    max_position_exposure_percent <= 0 means this cap is disabled (no
    setting currently exposes it in the UI - it defaults to 0/disabled for
    every account until one does)."""
    if max_position_exposure_percent <= 0:
        return 0.0
    return current_balance * (max_position_exposure_percent / 100)


def _extract_broker_buying_power(balance: Dict[str, object]) -> Optional[float]:
    """Pulls the REAL broker-reported buying power out of a get_account_balance
    payload - same field path already used for display in
    _get_live_webull_balance (account_currency_assets[0].buying_power).
    Returns None (not 0.0) on anything malformed or missing, since "we
    couldn't read it" and "the broker says zero" are different failure
    modes and only one of them is safe to size against - see
    _compute_position_quantity's fail-closed handling of a None
    broker_buying_power.

    Also rejects a value that parses but isn't finite (NaN or +/-Infinity).
    float("nan") and float("inf") both succeed without raising, and NaN in
    particular compares False to everything including `< 0`, so a NaN
    reading would have silently sailed past the negative-value check below
    and been returned as a "valid" buying power - which would then corrupt
    every downstream Decimal conversion/comparison it touches
    (Decimal("NaN") propagates through arithmetic rather than raising in
    most operations, or raises InvalidOperation in others, neither of which
    is a safe way to discover a bad reading three functions away from where
    it was parsed)."""
    try:
        assets = balance.get("account_currency_assets") or [{}]
        raw = assets[0].get("buying_power", None)
        if raw is None or raw == "":
            return None
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            return None
        return value
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def _extract_option_buying_power(balance: Dict[str, object]) -> Optional[float]:
    """Same shape/failure-mode discipline as _extract_broker_buying_power,
    reading option_buying_power instead of buying_power - a REAL, DISTINCT
    field confirmed live 2026-09-03 on the margin sandbox account's own
    balance response (account_currency_assets[0].option_buying_power),
    separate from cash_balance/day_buying_power - see
    /api/admin/diagnostic/sandbox-accounts. None (not 0.0) means "couldn't
    determine it", same reasoning as the equity function."""
    try:
        assets = balance.get("account_currency_assets") or [{}]
        raw = assets[0].get("option_buying_power", None)
        if raw is None or raw == "":
            return None
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            return None
        return value
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def _to_decimal(value: float) -> "Decimal":
    """Converts via str(), not Decimal(x) directly, so a float's own binary
    imprecision never enters the Decimal - str() gives the same decimal text
    a human would write (str(20.1) == '20.1', not the 20.100000000000001...
    Decimal(20.1) would produce)."""
    from decimal import Decimal

    return Decimal(str(value))


def _floor_shares(numerator: "Decimal", denominator: "Decimal") -> int:
    """Floor division for money math in Decimal throughout - both inputs
    must already be Decimal (see _to_decimal), not float, or the float
    imprecision this exists to avoid can already be baked into them before
    they arrive here. A plain float floor-divide can silently under-size at
    an exact boundary: $3.00 risk over a $0.10 risk-per-share (a $20.10
    entry, $20.00 stop - both perfectly ordinary stock prices) should floor
    to exactly 30, but entry_price - stop_price computed in float first
    gives 0.10000000000000142, and 3.0 // 0.10000000000000142 floors to 29 -
    a real, silent one-share under-size from a subtraction most callers
    would never think to distrust. Converting entry_price and stop_price to
    Decimal BEFORE subtracting (not just before the final division) is what
    actually fixes this - see _compute_position_quantity."""
    from decimal import ROUND_DOWN

    if denominator <= 0:
        return 0
    return int((numerator / denominator).to_integral_value(rounding=ROUND_DOWN))


def _compute_position_quantity(
    risk_budget: Optional[float],
    entry_price: Optional[float],
    stop_price: Optional[float],
    available_buying_power: Optional[float],
    broker_buying_power: Optional[float],
    position_exposure_cap: float = 0.0,
    portfolio_risk_remaining: Optional[float] = None,
    direction: str = "long",
) -> Dict[str, object]:
    """Sizes a position by risk-at-stop, not by raw share price - fixes a
    real bug found in production this session where "risk per trade" was
    being used as a maximum SPEND (budget // share_price) with the stop
    distance never entering the calculation at all. Two setups with the same
    entry price but very different stop distances used to size identically
    regardless of how much money was actually at risk if stopped out, and
    any stock priced above the (mislabeled) "risk" budget was hard-skipped
    even when a properly risk-sized quantity would have been affordable and
    well within the real risk budget.

    Every input is Optional and every failure mode fails CLOSED (quantity 0)
    rather than falling back to a default that could permit an unintended
    trade - "risk disabled" is defined precisely as risk_budget being None,
    <= 0, or otherwise unusable, and in every one of those cases this
    refuses to size a trade at all rather than substituting a fallback
    quantity. There is deliberately no fallback-to-N-shares path anymore: a
    trade that can't be risk-sized is a trade this function will not size,
    full stop. Missing entry_price, stop_price, available_buying_power, or
    broker_buying_power (None - the caller couldn't determine them, e.g. a
    failed balance fetch) are treated the same way, not coerced to 0 and
    sized as if "definitely zero dollars available" - that's a different,
    more specific failure than "we don't actually know."

    available_buying_power is the ISOLATED VIRTUAL allocation (this user's
    chosen virtual equity minus capital this app has already committed to
    its own tracked tickers) - it is what actually controls strategy sizing
    day to day. broker_buying_power is the REAL account's buying power as
    reported by the broker, and is layered on top as a hard ceiling per the
    formula quantity = min(risk, virtual allocation, broker buying power,
    position cap): a large sandbox balance will not normally bind, but
    checking it costs one extra constraint and prevents this architecture
    from becoming dangerous if it's ever pointed at a smaller or live
    account. The two are deliberately not merged into one number - they
    answer different questions and either one alone can be the tightest
    constraint depending on account state.

    Returns a structured result (all five keys always present) instead of a
    single reason string, so a successful sizing and a tied constraint are
    both auditable, not just the one label that happened to win a tie:
        {
          "quantity": int,
          "constraints": {"risk": int, "buying_power": int, "broker_buying_power": int, "position_cap": int, "portfolio_risk": int|None},
          "binding_constraints": [str, ...],   # every constraint tied for the minimum, not just one
          "reason": str,                        # "" on success, else a combined human-readable message
        }
    Constraint values in "constraints" are each independently computed
    (ignoring the others) so the full picture is visible even when only one
    ends up binding. A disabled constraint (risk_budget <= 0/None,
    position_exposure_cap <= 0, portfolio_risk_remaining is None) reports
    None in "constraints" rather than a number, and is excluded from the
    min() - it never binds, it just isn't evaluated.

    entry_price <= 0/None, or stop_price missing/invalid (<= 0, or on the
    wrong side of entry_price for `direction` - not a valid stop) both fail
    closed immediately with quantity 0 and no constraint breakdown, since
    risk-per-share can't be computed at all without a valid stop.

    direction="long" (the default, and the only shape this function sized
    before 2026-09-02) requires stop_price BELOW entry_price - a rise past
    entry is the position working, a fall to the stop is the loss.
    direction="short" requires stop_price ABOVE entry_price instead - the
    mirror image: a fall past entry is the position working, a rise to the
    stop is the loss. Either way risk_per_share is computed so it's always
    positive for a valid stop, never silently negative.

    Whole shares only - Webull's OpenAPI quantity field hasn't been verified
    to accept a fractional value, so this floors rather than guessing."""
    if entry_price is None or entry_price <= 0:
        return {"quantity": 0, "constraints": {}, "binding_constraints": ["entry_price"], "reason": "no valid entry price"}
    is_short = direction == "short"
    if stop_price is None or stop_price <= 0:
        return {
            "quantity": 0,
            "constraints": {},
            "binding_constraints": ["stop_price"],
            "reason": "no valid stop price to size risk against",
        }
    stop_on_wrong_side = (stop_price >= entry_price) if not is_short else (stop_price <= entry_price)
    if stop_on_wrong_side:
        return {
            "quantity": 0,
            "constraints": {},
            "binding_constraints": ["stop_price"],
            "reason": (
                "no valid stop below entry price to size risk against" if not is_short
                else "no valid stop above entry price to size risk against"
            ),
        }

    from decimal import Decimal

    # Converted to Decimal immediately, before ANY arithmetic (including the
    # subtraction below) touches them as float - see _floor_shares for why
    # doing this only at the final division is not enough.
    entry_price_dec = _to_decimal(entry_price)
    stop_price_dec = _to_decimal(stop_price)
    risk_per_share = (stop_price_dec - entry_price_dec) if is_short else (entry_price_dec - stop_price_dec)

    constraints: Dict[str, Optional[int]] = {
        "risk": None,
        "buying_power": None,
        "broker_buying_power": None,
        "position_cap": None,
        "portfolio_risk": None,
    }

    risk_disabled = risk_budget is None or risk_budget <= 0
    if not risk_disabled:
        constraints["risk"] = _floor_shares(_to_decimal(risk_budget), risk_per_share)

    buying_power_unknown = available_buying_power is None
    if not buying_power_unknown:
        constraints["buying_power"] = _floor_shares(_to_decimal(max(0.0, available_buying_power)), entry_price_dec)

    broker_buying_power_unknown = broker_buying_power is None
    if not broker_buying_power_unknown:
        constraints["broker_buying_power"] = _floor_shares(_to_decimal(max(0.0, broker_buying_power)), entry_price_dec)

    if position_exposure_cap and position_exposure_cap > 0:
        constraints["position_cap"] = _floor_shares(_to_decimal(position_exposure_cap), entry_price_dec)

    if portfolio_risk_remaining is not None:
        constraints["portfolio_risk"] = _floor_shares(_to_decimal(max(0.0, portfolio_risk_remaining)), risk_per_share)

    reasons_by_key = {
        "risk": "risk budget too small for one share at this stop",
        "buying_power": "insufficient buying power",
        "broker_buying_power": "insufficient real broker buying power",
        "position_cap": "position exposure cap reached",
        "portfolio_risk": "remaining portfolio risk too small for one share at this stop",
    }

    # Risk being disabled/unknown, or either buying-power figure being
    # unknown, fails closed outright rather than sizing off whatever
    # constraints remain - "we don't know if this is safe" is not the same
    # as "size it anyway".
    if risk_disabled:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["risk"],
            "reason": "risk-based sizing is disabled or unavailable - refusing to size a trade without a valid risk budget",
        }
    if buying_power_unknown:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["buying_power"],
            "reason": "available buying power could not be determined - refusing to size a trade against stale or missing account data",
        }
    if broker_buying_power_unknown:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["broker_buying_power"],
            "reason": "real broker buying power could not be determined - refusing to size a trade against stale or missing account data",
        }

    active = {key: value for key, value in constraints.items() if value is not None}
    minimum = min(active.values())
    binding = [key for key, value in active.items() if value == minimum]

    if minimum < 1:
        reason = " and ".join(reasons_by_key[key] for key in binding)
        return {"quantity": 0, "constraints": constraints, "binding_constraints": binding, "reason": reason}
    return {"quantity": minimum, "constraints": constraints, "binding_constraints": binding, "reason": ""}


def _compute_option_contract_quantity(
    risk_budget: Optional[float],
    ask_price: Optional[float],
    available_buying_power: Optional[float],
    broker_option_buying_power: Optional[float],
    position_exposure_cap: float = 0.0,
    contract_multiplier: float = 100.0,
) -> Dict[str, object]:
    """Sizes a long option position by premium cost, not risk-at-stop -
    unlike _compute_position_quantity (equity), a long call/put's maximum
    possible loss IS the premium paid: there is no separate stop-distance
    to size risk against, buying the contract already bounds the risk by
    construction (see the options plan's "Lifecycle" section - a long
    option skips PROTECTION_PENDING for exactly this reason). So "risk"
    and "cost" are the same number here, both measured against
    ask_price * contract_multiplier per contract.

    Every input is Optional and every failure mode fails CLOSED (quantity
    0), matching _compute_position_quantity's own discipline exactly - see
    its docstring for the full reasoning. available_buying_power is this
    app's own virtual/reserved allocation; broker_option_buying_power is
    the broker's real option_buying_power balance (confirmed live
    2026-09-03 as a distinct field from day_buying_power/cash_balance on
    the margin sandbox account - see get_account_balance's raw response
    via /api/admin/diagnostic/sandbox-accounts) - both must independently
    cover the trade, and either being unknown fails closed rather than
    sizing off the other alone.

    Whole contracts only, floored - fractional contracts don't exist."""
    if ask_price is None or ask_price <= 0:
        return {"quantity": 0, "constraints": {}, "binding_constraints": ["ask_price"], "reason": "no valid ask price to size against"}

    from decimal import Decimal

    ask_price_dec = _to_decimal(ask_price)
    cost_per_contract = ask_price_dec * _to_decimal(contract_multiplier)

    constraints: Dict[str, Optional[int]] = {
        "risk": None,
        "buying_power": None,
        "broker_buying_power": None,
        "position_cap": None,
    }

    risk_disabled = risk_budget is None or risk_budget <= 0
    if not risk_disabled:
        constraints["risk"] = _floor_shares(_to_decimal(risk_budget), cost_per_contract)

    buying_power_unknown = available_buying_power is None
    if not buying_power_unknown:
        constraints["buying_power"] = _floor_shares(_to_decimal(max(0.0, available_buying_power)), cost_per_contract)

    broker_buying_power_unknown = broker_option_buying_power is None
    if not broker_buying_power_unknown:
        constraints["broker_buying_power"] = _floor_shares(_to_decimal(max(0.0, broker_option_buying_power)), cost_per_contract)

    if position_exposure_cap and position_exposure_cap > 0:
        constraints["position_cap"] = _floor_shares(_to_decimal(position_exposure_cap), cost_per_contract)

    reasons_by_key = {
        "risk": "risk budget too small for one contract at this premium",
        "buying_power": "insufficient buying power",
        "broker_buying_power": "insufficient real broker option buying power",
        "position_cap": "position exposure cap reached",
    }

    if risk_disabled:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["risk"],
            "reason": "risk-based sizing is disabled or unavailable - refusing to size a trade without a valid risk budget",
        }
    if buying_power_unknown:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["buying_power"],
            "reason": "available buying power could not be determined - refusing to size a trade against stale or missing account data",
        }
    if broker_buying_power_unknown:
        return {
            "quantity": 0,
            "constraints": constraints,
            "binding_constraints": ["broker_buying_power"],
            "reason": "real broker option buying power could not be determined - refusing to size a trade against stale or missing account data",
        }

    active = {key: value for key, value in constraints.items() if value is not None}
    minimum = min(active.values())
    binding = [key for key, value in active.items() if value == minimum]

    if minimum < 1:
        reason = " and ".join(reasons_by_key[key] for key in binding)
        return {"quantity": 0, "constraints": constraints, "binding_constraints": binding, "reason": reason}
    return {"quantity": minimum, "constraints": constraints, "binding_constraints": binding, "reason": "", "cost_per_contract": float(cost_per_contract)}


def _compute_committed_virtual_capital(
    real_open_positions: Sequence[Dict[str, object]],
    real_open_orders: Sequence[Dict[str, object]],
    tracked_tickers: Sequence[str],
) -> float:
    """Dollars already committed or reserved - deliberately broker-
    authoritative, NOT derived from this app's own lifecycle_state
    bookkeeping. Nothing in this codebase currently transitions an entry to
    the CLOSED lifecycle state when a position actually exits (stop hit,
    target hit, manual close) - order_lifecycle.py defines CLOSED as
    terminal, but no code path ever calls ol.transition(entry, ol.CLOSED,
    ...). Trusting lifecycle_state here would mean every position this app
    ever opens counts as "still committing capital" forever, well past when
    it's actually closed. Checking the broker's own current positions and
    open orders instead means a stale, missing, or legacy (pre-dating the
    state machine entirely) local record can never cause an under- or
    over-release of committed capital - see test_capital_reconciliation.py.

    Two things commit capital, both read live from Webull:
      1. Shares currently HELD for a ticker this app has ever traded
         (matched against real_open_positions, filtered to tracked_tickers
         so a ticker the user only ever traded manually - never through
         autonomous mode - isn't pulled into this budget) - valued at
         CURRENT market price (quantity x last_price), never historical
         entry cost. Net liquidation value already reflects any unrealized
         gain/loss on that market value; subtracting cost basis from a
         net-liq figure that already includes the current (possibly
         appreciated) market value would let an unrealized gain masquerade
         as available cash that isn't actually free to deploy.
      2. The UNFILLED remainder of any resting BUY order (real_open_orders,
         side == BUY, total_quantity - filled_quantity), valued at its
         limit price - the actual dollar amount the broker holds against
         buying power until it fills or cancels. Only the unfilled portion,
         specifically so a partially-filled order's already-held shares
         (counted once, in #1, at current market value) are never also
         counted against their own still-resting remainder (counted here,
         at limit price) - a partial fill is one position's worth of
         capital, not two.

    Known limitation: if the SAME ticker is traded both autonomously and
    manually, real_open_positions can't distinguish which shares came from
    which source - the full held quantity for that ticker counts here,
    which can overstate committed capital if some of those shares are the
    user's own manual trade rather than this app's. Webull's position API
    doesn't expose per-lot/per-source tracking to resolve this further.

    Schema validation here rejects MISSING or NONSENSICAL required fields,
    not just the wrong container type (a non-dict record already raises on
    the first .get() call, which _build_capital_snapshot's caller-level
    try/except already turns into a fail-closed None). A field silently
    defaulted via .get(key, 0) - a missing quantity, price, or side - or a
    negative quantity/price, would previously pass through arithmetic
    without ever raising, silently UNDER-counting committed capital (the
    dangerous direction: it overstates available buying power, not
    understates it). Every field this function actually uses to decide
    whether a record counts, and how much, is validated to be present and
    non-negative before it's used; any violation raises ValueError, which
    fails the ENTIRE calculation closed via the same caller-level
    try/except - one malformed record must not let every OTHER record's
    good data compute a wrong (too permissive) answer."""
    tracked = {ticker.strip().upper() for ticker in tracked_tickers if ticker}

    position_value = _to_decimal(0.0)
    for position in real_open_positions:
        symbol = position.get("symbol")
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"malformed position record: missing or invalid symbol ({symbol!r})")
        ticker = symbol.upper()
        if ticker not in tracked:
            continue
        quantity_raw = position.get("quantity")
        price_raw = position.get("last_price")
        if quantity_raw is None or price_raw is None:
            raise ValueError(f"malformed position record for {ticker}: missing quantity or last_price")
        quantity = _to_decimal(float(quantity_raw))
        price = _to_decimal(float(price_raw))
        if quantity < 0 or price < 0:
            raise ValueError(
                f"malformed position record for {ticker}: negative quantity ({quantity_raw!r}) or last_price ({price_raw!r})"
            )
        position_value += quantity * price

    reserved_value = _to_decimal(0.0)
    for order in real_open_orders:
        side_raw = order.get("side")
        if side_raw is None or not isinstance(side_raw, str):
            raise ValueError(f"malformed open order record: missing or invalid side ({side_raw!r})")
        if side_raw.upper() != "BUY":
            continue
        symbol = order.get("symbol")
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"malformed open order record: missing or invalid symbol ({symbol!r})")
        ticker = symbol.upper()
        if ticker not in tracked:
            continue
        total_quantity_raw = order.get("total_quantity")
        filled_quantity_raw = order.get("filled_quantity")
        limit_price_raw = order.get("limit_price")
        if total_quantity_raw is None or filled_quantity_raw is None or limit_price_raw is None:
            raise ValueError(f"malformed open order record for {ticker}: missing total_quantity/filled_quantity/limit_price")
        total_quantity = _to_decimal(float(total_quantity_raw))
        filled_quantity = _to_decimal(float(filled_quantity_raw))
        limit_price = _to_decimal(float(limit_price_raw))
        if total_quantity < 0 or filled_quantity < 0 or limit_price < 0:
            raise ValueError(
                f"malformed open order record for {ticker}: negative total_quantity/filled_quantity/limit_price"
            )
        unfilled_quantity = total_quantity - filled_quantity
        if unfilled_quantity <= 0:
            continue
        reserved_value += unfilled_quantity * limit_price

    return float(position_value + reserved_value)


def _compute_available_buying_power(total_equity: float, committed_capital: float) -> float:
    """total_equity (net liquidation value) is not the same thing as
    available buying power - it doesn't subtract capital already tied up in
    open positions or reserved by pending orders. This does."""
    return max(0.0, float(_to_decimal(total_equity) - _to_decimal(committed_capital)))


# Found live 2026-08-28: an entry's limit_price is computed from get_bars'
# up-to-15-minutes-stale chart data at scan time (see
# integrations/alpaca_data.py's own module docstring), but the order can
# be submitted to Webull minutes later - for a fast-moving momentum
# candidate (by definition the kind this strategy looks for), that gap
# was enough real price drift to trip Webull's own
# OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE ("the order price is too
# deviated") rejection, which then had to be frozen and reconciled after
# the fact via the ambiguous-submission workflow. 2.0% is deliberately
# tight - momentum candidates by nature move fast, so this is meant to
# catch genuine multi-minute drift on a live mover, not every normal tick
# of noise; if this proves too tight or too loose in practice against
# real Webull rejections, tune the constant, not the comparison logic.
_MAX_ENTRY_PRICE_DRIFT_PERCENT = 2.0


def _price_has_drifted_too_far(
    scan_time_price: float, fresh_price: float, max_deviation_percent: float = _MAX_ENTRY_PRICE_DRIFT_PERCENT
) -> bool:
    """True if fresh_price has moved more than max_deviation_percent away
    from scan_time_price - the actual entry-price submission gate (see
    the caller in the entry-submission loop). scan_time_price <= 0 can't
    produce a meaningful percent deviation (division by a non-positive
    reference) and is deliberately NOT treated as "drifted" here - the
    caller's own `limit_price <= 0` check already fails that case closed
    for an entirely different, more specific reason before this is ever
    reached."""
    if scan_time_price <= 0:
        return False
    deviation_percent = abs(fresh_price - scan_time_price) / scan_time_price * 100
    return deviation_percent > max_deviation_percent


def _compute_available_buying_power_with_reservations(
    snapshot_available_buying_power: Optional[float], local_reservations: "Decimal | float"
) -> Optional[float]:
    """Available buying power for ONE candidate, layered over a single
    broker snapshot taken once at the start of the scan plus every
    reservation already added for candidates earlier in this SAME run - see
    the comment in _run_autonomous_trade_scan_locked for why re-reading the
    broker before each candidate does not solve broker-side eventual
    consistency. None propagates through unchanged (the snapshot itself
    could not be determined - see _compute_committed_virtual_capital's
    caller - so no candidate this tick can be sized, not just this one).
    local_reservations is normally already a Decimal (see _reservation_notional
    and the local_reservations accumulator in _run_autonomous_trade_scan_locked) -
    _to_decimal round-trips a Decimal through str() losslessly, so a plain
    float is still accepted too (e.g. from a test calling this directly)."""
    if snapshot_available_buying_power is None:
        return None
    return max(0.0, float(_to_decimal(snapshot_available_buying_power) - _to_decimal(local_reservations)))


def _reservation_notional(quantity: float, limit_price: float) -> "Decimal":
    """The full requested notional (not just what ends up filling) reserved
    the instant an order is accepted - conservative on purpose, the same
    convention _compute_committed_virtual_capital uses for a resting
    order's unfilled remainder: it still holds real buying power at the
    broker until it fills or is cancelled.

    Returns a Decimal, not a float, specifically so the local_reservations
    accumulator in _run_autonomous_trade_scan_locked can do `+=` in true
    Decimal arithmetic across every candidate in a scan - each individual
    call was already Decimal-safe internally, but accumulating the RESULTS
    as float (0.1 + 0.2 != 0.3) could still reintroduce binary-imprecision
    at the addition step across many candidates, even though no single
    multiplication ever touched a float. A negative result should never be
    possible (quantity and limit_price are always positive by construction
    upstream) - raising here rather than silently letting it through is a
    canary against a future caller bug, not a case expected in practice."""
    notional = _to_decimal(quantity) * _to_decimal(limit_price)
    if notional < 0:
        raise ValueError(f"reservation notional must never be negative (quantity={quantity!r}, limit_price={limit_price!r})")
    return notional


def _build_capital_snapshot(
    fetch_open_orders,
    real_open_positions: Sequence[Dict[str, object]],
    tracked_tickers: Sequence[str],
    total_equity: float,
) -> Optional[float]:
    """The one-time-per-scan broker snapshot _run_autonomous_trade_scan_locked
    takes before sizing any candidate. fetch_open_orders is a zero-arg
    callable (already bound to creds/account_id by the caller) rather than
    the orders list directly, specifically so a single try/except here
    covers BOTH failure modes the same way: the fetch itself raising
    (network error, auth failure) and the fetch succeeding but returning
    something malformed enough that processing it raises (unexpected shape,
    missing fields past what .get() defaults can absorb). Either way this
    returns None - never a partial number, never a value computed from only
    some of the real commitments - so every candidate in this scan tick
    fails closed together rather than some sizing against good data and
    others silently against wrong data."""
    try:
        real_open_orders = fetch_open_orders()
        committed_capital = _compute_committed_virtual_capital(real_open_positions, real_open_orders, tracked_tickers)
        return _compute_available_buying_power(total_equity, committed_capital)
    except Exception as error:  # noqa: BLE001 - intentionally broad, see docstring
        # Logged, not silent - found live 2026-08-28 that a candidate could
        # reach _compute_position_quantity with available_buying_power=None
        # and skip with "available buying power could not be determined",
        # zero trace of WHY anywhere a human could see it - the exact same
        # silent-degradation shape as the ticker-intelligence timeout that
        # caused candidates_found=0 for days earlier this session (see
        # _fetch_ticker_intelligence's own comment). Still fails closed
        # (returns None either way - a caught exception here must never
        # size a trade against partial/wrong data) but now leaves a real
        # trace of the actual cause instead of forcing a re-diagnosis from
        # scratch next time.
        logger.warning("_build_capital_snapshot failed, sizing this scan will fail closed: %s", error)
        return None


def _new_entries_allowed(trading_session: str) -> bool:
    """New autonomous entries are restricted to CORE hours - not because an
    entry order itself needs CORE (LIMIT orders accept ALL/NIGHT fine), but
    because place_stop_loss_order only accepts CORE. Entering outside CORE
    would leave a filled position with no real broker-side stop until CORE
    hours arrive, relying entirely on reconciliation to eventually close
    that gap - acceptable for retrying a stop on an existing position, not
    for opening a brand new one with no protection plan at all."""
    return trading_session == "CORE"


def _new_entries_disabled_by_deployment_kill_switch() -> bool:
    """A DEPLOYMENT-level (not per-user) kill switch, checked via the
    PLUTO_DISABLE_NEW_ENTRIES environment variable - flippable by an
    operator (a Render dashboard env var change + redeploy, or a manual
    restart with the var set) WITHOUT touching any individual user's own
    settings. Distinct from the existing PER-USER "emergency stop" toggle
    in Account Hub (risk_settings["emergency_stop_enabled"], checked
    separately in _run_autonomous_trade_scan_locked) - this is the
    platform-wide equivalent, for an operator who needs to halt ALL new
    autonomous entries at once (e.g. while investigating an incident)
    without needing to touch every account individually.

    Deliberately does NOT affect reconciliation/exit-monitoring/protection
    work anywhere in this app - _reconcile_exit_orders, _refresh_stop_confidence,
    _discover_orphaned_broker_entries, _reconcile_unknown_submissions,
    _monitor_transitional_orders (and everything the continuous monitor
    endpoint calls) all run UNCONDITIONALLY, before this check is even
    reached in _run_autonomous_trade_scan_locked - "leave safety
    monitoring active" is true by construction, not because this function
    special-cases it. This only ever blocks the NEW-entry candidate-
    sizing/placement path in the full 5-minute scan; the continuous
    monitor and fast monitor never place new entries at all regardless
    (see test_fast_monitor_never_scans_scores_or_places_a_new_entry)."""
    return os.environ.get("PLUTO_DISABLE_NEW_ENTRIES", "").strip().lower() in ("1", "true", "yes", "on")


def _entry_fill_is_final(status: str) -> bool:
    """True once an entry order's fill status won't change further without a
    brand new placement - FILLED (done) or CANCELLED/FAILED (never will).
    SUBMITTED/"PARTIAL FILLED" mean keep polling - real Webull OrderStatus
    values, confirmed live this session via get_order_detail."""
    return status in ("FILLED", "CANCELLED", "FAILED")


def _protective_leg_is_active(status: str) -> bool:
    """True if a protective order is genuinely resting at the broker right
    now - SUBMITTED (untouched) or "PARTIAL FILLED" (partially executed, the
    remainder still resting) both count as active. FILLED means it already
    executed (the position exited through this leg, not a confirmation
    failure); CANCELLED/FAILED mean it needs to be replaced."""
    return status in ("SUBMITTED", "PARTIAL FILLED")


def _compute_tightened_stop(current_stop: float, current_price: float) -> float:
    """Moves the stop halfway from where it was to the current price - locks
    in progress made so far without exiting on a single soft signal. Capped
    just under current price so the resulting stop order is always valid.
    Never returns a value below current_stop; callers must still check the
    result against current_stop themselves before acting; if
    current_price <= current_stop (price has already fallen through the
    stop), the formula would compute a value at or below current_stop, and
    the caller's own "only tighten, never loosen" check correctly no-ops."""
    return round(min(current_stop + (current_price - current_stop) * 0.5, current_price * 0.999), 2)


def _refresh_stop_confidence(user_id: str, creds: Dict[str, str], account_id: str) -> None:
    """Runs at the start of every scan tick (same 5-minute cadence as the
    cron scheduler) and re-scores every open position's ticker against the
    live, calibrated confidence engine - the same one used to find entries.
    If a setup has degraded since entry (confidence dropped a lot, or the
    recommendation flipped away from CALL), the resting stop-loss is tightened
    to lock in more of the gain / cap more of the loss. It only ever tightens,
    never loosens, and only ever touches the stop leg - any resting take-profit
    order for the same ticker is left completely alone via
    pop_exit_orders_by_type. STOP_LOSS only accepts the CORE session (same
    constraint _reconcile_exit_orders works around), so this is a no-op
    outside CORE hours and simply retries on the next tick."""
    if _current_webull_trading_session() != "CORE":
        return
    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    except Exception:  # noqa: BLE001 - best-effort refresh, don't block the scan over a flaky positions call
        return
    if not positions:
        return

    orders = list_overnight_orders(user_id)
    changed = False

    for position in positions:
        ticker = str(position.get("symbol", "")).upper()
        if not ticker:
            continue
        tracked_entry = next(
            (
                order
                for order in orders
                if str(order.get("ticker", "")).upper() == ticker
                and order.get("status") == "placed"
                and order.get("side") == "BUY"
            ),
            None,
        )
        if not tracked_entry:
            continue

        current_stop = float(tracked_entry.get("stop") or 0)
        if current_stop <= 0:
            continue

        try:
            fresh = build_strategy_intelligence(ticker)
        except Exception:  # noqa: BLE001 - flaky data fetch for this ticker, try again next tick
            continue
        if fresh.get("insufficient_data"):
            continue

        entry_confidence = int(tracked_entry.get("llm_adjusted_confidence") or tracked_entry.get("confidence") or 0)
        current_confidence = int(fresh.get("strategy_confidence", 0) or 0)
        current_recommendation = str(fresh.get("recommendation", "")).upper()
        degraded = (
            current_recommendation != "CALL"
            or (entry_confidence - current_confidence) >= CONFIDENCE_DEGRADATION_THRESHOLD
        )
        if not degraded:
            continue

        current_price = float(position.get("last_price", 0) or 0)
        quantity = float(position.get("quantity", 0) or 0)
        if current_price <= 0 or quantity <= 0:
            continue

        tightened_stop = _compute_tightened_stop(current_stop, current_price)
        if tightened_stop <= current_stop:
            continue

        stale_legs = pop_exit_orders_by_type(user_id, ticker, "stop")
        for exit_order in stale_legs:
            try:
                webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, exit_order["id"])
            except Exception:  # noqa: BLE001 - likely already filled/expired, that's fine
                pass

        try:
            time.sleep(1.0)
            stop_result = webull_api.place_stop_loss_order(
                app_key=creds["app_key"],
                app_secret=creds["app_secret"],
                account_id=account_id,
                symbol=ticker,
                quantity=quantity,
                stop_price=tightened_stop,
            )
            record_exit_order(user_id, ticker, stop_result["client_order_id"], "stop")
            tracked_entry["stop"] = tightened_stop
            tracked_entry["stop_refreshed_at"] = _now_utc().isoformat()
            tracked_entry["stop_refresh_reason"] = (
                f"confidence dropped {entry_confidence}->{current_confidence}"
                if current_recommendation == "CALL"
                else f"recommendation flipped to {current_recommendation} (was {entry_confidence}% CALL at entry)"
            )
            changed = True
        except Exception as stop_error:  # noqa: BLE001 - the old stop is already cancelled; flag it unprotected so _reconcile_exit_orders retries next tick
            tracked_entry["stop_order_placed"] = False
            tracked_entry["stop_refresh_error"] = str(stop_error)
            changed = True

    if changed:
        replace_overnight_orders(user_id, orders)


def _run_autonomous_trade_scan(user_id: str, dry_run: bool = False) -> Dict[str, object]:
    """Thin wrapper around _run_autonomous_trade_scan_locked that holds an
    OS-level per-user lock (scan_lock.py) for the scan's entire duration.
    gunicorn runs multiple worker processes, and this function is called
    both from a manual button click and from the cron-trigger endpoint's
    per-user loop - without this lock, two overlapping calls for the same
    user (a retry, a double cron fire, a click landing mid-tick) could each
    independently pass the position-cap/risk checks and place orders that
    were never meant to coexist. Raises ScanAlreadyRunningError (a
    PlutoTradeError, so it surfaces as a friendly 409) if a scan for this
    user is already in flight.

    dry_run passes straight through to _run_autonomous_trade_scan_locked -
    still held under the SAME lock even though a preview has no broker side
    effects of its own, so it can never read a torn/mid-update snapshot of
    overnight_orders.json while a REAL scan for this user is concurrently
    writing to it."""
    with user_scan_lock(user_id):
        return _run_autonomous_trade_scan_locked(user_id, dry_run=dry_run)


def _user_needs_fast_monitor_pass(user_id: str) -> bool:
    """Cheap, LOCAL-ONLY (no broker calls) check deciding whether the fast
    monitor should spend a broker round-trip on this user AT ALL. "Safety
    monitoring must continue when autonomy is switched OFF - OFF prevents
    new entries only" means this can NOT simply filter by autonomy mode
    the way the full 5-minute scan's own cron-trigger endpoint still does
    for ITS new-candidate work - a user who has since turned autonomy OFF
    (possibly BECAUSE something looked wrong) still needs their existing
    orders and positions managed, not abandoned.

    True if the user has ANY of:
      - an overnight_orders entry in a non-terminal lifecycle_state
        (order_lifecycle.is_transitional) - covers ordinary transitional
        entries, UNKNOWN_SUBMISSION_STATE, MANUAL_LINK_IN_PROGRESS, AND a
        fully-protected position that might still exit
        (PROTECTION_CONFIRMED_ACTIVE is deliberately non-terminal);
      - a tracked resting exit order (webull_stop_orders.tracked_tickers) -
        covers a position whose overnight_orders record might be stale or
        missing but still has a real order resting at the broker;
      - an incomplete manual resolution (find_incomplete_resolutions) - a
        resolution transaction whose closing audit write never durably
        landed.
    Deliberately does NOT check autonomy mode at all - _run_autonomous_trade_scan_locked's
    OWN new-entry gate is completely unaffected by this function; this only
    decides whether the FAST MONITOR (reconciliation/resumption only,
    never a new entry) has anything to do for this user this tick."""
    if any(ol.is_transitional(order) for order in list_overnight_orders(user_id)):
        return True
    if webull_tracked_tickers(user_id):
        return True
    if find_incomplete_resolutions(user_id):
        return True
    return False


def _run_fast_order_monitor(user_id: str) -> Dict[str, object]:
    """The FAST per-order monitor tick - task list: "Build fast per-order
    monitor decoupled from the 5-minute scan". Meant to be called on a
    MUCH shorter cadence than the full autonomous scan (e.g. every 30-60
    seconds, via a separate, more-frequent Render Cron Job hitting
    /api/autonomy/fast-monitor-trigger - see that route) so an ordinary
    transitional entry (order_lifecycle.MONITOR_RESUMABLE_STATES) doesn't
    sit unresumed or unprotected for up to 5 minutes after a crash or a
    slow fill.

    Deliberately does NOT run _refresh_stop_confidence or any
    new-candidate market-scanning/sizing work - only the reconciliation/
    resumption passes every entry (ambiguous, manually-resolved, or
    ordinary) might need:
      - _discover_orphaned_broker_entries: a broker-accepted entry with
        ZERO local trace at all (see that function). Deliberately called
        here TOO, not only from the full 5-minute scan - local state
        alone cannot identify a missing local write (there is nothing
        local to key off of), so relying solely on
        _user_needs_fast_monitor_pass's local-only gate (see the
        endpoint below, which now runs this function for every
        Webull-configured user regardless of that gate) would mean an
        orphan is invisible to the FAST monitor entirely, only ever
        caught by the slower 5-minute scan - or never, if that
        scheduler's own cron job is misconfigured or autonomy is OFF and
        nothing else calls it. Runs regardless of autonomy mode, exactly
        like every other call in this function;
      - _reconcile_exit_orders: sibling-leg cancellation (a position that
        closed because one exit leg filled must not leave the other
        resting against shares that no longer exist) and stop-refresh
        retry;
      - _reconcile_unknown_submissions: UNKNOWN_SUBMISSION_STATE;
      - _recover_incomplete_manual_resolutions: MANUAL_LINK_IN_PROGRESS /
        incomplete manual resolutions;
      - _monitor_transitional_orders: every ordinary entry from
        ENTRY_SUBMITTED through PROTECTION_FAILED - the fast monitor's
        own core purpose.
    That narrow scope is what makes this cheap enough to run this often -
    no market data pull, no candidate sizing, no new broker orders except
    the protective legs these reconciliation passes themselves place (and
    orphan discovery's own get_order_history call, which is read-only).

    Holds the SAME per-user lock (scan_lock.user_scan_lock) the full scan
    uses - both mutate the same overnight_orders.json, so a fast tick must
    never race a concurrent full scan (or another fast tick) for the same
    user. Raises ScanAlreadyRunningError if one is already in flight (the
    caller treats this as a benign skip, same as the full scan's own
    cron-trigger endpoint already does) rather than queueing - a missed
    tick is fine given how frequently this is meant to run again.

    Raises ValidationError if Webull isn't configured/connected or no
    sandbox account can be found - same reasoning as
    _run_autonomous_trade_scan_locked's own setup: a caller (the
    fast-monitor-trigger endpoint) decides how to surface that per user."""
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Webull is not configured for this user.")
    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("Webull is not connected for this user.")
    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for this user's credentials.")
    account_id = cash_account["account_id"]
    # See _run_autonomous_trade_scan_locked's own comment on the identical
    # margin lookup - None is the normal case for a user with no margin
    # account provisioned, not an error.
    margin_account = webull_api.find_individual_margin_account(sandbox_accounts)
    margin_account_id = margin_account["account_id"] if margin_account else None

    entries_checked_before = sum(1 for order in list_overnight_orders(user_id) if ol.is_transitional(order))

    with user_scan_lock(user_id):
        _discover_orphaned_broker_entries(user_id, creds, account_id)
        _reconcile_exit_orders(user_id, creds, account_id)
        has_unresolved_ambiguous_submission = _reconcile_unknown_submissions(user_id, creds, account_id)
        has_incomplete_manual_resolution = _recover_incomplete_manual_resolutions(user_id, creds, account_id)
        still_transitional = _monitor_transitional_orders(user_id, creds, account_id)
        # THE primary safety loop for a margin/short entry - see
        # _monitor_transitional_orders' own account-filtering docstring.
        # Deliberately narrower than the cash path above (orphan
        # discovery, ambiguous-submission recovery, and the outside-hours
        # stop retry are not yet extended to the margin account) - a
        # known, documented gap, not an oversight; see
        # _run_autonomous_trade_scan_locked's matching comment.
        if margin_account_id:
            still_transitional = _monitor_transitional_orders(user_id, creds, margin_account_id) or still_transitional

    entries_checked_after = sum(1 for order in list_overnight_orders(user_id) if ol.is_transitional(order))

    return {
        "has_unresolved_ambiguous_submission": has_unresolved_ambiguous_submission,
        "has_incomplete_manual_resolution": has_incomplete_manual_resolution,
        "still_transitional": still_transitional,
        # entries_checked is the count BEFORE this pass (how many this
        # invocation actually attempted to do something with) -
        # entries_checked_after is included too since a resolved-and-closed
        # entry disappears from the transitional count without having been
        # "not checked".
        "entries_checked": entries_checked_before,
        "still_transitional_count": entries_checked_after,
    }


def _submit_and_protect_entry(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    requested_quantity: int,
    limit_price: float,
    stop_price: float,
    target_price: float,
    trading_day: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """Submits the entry order and drives it through the full lifecycle -
    fill confirmation, then protection sized to the ACTUAL filled quantity,
    then confirmation that both protective legs are genuinely resting -
    before returning. Mutates and returns `entry` (the caller's
    overnight_orders record) with the final lifecycle_state, so a caller
    checking entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE knows
    the position is genuinely covered, not just that a placement call once
    returned success ("protection attempted" is never reported as
    "protection confirmed active" - see order_lifecycle.py).

    Bounded polling (ENTRY_FILL_POLL_ATTEMPTS / PROTECTION_CONFIRM_POLL_ATTEMPTS)
    means this can return with the order still in a transitional state
    (entry_submitted, protection_pending, protection_failed) instead of
    blocking indefinitely - _monitor_transitional_orders is what keeps
    checking after this function returns, so one slow-to-fill ticker can't
    stall the rest of the candidate batch.

    The initial placement call's exception, if any, is deliberately split
    two ways rather than treated uniformly as failure - using the explicit
    webull_api.DefiniteOrderRejection / webull_api.AmbiguousOrderSubmission
    classification (integrations/webull.py), not exception class alone.
    Exception TYPE by itself never proved whether a request reached
    Webull - _place_order_with_retry now parses the actual ServerException
    fields (error_code, http_status) to decide: only a well-formed, PARSED
    broker rejection (a real error_code, paired with an HTTP status that
    isn't auth/rate-limit/server-side) raises DefiniteOrderRejection.
    Everything else - timeouts, dropped connections, SDK exceptions,
    unparseable response bodies, 401/403/429/5xx - raises
    AmbiguousOrderSubmission, the fail-safe default. Only
    DefiniteOrderRejection is treated as a definite ENTRY_FAILED;
    AmbiguousOrderSubmission (and, as a final fail-safe, any exception type
    this classification scheme doesn't recognize) goes to
    UNKNOWN_SUBMISSION_STATE instead, specifically so the caller reserves
    capital for it and doesn't silently retry/duplicate an order that might
    already be resting at the broker - see _reconcile_unknown_submission for
    how this eventually gets resolved."""
    entry_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "entry", attempt=1)
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)

    # direction="short" (PUT) opens with a SELL (short-to-open) instead of
    # a BUY - see find_individual_margin_account/the 2026-09-02 short-
    # selling work. Defaults to "long" for every pre-existing entry that
    # never set this field, so this is a pure addition, not a behavior
    # change for the proven long path.
    entry_side = "SELL" if entry.get("direction") == "short" else "BUY"
    try:
        webull_api.place_stock_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            side=entry_side,
            quantity=requested_quantity,
            limit_price=limit_price,
            trading_session=_current_webull_trading_session(),  # candidates are already restricted to CORE hours by _new_entries_allowed
            client_order_id=entry_client_order_id,
        )
    except webull_api.DefiniteOrderRejection as error:
        # A well-formed, parsed broker rejection (see docstring) - the
        # order definitely never went through.
        ol.transition(entry, ol.ENTRY_FAILED, error=str(error))
        return entry
    except Exception as error:  # noqa: BLE001 - AmbiguousOrderSubmission, or anything else not explicitly classified - fail-safe default, see docstring
        ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error=str(error))
        return entry

    return _poll_fill_and_protect(
        user_id=user_id,
        creds=creds,
        account_id=account_id,
        ticker=ticker,
        entry_client_order_id=entry_client_order_id,
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=target_price,
        trading_day=trading_day,
        entry=entry,
    )


def _reconcile_protective_leg_quantity(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
    leg: str,
    target_quantity: float,
    leg_price: float,
) -> None:
    """Resizes ONE protective leg ("stop" or "target") to target_quantity -
    growing it (more of the entry order filled since protection was last
    sized) or shrinking it (the position partially exited through the
    OTHER leg - see _reconcile_position_exit).

    Deliberately does NOT assume replaying the same deterministic
    client_order_id resizes a resting order - Webull's own duplicate-order
    guard (_place_order_with_retry's 417/OAUTH_OPENAPI_TRADE_PLACE_ORDER_REPEAT
    handling) just returns the EXISTING, UNCHANGED order on a repeat
    client_order_id; it does not modify its quantity. A genuine
    replace/modify endpoint does exist in the vendored SDK
    (webull.trade.trade.v2.order_operation_v2.OrderOperationV2.replace_order,
    POST /openapi/trade/stock/order/replace) but that entire v2 class is
    marked "Deprecated - use OrderOperationV3" in the SDK's own source,
    and neither v2 nor v3's replace behavior for a quantity-only resize
    has ever been empirically confirmed against the live sandbox the way
    place/cancel/get_order_detail already have elsewhere in this app (see
    the allowlist-not-assumption discipline in integrations/webull.py's
    _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES for why this app does not
    build on unverified broker behavior). This uses an explicit
    cancel-confirm-replace sequence instead, under a NEW, VERSIONED
    deterministic client_order_id each time
    (order_lifecycle.deterministic_client_order_id's own `attempt`
    parameter) - a genuinely different order, never a same-id resubmission.

    NEVER drops tracking (webull_stop_orders.record_exit_order /
    pop_exit_order_by_id) of the OLD leg until its cancellation is
    DURABLY CONFIRMED - re-checked via a fresh get_order_detail call after
    the cancel, not assumed from the cancel call merely not raising. If
    the old leg turns out to have already FILLED (it executed before the
    cancel could land, or during the gap between cancelling and
    re-checking), this aborts the resize for this leg entirely and alerts
    - a filled leg means shares already left through it, so blindly
    placing a new leg for the full target_quantity would over-protect a
    position that's already partially exited; that scenario is instead
    left for _reconcile_position_exit to pick up as a genuine exit on its
    own next pass.

    Raises on any unresolved step (broker unreachable, cancel fails,
    cancellation unconfirmed, new placement fails) rather than swallowing
    - the caller is responsible for recording that as a failed monitor
    attempt (see monitor_last_error/monitor_attempt_count in
    _monitor_transitional_orders) and retrying on a later pass. This
    function is itself safe to simply call again after any such failure -
    every step re-checks current broker state rather than trusting
    anything left over from a prior, incomplete attempt.

    leg == "target" is a deliberate, permanent no-op (2026-08-31) - see
    _check_and_execute_target_exit's own docstring for the full evidence.
    Placing the target as a second independent resting SELL order was
    empirically confirmed to get rejected by Webull once a stop leg is
    already resting (HTTP 417, OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION -
    a real SLB entry hit this live), and a follow-up preview_order
    diagnostic ruled out every broker-native combo/bracket order_type this
    account supports (git history: commits d5c3c1a/7574f6c/38a0c8b) - only
    "NORMAL" works, which is exactly the independent-order shape that
    caused the rejection in the first place. The target price is still
    tracked on the entry and still enforced - just watched and executed by
    this app's own monitor instead of resting at the broker.

    For leg == "target" specifically: if this entry has no
    target_client_order_id, this is simply a no-op (the normal, current-
    scheme case - there was never a broker-side target leg to manage). If
    it DOES have one (a legacy entry from before 2026-08-31 whose target
    somehow got placed before this app stopped attempting it), this app
    can no longer resize it correctly - so the first time a resize would
    otherwise have been needed, this cancels-and-confirms that stale leg
    and drops its tracking instead, migrating the entry onto the new
    app-monitored scheme rather than leaving a target resting at the wrong
    (stale) quantity forever. Cancelling a take-profit leg is not a safety
    risk - it only forfeits upside capture until _check_and_execute_target_exit
    takes over watching price, unlike ever cancelling a stop."""
    if leg == "target":
        old_target_id = entry.get("target_client_order_id")
        if not old_target_id:
            return
        try:
            old_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, old_target_id)
            old_status = ol.summarize_fill(old_detail)["status"]
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"could not confirm the legacy target leg's status before migrating {ticker} off it: {error}") from error
        if old_status == "FILLED":
            # A genuine target exit via the old broker-order path, not a
            # migration case - _reconcile_position_exit's own broker-fill
            # detection handles this; leave it fully alone.
            return
        if _protective_leg_is_active(old_status):
            try:
                webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, old_target_id)
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not cancel the legacy target leg while migrating {ticker} off it: {error}") from error
            try:
                recheck_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, old_target_id)
                recheck_status = ol.summarize_fill(recheck_detail)["status"]
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not confirm the legacy target leg's cancellation while migrating {ticker} off it: {error}") from error
            if recheck_status == "FILLED":
                return  # filled during the cancel race - a genuine exit, not a migration case; leave it alone
            if _protective_leg_is_active(recheck_status):
                raise RuntimeError(f"legacy target leg cancellation not yet confirmed for {ticker} - will retry")
        # Confirmed CANCELLED/FAILED, or was never active to begin with -
        # durably gone. Dropping tracking here makes target_confirmed's own
        # check (see _confirm_and_finalize_protection) treat this entry the
        # same as any other new-scheme entry from here on.
        entry["target_client_order_id"] = None
        entry["target_leg_quantity"] = None
        pop_exit_order_by_id(user_id, ticker, old_target_id)
        return
    if leg_price <= 0:
        # A genuinely absent leg - e.g. no take-profit configured for this
        # setup - is a normal, expected condition, not a failure to raise
        # and retry. Matches _confirm_and_finalize_protection's own
        # asymmetric treatment: a missing STOP still blocks
        # PROTECTION_CONFIRMED_ACTIVE (safety) - a target's protection
        # requirement is unconditionally satisfied there now (see its own
        # comment), since there's no broker leg to confirm either way.
        entry[f"{leg}_order_error"] = f"no {leg} price computed for this setup"
        return
    current_leg_quantity = entry.get(f"{leg}_leg_quantity")
    if current_leg_quantity == target_quantity:
        return  # already correctly sized - nothing to do

    old_client_order_id = entry.get(f"{leg}_client_order_id")
    if old_client_order_id:
        try:
            old_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, old_client_order_id)
            old_status = ol.summarize_fill(old_detail)["status"]
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"could not confirm the existing {leg} leg's status before resizing: {error}") from error

        if old_status == "FILLED":
            entry[f"{leg}_order_error"] = (
                f"the previous {leg} leg filled before it could be resized - the position may have partially "
                "exited through it; not placing a new leg automatically"
            )
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "protective_leg_filled_during_resize",
                        "ticker": ticker,
                        "message": (
                            f"{ticker}: the {leg} leg (client_order_id {old_client_order_id}) filled before this "
                            "app could resize it for a larger position. Review this position manually - it may "
                            "have partially exited through this leg."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 - never let alerting itself break reconciliation
                pass
            raise RuntimeError(f"{leg} leg already filled during resize attempt - see _reconcile_position_exit")

        if _protective_leg_is_active(old_status):
            try:
                webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, old_client_order_id)
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not cancel the existing {leg} leg for resizing: {error}") from error
            try:
                recheck_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, old_client_order_id)
                recheck_status = ol.summarize_fill(recheck_detail)["status"]
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not confirm the {leg} leg's cancellation before resizing: {error}") from error
            if recheck_status == "FILLED":
                entry[f"{leg}_order_error"] = f"the {leg} leg filled during cancellation - not replacing automatically"
                raise RuntimeError(f"{leg} leg filled during cancel-confirm - see _reconcile_position_exit")
            if _protective_leg_is_active(recheck_status):
                raise RuntimeError(f"the {leg} leg's cancellation has not been confirmed yet - will retry next pass")
            old_status = recheck_status
        # old_status is now CANCELLED or FAILED - confirmed gone, safe to replace.

    next_attempt = int(entry.get(f"{leg}_leg_attempt") or 0) + 1
    new_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, leg, attempt=next_attempt)

    def _place_new_leg() -> None:
        if leg == "stop":
            # direction="short" protects with a BUY-side stop (a "buy-
            # stop" that covers on a price RISE) instead of the SELL-side
            # stop that protects a long - verified live via
            # preview_raw_order against the margin sandbox account before
            # being wired in here.
            stop_side = "BUY" if entry.get("direction") == "short" else "SELL"
            webull_api.place_stop_loss_order(
                app_key=creds["app_key"],
                app_secret=creds["app_secret"],
                account_id=account_id,
                symbol=ticker,
                quantity=target_quantity,
                stop_price=leg_price,
                client_order_id=new_client_order_id,
                side=stop_side,
            )
        else:
            webull_api.place_take_profit_order(
                app_key=creds["app_key"],
                app_secret=creds["app_secret"],
                account_id=account_id,
                symbol=ticker,
                quantity=target_quantity,
                target_price=leg_price,
                trading_session=_current_webull_trading_session(),
                client_order_id=new_client_order_id,
            )

    placement_error: Optional[BaseException] = None
    # ONE immediate in-function retry before giving up - the old leg is
    # already confirmed gone at this point (cancel-confirm already
    # happened above), so the position is genuinely unprotected on THIS
    # leg for as long as this takes; a single immediate retry (not a
    # bounded sleep-loop - the caller's own next monitor tick already
    # provides that) meaningfully shrinks that window for a transient
    # placement failure without materially delaying the failure-tracking
    # below for a persistent one.
    for placement_attempt in range(2):
        try:
            _place_new_leg()
            placement_error = None
            break
        except Exception as error:  # noqa: BLE001
            placement_error = error

    if placement_error is not None:
        entry[f"{leg}_order_error"] = str(placement_error)
        if old_client_order_id:
            # The OLD leg was already cancelled-and-confirmed above (that's
            # a precondition for reaching this placement call at all) - so
            # this specific failure mode means the position is ACTUALLY,
            # CURRENTLY missing protection on this leg, not merely
            # retrying a resize that hasn't started yet. Persisted, not
            # just alerted: entry[f"{leg}_protection_gap"] = True is an
            # IMMEDIATE, tick-granular freeze signal (see
            # _has_active_protection_gap_locally) - the account stays
            # frozen for every subsequent monitor pass (the ~60s fast
            # monitor keeps retrying this same placement via its own
            # normal resumable-state handling - see
            # order_lifecycle.MONITOR_RESUMABLE_STATES) until this exact
            # flag is cleared, which only happens in
            # _confirm_and_finalize_protection once this leg is genuinely
            # RE-confirmed active - never merely because a retry was
            # attempted, and never on a timer.
            entry[f"{leg}_protection_gap"] = True
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "resize_replacement_failed_after_cancel",
                        "ticker": ticker,
                        "priority": "critical",
                        "message": (
                            f"{ticker}: the {leg} leg was cancelled to resize it to {target_quantity:g} shares, but "
                            f"placing its replacement failed even after an immediate retry ({placement_error}). "
                            f"This leg is genuinely UNPROTECTED right now. The old tracked order (client_order_id "
                            f"{old_client_order_id}) is retained - not dropped. New autonomous entries are frozen "
                            "for this account until this leg is confirmed protected again. This will keep "
                            "retrying every monitor pass; investigate immediately if it does not recover."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        raise placement_error

    record_exit_order(user_id, ticker, new_client_order_id, "stop" if leg == "stop" else "take_profit")
    if old_client_order_id:
        # Only NOW, after the new leg is durably placed AND tracked, drop
        # tracking of the old one - it's confirmed gone (checked above),
        # and the new one is already tracked, so there is never a moment
        # with zero tracked orders for this leg type.
        pop_exit_order_by_id(user_id, ticker, old_client_order_id)

    entry[f"{leg}_client_order_id"] = new_client_order_id
    entry[f"{leg}_leg_attempt"] = next_attempt
    entry[f"{leg}_leg_quantity"] = target_quantity
    entry[f"{leg}_order_error"] = None
    # Deliberately NOT cleared here - placement succeeding is not the same
    # as confirmed active (this whole app's founding distinction). See
    # _confirm_and_finalize_protection for where entry[f"{leg}_protection_gap"]
    # actually gets cleared, once this leg is genuinely re-confirmed.


def _confirm_and_finalize_protection(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry: Dict[str, object],
    filled_quantity: float,
    stop_price: float,
    target_price: float,
) -> None:
    """Shared by _reconcile_entry_fill_and_protection (after an initial
    placement or a growth-resize) and _reconcile_position_exit (after
    resizing the sibling leg down following a partial exit) - polls each
    leg's REAL broker status before declaring protection confirmed
    (placement succeeding is not the same as confirmed active), then
    transitions to PROTECTION_CONFIRMED_ACTIVE or PROTECTION_FAILED and
    alerts on failure. A missing or failed stop is never treated as
    "fine" (unlike a missing target, which only forfeits upside capture,
    not safety) - that asymmetry is exactly what this whole mechanism
    exists to enforce. A leg whose resize did NOT complete this pass
    (`{leg}_leg_quantity` still doesn't match filled_quantity) is never
    checked for confirmation either - there is nothing at the right size
    to confirm yet, so it counts as unconfirmed by construction, not by
    an extra check.

    Requires entry.lifecycle_state to already be PROTECTION_PENDING - both
    callers ensure this before calling."""
    stop_client_order_id = entry.get("stop_client_order_id")
    target_client_order_id = entry.get("target_client_order_id")

    stop_confirmed = False
    stop_confirmed_dead = False  # a real, successful lookup that came back CANCELLED/FAILED - not a mere unconfirmed timeout
    # target_confirmed is unconditionally satisfied unless this is a
    # legacy entry that genuinely has a target_client_order_id from before
    # 2026-08-31 (see _reconcile_protective_leg_quantity's own comment) -
    # a target is never placed as a broker order anymore, so there is
    # nothing to confirm here; _check_and_execute_target_exit is what
    # actually watches and enforces it now.
    target_confirmed = target_price <= 0 or not target_client_order_id
    for attempt in range(PROTECTION_CONFIRM_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(PROTECTION_CONFIRM_POLL_INTERVAL_SECONDS)
        if stop_client_order_id and not stop_confirmed and entry.get("stop_leg_quantity") == filled_quantity:
            try:
                stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
                stop_status = ol.summarize_fill(stop_detail)["status"]
                stop_confirmed = _protective_leg_is_active(stop_status)
                stop_confirmed_dead = stop_status in ("CANCELLED", "FAILED")
            except Exception:  # noqa: BLE001 - transient lookup failure, try again next attempt
                pass
        if target_client_order_id and not target_confirmed and entry.get("target_leg_quantity") == filled_quantity:
            try:
                target_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, target_client_order_id)
                target_confirmed = _protective_leg_is_active(ol.summarize_fill(target_detail)["status"])
            except Exception:  # noqa: BLE001 - transient lookup failure, try again next attempt
                pass
        if stop_confirmed and target_confirmed:
            break

    # Clears entry[f"{leg}_protection_gap"] (see _reconcile_protective_leg_quantity)
    # exactly when - and only when - that leg is genuinely RE-confirmed
    # here, never merely because a placement attempt was made. Whichever
    # leg stays unconfirmed keeps its gap flag set (if it had one),
    # keeping the account frozen via _has_active_protection_gap_locally.
    if stop_confirmed:
        entry["stop_protection_gap"] = None
    if target_confirmed:
        entry["target_protection_gap"] = None

    stop_leg_needs_replacement = False
    if not stop_confirmed and stop_confirmed_dead:
        # Found live 2026-08-31: a real stop leg ended up CANCELLED at the
        # broker (by some means outside this app's own resize/exit paths -
        # the exact cause was never confirmed, only the resulting state)
        # while its tracked quantity never changed. _reconcile_protective_leg_quantity's
        # own "already correctly sized" short-circuit compares quantity
        # only, never broker status, so nothing was ever re-placing it -
        # this entry polled the same dead order every single monitor pass
        # (203+ attempts, 4+ hours) without ever attempting a replacement.
        # Clearing stop_leg_quantity here (NOT stop_client_order_id - the
        # old, now-confirmed-dead id is still needed so the next resize's
        # own cancel-confirm-replace sequence can look it up and correctly
        # skip straight to replacing it) makes the quantity no longer
        # "already correct" on the NEXT pass, which is exactly what's
        # needed to make _reconcile_protective_leg_quantity actually place
        # a fresh stop instead of silently polling a dead one forever.
        entry["stop_leg_quantity"] = None
        stop_leg_needs_replacement = True

    if stop_confirmed and target_confirmed:
        ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE, protection_confirmed_at=_now_utc().isoformat())
    else:
        ol.transition(
            entry,
            ol.PROTECTION_FAILED,
            error=(
                f"could not confirm protection active within {PROTECTION_CONFIRM_POLL_ATTEMPTS} attempts "
                f"(stop_confirmed={stop_confirmed}, target_confirmed={target_confirmed})"
                + (" - stop leg found CANCELLED/FAILED at the broker; will place a fresh one next pass" if stop_leg_needs_replacement else "")
            ),
        )
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "protection_failed",
                    "ticker": ticker,
                    "message": (
                        f"{ticker}: entry filled ({filled_quantity:g} shares) but protection could not be confirmed "
                        f"active (stop_confirmed={stop_confirmed}, target_confirmed={target_confirmed}). Retrying "
                        f"automatically - check the position manually if this persists."
                    ),
                },
            )
        except Exception:  # noqa: BLE001 - never let alerting itself break the scan
            pass


def _flag_ambiguous_exit_unresolved(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry: Dict[str, object],
    evidence_summary: str,
) -> None:
    """Shared by _reconcile_both_legs_filled_emergency and the
    sibling-also-filled-during-cancel-confirm case in
    _reconcile_position_exit - both are the SAME underlying race (two
    protective legs executing against one position), just detected at a
    different moment (up front vs. mid-cancel). Persists durable evidence
    (including a best-effort broker position snapshot) directly on the
    entry record, sets the immediate freeze flag
    (entry["ambiguous_exit_unresolved"] - see
    _has_active_protection_gap_locally), and fires a critical alert.
    Does NOT place any corrective order - see
    _reconcile_both_legs_filled_emergency's docstring for why automatic
    covering is disabled. Does NOT raise - each caller raises its own,
    more specifically-worded RuntimeError afterward."""
    position_snapshot: Optional[Dict[str, object]] = None
    position_lookup_error: Optional[str] = None
    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
        position = next((p for p in positions if str(p.get("symbol", "")).upper() == ticker.upper()), None)
        position_snapshot = position or {"symbol": ticker, "quantity": 0, "note": "no matching position row returned by the broker"}
    except Exception as error:  # noqa: BLE001
        position_lookup_error = str(error)

    entry["ambiguous_exit_unresolved"] = True
    entry["ambiguous_exit_evidence"] = {
        "evidence_summary": evidence_summary,
        "broker_position_snapshot": position_snapshot,
        "position_lookup_error": position_lookup_error,
        "recorded_at": _now_utc().isoformat(),
    }

    try:
        add_manual_alert(
            user_id,
            {
                "type": "ambiguous_exit_both_legs_filled",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker}: {evidence_summary} - this app's independent (non-OCO) leg design cannot prevent "
                    "this race. "
                    + (
                        f"Actual broker position is {position_snapshot.get('quantity')} shares."
                        if position_snapshot is not None
                        else f"Broker position lookup also failed: {position_lookup_error}."
                    )
                    + " AUTOMATIC CORRECTIVE TRADING IS DISABLED until Webull's short-position response schema is "
                    "empirically confirmed - no order has been placed. New autonomous entries are frozen for this "
                    "account immediately. Review the actual broker position and act manually if needed; this "
                    "reconciles again with fresh evidence every monitor pass."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _reconcile_both_legs_filled_emergency(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
    stop_filled_quantity: float,
    target_filled_quantity: float,
) -> None:
    """Called when BOTH the stop and target legs report a fill - a race
    this app's own protection design cannot structurally prevent, since
    the stop-loss and take-profit are placed as two INDEPENDENT orders,
    not a broker-native OCO/OTOCO bracket. (The vendored Webull SDK does
    expose OCO/OTOCO combo-order wire format - webull.trade.common.combo_type.ComboType.OCO/OTOCO,
    plumbed through PlaceOrderRequest.client_combo_order_id - and its
    docstring lists Webull US as supported. This app deliberately does
    NOT use it: that combo wire format has never been empirically
    verified against this app's actual sandbox account/API version,
    matching the same allowlist-not-assumption discipline applied to
    every other unverified broker behavior in this codebase - see
    _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES in integrations/webull.py
    for the same principle applied elsewhere. Building on an unverified
    combo format could silently fail to link the legs at all while
    LOOKING like it worked, which is worse than today's known, tested gap.
    This function exists precisely to make that known gap safe rather
    than merely detected - see its behavior below - until OCO/OTOCO is
    controlled-sandbox-verified and can be adopted with confidence.)

    AUTOMATIC CORRECTIVE TRADING IS DELIBERATELY DISABLED (explicit
    reviewer instruction) - an earlier version of this function placed a
    marketable BUY to cover an apparent short automatically. That was
    reverted: Webull's short-position representation in
    get_account_positions (sign convention, or a separate side field) has
    never been empirically observed live, so a stale or misread position
    response could make this app BUY shares it doesn't actually need to,
    creating a brand-new unwanted long position - a worse outcome than
    the ambiguity itself. Before any automatic covering is reintroduced,
    the corrective order needs everything a normal entry already has
    (deterministic client_order_id, ambiguous-submission-style
    classification of its own placement result, durable audit trail,
    restart recovery, and a post-order re-check that the account is
    ACTUALLY flat - not just "an order was placed") AND Webull's short
    schema needs to be confirmed via a controlled, human-approved sandbox
    observation first.

    Instead, this function now only:
      - best-effort queries the broker's actual position for the ticker,
        purely as EVIDENCE (a failed lookup doesn't block anything below -
        it's recorded as evidence too);
      - persists that evidence DURABLY on the entry record itself
        (overnight_orders.json's existing atomic+locked storage) under
        entry["ambiguous_exit_evidence"], refreshed on every attempt -
        not a one-shot snapshot, since this function is called again
        every monitor tick as long as the entry stays non-terminal;
      - sets entry["ambiguous_exit_unresolved"] = True - an EXPLICIT,
        IMMEDIATE local freeze signal (see _has_active_protection_gap_locally)
        that blocks new entries for this account starting THIS tick, not
        after MONITOR_STUCK_FREEZE_SECONDS like an ordinary stall would.
        Only cleared if a LATER tick's fresh evidence turns out
        conclusive after all (_reconcile_position_exit's normal
        single-leg path runs instead, and this function is never
        reached) - there is no dedicated admin action to manually clear
        it yet; reviewing the alert/evidence and managing the position
        directly at the broker is the current expectation.
      - fires a CRITICAL alert with the evidence, explicitly stating no
        corrective order was placed;
      - always still raises, so the caller's own failure-tracking
        (monitor_first_failure_at/monitor_attempt_count) also applies on
        top of the immediate flag-based freeze, and this entry keeps
        being re-examined with fresh evidence every subsequent tick
        rather than being a one-shot dead end."""
    evidence_summary = f"both legs show a fill - stop leg filled {stop_filled_quantity:g}, target leg filled {target_filled_quantity:g}"
    _flag_ambiguous_exit_unresolved(user_id, creds, account_id, ticker, entry, evidence_summary)
    raise RuntimeError(f"{ticker}: {evidence_summary} - frozen pending manual review, no automatic corrective action taken")


def _check_and_execute_target_exit(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
) -> bool:
    """The target leg's real exit mechanism, since 2026-08-31 - see
    _reconcile_protective_leg_quantity's own comment for the full evidence
    trail (a real SLB entry's take-profit leg was rejected by Webull as an
    attempted position reversal, and a follow-up preview_order diagnostic
    ruled out every broker-native combo/bracket order_type this account
    supports). The target is never placed as a resting broker order
    anymore - this function is what watches a fresh price against it and
    ACTIVELY executes the exit when reached, called once per monitor pass
    for a PROTECTION_CONFIRMED_ACTIVE entry, before the passive
    broker-fill-based exit detection in _reconcile_position_exit.

    Only ever acts on a NEW-style entry (no target_client_order_id - see
    _confirm_and_finalize_protection) and only while the stop leg is
    confirmed still resting and unfilled - if the stop has already filled
    (fully or partially), this is a stop-exit, not a target-exit, and
    _reconcile_position_exit's own broker-fill check handles it instead;
    racing a target-exit attempt on top of that would risk exactly the
    both-legs-executed ambiguity _reconcile_both_legs_filled_emergency
    exists to catch.

    Webull will not accept a second independent SELL order while the stop
    is resting (the confirmed root cause), so the stop must be
    cancelled-and-confirmed FIRST - which means the position is genuinely,
    briefly UNPROTECTED between that confirmation and the new sell order
    landing. That window is minimized by placing the sell in the same pass,
    immediately after cancellation is confirmed; if the sell placement
    itself then fails, this immediately attempts to restore a fresh stop
    order at the original stop price as a fallback, rather than leaving the
    position naked until the next monitor pass - and raises either way, so
    the caller's own failed-attempt tracking (monitor_first_failure_at,
    MONITOR_STUCK_FREEZE_SECONDS) applies on top of whatever local recovery
    was possible.

    Returns True only once the exit sell is CONFIRMED FILLED and the trade
    is recorded CLOSED. Fails closed at every earlier step (no fresh price,
    price not yet at target, stop not confirmed cancellable) by simply
    returning False - never guesses, never acts on stale or unconfirmed
    information given this actively places and cancels real orders, unlike
    every other check in this file that only ever reads broker state."""
    target_price = float(entry.get("target") or 0)
    if target_price <= 0 or entry.get("target_client_order_id"):
        return False  # no target configured, or a legacy broker-order-target entry - not this function's job

    stop_client_order_id = entry.get("stop_client_order_id")
    if not stop_client_order_id:
        return False  # nothing resting to reconcile against - shouldn't happen for PROTECTION_CONFIRMED_ACTIVE, fail closed

    stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
    stop_fill = ol.summarize_fill(stop_detail)
    if stop_fill["filled_quantity"] > 0 or not _protective_leg_is_active(stop_fill["status"]):
        return False  # stop already filled or otherwise not actively resting - let the normal exit path handle it

    is_short = entry.get("direction") == "short"
    fresh_price = alpaca_data.get_latest_trade_price(ticker)
    if fresh_price is None:
        return False  # can't confirm the target was reached - fail closed, the resting stop still covers safety
    # direction="short" has its target BELOW entry - reached on a FALL to
    # or past it, the mirror of a long's target being reached on a RISE.
    target_not_yet_reached = (fresh_price > target_price) if is_short else (fresh_price < target_price)
    if target_not_yet_reached:
        return False  # can't confirm the target was reached - fail closed, the resting stop still covers safety

    quantity = float(entry.get("stop_leg_quantity") or entry.get("filled_quantity") or 0)
    if quantity <= 0:
        return False

    try:
        webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"could not cancel the stop leg to execute the target exit for {ticker}: {error}") from error

    try:
        recheck_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
        recheck_fill = ol.summarize_fill(recheck_detail)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"could not confirm the stop leg's cancellation before executing the target exit for {ticker}: {error}") from error
    if recheck_fill["filled_quantity"] > 0:
        # Filled during the cancel race - now a stop-exit (or partial), not
        # a target-exit. Nothing to undo (a fill is final); let
        # _reconcile_position_exit's own broker-fill check pick this up.
        return False
    if _protective_leg_is_active(recheck_fill["status"]):
        raise RuntimeError(f"stop leg cancellation not yet confirmed for {ticker}'s target exit - will retry")

    # Stop is confirmed CANCELLED and never filled - genuinely unprotected
    # right now until the sell below is placed and confirmed filled.
    next_attempt = int(entry.get("target_exit_attempt") or 0) + 1
    entry["target_exit_attempt"] = next_attempt
    sell_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "target_exit", attempt=next_attempt)
    # direction="short" covers with a BUY - a marketable buy limit sits
    # slightly ABOVE fresh_price (the mirror of a long's sell limit
    # sitting slightly below it, to make it marketable on the way down).
    exit_limit_price = round(
        fresh_price * ((1 + TARGET_EXIT_SLIPPAGE_TOLERANCE) if is_short else (1 - TARGET_EXIT_SLIPPAGE_TOLERANCE)), 2
    )
    exit_side = "BUY" if is_short else "SELL"
    try:
        webull_api.place_stock_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            side=exit_side,
            quantity=quantity,
            limit_price=exit_limit_price,
            trading_session=_current_webull_trading_session(),
            client_order_id=sell_client_order_id,
        )
    except Exception as error:  # noqa: BLE001
        restore_error = _restore_fallback_stop_after_failed_target_exit(user_id, creds, account_id, ticker, trading_day, entry, quantity)
        if restore_error is not None:
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "target_exit_left_position_unprotected",
                        "ticker": ticker,
                        "priority": "critical",
                        "message": (
                            f"{ticker}: cancelled the stop leg to execute a target-price exit, but BOTH the exit "
                            f"sell order ({error}) and the fallback stop replacement ({restore_error}) failed. "
                            "This position is genuinely UNPROTECTED right now. Review and act manually immediately."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"target exit sell failed AND fallback stop restoration failed for {ticker}: sell={error}, restore={restore_error}"
            ) from restore_error
        raise RuntimeError(
            f"target exit sell order failed for {ticker} ({error}) - restored the stop leg as a fallback, will retry the target exit next pass"
        )

    filled_this_sell = 0.0
    average_exit_price: Optional[float] = None
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(ENTRY_FILL_POLL_INTERVAL_SECONDS)
        try:
            sell_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, sell_client_order_id)
            sell_fill = ol.summarize_fill(sell_detail)
        except Exception:  # noqa: BLE001 - transient lookup failure, try again next attempt
            continue
        filled_this_sell = sell_fill["filled_quantity"]
        average_exit_price = sell_fill.get("average_price")
        if filled_this_sell >= quantity or _entry_fill_is_final(sell_fill["status"]):
            break

    if filled_this_sell <= 0:
        # The sell is genuinely placed and resting/pending - not a
        # placement failure, so no fallback-stop restoration here (that's
        # only for "the sell attempt itself failed"). The position is
        # still unprotected until this fills; the caller's failed-attempt
        # tracking applies, and the next pass re-checks this same order.
        raise RuntimeError(
            f"target exit sell order for {ticker} has not filled yet after {ENTRY_FILL_POLL_ATTEMPTS} checks "
            "(position is currently UNPROTECTED - no resting stop) - will keep checking"
        )

    trade_id = str(entry.get("entry_client_order_id") or "")
    average_entry_price = entry.get("average_entry_fill_price")
    pnl_complete = average_entry_price is not None and average_exit_price is not None
    # direction="short" profits from a FALL (entry - exit), the mirror of
    # a long's (exit - entry) - opened with a SELL and closed with a BUY,
    # so a lower exit price than entry is the gain, not the loss.
    gross_pnl = (
        round(filled_this_sell * ((average_entry_price - average_exit_price) if is_short else (average_exit_price - average_entry_price)), 2)
        if pnl_complete else None
    )
    closed_record = {
        "ticker": ticker,
        "side": "SELL" if is_short else "BUY",
        "entry_client_order_id": entry.get("entry_client_order_id"),
        "stop_client_order_id": stop_client_order_id,
        "target_client_order_id": sell_client_order_id,
        "requested_quantity": entry.get("quantity"),
        "filled_quantity": quantity,
        "average_entry_price": average_entry_price,
        "exit_type": "target",
        "exited_quantity": filled_this_sell,
        "average_exit_price": average_exit_price,
        "entry_timestamp": entry.get("logged_at"),
        "exit_timestamp": _now_utc().isoformat(),
        "gross_realized_pnl": gross_pnl,
        "fees": None,
        "net_realized_pnl": gross_pnl,
        "pnl_status": "complete" if pnl_complete else "incomplete_missing_fill_price",
        "strategy": entry.get("strategy"),
        "close_reason": "target_exit_executed",
        "broker_evidence": {"exited_leg_status": "app_monitored_target_exit"},
        "reconciled_at": _now_utc().isoformat(),
    }
    record_closed_trade(user_id, trade_id, closed_record)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason="target_exit_executed")
    try:
        if pnl_complete:
            add_manual_alert(
                user_id,
                {
                    "type": "position_closed",
                    "ticker": ticker,
                    "message": f"{ticker}: position closed via target ({filled_this_sell:g} shares). Realized P&L: ${gross_pnl:.2f}.",
                },
            )
        else:
            add_manual_alert(
                user_id,
                {
                    "type": "position_closed_pnl_incomplete",
                    "ticker": ticker,
                    "message": (
                        f"{ticker}: position closed via target ({filled_this_sell:g} shares), but realized P&L "
                        "could not be computed - the broker response didn't report an average fill price for the "
                        "entry and/or the exit sell. Review this trade's actual fill prices manually."
                    ),
                },
            )
    except Exception:  # noqa: BLE001
        pass
    return True


def _restore_fallback_stop_after_failed_target_exit(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
    quantity: float,
) -> Optional[BaseException]:
    """Called only when a target-exit's own sell order placement failed
    AFTER the stop was already cancelled-and-confirmed - the position is
    genuinely naked at that point, and waiting for the next monitor pass to
    notice would leave it that way for longer than necessary. Places a
    fresh stop order (a NEW attempt/client_order_id, matching
    _reconcile_protective_leg_quantity's own cancel-confirm-replace
    discipline - never a same-id resubmission) at the entry's original stop
    price. Returns None on success, the exception on failure - never
    raises itself, so the caller can decide how to report a total loss of
    protection (both the sell AND this restoration failing) distinctly
    from a partial recovery (sell failed, but the stop is back)."""
    try:
        fallback_attempt = int(entry.get("stop_leg_attempt") or 0) + 1
        fallback_stop_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "stop", attempt=fallback_attempt)
        webull_api.place_stop_loss_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            quantity=quantity,
            stop_price=float(entry.get("stop") or 0),
            client_order_id=fallback_stop_id,
            side="BUY" if entry.get("direction") == "short" else "SELL",
        )
    except Exception as error:  # noqa: BLE001
        return error
    entry["stop_client_order_id"] = fallback_stop_id
    entry["stop_leg_attempt"] = fallback_attempt
    entry["stop_leg_quantity"] = quantity
    return None


def _check_and_rearm_dead_stop(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
) -> bool:
    """Closes a real, live gap confirmed empirically 2026-09-03: a resting
    STOP_LOSS order's time_in_force is "DAY" (integrations/webull.py,
    every order type), and while it was confirmed still SUBMITTED
    immediately AT today's market close, a follow-up check about an hour
    later found it had gone CANCELLED - Webull DOES eventually cancel a
    DAY-TIF stop-loss order, just not instantly at the close boundary.

    The gap this closes: once an entry reaches PROTECTION_CONFIRMED_ACTIVE
    with entry_order_terminal=True (a normal, fully-filled, healthy
    position - true for the overwhelming majority of held positions),
    _reconcile_entry_fill_and_protection - the ONLY function that already
    knows how to detect a dead stop and place a fresh one
    (_confirm_and_finalize_protection's stop_confirmed_dead handling) - is
    never called again (see its own docstring: it only re-runs for
    further FILL growth). _reconcile_position_exit only ever looks for a
    genuine FILL on the stop/target legs, never for "the stop order itself
    is simply gone with nothing having triggered it." A position could
    therefore sit genuinely unprotected, indefinitely, with nothing
    noticing - this function is the fix: called from
    _monitor_transitional_orders for exactly this state (PROTECTION_CONFIRMED_ACTIVE,
    entry_order_terminal=True, no exit found this pass).

    Deliberately narrower than the general protective-leg machinery:
      - Skips direction="short" entries - Webull's short-position sign
        convention in get_account_positions has never been empirically
        confirmed, same reason _check_position_absent_while_stuck already
        excludes shorts.
      - Only acts when the stop is confirmed CANCELLED/FAILED with ZERO
        fill (a genuine fill is a real exit, not this function's job -
        _reconcile_position_exit's own passive-fill path handles that) AND
        the broker's live position for this ticker is still > 0 shares (if
        the position is genuinely gone too, this is a position-absent
        case, not a rearm - _check_position_absent_while_active is the
        right handler, not this one).
      - Requires CORE trading hours to place the replacement (same
        constraint place_stop_loss_order/_reconcile_exit_orders already
        work around) - returns False and simply retries on a later tick
        outside CORE hours, exactly like _reconcile_exit_orders' own
        outside-hours stop retry.

    Returns True once a fresh stop is confirmed placed (or nothing needed
    doing - the stop is still genuinely resting); raises if the position
    is confirmed naked and a replacement placement attempt itself fails,
    so the caller's own failed-attempt tracking applies on top and this
    keeps retrying every subsequent tick rather than going silent."""
    stop_client_order_id = entry.get("stop_client_order_id")
    if not stop_client_order_id or entry.get("direction") == "short":
        return False

    try:
        stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
        stop_fill = ol.summarize_fill(stop_detail)
    except Exception:  # noqa: BLE001 - transient lookup failure, try again next tick
        return False

    if stop_fill["filled_quantity"] > 0 or _protective_leg_is_active(stop_fill["status"]):
        return False  # a genuine fill (real exit, handled elsewhere) or still genuinely resting - nothing to rearm

    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    except Exception:  # noqa: BLE001 - transient lookup failure, try again next tick
        return False
    position = next((p for p in positions if str(p.get("symbol", "")).upper() == ticker.upper()), None)
    live_quantity = float(position.get("quantity", 0) or 0) if position else 0.0
    if live_quantity <= 0:
        return False  # position is also gone - a position-absent case, not this function's job

    if _current_webull_trading_session() != "CORE":
        return False  # can't place a stop outside CORE hours - retry next tick, same as _reconcile_exit_orders' own outside-hours retry

    stop_price = float(entry.get("stop") or 0)
    if stop_price <= 0:
        return False

    next_attempt = int(entry.get("stop_rearm_attempt") or 0) + 1
    entry["stop_rearm_attempt"] = next_attempt
    new_stop_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "stop_rearm", attempt=next_attempt)
    try:
        webull_api.place_stop_loss_order(
            app_key=creds["app_key"], app_secret=creds["app_secret"], account_id=account_id,
            symbol=ticker, quantity=live_quantity, stop_price=stop_price,
            client_order_id=new_stop_id,
        )
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            f"{ticker}: protective stop went {stop_fill['status']} while {live_quantity:g} shares are still held, "
            f"AND re-arming a fresh stop also failed - position is genuinely UNPROTECTED right now: {error}"
        ) from error

    entry["stop_client_order_id"] = new_stop_id
    entry["stop_leg_quantity"] = live_quantity
    entry["stop_rearmed_at"] = _now_utc().isoformat()
    entry["stop_rearm_reason"] = f"previous stop showed {stop_fill['status']} with zero fill while {live_quantity:g} shares were still held"
    try:
        add_manual_alert(
            user_id,
            {
                "type": "stop_rearmed",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker}: the protective stop had gone {stop_fill['status']} (likely a DAY-TIF expiration) "
                    f"while {live_quantity:g} shares were still held - a fresh stop was just placed at ${stop_price:.2f} "
                    "to close the gap automatically. Review the position to confirm it looks right."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return True


def _reconcile_position_exit(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
) -> bool:
    """Detects whether a fully-protected position (PROTECTION_CONFIRMED_ACTIVE)
    has EXITED - a stop or target leg filled - and if so, reconciles the
    rest of the lifecycle: cancels the sibling leg (retaining its
    tracking until the cancellation is DURABLY CONFIRMED, never merely
    attempted - see webull_stop_orders.pop_exit_order_by_id), records a
    durable closed-trade entry with realized P&L for a FULL exit, or
    resizes the sibling down to the remaining quantity for a PARTIAL
    exit.

    NEVER infers which exit executed merely because the position
    disappeared from the broker's positions list - see
    _reconcile_exit_orders for that broader, ticker-centric,
    position-absence-triggered sweep, which exists as an ADDITIONAL
    safety net, not the primary signal. This function instead checks
    BOTH the stop and target legs' OWN broker status directly, every
    time it runs, and only acts on what THEY show:
      - neither leg shows any fill -> returns False, nothing to do -
        protection is still genuinely active and untouched;
      - exactly one leg shows a fill (fully or partially) -> conclusive:
        cancel-confirm-remove the sibling, then either close the trade
        out (full exit) or resize the sibling down to the remaining
        quantity (partial exit) - the exited leg's OWN remaining resting
        quantity, if any, needs no action; the broker already handles
        that correctly as part of the SAME order;
      - BOTH legs show a fill -> genuinely ambiguous (a race, or evidence
        this app cannot currently explain) - raises rather than guessing,
        which the caller treats as a failed monitor attempt (the stuck
        timer advances, eventually freezing new entries - "safe recovery
        cannot be proven" is exactly this situation).

    Returns True if an exit was found and handled (fully or partially),
    False if the position is still fully open and protected. Raises on
    any unresolved step (broker lookup failure, sibling cancellation not
    yet confirmed, ambiguous evidence) rather than silently continuing -
    the caller records that as a failed monitor attempt and retries.

    Checks the target FIRST, via _check_and_execute_target_exit (see its
    own docstring - the target is app-monitored now, never a resting
    broker order, so it can never show up in the broker-fill check below).
    Only falls through to that passive stop/target-leg-fill check if no
    target exit was executed this pass - a stop that's already filled is
    correctly picked up there either way, and _check_and_execute_target_exit
    itself declines to act at all once it sees the stop is no longer
    actively resting, so the two paths cannot race each other."""
    if _check_and_execute_target_exit(user_id, creds, account_id, ticker, trading_day, entry):
        return True

    stop_client_order_id = entry.get("stop_client_order_id")
    target_client_order_id = entry.get("target_client_order_id")

    stop_status: Optional[str] = None
    stop_filled_quantity = 0.0
    stop_average_price: Optional[float] = None
    if stop_client_order_id:
        stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
        stop_fill = ol.summarize_fill(stop_detail)
        stop_status = stop_fill["status"]
        stop_filled_quantity = stop_fill["filled_quantity"]
        stop_average_price = stop_fill.get("average_price")

    target_status: Optional[str] = None
    target_filled_quantity = 0.0
    target_average_price: Optional[float] = None
    if target_client_order_id:
        target_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, target_client_order_id)
        target_fill = ol.summarize_fill(target_detail)
        target_status = target_fill["status"]
        target_filled_quantity = target_fill["filled_quantity"]
        target_average_price = target_fill.get("average_price")

    stop_has_exited = stop_status is not None and stop_filled_quantity > 0
    target_has_exited = target_status is not None and target_filled_quantity > 0

    if not stop_has_exited and not target_has_exited:
        # Neither leg's OWN status explains an exit - the normal, primary
        # conclusion is "still open". But check whether the position is
        # genuinely gone anyway (closed directly at the broker, outside
        # any tracked leg - see _check_position_absent_while_active's own
        # docstring for the real incident this covers) before returning.
        # Never auto-closes; only flags for admin resolution.
        _check_position_absent_while_active(user_id, creds, account_id, ticker, entry)
        return False  # still fully open and protected (or flagged for review) - nothing more to do this pass

    if stop_has_exited and target_has_exited:
        _reconcile_both_legs_filled_emergency(
            user_id, creds, account_id, ticker, trading_day, entry, stop_filled_quantity, target_filled_quantity,
        )
        # _reconcile_both_legs_filled_emergency always raises - see its own
        # docstring for why (makes the immediate short-covering risk safe,
        # then still surfaces this as an unresolved failed monitor attempt
        # rather than ever silently resolving it here).

    # Reaching here means EXACTLY one leg shows exited this tick (the
    # both-legs-exited branch above always raises) - a conclusive,
    # single-leg read. Clears a PRIOR tick's ambiguous_exit_unresolved
    # flag, if one was set (a transient double-read that turned out not
    # to be a genuine race after all) - a stale flag here would freeze
    # the account forever over a resolved condition.
    entry["ambiguous_exit_unresolved"] = None

    exited_leg = "stop" if stop_has_exited else "target"
    sibling_leg = "target" if exited_leg == "stop" else "stop"
    exited_quantity = stop_filled_quantity if exited_leg == "stop" else target_filled_quantity
    exited_status = stop_status if exited_leg == "stop" else target_status
    # Broker-reported actual average fill price for the leg that exited -
    # NEVER the planned/proposed stop or target price (entry.get("stop")/
    # entry.get("target")) standing in as if it were real. None if the
    # broker response didn't include a recognized price field this poll -
    # the full-exit branch below must then mark P&L incomplete rather than
    # silently computing it from a proposed price. See
    # order_lifecycle.summarize_fill/_FILL_PRICE_FIELD_CANDIDATES.
    exited_average_price = stop_average_price if exited_leg == "stop" else target_average_price

    protected_quantity = float(entry.get(f"{exited_leg}_leg_quantity") or entry.get("filled_quantity") or 0)
    remaining_quantity = max(0.0, protected_quantity - exited_quantity)

    sibling_client_order_id = target_client_order_id if exited_leg == "stop" else stop_client_order_id
    sibling_current_status = target_status if exited_leg == "stop" else stop_status

    if sibling_client_order_id:
        if _protective_leg_is_active(sibling_current_status):
            try:
                webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, sibling_client_order_id)
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not cancel the sibling {sibling_leg} leg after a {exited_leg} exit: {error}") from error
            try:
                recheck_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, sibling_client_order_id)
                recheck_status = ol.summarize_fill(recheck_detail)["status"]
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"could not confirm the sibling {sibling_leg} leg's cancellation: {error}") from error
            if recheck_status == "FILLED":
                # Same underlying race as _reconcile_both_legs_filled_emergency,
                # just discovered mid-cancel instead of up front - same
                # treatment: freeze, persist evidence, alert critical, no
                # automatic corrective order.
                evidence_summary = f"the sibling {sibling_leg} leg also filled during cancellation after the {exited_leg} leg exited - both legs executed"
                _flag_ambiguous_exit_unresolved(user_id, creds, account_id, ticker, entry, evidence_summary)
                raise RuntimeError(f"{ticker}: {evidence_summary} - ambiguous double exit, frozen pending manual review")
            if _protective_leg_is_active(recheck_status):
                raise RuntimeError(
                    f"could not confirm the sibling {sibling_leg} leg's cancellation yet - retaining tracking, will retry"
                )
            # confirmed CANCELLED/FAILED - durably gone, safe to drop tracking now.
        pop_exit_order_by_id(user_id, ticker, sibling_client_order_id)

    if remaining_quantity <= 0.0001:
        # FULL exit - record the closed trade and mark the lifecycle CLOSED.
        # P&L is computed ONLY from broker-reported actual average fill
        # prices (entry.get("average_entry_fill_price"), stamped by
        # _reconcile_entry_fill_and_protection on every entry-order poll;
        # exited_average_price, from THIS function's own fresh
        # get_order_detail call on the leg that exited above) - NEVER from
        # the proposed limit_price/stop/target prices this app itself
        # computed when planning the trade. If either is missing (the
        # broker response didn't include a recognized price field this
        # poll - see order_lifecycle._FILL_PRICE_FIELD_CANDIDATES), P&L is
        # left None and pnl_status marks it explicitly incomplete rather
        # than silently estimating it from a planned price that could be
        # meaningfully different from what was actually paid/received.
        trade_id = str(entry.get("entry_client_order_id") or "")
        average_entry_price = entry.get("average_entry_fill_price")
        pnl_complete = average_entry_price is not None and exited_average_price is not None
        is_short = entry.get("direction") == "short"
        # direction="short" profits from a FALL (entry - exit) - the
        # mirror of a long's (exit - entry). The exited leg here is
        # always the stop for a short (its target never rests at the
        # broker - see _check_and_execute_target_exit), and a short's
        # stop is a BUY that fills on a RISE, i.e. a loss.
        gross_pnl = (
            round(exited_quantity * ((average_entry_price - exited_average_price) if is_short else (exited_average_price - average_entry_price)), 2)
            if pnl_complete else None
        )
        closed_record = {
            "ticker": ticker,
            "side": "SELL" if is_short else "BUY",
            "entry_client_order_id": entry.get("entry_client_order_id"),
            "stop_client_order_id": stop_client_order_id,
            "target_client_order_id": target_client_order_id,
            "requested_quantity": entry.get("quantity"),
            "filled_quantity": protected_quantity,
            "average_entry_price": average_entry_price,
            "exit_type": exited_leg,
            "exited_quantity": exited_quantity,
            "average_exit_price": exited_average_price,
            "entry_timestamp": entry.get("logged_at"),
            "exit_timestamp": _now_utc().isoformat(),
            "gross_realized_pnl": gross_pnl,
            "fees": None,
            "net_realized_pnl": gross_pnl,
            "pnl_status": "complete" if pnl_complete else "incomplete_missing_fill_price",
            "strategy": entry.get("strategy"),
            "close_reason": f"{exited_leg}_filled",
            "broker_evidence": {"exited_leg_status": exited_status},
            "reconciled_at": _now_utc().isoformat(),
        }
        record_closed_trade(user_id, trade_id, closed_record)
        ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason=f"{exited_leg}_filled")
        try:
            if pnl_complete:
                add_manual_alert(
                    user_id,
                    {
                        "type": "position_closed",
                        "ticker": ticker,
                        "message": (
                            f"{ticker}: position closed via {exited_leg} ({exited_quantity:g} shares). "
                            f"Realized P&L: ${gross_pnl:.2f}."
                        ),
                    },
                )
            else:
                add_manual_alert(
                    user_id,
                    {
                        "type": "position_closed_pnl_incomplete",
                        "ticker": ticker,
                        "message": (
                            f"{ticker}: position closed via {exited_leg} ({exited_quantity:g} shares), but realized "
                            "P&L could not be computed - the broker response didn't report an average fill price "
                            "for the entry and/or the exit leg. Review this trade's actual fill prices manually; "
                            "the closed-trade record is retained with pnl_status=incomplete_missing_fill_price."
                        ),
                    },
                )
        except Exception:  # noqa: BLE001
            pass
    else:
        # PARTIAL exit - the exited leg's own remaining resting quantity
        # already correctly covers what's left (same order, broker-managed
        # remainder) - only the SIBLING needs an explicit resize down.
        entry[f"{exited_leg}_leg_quantity"] = remaining_quantity
        entry["filled_quantity"] = remaining_quantity
        ol.transition(entry, ol.PROTECTION_PENDING)
        sibling_price = float(entry.get("target") or 0) if sibling_leg == "target" else float(entry.get("stop") or 0)
        _reconcile_protective_leg_quantity(user_id, creds, account_id, ticker, trading_day, entry, sibling_leg, remaining_quantity, sibling_price)
        _confirm_and_finalize_protection(
            user_id, creds, account_id, ticker, entry, remaining_quantity,
            float(entry.get("stop") or 0), float(entry.get("target") or 0),
        )

    return True


def _reconcile_entry_fill_and_protection(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry_client_order_id: str,
    limit_price: float,
    stop_price: float,
    target_price: float,
    trading_day: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """SINGLE PASS (no internal loop or sleep beyond the bounded
    protection-confirmation poll) - the one function responsible for
    keeping protection sized to the entry's ACTUAL, CURRENT cumulative
    filled quantity. Two callers:
      - _poll_fill_and_protect wraps this in a bounded sleep-and-retry
        loop for a fast INITIAL result right after a fresh
        placement/link/ambiguous-submission-reconciliation;
      - _monitor_transitional_orders calls this directly, once per pass,
        for ANY entry whose entry_order_terminal isn't True yet -
        INCLUDING one already at PROTECTION_CONFIRMED_ACTIVE, since a day
        limit entry order can keep filling MORE shares after protection
        was already confirmed for what had filled so far. This is what
        makes "a transition to PROTECTION_PENDING must not stop fill
        monitoring" true: entry_order_terminal, not lifecycle_state, is
        what actually decides whether more checking is needed -
        VALID_TRANSITIONS explicitly allows PROTECTION_CONFIRMED_ACTIVE
        -> PROTECTION_PENDING for exactly this resize case. See
        order_lifecycle.FILL_MONITORING_STATES.

    Step 1: if the entry's own order isn't confirmed broker-terminal yet,
    poll it ONCE (no loop) for its latest cumulative filled_quantity and
    status, and advance lifecycle_state forward accordingly.
    entry["entry_order_terminal"] is stamped True only once a genuinely
    final status (FILLED/CANCELLED/FAILED) is observed - never earlier,
    and never un-set once True.

    A CANCELLED or FAILED order is not automatically a zero-position
    outcome - a real, common brokerage behavior is a day order partially
    filling before the unfilled remainder is cancelled (session close, a
    triggered risk control, etc). filled_quantity is checked BEFORE
    deciding the outcome: a positive filled_quantity is treated exactly
    like a full fill for protection purposes, and the cancelled/failed
    remainder is recorded on the entry purely for the audit trail. Only a
    confirmed filled_quantity of zero resolves to the true no-position
    ENTRY_FAILED outcome.

    Step 2: nothing to protect (filled_quantity <= 0) - return as-is (or
    ENTRY_FAILED, if that's now confirmed terminal-and-zero).

    Step 3: reconcile protection to match - advances lifecycle_state
    through PROTECTION_PENDING (staying there if already there; returning
    to it from PROTECTION_CONFIRMED_ACTIVE or PROTECTION_FAILED only if a
    resize is actually needed - an entry that's fully filled, fully and
    correctly protected, and broker-terminal is left completely alone,
    not bounced through states forever), calls
    _reconcile_protective_leg_quantity for BOTH legs (idempotent no-op if
    already correctly sized; growing or shrinking as needed), re-confirms
    both legs genuinely active, and lands on PROTECTION_CONFIRMED_ACTIVE
    or PROTECTION_FAILED.

    Raises if either leg's resize/placement did not fully succeed (after
    still attempting BOTH legs and updating lifecycle_state as far as it
    legitimately could) - the caller is responsible for treating that as
    a failed monitor attempt (see monitor_last_error/monitor_attempt_count
    in _monitor_transitional_orders)."""
    current_state = entry.get("lifecycle_state")

    if not entry.get("entry_order_terminal"):
        detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        fill = ol.summarize_fill(detail)
        new_filled_quantity = fill["filled_quantity"]
        status = fill["status"]
        # Broker-reported cumulative average fill price for the entry, if
        # the response includes one (see order_lifecycle._FILL_PRICE_FIELD_CANDIDATES -
        # unconfirmed field name, best-effort). Recorded on EVERY poll while
        # present (not just once) so it reflects the latest cumulative
        # average as more shares fill, never left stale at an early
        # partial-fill price. Deliberately never falls back to limit_price
        # here - a poll that doesn't report a price simply leaves this
        # field exactly as it was (still None if never reported), which is
        # what _reconcile_position_exit's P&L-completeness check depends on.
        if fill.get("average_price") is not None:
            entry["average_entry_fill_price"] = fill["average_price"]

        if _entry_fill_is_final(status):
            entry["entry_order_terminal"] = True
            if status != "FILLED" and new_filled_quantity > 0:
                # Distinct from a TRUE zero-fill cancellation (which gets
                # its own clear "error" field via the ENTRY_FAILED
                # transition below) - this specifically flags the more
                # unusual "some shares filled, then the rest was
                # cancelled/failed" case worth calling out on its own.
                entry["unfilled_remainder_status"] = status

        if current_state in (ol.ENTRY_SUBMITTED, ol.ENTRY_PARTIALLY_FILLED, ol.MANUAL_LINK_IN_PROGRESS):
            if status == "PARTIAL FILLED":
                ol.transition(entry, ol.ENTRY_PARTIALLY_FILLED, filled_quantity=new_filled_quantity)
                current_state = ol.ENTRY_PARTIALLY_FILLED
            elif new_filled_quantity > 0:
                ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=new_filled_quantity)
                current_state = ol.ENTRY_FILLED
            elif entry.get("entry_order_terminal"):
                ol.transition(entry, ol.ENTRY_FAILED, error=f"entry order {status.lower()} at the broker", filled_quantity=0)
                return entry
            else:
                entry["filled_quantity"] = new_filled_quantity  # still 0, still resting - stays where it was
        else:
            # Resuming from ENTRY_FILLED / PROTECTION_PENDING /
            # PROTECTION_FAILED / PROTECTION_CONFIRMED_ACTIVE - just
            # refresh the number; the lifecycle transition (if any) is
            # decided below, uniformly, for both this and the fresh path.
            entry["filled_quantity"] = new_filled_quantity

    filled_quantity = float(entry.get("filled_quantity") or 0)
    current_state = entry.get("lifecycle_state")

    if filled_quantity <= 0:
        if entry.get("entry_order_terminal") and current_state not in ol.TERMINAL_STATES:
            ol.transition(entry, ol.ENTRY_FAILED, error="entry order finished with zero fill", filled_quantity=0)
        return entry

    # Realized risk - see the module-level note in _reconcile_protective_leg_quantity's
    # docstring for the same "estimate, not a guarantee" caveat that applies
    # to limit_price/stop_price as fill-price stand-ins. Computed only ONCE
    # per entry (guarded by the "not already set" check) - on a resumed or
    # resized call this was already computed (and alerted on, if
    # applicable) by whichever earlier pass first confirmed a fill;
    # recomputing on every resume would be harmless arithmetically but
    # would risk re-firing the over-planned-risk alert on every single
    # monitor tick while protection keeps retrying.
    if "realized_risk_dollars" not in entry:
        # direction="short" has its stop ABOVE limit_price (a rise is the
        # loss, not a fall) - (limit_price - stop_price) would come out
        # negative there, so the subtraction is mirrored to stay a
        # positive dollars-at-risk figure either way.
        realized_risk_dollars = round(
            filled_quantity * ((stop_price - limit_price) if entry.get("direction") == "short" else (limit_price - stop_price)), 2
        )
        entry["realized_risk_dollars"] = realized_risk_dollars
        planned_risk_dollars = entry.get("planned_risk_dollars")
        if isinstance(planned_risk_dollars, (int, float)) and realized_risk_dollars > planned_risk_dollars + 0.01:
            entry["realized_risk_exceeds_planned"] = True
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "realized_risk_exceeds_planned",
                        "ticker": ticker,
                        "message": (
                            f"{ticker}: realized risk (${realized_risk_dollars:.2f} on {filled_quantity:g} filled shares) "
                            f"exceeded the planned risk budget (${planned_risk_dollars:.2f}) for this entry. Review the "
                            f"position - the sizing that was supposed to bound this trade's loss did not hold as expected."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 - never let alerting itself break the fill/protection flow
                pass

    current_state = entry.get("lifecycle_state")
    if current_state in (ol.ENTRY_SUBMITTED, ol.ENTRY_PARTIALLY_FILLED, ol.MANUAL_LINK_IN_PROGRESS):
        ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=filled_quantity)
        current_state = ol.ENTRY_FILLED
    if current_state == ol.ENTRY_FILLED:
        ol.transition(entry, ol.PROTECTION_PENDING)
        current_state = ol.PROTECTION_PENDING
    elif current_state == ol.PROTECTION_FAILED:
        ol.transition(entry, ol.PROTECTION_PENDING)
        current_state = ol.PROTECTION_PENDING
    elif current_state == ol.PROTECTION_CONFIRMED_ACTIVE:
        stop_already_sized = stop_price <= 0 or entry.get("stop_leg_quantity") == filled_quantity
        target_already_sized = target_price <= 0 or entry.get("target_leg_quantity") == filled_quantity
        if stop_already_sized and target_already_sized:
            return entry  # steady state - fully filled, fully protected, broker-terminal or not - nothing to do
        ol.transition(entry, ol.PROTECTION_PENDING)
        current_state = ol.PROTECTION_PENDING
    # else: already PROTECTION_PENDING from a prior pass - stay, fall through.

    stop_resize_error: Optional[BaseException] = None
    target_resize_error: Optional[BaseException] = None
    try:
        _reconcile_protective_leg_quantity(user_id, creds, account_id, ticker, trading_day, entry, "stop", filled_quantity, stop_price)
    except Exception as error:  # noqa: BLE001 - still attempt the OTHER leg regardless
        stop_resize_error = error
    try:
        _reconcile_protective_leg_quantity(user_id, creds, account_id, ticker, trading_day, entry, "target", filled_quantity, target_price)
    except Exception as error:  # noqa: BLE001
        target_resize_error = error

    _confirm_and_finalize_protection(user_id, creds, account_id, ticker, entry, filled_quantity, stop_price, target_price)

    if stop_resize_error or target_resize_error:
        # Lifecycle_state above already reflects reality as best it could
        # be determined - this is purely to surface the failure to the
        # caller's own failed-attempt tracking (monitor_last_error etc.),
        # since a resize failure is exactly the kind of thing that must
        # count as a failed monitor attempt, not be silently absorbed.
        raise RuntimeError(f"protective leg resize incomplete for {ticker}: stop={stop_resize_error}, target={target_resize_error}")

    return entry


def _poll_fill_and_protect(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry_client_order_id: str,
    limit_price: float,
    stop_price: float,
    target_price: float,
    trading_day: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """Bounded sleep-and-retry wrapper around
    _reconcile_entry_fill_and_protection, for a FRESH entry
    (ENTRY_SUBMITTED / ENTRY_PARTIALLY_FILLED / MANUAL_LINK_IN_PROGRESS)
    right after a placement is accepted, an ambiguous submission is
    reconciled, or a manual "link" resolution finds a strong match.
    Exists purely so a caller gets a fast INITIAL result (up to
    ENTRY_FILL_POLL_ATTEMPTS tries, ENTRY_FILL_POLL_INTERVAL_SECONDS
    apart) instead of having to wait for the next monitor tick to see an
    obviously-fast-filling order end up protected.

    _monitor_transitional_orders, by contrast, calls
    _reconcile_entry_fill_and_protection DIRECTLY - no bounded loop -
    since re-checking again on ITS next tick (rather than blocking this
    one while it processes potentially many other users' entries) is the
    right trade-off for a monitor, and is also the only path that ever
    resumes an entry from ENTRY_FILLED / PROTECTION_PENDING /
    PROTECTION_FAILED / PROTECTION_CONFIRMED_ACTIVE (all four are invalid
    starting points for the bounded loop here, which assumes the entry's
    own fill has never yet been confirmed).

    Stops retrying once entry_order_terminal is True AND protection has
    reached PROTECTION_CONFIRMED_ACTIVE or PROTECTION_FAILED, or once
    attempts are exhausted - whichever is left in the latter case
    (typically still ENTRY_SUBMITTED with nothing filled yet, or
    PROTECTION_PENDING mid-resize) is exactly what the monitor's next
    pass picks up. Tolerates a failure on any individual attempt (a
    transient broker error, or a leg resize that didn't complete this
    round) and simply retries - the bounded window existing at all is a
    UX nicety, not a correctness requirement; correctness comes from the
    monitor continuing to retry indefinitely afterward."""
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(ENTRY_FILL_POLL_INTERVAL_SECONDS)
        try:
            entry = _reconcile_entry_fill_and_protection(
                user_id=user_id,
                creds=creds,
                account_id=account_id,
                ticker=ticker,
                entry_client_order_id=entry_client_order_id,
                limit_price=limit_price,
                stop_price=stop_price,
                target_price=target_price,
                trading_day=trading_day,
                entry=entry,
            )
        except Exception:  # noqa: BLE001 - transient failure this round; the monitor keeps retrying beyond this bounded window regardless
            continue
        current_state = entry.get("lifecycle_state")
        if current_state in ol.TERMINAL_STATES or current_state in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED):
            break
    return entry


def _parse_option_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _reconcile_option_entry_fill(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    entry_client_order_id: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """Options counterpart to _reconcile_entry_fill_and_protection - much
    simpler because a long option has no separate protective leg to place
    or resize (see the options plan's "Lifecycle" section): once the entry
    order is confirmed filled, protection IS the entry itself - risk is
    already bounded by the premium paid, nothing more to place. Mirrors
    the equity path's own fill-classification logic
    (_entry_fill_is_final, filled_quantity checked BEFORE status) exactly,
    just without the protective-leg machinery that follows it there.
    Reuses order_lifecycle.py's existing states/VALID_TRANSITIONS
    unchanged - transitions straight through PROTECTION_PENDING to
    PROTECTION_CONFIRMED_ACTIVE in the same call, not a new state."""
    if not entry.get("entry_order_terminal"):
        detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        fill = ol.summarize_fill(detail)
        new_filled_quantity = fill["filled_quantity"]
        status = fill["status"]
        if fill.get("average_price") is not None:
            entry["average_entry_fill_price"] = fill["average_price"]

        if _entry_fill_is_final(status):
            entry["entry_order_terminal"] = True
            if status != "FILLED" and new_filled_quantity > 0:
                entry["unfilled_remainder_status"] = status

        current_state = entry.get("lifecycle_state")
        if current_state == ol.ENTRY_SUBMITTED:
            if new_filled_quantity > 0:
                ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=new_filled_quantity)
            elif entry.get("entry_order_terminal"):
                ol.transition(entry, ol.ENTRY_FAILED, error=f"option entry order {status.lower()} at the broker", filled_quantity=0)
                return entry
            else:
                entry["filled_quantity"] = new_filled_quantity  # still 0, still resting
        else:
            entry["filled_quantity"] = new_filled_quantity

    filled_quantity = float(entry.get("filled_quantity") or 0)
    current_state = entry.get("lifecycle_state")

    if filled_quantity <= 0:
        if entry.get("entry_order_terminal") and current_state not in ol.TERMINAL_STATES:
            ol.transition(entry, ol.ENTRY_FAILED, error="option entry order finished with zero fill", filled_quantity=0)
        return entry

    if current_state == ol.ENTRY_FILLED:
        # Real broker-reported average fill price when available; otherwise
        # falls back to the intended limit price as an ENTRY-BASIS estimate
        # for sizing exit thresholds (target/stop are percentages OF this
        # number) - explicitly flagged, never silently treated as
        # confirmed. This is distinct from _reconcile_position_exit's own
        # "never invent a fill price for realized P&L" rule, which governs
        # the EXIT side of the trade, not this entry-basis estimate.
        average_price = entry.get("average_entry_fill_price")
        if average_price is not None:
            entry["premium_paid_per_contract"] = float(average_price)
            entry["premium_paid_is_estimated"] = False
        else:
            entry["premium_paid_per_contract"] = float(entry.get("limit_price") or 0)
            entry["premium_paid_is_estimated"] = True
        entry["contracts"] = filled_quantity
        ol.transition(entry, ol.PROTECTION_PENDING)
        ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)

    return entry


def _poll_option_fill(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    entry_client_order_id: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """Bounded sleep-and-retry wrapper around _reconcile_option_entry_fill,
    mirroring _poll_fill_and_protect's own reasoning exactly (a fast
    INITIAL result right after placement, with _monitor_transitional_orders
    resuming indefinitely afterward if this bounded window isn't enough)."""
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(ENTRY_FILL_POLL_INTERVAL_SECONDS)
        try:
            entry = _reconcile_option_entry_fill(user_id, creds, account_id, entry_client_order_id, entry)
        except Exception:  # noqa: BLE001 - transient failure this round; the monitor keeps retrying beyond this bounded window regardless
            continue
        current_state = entry.get("lifecycle_state")
        if current_state in ol.TERMINAL_STATES or current_state == ol.PROTECTION_CONFIRMED_ACTIVE:
            break
    return entry


def _submit_and_confirm_option_entry(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    option_contract: Dict[str, object],
    quantity: int,
    limit_price: float,
    trading_day: str,
    entry: Dict[str, object],
) -> Dict[str, object]:
    """Options counterpart to _submit_and_protect_entry - places a
    BUY_TO_OPEN order for the specific contract options_selector.py
    resolved (option_symbol/strike/expiration_date/option_type), then
    drives it through fill confirmation via the bounded poll above.
    Mutates and returns `entry`, same contract as the equity function:
    entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE means the
    position is genuinely filled and (by construction - see the Lifecycle
    section of the options plan) risk-bounded, not just that a placement
    call once returned success.

    Splits the placement exception the same way _submit_and_protect_entry
    does - webull_api.DefiniteOrderRejection (a real, parsed broker
    rejection) means ENTRY_FAILED; anything else (AmbiguousOrderSubmission,
    or any unclassified exception) means UNKNOWN_SUBMISSION_STATE, so the
    caller reserves capital for it rather than risking a silent duplicate."""
    entry_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "option_entry", attempt=1)
    ol.initialize(
        entry, ol.ENTRY_SUBMITTED,
        entry_client_order_id=entry_client_order_id,
        instrument_type="OPTION",
        option_symbol=option_contract["option_symbol"],
        strike=option_contract["strike"],
        expiration_date=option_contract["expiration_date"],
        option_type=option_contract["option_type"],
        limit_price=limit_price,
        quantity=quantity,
    )
    try:
        webull_api.place_option_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            option_type=option_contract["option_type"],
            strike_price=option_contract["strike"],
            expiration_date=option_contract["expiration_date"],
            side="BUY",
            quantity=quantity,
            limit_price=limit_price,
            client_order_id=entry_client_order_id,
        )
    except webull_api.DefiniteOrderRejection as error:
        ol.transition(entry, ol.ENTRY_FAILED, error=str(error))
        return entry
    except Exception as error:  # noqa: BLE001 - AmbiguousOrderSubmission, or anything else not explicitly classified - fail-safe default, see docstring
        ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error=str(error))
        return entry

    return _poll_option_fill(user_id, creds, account_id, entry_client_order_id, entry)


def _check_and_execute_option_exit(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    trading_day: str,
    entry: Dict[str, object],
) -> bool:
    """The option counterpart to _check_and_execute_target_exit - actively
    watches the option's OWN live price (via get_option_snapshot, not the
    underlying's) against premium-percentage-based target/stop thresholds
    plus a time-based safety net, and executes a SELL_TO_CLOSE when
    triggered. Simpler than the equity target-exit's cancel-then-sell
    dance: there is no resting protective order to cancel first (a long
    option's protection IS the entry - see the options plan's Lifecycle
    section), so this places the closing sell directly.

    Deliberately does NOT rely on a resting broker-side stop order the way
    equity's stop leg does - see _check_and_execute_option_exit's sibling
    finding this session about equity's own DAY-TIF resting stops having
    no detection/re-arm path once an entry is fully filled and terminal.
    Every monitor tick actively re-checks the live snapshot instead, so
    there's no analogous "silently expired and nothing noticed" gap here.

    Returns True only once the exit sell is CONFIRMED FILLED and the trade
    is recorded CLOSED. Returns False at every earlier step (missing
    entry fields, no live snapshot, no threshold reached) - never guesses,
    matching every other check in this file."""
    option_symbol = entry.get("option_symbol")
    strike = entry.get("strike")
    expiration_date = entry.get("expiration_date")
    option_type = entry.get("option_type")
    contracts = float(entry.get("contracts") or entry.get("filled_quantity") or 0)
    premium_paid = _parse_option_float(entry.get("premium_paid_per_contract"))
    if not option_symbol or not strike or not expiration_date or not option_type or contracts <= 0 or not premium_paid:
        return False  # entry not fully initialized yet - shouldn't happen for PROTECTION_CONFIRMED_ACTIVE, fail closed

    try:
        snapshot_rows = webull_api.get_option_snapshot(creds["app_key"], creds["app_secret"], [option_symbol])
    except Exception:  # noqa: BLE001 - transient data fetch failure, try again next tick
        return False
    row = next((r for r in snapshot_rows if str(r.get("symbol", "")) == option_symbol), snapshot_rows[0] if snapshot_rows else None)
    if not row:
        return False
    bid = _parse_option_float(row.get("bid"))
    if bid is None or bid <= 0:
        return False  # no live bid to value or exit the position against - fail closed

    autonomy_settings = get_autonomy_status(user_id)
    target_gain_pct = float(autonomy_settings.get("option_target_gain_percent") or 50.0) / 100.0
    stop_loss_pct = float(autonomy_settings.get("option_stop_loss_percent") or 50.0) / 100.0
    close_days_before_expiration = int(autonomy_settings.get("option_close_days_before_expiration") or 3)

    target_value = premium_paid * (1 + target_gain_pct)
    stop_value = premium_paid * (1 - stop_loss_pct)

    close_reason: Optional[str] = None
    if bid >= target_value:
        close_reason = "option_target_reached"
    elif bid <= stop_value:
        close_reason = "option_stop_reached"
    else:
        try:
            days_to_expiration = (date.fromisoformat(str(expiration_date)) - _now_utc().date()).days
        except ValueError:
            days_to_expiration = None
        if days_to_expiration is not None and days_to_expiration <= close_days_before_expiration:
            close_reason = "option_expiration_safety_close"

    if close_reason is None:
        return False

    next_attempt = int(entry.get("exit_attempt") or 0) + 1
    entry["exit_attempt"] = next_attempt
    sell_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "option_exit", attempt=next_attempt)
    # A marketable limit slightly below the live bid, mirroring the
    # equity target-exit's own slippage-tolerance approach for making a
    # closing sell reliably fillable rather than resting indefinitely.
    exit_limit_price = round(bid * (1 - TARGET_EXIT_SLIPPAGE_TOLERANCE), 2)
    try:
        webull_api.place_option_order(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_id=account_id,
            symbol=ticker,
            option_type=option_type,
            strike_price=strike,
            expiration_date=expiration_date,
            side="SELL",
            quantity=contracts,
            limit_price=exit_limit_price,
            client_order_id=sell_client_order_id,
        )
    except Exception as error:  # noqa: BLE001 - placement failed outright; nothing was cancelled first (unlike equity), so no unprotected window to restore - just retry next pass
        raise RuntimeError(f"option exit sell order failed for {ticker} ({close_reason}): {error} - will retry next pass")

    filled_this_sell = 0.0
    average_exit_price: Optional[float] = None
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(ENTRY_FILL_POLL_INTERVAL_SECONDS)
        try:
            sell_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, sell_client_order_id)
            sell_fill = ol.summarize_fill(sell_detail)
        except Exception:  # noqa: BLE001 - transient lookup failure, try again next attempt
            continue
        filled_this_sell = sell_fill["filled_quantity"]
        average_exit_price = sell_fill.get("average_price")
        if filled_this_sell >= contracts or _entry_fill_is_final(sell_fill["status"]):
            break

    if filled_this_sell <= 0:
        raise RuntimeError(
            f"option exit sell order for {ticker} has not filled yet after {ENTRY_FILL_POLL_ATTEMPTS} checks - will keep checking"
        )

    trade_id = str(entry.get("entry_client_order_id") or "")
    pnl_complete = average_exit_price is not None and not entry.get("premium_paid_is_estimated")
    # Long option P&L: (exit - entry) * contracts * 100 (the standard
    # per-contract multiplier - see integrations/webull.py's
    # get_option_contracts, which confirmed "multiplier": "100" live).
    gross_pnl = (
        round(filled_this_sell * (average_exit_price - premium_paid) * 100, 2)
        if pnl_complete else None
    )
    closed_record = {
        "ticker": ticker,
        "instrument_type": "OPTION",
        "side": "BUY",
        "option_symbol": option_symbol,
        "strike": strike,
        "expiration_date": expiration_date,
        "option_type": option_type,
        "entry_client_order_id": entry.get("entry_client_order_id"),
        "exit_client_order_id": sell_client_order_id,
        "requested_quantity": entry.get("quantity"),
        "filled_quantity": contracts,
        "premium_paid_per_contract": premium_paid,
        "premium_paid_is_estimated": bool(entry.get("premium_paid_is_estimated")),
        "exit_type": close_reason,
        "exited_quantity": filled_this_sell,
        "average_exit_price": average_exit_price,
        "entry_timestamp": entry.get("logged_at"),
        "exit_timestamp": _now_utc().isoformat(),
        "gross_realized_pnl": gross_pnl,
        "fees": None,
        "net_realized_pnl": gross_pnl,
        "pnl_status": "complete" if pnl_complete else "incomplete_missing_fill_price",
        "strategy": entry.get("strategy"),
        "close_reason": close_reason,
        "broker_evidence": {"exited_leg_status": "app_monitored_option_exit"},
        "reconciled_at": _now_utc().isoformat(),
    }
    record_closed_trade(user_id, trade_id, closed_record)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason=close_reason)
    try:
        if pnl_complete:
            add_manual_alert(
                user_id,
                {
                    "type": "position_closed",
                    "ticker": ticker,
                    "message": f"{ticker} {option_type} ${strike}: option position closed ({close_reason}, {filled_this_sell:g} contracts). Realized P&L: ${gross_pnl:.2f}.",
                },
            )
        else:
            add_manual_alert(
                user_id,
                {
                    "type": "position_closed_pnl_incomplete",
                    "ticker": ticker,
                    "message": (
                        f"{ticker} {option_type} ${strike}: option position closed ({close_reason}, {filled_this_sell:g} contracts), "
                        "but realized P&L could not be computed - the entry premium was estimated and/or the exit fill price wasn't "
                        "reported. Review this trade's actual fill prices manually."
                    ),
                },
            )
    except Exception:  # noqa: BLE001
        pass
    return True


def _parse_trusted_past_timestamp(raw: object, *, now: datetime, default: datetime) -> datetime:
    """Parses a stored ISO timestamp that's meant to anchor a grace period
    (e.g. first_definite_rejection_at) - and rejects anything that couldn't
    have been produced by this app's own _now_utc().isoformat() writes:
      - missing/empty -> default (a fresh anchor, not an error - this is
        the normal case for a value that's never been set yet);
      - unparseable -> default (corrupt data, not trusted);
      - naive (no tzinfo) -> default. _now_utc() always writes a
        timezone-aware ISO string; a naive one showing up means the value
        didn't come from this code path and can't be safely compared
        against an aware `now` anyway (mixing naive/aware datetimes raises
        TypeError on subtraction) - never silently assumed to be UTC and
        given a pass, since that would mask real data corruption;
      - in the FUTURE relative to `now` -> default. A first-rejection
        timestamp later than the current time is nonsensical (clock skew
        on whichever process wrote it, or a corrupted/tampered value) and
        must not be trusted to anchor how much time has "elapsed" since it.
    Falling back to `default` (normally `now`) restarts the grace period
    fresh rather than ever letting a bad stored value satisfy it early."""
    if not raw:
        return default
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return default
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return default
    if parsed > now:
        return default
    return parsed


def _resolve_orphan_recovered_entry(
    user_id: str, creds: Dict[str, str], account_id: str, entry: Dict[str, object]
) -> Dict[str, object]:
    """Called instead of the normal _poll_fill_and_protect resume, once
    _reconcile_unknown_submission's broker lookup confirms a
    _discover_orphaned_broker_entries-seeded entry (entry["orphan_recovered"]
    is True) genuinely exists at the broker. Caller has already
    transitioned entry to ENTRY_SUBMITTED.

    This entry's stop/target were NEVER known - only ever this app's own
    in-memory planning for a scan tick the process crashed before
    finishing - and are never invented here either (see
    _discover_orphaned_broker_entries's docstring for the full
    reasoning). Letting the NORMAL _poll_fill_and_protect /
    _reconcile_protective_leg_quantity flow run against a leg_price of 0
    would eventually self-correct to PROTECTION_FAILED (the `leg_price <=
    0` guard prevents an invented-price order), but only AFTER an
    unbounded number of shares could keep filling against a still-resting
    DAY limit order with no risk plan at all - reaching "unprotected but
    labeled PROTECTION_FAILED" is not the same as ACTIVELY bounding that
    exposure. This function is a DEFINED emergency policy instead:
      - zero shares filled, order already broker-terminal (CANCELLED/
        FAILED) - resolves cleanly to ENTRY_FAILED. No risk was ever
        taken; nothing to alert about beyond the orphan-discovery alert
        already fired.
      - zero shares filled, order STILL resting - cancels it OUTRIGHT
        right now, rather than leave a live, un-risk-planned order
        resting at the broker that could fill later. Resolves to
        ENTRY_FAILED once cancelled (no risk taken), with its own alert
        explaining why.
      - ANY shares filled (fully or partially) - cancels any STILL-
        UNFILLED remainder immediately (this is "cancel any unfilled
        entry remainder when safely identifiable" - bounds further
        GROWING exposure with no protection plan), then transitions
        ENTRY_FILLED -> PROTECTION_PENDING -> PROTECTION_FAILED (the only
        valid path to PROTECTION_FAILED per order_lifecycle.VALID_TRANSITIONS)
        and fires an IMMEDIATE critical alert. The alert and the
        transition's own error text both say UNPROTECTED explicitly -
        this position is never described as "protected" anywhere, since
        it structurally cannot be until a human supplies real stop/target
        levels."""
    entry_client_order_id = str(entry.get("entry_client_order_id") or "")
    ticker = str(entry.get("ticker", ""))
    try:
        detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        fill = ol.summarize_fill(detail)
    except Exception as error:  # noqa: BLE001 - inconclusive, stays UNKNOWN_SUBMISSION_STATE for the next attempt
        ol.transition(
            entry,
            ol.UNKNOWN_SUBMISSION_STATE,
            last_reconciliation_attempt_at=_now_utc().isoformat(),
            last_reconciliation_error=f"orphan fill-status lookup failed: {error}",
        )
        return entry

    filled_quantity = fill["filled_quantity"]
    status = fill["status"]

    if filled_quantity <= 0:
        if _entry_fill_is_final(status):
            # Already CANCELLED/FAILED at the broker with zero fill - a
            # clean, no-risk outcome, same as an ordinary zero-fill entry.
            ol.transition(entry, ol.ENTRY_FAILED, error=f"orphan entry order {status.lower()} at the broker with zero fill", filled_quantity=0)
            return entry
        # Still resting, genuinely untouched - eliminate the unknown-risk
        # exposure proactively rather than let it possibly fill later
        # with still no protection plan.
        try:
            webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        except Exception as error:  # noqa: BLE001
            ol.transition(
                entry,
                ol.UNKNOWN_SUBMISSION_STATE,
                last_reconciliation_attempt_at=_now_utc().isoformat(),
                last_reconciliation_error=f"could not cancel unfilled orphan entry: {error}",
            )
            return entry
        ol.transition(
            entry, ol.ENTRY_FAILED,
            error="orphan entry cancelled outright before any fill - stop/target were never known, so no risk was taken",
            filled_quantity=0,
        )
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "orphan_entry_cancelled_unfilled",
                    "ticker": ticker,
                    "priority": "critical",
                    "message": (
                        f"{ticker}: cancelled a recovered orphan entry order (client_order_id "
                        f"{entry_client_order_id}) before it filled. Its stop-loss/take-profit levels were never "
                        "known, so this app removed the unknown-risk exposure rather than risk it filling later "
                        "with no protection plan at all."
                    ),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return entry

    # Some shares filled - cancel any still-unfilled remainder RIGHT NOW
    # to stop further, GROWING unknown-risk exposure, then freeze+alert
    # on the filled portion. Best-effort: even if the cancel itself
    # fails, still proceed to freeze/alert on what's already filled -
    # that risk is real regardless of whether the remainder gets cancelled.
    if not _entry_fill_is_final(status):
        try:
            webull_api.cancel_order(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        except Exception:  # noqa: BLE001 - best-effort; the alert below covers the filled portion either way
            pass

    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=filled_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(
        entry, ol.PROTECTION_FAILED,
        error=f"orphan entry: {filled_quantity:g} shares filled but stop/target were never known - never invented, never auto-protected",
    )
    entry["entry_order_terminal"] = True
    try:
        add_manual_alert(
            user_id,
            {
                "type": "orphan_entry_filled_unprotected",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker}: a recovered orphan entry order (client_order_id {entry_client_order_id}) has "
                    f"{filled_quantity:g} shares FILLED and UNPROTECTED. Its stop-loss/take-profit levels were "
                    "never known and were never guessed. Any unfilled remainder of this order has been cancelled. "
                    "New autonomous entries are frozen for this account. Review this position manually and "
                    "protect or liquidate it directly at the broker."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return entry


def _reconcile_unknown_submission(
    user_id: str, creds: Dict[str, str], account_id: str, entry: Dict[str, object]
) -> Dict[str, object]:
    """Attempts to resolve one entry stuck in UNKNOWN_SUBMISSION_STATE by
    looking up its entry_client_order_id directly - the same lookup
    get_order_detail already does elsewhere, but here the question being
    asked is existence, not just status: does the broker have ANY record of
    this order at all?

    This entry's CAPITAL reservation does NOT self-correct on its own while
    still unresolved - see _reconcile_unknown_submissions, whose return
    value blocks every NEW entry for this account (not just within the
    scan tick the ambiguity first occurred in) for as long as ANY entry
    remains in UNKNOWN_SUBMISSION_STATE. What CAN self-correct here, once
    resolved, is PROTECTION: if the ambiguous submission actually filled,
    nothing else will notice and attach a stop-loss/take-profit unless this
    function resumes that flow - an unprotected filled position, not a
    bookkeeping discrepancy, is the real risk an unresolved entry
    represents.

    The lookup's outcome is classified using webull_api.DefiniteOrderRejection
    vs webull_api.AmbiguousOrderSubmission (see integrations/webull.py's
    _classify_server_exception) - exception class alone never proved
    whether the broker actually has no record of this order, so this no
    longer treats every lookup failure identically:
      - DefiniteOrderRejection (a well-formed, PARSED "no such order" /
        rejection-shaped response) is NOT immediately conclusive by itself -
        see UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS / MIN_DEFINITE_REJECTION_CONFIRMATIONS
        above for why. The first sighting just records itself
        (first_definite_rejection_at, definite_rejection_count) and stays
        UNKNOWN_SUBMISSION_STATE; only once the SAME definite absence has
        been confirmed enough times, spread across enough elapsed time,
        does this resolve to ENTRY_FAILED.
      - Any other exception (AmbiguousOrderSubmission, or anything else):
        still can't tell - stays UNKNOWN_SUBMISSION_STATE, reserved and
        alerted, for the next scan to try again. Does not reset or advance
        the definite-rejection counters above - an ambiguous result neither
        confirms nor contradicts a prior definite "not found" sighting.
      - A SUCCESSFUL lookup means the broker DOES have a record of this
        order - resumes fill-polling/protection via _poll_fill_and_protect
        exactly as if the original placement call had returned normally.
        _poll_fill_and_protect itself now correctly interprets whatever
        status comes back, including a CANCELLED/FAILED order that
        partially filled before being cancelled (see its docstring) - this
        function no longer duplicates that status interpretation."""
    entry_client_order_id = entry.get("entry_client_order_id")
    ticker = str(entry.get("ticker", ""))
    if not entry_client_order_id or not ticker:
        return entry

    try:
        webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, str(entry_client_order_id))
    except webull_api.DefiniteOrderRejection as error:
        now = _now_utc()
        first_seen_at = _parse_trusted_past_timestamp(entry.get("first_definite_rejection_at"), now=now, default=now)
        confirmations = int(entry.get("definite_rejection_count") or 0) + 1
        elapsed_seconds = (now - first_seen_at).total_seconds()
        if confirmations >= MIN_DEFINITE_REJECTION_CONFIRMATIONS and elapsed_seconds >= UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS:
            ol.transition(
                entry,
                ol.ENTRY_FAILED,
                error=f"reconciliation: {error} (confirmed absent {confirmations}x over {elapsed_seconds:.0f}s)",
            )
            return entry
        ol.transition(
            entry,
            ol.UNKNOWN_SUBMISSION_STATE,
            first_definite_rejection_at=first_seen_at.isoformat(),
            definite_rejection_count=confirmations,
            last_reconciliation_attempt_at=now.isoformat(),
            last_reconciliation_error=str(error),
        )
        return entry
    except Exception as error:  # noqa: BLE001 - AmbiguousOrderSubmission, or anything else - still can't tell, see docstring
        ol.transition(
            entry,
            ol.UNKNOWN_SUBMISSION_STATE,
            last_reconciliation_attempt_at=_now_utc().isoformat(),
            last_reconciliation_error=str(error),
        )
        return entry

    # The lookup succeeded - the broker DOES have a record of this order.
    ol.transition(entry, ol.ENTRY_SUBMITTED, error=None)
    if entry.get("orphan_recovered"):
        # This entry's stop/target were never known (seeded by
        # _discover_orphaned_broker_entries, not a normal placement) -
        # the ordinary fill/protect flow below assumes real risk
        # parameters exist. See _resolve_orphan_recovered_entry's
        # docstring for the defined emergency policy used instead.
        return _resolve_orphan_recovered_entry(user_id, creds, account_id, entry)
    # Resume fill-polling/protection exactly as if the original placement
    # call had returned; _poll_fill_and_protect interprets the status.
    return _poll_fill_and_protect(
        user_id=user_id,
        creds=creds,
        account_id=account_id,
        ticker=ticker,
        entry_client_order_id=str(entry_client_order_id),
        limit_price=float(entry.get("limit_price") or 0),
        stop_price=float(entry.get("stop") or 0),
        target_price=float(entry.get("target") or 0),
        trading_day=str(entry.get("trading_day") or ""),
        entry=entry,
    )


def _discover_orphaned_broker_entries(user_id: str, creds: Dict[str, str], account_id: str) -> int:
    """Restart recovery for the gap no other reconciliation function
    covers: a broker-ACCEPTED entry order whose local persistence never
    happened at all - not even as UNKNOWN_SUBMISSION_STATE - because the
    process crashed between webull_api.place_stock_order succeeding
    (inside _submit_and_protect_entry) and record_overnight_order actually
    being called back in _run_autonomous_trade_scan_locked's own loop (see
    that function - the ONLY durable write for a fresh entry happens
    there, after _submit_and_protect_entry already returns). Every other
    reconciliation function in this app (_reconcile_unknown_submissions,
    _monitor_transitional_orders, etc.) operates on entries ALREADY
    present in overnight_orders.json - an order with zero local trace is
    invisible to all of them.

    Detection strategy - STRONG attribution, not prefix matching: a bare
    "pt" prefix (2 characters) is not remotely specific enough to
    authoritatively attribute an order to THIS user - it's trivially
    satisfiable by coincidence, by a differently-configured deployment
    sharing the same broker account, or even by a human manually typing a
    client_order_id starting with those two letters. Instead, for every
    BUY order in broker history (an entry; stop/target legs are always
    SELL, so this can't misidentify a protective leg as an orphaned
    entry) whose client_order_id is not already locally known, this
    recomputes order_lifecycle.deterministic_client_order_id(user_id,
    symbol, day, "entry", attempt=1) for every day in a bounded recent
    window and requires an EXACT match against the row's own
    client_order_id before treating it as this user's own order. This id
    is a SHA-256 digest over (user_id, ticker, trading_day, leg, attempt) -
    at 120 bits of entropy in the truncated hex portion, an exact match is
    only possible if this exact function, with this exact user_id, was
    the one that generated it; a coincidental collision is computationally
    infeasible. attempt=1 only, matching _submit_and_protect_entry's own
    fresh-placement call (a resubmission under attempt>1 does not apply
    to entry orders anywhere in this codebase today). Diffs against EVERY
    entry_client_order_id already known locally, in ANY lifecycle_state
    (including terminal ones - a CLOSED or ENTRY_FAILED entry is still
    "known", not an orphan). Anything left over, and STRONGLY attributed
    by the exact-hash-match above, is broker-confirmed to exist and
    locally unknown to exist at all.

    Deliberately does NOT attempt to reconstruct stop/target risk
    parameters for a discovered orphan - those were only ever this app's
    own in-memory planning decision for that scan tick, never sent to the
    broker as such, and guessing them now (e.g. by re-running strategy
    scoring against current market data) would be inventing a NEW risk
    plan after the fact, not recovering the original one - out of scope
    for lifecycle reconciliation. Instead, seeds the orphan as
    UNKNOWN_SUBMISSION_STATE (stop/target left unset) - the SAME frozen,
    alerted, admin-resolvable state this app already uses for every other
    "broker accepted something, this app cannot safely act on it alone"
    situation. This is safe by construction, not by luck: if the orphan's
    fill is later discovered by _reconcile_unknown_submission's normal
    resume-via-_poll_fill_and_protect path, _reconcile_protective_leg_quantity's
    `leg_price <= 0` guard means no protective order is ever placed at an
    invented price - protection simply cannot be confirmed
    (stop_client_order_id stays unset, so _confirm_and_finalize_protection's
    stop_confirmed can never become True), landing on PROTECTION_FAILED
    with its own existing alert - never a silently "confirmed protected"
    position with zero real protection.

    Idempotent by construction: re-running this on a later tick after the
    orphan has already been recorded finds its entry_client_order_id in
    the known-ids set and skips it - no duplicate record is ever created,
    regardless of what trading_day this call happens to assign it (see
    below).

    Best-effort per row - one malformed history entry doesn't block
    detecting others. Returns the count of NEW orphans recorded this
    pass (0 if none, or if the history lookup itself failed - the caller
    treats that the same as "nothing found", matching every other
    reconciliation function's best-effort posture)."""
    try:
        history = webull_api.get_order_history(creds["app_key"], creds["app_secret"], account_id)
    except Exception:  # noqa: BLE001 - inconclusive, not "nothing to recover" - just can't check this tick
        return 0

    known_ids = {str(order.get("entry_client_order_id") or "") for order in list_overnight_orders(user_id)}
    discovered = 0
    today_key = _trading_day_key()
    # Bounded recent window to search for the trading_day that produces an
    # exact deterministic-id match - a few days past get_order_history's
    # own lookback as a buffer against weekend/holiday trading-day-vs-
    # calendar-day drift, not an attempt to cover an unbounded past.
    candidate_days = [_trading_day_key(_now_utc() - timedelta(days=offset)) for offset in range(0, webull_api.ORDER_HISTORY_LOOKBACK_DAYS + 3)]

    for row in history:
        client_order_id = row.get("client_order_id")
        if not client_order_id or not isinstance(client_order_id, str):
            continue
        side = str(row.get("side", "") or "").upper()
        if side != "BUY":
            continue
        if client_order_id in known_ids:
            continue

        ticker = str(row.get("symbol", "") or "").upper()
        if not ticker:
            continue

        # STRONG attribution - see docstring. Only a row whose id exactly
        # matches this user's own deterministic hash for this ticker on
        # one of the candidate days is treated as "ours"; everything else
        # (someone else's order, a different app, a coincidental prefix)
        # is silently ignored, not flagged as an orphan.
        attributed = any(
            ol.deterministic_client_order_id(user_id, ticker, day, "entry", attempt=1) == client_order_id
            for day in candidate_days
        )
        if not attributed:
            continue

        quantity_raw = row.get("total_quantity")
        limit_price_raw = row.get("limit_price")
        try:
            quantity = float(quantity_raw) if quantity_raw is not None else 0.0
            limit_price = float(limit_price_raw) if limit_price_raw is not None else 0.0
        except (TypeError, ValueError):
            quantity = 0.0
            limit_price = 0.0

        if quantity <= 0:
            # Can't safely construct even a minimal local record from
            # this row - still alert, so a human knows something matching
            # this app's own id pattern exists at the broker that this
            # pass could not bring under tracking, rather than staying
            # silent about a detection it couldn't complete.
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "orphan_entry_could_not_be_recovered",
                        "ticker": ticker or "UNKNOWN",
                        "priority": "critical",
                        "message": (
                            f"A broker order (client_order_id {client_order_id}) CONFIRMED to be this account's "
                            "own (exact deterministic-id match) was found in order history with no local record, "
                            "but its quantity could not be safely parsed to create a tracked record. Review this "
                            "order manually."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue

        orphan: Dict[str, object] = {}
        ol.initialize(
            orphan,
            ol.UNKNOWN_SUBMISSION_STATE,
            entry_client_order_id=client_order_id,
            error=(
                "orphan discovered via broker order-history reconciliation - no local record existed for this "
                "broker-accepted order (likely a crash between placement and local persistence)"
            ),
        )
        orphan["ticker"] = ticker
        orphan["quantity"] = quantity
        orphan["limit_price"] = limit_price
        # Deliberately unset - see docstring: never invented, only ever
        # supplied by a human resolving this through the existing
        # ambiguous-submission admin workflow.
        orphan["stop"] = 0
        orphan["target"] = 0
        # The day this orphan was DISCOVERED, not necessarily when it was
        # actually placed (unknowable from here) - only used later to
        # derive a deterministic, collision-free protective-leg id if this
        # entry is ever resolved and filled; never relied on for anything
        # date-sensitive or correctness-critical.
        orphan["trading_day"] = today_key
        orphan["orphan_recovered"] = True
        orphan["status"] = "unknown_submission_state"
        orphan["logged_at"] = _now_utc().isoformat()

        record_overnight_order(user_id, orphan)
        known_ids.add(client_order_id)
        discovered += 1

        try:
            add_manual_alert(
                user_id,
                {
                    "type": "orphan_entry_discovered",
                    "ticker": ticker,
                    "priority": "critical",
                    "message": (
                        f"{ticker}: recovered a broker order (client_order_id {client_order_id}, "
                        f"{quantity:g} shares @ ${limit_price:.2f}) that had NO local record at all - likely a "
                        "crash between placement and local persistence. New autonomous entries are frozen for "
                        "this account until this is resolved. Its stop-loss/take-profit levels are UNKNOWN and "
                        "were never guessed - review this position manually through the ambiguous-submission "
                        "admin workflow before it will be automatically protected."
                    ),
                },
            )
        except Exception:  # noqa: BLE001
            pass

    return discovered


def _reconcile_unknown_submissions(user_id: str, creds: Dict[str, str], account_id: str) -> bool:
    """Runs at the start of every scan, alongside _reconcile_exit_orders and
    _refresh_stop_confidence, to resolve any entry a prior scan's ambiguous
    order submission left in UNKNOWN_SUBMISSION_STATE - see
    _reconcile_unknown_submission for what "resolve" means and why it
    deliberately can't force a resolution on every attempt. Best-effort: one
    ticker's lookup failing doesn't stop the others from being tried.

    Returns True if ANY entry is still in UNKNOWN_SUBMISSION_STATE after
    this pass - the caller (_run_autonomous_trade_scan_locked) MUST NOT
    place any new entries for this account while this is True, and this is
    NOT limited to the scan tick the ambiguity first occurred in: it
    persists across every subsequent scan until conclusively resolved (or
    manually cleared through an audited procedure - not yet built). The
    account's true committed capital is not confidently knowable while an
    ambiguous submission is unresolved - the broker's own snapshot might, on
    any given scan, show neither the order nor a resulting position yet
    (broker-side eventual consistency can persist across scans, not just
    within one), which would silently UNDER-count committed capital and let
    a new candidate be sized against dollars that may still be spoken for.
    Refusing every new entry outright while unresolved is strictly more
    conservative than trying to carry forward a precise reserved-dollar
    figure across scans, and sidesteps that accounting question entirely.
    Existing position management (_reconcile_exit_orders,
    _refresh_stop_confidence, both already run before this) is NOT gated by
    this - already-open positions stay monitored and protected regardless."""
    orders = list_overnight_orders(user_id)
    pending = [order for order in orders if order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE]
    if not pending:
        return False
    for order in pending:
        try:
            _reconcile_unknown_submission(user_id, creds, account_id, order)
        except Exception:  # noqa: BLE001 - one bad record shouldn't block the others or the scan itself
            pass
    replace_overnight_orders(user_id, orders)
    return any(order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE for order in pending)


def _recover_incomplete_manual_resolutions(user_id: str, creds: Dict[str, str], account_id: str) -> bool:
    """Runs at the start of every scan, alongside _reconcile_unknown_submissions,
    to resume any manual resolution transaction (_resolve_ambiguous_submission)
    that was interrupted - by a crash, a worker restart, or any other
    process death - somewhere between writing its resolution_started audit
    record and successfully writing resolution_completed. See
    ambiguous_resolution_audit.find_incomplete_resolutions for exactly what
    "interrupted" means (an orphaned resolution_started record with no
    matching completed/failed record later in the chain) - the same
    orphaned-record signal is ALSO the durable freeze marker consulted by
    _has_unresolved_ambiguous_submission_locally, independent of whatever
    lifecycle_state the affected entry itself currently shows.

    Recovery is decided by the affected entry's CURRENT on-disk
    lifecycle_state, not by which stage the failure record (if one even
    exists) says it broke at - the state on disk is ground truth for what
    actually happened; the failure record is only ever a hint:
      - MANUAL_LINK_IN_PROGRESS: the link transaction's poll/protect step
        never finished. Resuming means calling _poll_fill_and_protect
        again - safe to simply retry (see _resolve_ambiguous_submission's
        docstring on why resubmitting a protective order under the same
        deterministic client_order_id is never a duplicate), then
        persisting the result and writing resolution_completed to close
        the transaction out.
      - UNKNOWN_SUBMISSION_STATE: the crash landed before step 1 (persisting
        MANUAL_LINK_IN_PROGRESS / MANUALLY_RESOLVED_NO_ORDER) ever
        completed - the entry is untouched and safely back to needing a
        fresh manual resolution attempt. Nothing to resume; this closes
        the orphaned transaction with resolution_failed so a future
        attempt isn't blocked by it forever.
      - Anything else (most commonly MANUALLY_RESOLVED_NO_ORDER, or a link
        that had already progressed past MANUAL_LINK_IN_PROGRESS before
        the crash): the state change already happened and was durably
        persisted - only the CLOSING audit write is missing. This
        retroactively writes resolution_completed to confirm it; nothing
        about the entry itself needs to change.
      - No matching entry found at all (defensive - should not normally
        happen): closes the transaction with resolution_failed rather
        than leaving it orphaned forever with nothing left to resume
        against.

    Best-effort per transaction, same as _reconcile_unknown_submissions -
    one incomplete resolution failing to recover must not block another
    from being tried, or block the rest of the scan; it simply stays
    orphaned (and the account stays frozen) for the next scan to retry.

    Returns True if anything is still incomplete after this pass -
    combined with _reconcile_unknown_submissions' own return value, this
    is what _run_autonomous_trade_scan_locked gates new entries on."""
    incomplete = find_incomplete_resolutions(user_id)
    if not incomplete:
        return False

    orders = list_overnight_orders(user_id)
    orders_by_entry_id = {str(o.get("entry_client_order_id")): o for o in orders if o.get("entry_client_order_id")}

    for started in incomplete:
        resolution_id = str(started.get("resolution_id") or "")
        entry_client_order_id = str(started.get("entry_client_order_id") or "")
        admin_user_id = str(started.get("administrator") or "")
        entry = orders_by_entry_id.get(entry_client_order_id)
        try:
            if entry is None:
                record_ambiguous_resolution_audit(
                    user_id,
                    {
                        "phase": RESOLUTION_PHASE_FAILED,
                        "resolution_id": resolution_id,
                        "administrator": admin_user_id,
                        "target_user_id": user_id,
                        "entry_client_order_id": entry_client_order_id,
                        "timestamp": _now_utc().isoformat(),
                        "error": "restart recovery: no matching overnight_orders entry found for this resolution",
                        "stage": "restart_recovery",
                    },
                )
                continue

            if entry.get("lifecycle_state") == ol.MANUAL_LINK_IN_PROGRESS:
                entry = _poll_fill_and_protect(
                    user_id=user_id,
                    creds=creds,
                    account_id=account_id,
                    ticker=str(entry.get("ticker", "")),
                    entry_client_order_id=entry_client_order_id,
                    limit_price=float(entry.get("limit_price") or 0),
                    stop_price=float(entry.get("stop") or 0),
                    target_price=float(entry.get("target") or 0),
                    trading_day=str(entry.get("trading_day") or ""),
                    entry=entry,
                )
                replace_overnight_orders(user_id, orders)
                record_ambiguous_resolution_audit(
                    user_id,
                    {
                        "phase": RESOLUTION_PHASE_COMPLETED,
                        "resolution_id": resolution_id,
                        "administrator": admin_user_id,
                        "target_user_id": user_id,
                        "entry_client_order_id": entry_client_order_id,
                        "timestamp": _now_utc().isoformat(),
                        "final_state": entry.get("lifecycle_state"),
                        "protective_order_ids": {"stop": entry.get("stop_client_order_id"), "target": entry.get("target_client_order_id")},
                        "recovered_by": "restart_recovery",
                    },
                )
            elif entry.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
                record_ambiguous_resolution_audit(
                    user_id,
                    {
                        "phase": RESOLUTION_PHASE_FAILED,
                        "resolution_id": resolution_id,
                        "administrator": admin_user_id,
                        "target_user_id": user_id,
                        "entry_client_order_id": entry_client_order_id,
                        "timestamp": _now_utc().isoformat(),
                        "error": "restart recovery: entry was never actually transitioned - state persistence did not complete before the process stopped",
                        "stage": "restart_recovery",
                    },
                )
            else:
                record_ambiguous_resolution_audit(
                    user_id,
                    {
                        "phase": RESOLUTION_PHASE_COMPLETED,
                        "resolution_id": resolution_id,
                        "administrator": admin_user_id,
                        "target_user_id": user_id,
                        "entry_client_order_id": entry_client_order_id,
                        "timestamp": _now_utc().isoformat(),
                        "final_state": entry.get("lifecycle_state"),
                        "protective_order_ids": (
                            {"stop": entry.get("stop_client_order_id"), "target": entry.get("target_client_order_id")}
                            if entry.get("stop_client_order_id") or entry.get("target_client_order_id")
                            else None
                        ),
                        "recovered_by": "restart_recovery",
                    },
                )
        except Exception:  # noqa: BLE001 - one bad recovery attempt must not block the others or the scan itself
            continue

    return bool(find_incomplete_resolutions(user_id))


# How long an ORDINARY transitional entry (order_lifecycle.MONITOR_RESUMABLE_STATES)
# can sit with _monitor_transitional_orders making NO forward progress on it
# before this app stops trusting its own ability to resolve it automatically
# and freezes new entries account-wide instead - the same posture
# UNKNOWN_SUBMISSION_STATE already takes for a different kind of
# uncertainty. Longer than UNKNOWN_SUBMISSION_GRACE_PERIOD_SECONDS (15 min)
# since these are typically lower-urgency (the entry's existence usually
# ISN'T in question here, only its exact fill/protection status) and a
# transient broker outage covering several consecutive fast-monitor ticks
# should not itself trigger a freeze.
MONITOR_STUCK_FREEZE_SECONDS = 1800

# The GitHub Actions schedulers (.github/workflows/autonomous-scan-scheduler.yml,
# fast-monitor-scheduler.yml) both only fire 13:00-21:00 UTC, Monday-Friday
# - see those files' own comments for why (approximates 9am-5pm ET with a
# buffer, deliberately NOT DST-aware, since GitHub Actions cron is plain
# UTC). Silence OUTSIDE that window is entirely expected - there is no
# scheduled trigger to be silent FROM overnight or on a weekend. Found
# empirically this session: recalibrating FAST_MONITOR_HEARTBEAT_STALE_SECONDS/
# FULL_SCAN_HEARTBEAT_STALE_SECONDS for intraday GitHub Actions jitter
# (below) without ALSO accounting for this overnight/weekend gap made the
# staleness check false-positive every single night - a ~15-hour expected
# gap is far larger than any intraday jitter threshold could reasonably
# be set to. This is a small, self-contained mirror of the schedule those
# YAML files actually use - not the DST-aware CORE/PRE/POST market-session
# concept (_current_webull_trading_session), which is a different thing
# answering a different question (is Webull open for a new order right
# now), not (is our external cron expected to have fired recently).
_SCHEDULED_TRIGGER_WINDOW_START_UTC_HOUR = 13
_SCHEDULED_TRIGGER_WINDOW_END_UTC_HOUR = 21


def _within_scheduled_trigger_window(now: Optional[datetime] = None) -> bool:
    """True if `now` (default: current UTC time) falls within the GitHub
    Actions schedulers' own active window - see the constants above."""
    now = now or _now_utc()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6 (datetime.weekday())
        return False
    return _SCHEDULED_TRIGGER_WINDOW_START_UTC_HOUR <= now.hour < _SCHEDULED_TRIGGER_WINDOW_END_UTC_HOUR


def _effective_heartbeat_stale_threshold(intraday_threshold: float, max_gap_seconds: float, now: Optional[datetime] = None) -> float:
    """The tight intraday_threshold applies while we're inside the
    scheduler's own active window (a real gap during that window is worth
    catching quickly, per the empirical 15-70 minute jitter this session
    measured). Outside it - nights, weekends - a long gap is entirely
    expected, so the much wider max_gap_seconds applies instead: generous
    enough to span a normal weekend without alarming, but still short
    enough to eventually catch a scheduler that's been genuinely disabled
    or broken, not just quiet overnight."""
    if _within_scheduled_trigger_window(now):
        return intraday_threshold
    return max_gap_seconds


# How stale fast_monitor_heartbeat.py's last-completed (or, if it's never
# completed even once, last-started) stamp can get before this app treats
# the fast monitor's SCHEDULER itself as unhealthy - "adding an endpoint
# is insufficient" without a way to notice its cron job was never
# configured, stopped firing, or is hanging.
#
# fast-monitor-trigger is currently driven by a GitHub Actions workflow
# (.github/workflows/fast-monitor-scheduler.yml, configured `*/5`) - NOT
# a dedicated Render Cron Job, which is what the original 600s threshold
# here assumed. Empirically observed this session (via the GitHub Actions
# API, across both this workflow and its autonomous-scan-scheduler
# sibling): actual firing gaps regularly run 15-70 minutes even on a
# `*/5` schedule - GitHub's own scheduler queue, not this app, is the
# bottleneck. 600s would false-positive on completely normal scheduling
# jitter, training whoever's watching to ignore this alert. Set to
# comfortably exceed the worst gap observed so far with real margin,
# while still catching a scheduler that's genuinely been disabled/broken
# for hours, not minutes. The dedicated, always-on continuous-monitor
# worker (CONTINUOUS_MONITOR_HEARTBEAT_STALE_SECONDS, far tighter) is the
# fast-reacting layer now; this is a slower fallback and its threshold
# should reflect that, not the cadence it was never actually able to hit.
#
# Only applies INSIDE the scheduler's own active window
# (_within_scheduled_trigger_window) - see FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS
# for the much wider threshold used outside it (nights/weekends), where a
# long gap is expected, not a fault.
FAST_MONITOR_HEARTBEAT_STALE_SECONDS = 5400

# The outer bound used OUTSIDE the scheduler's active window - covers the
# longest entirely-normal gap (a Friday afternoon's last run to Monday
# morning's first one, roughly 60-64 hours) plus real margin for a long
# weekend/holiday, while still eventually flagging a scheduler that's
# been genuinely abandoned rather than staying silent forever.
FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS = 345600  # 4 days


def _fast_monitor_health_status() -> Dict[str, object]:
    """LOCAL-ONLY (no broker calls) read of fast_monitor_heartbeat.py's
    latest recorded run, plus a computed "unhealthy" verdict - used by the
    admin-wide banner (base.html) and by _run_autonomous_trade_scan_locked
    to fire a one-shot alert when staleness is FIRST detected. Unhealthy
    if:
      - the fast monitor has NEVER run even once (the heartbeat file is
        completely empty) - the scheduler was probably never configured.
        Unconditional on the trigger window - "never run at all" is worth
        surfacing regardless of what time it's checked;
      - the most recent run STARTED but never recorded a matching
        COMPLETED stamp, and enough time has passed that it can no longer
        plausibly still be in flight - a hung run, a crash mid-run, or a
        run whose completion write itself failed. Also unconditional on
        the trigger window: a run that started must complete within
        seconds in ordinary operation, whatever time of day it started -
        this is OUR code getting stuck, not the external scheduler simply
        not having fired yet;
      - the most recent COMPLETED stamp is older than the EFFECTIVE
        threshold for right now (see _effective_heartbeat_stale_threshold) -
        tight while inside the scheduler's active window, much wider
        outside it, since a long overnight/weekend gap there is expected,
        not a fault.
    This says nothing about whether the full 5-minute scan (a completely
    separate, ALREADY-required cron job) is healthy - only about the
    OPTIONAL faster monitor layered on top of it."""
    heartbeat = get_fast_monitor_heartbeat_status()
    now = _now_utc()
    if not heartbeat:
        return {"healthy": False, "reason": "the fast monitor has never run - its scheduler may not be configured", "heartbeat": heartbeat, "age_seconds": None}

    last_completed_at_raw = heartbeat.get("last_completed_at")
    last_started_at_raw = heartbeat.get("last_started_at")
    most_recent_run_completed = heartbeat.get("last_started_run_id") and heartbeat.get("last_started_run_id") == heartbeat.get("last_completed_run_id")

    if most_recent_run_completed:
        # The most recent run finished cleanly - its completion time is
        # the right reference point.
        reference_raw = last_completed_at_raw
        hung = False
    else:
        # The most recent run either never completed, or there's no
        # completed record at all yet - use when it STARTED as the
        # reference. A run that started recently may simply still be in
        # flight, not stale; one that started long ago without ever
        # completing is a hang, which age_seconds below will catch.
        reference_raw = last_started_at_raw or last_completed_at_raw
        hung = True

    reference_at = _parse_trusted_past_timestamp(reference_raw, now=now, default=now)
    age_seconds = (now - reference_at).total_seconds()
    # Hung-run detection stays on the tight intraday threshold regardless
    # of the trigger window (see the docstring above); only a clean
    # completion's staleness gets the window-aware, wider tolerance.
    threshold = FAST_MONITOR_HEARTBEAT_STALE_SECONDS if hung else _effective_heartbeat_stale_threshold(
        FAST_MONITOR_HEARTBEAT_STALE_SECONDS, FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS, now
    )

    if age_seconds >= threshold:
        reason = (
            f"the fast monitor started a run over {int(age_seconds // 60)} minutes ago that never completed"
            if hung
            else f"no completed fast-monitor run in over {int(age_seconds // 60)} minutes"
        )
        return {"healthy": False, "reason": reason, "heartbeat": heartbeat, "age_seconds": age_seconds}

    return {"healthy": True, "reason": "", "heartbeat": heartbeat, "age_seconds": age_seconds}


_FAST_MONITOR_UNHEALTHY_ALERT_MESSAGE = (
    "The fast order monitor's scheduler appears misconfigured or stalled - no recent completed run has been "
    "recorded. Fills and exits still rely on the slower 5-minute scan until this is fixed. Check the "
    "fast-monitor-trigger cron job configuration."
)


def _alert_admins_fast_monitor_unhealthy_if_needed() -> None:
    """Called once per full 5-minute scan tick (from api_autonomy_cron_trigger,
    not per-user) - fires a manual alert to every admin account when
    _fast_monitor_health_status reports unhealthy. Deliberately uses a
    FIXED message text (no elapsed-minutes count baked in) so
    add_manual_alert's own content-hash dedup (see alerts._build_alert_id)
    makes repeated calls across ticks a no-op rather than spamming a new
    alert every 5 minutes while the condition persists - this is what makes
    it "one-shot": the first tick that observes staleness creates the
    alert, every later tick while still unhealthy just re-affirms the same
    alert id, and no explicit "already alerted" flag is needed. Does
    nothing while healthy; an admin dismissing the alert does not change
    heartbeat state, so it reappears (same id, effectively unchanged) if
    the condition is still true next tick, mirroring the freeze banner's
    own "dismissing doesn't clear it, only actual recovery does" behavior."""
    try:
        health = _fast_monitor_health_status()
    except Exception:  # noqa: BLE001 - an alerting/observability check must never crash the caller's real work (the cron tick that has actual reconciliation left to do)
        return
    if health.get("healthy"):
        return
    for user_id in list_all_user_ids():
        if not is_admin(user_id):
            continue
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "fast_monitor_unhealthy",
                    "ticker": "",
                    "priority": "critical",
                    "message": _FAST_MONITOR_UNHEALTHY_ALERT_MESSAGE,
                },
            )
        except Exception:  # noqa: BLE001 - one admin's alert write failing must not block the scan tick
            logger.exception("Failed to record fast-monitor-unhealthy alert for admin user_id=%s", user_id)


# How stale full_scan_heartbeat.py's last-completed (or last-started, if
# it's never completed even once) stamp can get before the full 5-minute
# scan's OWN scheduler is treated as unhealthy.
#
# Same recalibration, and the same evidence, as
# FAST_MONITOR_HEARTBEAT_STALE_SECONDS above: this is driven by a GitHub
# Actions workflow (.github/workflows/autonomous-scan-scheduler.yml,
# configured `*/5`), not a Render Cron Job, and real-world gaps of
# 15-70+ minutes have been directly observed via the GitHub Actions API
# even on that schedule. The original 900s threshold was set assuming a
# ~300s real cadence this platform doesn't actually deliver, and would
# false-positive on ordinary GitHub scheduling jitter multiple times a
# day. Widened to comfortably clear the worst gap observed so far.
#
# Only applies INSIDE the scheduler's own active window
# (_within_scheduled_trigger_window) - see FULL_SCAN_HEARTBEAT_MAX_GAP_SECONDS
# for the much wider threshold used outside it (nights/weekends), where a
# long gap is expected, not a fault - see _fast_monitor_health_status's
# own docstring for the fuller reasoning, shared by this mirror function.
FULL_SCAN_HEARTBEAT_STALE_SECONDS = 5400

# Same outer bound, and the same reasoning, as
# FAST_MONITOR_HEARTBEAT_MAX_GAP_SECONDS above.
FULL_SCAN_HEARTBEAT_MAX_GAP_SECONDS = 345600  # 4 days


def _full_scan_health_status() -> Dict[str, object]:
    """LOCAL-ONLY (no broker calls) mirror of _fast_monitor_health_status,
    but for the FULL 5-minute scan's own cron job
    (/api/autonomy/cron-trigger) instead of the faster reconciliation-only
    one. See full_scan_heartbeat.py."""
    heartbeat = get_full_scan_heartbeat_status()
    now = _now_utc()
    if not heartbeat:
        return {"healthy": False, "reason": "the full scan has never run - its scheduler may not be configured", "heartbeat": heartbeat, "age_seconds": None}

    last_completed_at_raw = heartbeat.get("last_completed_at")
    last_started_at_raw = heartbeat.get("last_started_at")
    most_recent_run_completed = heartbeat.get("last_started_run_id") and heartbeat.get("last_started_run_id") == heartbeat.get("last_completed_run_id")

    if most_recent_run_completed:
        reference_raw = last_completed_at_raw
        hung = False
    else:
        reference_raw = last_started_at_raw or last_completed_at_raw
        hung = True

    reference_at = _parse_trusted_past_timestamp(reference_raw, now=now, default=now)
    age_seconds = (now - reference_at).total_seconds()
    # Hung-run detection stays on the tight intraday threshold regardless
    # of the trigger window; only a clean completion's staleness gets the
    # window-aware, wider tolerance - see _fast_monitor_health_status.
    threshold = FULL_SCAN_HEARTBEAT_STALE_SECONDS if hung else _effective_heartbeat_stale_threshold(
        FULL_SCAN_HEARTBEAT_STALE_SECONDS, FULL_SCAN_HEARTBEAT_MAX_GAP_SECONDS, now
    )

    if age_seconds >= threshold:
        reason = (
            f"the full scan started a run over {int(age_seconds // 60)} minutes ago that never completed"
            if hung
            else f"no completed full-scan run in over {int(age_seconds // 60)} minutes"
        )
        return {"healthy": False, "reason": reason, "heartbeat": heartbeat, "age_seconds": age_seconds}

    return {"healthy": True, "reason": "", "heartbeat": heartbeat, "age_seconds": age_seconds}


_FULL_SCAN_UNHEALTHY_ALERT_MESSAGE = (
    "The full 5-minute autonomous-scan's scheduler appears misconfigured or stalled - no recent completed run has "
    "been recorded. This is the scan that resumes ordinary transitional orders when the faster optional monitor's "
    "own cron job isn't configured, and the one that places new autonomous entries. Check the cron-trigger cron "
    "job configuration."
)


def _alert_admins_full_scan_unhealthy_if_needed() -> None:
    """Called once per fast-monitor-trigger tick (NOT per-user) - the
    cross-check counterpart to _alert_admins_fast_monitor_unhealthy_if_needed:
    that function lets the full 5-minute scan detect the FASTER monitor's
    scheduler going silent; this one lets the FASTER monitor detect the
    full scan's own scheduler going silent. Neither scheduler can ever
    detect its OWN silence - only the other one calling it can. If BOTH
    schedulers stop firing at once, neither cross-check can fire either,
    which is exactly why an EXTERNAL, unauthenticated health-check
    endpoint also exists (see api_autonomy_monitor_health) for a
    third-party uptime service to poll independently of both. Same
    fixed-message, content-hash-deduped one-shot pattern as its
    counterpart."""
    try:
        health = _full_scan_health_status()
    except Exception:  # noqa: BLE001 - an alerting/observability check must never crash the caller's real work
        return
    if health.get("healthy"):
        return
    for user_id in list_all_user_ids():
        if not is_admin(user_id):
            continue
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "full_scan_unhealthy",
                    "ticker": "",
                    "priority": "critical",
                    "message": _FULL_SCAN_UNHEALTHY_ALERT_MESSAGE,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record full-scan-unhealthy alert for admin user_id=%s", user_id)


# How stale continuous_monitor_heartbeat.py's signals can get before the
# continuous monitor (Option A - a separately-deployed worker calling
# /api/autonomy/continuous-monitor-tick every ~10s) is treated as
# unhealthy. Set to a modest multiple of the WORKER's intended interval
# (CONTINUOUS_MONITOR_DEFAULT_INTERVAL_SECONDS) rather than a large fixed
# number like the slower schedulers use - a worker calling every 10s that
# goes quiet for even a minute is already a meaningfully different
# situation than a scheduler meant to fire every 5 minutes going quiet
# for a minute. Deliberately NOT hardcoded here as a bare literal - it's
# a small multiple of the worker's own configured interval, defined once
# both are in the same place (see CONTINUOUS_MONITOR_DEFAULT_INTERVAL_SECONDS
# just below) so tightening/loosening the worker's cadence keeps this
# threshold sensibly scaled without a separate manual edit.
CONTINUOUS_MONITOR_DEFAULT_INTERVAL_SECONDS = 10
CONTINUOUS_MONITOR_HEARTBEAT_STALE_SECONDS = CONTINUOUS_MONITOR_DEFAULT_INTERVAL_SECONDS * 6  # 60s at the default 10s interval


def _continuous_monitor_health_status() -> Dict[str, object]:
    """LOCAL-ONLY (no broker calls) health read for the continuous
    monitor - unlike _fast_monitor_health_status/_full_scan_health_status
    (single started/completed pair), this tracks TWO independent signals
    (see continuous_monitor_heartbeat.py's module docstring):
      - last_request_received_at - proves the WORKER is alive and
        successfully reaching/authenticating to this endpoint, regardless
        of whether reconciliation itself then succeeds;
      - last_completed_at - proves the ENDPOINT's own reconciliation
        logic completed without hanging, for the request that most
        recently arrived.
    Unhealthy if EITHER signal is stale - a fresh "received" with a stale
    "completed" specifically means the endpoint's own logic is stuck (the
    worker is fine); a stale "received" means the worker itself has gone
    quiet (regardless of how healthy the endpoint's last completion once
    was)."""
    heartbeat = get_continuous_monitor_heartbeat_status()
    now = _now_utc()
    if not heartbeat or not heartbeat.get("last_request_received_at"):
        return {"healthy": False, "reason": "the continuous monitor worker has never called this endpoint - it may not be deployed/configured", "heartbeat": heartbeat, "age_seconds": None}

    received_at = _parse_trusted_past_timestamp(heartbeat.get("last_request_received_at"), now=now, default=now)
    received_age_seconds = (now - received_at).total_seconds()
    if received_age_seconds >= CONTINUOUS_MONITOR_HEARTBEAT_STALE_SECONDS:
        return {
            "healthy": False,
            "reason": f"the continuous monitor worker has not called this endpoint in over {int(received_age_seconds)}s - the worker process may be down",
            "heartbeat": heartbeat,
            "age_seconds": received_age_seconds,
        }

    most_recent_completed = heartbeat.get("last_request_run_id") and heartbeat.get("last_request_run_id") == heartbeat.get("last_completed_run_id")
    if not most_recent_completed:
        # The worker IS reaching us (received_age_seconds just passed),
        # but the most recent request never recorded a completion - the
        # endpoint's own reconciliation logic for that specific request
        # is what's hung, not the worker.
        return {
            "healthy": False,
            "reason": "the continuous monitor worker is calling this endpoint, but its most recent request never completed - reconciliation logic may be stuck",
            "heartbeat": heartbeat,
            "age_seconds": received_age_seconds,
        }

    completed_at = _parse_trusted_past_timestamp(heartbeat.get("last_completed_at"), now=now, default=now)
    completed_age_seconds = (now - completed_at).total_seconds()
    if completed_age_seconds >= CONTINUOUS_MONITOR_HEARTBEAT_STALE_SECONDS:
        return {
            "healthy": False,
            "reason": f"no completed continuous-monitor reconciliation in over {int(completed_age_seconds)}s",
            "heartbeat": heartbeat,
            "age_seconds": completed_age_seconds,
        }

    return {"healthy": True, "reason": "", "heartbeat": heartbeat, "age_seconds": max(received_age_seconds, completed_age_seconds)}


_CONTINUOUS_MONITOR_UNHEALTHY_ALERT_MESSAGE = (
    "The continuous order monitor (the ~10-second worker) appears to be down or stalled - no recent activity has "
    "been recorded. Fills, exits, and protection gaps still rely on the slower fast-monitor/full-scan schedulers "
    "until this is fixed. Check the continuous monitor worker's deployment/logs."
)


def _alert_admins_continuous_monitor_unhealthy_if_needed() -> None:
    """Called once per full 5-minute scan tick (NOT per-user), alongside
    _alert_admins_fast_monitor_unhealthy_if_needed - the full scan is the
    one scheduler that's already required regardless of whether either
    faster mechanism is configured, making it the natural place to detect
    and surface either of them going silent. Same fixed-message,
    content-hash-deduped one-shot pattern as its siblings."""
    try:
        health = _continuous_monitor_health_status()
    except Exception:  # noqa: BLE001 - an alerting/observability check must never crash the caller's real work
        return
    if health.get("healthy"):
        return
    for user_id in list_all_user_ids():
        if not is_admin(user_id):
            continue
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "continuous_monitor_unhealthy",
                    "ticker": "",
                    "priority": "critical",
                    "message": _CONTINUOUS_MONITOR_UNHEALTHY_ALERT_MESSAGE,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record continuous-monitor-unhealthy alert for admin user_id=%s", user_id)


def _record_monitor_attempt(order: Dict[str, object], *, progressed: bool, error: Optional[str], now_iso: str) -> None:
    """Stamps failure-tracking fields on ONE overnight_orders entry after
    ONE _monitor_transitional_orders attempt - called for every outcome
    (success-with-progress, success-with-no-progress, a raised exception,
    or a malformed record this app couldn't even attempt), never skipped
    for any of them: "broker lookup failures and malformed transitional
    records must count as failed monitor attempts and start/advance the
    stuck timer" - a silent `continue` on exception would make exactly
    the entries most in need of escalation invisible to it.

    monitor_last_attempt_at is updated unconditionally, every call.
    monitor_first_failure_at is stamped the first time progress stalls and
    left untouched on every SUBSEQUENT stalled attempt (so it keeps
    reflecting when the stall actually BEGAN, for
    _has_stuck_transitional_orders_locally's threshold check) - cleared
    the moment progress resumes. monitor_attempt_count counts CONSECUTIVE
    no-progress attempts, reset to 0 on progress. monitor_last_error holds
    the most recent attempt's error, if any - cleared on any attempt that
    didn't raise, even one that also made no progress (an inconclusive
    but errorless check still means the LAST thing that happened wasn't
    an error)."""
    order["monitor_last_attempt_at"] = now_iso
    if progressed:
        order["monitor_first_failure_at"] = None
        order["monitor_attempt_count"] = 0
        order["monitor_last_error"] = None
        return
    if not order.get("monitor_first_failure_at"):
        order["monitor_first_failure_at"] = now_iso
    order["monitor_attempt_count"] = int(order.get("monitor_attempt_count") or 0) + 1
    order["monitor_last_error"] = error


def _alert_if_entry_newly_stuck(user_id: str, order: Dict[str, object]) -> None:
    """Fires a CRITICAL, ticker-scoped alert the first tick an individual
    entry's own monitor_first_failure_at crosses MONITOR_STUCK_FREEZE_SECONDS -
    called right after _record_monitor_attempt, for every outcome (no
    forward progress this tick, whatever the reason). Deliberately a FIXED
    message per entry (keyed by entry_client_order_id, no elapsed-minutes
    count baked in) so add_manual_alert's own content-hash dedup (see
    alerts._build_alert_id) makes this naturally one-shot per entry - the
    same pattern as _alert_admins_fast_monitor_unhealthy_if_needed. This is
    intentionally SEPARATE from the account-wide freeze itself
    (_has_stuck_transitional_orders_locally / _has_unresolved_ambiguous_submission_locally,
    which govern whether NEW entries are blocked) - dismissing this alert
    from the notifications drawer only marks the ALERT read/dismissed; it
    has no effect on that freeze predicate, which is a pure, independent
    read of monitor_first_failure_at/lifecycle_state every time it's
    checked. See test_dismissing_the_stuck_monitor_alert_does_not_release_the_freeze."""
    stuck_since_raw = order.get("monitor_first_failure_at")
    if not stuck_since_raw:
        return
    now = _now_utc()
    stuck_since = _parse_trusted_past_timestamp(stuck_since_raw, now=now, default=now)
    if (now - stuck_since).total_seconds() < MONITOR_STUCK_FREEZE_SECONDS:
        return
    ticker = str(order.get("ticker", "") or "")
    entry_client_order_id = str(order.get("entry_client_order_id", "") or "unknown")
    try:
        add_manual_alert(
            user_id,
            {
                "type": "monitor_stuck_freeze",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker} (entry {entry_client_order_id}): the fast order monitor has made no forward "
                    f"progress on this entry for over {MONITOR_STUCK_FREEZE_SECONDS // 60} minutes - safe "
                    "recovery can no longer be confidently proven. New autonomous entries are frozen account-wide "
                    "until this is resolved. Dismissing this alert does NOT lift the freeze - only genuine forward "
                    "progress on this entry (or manual intervention) does."
                ),
            },
        )
    except Exception:  # noqa: BLE001 - never let alerting itself break the monitor tick
        pass


def _check_position_absent_while_stuck(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry: Dict[str, object],
) -> bool:
    """Only meaningful for an entry stuck in PROTECTION_FAILED. Found live
    2026-08-31: a real SLB entry's stop leg was confirmed CANCELLED (see
    the stale-stop replacement fix, _confirm_and_finalize_protection) -
    but placing a fresh one then hit Webull's OPENAPI_GENERATE_NEW_SHORT_POSITION,
    revealing the broker's real position for this ticker was already ZERO,
    closed by some means this app's own tracked orders don't explain.
    Without this check, the resize path would keep re-attempting (and
    Webull would keep correctly rejecting) a stop for shares that no
    longer exist, forever.

    Deliberately does NOT auto-close the entry on position absence alone -
    matches _reconcile_closed_ticker_exit_orders' own established
    discipline ("position absence alone is never sufficient evidence" -
    see its own docstring). Only a tracked leg confirmed FILLED explains
    an exit well enough to record real P&L and close automatically; a
    CANCELLED stop plus an absent position together describe a genuine,
    unexplained gap this app has no business guessing at. Instead: flags
    entry["position_absent_unexplained"] = True (the caller uses this to
    skip the pointless protective-leg placement attempt for this pass),
    persists the evidence gathered, and fires ONE fixed-content critical
    alert (content-hash deduped, one-shot per entry - same pattern as
    _alert_if_entry_newly_stuck) pointing an admin at
    /api/admin/reconcile-position-absent for a real, human-reviewed
    resolution - never an automatic guess at what happened.

    Returns True if the entry is (now, or already) flagged - the caller
    uses this to skip the normal resize/confirm attempt this pass.

    Deliberately does NOT run this check for direction="short" entries
    yet (2026-09-02) - live_quantity > 0 below assumes the long-only sign
    convention this app has always observed (a held position reads as a
    positive quantity). Whether Webull represents a SHORT position as a
    negative quantity, a positive quantity with a separate side field, or
    something else has never been empirically observed - the exact same
    unconfirmed-schema concern an explicit prior reviewer instruction
    already disabled automatic short-covering over (see
    _reconcile_both_legs_filled_emergency's own docstring). Misreading
    that sign here could flag a genuinely still-open short as absent, or
    vice versa - so this returns False (inconclusive, not evidence either
    way) for a short until that schema is confirmed via a controlled,
    human-approved sandbox observation, matching this app's own
    established discipline of never building on unverified broker
    behavior."""
    if entry.get("direction") == "short":
        return False
    if entry.get("position_absent_unexplained"):
        return True
    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    except Exception:  # noqa: BLE001 - inconclusive, never treated as evidence either way
        return False
    live_quantity = next(
        (float(position.get("quantity", 0) or 0) for position in positions if str(position.get("symbol", "")).upper() == ticker.upper()),
        0.0,
    )
    if live_quantity > 0:
        return False  # position genuinely still held - not this situation

    stop_client_order_id = entry.get("stop_client_order_id")
    stop_status: Optional[str] = None
    if stop_client_order_id:
        try:
            stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
            stop_status = ol.summarize_fill(stop_detail)["status"]
        except Exception:  # noqa: BLE001
            stop_status = None

    if stop_status == "FILLED":
        # A tracked leg DOES explain the exit after all - not this
        # function's job; the normal resize/confirm path will itself find
        # the stop non-active, and _reconcile_position_exit-style handling
        # is the right place for a genuine FILLED-leg exit, not a guess
        # made here.
        return False

    entry["position_absent_unexplained"] = True
    entry["position_absent_evidence"] = {
        "checked_at": _now_utc().isoformat(),
        "stop_client_order_id": stop_client_order_id,
        "stop_status": stop_status,
        "live_quantity": live_quantity,
    }
    try:
        add_manual_alert(
            user_id,
            {
                "type": "position_absent_while_stuck",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker}: this app was still trying to protect a position it believes is held, but the "
                    "broker's own positions list shows ZERO shares - and no tracked protective leg confirms a "
                    "FILLED status that would explain how it closed. This app will not guess at what happened or "
                    "invent a P&L - review the real broker order history for this ticker and resolve via "
                    "/api/admin/reconcile-position-absent once confirmed. New autonomous entries stay frozen for "
                    "this account until this is resolved."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return True


def _check_position_absent_while_active(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry: Dict[str, object],
) -> bool:
    """The PROTECTION_CONFIRMED_ACTIVE counterpart to
    _check_position_absent_while_stuck - same detection logic (position
    absent from the broker's positions list, corroborated against each
    tracked leg's own status before concluding anything, per
    _reconcile_closed_ticker_exit_orders' "position absence alone is
    never sufficient evidence" discipline), but for an entry the app
    still believes is healthy and actively protected, not one already
    stuck. Found live 2026-09-03: a real PLTR position was closed
    directly at the broker (not through either tracked leg filling) -
    the stop leg shows CANCELLED, not FILLED, so neither
    _reconcile_position_exit's own fill-based detection nor
    _reconcile_closed_ticker_exit_orders' broader sweep (which also
    requires a FILLED leg before touching anything) could ever explain
    it, leaving the entry stuck reporting PROTECTION_CONFIRMED_ACTIVE
    forever with no path to ever record its real close.

    Called from _reconcile_position_exit ONLY after it has already
    concluded neither leg shows a fill this pass - never races or
    duplicates that normal, PRIMARY detection path.

    Deliberately does NOT freeze new entries account-wide the way the
    STUCK counterpart does - that freeze exists because a stuck, failed-
    protection entry represents genuine live risk (a position that might
    be open and unprotected); this case is the opposite: the broker
    confirms zero shares held, so there is no exposure to protect at
    all. This is a pure bookkeeping/record-keeping gap (no P&L recorded,
    the trade journal shows a phantom open position), not a safety
    condition, so blocking otherwise-healthy new entries over it would
    be needlessly conservative. Flags entry["position_absent_unexplained"] =
    True (same field _resolve_position_absent_reconciliation already
    resolves, regardless of which state flagged it), persists evidence,
    and fires one fixed-content critical alert - never auto-closes or
    invents a P&L; a human still resolves it via
    /api/admin/reconcile-position-absent.

    Deliberately does NOT run for direction="short" entries yet, for the
    identical unconfirmed-sign-convention reason as the STUCK
    counterpart - see that function's own docstring.

    Returns True if the entry is (now, or already) flagged."""
    if entry.get("direction") == "short":
        return False
    if entry.get("position_absent_unexplained"):
        return True
    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    except Exception:  # noqa: BLE001 - inconclusive, never treated as evidence either way
        return False
    live_quantity = next(
        (float(position.get("quantity", 0) or 0) for position in positions if str(position.get("symbol", "")).upper() == ticker.upper()),
        0.0,
    )
    if live_quantity > 0:
        return False  # position genuinely still held - not this situation

    stop_client_order_id = entry.get("stop_client_order_id")
    stop_status: Optional[str] = None
    if stop_client_order_id:
        try:
            stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
            stop_status = ol.summarize_fill(stop_detail)["status"]
        except Exception:  # noqa: BLE001
            stop_status = None

    if stop_status == "FILLED":
        # A tracked leg DOES explain the exit after all - the caller's
        # own next pass through the normal fill-based path picks this up
        # correctly; not this function's job to act on it.
        return False

    entry["position_absent_unexplained"] = True
    entry["position_absent_evidence"] = {
        "checked_at": _now_utc().isoformat(),
        "stop_client_order_id": stop_client_order_id,
        "stop_status": stop_status,
        "live_quantity": live_quantity,
    }
    try:
        add_manual_alert(
            user_id,
            {
                "type": "position_absent_while_active",
                "ticker": ticker,
                "priority": "normal",
                "message": (
                    f"{ticker}: this app still shows this position as actively protected, but the broker's own "
                    "positions list shows ZERO shares - and no tracked protective leg confirms a FILLED status "
                    "that would explain how it closed (most likely closed directly at the broker, outside this "
                    "app). No P&L has been recorded and new autonomous entries are NOT blocked by this (the "
                    "broker confirms nothing is actually at risk) - but resolve via "
                    "/api/admin/reconcile-position-absent when convenient so the trade journal reflects reality."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return True


def _resolve_position_absent_reconciliation(
    target_user_id: str, admin_user_id: str, entry_client_order_id: str, reason: str, confirmation: str
) -> Dict[str, object]:
    """The ONLY code path allowed to close an entry flagged
    position_absent_unexplained (see _check_position_absent_while_stuck's
    and _check_position_absent_while_active's own docstrings for the two
    real incidents this covers - a PROTECTION_FAILED entry whose position
    turned out already gone, and a PROTECTION_CONFIRMED_ACTIVE entry
    closed directly at the broker outside any tracked leg). Mirrors
    _resolve_ambiguous_submission's own discipline for the same reason:
    an incorrect close here would mean recording invented P&L for a real
    trade this app never actually confirmed the outcome of - so it never
    trusts the stored flag or evidence, always re-verifying fresh, right
    now.

    Requires BOTH a typed reason AND a typed confirmation (must exactly
    match the entry's own ticker) - same two-separate-inputs discipline as
    _resolve_ambiguous_submission, for the same reason (a stray click or
    an unread copy-paste must not execute this).

    Re-verifies against the entry's OWN stored account_id, not a freshly
    re-derived cash account - found live 2026-09-03 alongside the
    PROTECTION_CONFIRMED_ACTIVE case above: re-deriving the cash account
    here would silently re-verify a MARGIN-account short entry against
    the wrong account entirely. Falls back to re-deriving the cash
    account only for a legacy record that predates account_id always
    being stamped.

    The resulting closed_trade record is explicit about what this is:
    pnl_status="unknown_manual_reconciliation", gross/net_realized_pnl
    left None - this app never had a FILLED leg to compute a real exit
    price from, and inventing one would be worse than admitting it's
    unknown. The ADMIN's reason is the only human-provided account of what
    happened, recorded verbatim on the record for the actual trade history
    to reflect it."""
    orders = list_overnight_orders(target_user_id)
    entry = next((order for order in orders if order.get("entry_client_order_id") == entry_client_order_id), None)
    if entry is None:
        raise ValidationError(f"No entry found for target_user_id={target_user_id} with entry_client_order_id={entry_client_order_id!r}.")
    if entry.get("lifecycle_state") not in (ol.PROTECTION_FAILED, ol.PROTECTION_CONFIRMED_ACTIVE) or not entry.get("position_absent_unexplained"):
        raise ValidationError(
            f"This entry is not currently flagged position_absent_unexplained (lifecycle_state={entry.get('lifecycle_state')})."
        )
    ticker = str(entry.get("ticker", ""))
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    if not confirmation or confirmation.strip().upper() != ticker.strip().upper():
        raise ValidationError(f"Confirmation text must exactly match the ticker ({ticker!r}) to proceed.")

    creds = get_webull_credentials(target_user_id)
    accounts = get_accounts(target_user_id)
    webull_account = next((account for account in accounts if account.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("This user's Webull account is not connected - cannot re-verify fresh.")
    account_id = entry.get("account_id")
    if not account_id:
        sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
        if not cash_account:
            raise ValidationError("No Webull sandbox account found for this user's credentials.")
        account_id = cash_account["account_id"]

    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    except Exception as error:  # noqa: BLE001
        raise ValidationError(f"Could not re-verify the live position - broker call failed: {error}") from error
    live_quantity = next(
        (float(position.get("quantity", 0) or 0) for position in positions if str(position.get("symbol", "")).upper() == ticker.upper()),
        0.0,
    )
    if live_quantity > 0:
        raise ValidationError(f"Cannot resolve - the broker now shows {live_quantity:g} shares of {ticker} held. The position is not actually absent.")

    stop_client_order_id = entry.get("stop_client_order_id")
    if stop_client_order_id:
        try:
            stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
            stop_status = ol.summarize_fill(stop_detail)["status"]
        except Exception as error:  # noqa: BLE001
            raise ValidationError(f"Could not re-verify the stop leg's status - broker call failed: {error}") from error
        if stop_status == "FILLED":
            raise ValidationError(
                f"Cannot resolve as unexplained - the stop leg now shows FILLED, which DOES explain the exit. "
                "This entry should be reconciled through the normal fill-detection path instead, not this route."
            )

    trade_id = str(entry.get("entry_client_order_id") or "")
    closed_record = {
        "ticker": ticker,
        "side": "SELL" if entry.get("direction") == "short" else "BUY",
        "entry_client_order_id": entry.get("entry_client_order_id"),
        "stop_client_order_id": stop_client_order_id,
        "target_client_order_id": None,
        "requested_quantity": entry.get("quantity"),
        "filled_quantity": entry.get("filled_quantity"),
        "average_entry_price": entry.get("average_entry_fill_price"),
        "exit_type": "unknown",
        "exited_quantity": entry.get("filled_quantity"),
        "average_exit_price": None,
        "entry_timestamp": entry.get("logged_at"),
        "exit_timestamp": _now_utc().isoformat(),
        "gross_realized_pnl": None,
        "fees": None,
        "net_realized_pnl": None,
        "pnl_status": "unknown_manual_reconciliation",
        "strategy": entry.get("strategy"),
        "close_reason": "manual_reconciliation_position_absent",
        "broker_evidence": {"live_quantity_at_resolution": live_quantity, "stop_status_at_resolution": stop_status if stop_client_order_id else None},
        "resolved_by_admin": admin_user_id,
        "resolution_reason": reason.strip(),
        "reconciled_at": _now_utc().isoformat(),
    }
    record_closed_trade(target_user_id, trade_id, closed_record)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason="manual_reconciliation_position_absent")
    replace_overnight_orders(target_user_id, orders)
    logger.warning(
        "Manually reconciled position_absent_unexplained entry: user_id=%s ticker=%s entry_client_order_id=%s admin=%s reason=%s",
        target_user_id, ticker, entry_client_order_id, admin_user_id, reason.strip(),
    )
    return {"entry": entry}


@app.route("/api/admin/reconcile-position-absent", methods=["POST"])
def api_admin_reconcile_position_absent():
    """The admin-facing route for _resolve_position_absent_reconciliation -
    see its own docstring for the full discipline this delegates to."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        result = _resolve_position_absent_reconciliation(
            target_user_id=str(payload.get("user_id", "")),
            admin_user_id=_current_user_id(),
            entry_client_order_id=str(payload.get("entry_client_order_id", "")),
            reason=str(payload.get("reason", "")),
            confirmation=str(payload.get("confirmation", "")),
        )
    except ValidationError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success({"entry": result["entry"]}, ok=True, entry=result["entry"])


def _monitor_transitional_orders(user_id: str, creds: Dict[str, str], account_id: str) -> bool:
    """The fast, frequently-run per-order monitor - task list: "Build fast
    per-order monitor decoupled from the 5-minute scan". Processes every
    entry in one of TWO groups:
      - order_lifecycle.MONITOR_RESUMABLE_STATES (ENTRY_SUBMITTED through
        PROTECTION_FAILED): resumed via _reconcile_entry_fill_and_protection
        directly (single pass, no internal sleep loop - unlike
        _poll_fill_and_protect's bounded retry wrapper, which exists only
        for a caller wanting a fast INITIAL result; this monitor gets its
        own "next round" on its next tick instead of blocking this one).
      - PROTECTION_CONFIRMED_ACTIVE: checked for an EXIT first
        (_reconcile_position_exit - did a stop or target leg fill?), and
        only if no exit was found AND the entry's own order isn't yet
        broker-terminal, ALSO checked for further fill growth via
        _reconcile_entry_fill_and_protection. This is what makes "a
        transition to PROTECTION_PENDING must not stop fill monitoring"
        true system-wide, not just within one function call - see
        order_lifecycle.FILL_MONITORING_STATES.
    Every leg placement anywhere in this chain uses the SAME deterministic,
    VERSIONED client_order_ids (order_lifecycle.deterministic_client_order_id)
    - re-submitting an already-placed leg under the same id is Webull's
    own idempotency guard, never a duplicate; a genuine RESIZE uses a NEW
    version instead of assuming a same-id resubmission would resize
    anything (see _reconcile_protective_leg_quantity).

    UNKNOWN_SUBMISSION_STATE and MANUAL_LINK_IN_PROGRESS (order_lifecycle.FROZEN_STATES)
    are deliberately NOT in scope here - genuinely different problems (the
    broker's response to the ORIGINAL submission itself is unknown, or a
    manual admin resolution is mid-transaction) with their own
    audit/evidence requirements, already handled by
    _reconcile_unknown_submissions and _recover_incomplete_manual_resolutions
    respectively. Callers of this function are expected to run those two
    alongside it (see _run_fast_order_monitor and
    _run_autonomous_trade_scan_locked) so an account frozen by either one
    resumes as fast as this function's own calling cadence, not just once
    per 5-minute scan.

    Every entry processed here gets _record_monitor_attempt called on it,
    for EVERY outcome including a raised exception or a malformed record
    (missing ticker/entry_client_order_id) - none of those are silently
    skipped. See _has_stuck_transitional_orders_locally: an entry stuck
    long enough (by that tracking) freezes new entries account-wide, the
    same "safe recovery cannot be proven" posture UNKNOWN_SUBMISSION_STATE
    already takes for a different kind of uncertainty.

    Best-effort per entry, same pattern as _reconcile_unknown_submissions -
    one entry's broker call failing must not block the others or this
    function's own return value for the rest.

    Returns True if ANY entry this function is responsible for is still
    non-terminal after this pass.

    Account-aware since 2026-09-02 (short-selling work): a caller with a
    mix of cash-account (long) and margin-account (short) entries must
    call this ONCE PER ACCOUNT, each time with that account's own
    account_id - see _run_fast_order_monitor/_run_autonomous_trade_scan_locked.
    An order whose OWN stored account_id doesn't match the account_id
    THIS call is processing is skipped entirely - looking it up against
    the wrong account's credentials would be querying for an order that
    genuinely does not exist there. An order with no account_id at all
    (a legacy/test record from before this field was always stamped) is
    processed regardless - matching the original, single-account
    behavior exactly for anything that predates this distinction.

    instrument_type-aware since 2026-09-03 (real options trading): an
    "OPTION" entry (order.get("instrument_type") == "OPTION" - absent/
    "EQUITY" for every entry that predates this field, so this is a pure
    addition) routes to _check_and_execute_option_exit (from
    PROTECTION_CONFIRMED_ACTIVE) or _reconcile_option_entry_fill
    (everywhere else) instead of the equity-only functions. Known,
    documented gap matching this session's own short-selling precedent
    (orphan discovery/ambiguous-submission recovery not yet extended to
    margin): an option position closed OUTSIDE this app (manually, at the
    broker) is not yet detected here the way
    _check_position_absent_while_active/_stuck detect it for equity -
    get_account_positions' real response shape for an OPTION row has not
    been empirically confirmed. The failure mode is still safe, not
    silent: a stale entry's exit attempt would simply be rejected by the
    broker (nothing to sell) and surface as a normal monitor error via
    _record_monitor_attempt, not lost."""
    orders = [
        order for order in list_overnight_orders(user_id)
        if not order.get("account_id") or order.get("account_id") == account_id
    ]
    resumable = [order for order in orders if order.get("lifecycle_state") in ol.MONITOR_RESUMABLE_STATES]
    exit_checkable = [order for order in orders if order.get("lifecycle_state") == ol.PROTECTION_CONFIRMED_ACTIVE]
    now_iso = _now_utc().isoformat()
    changed = False

    for order in resumable + exit_checkable:
        entry_client_order_id = order.get("entry_client_order_id")
        ticker = str(order.get("ticker", ""))
        trading_day = str(order.get("trading_day") or "")
        if not entry_client_order_id or not ticker:
            _record_monitor_attempt(order, progressed=False, error="malformed transitional record: missing entry_client_order_id or ticker", now_iso=now_iso)
            _alert_if_entry_newly_stuck(user_id, order)
            changed = True
            continue

        state_before = order.get("lifecycle_state")
        is_option = order.get("instrument_type") == "OPTION"
        try:
            if is_option:
                if state_before == ol.PROTECTION_CONFIRMED_ACTIVE:
                    _check_and_execute_option_exit(user_id, creds, account_id, ticker, trading_day, order)
                else:
                    _reconcile_option_entry_fill(user_id, creds, account_id, str(entry_client_order_id), order)
            elif state_before == ol.PROTECTION_CONFIRMED_ACTIVE:
                exited = _reconcile_position_exit(user_id, creds, account_id, ticker, trading_day, order)
                if not exited and not order.get("entry_order_terminal"):
                    _reconcile_entry_fill_and_protection(
                        user_id=user_id,
                        creds=creds,
                        account_id=account_id,
                        ticker=ticker,
                        entry_client_order_id=str(entry_client_order_id),
                        limit_price=float(order.get("limit_price") or 0),
                        stop_price=float(order.get("stop") or 0),
                        target_price=float(order.get("target") or 0),
                        trading_day=trading_day,
                        entry=order,
                    )
                elif not exited and order.get("entry_order_terminal"):
                    # Entry is fully filled and broker-terminal, so
                    # _reconcile_entry_fill_and_protection above is never
                    # called again for this entry (see its own docstring) -
                    # this is the ONLY remaining check for "did the
                    # resting stop leg silently go dead while the position
                    # is still held" (see _check_and_rearm_dead_stop's own
                    # docstring for the real gap this closes, confirmed
                    # live 2026-09-03).
                    _check_and_rearm_dead_stop(user_id, creds, account_id, ticker, trading_day, order)
            elif state_before == ol.PROTECTION_FAILED and _check_position_absent_while_stuck(user_id, creds, account_id, ticker, order):
                pass  # flagged position_absent_unexplained this pass or already - skip the pointless resize attempt, see the function's own docstring
            else:
                _reconcile_entry_fill_and_protection(
                    user_id=user_id,
                    creds=creds,
                    account_id=account_id,
                    ticker=ticker,
                    entry_client_order_id=str(entry_client_order_id),
                    limit_price=float(order.get("limit_price") or 0),
                    stop_price=float(order.get("stop") or 0),
                    target_price=float(order.get("target") or 0),
                    trading_day=trading_day,
                    entry=order,
                )
        except Exception as error:  # noqa: BLE001 - one bad record shouldn't block the others or this monitor tick itself
            _record_monitor_attempt(order, progressed=False, error=str(error), now_iso=now_iso)
            _alert_if_entry_newly_stuck(user_id, order)
            changed = True
            continue

        changed = True
        # Reaching here means the whole try block above completed WITHOUT
        # raising - a real, fresh, successful check against the broker
        # this tick, whether or not the lifecycle_state LABEL happened to
        # change. That distinction matters: found live 2026-09-03, the
        # previous definition (progressed = lifecycle_state != state_before)
        # meant ANY perfectly healthy, actively-protected position with
        # nothing new to report - the overwhelmingly common case for a
        # normal trade sitting at its stop/target for a while - was marked
        # "no progress" on EVERY SINGLE TICK, forever, since a stable state
        # never "changes". After MONITOR_STUCK_FREEZE_SECONDS (30 min) that
        # silently tripped the SAME account-wide freeze meant for genuinely
        # uncertain entries, blocking new autonomous entries over a
        # position that was never actually wrong - exactly what happened
        # to a real ADBE position. The function this feeds
        # (_has_stuck_transitional_orders_locally) documents its own intent
        # as "safe recovery cannot be proven" - a real, RAISED exception
        # (network/broker failure, an unresolved resize, ambiguous
        # evidence - still caught above and still marked progressed=False)
        # is genuine uncertainty; a clean, successful re-verification that
        # a position is still fine, or a clean flag-and-alert for a human
        # to review (_check_position_absent_while_stuck reaching its own
        # short-circuit) is not - both are proof this app DID just confirm
        # reality, not evidence it can't.
        _record_monitor_attempt(order, progressed=True, error=None, now_iso=now_iso)
        _alert_if_entry_newly_stuck(user_id, order)

    if changed:
        replace_overnight_orders(user_id, orders)

    return any(
        order.get("lifecycle_state") in ol.MONITOR_RESUMABLE_STATES
        or (order.get("lifecycle_state") == ol.PROTECTION_CONFIRMED_ACTIVE and not order.get("entry_order_terminal"))
        for order in orders
    )


def _has_stuck_transitional_orders_locally(user_id: str) -> bool:
    """Fast, LOCAL-ONLY check (no broker calls) mirroring
    _has_unresolved_ambiguous_submission_locally's own contract - True if
    any entry has been sitting with NO forward progress
    (_monitor_transitional_orders' monitor_first_failure_at, via
    _record_monitor_attempt) for at least MONITOR_STUCK_FREEZE_SECONDS.
    This is "safe recovery cannot be proven", not "the position is
    definitely unsafe" - the position may well still be fine (or even
    already fully protected at the broker, just unconfirmed by this
    app's own polling), but this app can no longer confidently prove that
    on its own, so it stops compounding the uncertainty with new capital
    while a human looks at it - the same reasoning
    UNKNOWN_SUBMISSION_STATE already applies to a different kind of
    uncertainty.

    Found live 2026-09-01: monitor_first_failure_at is stamped once and
    never cleared by _record_monitor_attempt on its own - resolving the
    entry through an entirely different path (e.g.
    _resolve_position_absent_reconciliation, which transitions straight to
    CLOSED) left it set forever, so this check kept reporting the account
    frozen on an entry that was actually fully resolved and terminal.
    ol.is_transitional(order) - the same check order_lifecycle.py's own
    is_transitional already defines for exactly this purpose - excludes
    anything CLOSED/terminal, regardless of what monitor_first_failure_at
    still says."""
    now = _now_utc()
    for order in list_overnight_orders(user_id):
        if not ol.is_transitional(order):
            continue
        stuck_since_raw = order.get("monitor_first_failure_at")
        if not stuck_since_raw:
            continue
        stuck_since = _parse_trusted_past_timestamp(stuck_since_raw, now=now, default=now)
        if (now - stuck_since).total_seconds() >= MONITOR_STUCK_FREEZE_SECONDS:
            return True
    return False


def _has_active_protection_gap_locally(user_id: str) -> bool:
    """Fast, LOCAL-ONLY check (no broker calls) - True if any NON-TERMINAL
    entry currently carries either:
      - entry["ambiguous_exit_unresolved"] - both protective legs appeared
        filled (see _reconcile_both_legs_filled_emergency) and this has
        not since resolved conclusively; or
      - entry["stop_protection_gap"] / entry["target_protection_gap"] -
        a leg was cancelled to resize it and its replacement failed to
        place even after an immediate retry (see
        _reconcile_protective_leg_quantity), so that leg is CONFIRMED
        GONE and NOT YET replaced - a genuine, current gap in coverage,
        not a hypothetical one.
    Both are IMMEDIATE, tick-granular freeze signals - deliberately
    independent of and faster-acting than
    _has_stuck_transitional_orders_locally's 30-minute no-progress
    threshold, since "a leg is confirmed cancelled and unreplaced" or
    "both legs appear to have filled" are urgent enough that waiting on
    the generic stall timer would be too slow. Cleared automatically only
    once the underlying condition is genuinely resolved - see each
    field's own setter/clearer for exactly when - never on a timer and
    never merely because a retry was ATTEMPTED. TERMINAL entries are
    skipped so a resolved-but-still-flagged historical record (the flag
    was never explicitly cleared on close, only found irrelevant) can't
    freeze an account forever."""
    for order in list_overnight_orders(user_id):
        if order.get("lifecycle_state") in ol.TERMINAL_STATES:
            continue
        if order.get("ambiguous_exit_unresolved"):
            return True
        if order.get("stop_protection_gap") or order.get("target_protection_gap"):
            return True
    return False


def _has_unresolved_ambiguous_submission_locally(user_id: str) -> bool:
    """Fast, LOCAL-ONLY check (no broker calls) for the persistent dashboard
    banner - reads whatever the last scan/reconciliation pass already
    persisted, so it's cheap enough to call on every page load. Mirrors
    _reconcile_unknown_submissions' own "is anything still unresolved"
    check, but never attempts to resolve anything itself - purely a read.

    Four INDEPENDENT signals, any one enough to freeze:
      - any entry sitting in one of order_lifecycle.FROZEN_STATES
        (UNKNOWN_SUBMISSION_STATE itself, or MANUAL_LINK_IN_PROGRESS - a
        manual link resolution still mid-flight);
      - find_incomplete_resolutions being non-empty - a manual resolution
        (release OR link) whose resolution_completed audit record was
        never durably confirmed written, EVEN IF the affected entry's own
        lifecycle_state has already moved past every FROZEN_STATE (a
        released entry is MANUALLY_RESOLVED_NO_ORDER; a completed link
        may already be PROTECTION_CONFIRMED_ACTIVE) - see
        _resolve_ambiguous_submission's docstring for why the closing
        audit write, not the state change itself, is what's allowed to
        lift the freeze;
      - _has_active_protection_gap_locally - an ambiguous double-fill exit
        or a confirmed-but-unreplaced protective leg, both immediate,
        tick-granular signals (see that function);
      - _has_stuck_transitional_orders_locally - an ORDINARY entry (never
        ambiguous, never manually resolved) that the fast per-order
        monitor has made no progress on for too long. This is also a
        LOCAL-ONLY disk read (the audit log and overnight_orders.json are
        both local files, not broker calls), so it doesn't violate this
        function's own no-broker-calls contract."""
    if any(order.get("lifecycle_state") in ol.FROZEN_STATES for order in list_overnight_orders(user_id)):
        return True
    if find_incomplete_resolutions(user_id):
        return True
    if _has_active_protection_gap_locally(user_id):
        return True
    return _has_stuck_transitional_orders_locally(user_id)


def _count_users_with_unresolved_ambiguous_submissions() -> int:
    """Same LOCAL-ONLY, no-broker-calls reasoning as
    _has_unresolved_ambiguous_submission_locally, but system-wide - drives
    the admin-facing variant of the persistent freeze banner (see base.html)
    so an admin whose OWN account is not frozen still sees, on every page
    (not just the dedicated /admin table), that some OTHER user's account
    is. Only called when the current user is already confirmed to be an
    admin - see _build_page_context."""
    count = 0
    for user in list_all_users():
        target_user_id = user.get("id", "")
        if target_user_id and _has_unresolved_ambiguous_submission_locally(target_user_id):
            count += 1
    return count


AMBIGUOUS_RESOLUTION_RELEASE = "release"
AMBIGUOUS_RESOLUTION_LINK = "link"


def _order_history_lookback_days(entry: Dict[str, object]) -> int:
    """The evidence-gathering lookback window must cover from the entry's
    OWN original submission date to now, not just a fixed recent window -
    a stale ambiguous entry that sat unresolved for a while would otherwise
    get a false "nothing found in order history" simply because its real
    historical record has aged out of a fixed 7-day default, not because
    it doesn't exist. Falls back to the module default if trading_day is
    missing or unparseable (an old/legacy entry) - fails toward a WIDER
    window, not a narrower one, on any parsing trouble."""
    trading_day_raw = str(entry.get("trading_day") or "")
    try:
        trading_day = datetime.fromisoformat(trading_day_raw).date()
    except ValueError:
        return webull_api.ORDER_HISTORY_LOOKBACK_DAYS
    days_since = (datetime.now(timezone.utc).date() - trading_day).days
    return max(webull_api.ORDER_HISTORY_LOOKBACK_DAYS, days_since + 1)


def _correlation_is_plausible(candidate: Dict[str, object], entry: Dict[str, object]) -> bool:
    """Sanity correlation for a client_order_id-matched candidate against
    what this entry actually recorded requesting - defense in depth beyond
    the exact ID match alone, and the gate between "found" (blocks
    release) and "found_strong" (required for link).

    Deliberately STRICT, not best-effort: a field that's simply MISSING on
    the candidate counts as inconclusive - this candidate does NOT qualify
    as a strong correlation - rather than being silently skipped as "no
    red flag". A prior version of this function treated a missing field as
    no evidence either way, on the reasoning that open_orders/order_history's
    row shape wasn't confirmed to expose it; that reasoning is backwards
    for what found_strong is actually used for (justifying LINK, which
    immediately attaches real protective orders to real shares) - the
    absence of a field this app needs to check should never make a
    candidate look MORE trustworthy than one where the field was present
    and simply happened to fail the check.

    Required fields: symbol, side, quantity, and price all present and
    consistent.
      - symbol/side are held to this bar because get_open_orders rows are
        ALREADY relied upon elsewhere in this app to reliably carry them -
        _compute_committed_virtual_capital raises rather than defaults if
        either is absent from an open_orders row, since a malformed
        open-order record there would silently under-count committed
        capital. That is the closest thing to a confirmed schema this app
        has for that endpoint (not an official Webull schema document -
        this app doesn't have one), so the same fields are required here,
        for the same endpoint, for consistency.
      - No equivalent load-bearing precedent exists yet for
        get_order_history's row shape - nothing else in this app treats
        any of its fields (beyond client_order_id itself) as reliably
        present. An order_history candidate is held to the exact same bar
        regardless, which in practice means it will not qualify as
        found_strong until that's established - the correct, honest
        default given no proof either way, not a guess in either
        direction.
      - Webull's account_id is not re-checked per candidate row: every
        evidence source is already queried with the ONE account_id this
        resolution is scoped to (see _gather_ambiguous_submission_evidence),
        so account identity is satisfied by construction, not by a
        per-row field - none of these endpoints have been confirmed to
        even return an account identifier on each row to check against.
      - Submission/fill TIMING is not checked - no field name for it has
        been confirmed present on any of get_order_detail/get_open_orders/
        get_order_history's response shapes anywhere in this app (the
        same "don't guess a field name" discipline applied to
        _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES in integrations/webull.py).
        This is a known, deliberate gap, not an oversight - implementing a
        timing check against a guessed field name would risk exactly the
        false confidence this whole function exists to avoid."""
    ticker = str(entry.get("ticker", "")).upper()
    candidate_symbol = candidate.get("symbol")
    if not candidate_symbol or not isinstance(candidate_symbol, str) or candidate_symbol.upper() != ticker:
        return False

    candidate_side = candidate.get("side")
    if not candidate_side or not isinstance(candidate_side, str) or candidate_side.upper() != "BUY":
        return False

    requested_quantity_raw = entry.get("quantity")
    candidate_quantity_raw = candidate.get("total_quantity")
    if requested_quantity_raw is None or candidate_quantity_raw is None:
        return False
    try:
        requested_quantity = float(requested_quantity_raw)
        candidate_quantity = float(candidate_quantity_raw)
    except (TypeError, ValueError):
        return False
    if requested_quantity <= 0 or candidate_quantity <= 0:
        return False
    if abs(candidate_quantity - requested_quantity) > max(1.0, requested_quantity * 0.05):
        return False

    requested_price_raw = entry.get("limit_price")
    candidate_price_raw = candidate.get("limit_price")
    if requested_price_raw is None or candidate_price_raw is None:
        return False
    try:
        requested_price = float(requested_price_raw)
        candidate_price = float(candidate_price_raw)
    except (TypeError, ValueError):
        return False
    if requested_price <= 0 or candidate_price <= 0:
        return False
    if abs(candidate_price - requested_price) > max(0.50, requested_price * 0.10):
        return False

    return True


def _gather_ambiguous_submission_evidence(creds: Dict[str, str], account_id: str, entry: Dict[str, object]) -> Dict[str, object]:
    """The MANDATORY fresh-check step before any manual resolution of an
    entry stuck in UNKNOWN_SUBMISSION_STATE - re-queries the broker across
    FOUR independent sources right now, at resolution time, rather than
    trusting whatever the automatic reconciliation pass last observed
    (which could be stale by the time an admin actually acts) or anything
    an admin might claim to have already checked themselves:
      - order_detail: the direct client_order_id lookup
        _reconcile_unknown_submission itself uses - STRONG evidence;
      - open_orders: is it currently resting, unfilled, at the broker,
        matched by client_order_id - STRONG evidence;
      - order_history: did it fill/cancel/reject within a lookback window
        that covers back to this entry's OWN submission date (see
        _order_history_lookback_days), matched by client_order_id -
        STRONG evidence;
      - positions: does the ticker show up as a currently-held position -
        WEAK evidence only. Webull's position API aggregates by symbol,
        not by originating order, so a match here can't be distinguished
        from a manual trade on the same ticker (the same known, accepted
        limitation noted elsewhere in this app) - it's real enough to
        block a RELEASE (conservative: don't release capital while
        something unexplained sits on this ticker) but never strong enough
        to justify a LINK (attaching protective orders to shares that
        might not even be this entry's).

    Each source is checked independently and its own success/failure is
    recorded separately - one flaky call must never make the OTHERS'
    evidence look more conclusive than it actually is. Every STRONG match
    is additionally sanity-correlated against this entry's own recorded
    quantity/limit_price (_correlation_is_plausible) - a mismatch demotes
    it out of "found_strong" (it still counts toward "found").

    Returns {"found": bool, "found_strong": bool, "checks": {...},
    "errors": {...}, "gathered_at": str}. "found" is True if ANY check
    (strong or weak) positively located something - used to block RELEASE.
    "found_strong" is True only if a client_order_id-correlated,
    quantity/price-plausible match was found - required for LINK. A check
    that itself failed (network error, broker error, an ambiguous
    classification) is recorded in "errors" and counts as neither finding
    nor not-finding anything - it's simply inconclusive, and
    _resolve_ambiguous_submission treats ANY entry in "errors" as reason
    enough to refuse a "release" outright."""
    ticker = str(entry.get("ticker", "")).upper()
    entry_client_order_id = str(entry.get("entry_client_order_id") or "")
    checks: Dict[str, object] = {}
    errors: Dict[str, str] = {}
    found = False
    found_strong = False

    try:
        detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, entry_client_order_id)
        fill = ol.summarize_fill(detail)
        checks["order_detail"] = fill
        found = True
        # get_order_detail is looked up BY entry_client_order_id already -
        # the strongest possible correlation, no further matching needed.
        found_strong = True
    except webull_api.DefiniteOrderRejection:
        checks["order_detail"] = None
    except Exception as error:  # noqa: BLE001 - inconclusive, not a "not found" - see docstring
        errors["order_detail"] = str(error)

    try:
        open_orders = webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id)
        matches = [order for order in open_orders if order.get("client_order_id") == entry_client_order_id]
        checks["open_orders"] = matches
        if matches:
            found = True
            found_strong = found_strong or any(_correlation_is_plausible(m, entry) for m in matches)
    except Exception as error:  # noqa: BLE001
        errors["open_orders"] = str(error)

    try:
        positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
        matches = [position for position in positions if str(position.get("symbol", "")).upper() == ticker]
        checks["positions"] = matches
        # Deliberately WEAK - see docstring - never contributes to found_strong.
        found = found or bool(matches)
    except Exception as error:  # noqa: BLE001
        errors["positions"] = str(error)

    try:
        history = webull_api.get_order_history(
            creds["app_key"], creds["app_secret"], account_id, days_back=_order_history_lookback_days(entry)
        )
        matches = [order for order in history if order.get("client_order_id") == entry_client_order_id]
        checks["order_history"] = matches
        if matches:
            found = True
            found_strong = found_strong or any(_correlation_is_plausible(m, entry) for m in matches)
    except Exception as error:  # noqa: BLE001
        errors["order_history"] = str(error)

    return {
        "found": found,
        "found_strong": found_strong,
        "checks": checks,
        "errors": errors,
        "gathered_at": _now_utc().isoformat(),
    }


def _record_resolution_failed(
    target_user_id: str, resolution_id: str, admin_user_id: str, entry_client_order_id: str, error: BaseException, stage: str
) -> None:
    """Best-effort - see _resolve_ambiguous_submission's docstring for why
    a failure writing THIS record must never itself raise: the orphaned
    resolution_started record is still sitting in the chain either way,
    which is exactly what keeps the account frozen (see
    ambiguous_resolution_audit.find_incomplete_resolutions) whether or not
    this closing "failed" stamp manages to land."""
    try:
        record_ambiguous_resolution_audit(
            target_user_id,
            {
                "phase": RESOLUTION_PHASE_FAILED,
                "resolution_id": resolution_id,
                "administrator": admin_user_id,
                "target_user_id": target_user_id,
                "entry_client_order_id": entry_client_order_id,
                "timestamp": _now_utc().isoformat(),
                "error": str(error),
                "stage": stage,
            },
        )
    except Exception:  # noqa: BLE001 - see docstring
        pass


def _resolve_ambiguous_submission(
    target_user_id: str, admin_user_id: str, entry_client_order_id: str, action: str, reason: str, confirmation: str
) -> Dict[str, object]:
    """The ONLY code path allowed to move an entry out of
    UNKNOWN_SUBMISSION_STATE outside the automatic (grace-period-gated,
    currently unreachable in production given the empty confirmed-code
    allowlist - see integrations/webull.py) reconciliation path. This is
    deliberately narrow and heavily audited: it's the sole way to unfreeze
    an account's autonomous entries once ambiguity has occurred, and an
    incorrect "release" here is exactly the failure mode (duplicate/
    unintended trading, or an unprotected position) the whole
    UNKNOWN_SUBMISSION_STATE mechanism exists to prevent in the first
    place - so it never trusts anything it wasn't handed a chance to verify
    itself.

    Requires BOTH a typed reason AND a typed confirmation (must exactly
    match the entry's own ticker, case/whitespace-insensitive) - two
    separate deliberate inputs, not one, so a stray click or a
    copy-pasted-without-reading reason can't execute an action the admin
    didn't actually mean to take on THIS specific entry. This is
    single-admin approval, not two-person approval - genuine two-person
    sign-off (a second, different administrator independently confirming
    before execution) is NOT implemented and is a HARD PREREQUISITE before
    this mechanism is ever used against a real-money account; it is
    acceptable only for the current paper-trading phase.

    Always re-gathers evidence FRESH, right now
    (_gather_ambiguous_submission_evidence) - never the evidence an admin
    might be looking at on a page that's since gone stale, and never a
    client-submitted "I checked, it's fine" claim. Every precondition
    check (action/reason/confirmation validity, the fresh-evidence gate
    itself) happens BEFORE the transaction described below even begins -
    a refused call raises here with NOTHING written yet, since nothing has
    happened yet to audit.

    From here on this is a PHASED transaction, not a single audit write
    followed by a state change - see the specific concern that motivated
    this: writing one "it succeeded" audit record before the state change
    and protection work is actually attempted can leave that record
    claiming success when persistence or protection LATER fails. Three
    audit phases (autonomy.ambiguous_resolution_audit.RESOLUTION_PHASE_*),
    sharing one resolution_id:
      - resolution_started: written FIRST, before any state change. Says
        what was requested and what evidence justified it. If even THIS
        write fails, this raises immediately with the entry completely
        untouched, same guarantee as before.
      - resolution_completed: written LAST, only once the state change (and,
        for link, protection) has fully succeeded AND been durably
        persisted. Only this record is allowed to claim success.
      - resolution_failed: written if anything between started and
        completed raises, tagged with the STAGE it broke at
        (state_persistence / protection / final_persistence) - best-effort
        (see _record_resolution_failed): if even this write fails, the
        orphaned resolution_started record left behind is itself the
        durable signal (see find_incomplete_resolutions) that keeps the
        account frozen regardless.

    A resolution_completed write that itself fails is handled differently
    on purpose: the state change already genuinely happened and was
    durably persisted by that point, so writing resolution_failed would
    misrepresent what happened. Nothing is written for it - the orphaned
    resolution_started record is left as the durable freeze marker,
    exactly as if this had never gotten a completed record in the first
    place, and either a later scan's restart recovery
    (_recover_incomplete_manual_resolutions) or a retried write closes the
    loop later. This is why _has_unresolved_ambiguous_submission_locally
    checks find_incomplete_resolutions independently of the entry's own
    lifecycle_state - a released or fully-linked entry can be sitting in a
    perfectly good final state while the account STILL correctly reads as
    frozen, because the transaction that produced it was never durably
    confirmed complete.

    action="release" (confirm no order or position exists): refuses unless
    the evidence found NOTHING (strong OR weak) *and* every one of the
    four checks actually SUCCEEDED - a check that itself failed is
    inconclusive, never "checked and clean". Once permitted: transitions
    directly to MANUALLY_RESOLVED_NO_ORDER (deliberately NOT ENTRY_FAILED
    - that state specifically means the BROKER said so; this means a
    human, on evidence, did) and persists it - a single atomic step, no
    intermediate state, since there's no async work in between. The
    original ambiguity fields (error, first_definite_rejection_at,
    definite_rejection_count) are left untouched; this only ADDS the
    administrator, reason, confirmation, resolution_id, and evidence
    snapshot alongside them.

    action="link" (resume monitoring): refuses unless evidence["found_strong"]
    - a client_order_id-correlated match, strictly sanity-checked against
    this entry's own recorded ticker/side/quantity/limit_price (see
    _correlation_is_plausible) - was found. Once permitted, this is FOUR
    distinct steps, each persisted or audited before the next begins:
      1. Transition to MANUAL_LINK_IN_PROGRESS and persist it immediately -
         BEFORE any polling or protective-order placement - so a crash
         from this point forward leaves the account correctly frozen via
         the entry's own on-disk lifecycle_state, not just the audit
         marker.
      2. Poll the linked order and protect any filled quantity
         (_poll_fill_and_protect), using the SAME deterministic
         client_order_ids (order_lifecycle.deterministic_client_order_id)
         normal entry submission uses - safe to retry from any point,
         since resubmitting an already-placed leg under the same id is
         Webull's own idempotency guard (_place_order_with_retry), not a
         duplicate.
      3. Persist whatever lifecycle state and protective-order ids that
         produced.
      4. Only once ALL of the above has succeeded is resolution_completed
         written - new entries are not "permitted" merely because the
         entry's own lifecycle_state cleared MANUAL_LINK_IN_PROGRESS (see
         above); they're permitted once find_incomplete_resolutions no
         longer lists this transaction."""
    if action not in (AMBIGUOUS_RESOLUTION_RELEASE, AMBIGUOUS_RESOLUTION_LINK):
        raise ValidationError(f"Unknown resolution action: {action!r}")
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to resolve an ambiguous submission.")
    if not target_user_id:
        raise ValidationError("A target user_id is required.")

    orders = list_overnight_orders(target_user_id)
    entry = next((order for order in orders if order.get("entry_client_order_id") == entry_client_order_id), None)
    if entry is None:
        raise ValidationError("No matching ambiguous submission found for this user.")
    if entry.get("lifecycle_state") != ol.UNKNOWN_SUBMISSION_STATE:
        raise ValidationError(
            f"This entry is not currently in an unresolved ambiguous state (lifecycle_state={entry.get('lifecycle_state')})."
        )
    ticker = str(entry.get("ticker", ""))
    if not confirmation or confirmation.strip().upper() != ticker.strip().upper():
        raise ValidationError(f"Confirmation text must exactly match the ticker ({ticker!r}) to proceed.")

    creds = get_webull_credentials(target_user_id)
    accounts = get_accounts(target_user_id)
    webull_account = next((account for account in accounts if account.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("This user's Webull account is not connected - cannot gather fresh evidence.")
    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for this user's credentials.")
    account_id = cash_account["account_id"]

    evidence = _gather_ambiguous_submission_evidence(creds, account_id, entry)
    previous_state = entry.get("lifecycle_state")

    if action == AMBIGUOUS_RESOLUTION_RELEASE:
        if evidence["errors"]:
            # Found live 2026-08-28: this message previously named only
            # WHICH check(s) failed, never why - an admin staring at
            # "order_detail failed" has no way to tell "a broker error
            # message that just means 'order not found' (this app's own
            # empty _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES allowlist -
            # see integrations/webull.py - means that case still lands
            # here as 'inconclusive' rather than a clean not-found, until
            # that specific error code is empirically verified and added)"
            # apart from "a genuine timeout worth just retrying" apart
            # from "a real bug" - forcing exactly the kind of blind,
            # multi-step live debugging that took to even learn this much
            # about ONE stuck entry. The underlying error text is a broker
            # response string, not a credential or secret - safe to show
            # an admin who already has the platform-wide visibility this
            # whole panel requires.
            failed_check_details = "; ".join(
                f"{check}: {message}" for check, message in sorted(evidence["errors"].items())
            )
            raise ValidationError(
                f"Cannot release - {len(evidence['errors'])} of the mandatory fresh checks failed and are "
                f"inconclusive, not confirmed-clean ({failed_check_details}). Resolve the underlying failure and try again."
            )
        if evidence["found"]:
            raise ValidationError(
                "Cannot release - fresh checks found matching evidence (an order, position, or history record) "
                "for this entry. Use 'link' instead, or investigate further before taking any action."
            )
    else:
        if not evidence["found_strong"]:
            reason_detail = (
                "a match was found (by ticker or by client order ID) but cannot be reliably attributed to this "
                "specific order - either a weak ticker-only position match, or a client-order-id match missing "
                "the symbol/side/quantity/price fields needed to verify it"
                if evidence["found"]
                else "nothing at the broker to link this entry to"
            )
            raise ValidationError(f"Cannot link - fresh checks found {reason_detail}.")

    resolution_id = uuid.uuid4().hex
    record_ambiguous_resolution_audit(
        target_user_id,
        {
            "phase": RESOLUTION_PHASE_STARTED,
            "resolution_id": resolution_id,
            "administrator": admin_user_id,
            "target_user_id": target_user_id,
            "entry_client_order_id": entry_client_order_id,
            "ticker": entry.get("ticker"),
            "timestamp": _now_utc().isoformat(),
            "requested_action": action,
            "reason": reason.strip(),
            "confirmation": confirmation.strip(),
            "evidence": evidence,
            "previous_state": previous_state,
        },
    )

    common_manual_fields = dict(
        manual_resolution_id=resolution_id,
        manual_resolution_administrator=admin_user_id,
        manual_resolution_reason=reason.strip(),
        manual_resolution_confirmation=confirmation.strip(),
        manual_resolution_evidence=evidence,
        manual_resolution_at=_now_utc().isoformat(),
    )
    protective_order_ids: Optional[Dict[str, object]] = None

    if action == AMBIGUOUS_RESOLUTION_RELEASE:
        try:
            ol.transition(entry, ol.MANUALLY_RESOLVED_NO_ORDER, **common_manual_fields)
            replace_overnight_orders(target_user_id, orders)
        except Exception as error:  # noqa: BLE001 - state_persistence stage, see docstring
            _record_resolution_failed(target_user_id, resolution_id, admin_user_id, entry_client_order_id, error, stage="state_persistence")
            raise
    else:
        try:
            # Step 1: persist the in-progress marker BEFORE any polling or
            # placement work begins - see docstring. error=None clears the
            # stale ambiguity error (e.g. "timeout") now that a strong
            # match has been found and this is actively being linked - it
            # would otherwise keep showing on an entry that's about to be
            # confirmed protected and monitored normally.
            ol.transition(entry, ol.MANUAL_LINK_IN_PROGRESS, error=None, **common_manual_fields)
            replace_overnight_orders(target_user_id, orders)
        except Exception as error:  # noqa: BLE001
            _record_resolution_failed(target_user_id, resolution_id, admin_user_id, entry_client_order_id, error, stage="state_persistence")
            raise

        try:
            # Steps 2-3: poll and protect, using the SAME deterministic
            # client_order_ids normal entry submission uses - safe to
            # retry from any point (see docstring and
            # order_lifecycle.deterministic_client_order_id).
            entry = _poll_fill_and_protect(
                user_id=target_user_id,
                creds=creds,
                account_id=account_id,
                ticker=str(entry.get("ticker", "")),
                entry_client_order_id=entry_client_order_id,
                limit_price=float(entry.get("limit_price") or 0),
                stop_price=float(entry.get("stop") or 0),
                target_price=float(entry.get("target") or 0),
                trading_day=str(entry.get("trading_day") or ""),
                entry=entry,
            )
        except Exception as error:  # noqa: BLE001 - protection stage
            _record_resolution_failed(target_user_id, resolution_id, admin_user_id, entry_client_order_id, error, stage="protection")
            raise

        try:
            replace_overnight_orders(target_user_id, orders)
        except Exception as error:  # noqa: BLE001 - final_persistence stage
            _record_resolution_failed(target_user_id, resolution_id, admin_user_id, entry_client_order_id, error, stage="final_persistence")
            raise

        protective_order_ids = {"stop": entry.get("stop_client_order_id"), "target": entry.get("target_client_order_id")}

    # Step 4 (link) / final step (release): only written once everything
    # above has fully succeeded and been durably persisted. A failure
    # writing THIS specific record is deliberately NOT treated as
    # resolution_failed - see docstring for why.
    try:
        completed_record = record_ambiguous_resolution_audit(
            target_user_id,
            {
                "phase": RESOLUTION_PHASE_COMPLETED,
                "resolution_id": resolution_id,
                "administrator": admin_user_id,
                "target_user_id": target_user_id,
                "entry_client_order_id": entry_client_order_id,
                "timestamp": _now_utc().isoformat(),
                "final_state": entry.get("lifecycle_state"),
                "protective_order_ids": protective_order_ids,
            },
        )
    except Exception:  # noqa: BLE001 - see docstring: the orphaned resolution_started record is the durable marker
        completed_record = None

    try:
        add_manual_alert(
            target_user_id,
            {
                "type": "ambiguous_submission_resolved",
                "ticker": entry.get("ticker"),
                "message": (
                    f"{entry.get('ticker')}: an ambiguous order submission was manually {action}ed by an "
                    f"administrator (now {entry.get('lifecycle_state')}). Reason: {reason.strip()}"
                ),
            },
        )
    except Exception:  # noqa: BLE001 - never let alerting itself break the resolution
        pass

    return {"entry": entry, "evidence": evidence, "audit_record": completed_record, "resolution_id": resolution_id}


def _run_autonomous_trade_scan_locked(user_id: str, dry_run: bool = False) -> Dict[str, object]:
    """Scans current setups the same way the dashboard does, and for the
    highest-confidence bullish ones places real (sandbox) DAY limit orders on
    Webull - the trading session (CORE/ALL/NIGHT) is picked automatically by
    time of day, so outside market hours these queue and fill at the next
    market open rather than executing immediately. Every order, and every
    skip, is logged with the reasoning behind it so it can be reviewed later.
    Pure function of user_id - safe to call from a real request or from the
    cron trigger's simulated per-user request context. Callers must go
    through _run_autonomous_trade_scan above, not this directly, to hold the
    per-user lock for the scan's duration.

    dry_run=True runs the EXACT SAME discovery/threshold/sizing logic - real
    market data, real account balance, real risk math - but never calls
    _submit_and_protect_entry, never places or cancels anything at the
    broker, and never writes record_overnight_order or a research-log entry.
    Built for a user-facing "preview what the agent would do right now"
    action (see api_autonomy_preview_scan) - the whole point is showing the
    agent's OWN live research and candidate selection before anything
    real happens, not a hardcoded/hand-picked trade. Existing-position
    reconciliation (_reconcile_exit_orders and friends) is also skipped
    under dry_run, for the same reason - a preview must have zero side
    effects on anything already resting at the broker, not just on new
    entries."""
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub before running the trade scan.")

    anthropic_api_key = get_anthropic_api_key(user_id)

    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("Connect Webull in Account Hub before running the trade scan.")

    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for these credentials.")
    account_id = cash_account["account_id"]
    # PUT/short candidates need a genuine margin account - INDIVIDUAL_CASH
    # cannot hold a short position (OPENAPI_GENERATE_NEW_SHORT_POSITION,
    # confirmed live 2026-08-31). None here (no margin account provisioned
    # for these credentials) is a legitimate, expected state for most
    # users, not an error - PUT candidates are simply skipped below with a
    # clear reason rather than this whole scan failing over it.
    margin_account = webull_api.find_individual_margin_account(sandbox_accounts)
    margin_account_id = margin_account["account_id"] if margin_account else None

    # Every one of these reconciliation passes can place, cancel, or resize
    # a REAL order at the broker for an EXISTING position - a preview must
    # have zero side effects on anything already resting there, so dry_run
    # skips this entire block. Neither ambiguity flag can be true without
    # having actually submitted something first, so both default to False
    # rather than "unknown" - nothing for either to be ambiguous ABOUT yet.
    if not dry_run:
        _reconcile_exit_orders(user_id, creds, account_id)
        _refresh_stop_confidence(user_id, creds, account_id)
        # Restart recovery for a broker-accepted entry that never got ANY
        # local record at all (see _discover_orphaned_broker_entries) - must
        # run BEFORE _reconcile_unknown_submissions below so a freshly
        # discovered orphan is included in THIS SAME tick's freeze check, not
        # one scan late.
        _discover_orphaned_broker_entries(user_id, creds, account_id)
        # True if ANY entry - from this scan or an earlier one - is still stuck
        # in UNKNOWN_SUBMISSION_STATE after this reconciliation attempt, OR any
        # manual resolution transaction (_resolve_ambiguous_submission) is
        # still incomplete after restart recovery has had a chance to resume
        # it (see _recover_incomplete_manual_resolutions - covers both a
        # link stuck in MANUAL_LINK_IN_PROGRESS and a resolution whose closing
        # audit write never durably landed). Gates every NEW entry below (see
        # the comment above entries_allowed) - this account's true committed
        # capital isn't confidently known while either is true, so nothing new
        # gets sized against it, no matter how many scans ago the ambiguity
        # first occurred.
        has_unresolved_ambiguous_submission = _reconcile_unknown_submissions(user_id, creds, account_id)
        has_incomplete_manual_resolution = _recover_incomplete_manual_resolutions(user_id, creds, account_id)
        # Resumes every ORDINARY entry (never ambiguous, never manually
        # resolved) still transitional - see _monitor_transitional_orders.
        # Also runs on its own, much faster cadence via _run_fast_order_monitor
        # / the fast-monitor-trigger endpoint - this call is the safety net
        # that still applies even if that faster external cron was never
        # configured, so this app's OWN 5-minute scan never regresses to
        # leaving these unresumed.
        _monitor_transitional_orders(user_id, creds, account_id)
        # Same pass, but for the MARGIN account - only if one exists for
        # this user. Without this, a short entry's fill/protection/exit
        # would never be checked by this scan at all (see
        # _monitor_transitional_orders' own account-filtering docstring).
        # Deliberately narrower than the cash path above: only the fill/
        # protection/exit monitor runs against the margin account for now,
        # not orphan discovery, ambiguous-submission recovery, or the
        # outside-hours stop retry - a known, documented gap (matching
        # this app's own established discipline of a narrower, verified
        # feature over an unverified broader one - see the OCO/OTOCO
        # precedent), not an oversight.
        if margin_account_id:
            _monitor_transitional_orders(user_id, creds, margin_account_id)
    else:
        has_unresolved_ambiguous_submission = False
        has_incomplete_manual_resolution = False

    risk_settings = get_autonomy_status(user_id)
    if risk_settings.get("emergency_stop_enabled"):
        raise ValidationError("Emergency stop is enabled - reset it in Account Hub before running the scan.")

    # Risk limits are set as a percent of balance, not a flat dollar amount,
    # so they stay meaningful as the balance changes rather than becoming a
    # stale number - computed here off the same virtual balance shown on the
    # dashboard, not Webull's real (inflated) sandbox seed.
    balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id)
    real_net_liquidation_value = float(balance.get("total_net_liquidation_value", 0) or 0)
    virtual_balance = get_virtual_net_account_value(user_id, real_net_liquidation_value)
    current_balance = virtual_balance if virtual_balance is not None else real_net_liquidation_value
    # Real broker-reported buying power, read from the SAME balance call
    # above rather than a second broker round-trip - it's a hard ceiling
    # layered on top of the virtual allocation (see _compute_position_quantity),
    # not a substitute for it. None here means "couldn't determine it", which
    # fails every candidate closed for this scan rather than sizing against a
    # guess.
    broker_buying_power = _extract_broker_buying_power(balance)

    daily_loss_limit_percent = float(risk_settings.get("daily_loss_limit_percent", 0) or 0)
    day_pnl = float(balance.get("total_day_profit_loss", 0) or 0)
    if _is_daily_loss_limit_hit(day_pnl, current_balance, daily_loss_limit_percent):
        daily_loss_limit = current_balance * (daily_loss_limit_percent / 100)
        raise ValidationError(
            f"Daily loss limit reached (today's P/L ${day_pnl:.2f} vs -${daily_loss_limit:.2f} limit, "
            f"{daily_loss_limit_percent:.1f}% of ${current_balance:,.2f} balance). No new trades until tomorrow."
        )

    max_positions = int(risk_settings.get("max_positions", 0) or 0)
    real_open_positions_snapshot = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    # Portfolio-wide, not per-account - max_positions is the user's own
    # "how many concurrent positions am I comfortable holding" setting,
    # not a separate cap per broker account, so a margin-account short
    # counts against the same limit as a cash-account long. Not wrapped
    # in a try/except - same as the cash lookup immediately above it,
    # a failed positions read here must fail the whole scan closed, not
    # silently undercount the portfolio's true open-position exposure.
    open_position_count = len(real_open_positions_snapshot)
    if margin_account_id:
        open_position_count += len(webull_api.get_account_positions(creds["app_key"], creds["app_secret"], margin_account_id))
    available_position_slots = _available_position_slots(max_positions, open_position_count, OVERNIGHT_MAX_ORDERS_PER_RUN)

    # ONE reconciled broker snapshot for the whole scan, not one per
    # candidate. Re-reading the broker before every candidate does not solve
    # broker-side eventual consistency - an order accepted moments ago isn't
    # guaranteed to already appear in a fresh get_open_orders() read, so
    # candidate 2 could see the exact same (stale) snapshot candidate 1 did
    # and oversubscribe the same dollars. See _build_capital_snapshot for the
    # fail-closed handling of a broker failure or malformed response.
    tracked_tickers_for_user = {
        str(order.get("ticker", "")).upper() for order in list_overnight_orders(user_id) if order.get("ticker")
    }
    snapshot_available_buying_power = _build_capital_snapshot(
        fetch_open_orders=lambda: webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id),
        real_open_positions=real_open_positions_snapshot,
        tracked_tickers=tracked_tickers_for_user,
        total_equity=current_balance,
    )

    # The margin account's OWN real broker buying power - a hard ceiling
    # for a SHORT candidate specifically, same role broker_buying_power
    # plays for a long against the cash account. Deliberately does NOT
    # get its own risk_budget/current_balance - risk_percent_of_balance
    # stays anchored to the SAME (cash) balance for both directions, so
    # the user's per-trade risk setting means the same dollar amount
    # regardless of which account a candidate happens to trade through;
    # only the hard buying-power ceiling differs per account.
    margin_broker_buying_power: Optional[float] = None
    margin_snapshot_available_buying_power: Optional[float] = None
    # The margin account's real option_buying_power - a hard ceiling for an
    # OPTION candidate specifically, same role margin_broker_buying_power
    # plays for a short. Read from the SAME margin_balance call, not a
    # second round-trip. Options draw from this dedicated pool, not
    # margin_broker_buying_power (equity buying power) - see
    # _extract_option_buying_power.
    margin_option_buying_power: Optional[float] = None
    if margin_account_id:
        try:
            margin_balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], margin_account_id)
            margin_broker_buying_power = _extract_broker_buying_power(margin_balance)
            margin_option_buying_power = _extract_option_buying_power(margin_balance)
            margin_real_net_liquidation_value = float(margin_balance.get("total_net_liquidation_value", 0) or 0)
            margin_snapshot_available_buying_power = _build_capital_snapshot(
                fetch_open_orders=lambda: webull_api.get_open_orders(creds["app_key"], creds["app_secret"], margin_account_id),
                real_open_positions=webull_api.get_account_positions(creds["app_key"], creds["app_secret"], margin_account_id),
                tracked_tickers=tracked_tickers_for_user,
                total_equity=margin_real_net_liquidation_value,
            )
        except Exception as error:  # noqa: BLE001 - fails closed (both stay None -> every short candidate sizes to 0 below), never guesses
            logger.warning("Margin account balance/snapshot lookup failed, short candidates this scan will fail closed: %s", error)

    # In-scan reservations layered over the snapshot above - authoritative
    # and immediate the moment an order is accepted, regardless of whether
    # the broker's own read side has caught up yet. Reconciled against
    # reality again on the NEXT scan run's fresh snapshot; a reservation
    # here for an order that turns out to have been rejected or cancelled
    # simply isn't in the next snapshot's committed capital, correcting
    # itself rather than needing to be explicitly rolled back. A Decimal
    # from the very first candidate, not a float that only becomes Decimal
    # once _reservation_notional is added to it - see that function's
    # docstring for why accumulating in float can reintroduce
    # binary-imprecision even when each individual term was Decimal-safe.
    #
    # Keyed by account_id, not a single running total - a cash-account
    # reservation must never draw down the margin account's own buying
    # power, or vice versa; each account's own dollars are entirely
    # separate.
    local_reservations_by_account: Dict[str, "Decimal"] = {account_id: _to_decimal(0.0)}
    if margin_account_id:
        local_reservations_by_account[margin_account_id] = _to_decimal(0.0)

    risk_percent_of_balance = float(risk_settings.get("risk_percent_of_balance", 0) or 0)
    risk_budget = _compute_risk_budget(current_balance, risk_percent_of_balance)
    # No settings-UI control exists for this yet (see _compute_position_exposure_cap) -
    # reads as 0/disabled for every account until one does.
    max_position_exposure_percent = float(risk_settings.get("max_position_exposure_percent", 0) or 0)
    position_exposure_cap = _compute_position_exposure_cap(current_balance, max_position_exposure_percent)

    # include_options=False - the heaviest, most rate-limit-risky part of
    # _build_page_context (a real options-chain fetch per intelligence
    # ticker) feeds only options_expirations/expected_move on each
    # opportunity below, and neither field is ever read past this point -
    # candidate selection, sizing, and _submit_and_protect_entry all key off
    # ideal_entry/stop/target/confidence, which come from strategy/chart,
    # not options. Found while investigating the OOM crashes that
    # repeatedly line up with this exact scan trigger's own GitHub Actions
    # run timestamps (see the Autonomous scan scheduler 502 failures).
    context = _build_page_context(include_reversal=True, include_trend=True, include_options=False)
    opportunities = context.get("upcoming_opportunities", [])

    today_key = _trading_day_key()

    def _order_trading_day(order: Dict[str, object]) -> str:
        try:
            return _trading_day_key(datetime.fromisoformat(str(order.get("logged_at", ""))))
        except ValueError:
            return ""

    already_placed_today = {
        str(order.get("ticker", "")).upper()
        for order in list_overnight_orders(user_id)
        if order.get("status") == "placed" and _order_trading_day(order) == today_key
    }

    qualifying = [
        opp
        for opp in opportunities
        if str(opp.get("recommendation", "")).upper() in ("CALL", "PUT")
        and int(opp.get("confidence", 0) or 0) >= OVERNIGHT_MIN_CONFIDENCE
        and str(opp.get("ticker", "")).upper() not in already_placed_today
    ]
    qualifying.sort(key=lambda opp: int(opp.get("confidence", 0) or 0), reverse=True)

    # Position management for existing positions (_reconcile_exit_orders,
    # _refresh_stop_confidence, _reconcile_unknown_submissions above)
    # already ran regardless of session or unresolved ambiguity; only
    # NEW-entry candidates are gated - by CORE hours, AND by whether ANY
    # entry (this scan's or an earlier one's) is still stuck in
    # UNKNOWN_SUBMISSION_STATE. See _reconcile_unknown_submissions'
    # docstring for why an unresolved ambiguous submission blocks every new
    # entry, not just the ones in the run that created it.
    if _new_entries_disabled_by_deployment_kill_switch():
        new_entries_blocked_reason = (
            "new autonomous entries are disabled platform-wide by a deployment-level kill switch "
            "(PLUTO_DISABLE_NEW_ENTRIES) - existing positions are still monitored and protected normally"
        )
    elif not _new_entries_allowed(_current_webull_trading_session()):
        new_entries_blocked_reason = "outside CORE trading hours - a new entry can't get a real broker-side stop attached until CORE opens"
    elif has_unresolved_ambiguous_submission:
        new_entries_blocked_reason = (
            "a prior order submission returned an ambiguous broker response and remains unresolved - no new "
            "autonomous entries will be placed for this account until it is conclusively resolved or manually "
            "cleared (existing positions are still monitored and protected normally)"
        )
    elif has_incomplete_manual_resolution:
        new_entries_blocked_reason = (
            "a manual resolution of a prior ambiguous order submission has not been durably confirmed complete - "
            "no new autonomous entries will be placed for this account until it is (existing positions are still "
            "monitored and protected normally)"
        )
    elif _has_stuck_transitional_orders_locally(user_id):
        new_entries_blocked_reason = (
            f"an ordinary entry has made no fill/protection progress for at least "
            f"{MONITOR_STUCK_FREEZE_SECONDS // 60} minutes despite repeated monitor attempts - safe recovery "
            "cannot be proven, so no new autonomous entries will be placed for this account until a human "
            "reviews it (existing positions are still monitored and protected normally)"
        )
    else:
        new_entries_blocked_reason = ""
    entries_allowed = not new_entries_blocked_reason
    candidates = qualifying[: min(OVERNIGHT_MAX_ORDERS_PER_RUN, available_position_slots)] if entries_allowed else []

    placed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []

    # Leading market-regime SHADOW signal (VIX) - fetched/computed ONCE per
    # scan tick, before ANY candidate is evaluated, so every research-log
    # record written below (including opportunities that never even
    # became a qualifying candidate) carries the same tick's VIX context.
    # SHADOW MODE ONLY - see regime.py's module docstring. Nothing derived
    # from this is read by entries_allowed, sizing, the LLM step, or order
    # submission anywhere in this function - see _shadow_snapshot_for and
    # its call sites below for the structural guarantee.
    try:
        vix_snapshot = get_vix_snapshot()
        shadow_mapping = compute_shadow_adjustment(vix_snapshot)
    except Exception:  # noqa: BLE001 - shadow/research code must never affect the real scan
        vix_snapshot = {
            "vix_level": None, "source_time": None, "fetch_time": None,
            "age_seconds": None, "status": "unavailable", "used_stale_cache": False,
        }
        shadow_mapping = {"mapping_version": None, "proposed_adjustment": 0, "reasoning": "shadow computation failed"}

    # The EFFECTIVE confidence threshold actually governing whether a
    # candidate for THIS user ever reaches order submission - not just
    # OVERNIGHT_MIN_CONFIDENCE (a global floor also enforced in
    # `qualifying` above), but whichever is stricter between that floor
    # and the user's own Account Hub "AI confidence threshold" setting
    # (settings_store.py's ai_confidence_threshold, default 68 - see
    # _build_page_context, which already filters `opportunities` by this
    # SAME per-user setting before this function ever sees them). Using
    # only the global constant here would make the shadow comparison
    # wrong for any user who raised their own threshold above 55 - this
    # is what production actually uses, computed the same way, not a
    # separate hardcoded shadow number.
    try:
        effective_confidence_threshold = max(
            int(get_settings(user_id).get("ai_confidence_threshold", OVERNIGHT_MIN_CONFIDENCE) or OVERNIGHT_MIN_CONFIDENCE),
            OVERNIGHT_MIN_CONFIDENCE,
        )
    except Exception:  # noqa: BLE001 - shadow/research code must never affect the real scan
        effective_confidence_threshold = OVERNIGHT_MIN_CONFIDENCE

    def _shadow_snapshot_for(ticker: str, strategy: object, raw_confidence: int) -> Dict[str, object]:
        """Builds one candidate's shadow comparison record - SHADOW MODE
        ONLY, never read by the real decision path (see the module-level
        comment above). Wrapped in its own try/except so a bug here can
        never block, alter, or crash the real scan; every call site below
        relies on that rather than repeating the guard itself."""
        try:
            proposed_adjustment = int(shadow_mapping.get("proposed_adjustment", 0) or 0)
            shadow_adjusted_confidence = raw_confidence + proposed_adjustment
            raw_crosses_threshold = raw_confidence >= effective_confidence_threshold
            shadow_crosses_threshold = shadow_adjusted_confidence >= effective_confidence_threshold
            source_time = vix_snapshot.get("source_time")
            fetch_time = vix_snapshot.get("fetch_time")
            return {
                "regime_mode": "shadow",
                "mapping_version": shadow_mapping.get("mapping_version"),
                "strategy": strategy,
                "vix_level": vix_snapshot.get("vix_level"),
                "vix_source_time": source_time.isoformat() if source_time else None,
                "vix_fetch_time": fetch_time.isoformat() if fetch_time else None,
                "vix_age_seconds": vix_snapshot.get("age_seconds"),
                "vix_status": vix_snapshot.get("status"),
                "vix_used_stale_cache": vix_snapshot.get("used_stale_cache"),
                "raw_confidence": raw_confidence,
                "proposed_adjustment": proposed_adjustment,
                "shadow_adjusted_confidence": shadow_adjusted_confidence,
                # The SAME effective threshold production actually uses for
                # THIS user (see above) - not a separate hardcoded shadow
                # threshold - so this is a genuine apples-to-apples
                # comparison against production's own bar.
                "actual_decision_threshold": effective_confidence_threshold,
                "raw_crosses_threshold": raw_crosses_threshold,
                "shadow_crosses_threshold": shadow_crosses_threshold,
                "would_change_decision": shadow_crosses_threshold != raw_crosses_threshold,
                "reasoning": shadow_mapping.get("reasoning"),
                # Joins this record back to autonomy/closed_trades.py's own
                # trade_id once a trade (if any) is later closed.
                "ticker": ticker,
                "trading_day": today_key,
                "entry_client_order_id": None,
            }
        except Exception as shadow_error:  # noqa: BLE001 - shadow/research code must never affect the real scan
            return {"regime_mode": "shadow", "error": str(shadow_error)}

    def _log_research_decision(
        *, ticker, recommendation, strategy, raw_confidence, decision, reason_skipped,
        quantity, entry_client_order_id, regime_shadow=None,
    ) -> None:
        """Durably records ONE evaluated candidate to the append-only
        research log (autonomy/research_log.py) - called for EVERY
        opportunity this scan touches, whether it was ultimately placed,
        skipped below the confidence floor, skipped for max-positions/
        risk/buying-power reasons, or vetoed by the LLM step, so later
        analysis of the VIX shadow signal (or any other future research
        signal) isn't built only from the subset that reached submission -
        see research_log.py's own module docstring on survivorship bias.
        Never lets a logging failure affect the real scan.

        No-ops entirely under dry_run - a preview run isn't a real evaluated
        candidate with real consequences, and mixing preview rows into the
        durable research log would corrupt the survivorship-bias analysis
        that log exists for."""
        if dry_run:
            return
        try:
            record_research_decision(
                user_id,
                {
                    "trading_day": today_key,
                    "account_id": account_id,
                    "ticker": ticker,
                    "recommendation": recommendation,
                    "strategy": strategy,
                    "raw_confidence": raw_confidence,
                    "decision": decision,
                    "reason_skipped": reason_skipped,
                    "quantity": quantity,
                    "entry_client_order_id": entry_client_order_id,
                    "regime_shadow": regime_shadow if regime_shadow is not None else _shadow_snapshot_for(ticker, strategy, raw_confidence),
                },
            )
        except Exception:  # noqa: BLE001 - research logging must never affect the real scan
            pass

    for opp in opportunities:
        if opp in candidates:
            continue
        opp_was_qualifying = opp in qualifying
        # Only the max-positions/no-slots branch needs surfacing per-ticker
        # in the scan-run reason text (see the sizing-rejection skip's own
        # comment, above, in the main candidate loop) - the blocked-entries
        # branch is already covered once, globally, via
        # new_entries_blocked_reason in the top-level summary, so tagging it
        # too would just duplicate the same reason for every candidate.
        surface_in_summary = False
        if not entries_allowed and opp_was_qualifying:
            reason = new_entries_blocked_reason
        elif opp_was_qualifying:
            reason = f"max_positions limit reached ({open_position_count}/{max_positions} open)" if max_positions > 0 else "no position slots available"
            surface_in_summary = True
        elif str(opp.get("recommendation", "")).upper() in ("CALL", "PUT"):
            reason = f"confidence {opp.get('confidence')} below {OVERNIGHT_MIN_CONFIDENCE} threshold"
        else:
            reason = f"recommendation is {opp.get('recommendation')}, only CALL/PUT setups auto-order tonight"
        skip_record = {
            "ticker": opp.get("ticker"),
            "recommendation": opp.get("recommendation"),
            "confidence": opp.get("confidence"),
            "reason_skipped": reason,
        }
        if surface_in_summary:
            skip_record["was_qualifying"] = True
        skipped.append(skip_record)
        _log_research_decision(
            ticker=opp.get("ticker"), recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
            raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=reason,
            quantity=None, entry_client_order_id=None,
        )

    for candidate_index, opp in enumerate(candidates):
        if candidate_index > 0:
            time.sleep(1.0)  # spread order placements out to avoid tripping Webull's rate limiter
        ticker = str(opp.get("ticker", ""))
        limit_price = float(opp.get("ideal_entry") or 0)
        stop_price_for_sizing = float(opp.get("stop") or 0)
        direction = "short" if str(opp.get("recommendation", "")).upper() == "PUT" else "long"

        if direction == "short" and not margin_account_id:
            # A genuine margin account is required to hold a short position
            # (OPENAPI_GENERATE_NEW_SHORT_POSITION on the cash account,
            # confirmed live 2026-08-31) - this user simply doesn't have
            # one provisioned. Fails closed with a clear reason rather than
            # ever falling through to attempt this against the cash
            # account, which the broker would reject anyway.
            reason = "no margin account available for a PUT/short entry"
            skipped.append(
                {"ticker": ticker, "recommendation": opp.get("recommendation"), "confidence": opp.get("confidence"), "reason_skipped": reason, "was_qualifying": True}
            )
            _log_research_decision(
                ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=reason,
                quantity=0, entry_client_order_id=None,
            )
            continue

        # Real options attempt (2026-09-03) - tried FIRST for every
        # qualifying candidate, ahead of the equity long/short path below,
        # matching the user's own stated goal (learn to trade real options,
        # not just directional equity) while keeping the proven equity
        # path as the automatic fallback (see the plan's "additive, not a
        # replacement" decision). Only actually attempts an option trade -
        # and only then `continue`s past the equity path entirely for this
        # candidate - when select_option_contract finds a real, liquid,
        # listed contract AND it sizes to at least one contract; anything
        # short of that (no margin account, no listed options for this
        # ticker, no contract within the liquidity/strike/expiration
        # window, or a premium too large for the risk budget) falls
        # straight through to the unmodified equity code below, exactly as
        # if this block didn't exist.
        if margin_account_id and limit_price > 0:
            try:
                option_contract = select_option_contract(
                    creds["app_key"], creds["app_secret"], ticker,
                    "PUT" if direction == "short" else "CALL", limit_price,
                )
            except Exception as error:  # noqa: BLE001 - a broker/data failure on the OPTIONAL options lookup must never block the proven equity path below; log and fall through
                logger.warning("select_option_contract failed for %s, falling back to the equity path: %s", ticker, error)
                option_contract = None
            if option_contract:
                # Reuses the SAME account-level reservation pool a short
                # candidate on this account would use (not a separate
                # options-only bucket) - deliberately conservative: this
                # app's own virtual/committed-capital tracking
                # (margin_snapshot_available_buying_power) represents the
                # user's OVERALL chosen allocation for this account
                # regardless of product, so it's correct for options and
                # shorts to compete for the same pool there. The REAL
                # broker-side hard ceiling differs by product
                # (margin_option_buying_power vs margin_broker_buying_power)
                # and is checked separately below - option_buying_power is
                # confirmed live as its own distinct balance field (see
                # _extract_option_buying_power), not aliased to equity
                # buying power.
                option_available_buying_power = _compute_available_buying_power_with_reservations(
                    margin_snapshot_available_buying_power, local_reservations_by_account[margin_account_id]
                )
                option_available_broker_buying_power = _compute_available_buying_power_with_reservations(
                    margin_option_buying_power, local_reservations_by_account[margin_account_id]
                )
                option_sizing = _compute_option_contract_quantity(
                    risk_budget=risk_budget,
                    ask_price=option_contract["ask"],
                    available_buying_power=option_available_buying_power,
                    broker_option_buying_power=option_available_broker_buying_power,
                    position_exposure_cap=position_exposure_cap,
                )
                option_quantity = int(option_sizing["quantity"])
                if option_quantity >= 1:
                    option_entry: Dict[str, object] = {
                        "ticker": ticker,
                        "confidence": opp.get("confidence"),
                        "strategy": opp.get("strategy"),
                        "trade_quality": opp.get("trade_quality"),
                        "trade_thesis": opp.get("trade_thesis"),
                        "why_ai_likes_it": opp.get("why_ai_likes_it"),
                        "invalidation_rule": opp.get("invalidation_rule"),
                        "risk_warning": opp.get("risk_warning"),
                        "account_id": margin_account_id,
                        "status": "pending",
                        "sizing_constraints": option_sizing["constraints"],
                        "binding_constraints": option_sizing["binding_constraints"],
                        "trading_day": today_key,
                    }
                    option_limit_price = round(option_contract["ask"], 2)
                    option_cost_reservation = _to_decimal(option_sizing["cost_per_contract"]) * _to_decimal(option_quantity)
                    if dry_run:
                        option_entry["status"] = "preview"
                        option_entry["instrument_type"] = "OPTION"
                        option_entry["option_symbol"] = option_contract["option_symbol"]
                        option_entry["strike"] = option_contract["strike"]
                        option_entry["expiration_date"] = option_contract["expiration_date"]
                        option_entry["option_type"] = option_contract["option_type"]
                        option_entry["limit_price"] = option_limit_price
                        option_entry["quantity"] = option_quantity
                        placed.append(option_entry)
                        local_reservations_by_account[margin_account_id] += option_cost_reservation
                        continue
                    try:
                        _submit_and_confirm_option_entry(
                            user_id=user_id, creds=creds, account_id=margin_account_id, ticker=ticker,
                            option_contract=option_contract, quantity=option_quantity,
                            limit_price=option_limit_price, trading_day=today_key, entry=option_entry,
                        )
                        option_lifecycle_state = option_entry.get("lifecycle_state")
                        if option_lifecycle_state == ol.UNKNOWN_SUBMISSION_STATE:
                            option_entry["status"] = "unknown_submission_state"
                            option_entry["error"] = option_entry.get(
                                "error", "order submission result could not be confirmed (ambiguous broker response)"
                            )
                            local_reservations_by_account[margin_account_id] += option_cost_reservation
                            skipped.append(option_entry)
                        else:
                            option_entry["status"] = "failed" if option_lifecycle_state == ol.ENTRY_FAILED else "placed"
                            if option_entry["status"] == "failed":
                                option_entry["error"] = option_entry.get("error", "entry order failed")
                                skipped.append(option_entry)
                            else:
                                placed.append(option_entry)
                                local_reservations_by_account[margin_account_id] += option_cost_reservation
                    except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole batch, same discipline as the equity path below
                        option_entry["status"] = "failed"
                        option_entry["error"] = str(error)
                        skipped.append(option_entry)
                    record_overnight_order(user_id, option_entry)
                    _log_research_decision(
                        ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                        raw_confidence=int(opp.get("confidence", 0) or 0),
                        decision="placed" if option_entry.get("status") == "placed" else "skipped",
                        reason_skipped=option_entry.get("error") if option_entry.get("status") != "placed" else None,
                        quantity=option_quantity, entry_client_order_id=option_entry.get("entry_client_order_id"),
                    )
                    if option_entry.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
                        break  # same circuit breaker as the equity path - this account's committed capital is no longer confidently known this run
                    continue

        candidate_account_id = margin_account_id if direction == "short" else account_id
        candidate_snapshot_buying_power = margin_snapshot_available_buying_power if direction == "short" else snapshot_available_buying_power
        candidate_broker_buying_power = margin_broker_buying_power if direction == "short" else broker_buying_power

        # The snapshot taken once above, minus every reservation added for a
        # candidate earlier in THIS run AGAINST THIS SAME ACCOUNT - not a
        # fresh broker read per candidate, and never mixed with the OTHER
        # account's own reservations (a cash-account long and a margin-
        # account short draw from entirely separate dollars). See the
        # comment above the snapshot for why re-reading the broker here
        # would not actually be safe. The same in-scan reservations are
        # subtracted from the REAL broker buying power too (not just the
        # virtual allocation) - dollars an earlier candidate in this same
        # run already committed will draw down the real account once the
        # broker's own bookkeeping catches up, even though it hasn't yet,
        # so a later candidate must not be sized as if that money were
        # still free.
        available_buying_power = _compute_available_buying_power_with_reservations(
            candidate_snapshot_buying_power, local_reservations_by_account[candidate_account_id]
        )
        available_broker_buying_power = _compute_available_buying_power_with_reservations(
            candidate_broker_buying_power, local_reservations_by_account[candidate_account_id]
        )

        # Sized by risk-at-stop (how much you'd lose if the stop is hit), not
        # by raw share price - see _compute_position_quantity for the bug
        # this replaced. A stock priced above the risk budget is no longer
        # skipped outright; it's sized down to however many shares that
        # budget actually covers at this stop distance, then skipped only if
        # that comes out to zero whole shares.
        sizing = _compute_position_quantity(
            risk_budget=risk_budget,
            entry_price=limit_price,
            stop_price=stop_price_for_sizing,
            available_buying_power=available_buying_power,
            broker_buying_power=available_broker_buying_power,
            position_exposure_cap=position_exposure_cap,
            direction=direction,
        )
        quantity = int(sizing["quantity"])
        if quantity < 1:
            skipped.append(
                {
                    "ticker": ticker,
                    "recommendation": opp.get("recommendation"),
                    "confidence": opp.get("confidence"),
                    "reason_skipped": sizing["reason"],
                    "sizing_constraints": sizing["constraints"],
                    "binding_constraints": sizing["binding_constraints"],
                    # Marks this as a candidate that passed confidence AND
                    # made it into this run's capped candidate slice, not
                    # merely "didn't qualify" - see
                    # _summarize_scan_result_for_run_log, which surfaces
                    # exactly these in the persisted scan-run reason text.
                    # Before this, a qualifying candidate silently sized to
                    # zero shares showed up as "N qualifying, 0 placed" with
                    # no trace of why anywhere a human could see it.
                    "was_qualifying": True,
                }
            )
            _log_research_decision(
                ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=sizing["reason"],
                quantity=0, entry_client_order_id=None,
            )
            continue
        # direction="short" has its stop ABOVE limit_price - mirrored so
        # this stays a positive dollars-at-risk figure, matching
        # _reconcile_entry_fill_and_protection's own realized_risk_dollars.
        planned_risk_dollars = round(quantity * ((stop_price_for_sizing - limit_price) if direction == "short" else (limit_price - stop_price_for_sizing)), 2)
        entry = {
            "ticker": ticker,
            "direction": direction,
            "side": "SELL" if direction == "short" else "BUY",
            "quantity": quantity,
            "limit_price": limit_price,
            "confidence": opp.get("confidence"),
            # Was missing entirely until found while building the Tier 1
            # performance report - every closed_trade record's "strategy"
            # field (autonomy/closed_trades.py) reads entry.get("strategy"),
            # which was always None for every trade ever closed since this
            # key was never actually set here. Without it, breaking down
            # realized performance by strategy is structurally impossible.
            "strategy": opp.get("strategy"),
            "trade_quality": opp.get("trade_quality"),
            "trade_thesis": opp.get("trade_thesis"),
            "why_ai_likes_it": opp.get("why_ai_likes_it"),
            "invalidation_rule": opp.get("invalidation_rule"),
            "risk_warning": opp.get("risk_warning"),
            "target": opp.get("target"),
            "stop": opp.get("stop"),
            "account_id": candidate_account_id,
            "status": "pending",
            # Auditable even on success, not just on skip - see
            # _compute_position_quantity's structured return.
            "sizing_constraints": sizing["constraints"],
            "binding_constraints": sizing["binding_constraints"],
            "planned_risk_dollars": planned_risk_dollars,
            # Stored explicitly (not re-derived from logged_at later) so
            # _reconcile_unknown_submission can regenerate the exact same
            # stop/target client_order_ids this entry would have used - see
            # order_lifecycle.deterministic_client_order_id.
            "trading_day": today_key,
        }

        # Leading market-regime SHADOW observation (VIX, see regime.py and
        # _shadow_snapshot_for above) - RECORD ONLY. Never sets
        # entry["status"], never skips/resizes/vetoes, and is not read by
        # the LLM step, sizing, or submission - see REGIME_MAPPING_VERSION
        # in regime.py for why this stays observation-only until
        # backtested. _shadow_snapshot_for already isolates failures, so
        # nothing here can affect the real scan.
        entry["regime_shadow"] = _shadow_snapshot_for(ticker, opp.get("strategy"), int(opp.get("confidence", 0) or 0))

        # Optional second-opinion pass - only runs if the user configured
        # their own Anthropic key. Reviews the setup after it already passed
        # the technical confidence threshold, and can veto or nudge the
        # confidence score, but a missing key or a flaky API call degrades
        # to skipping this step entirely rather than blocking the trade.
        llm_verdict = get_llm_verdict(opp, anthropic_api_key)
        entry["llm_reasoning_available"] = llm_verdict.get("available", False)
        if llm_verdict.get("available"):
            adjustment = int(llm_verdict.get("confidence_adjustment", 0))
            adjusted_confidence = int(opp.get("confidence", 0)) + adjustment
            entry["llm_verdict"] = llm_verdict.get("verdict")
            entry["llm_confidence_adjustment"] = adjustment
            entry["llm_adjusted_confidence"] = adjusted_confidence
            entry["llm_reasoning"] = llm_verdict.get("reasoning")

            veto_reason = ""
            if llm_verdict.get("verdict") == "veto":
                veto_reason = f"LLM reasoning vetoed: {llm_verdict.get('reasoning')}"
            elif adjusted_confidence < OVERNIGHT_MIN_CONFIDENCE:
                veto_reason = (
                    f"LLM-adjusted confidence {adjusted_confidence} ({opp.get('confidence')}{adjustment:+d}) "
                    f"fell below {OVERNIGHT_MIN_CONFIDENCE} threshold: {llm_verdict.get('reasoning')}"
                )
            if veto_reason:
                entry["status"] = "skipped"
                entry["reason_skipped"] = veto_reason
                # See the sizing-rejection skip's own comment above - same
                # "surface it in the scan-run reason text" reasoning.
                entry["was_qualifying"] = True
                skipped.append(entry)
                if not dry_run:
                    record_overnight_order(user_id, entry)
                _log_research_decision(
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=veto_reason,
                    quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
                    regime_shadow=entry.get("regime_shadow"),
                )
                continue

        try:
            if limit_price <= 0:
                raise ValueError("No valid entry price computed for this ticker.")
            stop_price = float(entry.get("stop") or 0)
            target_price = float(entry.get("target") or 0)
            if dry_run:
                # The entire point: show exactly what the agent's OWN
                # research found and would have done, WITHOUT ever calling
                # _submit_and_protect_entry - no broker call, no order, no
                # persisted record. Reserved against local_reservations
                # anyway so a second preview candidate in the same run sizes
                # itself against a realistic remaining budget, matching what
                # a real run would actually do.
                entry["status"] = "preview"
                entry["stop_price"] = stop_price
                entry["target_price"] = target_price
                placed.append(entry)
                local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
                continue

            # A fresh, genuinely real-time price check immediately before
            # submission - not the same up-to-15-minutes-stale data
            # limit_price was computed from (see
            # integrations/alpaca_data.py's own module docstring). Found
            # live 2026-08-28: this gap was enough real price drift on a
            # fast-moving momentum candidate to trip Webull's own
            # OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE rejection, which
            # then had to be frozen and manually reconciled via the
            # ambiguous-submission workflow after the fact - catching it
            # HERE avoids that entirely, for the (structurally identical)
            # cost of one extra read-only market-data call per candidate,
            # not a broker/order call. get_latest_trade_price returning
            # None (request failed, or credentials unavailable) is treated
            # the SAME as a confirmed large drift - "couldn't confirm
            # freshness" fails closed exactly like "confirmed stale" does,
            # never "assume it's still fine and submit anyway."
            fresh_price = alpaca_data.get_latest_trade_price(ticker)
            drift_reason = ""
            if fresh_price is None:
                drift_reason = (
                    f"could not confirm a fresh, real-time price for {ticker} immediately before submission - "
                    "refusing to submit against a possibly-stale scan-time price"
                )
            elif _price_has_drifted_too_far(limit_price, fresh_price):
                drift_pct = abs(fresh_price - limit_price) / limit_price * 100
                drift_reason = (
                    f"price drifted {drift_pct:.1f}% since the scan computed this entry "
                    f"(scan-time limit ${limit_price:.2f}, real-time price ${fresh_price:.2f}) - "
                    "submitting now risks a broker rejection (Webull's own \"price too aggressive/deviated\" "
                    "risk rule) or an unintentionally bad fill; skipping rather than risking either"
                )
            if drift_reason:
                entry["status"] = "skipped"
                entry["reason_skipped"] = drift_reason
                entry["was_qualifying"] = True
                skipped.append(entry)
                record_overnight_order(user_id, entry)
                _log_research_decision(
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=drift_reason,
                    quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
                    regime_shadow=entry.get("regime_shadow"),
                )
                continue

            _submit_and_protect_entry(
                user_id=user_id,
                creds=creds,
                account_id=candidate_account_id,
                ticker=ticker,
                requested_quantity=quantity,
                limit_price=limit_price,
                stop_price=stop_price,
                target_price=target_price,
                trading_day=today_key,
                entry=entry,
            )
            # "placed" here means an entry order was successfully submitted,
            # not necessarily that protection is confirmed active yet - check
            # entry["lifecycle_state"] for that (see order_lifecycle.py).
            # entry_failed is the only DEFINITE lifecycle outcome that means
            # the submission itself never went through - UNKNOWN_SUBMISSION_STATE
            # is a third, deliberately distinct outcome: the broker's true
            # response is unknown (e.g. a timeout), so it must be treated as
            # neither "placed" nor "failed" outright.
            lifecycle_state = entry.get("lifecycle_state")
            if lifecycle_state == ol.UNKNOWN_SUBMISSION_STATE:
                entry["status"] = "unknown_submission_state"
                entry["error"] = entry.get(
                    "error", "order submission result could not be confirmed (ambiguous broker response)"
                )
                # Conservative: reserve the full attempted notional exactly
                # like a confirmed placement - the broker may well have
                # accepted this order even though the response was lost, and
                # a later candidate in this same run must not be sized as if
                # those dollars were still free. See _reconcile_unknown_submission
                # for how this gets resolved on a later scan.
                local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
                skipped.append(entry)
                try:
                    add_manual_alert(
                        user_id,
                        {
                            "type": "unknown_submission_state",
                            "ticker": ticker,
                            "message": (
                                f"{ticker}: entry order submission returned an ambiguous result - the broker's "
                                f"true response is unknown (e.g. a timeout), so it may or may not have been "
                                f"accepted. ${_reservation_notional(quantity, limit_price):,.2f} has been "
                                f"conservatively reserved and no further autonomous entries will be placed for "
                                f"this account this run. This will be reconciled automatically on the next scan - "
                                f"review the position manually if it persists."
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001 - never let alerting itself break the scan
                    pass
            else:
                entry["status"] = "failed" if lifecycle_state == ol.ENTRY_FAILED else "placed"
                if entry["status"] == "failed":
                    entry["error"] = entry.get("error", "entry order failed")
                    skipped.append(entry)
                else:
                    placed.append(entry)
                    # Reserved the instant the order is accepted, not after
                    # this function returns - this is what candidate 2's
                    # sizing above sees even if the broker's own open-orders
                    # read hasn't caught up to candidate 1 yet (see
                    # test_reservation_survives_broker_eventual_consistency).
                    local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
        except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole batch
            entry["status"] = "failed"
            entry["error"] = str(error)
            skipped.append(entry)
        if isinstance(entry.get("regime_shadow"), dict):
            # Backfilled here, not at construction time - entry_client_order_id
            # is only set once ol.initialize runs inside
            # _submit_and_protect_entry above, but this is the SAME entry
            # dict, so the shadow record and the real submission outcome
            # stay joinable in the one persisted overnight_order record.
            entry["regime_shadow"]["entry_client_order_id"] = entry.get("entry_client_order_id")
        record_overnight_order(user_id, entry)
        _log_research_decision(
            ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
            raw_confidence=int(opp.get("confidence", 0) or 0),
            decision="placed" if entry.get("status") == "placed" else "skipped",
            reason_skipped=entry.get("error") if entry.get("status") != "placed" else None,
            quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
            regime_shadow=entry.get("regime_shadow"),
        )
        if entry.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
            # Circuit breaker: an ambiguous submission means this account's
            # true committed capital is no longer confidently known for the
            # rest of this run (the reservation above is a conservative
            # estimate, not a confirmation) - stop placing further entries
            # for this user this tick rather than keep sizing against an
            # uncertain base. Existing position management
            # (_reconcile_exit_orders/_refresh_stop_confidence) already ran
            # unconditionally before this loop, so already-open positions
            # remain protected either way.
            break

    return {
        "ok": True,
        "placed_count": len(placed),
        "skipped_count": len(skipped),
        "placed": placed,
        "skipped": skipped,
        # For autonomy/scan_run_log.py's durable per-tick record - how
        # many opportunities this tick looked at in total, how many
        # cleared the confidence/recommendation/dedup filter, and whether
        # new entries were allowed at all this tick (independent of
        # whether any candidate actually existed to place).
        "candidates_found": len(opportunities),
        "candidates_qualifying": len(qualifying),
        "entries_allowed": entries_allowed,
        "new_entries_blocked_reason": new_entries_blocked_reason,
        "guardrail": "DAY limit orders in the Webull sandbox only. Session auto-selected by time of day.",
    }


@app.route("/api/autonomy/run-overnight-scan", methods=["POST"])
@api_guard
def api_autonomy_run_overnight_scan():
    summary = _run_autonomous_trade_scan(_current_user_id())
    return _api_success(summary, **summary)


@app.route("/api/autonomy/preview-scan", methods=["POST"])
@api_guard
def api_autonomy_preview_scan():
    """Runs the REAL scan - real market data, real account balance, real
    risk-based sizing - but with dry_run=True, so nothing is ever submitted
    to the broker and nothing is persisted (see _run_autonomous_trade_scan_locked's
    dry_run docstring for the exact guarantee). Built for exactly one
    purpose: let a user see precisely what the agent's own research and
    candidate selection would do RIGHT NOW, before ever authorizing a real
    submission - the agent does the research; the human still decides
    whether to act on it. Session-authenticated like every other manual
    autonomy action, not the cron secret."""
    summary = _run_autonomous_trade_scan(_current_user_id(), dry_run=True)
    return _api_success(summary, **summary)


@app.route("/api/autonomy/activate", methods=["POST"])
@api_guard
def api_autonomy_activate():
    user_id = _current_user_id()
    autonomy_result = set_mode(user_id, mode="AUTONOMOUS", reason="Activated from the Mission Control planet button.")
    try:
        scan_result = _run_autonomous_trade_scan(user_id)
    except ValidationError as error:
        scan_result = {"ok": False, "error": str(error), "placed_count": 0, "skipped_count": 0}
    payload = {"ok": True, "autonomy": autonomy_result, "scan": scan_result}
    return _api_success(payload, **payload)


@app.route("/api/autonomy/deactivate", methods=["POST"])
@api_guard
def api_autonomy_deactivate():
    user_id = _current_user_id()
    autonomy_result = set_mode(user_id, mode="OFF", reason="Deactivated from the Mission Control planet button.")
    payload = {"ok": True, "autonomy": autonomy_result}
    return _api_success(payload, **payload)


@app.route("/api/autonomy/scan-runs", methods=["GET"])
@api_guard
def api_autonomy_scan_runs():
    """Authenticated, per-session (NOT the cron secret) - the durable,
    newest-first record of every cron-trigger tick this account was
    included in, per autonomy/scan_run_log.py. Exists so a user can
    answer "was my account actually scanned" directly, instead of relying
    on the external scheduler's own HTTP 200 (which proves the cron
    fired, not that any given account was processed - see that module's
    docstring). Also rendered server-side on the Account Hub page; this
    endpoint is the same data for programmatic/refresh use."""
    user_id = _current_user_id()
    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 200))
    runs = list_scan_runs(user_id, limit=limit)
    return _api_success({"scan_runs": runs}, ok=True, scan_runs=runs)


def _webull_secret_values_for_redaction(user_id: str) -> List[str]:
    """Best-effort fetch of THIS user's own Webull app_key/app_secret, so
    autonomy/scan_run_log.py's redaction can scrub them out of an error
    string too (the module's own built-in list only covers this
    PROCESS's global env-var secrets - a per-user credential is a
    different value it has no way to know about on its own). Never raises -
    a failure to fetch credentials for redaction purposes must not itself
    block recording the (still-useful) error."""
    try:
        creds = get_webull_credentials(user_id)
        return [creds.get("app_key", ""), creds.get("app_secret", "")]
    except Exception:  # noqa: BLE001 - redaction-support code must never block recording the error
        return []


_CRON_TRIGGER_SCHEDULE_INTERVAL_MINUTES = 5


def _nearest_scheduled_slot(now: datetime, interval_minutes: int = _CRON_TRIGGER_SCHEDULE_INTERVAL_MINUTES) -> datetime:
    """Rounds `now` DOWN to the nearest interval-minute UTC boundary - an
    HONEST APPROXIMATION of "when this tick was supposed to fire" per the
    configured cron cadence, not a value received from the caller (the
    endpoint has no way to know the caller's true intended schedule; a
    GitHub Actions workflow doesn't pass one). Labeled clearly as
    "scheduled_start_time" alongside the REAL "actual_start_time" in every
    scan-run record specifically so a large, growing gap between the two
    across records is visible - which is exactly how GitHub Actions'
    documented scheduling imprecision (observed this session: a `*/5`
    cron firing roughly hourly instead) shows up in the data."""
    floored_minute = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=floored_minute, second=0, microsecond=0)


def _monitor_heartbeat_snapshot_for_scan_run() -> Dict[str, object]:
    """A compact snapshot of all three schedulers' health, taken once per
    cron tick (not once per user - this is GLOBAL state, identical for
    every user's record from the same tick) and attached to every
    scan-run record so the dashboard can show "was the monitor considered
    healthy when this ran" without a separate lookup. Never lets a
    health-check failure block writing the scan-run record itself."""
    try:
        fast = _fast_monitor_health_status()
        full = _full_scan_health_status()
        continuous = _continuous_monitor_health_status()
        return {
            "fast_monitor_healthy": fast.get("healthy"),
            "fast_monitor_age_seconds": fast.get("age_seconds"),
            "full_scan_healthy": full.get("healthy"),
            "full_scan_age_seconds": full.get("age_seconds"),
            "continuous_monitor_healthy": continuous.get("healthy"),
            "continuous_monitor_age_seconds": continuous.get("age_seconds"),
        }
    except Exception as error:  # noqa: BLE001 - observability code must never block the real cron tick
        return {"error": f"heartbeat snapshot failed: {error}"}


def _summarize_scan_result_for_run_log(scan_result: Dict[str, object]) -> Dict[str, object]:
    """Extracts the fields autonomy/scan_run_log.py's schema wants out of
    _run_autonomous_trade_scan's return value - see that function's own
    return statement for where placed/skipped entries get their `status`
    field, which is what distinguishes "reached order submission" (placed/
    failed/unknown_submission_state) from every OTHER skip reason
    (confidence floor, sizing, LLM veto - none of those ever call
    _submit_and_protect_entry at all, so they must not count as
    "attempted")."""
    placed = scan_result.get("placed") or []
    skipped = scan_result.get("skipped") or []
    submission_attempted = [e for e in (placed + skipped) if isinstance(e, dict) and e.get("status") in ("placed", "failed", "unknown_submission_state")]
    outcomes = {"placed": 0, "failed": 0, "unknown_submission_state": 0}
    for entry in submission_attempted:
        outcomes[entry["status"]] = outcomes.get(entry["status"], 0) + 1

    candidates_found = scan_result.get("candidates_found")
    candidates_qualifying = scan_result.get("candidates_qualifying")
    reason_parts = [f"{candidates_found if candidates_found is not None else '?'} opportunities scanned, "
                     f"{candidates_qualifying if candidates_qualifying is not None else '?'} qualifying, "
                     f"{outcomes['placed']} placed, {outcomes['failed']} failed, {outcomes['unknown_submission_state']} ambiguous"]
    if not scan_result.get("entries_allowed", True) and scan_result.get("new_entries_blocked_reason"):
        reason_parts.append(f"new entries blocked this tick: {scan_result['new_entries_blocked_reason']}")

    # A candidate that passed confidence and was never blocked by the
    # global entries_allowed gate could still be dropped before ever
    # reaching submission - sized down to 0 shares, LLM-vetoed, or crowded
    # out by max_positions/no open slots. None of that shows up in the
    # placed/failed/ambiguous counts above (none of them ever call
    # _submit_and_protect_entry), so a run could previously read "2
    # qualifying, 0 placed, 0 failed, 0 ambiguous" with zero trace anywhere
    # a human could see of what actually happened to those 2 candidates.
    # was_qualifying=True (set at each of the three skip sites above) is
    # what marks these specifically, as opposed to a candidate that simply
    # never qualified in the first place (below-threshold confidence, or a
    # non-CALL recommendation) - reporting every one of those individually
    # here would bury the signal in noise on any normal scan.
    silently_skipped = [e for e in skipped if isinstance(e, dict) and e.get("was_qualifying") and e.get("reason_skipped")]
    if silently_skipped:
        details = "; ".join(f"{e.get('ticker', '?')} ({e['reason_skipped']})" for e in silently_skipped)
        reason_parts.append(f"not submitted - {details}")

    return {
        "candidates_found": candidates_found,
        "candidates_qualifying": candidates_qualifying,
        "orders_attempted": len(submission_attempted),
        "orders_outcomes": outcomes,
        "reason": " | ".join(reason_parts),
    }


@app.route("/api/autonomy/cron-trigger", methods=["POST"])
def api_autonomy_cron_trigger():
    """Called on a timer by a Render Cron Job (or, currently, a GitHub
    Actions schedule - see .github/workflows/autonomous-scan-scheduler.yml),
    not by a logged-in browser - authenticated by a shared secret instead
    of a session cookie.

    Processes EVERY registered user on EVERY tick, regardless of mode -
    this is the fix for a real gap: previously, any user NOT in
    AUTONOMOUS mode was skipped entirely, which meant their EXISTING
    positions/pending orders were never reconciled by this scheduler
    either (only new-entry scanning is supposed to be mode-gated -
    position management must stay independent of mode, so switching
    autonomy OFF stops new entries but must not stop protecting whatever
    is already open). Now:
      - AUTONOMOUS-mode users get the full scan (position management +
        new-candidate evaluation + entries), exactly as before;
      - every OTHER Webull-configured user gets ONLY the lightweight
        reconciliation pass (_run_fast_order_monitor - no opportunity
        scan, no LLM calls, no new entries), so pending orders and open
        positions stay managed either way;
      - a user with no Webull configured has nothing to reconcile and is
        recorded as such.

    Every user gets a DURABLE, per-tick record via
    autonomy/scan_run_log.py (record_scan_run) - "the cron job returned
    HTTP 200" does not prove any given account was actually scanned, since
    this endpoint can (and does) skip or fail individual users while
    still returning 200 overall. See that module's docstring and
    /api/autonomy/scan-runs (the authenticated endpoint that surfaces
    these in the dashboard) for why this exists.

    Also does one LOCAL-ONLY (no broker calls) check per tick - not per
    user - of the fast monitor's own heartbeat, alerting every admin
    account if it's gone stale/was never configured (see
    _alert_admins_fast_monitor_unhealthy_if_needed). This full 5-minute
    scan is the one cron job that's already required regardless of
    whether the faster optional monitor's own separate cron job was ever
    set up, which is what makes it the right place to detect and surface
    that the faster one is missing or stalled. Records its OWN heartbeat
    (full_scan_heartbeat.py) around the whole per-user loop, which the
    FASTER monitor's own endpoint cross-checks in the other direction -
    see _alert_admins_full_scan_unhealthy_if_needed."""
    expected_secret = os.environ.get("CRON_SECRET", "").strip()
    provided_secret = request.headers.get("X-Cron-Secret", "").strip()
    if not expected_secret or not hmac.compare_digest(expected_secret, provided_secret):
        return _api_failure("Invalid or missing cron secret.", status_code=401, error_code="unauthorized", ok=False)

    _alert_admins_fast_monitor_unhealthy_if_needed()
    _alert_admins_continuous_monitor_unhealthy_if_needed()
    run_id = record_full_scan_run_started()
    tick_started_at = _now_utc()
    scheduled_start_time = _nearest_scheduled_slot(tick_started_at)
    heartbeat_snapshot = _monitor_heartbeat_snapshot_for_scan_run()

    results = []
    for user_id in list_all_user_ids():
        status = get_autonomy_status(user_id)
        current_mode = str(status.get("current_mode", status.get("mode", "OFF"))).upper()
        user_actual_start = _now_utc()
        base_record = {
            "trigger_source": "cron-trigger",
            "run_id": run_id,
            "scheduled_start_time": scheduled_start_time.isoformat(),
            "actual_start_time": user_actual_start.isoformat(),
            "account_mode": current_mode,
            "monitor_heartbeat": heartbeat_snapshot,
        }

        if current_mode != "AUTONOMOUS":
            # Existing-position monitoring stays independent of mode - see
            # the docstring above. Only a lightweight reconciliation pass
            # here, never the opportunity-scanning/new-entry work, which
            # is AUTONOMOUS-only.
            if not is_webull_configured(user_id):
                record_scan_run(user_id, {
                    **base_record, "status": "skipped",
                    "reason": f"autonomy mode is {current_mode}, not AUTONOMOUS, and Webull is not configured for this account - nothing to reconcile",
                    "candidates_found": None, "candidates_qualifying": None,
                    "orders_attempted": None, "orders_outcomes": None, "error": None,
                    "completion_time": _now_utc().isoformat(),
                })
                results.append({"user_id": user_id, "ok": True, "skipped": "not_autonomous_mode_no_webull"})
                continue
            with app.test_request_context():
                session["user_id"] = user_id
                try:
                    monitor_result = _run_fast_order_monitor(user_id)
                    record_scan_run(user_id, {
                        **base_record, "status": "skipped",
                        "reason": (
                            f"autonomy mode is {current_mode}, not AUTONOMOUS - no new-entry scan this tick; "
                            f"existing positions/orders were still reconciled "
                            f"({monitor_result.get('entries_checked', 0)} transitional entries checked, "
                            f"{monitor_result.get('still_transitional_count', 0)} still open afterward)"
                        ),
                        "candidates_found": None, "candidates_qualifying": None,
                        "orders_attempted": None, "orders_outcomes": None, "error": None,
                        "completion_time": _now_utc().isoformat(),
                    })
                    results.append({"user_id": user_id, "ok": True, "reconciled_only": True, **monitor_result})
                except ScanAlreadyRunningError:
                    record_scan_run(user_id, {
                        **base_record, "status": "skipped",
                        "reason": "a concurrent scan/monitor tick was already running for this account - skipped; existing positions remain protected by that concurrent pass",
                        "candidates_found": None, "candidates_qualifying": None,
                        "orders_attempted": None, "orders_outcomes": None, "error": None,
                        "completion_time": _now_utc().isoformat(),
                    })
                    results.append({"user_id": user_id, "ok": True, "skipped": "scan_already_running"})
                except Exception as error:  # noqa: BLE001 - one user's failure shouldn't block others
                    record_scan_run(user_id, {
                        **base_record, "status": "failed",
                        "reason": f"reconciliation of existing positions failed while in {current_mode} mode",
                        "candidates_found": None, "candidates_qualifying": None,
                        "orders_attempted": None, "orders_outcomes": None, "error": str(error),
                        "completion_time": _now_utc().isoformat(),
                    }, extra_redact_secrets=_webull_secret_values_for_redaction(user_id))
                    results.append({"user_id": user_id, "ok": False, "error": str(error)})
            continue

        with app.test_request_context():
            session["user_id"] = user_id
            try:
                scan_result = _run_autonomous_trade_scan(user_id)
                results.append({"user_id": user_id, "ok": True, **scan_result})
                summary = _summarize_scan_result_for_run_log(scan_result)
                record_scan_run(user_id, {
                    **base_record, "status": "processed", "error": None,
                    "completion_time": _now_utc().isoformat(),
                    **summary,
                })
            except ScanAlreadyRunningError:
                # Benign, expected overlap (a retry, a double-fire, a manual
                # click mid-tick) - the lock did its job by refusing this
                # call outright, so this is not a real failure worth
                # surfacing the same way as an actual error.
                results.append({"user_id": user_id, "ok": True, "skipped": "scan_already_running"})
                record_scan_run(user_id, {
                    **base_record, "status": "skipped",
                    "reason": "a concurrent scan was already running for this account - skipped",
                    "candidates_found": None, "candidates_qualifying": None,
                    "orders_attempted": None, "orders_outcomes": None, "error": None,
                    "completion_time": _now_utc().isoformat(),
                })
            except Exception as error:  # noqa: BLE001 - one user's failure shouldn't block others
                results.append({"user_id": user_id, "ok": False, "error": str(error)})
                record_scan_run(user_id, {
                    **base_record, "status": "failed",
                    "reason": "the autonomous scan raised an unhandled error",
                    "candidates_found": None, "candidates_qualifying": None,
                    "orders_attempted": None, "orders_outcomes": None, "error": str(error),
                    "completion_time": _now_utc().isoformat(),
                }, extra_redact_secrets=_webull_secret_values_for_redaction(user_id))

    record_full_scan_run_completed(
        run_id,
        ran_for_users=len(results),
        failures_by_account={r["user_id"]: r["error"] for r in results if not r.get("ok")},
    )

    return _api_success({"ran_for_users": len(results), "results": results}, ok=True, ran_for_users=len(results))


@app.route("/api/autonomy/fast-monitor-trigger", methods=["POST"])
def api_autonomy_fast_monitor_trigger():
    """Called on a SHORT timer by a SEPARATE, more-frequent Render Cron Job
    than the one hitting /api/autonomy/cron-trigger above (that one runs
    the full 5-minute scan). Runs ONLY _run_fast_order_monitor for each
    user _user_needs_fast_monitor_pass says has something to check -
    reconciliation/resumption passes, never the market-scan/new-candidate-
    sizing work the full scan does - see that function's docstring for
    exactly why that narrow scope is what makes this safe to run this
    often.

    CADENCE CORRECTION (confirmed against Render's own docs this session,
    since this had previously been described as "30-60 seconds" without
    verification): Render Cron Jobs use standard cron expression syntax,
    whose minimum resolution is ONE MINUTE - there is no native way to
    schedule a Render Cron Job more often than once per minute. The
    realistic cadence for this endpoint via a native Render Cron Job is
    therefore ~60 seconds, not 30-60. A Render Cron Job also does not
    natively call an HTTP endpoint - it runs an arbitrary shell command in
    a container on each firing, so the actual cron job configuration must
    literally be a curl command, e.g.:
        curl -X POST -H "X-Cron-Secret: $CRON_SECRET" https://<this-app>/api/autonomy/fast-monitor-trigger
    with CRON_SECRET set as an environment variable on THAT Cron Job
    service (Render env vars are per-service, not shared automatically
    with the main web service).

    Deliberately NOT filtered by autonomy mode (unlike the cron-trigger
    endpoint above, whose _run_autonomous_trade_scan call legitimately
    IS AUTONOMOUS-only, since THAT work includes placing new entries) -
    see _user_needs_fast_monitor_pass: "safety monitoring must continue
    when autonomy is switched OFF - OFF prevents new entries only". A
    user who turned autonomy off is still fully covered here as long as
    they have anything transitional, tracked, or unresolved.

    This endpoint existing does NOT, on its own, make the fast monitor
    active - the Render Cron Job described above still needs to be set up
    separately (outside this repo). Until then, _run_autonomous_trade_scan_locked's
    own direct call to _monitor_transitional_orders is the only thing
    resuming ordinary transitional orders, at the slower 5-minute cadence -
    and that call is STILL gated by AUTONOMOUS mode today, since it only
    runs as part of the full scan the cron-trigger endpoint already
    restricts that way.

    Records a heartbeat (fast_monitor_heartbeat.py) around the ENTIRE
    sweep - a "started" stamp before the per-user loop, a "completed"
    stamp (with aggregate entries-checked/still-transitional counts and a
    per-account failure map) after it - regardless of how many users had
    anything to do. "Adding an endpoint is insufficient" without a way to
    tell whether its scheduler is actually calling it: see
    _fast_monitor_health_status, which the full 5-minute scan and the
    admin-wide banner both surface a "monitor unhealthy" signal from if
    this heartbeat goes stale.

    Also does the CROSS-CHECK in the other direction - checks the FULL
    scan's own heartbeat (_alert_admins_full_scan_unhealthy_if_needed) and
    alerts if IT has gone stale. Neither scheduler can detect its own
    silence; each can only detect the OTHER'S. See that function's
    docstring for the residual gap this still leaves (both stopping at
    once) and api_autonomy_monitor_health, the external, unauthenticated
    endpoint that exists specifically to close it via a third-party uptime
    monitor."""
    expected_secret = os.environ.get("CRON_SECRET", "").strip()
    provided_secret = request.headers.get("X-Cron-Secret", "").strip()
    if not expected_secret or not hmac.compare_digest(expected_secret, provided_secret):
        return _api_failure("Invalid or missing cron secret.", status_code=401, error_code="unauthorized", ok=False)

    _alert_admins_full_scan_unhealthy_if_needed()
    run_id = record_fast_monitor_run_started()
    results = []
    failures_by_account: Dict[str, str] = {}
    total_entries_checked = 0
    total_still_transitional = 0
    for user_id in list_all_user_ids():
        # _user_needs_fast_monitor_pass alone would skip a user with ZERO
        # local records - but that is EXACTLY the orphan-discovery case
        # (see _discover_orphaned_broker_entries, now called from inside
        # _run_fast_order_monitor): local state can't identify a missing
        # local write, so a Webull-configured user must still get a pass
        # even with nothing locally transitional. is_webull_configured is
        # a cheap, local-only check; _run_fast_order_monitor's own setup
        # still validates the account is actually connected and raises
        # ValidationError (caught below, same as any other per-user
        # failure) if not.
        if not (_user_needs_fast_monitor_pass(user_id) or is_webull_configured(user_id)):
            continue
        with app.test_request_context():
            session["user_id"] = user_id
            try:
                monitor_result = _run_fast_order_monitor(user_id)
                results.append({"user_id": user_id, "ok": True, **monitor_result})
                total_entries_checked += int(monitor_result.get("entries_checked") or 0)
                total_still_transitional += int(monitor_result.get("still_transitional_count") or 0)
            except ScanAlreadyRunningError:
                # Benign, expected overlap with a concurrent full scan (or
                # another fast tick) for the same user - skip, the next
                # tick will try again shortly.
                results.append({"user_id": user_id, "ok": True, "skipped": "scan_already_running"})
            except Exception as error:  # noqa: BLE001 - one user's failure shouldn't block others
                results.append({"user_id": user_id, "ok": False, "error": str(error)})
                failures_by_account[user_id] = str(error)

    record_fast_monitor_run_completed(
        run_id,
        entries_checked=total_entries_checked,
        still_transitional=total_still_transitional,
        failures_by_account=failures_by_account,
    )

    return _api_success({"ran_for_users": len(results), "results": results}, ok=True, ran_for_users=len(results))


@app.route("/api/autonomy/continuous-monitor-tick", methods=["POST"])
def api_autonomy_continuous_monitor_tick():
    """Option A (per explicit reviewer decision): the endpoint a SEPARATE,
    independently-deployed worker process (a Render Background Worker -
    see continuous_monitor_worker.py, NOT part of this web service) calls
    on a tight loop (default CONTINUOUS_MONITOR_DEFAULT_INTERVAL_SECONDS,
    10s) to reconcile active orders far faster than the ~60s fast-monitor
    cron or the 5-minute full scan can. The worker itself has NO disk
    access and NO Webull credentials - it is a supervised scheduler only;
    THIS endpoint, which already owns the persistent disk and every
    user's encrypted credentials, is what actually performs reconciliation,
    by calling _run_fast_order_monitor per user - the exact same,
    already-tested, never-scans-never-places-entries function the
    ~60s cron-triggered fast-monitor-trigger endpoint calls (see
    test_fast_monitor_never_scans_scores_or_places_a_new_entry, which
    covers this call path too).

    AUTHENTICATION - a DEDICATED secret (MONITOR_WORKER_SECRET), NOT
    CRON_SECRET: this endpoint is meant to be called far more frequently,
    from a differently-deployed process, and rotating/revoking its
    credential should never require also rotating the slower cron jobs'
    shared secret. Same constant-time comparison (hmac.compare_digest)
    already used for CRON_SECRET elsewhere in this file - never a plain
    `==`, which would leak timing information about how many leading
    characters matched.

    NEVER scans for candidates or places entry orders - structurally
    true, not merely documented: this calls _run_fast_order_monitor,
    which only ever calls _discover_orphaned_broker_entries,
    _reconcile_exit_orders, _reconcile_unknown_submissions,
    _recover_incomplete_manual_resolutions, and _monitor_transitional_orders -
    none of which touch get_market_data/build_strategy_intelligence/
    _submit_and_protect_entry/webull_api.place_stock_order.

    Includes every user with unfinished orders REGARDLESS of autonomy
    mode - same _user_needs_fast_monitor_pass(user_id) or
    is_webull_configured(user_id) gate as the fast-monitor-trigger
    endpoint (see that endpoint's own comment for why the OR is
    necessary: local state alone can't identify a user needing orphan
    discovery).

    NO OVERLAPPING REQUESTS, enforced at two independent layers:
      - the WORKER itself must never fire the next request before the
        previous one returns or times out (see continuous_monitor_worker.py -
        a strictly sequential loop, not a fire-and-forget interval timer);
      - belt-and-suspenders on THIS side: continuous_monitor_tick_lock()
        is a GLOBAL, non-blocking lock - if a previous tick's per-user
        loop is somehow still running when a new request arrives (a
        worker bug, a duplicate deploy, a retried request racing the
        original), this one returns 409 immediately rather than doing
        redundant/racy work. Existing PER-ACCOUNT locks
        (scan_lock.user_scan_lock, held inside _run_fast_order_monitor)
        remain the actual authoritative data-race protection regardless -
        this global lock only prevents wasted duplicate WORK, since any
        real per-user conflict was already impossible before this lock
        existed.

    Records TWO heartbeat signals (continuous_monitor_heartbeat.py) -
    "worker reached us" (stamped before the lock, so recorded even on a
    409-skip) and "reconciliation completed" (stamped after the per-user
    loop) - see that module's docstring for why both are needed
    independently."""
    expected_secret = os.environ.get("MONITOR_WORKER_SECRET", "").strip()
    provided_secret = request.headers.get("X-Monitor-Worker-Secret", "").strip()
    if not expected_secret or not hmac.compare_digest(expected_secret, provided_secret):
        return _api_failure("Invalid or missing monitor worker secret.", status_code=401, error_code="unauthorized", ok=False)

    run_id = record_continuous_monitor_request_received()

    try:
        with continuous_monitor_tick_lock():
            results = []
            failures_by_account: Dict[str, str] = {}
            total_entries_checked = 0
            total_still_transitional = 0
            for user_id in list_all_user_ids():
                if not (_user_needs_fast_monitor_pass(user_id) or is_webull_configured(user_id)):
                    continue
                with app.test_request_context():
                    session["user_id"] = user_id
                    try:
                        monitor_result = _run_fast_order_monitor(user_id)
                        results.append({"user_id": user_id, "ok": True, **monitor_result})
                        total_entries_checked += int(monitor_result.get("entries_checked") or 0)
                        total_still_transitional += int(monitor_result.get("still_transitional_count") or 0)
                    except ScanAlreadyRunningError:
                        # Benign, expected overlap with a concurrent full
                        # scan (or fast-monitor tick) for the SAME user -
                        # skip, the next tick will try again shortly.
                        results.append({"user_id": user_id, "ok": True, "skipped": "scan_already_running"})
                    except Exception as error:  # noqa: BLE001 - one user's failure shouldn't block others
                        results.append({"user_id": user_id, "ok": False, "error": str(error)})
                        failures_by_account[user_id] = str(error)

            record_continuous_monitor_reconciliation_completed(
                run_id,
                entries_checked=total_entries_checked,
                still_transitional=total_still_transitional,
                failures_by_account=failures_by_account,
            )
            # Temporary leak-hunting instrumentation - see the module-level
            # comment above _MEMORY_PROFILING_ENABLED. This IS the ~10s hot
            # loop under investigation, so the snapshot is taken from
            # exactly the code path in question.
            _maybe_log_memory_profile_snapshot()
    except ContinuousMonitorTickAlreadyRunningError as error:
        # Deliberately does NOT call record_continuous_monitor_reconciliation_completed -
        # no reconciliation actually happened this request. "Worker
        # reached us" was already recorded above regardless.
        return _api_failure(str(error), status_code=409, error_code="continuous_monitor_tick_already_running", ok=False)

    return _api_success({"ran_for_users": len(results), "results": results}, ok=True, ran_for_users=len(results))


@app.route("/api/autonomy/monitor-health", methods=["GET"])
def api_autonomy_monitor_health():
    """DELIBERATELY UNAUTHENTICATED (no CRON_SECRET, no session) and
    read-only - the residual gap neither in-app scheduler can close on its
    own: _alert_admins_fast_monitor_unhealthy_if_needed and
    _alert_admins_full_scan_unhealthy_if_needed each let ONE scheduler
    detect the OTHER going silent, but if BOTH stop firing at the exact
    same time (e.g. the whole web service is down, or both Render Cron
    Jobs were deleted together), neither cross-check ever runs, and
    nothing inside this app can notice its own total silence. This
    endpoint exists so an EXTERNAL, independent uptime service (e.g.
    UptimeRobot, Healthchecks.io, Render's own health checks pointed at a
    custom path) can poll it on its own schedule and alert through a
    completely separate channel (email/SMS/PagerDuty) if it ever stops
    responding at all - closing that gap requires configuring such a
    service in the operator's own account; this endpoint is the piece
    this app can provide, not a substitute for actually setting one up.

    MINIMAL response shape, deliberately - reviewer instruction: no
    per-scheduler breakdown, no reasons, no heartbeat internals, no user
    IDs, no account details, no error text, no paths, no secrets. Just:
        {"healthy": bool, "last_completed_age_seconds": number | null}
    A human-readable "why" (which scheduler, what error) is already
    available to admins via _build_page_context's fast_monitor_health/
    full_scan_health fields (session-authenticated, admin-only) - this
    public, unauthenticated surface exists ONLY so an external uptime
    service can detect total silence, not to explain it. GET, not POST
    (no CRON_SECRET to protect it with), and NOT registered in
    _TOKEN_AUTH_PATHS - see _PUBLIC_PATHS, where this path is listed
    instead, so the global before_request session-auth gate never blocks
    an external caller with no session either.

    last_completed_age_seconds is the WORST (largest / most stale) age
    across every tracked scheduler, rounded to a whole second - "how long
    since the least-recently-healthy signal last completed" - null only
    if a scheduler has literally never run even once (age is undefined,
    not merely large).

    Tracks THREE schedulers as of the continuous-monitor (Option A) work:
    fast_monitor, full_scan, and continuous_monitor. overall_healthy
    requires ALL THREE to be healthy - once the continuous-monitor worker
    is actually deployed, this endpoint will correctly report unhealthy
    if IT alone goes quiet, even while the two slower schedulers are
    fine, since it's now the PRIMARY safety mechanism. Before the worker
    is deployed at all, continuous_monitor's own health check reports
    unhealthy with age_seconds=None (never called) - deploying this
    endpoint code before the worker exists will make this report
    unhealthy; that is an accurate reflection of the architecture this
    endpoint now describes, not a bug, and is exactly why the worker
    should be deployed at the same time as - or before - this code."""
    fast_monitor = _fast_monitor_health_status()
    full_scan = _full_scan_health_status()
    continuous_monitor = _continuous_monitor_health_status()
    overall_healthy = bool(fast_monitor.get("healthy")) and bool(full_scan.get("healthy")) and bool(continuous_monitor.get("healthy"))
    ages = [a for a in (fast_monitor.get("age_seconds"), full_scan.get("age_seconds"), continuous_monitor.get("age_seconds")) if a is not None]
    last_completed_age_seconds = round(max(ages)) if ages else None
    payload = {"healthy": overall_healthy, "last_completed_age_seconds": last_completed_age_seconds}
    return _api_success(payload, status_code=200 if overall_healthy else 503, **payload)


@app.route("/api/accounts", methods=["GET"])
@api_guard
def api_accounts():
    user_id = _current_user_id()
    accounts = get_accounts(user_id)
    broker_framework = _broker_framework_status()
    safety = {
        "store_passwords": False,
        "hardcoded_api_keys": False,
        "credentials_source": "Per-user, entered in Account Hub - never shared across accounts",
        "live_trading_default_off": True,
        "etrade_approval_mode_required": True,
        "webull_default_paper_mode": True,
        "tradingview_executes_trades": False,
        "options_execution_disabled": True,
        "approval_required_by_default": True,
        "emergency_kill_switch_placeholder": True,
    }
    return _api_success(
        {"accounts": accounts, "safety": safety, "broker_framework": broker_framework, "webull_configured": is_webull_configured(user_id)},
        accounts=accounts,
        safety=safety,
        webull_configured=is_webull_configured(user_id),
    )


@app.route("/api/accounts/connect", methods=["POST"])
def api_accounts_connect():
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "")
    if not platform:
        return jsonify({"ok": False, "error": "Platform is required."}), 400

    user_id = _current_user_id()
    try:
        if payload.get("action") == "generate_webhook":
            account = ensure_tradingview_webhook(user_id, platform=platform)
            return jsonify({"ok": True, "account": account})
        if "trading_enabled" in payload:
            account = update_trading_enabled(user_id, platform=platform, trading_enabled=bool(payload.get("trading_enabled")))
            return jsonify({"ok": True, "account": account})
        account = connect_account(user_id, platform=platform)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "account": account})


@app.route("/api/accounts/disconnect", methods=["POST"])
def api_accounts_disconnect():
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "")
    if not platform:
        return jsonify({"ok": False, "error": "Platform is required."}), 400
    try:
        account = disconnect_account(_current_user_id(), platform=platform)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "account": account})


@app.route("/api/accounts/test", methods=["POST"])
def api_accounts_test():
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "")
    if not platform:
        return jsonify({"ok": False, "error": "Platform is required."}), 400
    try:
        account = test_account(_current_user_id(), platform=platform)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "account": account})


@app.route("/api/accounts/webull-credentials", methods=["POST"])
def api_accounts_webull_credentials():
    """Each user brings their own Webull OpenAPI app key/secret - saved here,
    never a shared server-wide credential, so one user can never connect to
    or see another user's Webull sandbox account."""
    payload = request.get_json(silent=True) or {}
    try:
        set_webull_credentials(_current_user_id(), payload.get("app_key", ""), payload.get("app_secret", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "configured": True})


@app.route("/api/accounts/anthropic-credentials", methods=["POST"])
def api_accounts_anthropic_credentials():
    """Each user brings their own Anthropic API key for the LLM reasoning
    pass on autonomous trade candidates - it's opt-in and billed to whoever
    configures it, never a shared server-wide key."""
    payload = request.get_json(silent=True) or {}
    try:
        set_anthropic_api_key(_current_user_id(), payload.get("api_key", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "configured": True})


@app.route("/api/tradingview/webhook", methods=["POST"])
@api_guard
def api_tradingview_webhook():
    # TradingView has no session/login - the token in the URL is the only
    # identifying information, so it has to double as both auth and the
    # lookup key for which user this alert belongs to.
    token = request.args.get("token", "")
    owner = find_user_by_webhook_token(token)
    if not owner or not verify_tradingview_token(owner["id"], token):
        return _api_failure(
            "Invalid or missing webhook token.",
            status_code=401,
            error_code="invalid_webhook_token",
            ok=False,
        )
    user_id = owner["id"]
    # TradingView's default alert message is plain text unless the user's alert
    # is configured to send JSON, and it doesn't always set Content-Type:
    # application/json even then - so parse defensively instead of trusting
    # get_json() to succeed. force=True skips the content-type check; if the
    # body still isn't valid JSON, fall back to capturing it as free text so
    # the alert isn't silently dropped into an empty payload.
    payload = request.get_json(silent=True, force=True)
    if not isinstance(payload, dict):
        raw_text = request.get_data(as_text=True) or ""
        try:
            parsed = json.loads(raw_text) if raw_text else {}
            payload = parsed if isinstance(parsed, dict) else {"message": raw_text}
        except ValueError:
            payload = {"message": raw_text} if raw_text else {}
    stored_alert = save_alert(user_id, payload)
    account_result = record_tradingview_signal(user_id, payload=payload)
    result = {
        "signal_received": True,
        "alert": stored_alert,
        "account_status": account_result,
        "never_auto_execute": True,
    }
    return _api_success(result, ok=True, **result)


@app.route("/api/status", methods=["GET"])
@api_guard
def api_status():
    context = _build_page_context(include_news=True)
    tradingview = get_tradingview_status(_current_user_id())
    neural = context["status_summary"]["neural_status"]
    broker_statuses = _broker_framework_status()
    data = {
        "api": {
            "healthy": True,
            "version": "foundation-sprint",
        },
        "market": {
            "scanner_status": context["status_summary"]["scanner_status"],
            "market_status": context["status_summary"]["market_status"],
            "last_scanner_update": context["scanner_last_updated"],
        },
        "brokers": broker_statuses,
        "tradingview": tradingview,
        "neural": neural,
        "intelligence": {
            "upcoming_opportunities": context.get("upcoming_opportunities", []),
            "mission_queue": context.get("mission_queue", []),
            "strategy_map": context.get("strategy_map", {}),
            "chart_levels_map": context.get("chart_levels_map", {}),
        },
        "safety": {
            "live_trading_enabled": False,
            "options_execution_enabled": False,
            "approval_required": True,
            "emergency_kill_switch_placeholder": True,
        },
        "autonomy": get_autonomy_status(_current_user_id()),
    }
    return _api_success(data)


@app.route("/api/news", methods=["GET"])
@api_guard
def api_news():
    tickers = get_watchlist_tickers(_current_user_id()) or CORE_SCAN_UNIVERSE[:4]
    bundle = fetch_news_bundle(tickers=tickers, limit=30)
    return _api_success(bundle, news=bundle.get("items", []), errors=bundle.get("errors", []))


@app.route("/api/neural/status", methods=["GET"])
@api_guard
def api_neural_status():
    watchlist = get_watchlist(_current_user_id())
    scanner_rows, _, _ = get_market_data(force_refresh=False)
    news_bundle = fetch_news_bundle(tickers=[item["ticker"] for item in watchlist], limit=10)
    option_inputs = [get_options_data_for_ticker(row["ticker"], force_refresh=False) for row in scanner_rows[:3]]
    neural = build_neural_status(
        scanner_rows=scanner_rows,
        watchlist_rows=watchlist,
        news_items=news_bundle.get("items", []),
        options_payloads=option_inputs,
    )
    top_tickers = _resolve_analysis_tickers([item["ticker"] for item in watchlist], scanner_rows, limit=5)
    strategy_map = {ticker: get_strategy_data_for_ticker(ticker=ticker, force_refresh=False) for ticker in top_tickers}
    chart_levels_map = {ticker: get_chart_levels_for_ticker(ticker=ticker, force_refresh=False) for ticker in top_tickers}
    return _api_success(
        {
            **neural,
            "strategy_map": strategy_map,
            "chart_levels_map": chart_levels_map,
        }
    )


@app.route("/api/tradingview/status", methods=["GET"])
@api_guard
def api_tradingview_status():
    return _api_success(get_tradingview_status(_current_user_id()))


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
