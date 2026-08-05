from __future__ import annotations

import hmac
import json
import os
import secrets as secrets_module
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for

if __package__:
    from .auth import (
        approve_user,
        authenticate_user,
        find_user_by_webhook_token,
        get_user_by_id,
        is_admin,
        list_all_user_ids,
        list_pending_users,
        public_user,
        register_user,
        reject_user,
        reset_password,
    )
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
    from .analytics import build_reversal_and_trend_payload
    from .candle_brain import analyze_candles
    from .core.errors import PlutoTradeError, ValidationError
    from .core.logger import get_logger, setup_logging
    from .brokers.etrade_broker import ETradeBroker
    from .brokers.webull_broker import WebullBroker
    from .brains.charting_brain import build_chart_levels
    from .brains.extended_hours_brain import build_extended_hours_intelligence
    from .brains.strategy_brain import build_strategy_intelligence
    from .integrations.tradingview import get_tradingview_status, save_alert
    from .integrations import webull as webull_api
    from .webull_credentials import get_webull_credentials, is_webull_configured, set_webull_credentials
    from .autonomy.overnight_orders import list_overnight_orders, record_overnight_order
    from .backtest_engine import run_backtest
    from .market_scanner import scan_market
    from .news.future_news import get_future_news_roadmap
    from .news.news_service import fetch_news_bundle
    from .news.x_news import (
        add_trusted_account,
        fetch_x_news_for_watchlist,
        get_trusted_accounts,
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
        approve_user,
        authenticate_user,
        find_user_by_webhook_token,
        get_user_by_id,
        is_admin,
        list_all_user_ids,
        list_pending_users,
        public_user,
        register_user,
        reject_user,
        reset_password,
    )
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
    from analytics import build_reversal_and_trend_payload
    from candle_brain import analyze_candles
    from core.errors import PlutoTradeError, ValidationError
    from core.logger import get_logger, setup_logging
    from brokers.etrade_broker import ETradeBroker
    from brokers.webull_broker import WebullBroker
    from brains.charting_brain import build_chart_levels
    from brains.extended_hours_brain import build_extended_hours_intelligence
    from brains.strategy_brain import build_strategy_intelligence
    from integrations.tradingview import get_tradingview_status, save_alert
    from integrations import webull as webull_api
    from webull_credentials import get_webull_credentials, is_webull_configured, set_webull_credentials
    from autonomy.overnight_orders import list_overnight_orders, record_overnight_order
    from backtest_engine import run_backtest
    from market_scanner import scan_market
    from news.future_news import get_future_news_roadmap
    from news.news_service import fetch_news_bundle
    from news.x_news import (
        add_trusted_account,
        fetch_x_news_for_watchlist,
        get_trusted_accounts,
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

# Curated, liquid Nasdaq-heavy scan universe (mostly Nasdaq-100 constituents
# plus SPY/QQQ). scan_market() fetches this in two batched yf.download() calls
# regardless of list size, so this can grow without a per-ticker request cost -
# see market_scanner.py.
CORE_SCAN_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "COST", "NFLX",
    "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTC", "QCOM", "TXN", "AMAT", "INTU",
    "ISRG", "BKNG", "VRTX", "REGN", "GILD", "MU", "LRCX", "KLAC", "PANW", "ADI",
    "MDLZ", "PYPL", "SNPS", "CDNS", "CRWD", "MRVL", "ABNB", "DXCM", "ORLY", "MNST",
    "CTAS", "PDD", "MELI", "WDAY", "ROP", "PLTR", "SPY", "QQQ",
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
CACHE_SECONDS = 45
ANALYTICS_CACHE_SECONDS = 180
NEWS_CACHE_SECONDS = 120
PATTERN_CACHE_SECONDS = 180
OPTIONS_CACHE_SECONDS = 150
STRATEGY_CACHE_SECONDS = 120
CHART_LEVEL_CACHE_SECONDS = 120

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


_PUBLIC_PATHS = {"/login", "/register", "/logout", "/forgot-password", "/service-worker.js"}
_PUBLIC_PATH_PREFIXES = ("/static/",)
_TOKEN_AUTH_PATHS = {"/api/tradingview/webhook", "/api/autonomy/cron-trigger"}


@app.before_request
def _require_login():
    path = request.path
    if path in _PUBLIC_PATHS or path in _TOKEN_AUTH_PATHS:
        return None
    if any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES):
        return None

    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if user and user.get("approved", True):
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

    session["user_id"] = user["id"]
    session.permanent = True
    target = next_path if next_path.startswith("/") else url_for("dashboard_page")
    return redirect(target)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        if session.get("user_id") and get_user_by_id(session["user_id"]):
            return redirect(url_for("dashboard_page"))
        return render_template("register.html", error="")

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
    context = _build_page_context()
    context["pending_users"] = list_pending_users()
    return render_template("admin.html", **context)


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
    rows, errors, last_updated = scan_market(tickers=scan_universe, watchlist_tickers=watchlist_tickers)
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


