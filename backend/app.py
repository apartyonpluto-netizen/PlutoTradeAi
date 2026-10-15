from __future__ import annotations

import hmac
import json
import math
import os
import resource
import secrets as secrets_module
import sys
import threading
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait as futures_wait
from contextlib import contextmanager
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for

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
        SHADOW_ONLY_BRAINS,
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
    from .readiness import build_report as build_readiness_report
    from .observability import is_active as observability_active
    from .integrations import market_data_aggregator
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
    from .autonomy.outcomes_analysis import build_outcomes_analysis
    from .autonomy.lifecycle_tracker import build_lifecycle_summary
    from .calibration_store import MIN_TRADES_TO_TRUST, REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL
    from .autonomy.daily_digest import build_daily_digest
    from .autonomy.efficiency_report import build_efficiency_report
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
    from .autonomy import event_journal, trade_tickets
    from .autonomy.research_log import list_research_decisions, record_research_decision
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
    from . import portfolio_limits, trade_planner
    from .calibration import (
        closed_trade_returns_by_strategy,
        get_calibration,
        maybe_trigger_real_outcomes_recalibration,
        recalibrate_from_real_outcomes,
        start_calibration,
    )
    from . import confirm_entry, evidence_gates, ml_confidence, suggestion_ledger, trailing_stop
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
    from . import backups, broker_env, broker_fees, broker_reconciliation, login_throttle, observatory
    from .setups import service as setups_service
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
        SHADOW_ONLY_BRAINS,
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
    from readiness import build_report as build_readiness_report
    from observability import is_active as observability_active
    from integrations import market_data_aggregator
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
    from autonomy.outcomes_analysis import build_outcomes_analysis
    from autonomy.lifecycle_tracker import build_lifecycle_summary
    from calibration_store import MIN_TRADES_TO_TRUST, REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL
    from autonomy.daily_digest import build_daily_digest
    from autonomy.efficiency_report import build_efficiency_report
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
    from autonomy import event_journal, trade_tickets
    from autonomy.research_log import list_research_decisions, record_research_decision
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
    import portfolio_limits
    import trade_planner
    from calibration import (
        closed_trade_returns_by_strategy,
        get_calibration,
        maybe_trigger_real_outcomes_recalibration,
        recalibrate_from_real_outcomes,
        start_calibration,
    )
    import confirm_entry
    import evidence_gates
    import ml_confidence
    import suggestion_ledger
    import trailing_stop
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
    import broker_env
    import backups
    import broker_fees
    import broker_reconciliation
    import observatory
    import login_throttle
    from setups import service as setups_service
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

try:
    from .observability import init_sentry
except ImportError:
    from observability import init_sentry

init_sentry("web")
app = Flask(__name__)
setup_logging()
logger = get_logger("app")

# User strategies live in their own blueprint so this file stays out of the
# scanner and order path. The blueprint does not submit broker orders.
try:
    from .user_strategies.blueprint import user_strategies_bp
except ImportError:
    from user_strategies.blueprint import user_strategies_bp

app.register_blueprint(user_strategies_bp)

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
def _memory_profiling_requested(environ=os.environ) -> bool:
    """OFF unless PLUTO_MEMORY_PROFILING is set to 1/true/on/yes. It used to default ON; changed
    2026-09-27 after measuring tracemalloc.start(10) slowing allocation-heavy code (JSON parsing,
    which is most of what a monitor tick does) by ~30x in a micro-benchmark, alongside production
    ticks taking 3-37s against ~0.1-0.3s per Webull call - enough to trip the worker's 30s timeout
    and show the monitor as unhealthy. Turn it back on only while actively hunting a leak."""
    return environ.get("PLUTO_MEMORY_PROFILING", "0").strip().lower() in ("1", "true", "on", "yes")


_MEMORY_PROFILING_ENABLED = (
    _memory_profiling_requested()
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




_PUBLIC_PATHS = {"/login", "/register", "/logout", "/forgot-password", "/service-worker.js", "/healthz", "/api/autonomy/monitor-health"}
_PUBLIC_PATH_PREFIXES = ("/static/",)
_TOKEN_AUTH_PATHS = {
    "/api/tradingview/webhook",
    "/api/autonomy/cron-trigger",
    "/api/autonomy/fast-monitor-trigger",
    "/api/autonomy/continuous-monitor-tick",
}


_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.before_request
def _reject_cross_site_writes():
    """CSRF defense in depth (on top of the SameSite=Lax session cookie):
    a state-changing request whose Origin - or, without Origin, Referer -
    names a different host is refused. Browsers always send Origin on
    cross-origin POSTs, so a forged request from another site cannot pass;
    server-to-server callers (the cron, webhooks) send neither header and
    authenticate with their own secret."""
    if request.method not in _STATE_CHANGING_METHODS or request.path in _TOKEN_AUTH_PATHS:
        return None
    if request.path.startswith("/api/confirm-entry/"):
        return None
    from urllib.parse import urlsplit

    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return None
    source_host = urlsplit(source).netloc.lower()
    if source == "null" or source_host != request.host.lower():
        logger.warning("Refused cross-site %s %s from %s", request.method, request.path, source_host or source)
        if request.path.startswith("/api/"):
            return _api_failure("Cross-site request refused.", status_code=403, error_code="cross_site", ok=False)
        return ("Cross-site request refused.", 403)
    return None


@app.before_request
def _require_login():
    path = request.path
    if path in _PUBLIC_PATHS or path in _TOKEN_AUTH_PATHS or path.startswith("/api/confirm-entry/"):
        return None
    if any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES):
        return None

    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if user and user.get("approved", True) and not user.get("suspended", False):
        try:
            import go_live_gate
            go_live_gate.bind(user_id)
        except Exception:
            pass
        return None

    session.pop("user_id", None)
    if path.startswith("/api/"):
        return _api_failure("Authentication required. Please log in.", status_code=401, error_code="unauthorized", ok=False)
    return redirect(url_for("login_page", next=path))












def _current_user_id() -> str:
    """Guaranteed non-empty for any route reachable past the before_request auth gate."""
    return session.get("user_id", "")


def _require_admin():
    if not is_admin(_current_user_id()):
        return _api_failure("Admin access required.", status_code=403, error_code="forbidden", ok=False)
    return None
























































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
# A full Nasdaq screen is a few dozen Alpaca snapshot calls plus bar
# scoring of the liquid survivors. 20s was enough for the old fixed list
# and cut the new screen off. 75s stays under the 90s gunicorn timeout;
# gthread keeps /healthz responsive while one scan runs.
SCAN_MARKET_DEADLINE_SECONDS = 75
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
    rows, errors, last_updated = _run_with_hard_deadline(
        scan_market,
        kwargs={
            "tickers": None,
            "watchlist_tickers": watchlist_tickers,
            "universe_fallback": list(CORE_SCAN_UNIVERSE),
            "force_refresh": force_refresh,
        },
        deadline_seconds=SCAN_MARKET_DEADLINE_SECONDS,
        default=([], ["Scanner timed out before the Nasdaq screen finished. Showing the last good list if one is cached."], _now_utc().isoformat()),
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
            "summary": getattr(rows, "summary", None) or {},
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


# Maps a user-facing chart range (2026-09-04 - "different time intervals to
# get a more in-depth look") to a (period, interval) pair alpaca_data.
# get_bars_single actually accepts - each entry pairs a real candle
# granularity (matching what any real broker's chart interval selector
# offers: 1m/5m/10m/15m/30m/1h/1d) with a lookback window sized so the
# request stays a reasonable number of bars (a 1-minute chart shows the
# most recent trading day, not 9 months of 1-minute bars) -
# _PERIOD_TO_LOOKBACK_DAYS/_INTERVAL_TO_ALPACA_TIMEFRAME in
# integrations/alpaca_data.py are this app's own explicit allowlists
# (extended 2026-09-04 alongside this to add 1m/10m/15m/30m/1h), not a
# ceiling Alpaca's real API imposes. "1d" (daily bars, ~9mo lookback) is
# the default - the pre-existing, only-ever behavior before an interval
# selector existed at all.
CHART_INTERVAL_TO_PERIOD: Dict[str, str] = {
    "1m": "1d",
    "5m": "5d",
    "10m": "5d",
    "15m": "1mo",
    "30m": "1mo",
    "1h": "9mo",
    "1d": "9mo",
}
DEFAULT_CHART_INTERVAL = "1d"


def _build_chart_history_payload(ticker: str, chart_interval: str = DEFAULT_CHART_INTERVAL) -> Dict[str, object]:
    """Raw OHLC candles for the real dashboard chart (2026-09-04) - the
    SAME alpaca_data.get_bars_single call charting_brain.py's
    build_chart_levels already uses internally (real, Alpaca-sourced, not
    Yahoo - see that module's own migration comment), just exposed here as
    plain candles instead of computed levels. A dropped/NaN close (e.g. the
    most recent bar before today's session has fully settled - observed
    live this session) is excluded rather than sent to the frontend as
    NaN, which is not valid JSON and would break JSON.parse on the
    receiving end."""
    period = CHART_INTERVAL_TO_PERIOD.get(chart_interval, CHART_INTERVAL_TO_PERIOD[DEFAULT_CHART_INTERVAL])
    try:
        history = alpaca_data.get_bars_single(ticker, period=period, interval=chart_interval)
    except Exception as error:  # noqa: BLE001 - a data-fetch failure is a normal, expected outcome for a bad/delisted ticker, not a crash
        return {"ticker": ticker, "interval": chart_interval, "candles": [], "error": str(error), "generated_at": _now_utc().isoformat()}
    if history.empty:
        return {"ticker": ticker, "interval": chart_interval, "candles": [], "error": "No historical data available.", "generated_at": _now_utc().isoformat()}

    intraday = chart_interval != "1d"
    candles: List[Dict[str, object]] = []
    for index, row in history.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        candles.append(
            {
                # Intraday bars need a real timestamp (lightweight-charts
                # plots multiple same-day bars distinctly only with one) -
                # daily bars keep the plain date string every existing
                # caller of this endpoint already expects.
                "date": index.isoformat() if intraday else index.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(close), 2),
                "volume": int(row.get("Volume", 0) or 0),
            }
        )
    return {"ticker": ticker, "interval": chart_interval, "candles": candles, "error": "", "generated_at": _now_utc().isoformat()}