def get_news_data(watchlist_tickers: List[str], force_refresh: bool = False) -> Tuple[List[Dict[str, object]], List[str]]:
    ticker_key = _ticker_key(watchlist_tickers)
    if (
        not force_refresh
        and ticker_key == NEWS_CACHE.get("ticker_key")
        and NEWS_CACHE.get("rows")
        and _cache_is_fresh(NEWS_CACHE)
    ):
        return NEWS_CACHE["rows"], NEWS_CACHE["errors"]

    bundle = fetch_news_bundle(tickers=watchlist_tickers, limit=30)
    rows = bundle.get("items", [])
    errors = bundle.get("errors", [])
    NEWS_CACHE.update(
        {
            "ticker_key": ticker_key,
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
    force_refresh: bool = False,
    focus_ticker: str = "",
) -> Dict[str, object]:
    focus_ticker = focus_ticker.strip().upper()
    user_id = _current_user_id()
    settings_payload = get_settings(user_id)
    watchlist = get_watchlist(user_id)
    watchlist_tickers = [row["ticker"] for row in watchlist]
    scanner_rows, scanner_errors, scanner_last_updated = get_market_data(force_refresh=force_refresh)
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
        news_rows, news_errors = get_news_data(watchlist_tickers=watchlist_tickers, force_refresh=force_refresh)

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
    cached_news = news_rows or (NEWS_CACHE.get("rows", []) if _cache_is_fresh(NEWS_CACHE) else [])

    intelligence_tickers = _resolve_analysis_tickers(watchlist_tickers, scanner_rows, limit=6)
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
        with ThreadPoolExecutor(max_workers=len(intelligence_tickers)) as executor:
            for ticker, extended_hours, strategy, chart in executor.map(_fetch_ticker_intelligence, intelligence_tickers):
                extended_hours_map[ticker] = extended_hours
                strategy_map[ticker] = strategy
                chart_levels_map[ticker] = chart

        # Options data alone fires several Yahoo requests per ticker (expiration
        # list + one option_chain() call per expiration). Running all tickers at
        # full concurrency stacks those into a burst large enough to trip
        # Yahoo's rate limiting, so this pool is capped well below the others.
        with ThreadPoolExecutor(max_workers=min(3, len(intelligence_tickers))) as executor:
            options_map = dict(
                zip(
                    intelligence_tickers,
                    executor.map(lambda t: get_options_data_for_ticker(t, force_refresh=force_refresh), intelligence_tickers),
                )
            )
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
    }
    if include_trusted_accounts:
        context["trusted_accounts"] = get_trusted_accounts()
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
    return render_template(
        "reversal_map.html", **_build_page_context(include_reversal=True, include_trend=True, focus_ticker=focus_ticker)
    )


@app.route("/trend-detection")
@app.route("/volume-scanner")
def trend_detection_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    return render_template(
        "trend_detection.html", **_build_page_context(include_reversal=True, include_trend=True, focus_ticker=focus_ticker)
    )


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
    return render_template("settings.html", **_build_page_context(include_trusted_accounts=True))


@app.route("/account-hub")
def account_hub_page() -> str:
    context = _build_page_context()
    user_id = _current_user_id()
    context["accounts"] = get_accounts(user_id)
    context["webull_configured"] = is_webull_configured(user_id)
    return render_template("account_hub.html", **context)


@app.route("/notifications")
def notifications_page() -> str:
    return render_template("notifications.html", **_build_page_context(include_suggestions=True, include_news=True))


@app.route("/trade-journal")
def trade_journal_page() -> str:
    context = _build_page_context(include_reversal=True, include_trend=True)
    user_id = _current_user_id()
    context["paper_trades"] = list_paper_trades(user_id)
    context["paper_trade_summary"] = get_paper_trade_summary(user_id)
    overnight_orders = list_overnight_orders(user_id)
    context["overnight_orders"] = overnight_orders
    webull_positions = _get_live_webull_positions(user_id)
    if webull_positions.get("connected") and not webull_positions.get("error"):
        webull_positions = {
            **webull_positions,
            "positions": annotate_positions_with_exit_signal(webull_positions["positions"], overnight_orders),
        }
    context["webull_positions"] = webull_positions
    context["webull_balance"] = _get_live_webull_balance(user_id)
    return render_template("trade_journal.html", **context)


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

    entry = {
        "ticker": ticker,
        "side": "SELL",
        "quantity": quantity,
        "limit_price": limit_price,
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


@app.route("/candle-brain")
def candle_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    return render_template("candle_brain.html", **_build_page_context(include_patterns=True, focus_ticker=focus_ticker))


@app.route("/pattern-brain")
def pattern_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    return render_template("pattern_brain.html", **_build_page_context(include_patterns=True, focus_ticker=focus_ticker))


@app.route("/neural-engine")
def neural_engine_page() -> str:
    return render_template("neural_engine.html", **_build_page_context())


@app.route("/backtest")
def backtest_page() -> str:
    return render_template("backtest.html", **_build_page_context())


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


@app.route("/api/reversal-map", methods=["GET"])
def api_reversal_map():
    context = _build_page_context(include_reversal=True, include_trend=True)
    return jsonify({"rows": context["reversal_rows"], "errors": context["trend_errors"]})


@app.route("/api/trend-detection", methods=["GET"])
def api_trend_detection():
    context = _build_page_context(include_reversal=True, include_trend=True)
    return jsonify({"rows": context["trend_rows"], "errors": context["trend_errors"]})


@app.route("/api/patterns", methods=["GET"])
def api_patterns():
    context = _build_page_context(include_patterns=True)
    return jsonify({"candles": context["candle_rows"], "patterns": context["pattern_rows"], "errors": context["pattern_errors"]})


@app.route("/api/trusted-accounts", methods=["GET", "POST", "DELETE"])
def api_trusted_accounts():
    if request.method == "GET":
        return jsonify({"accounts": get_trusted_accounts()})

    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    try:
        if request.method == "POST":
            account = add_trusted_account(username=username)
            return jsonify({"ok": True, "account": account})
        remove_trusted_account(username=username)
        return jsonify({"ok": True})
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


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
            daily_loss_limit=payload.get("daily_loss_limit"),
            max_trade_size=payload.get("max_trade_size"),
            max_positions=payload.get("max_positions"),
        )
    except (ValueError, TypeError) as error:
        raise ValidationError(str(error)) from error
    return _api_success(result, autonomy=result, ok=True)