def get_chart_history_for_ticker(ticker: str, chart_interval: str = DEFAULT_CHART_INTERVAL, force_refresh: bool = False) -> Dict[str, object]:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker is required.")
    # Deliberately an EXACT, case-sensitive match against
    # CHART_INTERVAL_TO_PERIOD's keys - no upper()/lower() folding. "m"
    # (minute) and "M" (month, in the old range-based design this
    # replaced) are not interchangeable; case-folding either direction
    # would silently coerce one into meaning the other. Anything that
    # isn't an exact match (including a stray "1M") falls back to the
    # default rather than guessing which one was meant.
    normalized_interval = chart_interval.strip() if chart_interval else DEFAULT_CHART_INTERVAL
    if normalized_interval not in CHART_INTERVAL_TO_PERIOD:
        normalized_interval = DEFAULT_CHART_INTERVAL

    cache_key = f"{normalized_ticker}:{normalized_interval}"
    cached_item = CHART_HISTORY_CACHE.get(cache_key)
    if (
        not force_refresh
        and isinstance(cached_item, dict)
        and isinstance(cached_item.get("expires_at"), datetime)
        and cached_item["expires_at"] > _now_utc()
    ):
        return cached_item["payload"]

    payload = _build_chart_history_payload(normalized_ticker, normalized_interval)
    CHART_HISTORY_CACHE[cache_key] = {
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
    live_trading_armed = webull_api.is_live_trading_armed()
    return {
        "etrade": etrade.get_account_status(),
        "webull": webull.get_account_status(),
        "safety_defaults": {
            "live_trading_enabled": live_trading_armed,
            "options_execution_enabled": False,
            "etrade_execution_enabled": False,
            "webull_paper_mode_only": not live_trading_armed,
            "approval_required": True,
            "emergency_kill_switch_placeholder": False,
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
        "current_time": _now_utc().astimezone(ZoneInfo("America/New_York")).strftime("%I:%M:%S %p"),  # labelled Eastern Time in base.html; datetime.now() was server (UTC) time on Render
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
        "live_trading_enabled": webull_api.is_live_trading_armed(),
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
    scanner_summary: Dict[str, object] = {}
    if include_market_scan:
        scanner_rows, scanner_errors, scanner_last_updated = get_market_data(force_refresh=force_refresh)
        scanner_summary = MARKET_CACHE.get("summary") or {}
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
        ideal_entry = round(breakout_level * 1.001, 2) if bias == "CALL" else round(breakdown_level * 0.999, 2)
        target = round(breakout_level * 1.02, 2) if bias == "CALL" else round(breakdown_level * 0.98, 2)
        stop = round(breakdown_level * 0.997, 2) if bias == "CALL" else round(breakout_level * 1.003, 2)

        # A CALL/PUT call is only actually tradeable if its chart
        # breakout/breakdown levels produced a usable protective structure:
        # entry, stop and target all > 0 and on the right sides (long:
        # stop < entry < target; short: the mirror). When
        # get_chart_levels_for_ticker returns 0 for a level it couldn't
        # identify, these zero out - and a candidate that reaches
        # _run_autonomous_trade_scan_locked like that is now rejected there
        # (skip_category="unprotectable_levels", 2026-09-10, the root-cause
        # fix for the 2026-09-04 orphan cascade). It should never have
        # looked tradeable on the dashboard / mission queue in the first
        # place either. Downgrade it to WAIT - the ticker stays visible
        # with its real confidence score, it just isn't presented as an
        # actionable directional entry.
        levels_unavailable = False
        if bias in ("CALL", "PUT"):
            structure_ok = (
                ideal_entry > 0 and stop > 0 and target > 0
                and (stop < ideal_entry < target if bias == "CALL" else target < ideal_entry < stop)
            )
            if not structure_ok:
                levels_unavailable = True
                bias = "WAIT"

        # Market-intel votes nudge a score the strategy gate already accepted.
        # They cannot add a ticker the floor rejected (that check already
        # happened), and score() itself refuses to lift a below-floor number
        # up to the floor. A dead source leaves its vote missing.
        base_confidence = confidence
        market_intel_record = None
        try:
            import market_intel

            market_intel_record = market_intel.score(
                ticker,
                recommendation=bias,
                base_confidence=base_confidence,
                threshold=confidence_floor,
            )
            confidence = int(market_intel_record["adjusted_confidence"])
        except Exception:
            market_intel_record = None

        trade_quality = "A" if confidence >= 85 else "B" if confidence >= 72 else "C"
        expiration_suggestions = options_payload.get("expiration_suggestions", ["Data unavailable"] * 3)
        upcoming_opportunities.append(
            {
                "ticker": ticker,
                "current_price": current_price,
                "recommendation": bias,
                "confidence": confidence,
                "market_intel": market_intel_record,
                "trade_quality": trade_quality,
                "ideal_entry": ideal_entry,
                "support": support,
                "resistance": resistance,
                "breakout_level": breakout_level,
                "breakdown_level": breakdown_level,
                "target": target,
                "stop": stop,
                "levels_unavailable": levels_unavailable,
                "expected_hold_time": strategy.get("expected_hold_time", "Unknown"),
                "expected_move": options_payload.get("expected_move", "Data unavailable"),
                "strategy": strategy.get("best_strategy", strategy.get("recommended_strategy", "Unknown")),
                "trade_thesis": (
                    f"Engine sees a {strategy.get('recommendation', '')} setup (confidence {confidence}) but the chart "
                    "breakout/breakdown levels needed to place a protected trade aren't available - shown as WAIT until they are."
                    if levels_unavailable
                    else strategy.get("why_this_strategy_fits", "Data unavailable")
                ),
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
                # Pass-through, never recomputed - this is exactly what
                # strategy_brain actually used to produce confidence/
                # recommendation for this candidate (EMA stack, RSI, VWAP,
                # support/resistance, relative volume, every strategy it
                # scored and rejected). Zero marginal cost (strategy_brain
                # already computed this once above) - carried through so
                # the autonomous scan can durably record it as a signal
                # snapshot (see docs/AGENT_ARCHITECTURE.md's "memory"
                # section) without ever recomputing or re-fetching
                # anything. Not read by any decision logic itself.
                "market_context": strategy.get("market_context"),
                "strategies_evaluated": strategy.get("strategies_evaluated"),
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
        "scanner_summary": scanner_summary,
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
    try:
        import home_status as home_status_mod
        from go_live_gate import evaluate as evaluate_go_live

        context["home_status"] = home_status_mod.build_home_status(user_id)
        context["savings_goal"] = home_status_mod.build_savings_goal(user_id)
        context["daily_recap"] = home_status_mod.build_daily_recap(user_id)
        context["go_live"] = evaluate_go_live(user_id)
        context["trade_cards"] = home_status_mod.build_trade_cards(user_id)
    except Exception:
        context.setdefault("home_status", None)
        context.setdefault("savings_goal", None)
        context.setdefault("daily_recap", None)
        context.setdefault("go_live", None)
        context.setdefault("trade_cards", [])
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


@app.context_processor
def inject_loss_cap():
    """Home meter. Same inputs as the scan halt: broker day P/L (cash plus
    margin when that balance was readable) and the sizing equity already on
    the balance payload. Runs only while rendering a page, not during a scan."""
    user_id = session.get("user_id")
    if not user_id:
        return {}
    try:
        import daily_loss
        payload = _get_live_webull_balance(user_id)
        balance = (payload or {}).get("balance") or None
        if not isinstance(balance, dict) or balance.get("day_pnl") is None:
            return {"loss_cap": {"available": False}}
        status = get_autonomy_status(user_id)
        state = daily_loss.evaluate(
            float(balance["day_pnl"]),
            float(balance["net_liquidation_value"]),
            float(status.get("daily_loss_limit_percent") or 0),
        )
        state["available"] = True
        return {"loss_cap": state}
    except Exception:
        return {"loss_cap": {"available": False}}
















def _build_price_chart(ticker: str) -> Dict[str, object]:
    try:
        history = alpaca_data.get_bars_single(ticker, period="3mo", interval="1d")
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






CHAIN_CACHE: Dict[str, Dict[str, object]] = {}
CHAIN_CACHE_SECONDS = 20












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






# How many of the most recent research_log records to show/sample on the
# Agent Map page - recent enough to be meaningful "is this actually
# running right now" evidence, not so many the page is slow to render.
AGENT_MAP_RECENT_RECORDS = 25

# A node on the Agent Map's radial diagram counts as "active" (pulsing)
# if the signal it depends on is newer than this. The autonomous scan's
# own cadence is ~5 minutes (see settings_payload.scanner_frequency_seconds,
# default 20s poll but a real candidate re-evaluation cycle is minutes,
# not seconds) - 30 minutes gives real headroom for a slow tick or a
# markets-closed gap without the map falsely reading "dead".
AGENT_MAP_ACTIVITY_WINDOW_MINUTES = 30
# Calibration updates on a much slower cadence (every 10 closed trades, or
# a manual admin trigger) - a separate, longer window so it doesn't read
# as "idle" between real recalibrations that are working exactly as designed.
AGENT_MAP_CALIBRATION_WINDOW_HOURS = 48


def _agent_map_is_recent(timestamp: str, window: timedelta) -> bool:
    if not timestamp:
        return False
    try:
        parsed = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (_now_utc() - parsed) <= window


def _agent_map_feed_description(record: Dict[str, object]) -> str:
    """A short, human-readable line for the Pulse Feed - reuses
    reason_skipped, which every skip site in _run_autonomous_trade_scan_locked
    already writes as a real sentence (e.g. "confidence 20 below 55
    threshold"), rather than re-deriving new copy from skip_category."""
    if record.get("decision") == "placed":
        quantity = record.get("quantity")
        return f"Entry placed{f' — {quantity} shares/contracts' if quantity else ''}"
    reason = str(record.get("reason_skipped") or "").strip()
    if reason:
        return reason if len(reason) <= 110 else reason[:107] + "..."
    return "Evaluated, no further detail recorded"


def _build_agent_map_data(user_id: str, context: Dict[str, object]) -> Dict[str, object]:
    """Everything the 2D page, the 3D page and /api/agent-map show, computed once from this
    account's own recorded history (never live market data). context supplies autonomy_status
    (for validated_brains)."""
    # Newest first - list_research_decisions returns oldest-first append order.
    recent_records = list(reversed(list_research_decisions(user_id)))[:AGENT_MAP_RECENT_RECORDS]
    pulse_feed = []
    signal_snapshot_populated_count = 0
    llm_touched_recently = False
    option_touched_recently = False
    for record in recent_records:
        snapshot = record.get("signal_snapshot")
        market_context = snapshot.get("market_context") if isinstance(snapshot, dict) else None
        has_memory = isinstance(market_context, dict) and bool(market_context)
        if has_memory:
            signal_snapshot_populated_count += 1
        skip_category = record.get("skip_category")
        if skip_category == "llm_veto":
            llm_touched_recently = True
        if skip_category in ("sizing_too_small", "unprotectable_levels") or record.get("decision") == "placed":
            option_touched_recently = True  # every candidate attempts the real-option path first, win or lose
        pulse_feed.append({
            "ticker": record.get("ticker") or "—",
            "description": _agent_map_feed_description(record),
            "logged_at": record.get("logged_at"),
            "is_placed": record.get("decision") == "placed",
            "has_signal_snapshot": has_memory,
        })

    calibration = get_calibration()
    real_stats = calibration.get("real_strategy_stats") or {}
    backtest_stats = calibration.get("strategy_stats") or {}
    calibration_rows = []
    for strategy_name in sorted(set(real_stats) | set(backtest_stats)):
        real = real_stats.get(strategy_name)
        backtest = backtest_stats.get(strategy_name)
        # Mirrors calibration_store.score_multiplier's own preference
        # order exactly - real-if-trusted, else backtest-if-trusted, else
        # neither - so this column never implies a source is "in effect"
        # when score_multiplier itself would have ignored it.
        if real and real.get("trusted"):
            active_source = "real"
        elif backtest and backtest.get("trusted"):
            active_source = "backtest"
        else:
            active_source = None
        calibration_rows.append({"strategy": strategy_name, "real": real, "backtest": backtest, "active_source": active_source})

    validated_brains = set(context.get("autonomy_status", {}).get("validated_brains") or [])
    brain_validation = [{"name": name, "validated": name in validated_brains} for name in SHADOW_ONLY_BRAINS]

    most_recent_logged_at = recent_records[0].get("logged_at") if recent_records else ""
    scan_is_active = _agent_map_is_recent(most_recent_logged_at, timedelta(minutes=AGENT_MAP_ACTIVITY_WINDOW_MINUTES))
    calibration_is_active = _agent_map_is_recent(
        calibration.get("real_outcomes_generated_at") or calibration.get("generated_at") or "",
        timedelta(hours=AGENT_MAP_CALIBRATION_WINDOW_HOURS),
    )

    # The radial diagram's nodes - every one maps to a real module traced in
    # docs/AGENT_ARCHITECTURE.md, grouped the same way that doc groups them
    # (feeds a live decision / shadow-mode by design / display-only
    # research). "active" drives the pulse animation; it is never invented -
    # each one is tied to a real signal computed above, and a shadow/
    # display-only node is ALWAYS static, because it structurally never
    # feeds signal_snapshot no matter how recently the scan ran (see
    # research_log.py's own docstring on why candle_brain/pattern_brain/
    # neural_engine aren't captured yet).
    pulse_nodes = [
        {"id": "strategy", "label": "Strategy Engine", "icon": "◆", "category": "live",
         "detail": "strategy_brain + charting_brain + extended_hours_brain", "active": scan_is_active},
        {"id": "scanner", "label": "Market Scanner", "icon": "⌁", "category": "live",
         "detail": "picks which tickers get deep-analyzed", "active": scan_is_active},
        {"id": "llm", "label": "LLM Veto", "icon": "✎", "category": "live",
         "detail": "optional - only if a Claude key is configured", "active": scan_is_active and llm_touched_recently},
        {"id": "options", "label": "Options Selector", "icon": "◈", "category": "live",
         "detail": "the only path that calls the broker for a real option", "active": scan_is_active and option_touched_recently},
        {"id": "pricecheck", "label": "Price Cross-Check", "icon": "⇄", "category": "live",
         "detail": "Alpaca vs. Webull's own quote, added 9/11", "active": scan_is_active},
        {"id": "calibration", "label": "Calibration", "icon": "⚖", "category": "live",
         "detail": "real outcomes preferred over backtest", "active": calibration_is_active},
        {"id": "outcomes", "label": "Outcomes Analysis", "icon": "⟐", "category": "support",
         "detail": "reporting only, computed on page load", "active": False},
        {"id": "regime", "label": "Regime (VIX)", "icon": "◐", "category": "shadow",
         "detail": "shadow-mode by design, never gates a trade", "active": False},
        {"id": "candlepattern", "label": "Candle + Pattern", "icon": "◌", "category": "shadow",
         "detail": "still yfinance-backed, not in signal_snapshot yet", "active": False},
        {"id": "neural", "label": "Neural Engine", "icon": "◎", "category": "shadow",
         "detail": "feeds the dashboard stat card only", "active": False},
        {"id": "optionsresearch", "label": "Options Brain", "icon": "⌬", "category": "shadow",
         "detail": "legacy research page, no execution", "active": False},
        {"id": "tradingview", "label": "TradingView", "icon": "⌖", "category": "shadow",
         "detail": "inbound webhook, logged only", "active": False},
    ]
    # Positions every node on an ellipse around the central Memory hub -
    # computed here rather than in the template because Jinja has no trig
    # of its own; center/radii chosen to fit the diagram's own viewBox
    # (see agent_map.html) with room for labels below each node.
    hub_x, hub_y, ellipse_rx, ellipse_ry = 460.0, 280.0, 230.0, 185.0
    node_count = len(pulse_nodes)
    for index, node in enumerate(pulse_nodes):
        angle = math.radians(-90 + (360.0 / node_count) * index)
        node["x"] = round(hub_x + ellipse_rx * math.cos(angle), 1)
        node["y"] = round(hub_y + ellipse_ry * math.sin(angle), 1)

    return {
        "memory_feed_total": len(recent_records),
        "memory_feed_populated": signal_snapshot_populated_count,
        "calibration_status": calibration.get("status", "never_run"),
        "calibration_rows": calibration_rows,
        "real_outcomes_generated_at": calibration.get("real_outcomes_generated_at") or "",
        "closed_trades_since_last_real_recalibration": int(calibration.get("closed_trades_since_last_real_recalibration", 0) or 0),
        "real_outcomes_recalibration_interval": REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL,
        "min_trades_to_trust": MIN_TRADES_TO_TRUST,
        "outcomes": build_outcomes_analysis(user_id),
        "lifecycle": build_lifecycle_summary(user_id),
        "brain_validation": brain_validation,
        "pulse_nodes": pulse_nodes,
        "pulse_feed": pulse_feed,
        "scan_is_active": scan_is_active,
        "most_recent_logged_at": most_recent_logged_at,
        "hub_x": hub_x,
        "hub_y": hub_y,
    }


















# Built for staged sandbox validation ("Stage 2: one tiny entry and
# zero-fill cancellation" - see conversation history) and left as a
# permanent, reusable, audited tool per explicit user request, rather
# than a one-off script - so it's deliberately narrow, not a general
# "place any order" capability. See api_webull_place_test_order's own
# docstring for the full safety-rail reasoning.
MANUAL_TEST_ORDER_MAX_QUANTITY = 5
MANUAL_TEST_ORDER_MIN_DISCOUNT_FROM_MARKET = 0.20






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






























TICKER_SEARCH_CACHE: Dict[str, Dict[str, object]] = {}
TICKER_SEARCH_CACHE_SECONDS = 300




























































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
            margin_account = webull_api.find_individual_margin_account(sandbox_accounts)
            if margin_account and margin_account.get("account_id") and margin_account.get("account_id") != cash_account.get("account_id"):
                try:
                    import daily_loss
                    margin_balance = webull_api.get_account_balance(
                        creds["app_key"], creds["app_secret"], margin_account["account_id"],
                    )
                    day_pnl = daily_loss.combined_day_pnl(balance, margin_balance)
                except Exception as margin_error:  # noqa: BLE001 - cash day P/L still shown; the scan does the same when margin balance fails
                    logger.warning("Margin day P/L was not included in the loss cap: %s", margin_error)
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
            for position in _positions_for_pass(creds, account_id)
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
            # Only this account's records, in the current environment: a
            # stop is never placed on one account for another's position.
            or (order.get("account_id") and order.get("account_id") != account_id)
            or not broker_env.in_current(order)
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


def _new_entry_halt_reason(user_id: str, day_pnl: float, current_balance: float, risk_settings: Dict[str, object]) -> Optional[str]:
    """Daily-loss limit or losing streak. Phones once per distinct reason.
    None means new entries are allowed by this check."""
    import kill_switch
    from autonomy.closed_trades import list_closed_trades

    import daily_loss
    daily_loss_limit_percent = float(risk_settings.get("daily_loss_limit_percent", 0) or 0)
    state = daily_loss.evaluate(day_pnl, current_balance, daily_loss_limit_percent)
    if state["halted"]:
        kill_switch.note_halt(user_id, state["message"])
        return state["message"]
    streak_limit = int(risk_settings.get("losing_streak_limit", 3) or 0)
    reason = kill_switch.streak_block_reason(list_closed_trades(user_id), streak_limit)
    if reason:
        kill_switch.note_halt(user_id, reason)
        return reason
    kill_switch.clear_halt(user_id)
    return None


def _is_daily_loss_limit_hit(day_pnl: float, current_balance: float, daily_loss_limit_percent: float) -> bool:
    """daily_loss_limit_percent <= 0 means the limit is disabled - never blocks.

    Same formula as the home loss-cap meter (daily_loss.evaluate)."""
    import daily_loss
    return bool(daily_loss.evaluate(day_pnl, current_balance, daily_loss_limit_percent)["halted"])


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
    extra_caps: Optional[Dict[str, Tuple[float, str]]] = None,
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
    # Portfolio limits (portfolio_limits.room_for): dollars of room left,
    # each with the reason to report if it binds.
    for name, (room, reason) in (extra_caps or {}).items():
        constraints[name] = _floor_shares(_to_decimal(max(0.0, room)), entry_price_dec)
        reasons_by_key[name] = reason

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
    extra_caps: Optional[Dict[str, Tuple[float, str]]] = None,
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
    # Portfolio limits: dollars of room left, measured in premium paid.
    for name, (room, reason) in (extra_caps or {}).items():
        constraints[name] = _floor_shares(_to_decimal(max(0.0, room)), cost_per_contract)
        reasons_by_key[name] = reason

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
        trailing_on = trailing_stop.configured_percent() is not None
        if not degraded and not trailing_on:
            continue

        current_price = float(position.get("last_price", 0) or 0)
        quantity = float(position.get("quantity", 0) or 0)
        if current_price <= 0 or quantity <= 0:
            continue

        choices = []
        if degraded:
            tightened_stop = _compute_tightened_stop(current_stop, current_price)
            if tightened_stop > current_stop:
                choices.append((
                    tightened_stop,
                    (
                        f"confidence dropped {entry_confidence}->{current_confidence}"
                        if current_recommendation == "CALL"
                        else f"recommendation flipped to {current_recommendation} (was {entry_confidence}% CALL at entry)"
                    ),
                ))
        if str(tracked_entry.get("side") or "BUY").upper() == "BUY":
            trailed = trailing_stop.maybe_tighten(current_stop, current_price, "long")
            if trailed is not None:
                choices.append((trailed, "trailing stop"))
        if not choices:
            continue
        tightened_stop, refresh_reason = max(choices, key=lambda item: item[0])

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
            tracked_entry["stop_refresh_reason"] = refresh_reason
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


# The 5-minute scan is the only thing that places new entries, so losing a
# whole tick to a lock collision costs real opportunities. Seen live
# 2026-09-29: two of four consecutive scans were "skipped - a concurrent
# scan was already running" because a ~10s continuous-monitor tick happened
# to hold the account lock when the cron fired. A tick holds the lock for a
# second or two, so a short wait almost always succeeds; the monitor's own
# skip-on-collision stays as it is (it runs again 10s later regardless).
CRON_SCAN_LOCK_RETRIES = 30
CRON_SCAN_LOCK_RETRY_SECONDS = 0.5  # 30 x 0.5s = up to 15s per user


def _run_autonomous_trade_scan_waiting_for_lock(user_id: str) -> Dict[str, object]:
    """_run_autonomous_trade_scan, retried briefly while another pass holds
    this account's lock. Re-raises ScanAlreadyRunningError once the retries
    are exhausted, so the caller records the skip exactly as before."""
    for attempt in range(CRON_SCAN_LOCK_RETRIES + 1):
        try:
            return _run_autonomous_trade_scan(user_id)
        except ScanAlreadyRunningError:
            if attempt >= CRON_SCAN_LOCK_RETRIES:
                raise
            time.sleep(CRON_SCAN_LOCK_RETRY_SECONDS)
    raise ScanAlreadyRunningError(f"A scan is already running for user {user_id} - skipping this trigger.")  # unreachable


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
    if any(ol.is_transitional(order) for order in broker_env.only_current(list_overnight_orders(user_id))):
        return True
    if webull_tracked_tickers(user_id):
        return True
    if find_incomplete_resolutions(user_id):
        return True
    return False


def _timed(stages: Dict[str, float], name: str, fn, *args):
    """Runs fn(*args) and records its wall time under `name` in `stages` (diagnostics only)."""
    started = time.perf_counter()
    try:
        return fn(*args)
    finally:
        stages[name] = time.perf_counter() - started


FAST_MONITOR_SLOW_TICK_SECONDS = 10.0


def _log_fast_monitor_timing(user_id: str, started: float, stages: Dict[str, float], webull_stats) -> None:
    """One line per tick: where the time went. WARNING when a tick is slow enough that the
    worker's ~30s request timeout is at risk. Never raises - diagnostics must not affect the tick."""
    try:
        total = time.perf_counter() - started
        parts = " ".join(f"{name}={seconds:.2f}s" for name, seconds in stages.items())
        stats = webull_stats or {"calls": 0, "seconds": 0.0, "rate_limited": 0, "backoff_seconds": 0.0}
        message = (
            "FAST_MONITOR_TIMING user=%s total=%.2fs %s webull_calls=%d webull_seconds=%.2fs "
            "webull_429s=%d backoff_sleep=%.1fs"
        )
        args = (user_id[:8], total, parts, stats["calls"], stats["seconds"], stats["rate_limited"], stats["backoff_seconds"])
        (logger.warning if total >= FAST_MONITOR_SLOW_TICK_SECONDS else logger.info)(message, *args)
    except Exception:  # noqa: BLE001
        pass


def _review_naked_equity_positions(user_id: str, creds: Dict[str, str], account_id: str) -> List[Dict[str, object]]:
    """Every 10-second monitor tick: any open equity position with no working stop.

    Attaches a stop only when a price is already known and still below the
    last trade — the record's own stop, or the newest unfilled STOP_LOSS
    from order history (the cancelled sandbox stops on INTC, MSTR, and
    MRVL). Never invents a price. Outside CORE hours it flags the position
    and waits, because Webull rejects STOP_LOSS outside that session.
    Shorts are flagged and left alone; their position sign is unconfirmed.
    """
    import protective_stops

    try:
        positions = _positions_for_pass(creds, account_id)
        open_orders = webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id)
    except Exception as error:  # noqa: BLE001 - a positions read failing must not fail the rest of the tick
        return [{"action": "review_failed", "reason": str(error), "account_id": account_id}]

    protected = protective_stops.symbols_with_working_stop(open_orders)
    orders = list_overnight_orders(user_id)
    session = _current_webull_trading_session()
    report: List[Dict[str, object]] = []
    changed = False
    history_stops: Optional[Dict[str, float]] = None

    def _history() -> Dict[str, float]:
        nonlocal history_stops
        if history_stops is None:
            history_stops = protective_stops.historical_stops_for_account(
                account_id,
                lambda: webull_api.get_order_history(creds["app_key"], creds["app_secret"], account_id, days_back=30),
            )
        return history_stops

    for position in positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        quantity = protective_stops.position_quantity(position)
        if not symbol or quantity <= 0 or not protective_stops.is_equity_symbol(symbol):
            continue
        if symbol in protected:
            continue
        record = _naked_review_record(orders, symbol, account_id)
        if record is not None and record.get("stop_client_order_id"):
            continue
        last_price = protective_stops.position_last_price(position)
        item: Dict[str, object] = {
            "ticker": symbol,
            "quantity": quantity,
            "account_id": account_id,
            "last_price": last_price,
            "action": "flagged",
        }
        if record is None:
            item["reason"] = "broker position has no local record, so a stop will not be placed without one"
            report.append(item)
            continue
        if record.get("position_absent_unexplained"):
            item["reason"] = "position is flagged absent; not attaching a stop"
            record["naked_position"] = True
            record["naked_position_reason"] = item["reason"]
            record["naked_position_checked_at"] = _now_utc().isoformat()
            changed = True
            report.append(item)
            continue
        if record.get("direction") == "short":
            item["reason"] = "short positions are not auto-protected"
            record["naked_position"] = True
            record["naked_position_reason"] = item["reason"]
            record["naked_position_checked_at"] = _now_utc().isoformat()
            changed = True
            report.append(item)
            continue

        stored_stop = float(record.get("stop") or 0)
        source = ""
        stop_price = 0.0
        if stored_stop > 0:
            source = "record"
            stop_price = stored_stop
        else:
            recovered = _history().get(symbol)
            if recovered:
                source = "order_history"
                stop_price = float(recovered)

        if stop_price <= 0:
            item["reason"] = "no stop price on the record and none in recent unfilled stop orders"
        elif not protective_stops.stop_is_below_market(stop_price, last_price):
            item["reason"] = f"{source} stop ${stop_price:.2f} is not below the last price; not attaching it"
        elif session != "CORE":
            item["reason"] = f"will attach ${stop_price:.2f} ({source}) on the next CORE session"
            item["stop_price"] = stop_price
        else:
            trading_day = str(record.get("trading_day") or _trading_day_key())
            attempt = int(record.get("naked_stop_attempt") or 0) + 1
            client_order_id = ol.deterministic_client_order_id(user_id, symbol, trading_day, "naked_stop", attempt=attempt)
            try:
                webull_api.place_stop_loss_order(
                    app_key=creds["app_key"],
                    app_secret=creds["app_secret"],
                    account_id=account_id,
                    symbol=symbol,
                    quantity=quantity,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            except Exception as error:  # noqa: BLE001
                item["action"] = "attach_failed"
                item["reason"] = str(error)
                record["naked_position"] = True
                record["naked_position_reason"] = f"stop attach failed: {error}"
                record["naked_position_checked_at"] = _now_utc().isoformat()
                record["naked_stop_attempt"] = attempt
                changed = True
                report.append(item)
                continue
            record["stop"] = stop_price
            record["stop_client_order_id"] = client_order_id
            record["stop_leg_quantity"] = quantity
            record["filled_quantity"] = float(record.get("filled_quantity") or quantity)
            record["naked_stop_attempt"] = attempt
            record["naked_position"] = False
            record["naked_position_reason"] = ""
            record["naked_stop_attached_at"] = _now_utc().isoformat()
            record["stop_rearm_reason"] = f"attached {source} stop at ${stop_price:.2f} for {quantity:g} shares with no working stop"
            item["action"] = "attached"
            item["reason"] = record["stop_rearm_reason"]
            item["stop_price"] = stop_price
            changed = True
            try:
                add_manual_alert(
                    user_id,
                    {
                        "type": "stop_rearmed",
                        "ticker": symbol,
                        "priority": "critical",
                        "message": (
                            f"{symbol}: no working protective stop was on the {quantity:g} shares held. "
                            f"A stop at ${stop_price:.2f} from the {source} was placed. Confirm it on the position."
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            report.append(item)
            continue

        record["naked_position"] = True
        record["naked_position_reason"] = str(item["reason"])
        record["naked_position_checked_at"] = _now_utc().isoformat()
        changed = True
        report.append(item)

    if changed:
        replace_overnight_orders(user_id, orders)
    return report


def _naked_review_record(orders: List[Dict[str, object]], ticker: str, account_id: str) -> Optional[Dict[str, object]]:
    matches = []
    for order in orders:
        if str(order.get("ticker") or "").upper() != ticker:
            continue
        if order.get("instrument_type") == "OPTION":
            continue
        if order.get("lifecycle_state") in ol.TERMINAL_STATES:
            continue
        if order.get("account_id") and order.get("account_id") != account_id:
            continue
        matches.append(order)
    if not matches:
        return None
    matches.sort(key=lambda order: (float(order.get("stop") or 0) > 0, str(order.get("logged_at") or "")))
    return matches[-1]


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
    started = time.perf_counter()
    stages: Dict[str, float] = {}
    webull_api.start_call_stats()
    try:
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

        stages["setup"] = time.perf_counter() - started
        with user_scan_lock(user_id), _monitor_pass_snapshot_cache():
            _timed(stages, "orphan_discovery", _discover_orphaned_broker_entries, user_id, creds, account_id)
            _timed(stages, "exit_orders", _reconcile_exit_orders, user_id, creds, account_id)
            has_unresolved_ambiguous_submission = _timed(stages, "unknown_submissions", _reconcile_unknown_submissions, user_id, creds, account_id)
            has_incomplete_manual_resolution = _timed(stages, "manual_resolutions", _recover_incomplete_manual_resolutions, user_id, creds, account_id)
            still_transitional = _timed(stages, "transitional_orders", _monitor_transitional_orders, user_id, creds, account_id)
            # THE primary safety loop for a margin/short entry - see
            # _monitor_transitional_orders' own account-filtering docstring.
            # Deliberately narrower than the cash path above (orphan
            # discovery, ambiguous-submission recovery, and the outside-hours
            # stop retry are not yet extended to the margin account) - a
            # known, documented gap, not an oversight; see
            # _run_autonomous_trade_scan_locked's matching comment.
            if margin_account_id:
                still_transitional = _timed(stages, "transitional_margin", _monitor_transitional_orders, user_id, creds, margin_account_id, False) or still_transitional
            naked_positions = _timed(stages, "naked_positions", _review_naked_equity_positions, user_id, creds, account_id)
            if margin_account_id:
                naked_positions = list(naked_positions or []) + list(
                    _timed(stages, "naked_positions_margin", _review_naked_equity_positions, user_id, creds, margin_account_id) or []
                )

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
            "naked_positions": naked_positions,
        }
    finally:
        _log_fast_monitor_timing(user_id, started, stages, webull_api.take_call_stats())


def _persist_entry_before_submission(user_id: str, account_id: str, entry: Dict[str, object]) -> bool:
    """Write-ahead: durably records an entry (planned stop/target included)
    BEFORE its order goes to the broker. Returns False - after marking the
    entry ENTRY_FAILED - if the record can't be written, so the caller never
    sends an order it has no local trace of.

    Found live 2026-09-29: every entry used to be recorded only AFTER
    submission plus the fill/protection polling that follows it (tens of
    seconds). A worker that died in that window (deploy restart, OOM,
    instance restart) left a real filled position with no local record.
    Orphan discovery later re-imported each one with stop=0/target=0 -
    never invented, so never protectable - and they sat in
    PROTECTION_FAILED holding every position slot, blocking all new
    entries (INTC, MSTR, MRVL: 132 qualifying candidates in two days, zero
    placed). With the record on disk first, a crash leaves an ordinary
    ENTRY_SUBMITTED record the monitor resumes with the real planned levels;
    the deterministic client_order_ids make that resumption idempotent."""
    entry.setdefault("record_id", uuid.uuid4().hex)
    entry.setdefault("account_id", account_id)
    try:
        record_overnight_order(user_id, entry)
    except Exception as error:  # noqa: BLE001 - any failure to persist must block submission
        ol.transition(
            entry, ol.ENTRY_FAILED,
            error=f"could not durably record this entry before submission - refusing to submit: {error}",
        )
        return False
    return True


def _ledger_bars_for(ticker: str) -> List[Dict[str, object]]:
    """Daily bars for grading a suggestion. Failures stay empty so a scan never blocks on them."""
    frame = alpaca_data.get_bars_single(ticker, "1mo", "1d")
    if frame is None or getattr(frame, "empty", True):
        return []
    rows = []
    for idx, bar in frame.iterrows():
        stamp = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        rows.append({
            "date": stamp,
            "open": float(bar["Open"]),
            "high": float(bar["High"]),
            "low": float(bar["Low"]),
            "close": float(bar["Close"]),
        })
    return rows


def _execute_confirmed_entry(body: Dict[str, object]) -> str:
    """Enter from a signed phone button. Uses the same protected order path as the scan."""
    user_id = str(body.get("uid") or "")
    if not user_id:
        raise ValidationError("This confirm link has no account.")
    try:
        import go_live_gate
        go_live_gate.bind(user_id)
        go_live_gate.refuse_if_not_ready()
    except Exception as error:
        if error.__class__.__name__ == "GoLiveNotReady":
            raise ValidationError(str(error)) from error
        if error.__class__.__name__ == "ValidationError":
            raise
    if _new_entries_disabled_by_deployment_kill_switch():
        raise ValidationError("New entries are disabled.")
    if not _new_entries_allowed(_current_webull_trading_session()):
        raise ValidationError("Outside CORE hours, so a protected entry cannot be placed.")
    if get_autonomy_status(user_id).get("emergency_stop_enabled"):
        raise ValidationError("Emergency stop is on.")
    ticker = str(body.get("sym") or "").upper()
    direction = "short" if body.get("dir") == "short" else "long"
    blocked = evidence_gates.block_reason({
        "strategy": body.get("strat"),
        "recommendation": "PUT" if direction == "short" else "CALL",
        "confidence": body.get("conf"),
        "market_context": {},
    })
    if blocked:
        why, category = blocked
        suggestion_ledger.update(str(body.get("sid") or ""), decision="skipped", skip_category=category, reason=why)
        raise ValidationError(why)
    if not is_webull_configured(user_id):
        raise ValidationError("Webull is not configured for this account.")
    creds = get_webull_credentials(user_id)
    paper_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    allowed = set()
    for finder in (webull_api.find_individual_cash_account, webull_api.find_individual_margin_account):
        found = finder(paper_accounts)
        if found and found.get("account_id"):
            allowed.add(str(found["account_id"]))
    account_id = str(body.get("acct") or "")
    if account_id not in allowed:
        raise ValidationError("This confirm link is not for an account on this login.")
    quantity = int(float(body.get("qty") or 0))
    limit_price = float(body.get("lim") or 0)
    stop_price = float(body.get("stop") or 0)
    target_price = float(body.get("tgt") or 0)
    if quantity < 1 or limit_price <= 0 or stop_price <= 0 or target_price <= 0:
        raise ValidationError("This confirm link is missing a size or a protective price.")
    trading_day = str(body.get("day") or _trading_day_key())
    entry: Dict[str, object] = {
        "ticker": ticker,
        "direction": direction,
        "side": "SELL" if direction == "short" else "BUY",
        "quantity": quantity,
        "limit_price": limit_price,
        "confidence": body.get("conf"),
        "strategy": body.get("strat"),
        "target": target_price,
        "stop": stop_price,
        "account_id": account_id,
        "status": "pending",
        "trading_day": trading_day,
        "instrument_type": "EQUITY",
        "correlation_id": event_journal.new_correlation_id(),
        "planned_risk_dollars": round(quantity * abs(limit_price - stop_price), 2),
    }
    _submit_and_protect_entry(
        user_id=user_id,
        creds=creds,
        account_id=account_id,
        ticker=ticker,
        requested_quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=target_price,
        trading_day=trading_day,
        entry=entry,
    )
    lifecycle_state = entry.get("lifecycle_state")
    if lifecycle_state == ol.ENTRY_FAILED:
        entry["status"] = "failed"
        record_overnight_order(user_id, entry)
        suggestion_ledger.update(
            str(body.get("sid") or ""),
            decision="skipped",
            skip_category="entry_failed",
            reason=str(entry.get("error") or "entry order failed"),
        )
        raise ValidationError(str(entry.get("error") or "The order did not go through."))
    entry["status"] = "unknown_submission_state" if lifecycle_state == ol.UNKNOWN_SUBMISSION_STATE else "placed"
    record_overnight_order(user_id, entry)
    suggestion_ledger.update(
        str(body.get("sid") or ""),
        decision="taken" if entry["status"] == "placed" else "skipped",
        skip_category=None if entry["status"] == "placed" else "unknown_submission_state",
        reason=None if entry["status"] == "placed" else "order submission result could not be confirmed",
    )
    if entry["status"] != "placed":
        return f"{ticker}: the broker response was ambiguous, so the order is being reconciled. It was not treated as a clean fill."
    return f"Entered {ticker}. Stop ${stop_price:.2f}, target ${target_price:.2f}."


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
    if not _persist_entry_before_submission(user_id, account_id, entry):
        return entry

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


BROKER_FEE_LOOKBACK_DAYS = 30


def _apply_broker_fees(creds: Dict[str, str], account_id: str, closed_record: Dict[str, object]) -> None:
    """Fees from the broker's own order history (entry + the exit that
    filled) - see broker_fees.py. Unknown stays unknown: if either order
    can't be found or read, fees stay None and net P&L is marked as
    excluding fees."""
    order_ids = [closed_record.get("entry_client_order_id")]
    exit_type = str(closed_record.get("exit_type") or "")
    exit_id = (closed_record.get("exit_client_order_id") or
               (closed_record.get("stop_client_order_id") if exit_type == "stop" else closed_record.get("target_client_order_id")))
    order_ids.append(exit_id)
    # Order HISTORY carries fees/commission; the order-detail endpoint does
    # not (checked against the sandbox 2026-09-30).
    wanted = [str(i) for i in order_ids if i]
    try:
        history = {str(o.get("client_order_id")): o for o in
                   webull_api.get_order_history(creds["app_key"], creds["app_secret"], account_id, days_back=BROKER_FEE_LOOKBACK_DAYS)}
        per_order = [broker_fees.from_order(history[i]) if i in history else None for i in wanted]
    except Exception:  # noqa: BLE001 - unknown, never guessed
        per_order = [None]
    fees = broker_fees.combine(per_order) if len(wanted) == 2 else None
    if fees is None:
        closed_record["fees"] = None
        closed_record["fees_source"] = "unavailable"
        closed_record["net_excludes_fees"] = True
    else:
        closed_record["fees"] = fees["total"]
        closed_record["fee_items"] = fees["items"]
        closed_record["fees_source"] = "broker order history"
        gross = closed_record.get("gross_realized_pnl")
        if gross is not None:
            closed_record["net_realized_pnl"] = round(float(gross) - fees["total"], 4)
        closed_record["net_excludes_fees"] = False
    try:
        import paper_costs
        paper_costs.stamp_closed_record(closed_record)
    except Exception:
        pass


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
            opens_position=False,  # target exit reduces risk - never blocked by live dollar caps
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
    _apply_broker_fees(creds, account_id, closed_record)
    record_closed_trade(user_id, trade_id, closed_record)
    maybe_trigger_real_outcomes_recalibration()
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
    if entry.get("direction") == "short":
        return False

    stop_client_order_id = entry.get("stop_client_order_id")
    if not stop_client_order_id:
        # A known stop price with no order id is the same naked position as
        # a DAY stop that expired, except there is nothing to look up.
        # Inventing a price is still refused: stop <= 0 returns here.
        if float(entry.get("stop") or 0) <= 0:
            return False
        stop_fill = {"status": "MISSING", "filled_quantity": 0}
    else:
        try:
            stop_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, stop_client_order_id)
            stop_fill = ol.summarize_fill(stop_detail)
        except Exception:  # noqa: BLE001 - transient lookup failure, try again next tick
            return False

        if stop_fill["filled_quantity"] > 0 or _protective_leg_is_active(stop_fill["status"]):
            return False  # a genuine fill (real exit, handled elsewhere) or still genuinely resting - nothing to rearm

    try:
        positions = _positions_for_pass(creds, account_id)
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
        _apply_broker_fees(creds, account_id, closed_record)
        record_closed_trade(user_id, trade_id, closed_record)
        maybe_trigger_real_outcomes_recalibration()
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
    if not _persist_entry_before_submission(user_id, account_id, entry):
        return entry
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


def _settle_option_exit_order(
    user_id: str,
    creds: Dict[str, str],
    account_id: str,
    ticker: str,
    entry: Dict[str, object],
    *,
    sell_client_order_id: str,
    close_reason: str,
    contracts: float,
    premium_paid: float,
    option_symbol: object,
    strike: object,
    expiration_date: object,
    option_type: object,
) -> bool:
    """Poll one option exit sell. A still-working order returns False and
    keeps option_exit_client_order_id so the next tick polls the same
    order instead of placing another. A cancelled sell with zero fill
    clears the id so the next tick may place a new attempt. Returns True
    only once the sell is filled and the trade is recorded closed."""
    filled_this_sell = 0.0
    average_exit_price: Optional[float] = None
    status = ""
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        if attempt > 0:
            time.sleep(ENTRY_FILL_POLL_INTERVAL_SECONDS)
        try:
            sell_detail = webull_api.get_order_detail(creds["app_key"], creds["app_secret"], account_id, sell_client_order_id)
            sell_fill = ol.summarize_fill(sell_detail)
        except Exception:  # noqa: BLE001 - transient lookup failure, try again next tick
            continue
        filled_this_sell = sell_fill["filled_quantity"]
        average_exit_price = sell_fill.get("average_price")
        status = sell_fill["status"]
        if filled_this_sell >= contracts or _entry_fill_is_final(status):
            break

    if not (filled_this_sell >= contracts or (filled_this_sell > 0 and _entry_fill_is_final(status))):
        if _entry_fill_is_final(status) and filled_this_sell <= 0:
            entry["option_exit_client_order_id"] = None
        return False

    trade_id = str(entry.get("entry_client_order_id") or "")
    pnl_complete = average_exit_price is not None and not entry.get("premium_paid_is_estimated")
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
    _apply_broker_fees(creds, account_id, closed_record)
    record_closed_trade(user_id, trade_id, closed_record)
    maybe_trigger_real_outcomes_recalibration()
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason=close_reason)
    entry["option_exit_client_order_id"] = None
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

    pending_id = str(entry.get("option_exit_client_order_id") or "")
    if pending_id:
        # A previous tick already sent the sell. Poll that order. Do not
        # place another one while it is still working.
        return _settle_option_exit_order(
            user_id, creds, account_id, ticker, entry,
            sell_client_order_id=pending_id,
            close_reason=str(entry.get("option_exit_reason") or "option_exit"),
            contracts=contracts, premium_paid=premium_paid, option_symbol=option_symbol,
            strike=strike, expiration_date=expiration_date, option_type=option_type,
        )

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
    stop_loss_pct = float(autonomy_settings.get("option_stop_loss_percent") or 20.0) / 100.0
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
    entry["option_exit_reason"] = close_reason
    sell_client_order_id = ol.deterministic_client_order_id(user_id, ticker, trading_day, "option_exit", attempt=next_attempt)
    # Stored before the broker call so a crash or an unfilled sell resumes
    # on this id instead of placing a second SELL (which Webull rejects as
    # a reverse position, and which used to stack a new order every tick).
    entry["option_exit_client_order_id"] = sell_client_order_id
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
            opens_position=False,  # option exit reduces risk - never blocked by live dollar caps
        )
    except webull_api.AmbiguousOrderSubmission:
        return False  # the id is already stored; the next tick polls it
    except Exception as error:  # noqa: BLE001 - definite failure: nothing is resting, so the next tick may try a new attempt
        entry["option_exit_client_order_id"] = None
        raise RuntimeError(f"option exit sell order failed for {ticker} ({close_reason}): {error} - will retry next pass")

    return _settle_option_exit_order(
        user_id, creds, account_id, ticker, entry,
        sell_client_order_id=sell_client_order_id,
        close_reason=close_reason,
        contracts=contracts, premium_paid=premium_paid, option_symbol=option_symbol,
        strike=strike, expiration_date=expiration_date, option_type=option_type,
    )


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


# See _discover_orphaned_broker_entries's own comment at the guard site
# for the real 2026-09-04 incident this exists to catch: many long-tracked
# tickers going "unknown locally" in a single pass is not genuine crash
# recovery, it's a signal overnight_orders.json may have been transiently
# or actually reset. A LOW number (1-3) is the ordinary, expected case
# this function exists for (one crashed placement) and is unaffected.
ORPHAN_BATCH_ANOMALY_THRESHOLD = 3


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
    pass (0 if none, if the history lookup itself failed, or if the
    ORPHAN_BATCH_ANOMALY_THRESHOLD guard below refused to write a
    suspiciously large batch - the caller treats all three the same as
    "nothing found", matching every other reconciliation function's
    best-effort posture)."""
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

    # First pass: identify every STRONGLY-attributed row not already known
    # locally, without writing anything yet - see the batch-size guard
    # right below for why this can't just stream-write as it finds them
    # the way it used to.
    orphan_rows = []
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

        orphan_rows.append((client_order_id, ticker, row))

    # Found live 2026-09-04: a real incident where overnight_orders.json's
    # entire history (dozens of entries, spanning back through the prior
    # trading day) appeared to vanish between two ordinary ticks, and this
    # function then "discovered" SIX previously-well-tracked tickers as
    # orphans in one single pass - each one had a full lifecycle_history
    # this app itself had been actively reading and writing for hours.
    # That pattern - many long-tracked entries ALL going "unknown" in one
    # shot - cannot be genuine restart-recovery (a real crash orphans
    # entries one at a time, as they're placed; it does not retroactively
    # orphan an entry this app had already been successfully monitoring
    # for hours). It IS exactly consistent with list_overnight_orders
    # transiently reading an empty/reset file - the same "brief window
    # where the persistent disk isn't yet mounted when a request lands"
    # class of incident account_hub.py's _looks_like_an_established_user_dir
    # was already built to guard against for accounts.json, never ported
    # here. Writing "recovered" orphans in that state would PERMANENTLY
    # replace real history with a much smaller rediscovered stub (record_overnight_order's
    # _atomic_write does not merge, it replaces the file's content on
    # disk). A handful of genuine orphans (the crash-recovery case this
    # function exists for) is normal and still written exactly as before;
    # a sudden batch is the anomaly this guards against.
    if len(orphan_rows) > ORPHAN_BATCH_ANOMALY_THRESHOLD:
        try:
            add_manual_alert(
                user_id,
                {
                    "type": "orphan_discovery_batch_anomaly",
                    "priority": "critical",
                    "message": (
                        f"Order-history reconciliation found {len(orphan_rows)} tickers "
                        f"({', '.join(sorted({ticker for _, ticker, _ in orphan_rows}))}) simultaneously "
                        "\"unknown locally\" in one pass - too many at once to be genuine crash recovery, and "
                        "consistent with overnight_orders.json having been transiently or actually reset. "
                        "Refusing to write these as fresh orphan records (that would permanently replace any "
                        "real history still on disk with this much smaller rediscovered list) - this does NOT "
                        "on its own freeze new autonomous entries, since none of these rows were written to a "
                        "frozen state. Review overnight_orders.json directly and the Render disk/deploy history "
                        "as soon as possible."
                    ),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return 0

    for client_order_id, ticker, row in orphan_rows:
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
        # The account whose order history this orphan was found in. Without
        # it, the margin-account monitor pass also picked the record up (see
        # _monitor_transitional_orders' include_unstamped) and, finding no
        # such position in the margin account, flagged a held cash position
        # as absent.
        orphan["account_id"] = account_id
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
    pending = [
        order for order in orders
        if order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE and broker_env.in_current(order)
    ]
    if not pending:
        return False
    for order in pending:
        try:
            # The record's own account - an ambiguous margin-account order
            # looked up on the cash account would never be found there.
            _reconcile_unknown_submission(user_id, creds, str(order.get("account_id") or account_id), order)
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
                    account_id=str(entry.get("account_id") or account_id),
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

    # A request that has arrived but not yet completed is normally just IN
    # FLIGHT: a real tick takes ~30s+ in production (observed 2026-09-26 -
    # POST received 23:45:56, 200 returned 23:46:30) while the worker calls
    # again ~6s after each response, so most of the time a request is
    # mid-run. This branch is only reached while received_age_seconds is
    # under CONTINUOUS_MONITOR_HEARTBEAT_STALE_SECONDS, so an in-flight
    # request cannot be old enough here to be hung; a request that truly
    # hangs stops the worker's next call and trips the "worker has not
    # called this endpoint" check above, and a stalled completion trips the
    # completed_age check below. Flagging every in-flight request as
    # "never completed" (this branch's old behavior) made the health
    # endpoint, the admin readiness report, and any external uptime monitor
    # report unhealthy for most of every minute.

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


# A fill can take longer than a few seconds to appear in Webull's own
# positions list. Found live 2026-09-04 (MU): the position-absent check ran
# ~9 seconds after an orphan entry filled, the broker's positions endpoint
# hadn't caught up, the check read ZERO shares and flagged
# position_absent_unexplained (a sticky flag that then stops the monitor
# from ever protecting the entry) - the 1-share MU position was, and still
# is, genuinely held and unprotected. So an absent reading is not acted on
# until it has ALSO been seen absent on an earlier pass, at least this long
# ago - long enough that post-fill propagation lag cannot explain it.
POSITION_ABSENT_CONFIRM_SECONDS = 180


# One positions read per account per monitor pass. Found live 2026-09-29
# (FAST_MONITOR_TIMING): every PROTECTION_FAILED record made its own
# get_account_positions call - 8 identical reads per ~10s tick on top of the
# pass's other calls - and Webull answered a third of the ticks with 429s,
# each costing 1.5-3s of back-off sleep (ticks of 10-23s instead of ~1.3s).
# Active only inside _monitor_pass_snapshot_cache(); everywhere else the
# functions below read the broker fresh, exactly as before.
_monitor_pass_cache = threading.local()


@contextmanager
def _monitor_pass_snapshot_cache():
    _monitor_pass_cache.positions = {}
    try:
        yield
    finally:
        _monitor_pass_cache.positions = None


def _positions_for_pass(creds: Dict[str, str], account_id: str) -> List[Dict[str, object]]:
    """get_account_positions, read once per account while a monitor pass
    cache is active. Nothing in a pass changes a position within the same
    second - it only reads order state and places or cancels resting
    orders - and the next pass reads fresh anyway."""
    cache = getattr(_monitor_pass_cache, "positions", None)
    key = (creds.get("app_key", ""), account_id)
    if cache is not None and key in cache:
        return cache[key]
    positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    if cache is not None:
        cache[key] = positions
    return positions


def _position_quantity_at_broker(creds: Dict[str, str], account_id: str, ticker: str) -> Optional[float]:
    """Live share count the broker reports for `ticker` on `account_id`,
    or None when the lookup itself failed (network/broker error) - None
    means "could not verify", never "zero". Long-only sign convention (a
    held position reads positive), same as the callers that use it."""
    try:
        positions = _positions_for_pass(creds, account_id)
    except Exception:  # noqa: BLE001 - inconclusive, never treated as evidence either way
        return None
    return next(
        (float(position.get("quantity", 0) or 0) for position in positions if str(position.get("symbol", "")).upper() == ticker.upper()),
        0.0,
    )


def _clear_stale_position_absent_flag(user_id: str, ticker: str, entry: Dict[str, object]) -> None:
    """Self-heal: an entry flagged position_absent_unexplained whose shares
    the broker now confirms ARE held. The flag was wrong - almost always a
    positions-list lag right after the fill (see
    POSITION_ABSENT_CONFIRM_SECONDS). Leaving it set keeps the monitor from
    ever protecting a real, live position, which is the exact risk this
    whole subsystem exists to prevent - so clear it and let normal
    protection handling resume, with an alert so the correction is
    visible."""
    entry.pop("position_absent_unexplained", None)
    entry.pop("position_absent_evidence", None)
    entry.pop("position_absent_first_seen_at", None)
    entry["position_absent_flag_cleared_at"] = _now_utc().isoformat()
    entry["position_absent_flag_cleared_reason"] = (
        "broker positions list now shows the shares held - the earlier absent reading was wrong "
        "(most likely the positions list lagging the fill); resuming normal protection handling"
    )
    try:
        add_manual_alert(
            user_id,
            {
                "type": "position_absent_flag_cleared",
                "ticker": ticker,
                "priority": "normal",
                "message": (
                    f"{ticker}: an earlier 'position absent' flag on this entry has been cleared - the broker's "
                    "positions list now confirms the shares ARE held. The flag was most likely a positions-list "
                    "lag right after the fill. Normal protection handling resumes. If this entry never had usable "
                    "stop/target levels (an orphan-recovered entry), the app still cannot auto-protect it - "
                    "close the position or supply a stop manually at the broker."
                ),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _absence_corroborated_across_passes(entry: Dict[str, object]) -> bool:
    """True only once an absent reading has persisted: seen absent on a
    prior pass too, and first seen absent at least
    POSITION_ABSENT_CONFIRM_SECONDS ago. The first absent sighting only
    records a timestamp and returns False - a single read is never enough,
    matching this subsystem's own 'position absence alone is never
    sufficient evidence' discipline."""
    now = _now_utc()
    first_seen_raw = entry.get("position_absent_first_seen_at")
    if not first_seen_raw:
        entry["position_absent_first_seen_at"] = now.isoformat()
        return False
    try:
        first_seen = datetime.fromisoformat(str(first_seen_raw))
    except ValueError:
        first_seen = now
    return (now - first_seen).total_seconds() >= POSITION_ABSENT_CONFIRM_SECONDS


def _alert_unprotected_orphan_needs_action(user_id: str, order: Dict[str, object]) -> None:
    """One-shot (content-hash deduped, same pattern as
    _alert_if_entry_newly_stuck) alert for a recovered orphan the app can
    never auto-protect - it filled with no known stop/target, so it just
    sits at PROTECTION_FAILED. The app will not invent a stop; a human has
    to close the position or attach one at the broker. Fixed message per
    ticker so add_manual_alert's own dedup makes it fire once, not every
    monitor pass."""
    ticker = str(order.get("ticker", "?"))
    quantity = order.get("filled_quantity") or order.get("quantity") or "?"
    try:
        add_manual_alert(
            user_id,
            {
                "type": "unprotected_orphan_needs_action",
                "ticker": ticker,
                "priority": "critical",
                "message": (
                    f"{ticker}: {quantity} share(s) are held from a recovered orphan entry that never had a "
                    "stop/target, so this app cannot protect it automatically and will stop retrying. Close the "
                    "position at the broker (Trading Portfolio -> Close), or place a stop there yourself. This "
                    "alert will not repeat."
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

    live_quantity = _position_quantity_at_broker(creds, account_id, ticker)
    if live_quantity is None:
        # Could not verify this pass - keep whatever the entry already
        # concluded (a set flag stays set and short-circuits; an unflagged
        # entry stays unflagged), never turn an inconclusive read into
        # evidence either way.
        return bool(entry.get("position_absent_unexplained"))

    if live_quantity > 0:
        # Position IS held right now. If this entry was previously flagged
        # position_absent_unexplained, that flag was wrong - self-heal it
        # so the monitor resumes protecting the (real) position.
        if entry.get("position_absent_unexplained"):
            _clear_stale_position_absent_flag(user_id, ticker, entry)
        else:
            entry.pop("position_absent_first_seen_at", None)
        return False

    if entry.get("position_absent_unexplained"):
        return True  # already confirmed absent on an earlier pass, still absent

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
        entry.pop("position_absent_first_seen_at", None)
        return False

    if not _absence_corroborated_across_passes(entry):
        # Absent this pass, but not yet on a prior pass / not long enough
        # to rule out post-fill positions-list lag (see
        # POSITION_ABSENT_CONFIRM_SECONDS). Re-verify next pass rather than
        # flag now.
        return False

    entry["position_absent_unexplained"] = True
    entry["position_absent_evidence"] = {
        "checked_at": _now_utc().isoformat(),
        "first_seen_absent_at": entry.get("position_absent_first_seen_at"),
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

    live_quantity = _position_quantity_at_broker(creds, account_id, ticker)
    if live_quantity is None:
        return bool(entry.get("position_absent_unexplained"))

    if live_quantity > 0:
        if entry.get("position_absent_unexplained"):
            _clear_stale_position_absent_flag(user_id, ticker, entry)
        else:
            entry.pop("position_absent_first_seen_at", None)
        return False

    if entry.get("position_absent_unexplained"):
        return True  # already confirmed absent on an earlier pass, still absent

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
        entry.pop("position_absent_first_seen_at", None)
        return False

    if not _absence_corroborated_across_passes(entry):
        return False  # see the STUCK counterpart / POSITION_ABSENT_CONFIRM_SECONDS

    entry["position_absent_unexplained"] = True
    entry["position_absent_evidence"] = {
        "checked_at": _now_utc().isoformat(),
        "first_seen_absent_at": entry.get("position_absent_first_seen_at"),
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
    maybe_trigger_real_outcomes_recalibration()
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id, close_reason="manual_reconciliation_position_absent")
    replace_overnight_orders(target_user_id, orders)
    logger.warning(
        "Manually reconciled position_absent_unexplained entry: user_id=%s ticker=%s entry_client_order_id=%s admin=%s reason=%s",
        target_user_id, ticker, entry_client_order_id, admin_user_id, reason.strip(),
    )
    return {"entry": entry}


def _call_holding_user_scan_lock(user_id: str, fn, wait_seconds: float = 20.0):
    """Runs fn() holding user_scan_lock, retrying for up to wait_seconds
    while a scan or monitor pass holds it (the lock itself never blocks)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            with user_scan_lock(user_id):
                return fn()
        except ScanAlreadyRunningError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)




def _monitor_transitional_orders(user_id: str, creds: Dict[str, str], account_id: str, include_unstamped: bool = True) -> bool:
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
    (a legacy/test record from before this field was always stamped, or an
    orphan imported before 2026-09-29) is processed only when
    include_unstamped is True - the cash-account pass. The margin pass
    passes False: every unstamped record predates margin trading, and
    checking one against the margin account found "no such position" and
    flagged a held cash position as absent (seen live 2026-09-29 on INTC,
    MSTR and MRVL).

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
    # `orders` holds the SAME dict objects as `all_orders`, so the in-place
    # updates below land in `all_orders` too - and `all_orders` is what gets
    # written back. Until 2026-09-29 this wrote back the account-FILTERED
    # list, so each account's pass deleted every record belonging to the
    # other account: every cash-account entry vanished on the next margin
    # pass (seconds after placement), which is what reset this file on
    # 2026-09-04 and later orphaned MRVL, MSTR and INTC.
    all_orders = list_overnight_orders(user_id)
    orders = [
        order for order in all_orders
        if (order.get("account_id") == account_id or (include_unstamped and not order.get("account_id")))
        and broker_env.in_current(order)
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
            elif (
                state_before == ol.PROTECTION_FAILED
                and order.get("orphan_recovered")
                and order.get("entry_order_terminal")
                and float(order.get("stop") or 0) <= 0
            ):
                # A recovered orphan whose stop/target were never known
                # (see _resolve_orphan_recovered_entry): its unfilled
                # remainder was already cancelled, its critical unprotected-
                # position alert already fired, and it is already terminal.
                # _reconcile_entry_fill_and_protection can do nothing here -
                # its leg_price <= 0 guard rejects every attempt - so
                # calling it every monitor pass just churns PROTECTION_PENDING
                # -> PROTECTION_FAILED forever and floods the lifecycle
                # history and the alert drawer. Leave it for a human to
                # close or attach a stop to; re-surface once (deduped).
                _alert_unprotected_orphan_needs_action(user_id, order)
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
        replace_overnight_orders(user_id, all_orders)

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
    for order in broker_env.only_current(list_overnight_orders(user_id)):
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
    for order in broker_env.only_current(list_overnight_orders(user_id)):
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
    if any(order.get("lifecycle_state") in ol.FROZEN_STATES for order in broker_env.only_current(list_overnight_orders(user_id))):
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


def _remember_rows(bucket: List[Dict[str, object]], rows: object) -> object:
    """Returns rows unchanged after copying them into bucket (a list)."""
    if isinstance(rows, list):
        bucket.extend(row for row in rows if isinstance(row, dict))
    return rows


def _float_or_zero(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _trade_plan_for_candidate(
    *, ticker, opp, direction, entry_price, stop_price, target_price, quote_price, sizing,
    plan_equity, equity_source, existing_exposure, available_buying_power, short_permitted, evidence_returns,
    quote_deferred=False, portfolio_context=None,
) -> Dict[str, object]:
    """One trade plan for a scan candidate, from the scan's own inputs and
    sizing result (see trade_planner.py). quote_price is a price fetched
    moments ago (the pre-submission cross-check), so its age is ~0; None
    means no confirmed current price. Never raises - a planning failure
    becomes a plan that says so, never a scan failure."""
    strategy = opp.get("strategy")
    try:
        evidence = trade_planner.evidence_from_returns(
            evidence_returns.get(strategy) or [], strategy=strategy or "unknown", source="closed trades, all accounts",
        )
        plan = trade_planner.build_trade_plan(
            ticker=ticker, strategy=strategy, direction=direction, setup_score=opp.get("confidence"),
            entry_price=entry_price, stop_price=stop_price, target_price=target_price,
            quote_price=quote_price, quote_age_seconds=0.0 if quote_price is not None else None,
            plan_equity=plan_equity, equity_source=equity_source, existing_exposure=existing_exposure,
            available_buying_power=available_buying_power, sizing=sizing, short_permitted=short_permitted,
            evidence=evidence, quote_deferred=quote_deferred,
        )
        if portfolio_context:
            plan["portfolio"] = portfolio_context
        return plan
    except Exception as error:  # noqa: BLE001
        logger.warning("Trade plan failed for %s: %s", ticker, error)
        return {"planner_version": trade_planner.PLANNER_VERSION, "ticker": ticker, "decision": "reject", "reasons": [f"Plan could not be built: {error}"]}


OPTION_PREMIUM_DRIFT_TOLERANCE_PERCENT = 10.0


def _propose_trade_ticket(user_id: str, entry: Dict[str, object], opp: Dict[str, object]) -> Dict[str, object]:
    """Turns a scan candidate that reached submission into a trade ticket
    (APPROVAL mode). Marks `entry` awaiting_approval and stamps the ticket
    id/version on it. Nothing is sent to the broker here."""
    proposal = {
        "trading_day": entry.get("trading_day"),
        "ticker": entry.get("ticker"),
        "instrument_type": entry.get("instrument_type") or "EQUITY",
        "direction": entry.get("direction") or "long",
        "side": entry.get("side"),
        "quantity": entry.get("quantity"),
        "limit_price": entry.get("limit_price"),
        "stop": entry.get("stop"),
        "target": entry.get("target"),
        "account_id": entry.get("account_id"),
        "strategy": entry.get("strategy"),
        "confidence": entry.get("confidence"),
        "trade_quality": entry.get("trade_quality"),
        "trade_thesis": entry.get("trade_thesis"),
        "why_ai_likes_it": entry.get("why_ai_likes_it"),
        "invalidation_rule": entry.get("invalidation_rule"),
        "risk_warning": entry.get("risk_warning"),
        "recommendation": opp.get("recommendation"),
        "planned_risk_dollars": entry.get("planned_risk_dollars"),
        "premium_at_risk": entry.get("premium_at_risk"),
        "option_symbol": entry.get("option_symbol"),
        "strike": entry.get("strike"),
        "expiration_date": entry.get("expiration_date"),
        "option_type": entry.get("option_type"),
        "trade_plan": trade_planner.compact_plan(entry["trade_plan"]) if isinstance(entry.get("trade_plan"), dict) else None,
        "llm_verdict": entry.get("llm_verdict"),
        "llm_reasoning": entry.get("llm_reasoning"),
        "correlation_id": entry.get("correlation_id"),
        **broker_env.current(),
    }
    ticket, outcome = trade_tickets.create_or_refresh_ticket(user_id, proposal)
    # A refreshed ticket keeps the id it was created under, so every scan
    # that re-proposes the same terms joins the same chain.
    if ticket.get("correlation_id"):
        entry["correlation_id"] = ticket["correlation_id"]
    entry["status"] = "awaiting_approval"
    entry["skip_category"] = "awaiting_approval"
    entry["ticket_id"] = ticket.get("ticket_id")
    entry["ticket_version"] = ticket.get("version")
    entry["ticket_outcome"] = outcome
    if outcome in ("created", "superseded"):
        try:
            if proposal["instrument_type"] == "OPTION":
                terms = (f"{proposal['quantity']} x {proposal['ticker']} {proposal['option_type']} {proposal['strike']} "
                         f"exp {proposal['expiration_date']} at ${proposal['limit_price']}")
            else:
                terms = f"{proposal['quantity']} share(s) of {proposal['ticker']} at ${proposal['limit_price']}"
            add_manual_alert(
                user_id,
                {
                    "type": "trade_ticket_awaiting_approval",
                    "ticker": str(proposal["ticker"]),
                    "message": f"Trade ticket awaiting your approval: {terms}. Open Mission Control to approve or decline.",
                },
            )
        except Exception:  # noqa: BLE001 - alerting must never affect the scan
            pass
    return ticket


def _recheck_ticket_before_submission(user_id: str, creds: Dict[str, str], ticket: Dict[str, object]) -> Dict[str, object]:
    """Everything that could have changed between the scan and the click,
    re-read from the broker and the market now. Any failed check refuses the
    submission; the ticket then needs a fresh proposal (the next scan makes
    one if the setup still qualifies). Returns {"ok", "reasons", "fresh_price", "checked_at"}."""
    reasons: List[str] = []
    ticker = str(ticket.get("ticker", "")).upper()
    instrument_type = str(ticket.get("instrument_type") or "EQUITY")
    quantity = float(ticket.get("quantity") or 0)
    limit_price = float(ticket.get("limit_price") or 0)
    account_id = str(ticket.get("account_id") or "")
    fresh_price: Optional[float] = None

    if ticket.get("trading_day") != _trading_day_key():
        reasons.append("this ticket is from a previous trading day")
    if not broker_env.in_current(ticket):
        reasons.append(f"this ticket was proposed in the {broker_env.environment_of(ticket)[0]} environment, "
                       f"but orders now go to {broker_env.current()['environment']} - it cannot be submitted here")
    if _new_entries_disabled_by_deployment_kill_switch():
        reasons.append("new entries are disabled platform-wide (PLUTO_DISABLE_NEW_ENTRIES)")
    if not _new_entries_allowed(_current_webull_trading_session()):
        reasons.append("outside CORE trading hours - a broker-side stop cannot be attached until the market opens")
    risk_settings = get_autonomy_status(user_id)
    if risk_settings.get("emergency_stop_enabled"):
        reasons.append("the emergency stop is active")
    if _has_unresolved_ambiguous_submission_locally(user_id):
        reasons.append("a prior order submission is unresolved (ambiguous) - entries are frozen until it is resolved")
    if _has_stuck_transitional_orders_locally(user_id):
        reasons.append("an existing entry has made no progress for too long - entries are frozen until a human reviews it")
    if ticker in _tickers_with_open_positions(broker_env.only_current(list_overnight_orders(user_id))):
        reasons.append(f"{ticker} already has an open, unclosed position")
    if quantity < 1 or limit_price <= 0 or not account_id:
        reasons.append("the ticket is missing a valid quantity, price or account")
    if reasons:
        return {"ok": False, "reasons": reasons, "fresh_price": None, "checked_at": _now_utc().isoformat()}

    try:
        sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        all_positions: List[Dict[str, object]] = []
        all_working_orders: List[Dict[str, object]] = []
        for account in (webull_api.find_individual_cash_account(sandbox_accounts), webull_api.find_individual_margin_account(sandbox_accounts)):
            if account:
                all_positions.extend(webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account["account_id"]))
                all_working_orders.extend(webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account["account_id"]))
        open_position_count = len(all_positions)
        max_positions = int(risk_settings.get("max_positions", 0) or 0)
        if max_positions > 0 and open_position_count >= max_positions:
            reasons.append(f"max positions reached ({open_position_count}/{max_positions} open)")

        balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id)
        real_net_liquidation_value = float(balance.get("total_net_liquidation_value", 0) or 0)
        virtual_balance = get_virtual_net_account_value(user_id, real_net_liquidation_value)
        current_balance = virtual_balance if virtual_balance is not None else real_net_liquidation_value

        # Portfolio limits against fresh positions: the whole ticket must fit
        # (an approval never resizes the order the user approved).
        limits = portfolio_limits.effective_limits(risk_settings, live=webull_api.is_live_trading_armed())
        ticket_value = quantity * limit_price * (100 if instrument_type == "OPTION" else 1)
        if limits["max_position_percent"] and current_balance > 0 and ticket_value > current_balance * limits["max_position_percent"] / 100:
            reasons.append(f"this position would be {ticket_value / current_balance * 100:.1f}% of the account, over your {limits['max_position_percent']:g}% position limit")
        exposure = portfolio_limits.exposure_snapshot(all_positions, all_working_orders)
        for name, (room, reason) in portfolio_limits.room_for(ticker, current_balance, exposure, limits).items():
            if ticket_value > room:
                reasons.append(reason)
        if limits["max_drawdown_percent"]:
            source = "configured paper balance" if virtual_balance is not None else "broker net liquidation value"
            peak = portfolio_limits.record_equity_and_get_peak(user_id, current_balance, source)
            drawdown_reason = portfolio_limits.drawdown_block_reason(current_balance, float(peak.get("peak") or 0), limits["max_drawdown_percent"])
            if drawdown_reason:
                reasons.append(drawdown_reason)
        day_pnl = float(balance.get("total_day_profit_loss", 0) or 0)
        halt_reason = _new_entry_halt_reason(user_id, day_pnl, current_balance, risk_settings)
        if halt_reason:
            reasons.append(halt_reason)
        if instrument_type == "OPTION":
            needed = quantity * limit_price * 100
            buying_power = _extract_option_buying_power(balance)
        else:
            needed = quantity * limit_price
            buying_power = _extract_broker_buying_power(balance)
        if buying_power is None:
            reasons.append("could not read the account's buying power")
        elif buying_power < needed:
            reasons.append(f"buying power ${buying_power:,.2f} no longer covers this order (${needed:,.2f})")
    except Exception as error:  # noqa: BLE001 - a broker failure refuses the submission, never guesses
        reasons.append(f"broker re-check failed: {str(error)[:160]}")

    if instrument_type == "OPTION":
        try:
            rows = webull_api.get_option_snapshot(creds["app_key"], creds["app_secret"], [str(ticket.get("option_symbol"))])
            ask = _parse_option_float((rows[0] if rows else {}).get("ask"))
        except Exception as error:  # noqa: BLE001
            ask = None
            reasons.append(f"could not fetch a fresh option quote: {str(error)[:120]}")
        if ask is None or ask <= 0:
            if not any("option quote" in r for r in reasons):
                reasons.append("no fresh option ask available")
        else:
            fresh_price = ask
            drift_pct = abs(ask - limit_price) / limit_price * 100
            if drift_pct > OPTION_PREMIUM_DRIFT_TOLERANCE_PERCENT:
                reasons.append(f"the option premium moved {drift_pct:.1f}% since the ticket (${limit_price:.2f} -> ${ask:.2f})")
            # The exit is judged on the bid; buying at the ask when the bid is
            # already at the trigger would be sold on the first monitor pass.
            bid = _parse_option_float((rows[0] if rows else {}).get("bid"))
            trigger = limit_price * (1 - _option_exit_settings(risk_settings)["stop_loss_percent"] / 100)
            if bid is None or bid <= 0:
                reasons.append("no fresh option bid available - the exit rule could not be evaluated")
            elif bid <= trigger:
                reasons.append(f"the bid (${bid:.2f}) is at or below the exit trigger (${trigger:.2f}) - it would be sold immediately")
    else:
        try:
            cross_check = market_data_aggregator.get_cross_checked_price(ticker, creds)
        except Exception as error:  # noqa: BLE001
            cross_check = {"price": None, "disagreement": False, "provider_status": [], "error": str(error)}
        fresh_price = cross_check.get("price")
        if cross_check.get("disagreement"):
            reasons.append("real-time price sources disagree - refusing to submit against an unconfirmed price")
        elif fresh_price is None:
            reasons.append("could not confirm a fresh, real-time price")
        elif _price_has_drifted_too_far(limit_price, fresh_price):
            drift_pct = abs(fresh_price - limit_price) / limit_price * 100
            reasons.append(f"the price moved {drift_pct:.1f}% since the ticket (${limit_price:.2f} -> ${fresh_price:.2f})")

    return {"ok": not reasons, "reasons": reasons, "fresh_price": fresh_price, "checked_at": _now_utc().isoformat()}


def _execute_approved_ticket(user_id: str, creds: Dict[str, str], ticket: Dict[str, object]) -> Dict[str, object]:
    """Submits an approved, re-checked ticket through the same functions the
    autonomous scan uses. Returns the entry record."""
    trading_day = str(ticket.get("trading_day") or _trading_day_key())
    ticker = str(ticket.get("ticker", "")).upper()
    account_id = str(ticket.get("account_id") or "")
    quantity = int(ticket.get("quantity") or 0)
    limit_price = float(ticket.get("limit_price") or 0)
    entry: Dict[str, object] = {
        key: ticket.get(key)
        for key in ("ticker", "direction", "side", "quantity", "limit_price", "confidence", "strategy", "trade_quality",
                    "trade_thesis", "why_ai_likes_it", "invalidation_rule", "risk_warning", "target", "stop",
                    "planned_risk_dollars", "trade_plan", "instrument_type", "premium_at_risk")
    }
    entry.update({
        "account_id": account_id, "status": "pending", "trading_day": trading_day,
        "source": "approved_ticket", "ticket_id": ticket.get("ticket_id"), "ticket_version": ticket.get("version"),
        "approved_at": ticket.get("approved_at"), "approved_by": ticket.get("approved_by"),
        "correlation_id": ticket.get("correlation_id") or f"tkt-{ticket.get('ticket_id')}",
    })
    if ticket.get("instrument_type") == "OPTION":
        option_contract = {key: ticket.get(key) for key in ("option_symbol", "strike", "expiration_date", "option_type")}
        _submit_and_confirm_option_entry(
            user_id=user_id, creds=creds, account_id=account_id, ticker=ticker, option_contract=option_contract,
            quantity=quantity, limit_price=limit_price, trading_day=trading_day, entry=entry,
        )
    else:
        _submit_and_protect_entry(
            user_id=user_id, creds=creds, account_id=account_id, ticker=ticker, requested_quantity=quantity,
            limit_price=limit_price, stop_price=float(ticket.get("stop") or 0), target_price=float(ticket.get("target") or 0),
            trading_day=trading_day, entry=entry,
        )
    lifecycle_state = entry.get("lifecycle_state")
    if lifecycle_state == ol.UNKNOWN_SUBMISSION_STATE:
        entry["status"] = "unknown_submission_state"
        entry["error"] = entry.get("error", "order submission result could not be confirmed (ambiguous broker response)")
        entry["skip_category"] = "unknown_submission_state"
        try:
            add_manual_alert(user_id, {
                "type": "unknown_submission_state", "ticker": ticker,
                "message": f"{ticker}: the approved order's submission returned an ambiguous result - it may or may not have been accepted. It will be reconciled automatically; review the position manually if it persists.",
            })
        except Exception:  # noqa: BLE001
            pass
    elif lifecycle_state == ol.ENTRY_FAILED:
        entry["status"] = "failed"
        entry["error"] = entry.get("error", "entry order failed")
        entry["skip_category"] = "entry_failed"
    else:
        entry["status"] = "placed"
    record_overnight_order(user_id, entry)
    try:
        record_research_decision(user_id, {
            "trading_day": trading_day, "account_id": account_id, "ticker": ticker,
            "recommendation": ticket.get("recommendation"), "strategy": ticket.get("strategy"),
            "raw_confidence": int(ticket.get("confidence") or 0),
            "decision": "placed" if entry["status"] == "placed" else "skipped",
            "reason_skipped": entry.get("error") if entry["status"] != "placed" else None,
            "skip_category": entry.get("skip_category"), "quantity": quantity,
            "entry_client_order_id": entry.get("entry_client_order_id"),
            "approved_ticket_id": ticket.get("ticket_id"), "trade_plan": ticket.get("trade_plan"),
            "correlation_id": entry.get("correlation_id"),
        })
    except Exception:  # noqa: BLE001
        pass
    POSITIONS_CACHE.pop(user_id, None)
    BALANCE_CACHE.pop(user_id, None)
    return entry


def _ticket_for_api(ticket: Dict[str, object]) -> Dict[str, object]:
    return {key: value for key, value in ticket.items() if key not in ("llm_reasoning",)} | {"llm_reasoning": ticket.get("llm_reasoning")}








SETUP_TIMEFRAMES = ("1d", "1h", "30m", "15m", "5m")






BROKER_RECONCILIATION_INTERVAL_SECONDS = 15 * 60


def _run_broker_reconciliation(user_id: str) -> Dict[str, object]:
    """Read-only: broker positions and working orders vs this user's records
    for the current environment (broker_reconciliation.py)."""
    creds = get_webull_credentials(user_id)
    try:
        listed = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        accounts = []
        for account in (webull_api.find_individual_cash_account(listed), webull_api.find_individual_margin_account(listed)):
            if account and account.get("account_id") and account["account_id"] not in accounts:
                accounts.append(account["account_id"])
    except Exception as error:  # noqa: BLE001 - recorded as disconnected
        return broker_reconciliation.run(user_id, accounts=[], records=[], read_positions=lambda a: [], read_open_orders=lambda a: [],
                                         account_error=f"{type(error).__name__}: {str(error)[:160]}")
    return broker_reconciliation.run(
        user_id, accounts=accounts, records=list_overnight_orders(user_id),
        read_positions=lambda account_id: webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id),
        read_open_orders=lambda account_id: webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id),
        read_history=lambda account_id: webull_api.get_order_history(creds["app_key"], creds["app_secret"], account_id, days_back=30),
        read_balance=lambda account_id: webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id),
    )


def _maybe_run_broker_reconciliation(user_id: str) -> None:
    if not is_webull_configured(user_id):
        return
    latest = (broker_reconciliation.load(user_id).get("latest") or {}).get("checked_at")
    if latest:
        try:
            if (_now_utc() - datetime.fromisoformat(str(latest))).total_seconds() < BROKER_RECONCILIATION_INTERVAL_SECONDS:
                return
        except ValueError:
            pass
    _run_broker_reconciliation(user_id)


# --- owner actions for positions held with no protective stop -----------------
#
# Protect: the owner chooses a stop price; it is saved on the position's own
# record and the existing monitor places, confirms and keeps watching the
# stop at the broker (_reconcile_entry_fill_and_protection - the same path
# every app-opened position uses). Close: a marketable limit order for the
# exact quantity the broker reports. Both: sandbox only, a fresh broker read
# first, the ticker typed to confirm, under the account's scan lock.

UNPROTECTED_CATEGORIES = ("unprotected_at_broker", "protection_missing_at_broker")
MANUAL_CLOSE_SLIPPAGE = 0.01


def _owner_position_action_guard(user_id: str, record_id: str, confirm_symbol: str):
    """(record, account_id, broker_position) or raises ValidationError."""
    if broker_env.current()["environment"] != broker_env.SANDBOX:
        raise ValidationError("Protect/close from here is enabled for the sandbox only.")
    record = next((o for o in list_overnight_orders(user_id) if str(o.get("record_id") or o.get("entry_client_order_id")) == record_id), None)
    if record is None or not broker_env.in_current(record):
        raise ValidationError("No such position record.")
    symbol = str(record.get("ticker") or "").upper()
    if confirm_symbol.strip().upper() != symbol:
        raise ValidationError(f"Type {symbol} exactly to confirm.")
    if record.get("instrument_type") == "OPTION":
        raise ValidationError("Options are exited by the app's own monitor; this action is for stock positions.")
    creds = get_webull_credentials(user_id)
    account_id = str(record.get("account_id") or "")
    if not account_id:
        listed = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
        account_id = str((webull_api.find_individual_cash_account(listed) or {}).get("account_id") or "")
    positions = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id)
    position = next((p for p in positions if str(p.get("symbol") or "").upper() == symbol), None)
    if position is None or float(position.get("quantity") or 0) == 0:
        raise ValidationError(f"The broker shows no {symbol} position right now - nothing to protect or close.")
    return record, account_id, position, creds














def _option_exit_settings(risk_settings: Dict[str, object]) -> Dict[str, object]:
    """The option exit rule exactly as _check_and_execute_option_exit applies it."""
    return {
        "stop_loss_percent": float(risk_settings.get("option_stop_loss_percent") or 20.0),
        "target_gain_percent": float(risk_settings.get("option_target_gain_percent") or 50.0),
        "close_days_before_expiration": int(risk_settings.get("option_close_days_before_expiration") or 3),
    }


def _option_trade_plan_for_candidate(
    *, ticker, opp, contract, underlying_price, sizing, plan_equity, equity_source, existing_exposure,
    available_buying_power, risk_settings, evidence_returns, quote_age_seconds=0.0, portfolio_context=None,
) -> Dict[str, object]:
    """Option counterpart of _trade_plan_for_candidate. Never raises."""
    strategy = opp.get("strategy")
    try:
        exits = _option_exit_settings(risk_settings)
        evidence = trade_planner.evidence_from_returns(
            evidence_returns.get(strategy) or [], strategy=strategy or "unknown", source="closed option trades, all accounts",
        )
        plan = trade_planner.build_option_trade_plan(
            ticker=ticker, strategy=strategy, setup_score=opp.get("confidence"), contract=contract,
            underlying_price=underlying_price, quote_age_seconds=quote_age_seconds, plan_equity=plan_equity,
            equity_source=equity_source, existing_exposure=existing_exposure, available_buying_power=available_buying_power,
            sizing=sizing, stop_loss_percent=exits["stop_loss_percent"], target_gain_percent=exits["target_gain_percent"],
            close_days_before_expiration=exits["close_days_before_expiration"], today=_now_utc().astimezone(ZoneInfo("America/New_York")).date(),
            evidence=evidence,
        )
        if portfolio_context:
            plan["portfolio"] = portfolio_context
        return plan
    except Exception as error:  # noqa: BLE001
        logger.warning("Option trade plan failed for %s: %s", ticker, error)
        return {"planner_version": trade_planner.PLANNER_VERSION, "ticker": ticker, "instrument": "OPTION", "decision": "reject",
                "reasons": [f"Plan could not be built: {error}"]}


def _tickers_with_open_positions(orders: List[Dict[str, object]]) -> set:
    """Tickers with an entry still short of a terminal state - the
    duplicate-position guard's input.

    A record with no lifecycle_state never went through submission: a
    skipped candidate (price drift, LLM veto, sizing), or a Trade Journal
    manual-close SELL. ol.is_transitional defaults those to "still open"
    (the conservative choice for the monitor), which here meant one skip
    locked that ticker out of new entries permanently. Such a record only
    counts if it is a legacy placed entry from before the lifecycle field
    existed."""
    tickers = set()
    for order in orders:
        ticker = str(order.get("ticker", "")).upper()
        if not ticker:
            continue
        if "lifecycle_state" in order:
            if ol.is_transitional(order):
                tickers.add(ticker)
        elif order.get("status") == "placed" and (order.get("side") == "BUY" or order.get("direction") == "short"):
            tickers.add(ticker)
    return tickers


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
    try:
        import go_live_gate
        go_live_gate.bind(user_id)
    except Exception:
        pass
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
            _monitor_transitional_orders(user_id, creds, margin_account_id, include_unstamped=False)
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

    # Fetched once and reused for buying power below. combined_day_pnl is the
    # same figure inject_loss_cap shows on the home meter.
    margin_balance = None
    if margin_account_id:
        try:
            margin_balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], margin_account_id)
        except Exception as error:  # noqa: BLE001 - shorts fail closed below; day P/L stays cash-only, matching the home meter
            logger.warning("Margin account balance/snapshot lookup failed, short candidates this scan will fail closed: %s", error)

    import daily_loss
    # Same account id would be the cash balance counted twice. The home
    # meter skips that case too.
    distinct_margin = margin_balance if margin_account_id and margin_account_id != account_id else None
    day_pnl = daily_loss.combined_day_pnl(balance, distinct_margin)
    halt_reason = _new_entry_halt_reason(user_id, day_pnl, current_balance, risk_settings)
    if halt_reason:
        raise ValidationError(halt_reason)

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
    # Trade-plan context (trade_planner.py), from the same snapshot sizing uses.
    plan_equity_source = "configured paper balance" if virtual_balance is not None else "broker net liquidation value"
    plan_existing_exposure = sum(_float_or_zero(position.get("market_value")) for position in real_open_positions_snapshot)
    try:
        strategy_evidence_returns = closed_trade_returns_by_strategy(instrument_type="EQUITY")
        option_evidence_returns = closed_trade_returns_by_strategy(instrument_type="OPTION")
    except Exception:  # noqa: BLE001 - evidence is informational; plans then say "not reliably estimated"
        strategy_evidence_returns, option_evidence_returns = {}, {}
    margin_open_positions_snapshot: List[Dict[str, object]] = []
    if margin_account_id:
        margin_open_positions_snapshot = webull_api.get_account_positions(creds["app_key"], creds["app_secret"], margin_account_id)
        open_position_count += len(margin_open_positions_snapshot)
    available_position_slots = _available_position_slots(max_positions, open_position_count, OVERNIGHT_MAX_ORDERS_PER_RUN)
    # Working orders seen by the capital snapshots below, kept for the
    # portfolio-exposure snapshot (no extra broker call).
    scan_working_orders: List[Dict[str, object]] = []

    # ONE reconciled broker snapshot for the whole scan, not one per
    # candidate. Re-reading the broker before every candidate does not solve
    # broker-side eventual consistency - an order accepted moments ago isn't
    # guaranteed to already appear in a fresh get_open_orders() read, so
    # candidate 2 could see the exact same (stale) snapshot candidate 1 did
    # and oversubscribe the same dollars. See _build_capital_snapshot for the
    # fail-closed handling of a broker failure or malformed response.
    tracked_tickers_for_user = {
        str(order.get("ticker", "")).upper() for order in broker_env.only_current(list_overnight_orders(user_id)) if order.get("ticker")
    }
    snapshot_available_buying_power = _build_capital_snapshot(
        fetch_open_orders=lambda: _remember_rows(scan_working_orders, webull_api.get_open_orders(creds["app_key"], creds["app_secret"], account_id)),
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
    if margin_balance is not None:
        try:
            margin_broker_buying_power = _extract_broker_buying_power(margin_balance)
            margin_option_buying_power = _extract_option_buying_power(margin_balance)
            margin_real_net_liquidation_value = float(margin_balance.get("total_net_liquidation_value", 0) or 0)
            margin_snapshot_available_buying_power = _build_capital_snapshot(
                fetch_open_orders=lambda: _remember_rows(scan_working_orders, webull_api.get_open_orders(creds["app_key"], creds["app_secret"], margin_account_id)),
                real_open_positions=margin_open_positions_snapshot,
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
    # Paper: off until set. Live: sensible defaults until the owner saves their own.
    live_entries = webull_api.is_live_trading_armed()
    portfolio_limit_settings = portfolio_limits.effective_limits(risk_settings, live=live_entries)
    max_position_exposure_percent = float(portfolio_limit_settings.get("max_position_percent") or 0)
    position_exposure_cap = _compute_position_exposure_cap(current_balance, max_position_exposure_percent)
    portfolio_snapshot = portfolio_limits.exposure_snapshot(
        list(real_open_positions_snapshot) + list(margin_open_positions_snapshot), scan_working_orders,
    )
    try:
        equity_peak = portfolio_limits.record_equity_and_get_peak(user_id, current_balance, plan_equity_source, persist=not dry_run)
        drawdown_block = portfolio_limits.drawdown_block_reason(
            current_balance, float(equity_peak.get("peak") or 0), portfolio_limit_settings["max_drawdown_percent"],
        )
    except Exception as error:  # noqa: BLE001 - an unreadable peak must not silently disable an enabled limit
        drawdown_block = (
            f"the drawdown limit is on but the equity peak could not be read ({error}) - new entries paused"
            if portfolio_limit_settings["max_drawdown_percent"] else None
        )

    def _scan_portfolio_room(ticker: str) -> Dict[str, Tuple[float, str]]:
        return portfolio_limits.room_for(ticker, current_balance, portfolio_snapshot, portfolio_limit_settings)

    def _scan_portfolio_context(ticker: str, trade_value: float) -> Dict[str, object]:
        return portfolio_limits.portfolio_context(ticker, trade_value, current_balance, portfolio_snapshot, portfolio_limit_settings)

    # include_options=False - the heaviest, most rate-limit-risky part of
    # _build_page_context (a real options-chain fetch per intelligence
    # ticker) feeds only options_expirations/expected_move on each
    # opportunity below, and neither field is ever read past this point -
    # candidate selection, sizing, and _submit_and_protect_entry all key off
    # ideal_entry/stop/target/confidence, which come from strategy/chart,
    # not options. Found while investigating the OOM crashes that
    # repeatedly line up with this exact scan trigger's own GitHub Actions
    # run timestamps (see the Autonomous scan scheduler 502 failures).
    # Reversal/trend rows are not read anywhere in this scan, and building
    # them pulls yfinance (analytics.py). The live decision path is the
    # Alpaca-backed strategy/chart/extended-hours brains. Dashboard pages
    # that display reversal and trend still request them.
    context = _build_page_context(include_reversal=False, include_trend=False, include_options=False)
    opportunities = context.get("upcoming_opportunities", [])

    today_key = _trading_day_key()

    def _order_trading_day(order: Dict[str, object]) -> str:
        try:
            return _trading_day_key(datetime.fromisoformat(str(order.get("logged_at", ""))))
        except ValueError:
            return ""

    already_placed_today = {
        str(order.get("ticker", "")).upper()
        for order in broker_env.only_current(list_overnight_orders(user_id))
        if order.get("status") == "placed" and _order_trading_day(order) == today_key
    }
    reentry_ready = set()
    if confirm_entry.confirm_enabled():
        try:
            reentry_ready = confirm_entry.reentry_tickers(
                broker_env.only_current(list_overnight_orders(user_id)),
                today_key,
                _order_trading_day,
            )
        except Exception:
            reentry_ready = set()

    # A ticker whose entry is still anywhere short of CLOSED - opened
    # today or on an earlier day, equity or option - must never receive a
    # second, independent autonomous entry. already_placed_today above
    # only catches TODAY's placements; a multi-day swing hold from Monday
    # would silently fall through it on Wednesday and let a fresh bullish
    # signal pyramid into the same ticker as an entirely separate
    # position. Sourced from overnight_orders (not the raw broker position
    # snapshot) specifically because ol.is_transitional's ticker field is
    # already correct for BOTH equity and option entries - get_account_positions'
    # real response shape for an OPTION row is documented elsewhere in this
    # function as unconfirmed, so matching on the broker snapshot directly
    # would silently miss every open option position.
    tickers_with_open_positions = _tickers_with_open_positions(broker_env.only_current(list_overnight_orders(user_id)))

    # APPROVAL mode (autonomous_controller): the scan runs exactly as it
    # does in AUTONOMOUS mode - same data, sizing, LLM step and gates - but
    # a candidate that reaches submission becomes a trade ticket for the
    # user to approve (see trade_tickets.py and api_trade_ticket_approve)
    # instead of an order. A preview never creates tickets. A ticker the
    # user declined today is not proposed again today.
    approval_mode_active = bool(risk_settings.get("approval_required_status")) and not dry_run
    declined_today = trade_tickets.declined_tickers_today(user_id, today_key) if approval_mode_active else set()

    qualifying = [
        opp
        for opp in opportunities
        if str(opp.get("recommendation", "")).upper() in ("CALL", "PUT")
        and int(opp.get("confidence", 0) or 0) >= OVERNIGHT_MIN_CONFIDENCE
        and (
            str(opp.get("ticker", "")).upper() not in already_placed_today
            or str(opp.get("ticker", "")).upper() in reentry_ready
        )
        and str(opp.get("ticker", "")).upper() not in tickers_with_open_positions
        and str(opp.get("ticker", "")).upper() not in declined_today
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
    elif drawdown_block:
        new_entries_blocked_reason = drawdown_block
    else:
        new_entries_blocked_reason = ""
    entries_allowed = not new_entries_blocked_reason
    candidates = qualifying[: min(OVERNIGHT_MAX_ORDERS_PER_RUN, available_position_slots)] if entries_allowed else []

    placed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []
    proposed: List[Dict[str, object]] = []  # APPROVAL mode: tickets created this run
    # Options-path funnel counters (2026-09-09) - tracked independently of
    # placed/skipped because a found-but-too-small-to-size option contract
    # falls straight through to the equity path with no placed/skipped
    # record of its own (see the "additive, not a replacement" comment
    # above the options-attempt block below) - without these counters that
    # attempt would be invisible to the new efficiency report entirely.
    # option_attempted counts every candidate for which a contract lookup
    # was actually made (margin account + valid price); option_contract_found
    # counts how many of those lookups returned a real, listed contract,
    # regardless of what happened after (sized to zero, submitted, failed).
    option_attempted = 0
    option_contract_found = 0

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

    def _signal_snapshot_from(opp: Dict[str, object]) -> Dict[str, object]:
        """SHADOW MODE ONLY - see _log_research_decision's own docstring.
        Pass-through of strategy_brain's already-computed market_context/
        strategies_evaluated (carried on the opportunity dict by
        _build_page_context) - never recomputed or re-fetched here, so
        this is zero marginal cost and cannot itself fail in a way that
        needs a try/except the way a fresh network call would."""
        return {
            "market_context": opp.get("market_context"),
            "strategies_evaluated": opp.get("strategies_evaluated"),
            "market_intel": opp.get("market_intel"),
        }

    def _log_research_decision(
        *, ticker, recommendation, strategy, raw_confidence, decision, reason_skipped,
        quantity, entry_client_order_id, regime_shadow=None, skip_category=None, signal_snapshot=None,
        trade_plan=None, correlation_id=None, evaluated_at=None,
        suggestion_id=None, ledger_decision=None, ledger_entry=None, ledger_stop=None,
        ledger_target=None, ledger_regime=None, ledger_reentry=False, confirm_expires_at=None,
    ):
        """Durably records ONE evaluated candidate to the append-only
        research log (autonomy/research_log.py) - called for EVERY
        opportunity this scan touches, whether it was ultimately placed,
        skipped below the confidence floor, skipped for max-positions/
        risk/buying-power reasons, or vetoed by the LLM step, so later
        analysis of the VIX shadow signal (or any other future research
        signal) isn't built only from the subset that reached submission -
        see research_log.py's own module docstring on survivorship bias.
        Never lets a logging failure affect the real scan.

        skip_category (2026-09-11): the same short skip-reason code already
        stamped on the in-memory skipped/placed record for the trading-
        efficiency funnel (app.py's own skip_category work) - was
        previously computed at every site but never threaded into this
        durable log, so it silently never reached research_log.py. None
        for a placed candidate (no skip to categorize).

        signal_snapshot (2026-09-11): the "memory" layer - market_context
        and strategies_evaluated exactly as strategy_brain computed them
        for THIS candidate (see _build_page_context's own opportunity
        construction, which now carries these through at zero marginal
        cost - never recomputed here). SHADOW MODE ONLY, same discipline
        as regime_shadow below: nothing reads this back into a decision.
        See docs/AGENT_ARCHITECTURE.md for why candle_brain/pattern_brain/
        neural_engine are deliberately NOT captured here yet - they'd add
        real yfinance network calls to every candidate on every scan tick,
        the same load pattern that caused the original Yahoo rate-limit
        incident this app already migrated away from once.

        No-ops entirely under dry_run - a preview run isn't a real evaluated
        candidate with real consequences, and mixing preview rows into the
        durable research log would corrupt the survivorship-bias analysis
        that log exists for.

        correlation_id / evaluated_at (2026-09-29): the id that ties this
        candidate to its plan, ticket and orders in the event journal
        (autonomy/event_journal.py), and when its evaluation began - a placed
        candidate is logged after submission, so the evaluation is dated to
        the start of its loop iteration, not to this call."""
        if dry_run:
            return
        correlation_id = correlation_id or event_journal.new_correlation_id()
        evaluated_at = evaluated_at or _now_utc().isoformat()
        try:
            record_research_decision(
                user_id,
                {
                    "correlation_id": correlation_id,
                    "trading_day": today_key,
                    "account_id": account_id,
                    "ticker": ticker,
                    "recommendation": recommendation,
                    "strategy": strategy,
                    "raw_confidence": raw_confidence,
                    "decision": decision,
                    "reason_skipped": reason_skipped,
                    "skip_category": skip_category,
                    "quantity": quantity,
                    "entry_client_order_id": entry_client_order_id,
                    "regime_shadow": regime_shadow if regime_shadow is not None else _shadow_snapshot_for(ticker, strategy, raw_confidence),
                    "signal_snapshot": signal_snapshot,
                    "trade_plan": trade_planner.compact_plan(trade_plan) if trade_plan else None,
                },
            )
        except Exception:  # noqa: BLE001 - research logging must never affect the real scan
            pass
        events = [event_journal.build_event(
            "signal.evaluated", correlation_id=correlation_id, ticker=ticker, account_id=account_id, source="scan",
            at=evaluated_at,
            data={
                "trading_day": today_key, "decision": decision, "skip_category": skip_category, "reason": reason_skipped,
                "recommendation": recommendation, "strategy": strategy, "confidence": raw_confidence, "quantity": quantity,
            },
        )]
        if isinstance(trade_plan, dict):
            numbers = trade_plan.get("numbers") or {}
            events.append(event_journal.build_event(
                "plan.built", correlation_id=correlation_id, ticker=ticker, account_id=account_id, source="trade_planner",
                at=evaluated_at,
                data={
                    "planner_version": trade_plan.get("planner_version"), "instrument": trade_plan.get("instrument"),
                    "direction": trade_plan.get("direction"), "decision": trade_plan.get("decision"),
                    "reasons": trade_plan.get("reasons"),
                    **{key: numbers.get(key) for key in (
                        "quantity", "entry_price", "entry_premium", "stop_price", "exit_trigger_premium", "target_price",
                        "planned_loss", "loss_at_trigger", "premium_at_risk", "reward_to_risk", "allocation_percent",
                    ) if numbers.get(key) is not None},
                },
            ))
        event_journal.emit_many(user_id, events)
        try:
            mapped = ledger_decision
            if not mapped:
                if decision == "placed":
                    mapped = "taken"
                elif decision == "proposed":
                    mapped = "proposed"
                else:
                    mapped = "skipped"
            intel_record = None
            if isinstance(signal_snapshot, dict) and isinstance(signal_snapshot.get("market_intel"), dict):
                intel_record = signal_snapshot.get("market_intel")
            suggestion_ledger.append({
                "id": suggestion_id,
                "user_id": user_id,
                "trading_day": today_key,
                "ticker": ticker,
                "strategy": strategy,
                "recommendation": recommendation,
                "confidence": raw_confidence,
                "decision": mapped,
                "skip_category": skip_category,
                "reason": reason_skipped,
                "features": suggestion_ledger.features_from(signal_snapshot, raw_confidence),
                "market_intel": intel_record,
                "entry": ledger_entry,
                "stop": ledger_stop,
                "target": ledger_target,
                "quantity": quantity,
                "regime": ledger_regime,
                "reentry": bool(ledger_reentry),
                "confirm_expires_at": confirm_expires_at,
            })
        except Exception:
            pass

    for opp in opportunities:
        if opp in candidates:
            continue
        opp_was_qualifying = opp in qualifying
        ticker_upper = str(opp.get("ticker", "")).upper()
        # Only the max-positions/no-slots branch needs surfacing per-ticker
        # in the scan-run reason text (see the sizing-rejection skip's own
        # comment, above, in the main candidate loop) - the blocked-entries
        # branch is already covered once, globally, via
        # new_entries_blocked_reason in the top-level summary, so tagging it
        # too would just duplicate the same reason for every candidate.
        surface_in_summary = False
        if not entries_allowed and opp_was_qualifying:
            reason = new_entries_blocked_reason
            skip_category = "entries_blocked"
        elif opp_was_qualifying:
            reason = f"max_positions limit reached ({open_position_count}/{max_positions} open)" if max_positions > 0 else "no position slots available"
            surface_in_summary = True
            skip_category = "max_positions"
        elif str(opp.get("recommendation", "")).upper() not in ("CALL", "PUT"):
            reason = f"recommendation is {opp.get('recommendation')}, only CALL/PUT setups auto-order tonight"
            skip_category = "not_call_or_put"
        elif int(opp.get("confidence", 0) or 0) < OVERNIGHT_MIN_CONFIDENCE:
            reason = f"confidence {opp.get('confidence')} below {OVERNIGHT_MIN_CONFIDENCE} threshold"
            skip_category = "confidence_threshold"
        elif ticker_upper in declined_today:
            reason = f"{opp.get('ticker')} was declined by you today - not proposed again until tomorrow"
            skip_category = "declined_today"
        elif ticker_upper in tickers_with_open_positions:
            # Checked before already_placed_today below - a position opened
            # today is ALSO still transitional, so this is the more specific
            # (and more common) of the two true reasons; still_placed_today
            # only ever fires for the narrower case of a same-day entry that
            # already fully closed today (terminal, so not caught above).
            reason = f"{opp.get('ticker')} already has an open, unclosed position - refusing to place a second, independent entry in the same ticker"
            skip_category = "already_holds_position"
        else:
            reason = f"{opp.get('ticker')} was already placed today"
            skip_category = "already_placed_today"
        skip_record = {
            "ticker": opp.get("ticker"),
            "recommendation": opp.get("recommendation"),
            "confidence": opp.get("confidence"),
            "reason_skipped": reason,
            "skip_category": skip_category,
        }
        if surface_in_summary:
            skip_record["was_qualifying"] = True
        skipped.append(skip_record)
        _log_research_decision(
            ticker=opp.get("ticker"), recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
            raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=reason,
            quantity=None, entry_client_order_id=None, skip_category=skip_category,
            signal_snapshot=_signal_snapshot_from(opp),
        )

    for candidate_index, opp in enumerate(candidates):
        blocked = None
        try:
            blocked = evidence_gates.block_reason(opp)
        except Exception:
            blocked = None
        if blocked:
            why, category = blocked
            blocked_ticker = str(opp.get("ticker", ""))
            skipped.append({
                "ticker": blocked_ticker,
                "recommendation": opp.get("recommendation"),
                "confidence": opp.get("confidence"),
                "reason_skipped": why,
                "was_qualifying": True,
                "skip_category": category,
            })
            _log_research_decision(
                ticker=blocked_ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=why,
                quantity=None, entry_client_order_id=None, skip_category=category,
                signal_snapshot=_signal_snapshot_from(opp),
                ledger_regime=evidence_gates.regime_from_context(
                    opp.get("market_context") if isinstance(opp.get("market_context"), dict) else None
                ),
            )
            continue
        if candidate_index > 0:
            time.sleep(1.0)  # spread order placements out to avoid tripping Webull's rate limiter
        ticker = str(opp.get("ticker", ""))
        correlation_id = event_journal.new_correlation_id()
        evaluated_at = _now_utc().isoformat()
        limit_price = float(opp.get("ideal_entry") or 0)
        stop_price_for_sizing = float(opp.get("stop") or 0)
        target_price_for_check = float(opp.get("target") or 0)
        direction = "short" if str(opp.get("recommendation", "")).upper() == "PUT" else "long"

        # An entry that has no computable stop or target can never be
        # placed as a *protected* trade - the protective legs have nothing
        # to attach to. Submitting it anyway is exactly what produced the
        # 2026-09-04 orphan cascade: five entries went in with stop=0/
        # target=0 (their chart breakout/breakdown levels came back 0 from
        # get_chart_levels_for_ticker), the fills leaked through an
        # ambiguous broker response, the protective orders had no price to
        # use, and all five ended up as naked "orphan" positions the
        # monitor then had to hunt down and reconcile - blocking every new
        # autonomous entry for days in the process. Reject it here, before
        # a single broker call, rather than downstream: _compute_position_
        # quantity would also fail this (stop_price<=0 -> quantity 0) but
        # reports it as "sizing_too_small", which is misleading - this is a
        # data-quality problem with the candidate, not a capital one.
        # Directional sanity too: a long (CALL) needs stop < entry < target
        # and a short (PUT) the mirror; inverted/garbage levels are just as
        # unprotectable as a zero.
        levels_ok = (
            limit_price > 0
            and stop_price_for_sizing > 0
            and target_price_for_check > 0
            and (
                (stop_price_for_sizing < limit_price < target_price_for_check)
                if direction == "long"
                else (target_price_for_check < limit_price < stop_price_for_sizing)
            )
        )
        if not levels_ok:
            reason = (
                f"candidate has no usable protective levels "
                f"(entry {limit_price}, stop {stop_price_for_sizing}, target {target_price_for_check}) - "
                "cannot be placed as a protected trade, skipping rather than risking an unprotected fill"
            )
            skipped.append(
                {
                    "ticker": ticker,
                    "recommendation": opp.get("recommendation"),
                    "confidence": opp.get("confidence"),
                    "reason_skipped": reason,
                    "was_qualifying": True,
                    "skip_category": "unprotectable_levels",
                }
            )
            _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=reason,
                quantity=0, entry_client_order_id=None, skip_category="unprotectable_levels",
                signal_snapshot=_signal_snapshot_from(opp),
            )
            continue

        if direction == "short" and not margin_account_id:
            # A genuine margin account is required to hold a short position
            # (OPENAPI_GENERATE_NEW_SHORT_POSITION on the cash account,
            # confirmed live 2026-08-31) - this user simply doesn't have
            # one provisioned. Fails closed with a clear reason rather than
            # ever falling through to attempt this against the cash
            # account, which the broker would reject anyway.
            reason = "no margin account available for a PUT/short entry"
            skipped.append(
                {"ticker": ticker, "recommendation": opp.get("recommendation"), "confidence": opp.get("confidence"), "reason_skipped": reason, "was_qualifying": True, "skip_category": "no_margin_account"}
            )
            _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=reason,
                quantity=0, entry_client_order_id=None, skip_category="no_margin_account",
                signal_snapshot=_signal_snapshot_from(opp),
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
            option_attempted += 1
            try:
                option_contract = select_option_contract(
                    creds["app_key"], creds["app_secret"], ticker,
                    "PUT" if direction == "short" else "CALL", limit_price,
                )
            except Exception as error:  # noqa: BLE001 - a broker/data failure on the OPTIONAL options lookup must never block the proven equity path below; log and fall through
                logger.warning("select_option_contract failed for %s, falling back to the equity path: %s", ticker, error)
                option_contract = None
            if option_contract:
                option_contract_found += 1
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
                    extra_caps=_scan_portfolio_room(ticker),
                )
                option_quantity = int(option_sizing["quantity"])
                if option_quantity >= 1 and webull_api.is_live_trading_armed():
                    import live_ramp
                    ramped_option_quantity = live_ramp.scale_quantity(option_quantity)
                    if ramped_option_quantity < 1:
                        skipped.append({
                            "ticker": ticker,
                            "recommendation": opp.get("recommendation"),
                            "confidence": opp.get("confidence"),
                            "reason_skipped": (
                                f"live size ramp ({live_ramp.fraction():.0%} of the paper size) "
                                "is below 1 contract"
                            ),
                            "was_qualifying": True,
                            "skip_category": "live_ramp",
                            "instrument_type": "OPTION",
                        })
                        continue
                    option_quantity = ramped_option_quantity
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
                        # Stamped here, not per-branch below, so every
                        # eventual outcome (preview/placed/failed/
                        # unknown_submission_state) carries it uniformly -
                        # see the option_attempted/option_contract_found
                        # counters above for the funnel-level view of this
                        # same signal. True unconditionally here because
                        # this dict only gets built once select_option_contract
                        # has already returned a real, non-None contract.
                        "instrument_type": "OPTION",
                        "option_contract_found": True,
                        "correlation_id": correlation_id,
                    }
                    # Contract quote was fetched moments ago by select_option_contract.
                    option_entry["trade_plan"] = _option_trade_plan_for_candidate(
                        ticker=ticker, opp=opp, contract=option_contract, underlying_price=limit_price, sizing=option_sizing,
                        plan_equity=current_balance, equity_source=plan_equity_source, existing_exposure=plan_existing_exposure,
                        available_buying_power=option_available_buying_power, risk_settings=risk_settings,
                        evidence_returns=option_evidence_returns,
                        portfolio_context=_scan_portfolio_context(ticker, float(option_quantity) * float(option_contract["ask"]) * 100),
                    )
                    option_limit_price = round(option_contract["ask"], 2)
                    option_cost_reservation = _to_decimal(option_sizing["cost_per_contract"]) * _to_decimal(option_quantity)
                    if dry_run:
                        option_entry["status"] = "preview"
                        option_entry["option_symbol"] = option_contract["option_symbol"]
                        option_entry["strike"] = option_contract["strike"]
                        option_entry["expiration_date"] = option_contract["expiration_date"]
                        option_entry["option_type"] = option_contract["option_type"]
                        option_entry["limit_price"] = option_limit_price
                        option_entry["quantity"] = option_quantity
                        placed.append(option_entry)
                        local_reservations_by_account[margin_account_id] += option_cost_reservation
                        portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(option_cost_reservation))
                        continue
                    if approval_mode_active:
                        option_entry.update({
                            "option_symbol": option_contract["option_symbol"], "strike": option_contract["strike"],
                            "expiration_date": option_contract["expiration_date"], "option_type": option_contract["option_type"],
                            "limit_price": option_limit_price, "quantity": option_quantity, "direction": direction,
                            "premium_at_risk": round(float(option_cost_reservation), 2),
                        })
                        _propose_trade_ticket(user_id, option_entry, opp)
                        correlation_id = str(option_entry.get("correlation_id") or correlation_id)
                        proposed.append(option_entry)
                        local_reservations_by_account[margin_account_id] += option_cost_reservation
                        portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(option_cost_reservation))
                        _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                            ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                            raw_confidence=int(opp.get("confidence", 0) or 0), decision="proposed", reason_skipped=None,
                            quantity=option_quantity, entry_client_order_id=None, skip_category="awaiting_approval",
                            signal_snapshot=_signal_snapshot_from(opp), trade_plan=option_entry.get("trade_plan"),
                        )
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
                            option_entry["skip_category"] = "unknown_submission_state"
                            local_reservations_by_account[margin_account_id] += option_cost_reservation
                            portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(option_cost_reservation))
                            skipped.append(option_entry)
                        else:
                            option_entry["status"] = "failed" if option_lifecycle_state == ol.ENTRY_FAILED else "placed"
                            if option_entry["status"] == "failed":
                                option_entry["error"] = option_entry.get("error", "entry order failed")
                                option_entry["skip_category"] = "entry_failed"
                                skipped.append(option_entry)
                            else:
                                placed.append(option_entry)
                                local_reservations_by_account[margin_account_id] += option_cost_reservation
                                portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(option_cost_reservation))
                    except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole batch, same discipline as the equity path below
                        option_entry["status"] = "failed"
                        option_entry["error"] = str(error)
                        option_entry["skip_category"] = "entry_failed"
                        skipped.append(option_entry)
                    record_overnight_order(user_id, option_entry)
                    _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                        ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                        raw_confidence=int(opp.get("confidence", 0) or 0),
                        decision="placed" if option_entry.get("status") == "placed" else "skipped",
                        reason_skipped=option_entry.get("error") if option_entry.get("status") != "placed" else None,
                        quantity=option_quantity, entry_client_order_id=option_entry.get("entry_client_order_id"),
                        skip_category=option_entry.get("skip_category"), signal_snapshot=_signal_snapshot_from(opp),
                        trade_plan=option_entry.get("trade_plan"),
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
            extra_caps=_scan_portfolio_room(ticker),
        )
        quantity = int(sizing["quantity"])
        if quantity >= 1 and webull_api.is_live_trading_armed():
            import live_ramp
            ramped_quantity = live_ramp.scale_quantity(quantity)
            if ramped_quantity < 1:
                sizing = {
                    **sizing,
                    "reason": (
                        f"live size ramp ({live_ramp.fraction():.0%} of the paper size) is below 1 share"
                    ),
                }
                quantity = 0
            else:
                quantity = ramped_quantity
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
                    "skip_category": "sizing_too_small",
                    "instrument_type": "EQUITY",
                }
            )
            _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=sizing["reason"],
                quantity=0, entry_client_order_id=None, skip_category="sizing_too_small",
                signal_snapshot=_signal_snapshot_from(opp),
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
            # Stamped here, not per-branch below, so every eventual outcome
            # (preview/llm_veto/price_drift/placed/failed/
            # unknown_submission_state) carries it uniformly - mirrors the
            # same field on option_entry above.
            "instrument_type": "EQUITY",
            "correlation_id": correlation_id,
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
                entry["skip_category"] = "llm_veto"
                skipped.append(entry)
                if not dry_run:
                    record_overnight_order(user_id, entry)
                _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=veto_reason,
                    quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
                    regime_shadow=entry.get("regime_shadow"), skip_category="llm_veto",
                    signal_snapshot=_signal_snapshot_from(opp),
                )
                continue

        try:
            if limit_price <= 0:
                raise ValueError("No valid entry price computed for this ticker.")
            stop_price = float(entry.get("stop") or 0)
            target_price = float(entry.get("target") or 0)
            if dry_run:
                entry["trade_plan"] = _trade_plan_for_candidate(
                    ticker=ticker, opp=opp, direction=direction, entry_price=limit_price, stop_price=stop_price,
                    target_price=target_price, quote_price=None, sizing=sizing, plan_equity=current_balance,
                    equity_source=plan_equity_source, existing_exposure=plan_existing_exposure,
                    available_buying_power=available_buying_power, short_permitted=margin_account_id is not None,
                    evidence_returns=strategy_evidence_returns, portfolio_context=_scan_portfolio_context(ticker, float(quantity) * float(limit_price)), quote_deferred=True,
                )
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
                portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(quantity) * float(limit_price))
                continue

            if approval_mode_active:
                # The plan is built like a preview's (no market-data call);
                # the approval recheck fetches the live price.
                entry["trade_plan"] = _trade_plan_for_candidate(
                    ticker=ticker, opp=opp, direction=direction, entry_price=limit_price, stop_price=stop_price,
                    target_price=target_price, quote_price=None, sizing=sizing, plan_equity=current_balance,
                    equity_source=plan_equity_source, existing_exposure=plan_existing_exposure,
                    available_buying_power=available_buying_power, short_permitted=margin_account_id is not None,
                    evidence_returns=strategy_evidence_returns, portfolio_context=_scan_portfolio_context(ticker, float(quantity) * float(limit_price)), quote_deferred=True,
                )
                _propose_trade_ticket(user_id, entry, opp)
                correlation_id = str(entry.get("correlation_id") or correlation_id)
                proposed.append(entry)
                local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
                portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(quantity) * float(limit_price))
                _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="proposed", reason_skipped=None,
                    quantity=quantity, entry_client_order_id=None, skip_category="awaiting_approval",
                    signal_snapshot=_signal_snapshot_from(opp), trade_plan=entry.get("trade_plan"),
                )
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
            #
            # Cross-checked against Webull's own quote since 2026-09-11
            # (see integrations/market_data_aggregator.py) - the broker
            # we're about to submit this order TO agreeing with the price
            # we computed is a stronger confirmation than Alpaca alone.
            # Webull being unavailable falls back to Alpaca's price alone
            # (unchanged from before); only a genuine disagreement between
            # both sources gets its own distinct skip_category below.
            cross_check = market_data_aggregator.get_cross_checked_price(ticker, creds)
            fresh_price = cross_check["price"]
            # Advisory in this version: recorded with the candidate and shown
            # in previews, but it does not gate submission - the scan's own
            # checks still decide. See trade_planner.py.
            entry["trade_plan"] = _trade_plan_for_candidate(
                ticker=ticker, opp=opp, direction=direction, entry_price=limit_price, stop_price=stop_price,
                target_price=target_price, quote_price=None if cross_check["disagreement"] else fresh_price, sizing=sizing,
                plan_equity=current_balance, equity_source=plan_equity_source, existing_exposure=plan_existing_exposure,
                available_buying_power=available_buying_power, short_permitted=margin_account_id is not None,
                evidence_returns=strategy_evidence_returns, portfolio_context=_scan_portfolio_context(ticker, float(quantity) * float(limit_price)),
            )
            drift_reason = ""
            drift_skip_category = "price_drift"
            if cross_check["disagreement"]:
                alpaca_reading = next((p["price"] for p in cross_check["provider_status"] if p["provider"] == "alpaca"), None)
                webull_reading = next((p["price"] for p in cross_check["provider_status"] if p["provider"] == "webull"), None)
                drift_reason = (
                    f"real-time price sources disagree for {ticker} immediately before submission "
                    f"(Alpaca ${alpaca_reading:.2f} vs Webull ${webull_reading:.2f}, beyond "
                    f"{market_data_aggregator.PRICE_SOURCE_DISAGREEMENT_TOLERANCE_PERCENT:.1f}% tolerance) - "
                    "refusing to submit against an unconfirmed price"
                )
                drift_skip_category = "price_sources_disagree"
            elif fresh_price is None:
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
                entry["skip_category"] = drift_skip_category
                skipped.append(entry)
                record_overnight_order(user_id, entry)
                _log_research_decision(
                correlation_id=correlation_id, evaluated_at=evaluated_at,
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped", reason_skipped=drift_reason,
                    quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
                    regime_shadow=entry.get("regime_shadow"), skip_category=drift_skip_category,
                    signal_snapshot=_signal_snapshot_from(opp), trade_plan=entry.get("trade_plan"),
                )
                continue

            if confirm_entry.confirm_enabled():
                regime_name = evidence_gates.regime_from_context(
                    opp.get("market_context") if isinstance(opp.get("market_context"), dict) else None
                )
                why_line = " ".join(str(opp.get("why_ai_likes_it") or opp.get("strategy") or "qualifying setup").split())
                if len(why_line) > 160:
                    why_line = why_line[:157].rstrip() + "..."
                is_reentry = ticker.upper() in reentry_ready
                base = confirm_entry.public_base_url()
                if not base:
                    choice = {
                        "send": False,
                        "skip_category": "confirm_unconfigured",
                        "ledger_decision": "skipped",
                        "reason": "confirm-to-enter is on but no public address is set, so nothing was ordered",
                    }
                else:
                    choice = confirm_entry.delivery(user_id, today_key, _now_utc())
                suggestion_id = uuid.uuid4().hex
                expires_at = None
                if choice.get("send"):
                    common = {
                        "uid": user_id,
                        "sid": suggestion_id,
                        "px": fresh_price if fresh_price else limit_price,
                        "sym": ticker,
                        "qty": quantity,
                        "lim": limit_price,
                        "stop": stop_price,
                        "tgt": target_price,
                        "dir": direction,
                        "day": today_key,
                        "acct": candidate_account_id,
                        "strat": opp.get("strategy"),
                        "conf": opp.get("confidence"),
                    }
                    now_token = _now_utc()
                    enter_token = confirm_entry.issue_token({**common, "act": "enter"}, now=now_token)
                    skip_token = confirm_entry.issue_token({**common, "act": "skip"}, now=now_token)
                    expires_at = (now_token + timedelta(seconds=confirm_entry.ttl_seconds())).isoformat()
                    message = confirm_entry.setup_message(
                        ticker=ticker,
                        why=why_line,
                        confidence=opp.get("confidence"),
                        regime=regime_name,
                        entry=limit_price,
                        stop=stop_price,
                        target=target_price,
                        quantity=float(quantity),
                        direction=direction,
                        reentry=is_reentry,
                    )
                    confirm_entry.send_setup_alert(
                        message,
                        confirm_entry.ntfy_actions(base, enter_token, skip_token),
                    )
                entry["status"] = "skipped"
                entry["reason_skipped"] = choice.get("reason")
                entry["was_qualifying"] = True
                entry["skip_category"] = choice.get("skip_category")
                skipped.append(entry)
                record_overnight_order(user_id, entry)
                _log_research_decision(
                    correlation_id=correlation_id, evaluated_at=evaluated_at,
                    ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
                    raw_confidence=int(opp.get("confidence", 0) or 0), decision="skipped",
                    reason_skipped=choice.get("reason"),
                    quantity=quantity, entry_client_order_id=None,
                    regime_shadow=entry.get("regime_shadow"), skip_category=choice.get("skip_category"),
                    signal_snapshot=_signal_snapshot_from(opp), trade_plan=entry.get("trade_plan"),
                    suggestion_id=suggestion_id, ledger_decision=choice.get("ledger_decision"),
                    ledger_entry=limit_price, ledger_stop=stop_price, ledger_target=target_price,
                    ledger_regime=regime_name, ledger_reentry=is_reentry, confirm_expires_at=expires_at,
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
                entry["skip_category"] = "unknown_submission_state"
                # Conservative: reserve the full attempted notional exactly
                # like a confirmed placement - the broker may well have
                # accepted this order even though the response was lost, and
                # a later candidate in this same run must not be sized as if
                # those dollars were still free. See _reconcile_unknown_submission
                # for how this gets resolved on a later scan.
                local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
                portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(quantity) * float(limit_price))
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
                    entry["skip_category"] = "entry_failed"
                    skipped.append(entry)
                else:
                    placed.append(entry)
                    # Reserved the instant the order is accepted, not after
                    # this function returns - this is what candidate 2's
                    # sizing above sees even if the broker's own open-orders
                    # read hasn't caught up to candidate 1 yet (see
                    # test_reservation_survives_broker_eventual_consistency).
                    local_reservations_by_account[candidate_account_id] += _reservation_notional(quantity, limit_price)
                    portfolio_limits.add_exposure(portfolio_snapshot, ticker, float(quantity) * float(limit_price))
        except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole batch
            entry["status"] = "failed"
            entry["error"] = str(error)
            entry["skip_category"] = "entry_failed"
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
                correlation_id=correlation_id, evaluated_at=evaluated_at,
            ticker=ticker, recommendation=opp.get("recommendation"), strategy=opp.get("strategy"),
            raw_confidence=int(opp.get("confidence", 0) or 0),
            decision="placed" if entry.get("status") == "placed" else "skipped",
            reason_skipped=entry.get("error") if entry.get("status") != "placed" else None,
            quantity=quantity, entry_client_order_id=entry.get("entry_client_order_id"),
            regime_shadow=entry.get("regime_shadow"), skip_category=entry.get("skip_category"),
            signal_snapshot=_signal_snapshot_from(opp), trade_plan=entry.get("trade_plan"),
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

    if not dry_run:
        try:
            graded = suggestion_ledger.grade_due(_ledger_bars_for)
            immediate = os.environ.get("PLUTO_WHATIF_IMMEDIATE", "").strip().lower() in {"1", "true", "yes", "on"}
            if immediate and graded and not confirm_entry.in_quiet_hours(_now_utc()):
                import phone_alerts
                sent_ids = []
                for item in suggestion_ledger.pending_what_ifs(user_id):
                    if phone_alerts.notify("setup", "What-if", item["text"]):
                        sent_ids.append(item["id"])
                if sent_ids:
                    suggestion_ledger.mark_what_ifs_sent(sent_ids)
        except Exception:
            pass

    return {
        "ok": True,
        "placed_count": len(placed),
        "skipped_count": len(skipped),
        "proposed_count": len(proposed),
        "placed": placed,
        "skipped": skipped,
        "proposed": proposed,
        # For autonomy/scan_run_log.py's durable per-tick record - how
        # many opportunities this tick looked at in total, how many
        # cleared the confidence/recommendation/dedup filter, and whether
        # new entries were allowed at all this tick (independent of
        # whether any candidate actually existed to place).
        "candidates_found": len(opportunities),
        "candidates_qualifying": len(qualifying),
        "entries_allowed": entries_allowed,
        "new_entries_blocked_reason": new_entries_blocked_reason,
        # Funnel-level options stats (2026-09-09) - see the counters'
        # own comment near their initialization for why these can't be
        # fully reconstructed from placed/skipped alone.
        "option_attempted": option_attempted,
        "option_contract_found": option_contract_found,
        "guardrail": "DAY limit orders in the Webull sandbox only. Session auto-selected by time of day.",
    }














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
    proposed_count = int(scan_result.get("proposed_count") or 0)
    if proposed_count:
        reason_parts.append(f"{proposed_count} ticket(s) awaiting your approval")
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

    # Trading-efficiency funnel (2026-09-09) - tallies the skip_category
    # field stamped at every skip site in _run_autonomous_trade_scan_locked
    # (see that function's per-candidate loop) into per-category counts for
    # this one tick, so autonomy/efficiency_report.py can sum them across a
    # window without re-parsing free-text reason strings. A record written
    # before skip_category existed simply has no entries here - not an
    # error, just "no category data for this tick" (see
    # SCAN_RUN_LOG_SCHEMA_VERSION's own "additive fields" convention).
    skip_categories: Dict[str, int] = {}
    for entry in skipped:
        if not isinstance(entry, dict):
            continue
        category = entry.get("skip_category")
        if category:
            skip_categories[category] = skip_categories.get(category, 0) + 1

    # Mirrors option_attempted/option_contract_found from the scan result
    # itself (counted independently of placed/skipped - see those counters'
    # own comment in _run_autonomous_trade_scan_locked for why a found-but-
    # too-small-to-size option contract would otherwise be invisible here).
    option_stats = {
        "attempted": int(scan_result.get("option_attempted") or 0),
        "contract_found": int(scan_result.get("option_contract_found") or 0),
    }

    return {
        "candidates_found": candidates_found,
        "candidates_qualifying": candidates_qualifying,
        "orders_attempted": len(submission_attempted),
        "orders_outcomes": outcomes,
        "reason": " | ".join(reason_parts),
        "skip_categories": skip_categories,
        "option_stats": option_stats,
    }


# Route functions live in backend/routes. They are executed into this
# module's globals so endpoint names, url_for(), and helper patches stay
# the same as when the views were defined here.
import routes as _pluto_routes
_pluto_routes.load_into(globals())
del _pluto_routes

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