OVERNIGHT_MIN_CONFIDENCE = 55  # matches the confidence floor the dashboard itself uses to call something a real opportunity vs WAIT
OVERNIGHT_MAX_ORDERS_PER_RUN = 5
OVERNIGHT_ORDER_QUANTITY = 1

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
            payload = {
                "connected": True,
                "error": "",
                "balance": {
                    "account_number": cash_account.get("account_number", ""),
                    "net_liquidation_value": balance.get("total_net_liquidation_value", ""),
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


def _run_autonomous_trade_scan(user_id: str) -> Dict[str, object]:
    """Scans current setups the same way the dashboard does, and for the
    highest-confidence bullish ones places real (sandbox) DAY limit orders on
    Webull - the trading session (CORE/ALL/NIGHT) is picked automatically by
    time of day, so outside market hours these queue and fill at the next
    market open rather than executing immediately. Every order, and every
    skip, is logged with the reasoning behind it so it can be reviewed later.
    Pure function of user_id - safe to call from a real request or from the
    cron trigger's simulated per-user request context."""
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        raise ValidationError("Enter your Webull App Key and App Secret in Account Hub before running the trade scan.")

    accounts = get_accounts(user_id)
    webull_account = next((a for a in accounts if a.get("platform") == "webull"), None)
    if not webull_account or webull_account.get("status") != "Connected":
        raise ValidationError("Connect Webull in Account Hub before running the trade scan.")

    sandbox_accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    cash_account = webull_api.find_individual_cash_account(sandbox_accounts)
    if not cash_account:
        raise ValidationError("No Webull sandbox account found for these credentials.")
    account_id = cash_account["account_id"]

    risk_settings = get_autonomy_status(user_id)
    if risk_settings.get("emergency_stop_enabled"):
        raise ValidationError("Emergency stop is enabled - reset it in Account Hub before running the scan.")

    daily_loss_limit = float(risk_settings.get("daily_loss_limit", 0) or 0)
    if daily_loss_limit > 0:
        balance = webull_api.get_account_balance(creds["app_key"], creds["app_secret"], account_id)
        day_pnl = float(balance.get("total_day_profit_loss", 0) or 0)
        if day_pnl <= -daily_loss_limit:
            raise ValidationError(
                f"Daily loss limit reached (today's P/L ${day_pnl:.2f} vs -${daily_loss_limit:.2f} limit). No new trades until tomorrow."
            )

    max_positions = int(risk_settings.get("max_positions", 0) or 0)
    open_position_count = len(webull_api.get_account_positions(creds["app_key"], creds["app_secret"], account_id))
    available_position_slots = max(0, max_positions - open_position_count) if max_positions > 0 else OVERNIGHT_MAX_ORDERS_PER_RUN

    max_trade_size = float(risk_settings.get("max_trade_size", 0) or 0)

    context = _build_page_context(include_reversal=True, include_trend=True)
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
        if str(opp.get("recommendation", "")).upper() == "CALL"
        and int(opp.get("confidence", 0) or 0) >= OVERNIGHT_MIN_CONFIDENCE
        and str(opp.get("ticker", "")).upper() not in already_placed_today
    ]
    qualifying.sort(key=lambda opp: int(opp.get("confidence", 0) or 0), reverse=True)
    candidates = qualifying[: min(OVERNIGHT_MAX_ORDERS_PER_RUN, available_position_slots)]

    placed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []

    for opp in opportunities:
        if opp in candidates:
            continue
        if opp in qualifying:
            reason = f"max_positions limit reached ({open_position_count}/{max_positions} open)" if max_positions > 0 else "no position slots available"
        elif str(opp.get("recommendation", "")).upper() == "CALL":
            reason = f"confidence {opp.get('confidence')} below {OVERNIGHT_MIN_CONFIDENCE} threshold"
        else:
            reason = f"recommendation is {opp.get('recommendation')}, only CALL/bullish setups auto-order tonight"
        skipped.append(
            {
                "ticker": opp.get("ticker"),
                "recommendation": opp.get("recommendation"),
                "confidence": opp.get("confidence"),
                "reason_skipped": reason,
            }
        )

    for candidate_index, opp in enumerate(candidates):
        if candidate_index > 0:
            time.sleep(1.0)  # spread order placements out to avoid tripping Webull's rate limiter
        ticker = str(opp.get("ticker", ""))
        limit_price = float(opp.get("ideal_entry") or 0)

        if max_trade_size > 0 and limit_price > max_trade_size:
            skipped.append(
                {
                    "ticker": ticker,
                    "recommendation": opp.get("recommendation"),
                    "confidence": opp.get("confidence"),
                    "reason_skipped": f"share price ${limit_price:.2f} exceeds max_trade_size ${max_trade_size:.2f} risk limit",
                }
            )
            continue

        quantity = max(1, int(max_trade_size // limit_price)) if max_trade_size > 0 and limit_price > 0 else OVERNIGHT_ORDER_QUANTITY
        entry = {
            "ticker": ticker,
            "side": "BUY",
            "quantity": quantity,
            "limit_price": limit_price,
            "confidence": opp.get("confidence"),
            "trade_quality": opp.get("trade_quality"),
            "trade_thesis": opp.get("trade_thesis"),
            "why_ai_likes_it": opp.get("why_ai_likes_it"),
            "invalidation_rule": opp.get("invalidation_rule"),
            "risk_warning": opp.get("risk_warning"),
            "target": opp.get("target"),
            "stop": opp.get("stop"),
            "account_id": account_id,
            "status": "pending",
        }
        try:
            if limit_price <= 0:
                raise ValueError("No valid entry price computed for this ticker.")
            result = webull_api.place_stock_order(
                app_key=creds["app_key"],
                app_secret=creds["app_secret"],
                account_id=account_id,
                symbol=ticker,
                side="BUY",
                quantity=quantity,
                limit_price=limit_price,
                trading_session=_current_webull_trading_session(),
            )
            entry["status"] = "placed"
            entry["webull_response"] = result
            placed.append(entry)
        except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole batch
            entry["status"] = "failed"
            entry["error"] = str(error)
            skipped.append(entry)
        record_overnight_order(user_id, entry)

    return {
        "ok": True,
        "placed_count": len(placed),
        "skipped_count": len(skipped),
        "placed": placed,
        "skipped": skipped,
        "guardrail": "DAY limit orders in the Webull sandbox only. Session auto-selected by time of day.",
    }


@app.route("/api/autonomy/run-overnight-scan", methods=["POST"])
@api_guard
def api_autonomy_run_overnight_scan():
    summary = _run_autonomous_trade_scan(_current_user_id())
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


@app.route("/api/autonomy/cron-trigger", methods=["POST"])
def api_autonomy_cron_trigger():
    """Called on a timer by a Render Cron Job, not by a logged-in browser -
    authenticated by a shared secret instead of a session cookie. Runs the
    scan for every registered user currently in AUTONOMOUS mode; does nothing
    for everyone else."""
    expected_secret = os.environ.get("CRON_SECRET", "").strip()
    provided_secret = request.headers.get("X-Cron-Secret", "").strip()
    if not expected_secret or not hmac.compare_digest(expected_secret, provided_secret):
        return _api_failure("Invalid or missing cron secret.", status_code=401, error_code="unauthorized", ok=False)

    results = []
    for user_id in list_all_user_ids():
        status = get_autonomy_status(user_id)
        if str(status.get("current_mode", status.get("mode", "OFF"))).upper() != "AUTONOMOUS":
            continue
        with app.test_request_context():
            session["user_id"] = user_id
            try:
                scan_result = _run_autonomous_trade_scan(user_id)
                results.append({"user_id": user_id, "ok": True, **scan_result})
            except Exception as error:  # noqa: BLE001 - one user's failure shouldn't block others
                results.append({"user_id": user_id, "ok": False, "error": str(error)})

    return _api_success({"ran_for_users": len(results), "results": results}, ok=True, ran_for_users=len(results))


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
