from __future__ import annotations

import os
import secrets
import sys
import threading
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for, g
try:
    from flask_socketio import SocketIO
except Exception:  # pragma: no cover - fallback for environments without optional dependency
    class SocketIO:  # type: ignore[override]
        def __init__(self, _app, *args, **kwargs):
            pass

        def on(self, _event: str):
            def decorator(func):
                return func

            return decorator

        def emit(self, _event: str, _payload=None):
            return None

        def run(self, app, host="0.0.0.0", port=8000, debug=False):
            app.run(host=host, port=port, debug=debug)

from backend.services.auth_service import authenticate_user, normalize_username, register_user, user_namespace
from backend.brains.registry import BrainRegistry
from backend.scanner.registry import ScannerRegistry
from backend.services.market_data_service import (
    build_api_response,
    get_chart_data,
    get_stock_snapshot,
    normalize_ticker,
    validate_ticker,
)
from backend.services.mission_assignment_service import (
    build_mission_summary,
    build_mission_timeline,
    load_user_store,
    save_user_store,
    upsert_mission,
    utc_now_iso,
)
from backend.services.mission_refresh_worker import MissionRefreshWorker
from backend.services.live_market_stream import MarketStreamService
from backend.services.paper_engine import InternalPaperEngine, OrderMonitorWorker, RETRYABLE_REJECTIONS
from backend.services.storage import StorageCorruptionError
from backend.services.market_scanner_engine import MarketScannerEngine
from backend.services.research_service import ResearchService
from backend.services.symbol_universe_service import SymbolUniverseService
from backend.services.tradingview_signals import TradingViewSignalService

DEFAULT_TICKER = "SPY"

SCANNER_ROUTES = {
    "overview": "overview",
    "movers": "movers",
    "volume": "volume",
    "reversals": "reversals",
    "breakouts": "breakouts",
    "extended-hours": "extended-hours",
    "futures": "futures",
    "sector-rotation": "sector-rotation",
    "watchlist": "watchlist",
    "upcoming": "upcoming",
}

SCANNER_PAGE_ORDER = [
    "overview",
    "movers",
    "volume",
    "reversals",
    "breakouts",
    "extended-hours",
    "futures",
    "sector-rotation",
    "watchlist",
    "upcoming",
]

BRAIN_ROUTES = {
    "overview": "overview",
    "candle": "candle",
    "pattern": "pattern",
    "trend": "trend",
    "volume": "volume",
    "support-resistance": "support-resistance",
    "strategy": "strategy",
    "options": "options",
    "risk": "risk",
    "market-readiness": "market-readiness",
    "confidence": "confidence",
    "support": "support-resistance",
    "resistance": "support-resistance",
}

ASSET_TYPES = ["Stock", "ETF", "Index", "Futures", "Crypto (Future)", "Forex (Future)"]
MISSION_TYPES = ["Research", "Day Trade", "Swing Trade", "Long Term", "Options", "Futures", "Scalp", "AI Discovery"]
PRIORITIES = ["★★★★★ Critical", "★★★★ High", "★★★ Medium", "★★ Low", "★ Low Priority"]

SCANNER_ASSIGNMENTS = [
    "Scanner Center",
    "Market Movers",
    "Volume Scanner",
    "Breakout Scanner",
    "Breakdown Scanner",
    "Reversal Scanner",
    "Extended Hours Scanner",
    "Premarket Scanner",
    "Futures Scanner",
]

BRAIN_ASSIGNMENTS = [
    "Pattern Brain",
    "Trend Brain",
    "Strategy Brain",
    "Support & Resistance Brain",
    "Risk Brain",
    "Confidence Engine",
    "Options Brain",
    "Market Readiness",
    "Trade Thesis",
    "Upcoming Opportunities",
    "Mission Queue",
]

MONITORING_FLAGS = [
    "Monitor Continuously",
    "Include in Daily Mission Brief",
    "Include in Market Readiness",
    "Notify on Breakout",
    "Notify on Breakdown",
    "Notify on New High",
    "Notify on New Low",
    "Notify on Volume Spike",
    "Notify on Relative Volume",
    "Notify on Earnings",
    "Notify on News",
    "Notify on AI Confidence Increase",
    "Notify when Trade Thesis Changes",
    "Notify when Risk Changes",
]

QUICK_ACTIONS = [
    "Analyze Everywhere",
    "Scanner Only",
    "Brains Only",
    "Options Only",
    "Futures Only",
    "Custom Selection",
]

FUTURES_SYMBOLS = ["ES=F", "NQ=F", "YM=F", "RTY=F", "CL=F", "GC=F"]

_MARKET_STREAM_SINGLETON: MarketStreamService | None = None
_MARKET_STREAM_SINGLETON_PID: int | None = None
_MARKET_STREAM_LOCK = threading.Lock()
_RELIABILITY_LOGGER = logging.getLogger("plutotrade.reliability")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _is_test_runtime() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    if "pytest" in sys.modules:
        return True
    return False


def _is_admin_mode() -> bool:
    return os.getenv("PLUTO_ADMIN_MODE", "false").lower() in {"1", "true", "yes", "on"}


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


def _json_error_response(code: int, message: str, error_kind: str = "UNAVAILABLE"):
    payload = build_api_response(
        False,
        data={"error_kind": error_kind, "path": request.path},
        error=message,
        data_status="unavailable",
        provider="PlutoTradeAI",
    )
    return jsonify(payload), code


def _status_from_error_kind(error_kind: str) -> int:
    mapping = {
        "INVALID_TICKER": 400,
        "UNSUPPORTED_SECURITY": 404,
        "PROVIDER_OUTAGE": 503,
        "TEMPORARY_TIMEOUT": 504,
    }
    return mapping.get(error_kind, 404)


def _build_market_stream(socketio: SocketIO) -> MarketStreamService:
    global _MARKET_STREAM_SINGLETON, _MARKET_STREAM_SINGLETON_PID

    if _is_test_runtime():
        return MarketStreamService(socketio=socketio)

    with _MARKET_STREAM_LOCK:
        current_pid = os.getpid()
        if _MARKET_STREAM_SINGLETON is None or _MARKET_STREAM_SINGLETON_PID != current_pid:
            _MARKET_STREAM_SINGLETON = MarketStreamService(socketio=socketio)
            _MARKET_STREAM_SINGLETON_PID = current_pid
        else:
            _MARKET_STREAM_SINGLETON.bind_socketio(socketio)

    return _MARKET_STREAM_SINGLETON


def _is_authenticated() -> bool:
    return bool(session.get("pluto_authenticated", False)) or _is_admin_mode()


def _current_username() -> str:
    return normalize_username(session.get("pluto_user") or "")


def _current_namespace() -> str:
    if _is_authenticated():
        return user_namespace(_current_username() or session.get("pluto_user") or "guest")
    return "guest"


def _session_missions() -> List[Dict[str, Any]]:
    if "session_missions" not in session:
        session["session_missions"] = []
    return session["session_missions"]


def _dashboard_mission_brief(summary: Dict[str, Any], missions: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = summary.get("active_missions", 0)
    needing_attention = min(active, max(1, active // 4)) if active else 0
    strengthened = min(active, max(0, active // 5)) if active else 0
    invalidated = 1 if active >= 6 else 0

    highest = None
    if missions:
        highest = sorted(
            missions,
            key=lambda item: (
                1 if "Critical" in (item.get("priority") or "") else 0,
                (item.get("confidence_history") or [{}])[-1].get("value") or 0,
            ),
            reverse=True,
        )[0]

    highest_payload = {
        "ticker": highest.get("ticker") if highest else DEFAULT_TICKER,
        "confidence": (highest.get("confidence_history") or [{"value": 0}])[-1].get("value") if highest else 0,
        "recommended": (highest.get("options_history") or [{"direction": "WAIT"}])[-1].get("direction") if highest else "WAIT",
    }

    return {
        "overnight_monitored": active,
        "require_attention": needing_attention,
        "setups_strengthened": strengthened,
        "setups_invalidated": invalidated,
        "highest_priority_mission": highest_payload,
        "generated_at": _utc_now_iso(),
    }


def _mission_store() -> Dict[str, Any]:
    namespace = _current_namespace()
    if _is_authenticated():
        return load_user_store(namespace)
    return {"missions": _session_missions()}


def _save_mission_store(store: Dict[str, Any]) -> None:
    namespace = _current_namespace()
    if _is_authenticated():
        save_user_store(namespace, store)
    else:
        session["session_missions"] = store.get("missions", [])
        session.modified = True


def _find_mission_by_ticker(ticker: str) -> Dict[str, Any] | None:
    normalized = normalize_ticker(ticker)
    store = _mission_store()
    for mission in store.get("missions", []):
        if normalize_ticker(mission.get("ticker", "")) == normalized:
            return mission
    return None


def _build_mission_profile(payload: Dict[str, Any], snapshot: Dict[str, Any], overview: Dict[str, Any], scanner_overview: Dict[str, Any]) -> Dict[str, Any]:
    overview_data = overview.get("data", {}) if overview.get("success") else {}
    scanner_data = scanner_overview.get("data", {}) if scanner_overview.get("success") else {}
    risk_ratio = overview_data.get("risk_score")

    return {
        "ticker": snapshot.get("ticker"),
        "company": snapshot.get("company") or snapshot.get("ticker"),
        "priority": payload.get("priority") or "★★★ Medium",
        "mission_type": payload.get("mission_type") or "Research",
        "asset_type": payload.get("asset_type") or snapshot.get("asset_type") or "Stock",
        "assigned_scanners": payload.get("assigned_scanners", []),
        "assigned_brains": payload.get("assigned_brains", []),
        "monitoring_flags": payload.get("monitoring_flags", []),
        "quick_action": payload.get("quick_action") or "Analyze Everywhere",
        "confidence_history": [{"timestamp": utc_now_iso(), "value": overview_data.get("confidence")}],
        "risk_history": [{"timestamp": utc_now_iso(), "value": risk_ratio}],
        "trade_thesis_history": [{"timestamp": utc_now_iso(), "value": overview_data.get("trade_thesis")}],
        "support_history": [{"timestamp": utc_now_iso(), "value": overview_data.get("support")}],
        "resistance_history": [{"timestamp": utc_now_iso(), "value": overview_data.get("resistance")}],
        "options_history": [{
            "timestamp": utc_now_iso(),
            "direction": overview_data.get("call_put_wait_bias"),
            "strategy": overview_data.get("recommended_strategy"),
        }],
        "scanner_results": scanner_data.get("rows", [])[:5],
        "last_ai_update": utc_now_iso(),
        "last_market_update": snapshot.get("last_updated") or utc_now_iso(),
        "notes": payload.get("notes") or "",
        "mission_status": "Active",
    }


def _futures_analysis(ticker: str, brain_registry: BrainRegistry) -> Dict[str, Any]:
    snapshot_response = get_stock_snapshot(ticker)
    if not snapshot_response.get("success"): 
        return {
            "ticker": ticker,
            "success": False,
            "error": snapshot_response.get("error") or "Futures data unavailable",
            "data_status": snapshot_response.get("data_status", "unavailable"),
            "provider": snapshot_response.get("provider", "Yahoo Finance"),
            "timestamp": utc_now_iso(),
        }

    snapshot = snapshot_response["data"]
    overview = brain_registry.run_overview(ticker)
    trend = brain_registry.run("trend", ticker)
    levels = brain_registry.run("support-resistance", ticker)
    risk = brain_registry.run("risk", ticker)

    overview_data = overview.get("data", {}) if overview.get("success") else {}
    trend_data = trend.get("data", {}) if trend.get("success") else {}
    level_data = levels.get("data", {}) if levels.get("success") else {}
    risk_data = risk.get("data", {}) if risk.get("success") else {}

    suggested_direction = "WAIT"
    if overview_data.get("call_put_wait_bias") == "CALL" or trend_data.get("trend") == "Uptrend":
        suggested_direction = "LONG"
    elif overview_data.get("call_put_wait_bias") == "PUT" or trend_data.get("trend") == "Downtrend":
        suggested_direction = "SHORT"

    return {
        "ticker": snapshot.get("ticker"),
        "company": snapshot.get("company"),
        "asset_type": snapshot.get("asset_type"),
        "current_price": snapshot.get("current_price"),
        "market_session": snapshot.get("market_session"),
        "data_provider": snapshot_response.get("provider"),
        "timestamp": snapshot_response.get("timestamp"),
        "data_status": snapshot_response.get("data_status"),
        "trend": trend_data.get("trend"),
        "support": level_data.get("support"),
        "resistance": level_data.get("resistance"),
        "breakout": level_data.get("distance_to_resistance_percent"),
        "volume": snapshot.get("volume"),
        "relative_volume": snapshot.get("relative_volume"),
        "suggested_direction": suggested_direction,
        "risk": risk_data.get("risk_status"),
        "risk_reward_ratio": risk_data.get("risk_reward_ratio"),
        "confidence": overview_data.get("confidence"),
        "trade_thesis": overview_data.get("trade_thesis"),
        "invalidation": overview_data.get("stop_invalidation") or overview_data.get("stop_loss"),
        "reasoning": overview_data.get("recommended_strategy"),
        "why": {
            "trend": trend_data.get("trend"),
            "volume": snapshot.get("relative_volume"),
            "support": level_data.get("support"),
            "resistance": level_data.get("resistance"),
            "risk": risk_data.get("risk_status"),
            "invalidation": overview_data.get("stop_invalidation") or overview_data.get("stop_loss"),
        },
    }


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv("PLUTO_SECRET_KEY") or secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("PLUTO_MAX_REQUEST_BYTES", str(256 * 1024)))
    if not os.getenv("PLUTO_SECRET_KEY") and not _is_test_runtime():
        _RELIABILITY_LOGGER.warning("PLUTO_SECRET_KEY is not set: a random key is used, so logins are lost on every restart and differ per worker")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("PLUTO_SESSION_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("PLUTO_SESSION_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=int(os.getenv("PLUTO_SESSION_DAYS", "7")))

    brain_registry = BrainRegistry()
    paper_engine = InternalPaperEngine()
    universe_service = SymbolUniverseService()
    research_service = ResearchService(universe_service, brain_registry)
    scanner_engine = MarketScannerEngine(universe_service, research_service)

    def _analyze_tradingview_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
        ticker = normalize_ticker(signal.get("symbol") or "")
        snapshot = get_stock_snapshot(ticker)
        overview = brain_registry.run_overview(ticker)
        trend = brain_registry.run("trend", ticker)
        confidence = brain_registry.run("confidence", ticker)
        risk = brain_registry.run("risk", ticker)

        snapshot_data = snapshot.get("data", {}) if snapshot.get("success") else {}
        overview_data = overview.get("data", {}) if overview.get("success") else {}
        trend_data = trend.get("data", {}) if trend.get("success") else {}
        confidence_data = confidence.get("data", {}) if confidence.get("success") else {}
        risk_data = risk.get("data", {}) if risk.get("success") else {}

        data_freshness = (snapshot_data.get("market_data_status") or snapshot.get("data_status") or "UNAVAILABLE").upper()
        direction_hint = trend_data.get("trend")
        if direction_hint == "Uptrend":
            market_direction = "BULLISH"
        elif direction_hint == "Downtrend":
            market_direction = "BEARISH"
        else:
            market_direction = "NEUTRAL"

        recommended_entry = overview_data.get("entry_zone")
        signal_price = signal.get("price")
        entry_zone = recommended_entry if recommended_entry is not None else signal_price

        stop_loss = signal.get("stop_loss")
        if stop_loss is None:
            stop_loss = overview_data.get("stop_invalidation")

        targets = overview_data.get("targets") if isinstance(overview_data.get("targets"), list) else []
        primary_target = signal.get("take_profit") if signal.get("take_profit") is not None else (targets[0] if targets else None)
        secondary_target = targets[1] if len(targets) > 1 else None

        warning_flags: List[str] = []
        if data_freshness in {"STALE", "UNAVAILABLE"}:
            warning_flags.append("BACKEND_DATA_NOT_FRESH")
        if not snapshot.get("success"):
            warning_flags.append("SNAPSHOT_UNAVAILABLE")
        if not overview.get("success"):
            warning_flags.append("OVERVIEW_ANALYSIS_UNAVAILABLE")

        return {
            "market_direction": market_direction,
            "confidence_score": confidence_data.get("overall") or overview_data.get("confidence"),
            "entry_zone": entry_zone,
            "stop_loss": stop_loss,
            "primary_target": primary_target,
            "secondary_target": secondary_target,
            "risk_reward_ratio": risk_data.get("risk_reward_ratio") or overview_data.get("risk_score"),
            "invalidation_condition": stop_loss,
            "timeframe": signal.get("timeframe"),
            "rationale": overview_data.get("trade_thesis") or overview_data.get("recommended_strategy") or "Analysis available with limited context",
            "warning_flags": warning_flags,
            "warnings": warning_flags,
            "data_freshness": data_freshness,
            "data_source": {
                "chart_provider": os.getenv("PLUTO_CHART_PROVIDER", "tradingview"),
                "backend_market_provider": os.getenv("PLUTO_MARKET_PROVIDER", "yfinance"),
                "signal_source": "TRADINGVIEW",
            },
        }

    signal_service = TradingViewSignalService(analyzer=_analyze_tradingview_signal)

    @app.before_request
    def _capture_request_start():
        g.request_started_at = time.perf_counter()

    @app.after_request
    def _log_failed_api_response(response):
        if not _is_api_request():
            return response

        elapsed_ms = None
        started = getattr(g, "request_started_at", None)
        if started is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        if response.status_code >= 400:
            ticker = request.view_args.get("ticker") if isinstance(request.view_args, dict) else None
            symbol = request.view_args.get("symbol") if isinstance(request.view_args, dict) else None
            _RELIABILITY_LOGGER.warning(
                "api_failure endpoint=%s status=%s ticker=%s symbol=%s provider=%s latency_ms=%s timestamp=%s",
                request.path,
                response.status_code,
                ticker,
                symbol,
                response.headers.get("X-Provider", "PlutoTradeAI"),
                elapsed_ms,
                _utc_now_iso(),
            )
        return response

    worker = None
    order_monitor_worker = None
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    market_stream = _build_market_stream(socketio)
    signal_sync_processing = _is_test_runtime() or _is_truthy(os.getenv("PLUTO_SIGNAL_SYNC_PROCESSING", "false"))
    if not _is_test_runtime() and os.getenv("PLUTO_DISABLE_MARKET_STREAM", "false").lower() not in {"1", "true", "yes", "on"}:
        market_stream.start()
    if not signal_sync_processing:
        signal_service.start_worker()

    def _recover_interrupted_work() -> None:
        """After a restart: re-queue signals whose analysis never finished and settle approvals
        that were claimed but whose execution outcome was never recorded."""
        try:
            recovered = signal_service.recover_pending(process_inline=signal_sync_processing)
            if recovered:
                _RELIABILITY_LOGGER.warning("recovered %s signal(s) awaiting analysis: %s", len(recovered), ", ".join(recovered[:10]))
            now = datetime.now(timezone.utc)
            for row in signal_service.list_signals(limit=500):
                if row.get("status") != "APPROVED":
                    continue
                approved_at = (row.get("approval") or {}).get("timestamp")
                try:
                    age = (now - datetime.fromisoformat(str(approved_at))).total_seconds()
                except (TypeError, ValueError):
                    age = 3600.0
                if age < 30:
                    continue  # another process may still be executing this approval
                lifecycle = paper_engine.lifecycle_for_signal(str(row.get("signal_id")))
                if lifecycle:
                    signal_service.finish_execution(str(row["signal_id"]), executed=True, message="", position=lifecycle)
                else:
                    signal_service.revert_interrupted_approval(str(row["signal_id"]), "approval was interrupted before execution; review and approve again")
        except StorageCorruptionError as exc:
            _RELIABILITY_LOGGER.critical("startup recovery skipped, state file is corrupted: %s", exc)
        except Exception:  # noqa: BLE001 - recovery must never prevent the app from starting
            _RELIABILITY_LOGGER.exception("startup recovery failed")

    _recover_interrupted_work()

    if not _is_test_runtime() and os.getenv("PLUTO_ENABLE_ORDER_MONITOR", "true").lower() in {"1", "true", "yes", "on"}:
        order_monitor_worker = OrderMonitorWorker(
            engine=paper_engine,
            interval_seconds=int(os.getenv("PLUTO_ORDER_MONITOR_INTERVAL_SECONDS", "45")),
        )
        if os.getenv("PLUTO_ROLE", "web").lower() == "web":
            order_monitor_worker.start()

    @socketio.on("connect")
    def socket_connect():
        status = market_stream.connection_status()
        socketio.emit("connection_status", status)

    @socketio.on("disconnect")
    def socket_disconnect():
        # No-op handler keeps disconnect telemetry explicit and prevents duplicate listener warnings.
        return None

    @app.context_processor
    def inject_navigation() -> Dict[str, Any]:
        nav_groups = [
            {
                "name": "Mission Control",
                "href": url_for("dashboard"),
                "children": [
                    {"name": "Mission Control", "href": url_for("dashboard")},
                ],
            },
            {
                "name": "Market Intelligence",
                "href": url_for("scanners_overview"),
                "children": [
                    {"name": "Scanner Center", "href": url_for("scanners_overview")},
                    {"name": "Market Movers", "href": url_for("scanners_workspace", scanner_name="movers")},
                    {"name": "Volume", "href": url_for("scanners_workspace", scanner_name="volume")},
                    {"name": "Breakouts", "href": url_for("scanners_workspace", scanner_name="breakouts")},
                    {"name": "Reversals", "href": url_for("scanners_workspace", scanner_name="reversals")},
                    {"name": "Extended Hours", "href": url_for("scanners_workspace", scanner_name="extended-hours")},
                    {"name": "Futures", "href": url_for("scanners_workspace", scanner_name="futures")},
                    {"name": "Sector Rotation", "href": url_for("scanners_workspace", scanner_name="sector-rotation")},
                ],
            },
            {
                "name": "AI Intelligence",
                "href": url_for("brains_overview"),
                "children": [
                    {"name": "Brain Center", "href": url_for("brains_overview")},
                    {"name": "Pattern Brain", "href": url_for("brains_workspace", brain_name="pattern")},
                    {"name": "Strategy", "href": url_for("brains_workspace", brain_name="strategy")},
                    {"name": "Candle Brain", "href": url_for("brains_workspace", brain_name="candle")},
                    {"name": "Trend Brain", "href": url_for("brains_workspace", brain_name="trend")},
                    {"name": "Risk", "href": url_for("brains_workspace", brain_name="risk")},
                    {"name": "Confidence Engine", "href": url_for("brains_workspace", brain_name="confidence")},
                    {"name": "Market Readiness", "href": url_for("brains_workspace", brain_name="market-readiness")},
                    {"name": "Options Brain", "href": url_for("brains_workspace", brain_name="options")},
                ],
            },
            {
                "name": "Opportunity Center",
                "href": url_for("stock_workspace", ticker=DEFAULT_TICKER),
                "children": [
                    {"name": "Mission Queue", "href": url_for("mission_control")},
                    {"name": "Upcoming Opportunities", "href": url_for("scanners_workspace", scanner_name="upcoming")},
                    {"name": "Trade Thesis", "href": url_for("brains_workspace", brain_name="strategy")},
                    {"name": "Mission Timeline", "href": url_for("mission_control")},
                    {"name": "Alerts", "href": url_for("alerts_workspace")},
                ],
            },
            {
                "name": "Portfolio",
                "href": url_for("portfolio_command"),
                "children": [
                    {"name": "Watchlist", "href": url_for("scanners_workspace", scanner_name="watchlist")},
                    {"name": "Paper Trading", "href": url_for("paper_trading_workspace")},
                    {"name": "Journal", "href": url_for("journal")},
                    {"name": "Performance", "href": url_for("performance_workspace")},
                ],
            },
            {"name": "Settings", "href": url_for("settings")},
        ]

        return {
            "nav_groups": nav_groups,
            "research_disclaimer": "Research only. No autonomous execution. No financial advice.",
            "admin_mode": _is_admin_mode(),
            "current_user": _current_username(),
            "is_authenticated": _is_authenticated(),
            "founder_preview": "Founder Preview",
            "research_only_badge": "Research Only",
            "not_financial_advice": "Not Financial Advice",
        }

    @app.get("/auth/login")
    def login_page():
        return render_template(
            "auth.html",
            mode="login",
            title="Sign In",
            subtitle="Access authenticated mission storage and persistent founder assignments.",
            action_label="Sign In",
            toggle_href=url_for("register_page"),
            toggle_label="Create Account",
        )

    @app.get("/auth/register")
    def register_page():
        return render_template(
            "auth.html",
            mode="register",
            title="Create Founder Account",
            subtitle="Register a preview account for user-scoped mission persistence.",
            action_label="Create Account",
            toggle_href=url_for("login_page"),
            toggle_label="Sign In",
        )

    @app.post("/auth/login")
    def login_submit():
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        ok, message = authenticate_user(username, password)
        if not ok:
            return render_template(
                "auth.html",
                mode="login",
                title="Sign In",
                subtitle=message,
                action_label="Sign In",
                toggle_href=url_for("register_page"),
                toggle_label="Create Account",
            ), 401

        session["pluto_authenticated"] = True
        session["pluto_user"] = normalize_username(username)
        session.modified = True
        return redirect(url_for("mission_control"))

    @app.post("/auth/register")
    def register_submit():
        if not _is_truthy(os.getenv("PLUTO_ALLOW_REGISTRATION", "true")):
            return render_template("error.html", code=403, title="Registration Closed", message="New accounts are disabled on this deployment."), 403
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            return render_template(
                "auth.html",
                mode="register",
                title="Create Founder Account",
                subtitle="Passwords do not match.",
                action_label="Create Account",
                toggle_href=url_for("login_page"),
                toggle_label="Sign In",
            ), 400

        ok, message = register_user(username, password)
        if not ok:
            return render_template(
                "auth.html",
                mode="register",
                title="Create Founder Account",
                subtitle=message,
                action_label="Create Account",
                toggle_href=url_for("login_page"),
                toggle_label="Sign In",
            ), 400

        session["pluto_authenticated"] = True
        session["pluto_user"] = normalize_username(username)
        session.modified = True
        return redirect(url_for("mission_control"))

    @app.get("/auth/logout")
    def logout():
        session.pop("pluto_authenticated", None)
        session.pop("pluto_user", None)
        return redirect(url_for("dashboard"))

    @app.get("/")
    def dashboard():
        ticker = normalize_ticker(request.args.get("ticker", DEFAULT_TICKER)) or DEFAULT_TICKER
        store = _mission_store()
        combined = store.get("missions", [])
        if not _is_authenticated():
            combined = combined + _session_missions()
        summary = build_mission_summary(combined)
        mission_brief = _dashboard_mission_brief(summary, combined)
        return render_template("dashboard.html", ticker=ticker, mission_brief=mission_brief)

    @app.get("/dashboard")
    def dashboard_alias():
        return redirect(url_for("dashboard"))

    @app.get("/missions")
    def mission_control():
        ticker = normalize_ticker(request.args.get("ticker", DEFAULT_TICKER)) or DEFAULT_TICKER
        return render_template("missions.html", ticker=ticker)

    @app.get("/portfolio")
    def portfolio_command():
        return render_template("portfolio.html", ticker=DEFAULT_TICKER)

    @app.get("/account")
    def account_hub():
        return render_template("workspace_section.html", title="Account Hub", subtitle="Manage preview account settings and authenticated mission persistence.")

    @app.get("/portfolio/paper-trading")
    def paper_trading_workspace():
        return render_template("paper_trading.html", ticker=DEFAULT_TICKER)

    @app.get("/portfolio/performance")
    def performance_workspace():
        return render_template("performance.html", ticker=DEFAULT_TICKER)

    @app.get("/opportunities/alerts")
    def alerts_workspace():
        return render_template("alerts.html", ticker=DEFAULT_TICKER)

    @app.get("/journal")
    def journal():
        return render_template("workspace_section.html", title="Journal", subtitle="Review mission notes and research logs from command-center workflows.")

    @app.get("/settings")
    def settings():
        return render_template("workspace_section.html", title="Settings", subtitle="Control preview behavior, safety modes, and display preferences.")

    @app.get("/stock")
    def stock_redirect():
        ticker = normalize_ticker(request.args.get("ticker", DEFAULT_TICKER)) or DEFAULT_TICKER
        return redirect(url_for("stock_workspace", ticker=ticker))

    @app.get("/stock/<ticker>")
    def stock_workspace(ticker: str):
        normalized = normalize_ticker(ticker)
        return render_template("stock_workspace.html", ticker=normalized)

    @app.get("/scanners")
    def scanners_overview():
        return render_template("scanners.html", active_scanner="overview", scanners=SCANNER_PAGE_ORDER)

    @app.get("/scanners/<scanner_name>")
    def scanners_workspace(scanner_name: str):
        normalized = scanner_name.lower()
        active_scanner = SCANNER_ROUTES.get(normalized)
        if not active_scanner:
            return render_template("scanners.html", active_scanner="overview", scanners=SCANNER_PAGE_ORDER), 404

        return render_template("scanners.html", active_scanner=active_scanner, scanners=SCANNER_PAGE_ORDER)

    @app.get("/brains")
    def brains_overview():
        return render_template("brains.html", active_brain="overview", ticker=DEFAULT_TICKER)

    @app.get("/futures")
    def futures_command():
        return render_template("futures.html", ticker=DEFAULT_TICKER, futures_symbols=FUTURES_SYMBOLS)

    @app.get("/brains/<brain_name>")
    def brains_workspace(brain_name: str):
        normalized = brain_name.lower()
        active_brain = BRAIN_ROUTES.get(normalized)
        if not active_brain:
            return render_template("brains.html", active_brain="overview", ticker=DEFAULT_TICKER), 404

        ticker = normalize_ticker(request.args.get("ticker", DEFAULT_TICKER)) or DEFAULT_TICKER
        return render_template("brains.html", active_brain=active_brain, ticker=ticker)

    @app.get("/missions/<ticker>")
    def mission_symbol_view(ticker: str):
        normalized = normalize_ticker(ticker)
        return render_template("missions.html", ticker=normalized)

    @app.get("/api/health")
    def api_health():
        return jsonify(
            {
                "success": True,
                "data": {
                    "status": "ok" if paper_engine.storage_health()["ok"] else "degraded",
                    "deployment": "render-ready",
                    "paper_storage": paper_engine.storage_health(),
                },
                "error": None,
                "timestamp": _utc_now_iso(),
                "data_status": "live",
                "provider": "PlutoTradeAI",
            }
        )

    @app.get("/api/status")
    def api_status():
        return api_health()

    @app.get("/api/runtime/config")
    def api_runtime_config():
        universe_service.ensure_fresh()
        payload = {
            "signal_provider": os.getenv("PLUTO_SIGNAL_PROVIDER", "tradingview").upper(),
            "chart_provider": os.getenv("PLUTO_CHART_PROVIDER", "tradingview").upper(),
            "market_provider": os.getenv("PLUTO_MARKET_PROVIDER", "yfinance").upper(),
            "paper_broker": os.getenv("PLUTO_PAPER_BROKER", "internal").upper(),
            "execution_provider": os.getenv("PLUTO_EXECUTION_PROVIDER", "disabled").upper(),
            "auto_paper_execution": paper_engine.auto_execution_enabled(),
            "autonomy_enabled": paper_engine.autonomy_enabled(),
            "order_monitor": paper_engine.monitor_status(),
            "providers": research_service.provider_map(),
            "symbol_universe": universe_service.status(),
        }
        return jsonify(build_api_response(True, data=payload, data_status="cached", provider="PlutoTradeAI"))

    def _api_error(message: str, code: int, data: Dict[str, Any] | None = None):
        return jsonify(build_api_response(False, error=message, data=data, data_status="unavailable", provider="PlutoTradeAI")), code

    def _operator_name() -> str:
        return _current_username() or "admin-mode"

    def _require_operator():
        """State-changing endpoints need a signed-in user (or admin mode); PLUTO_APPROVER_USERS narrows that further."""
        if not _is_authenticated():
            return _api_error("Authentication required", 401)
        allowed = {normalize_username(name) for name in os.getenv("PLUTO_APPROVER_USERS", "").split(",") if name.strip()}
        if allowed and not _is_admin_mode() and _current_username() not in allowed:
            return _api_error("Your account is not permitted to approve or change paper trades", 403)
        return None

    def _json_object_body():
        """Empty body -> {}; anything else must be a JSON object. Returns (body, error_response)."""
        raw = request.get_data(cache=True)
        if not raw or not raw.strip():
            return {}, None
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, _api_error("Request body must be a JSON object", 400)
        return body, None

    def _text_field(body: Dict[str, Any], key: str, limit: int = 500):
        value = body.get(key)
        if value is None:
            return None, None
        if not isinstance(value, str) or len(value) > limit:
            return None, _api_error(f"{key} must be a string of at most {limit} characters", 400)
        return value.strip(), None

    @app.post("/api/webhooks/tradingview")
    def api_webhook_tradingview():
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify(build_api_response(False, error="Malformed JSON payload", data_status="unavailable", provider="PlutoTradeAI")), 400

        if not isinstance(payload, dict):
            return jsonify(build_api_response(False, error="JSON payload must be an object", data_status="unavailable", provider="PlutoTradeAI")), 400

        enriched = dict(payload)
        enriched["_header_secret"] = request.headers.get("X-Pluto-Webhook-Secret", "")
        ok, code, response = signal_service.normalize_and_store(enriched)
        if not ok:
            return jsonify(build_api_response(False, error=response.get("error", "Webhook rejected"), data=response, data_status="unavailable", provider="PlutoTradeAI")), code

        signal_id = response.get("data", {}).get("signal_id")
        if signal_sync_processing and signal_id:
            signal_service.process_signal(signal_id)

        return jsonify(build_api_response(True, data=response.get("data", {}), data_status="cached", provider="TradingViewWebhook")), code

    @app.get("/api/signals")
    def api_signals_list():
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            limit = 100
        rows = signal_service.list_signals(limit=limit)
        return jsonify(build_api_response(True, data={"signals": rows}, data_status="cached", provider="PlutoTradeAI"))

    @app.get("/api/signals/<signal_id>")
    def api_signal_detail(signal_id: str):
        row = signal_service.get_signal(signal_id)
        if not row:
            return jsonify(build_api_response(False, error="Signal not found", data_status="unavailable", provider="PlutoTradeAI")), 404
        return jsonify(build_api_response(True, data=row, data_status="cached", provider="PlutoTradeAI"))

    @app.post("/api/signals/<signal_id>/approve")
    def api_signal_approve(signal_id: str):
        denied = _require_operator()
        if denied:
            return denied
        body, error = _json_object_body()
        if error:
            return error
        note, error = _text_field(body, "note")
        if error:
            return error
        if body.get("acknowledge_stale") not in (None, True, False):
            return _api_error("acknowledge_stale must be true or false", 400)
        approver = _operator_name()

        outcome, row, message = signal_service.begin_approval(signal_id, approver, note, acknowledge_stale=body.get("acknowledge_stale") is True)
        if outcome == "NOT_FOUND":
            return _api_error(message, 404)
        if outcome == "ALREADY_EXECUTED":
            return jsonify(build_api_response(True, data={"signal": row, "paper_position": (row or {}).get("paper_position"), "message": message, "idempotent": True}, data_status="cached", provider="PlutoTradeAI"))
        if outcome == "STALE_DATA":
            return _api_error(message, 409, {"code": "STALE_DATA", "signal_id": signal_id})
        if outcome in {"NOT_READY", "CONFLICT", "EXPIRED"}:
            return _api_error(message, 409, {"code": outcome, "signal_id": signal_id})

        try:
            executed, message, position = paper_engine.execute_approved_signal(row, approved_by=approver, validate_market=True)
        except Exception as exc:  # noqa: BLE001 - never leave a claimed approval dangling on an unexpected failure
            _RELIABILITY_LOGGER.exception("approval execution failed for %s", signal_id)
            signal_service.finish_execution(signal_id, executed=False, message=f"execution error: {type(exc).__name__}", retryable=True)
            status = 503 if isinstance(exc, StorageCorruptionError) else 500
            return _api_error("Execution failed unexpectedly; the approval was released, check the ledger before retrying", status, {"signal_id": signal_id})

        if not executed:
            retryable = message in RETRYABLE_REJECTIONS
            signal_service.finish_execution(signal_id, executed=False, message=message, retryable=retryable)
            return _api_error(message, 422, {"signal_id": signal_id, "retryable": retryable})

        executed_row = signal_service.finish_execution(signal_id, executed=True, message=message, position=position)
        return jsonify(build_api_response(True, data={"signal": executed_row, "paper_position": position, "message": message}, data_status="cached", provider="PlutoTradeAI"))

    @app.post("/api/signals/<signal_id>/decline")
    def api_signal_decline(signal_id: str):
        denied = _require_operator()
        if denied:
            return denied
        body, error = _json_object_body()
        if error:
            return error
        reason, error = _text_field(body, "reason")
        if error:
            return error
        if reason is None:
            reason, error = _text_field(body, "note")
            if error:
                return error
        outcome, row, message = signal_service.decline(signal_id, _operator_name(), reason or "No rationale provided")
        if outcome == "NOT_FOUND":
            return _api_error(message, 404)
        if outcome == "CONFLICT":
            return _api_error(message, 409, {"code": "CONFLICT", "signal_id": signal_id})
        return jsonify(build_api_response(True, data={"signal": row, "message": message, "idempotent": outcome == "ALREADY_DECLINED"}, data_status="cached", provider="PlutoTradeAI"))

    @app.get("/api/paper/account")
    def api_paper_account():
        return jsonify(build_api_response(True, data=paper_engine.account(), data_status="cached", provider="PlutoTradeInternal"))

    @app.get("/api/paper/positions")
    def api_paper_positions():
        return jsonify(build_api_response(True, data={"positions": paper_engine.positions()}, data_status="cached", provider="PlutoTradeInternal"))

    @app.get("/api/paper/trades")
    def api_paper_trades():
        return jsonify(build_api_response(True, data={"trades": paper_engine.trades()}, data_status="cached", provider="PlutoTradeInternal"))

    @app.get("/api/paper/alerts")
    def api_paper_alerts():
        return jsonify(build_api_response(True, data={"alerts": paper_engine.alerts()}, data_status="cached", provider="PlutoTradeInternal"))

    @app.post("/api/paper/alerts/<alert_id>/dismiss")
    def api_paper_alert_dismiss(alert_id: str):
        denied = _require_operator()
        if denied:
            return denied
        if not paper_engine.dismiss_alert(alert_id):
            return jsonify(build_api_response(False, error="Alert not found", data_status="unavailable", provider="PlutoTradeInternal")), 404
        return jsonify(build_api_response(True, data={"alert_id": alert_id, "dismissed": True}, data_status="cached", provider="PlutoTradeInternal"))

    @app.post("/api/paper/alerts/<alert_id>/resolve")
    def api_paper_alert_resolve(alert_id: str):
        denied = _require_operator()
        if denied:
            return denied
        body, error = _json_object_body()
        if error:
            return error
        note, error = _text_field(body, "note")
        if error:
            return error
        if not paper_engine.resolve_alert(alert_id, resolved_by=_operator_name(), note=note or ""):
            return jsonify(build_api_response(False, error="Alert not found or already resolved", data_status="unavailable", provider="PlutoTradeInternal")), 404
        return jsonify(build_api_response(True, data={"alert_id": alert_id, "resolved": True, "monitor": paper_engine.monitor_status()}, data_status="cached", provider="PlutoTradeInternal"))

    @app.get("/api/paper/monitor")
    def api_paper_monitor_status():
        return jsonify(build_api_response(True, data=paper_engine.monitor_status(), data_status="cached", provider="PlutoTradeInternal"))

    @app.post("/api/paper/monitor/run")
    def api_paper_monitor_run():
        expected = (os.getenv("PLUTO_MONITOR_TRIGGER_TOKEN", "")).strip()
        provided = (request.headers.get("X-Pluto-Monitor-Token") or "").strip()
        if expected:
            if not provided or not secrets.compare_digest(provided, expected):
                return jsonify(build_api_response(False, error="Unauthorized monitor trigger", data_status="unavailable", provider="PlutoTradeInternal")), 401
        elif not _is_authenticated():
            return jsonify(build_api_response(False, error="Authentication required", data_status="unavailable", provider="PlutoTradeInternal")), 401
        result = paper_engine.monitor_once()
        return jsonify(build_api_response(True, data=result, data_status="live", provider="PlutoTradeInternal"))

    @app.get("/api/paper/performance")
    def api_paper_performance():
        return jsonify(build_api_response(True, data=paper_engine.performance(), data_status="cached", provider="PlutoTradeInternal"))

    @app.get("/api/market/status")
    def api_market_status():
        status = market_stream.connection_status()
        universe_service.ensure_fresh()
        scanner_snapshot = scanner_engine.snapshot(force=False)
        data = {
            **status,
            "providers": research_service.provider_map(),
            "symbol_universe": universe_service.status(),
            "scanner_snapshot": scanner_snapshot.get("data", {}).get("freshness", {}),
        }
        return jsonify(
            {
                "success": True,
                "data": data,
                "error": None,
                "timestamp": _utc_now_iso(),
                "data_status": status.get("state", "unavailable"),
                "provider": status.get("provider", "Unavailable"),
            }
        )

    @app.get("/api/market/quote/<ticker>")
    def api_market_quote(ticker: str):
        response = market_stream.get_quote(ticker)
        code = 200 if response.get("success") else 404
        return jsonify(response), code

    @app.get("/api/market/quotes")
    def api_market_quotes():
        raw = request.args.get("symbols", "")
        symbols = [item.strip().upper() for item in raw.split(",") if item.strip()] if raw else None
        response = market_stream.get_quotes(symbols)
        code = 200 if response.get("success") else 400
        return jsonify(response), code

    @app.get("/api/validate-symbol/<ticker>")
    def api_validate_symbol(ticker: str):
        resolved = research_service.resolve_symbol(ticker)
        if not resolved.get("success"):
            return jsonify(resolved), 404

        data = resolved.get("data", {}).get("identity", {})
        quote = research_service.quote(ticker)
        quote_data = quote.get("data", {}) if quote.get("success") else {}
        preview = {
            "ticker": data.get("symbol"),
            "company_name": data.get("name") or data.get("symbol"),
            "current_price": quote_data.get("price"),
            "asset_type": data.get("asset_type") or "stock",
            "market_status": quote_data.get("freshness", {}).get("status") or "unavailable",
            "data_source": quote.get("provider") or resolved.get("provider"),
            "last_updated": quote_data.get("freshness", {}).get("last_updated"),
        }
        return jsonify(build_api_response(True, data=preview, data_status=quote.get("data_status", resolved.get("data_status", "unavailable")))), 200

    @app.get("/api/stock/<ticker>")
    def api_stock(ticker: str):
        response = get_stock_snapshot(ticker)
        status = 200 if response.get("success") else 404
        return jsonify(response), status

    @app.get("/api/chart/<ticker>")
    def api_chart(ticker: str):
        timeframe = request.args.get("timeframe", "1M")
        response = get_chart_data(ticker, timeframe=timeframe)
        status = 200 if response.get("success") else 404
        return jsonify(response), status

    @app.get("/api/tickers/suggest")
    def api_ticker_suggest():
        query = normalize_ticker(request.args.get("q", ""))
        universe_service.ensure_fresh()
        matches = universe_service.search(query, limit=20)
        candidates = [row.get("symbol") for row in matches if row.get("symbol")]
        return jsonify(build_api_response(True, data={"items": candidates}, data_status="cached", provider="PlutoTradeAI"))

    @app.get("/api/symbols/search")
    def api_symbols_search():
        query = request.args.get("q", "")
        universe_service.ensure_fresh()
        matches = universe_service.search(query, limit=25)
        return jsonify(build_api_response(True, data={"query": query, "items": matches}, data_status="cached", provider="NasdaqTraderDirectory"))

    @app.get("/api/stocks/<symbol>/resolve")
    def api_stock_resolve(symbol: str):
        response = research_service.resolve_symbol(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/overview")
    def api_stock_overview(symbol: str):
        response = research_service.overview(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/quote")
    def api_stock_quote(symbol: str):
        response = research_service.quote(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/history")
    def api_stock_history(symbol: str):
        timeframe = request.args.get("timeframe", "3M")
        response = research_service.history(symbol, timeframe=timeframe)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/technicals")
    def api_stock_technicals(symbol: str):
        response = research_service.technicals(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/analysis")
    def api_stock_analysis(symbol: str):
        response = research_service.analysis(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/stocks/<symbol>/news")
    def api_stock_news(symbol: str):
        response = research_service.news(symbol)
        error_kind = response.get("data", {}).get("error_kind") if isinstance(response.get("data"), dict) else None
        code = 200 if response.get("success") else _status_from_error_kind(str(error_kind or ""))
        return jsonify(response), code

    @app.get("/api/market/movers")
    def api_market_movers():
        force = request.args.get("refresh", "").lower() in {"1", "true", "yes", "on"}
        response = scanner_engine.movers(force=force)
        return jsonify(response), 200

    @app.get("/api/market/scanners")
    def api_market_scanners():
        force = request.args.get("refresh", "").lower() in {"1", "true", "yes", "on"}
        response = scanner_engine.scanners(force=force)
        return jsonify(response), 200

    @app.get("/api/market/scanners/<scanner_name>")
    def api_market_scanner_by_name(scanner_name: str):
        force = request.args.get("refresh", "").lower() in {"1", "true", "yes", "on"}
        response = scanner_engine.scanner_by_name(scanner_name, force=force)
        code = 200 if response.get("success") else 404
        return jsonify(response), code

    @app.get("/api/futures")
    def api_futures_overview():
        analyses = [_futures_analysis(symbol, brain_registry) for symbol in FUTURES_SYMBOLS]
        analyses = [item for item in analyses if item.get("ticker")]
        return jsonify(
            build_api_response(
                True,
                data={
                    "rows": analyses,
                    "top_opportunity": analyses[0] if analyses else None,
                    "last_updated": utc_now_iso(),
                },
                data_status="cached" if analyses else "unavailable",
                provider="PlutoTradeAI",
            )
        )

    @app.get("/api/futures/<ticker>")
    def api_futures_ticker(ticker: str):
        normalized = normalize_ticker(ticker)
        if normalized in {"ES", "NQ", "YM", "RTY", "CL", "GC"}:
            normalized = f"{normalized}=F"
        response = _futures_analysis(normalized, brain_registry)
        status = 200 if response.get("ticker") else 404
        if response.get("success") is False:
            return jsonify(response), 404
        return jsonify(build_api_response(True, data=response, data_status=response.get("data_status", "cached"), provider=response.get("provider", "Yahoo Finance"))), status

    @app.get("/api/scanners")
    def api_scanner_overview():
        response = scanner_engine.scanners(force=False)
        data = response.get("data", {})
        raw_rows = data.get("scanners", {}).get("plutotrade_high_confidence", [])
        rows = [
            {
                "ticker": item.get("symbol"),
                "price": item.get("price"),
                "percent_change": item.get("percent_change"),
                "relative_volume": item.get("relative_volume"),
                "scanner_score": item.get("confidence"),
                "provider": response.get("provider", "PlutoScannerEngine"),
                "timestamp": _utc_now_iso(),
                "status": (item.get("freshness", {}) or {}).get("status"),
                "age": (item.get("freshness", {}) or {}).get("age_seconds"),
            }
            for item in raw_rows
        ]
        top_gainers = data.get("scanners", {}).get("top_gainers", [])
        breakouts = data.get("scanners", {}).get("breakout_candidates", [])
        reversals = data.get("scanners", {}).get("reversal_candidates", [])
        high_rvol = data.get("scanners", {}).get("unusual_relative_volume", [])
        high_volatility = data.get("scanners", {}).get("high_volatility", [])
        return jsonify(
            {
                "success": True,
                "error": None,
                "timestamp": _utc_now_iso(),
                "data_status": response.get("data_status", "cached"),
                "provider": response.get("provider", "PlutoScannerEngine"),
                "data": {
                    "scanner": "Overview",
                    "rows": rows,
                    "highest_confidence_setup": rows[0] if rows else None,
                    "largest_mover": {"ticker": (top_gainers[0] or {}).get("symbol")} if top_gainers else None,
                    "best_breakout_candidate": {"ticker": (breakouts[0] or {}).get("symbol")} if breakouts else None,
                    "best_reversal_candidate": {"ticker": (reversals[0] or {}).get("symbol")} if reversals else None,
                    "strongest_relative_volume": {"ticker": (high_rvol[0] or {}).get("symbol")} if high_rvol else None,
                    "highest_risk_setup": {"ticker": (high_volatility[0] or {}).get("symbol")} if high_volatility else None,
                    "data_health": "ok" if rows else "partial",
                    "provider_status": data.get("freshness", {}).get("status", "UNAVAILABLE"),
                    "last_scan_time": data.get("generated_at"),
                },
            }
        )

    @app.get("/api/scanners/<scanner_name>")
    def api_scanner_by_name(scanner_name: str):
        normalized = scanner_name.lower()
        mapped = SCANNER_ROUTES.get(normalized)
        if not mapped:
            return jsonify({"success": False, "data": {}, "error": "Unknown scanner", "timestamp": _utc_now_iso()}), 404
        alias_map = {
            "movers": "top_gainers",
            "volume": "unusual_relative_volume",
            "reversals": "reversal_candidates",
            "breakouts": "breakout_candidates",
            "watchlist": "plutotrade_high_confidence",
            "upcoming": "momentum_leaders",
            "sector-rotation": "near_resistance",
            "extended-hours": "most_active",
            "futures": "high_volatility",
            "overview": "plutotrade_high_confidence",
        }
        response = scanner_engine.scanner_by_name(alias_map.get(mapped, "plutotrade_high_confidence"), force=False)
        if not response.get("success"):
            return jsonify(response), 404
        rows = response.get("data", {}).get("rows", [])
        return jsonify(
            {
                "success": True,
                "error": None,
                "timestamp": _utc_now_iso(),
                "data_status": response.get("data_status", "cached"),
                "provider": response.get("provider", "PlutoScannerEngine"),
                "data": {
                    "scanner": mapped,
                    "rows": [
                        {
                            "ticker": item.get("symbol"),
                            "price": item.get("price"),
                            "percent_change": item.get("percent_change"),
                            "relative_volume": item.get("relative_volume"),
                            "scanner_score": item.get("confidence"),
                            "provider": response.get("provider", "PlutoScannerEngine"),
                            "timestamp": _utc_now_iso(),
                            "status": (item.get("freshness", {}) or {}).get("status"),
                            "age": (item.get("freshness", {}) or {}).get("age_seconds"),
                        }
                        for item in rows
                    ],
                    "data_health": "ok" if rows else "partial",
                    "last_scan_time": response.get("data", {}).get("generated_at"),
                },
            }
        )

    @app.get("/api/brains/<ticker>")
    def api_brains_overview(ticker: str):
        response = brain_registry.run_overview(ticker)
        status = 200 if response.get("success") else 404
        return jsonify(response), status

    @app.get("/api/brains/<brain_name>/<ticker>")
    def api_brain_by_name(brain_name: str, ticker: str):
        normalized = brain_name.lower()
        mapped = BRAIN_ROUTES.get(normalized)
        if not mapped:
            return jsonify({"success": False, "data": {}, "error": "Unknown brain", "timestamp": _utc_now_iso()}), 404

        response = brain_registry.run(mapped, ticker)
        status = 200 if response.get("success") else 404
        return jsonify(response), status

    @app.get("/api/missions")
    def api_missions():
        store = _mission_store()
        persistent = store.get("missions", []) if _is_authenticated() else []
        session_missions = [] if _is_authenticated() else _session_missions()
        data = {
            "persistent": persistent,
            "session_only": session_missions,
            "mode": "authenticated_persistent" if _is_authenticated() else "anonymous_session",
            "counts": {
                "persistent": len(persistent),
                "session": len(session_missions),
                "total": len(persistent) + len(session_missions),
            },
        }
        return jsonify(build_api_response(True, data=data, data_status="cached", provider="PlutoTradeAI"))

    @app.get("/api/missions/summary")
    def api_missions_summary():
        store = _mission_store()
        combined = store.get("missions", [])
        if not _is_authenticated():
            combined = combined + _session_missions()
        summary = build_mission_summary(combined)
        dashboard_summary = _dashboard_mission_brief(summary, combined)
        return jsonify(
            build_api_response(
                True,
                data={"mission_control": summary, "dashboard": dashboard_summary},
                data_status="cached",
                provider="PlutoTradeAI",
            )
        )

    @app.post("/api/missions/assign")
    def api_assign_mission():
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify(build_api_response(False, error="Request body must be a JSON object", data_status="unavailable")), 400
        raw_ticker = payload.get("ticker_symbol") or payload.get("ticker")
        if raw_ticker is not None and not isinstance(raw_ticker, str):
            return jsonify(build_api_response(False, error="ticker must be a string", data_status="unavailable")), 400
        for list_field in ("assigned_scanners", "assigned_brains", "monitoring_flags"):
            value = payload.get(list_field, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                return jsonify(build_api_response(False, error=f"{list_field} must be a list of strings", data_status="unavailable")), 400
        ticker = normalize_ticker(raw_ticker)

        is_valid, error = validate_ticker(ticker)
        if not is_valid:
            return jsonify(build_api_response(False, error=error, data_status="unavailable")), 400

        snapshot_response = get_stock_snapshot(ticker)
        if not snapshot_response.get("success"):
            return jsonify(build_api_response(False, error=snapshot_response.get("error"), data_status="unavailable")), 404

        snapshot = snapshot_response.get("data", {})
        overview = brain_registry.run_overview(ticker)
        scanner_snapshot = scanner_engine.scanners(force=False)
        scanner_overview = {
            "success": scanner_snapshot.get("success", False),
            "data": {
                "rows": scanner_snapshot.get("data", {}).get("scanners", {}).get("plutotrade_high_confidence", []),
            },
            "error": scanner_snapshot.get("error"),
        }

        assigned_scanners = [item for item in payload.get("assigned_scanners", []) if item in SCANNER_ASSIGNMENTS]
        assigned_brains = [item for item in payload.get("assigned_brains", []) if item in BRAIN_ASSIGNMENTS]
        monitoring_flags = [item for item in payload.get("monitoring_flags", []) if item in MONITORING_FLAGS]

        mission_payload = {
            "ticker": ticker,
            "asset_type": payload.get("asset_type") if payload.get("asset_type") in ASSET_TYPES else snapshot.get("asset_type", "Stock"),
            "mission_type": payload.get("mission_type") if payload.get("mission_type") in MISSION_TYPES else "Research",
            "quick_action": payload.get("quick_action") if payload.get("quick_action") in QUICK_ACTIONS else "Analyze Everywhere",
            "priority": payload.get("priority") if payload.get("priority") in PRIORITIES else "★★★ Medium",
            "assigned_scanners": assigned_scanners,
            "assigned_brains": assigned_brains,
            "monitoring_flags": monitoring_flags,
            "notes": payload.get("notes") or "",
        }

        profile = _build_mission_profile(mission_payload, snapshot, overview, scanner_overview)

        if _is_authenticated():
            store = load_user_store(_current_namespace())
            persistent = store.get("missions", [])
            store["missions"] = upsert_mission(persistent, profile)
            save_user_store(_current_namespace(), store)
            return jsonify(
                build_api_response(
                    True,
                    data={
                        "mode": "authenticated_persistent",
                        "message": f"Mission assigned for {ticker}",
                        "mission": profile,
                    },
                    data_status="cached",
                    provider="PlutoTradeAI",
                )
            ), 201

        session_rows = _session_missions()
        session_rows = upsert_mission(session_rows, profile)
        session["session_missions"] = session_rows
        session.modified = True

        return jsonify(
            build_api_response(
                True,
                data={
                    "mode": "anonymous_session",
                    "message": f"Mission assigned for {ticker} in session mode",
                    "mission": profile,
                },
                data_status="cached",
                provider="PlutoTradeAI",
            )
        ), 201

    @app.get("/api/missions/<ticker>/timeline")
    def api_mission_timeline(ticker: str):
        mission = _find_mission_by_ticker(ticker)
        if not mission:
            return jsonify(build_api_response(False, error="Mission not found", data_status="unavailable")), 404

        timeline = build_mission_timeline(mission)
        return jsonify(
            build_api_response(
                True,
                data={
                    "ticker": mission.get("ticker"),
                    "company": mission.get("company"),
                    "priority": mission.get("priority"),
                    "mission_type": mission.get("mission_type"),
                    "mission_status": mission.get("mission_status"),
                    "timeline": timeline,
                    "latest": {
                        "confidence": (mission.get("confidence_history") or [{}])[-1].get("value"),
                        "risk": (mission.get("risk_history") or [{}])[-1].get("value"),
                        "trade_thesis": (mission.get("trade_thesis_history") or [{}])[-1].get("value"),
                        "support": (mission.get("support_history") or [{}])[-1].get("value"),
                        "resistance": (mission.get("resistance_history") or [{}])[-1].get("value"),
                    },
                },
                data_status="cached",
                provider="PlutoTradeAI",
            )
        )

    # Backward-compatible read/write aliases during migration from watchlist to missions.
    @app.post("/api/watchlist")
    def api_watchlist_add_alias():
        return api_assign_mission()

    @app.get("/api/watchlist")
    def api_watchlist_view_alias():
        return api_missions()

    if (
        os.getenv("PLUTO_ROLE", "web").lower() == "web"
        and os.getenv("PLUTO_ENABLE_WORKER", "true").lower() in {"1", "true", "yes", "on"}
        and not _is_test_runtime()
    ):
        worker = MissionRefreshWorker(interval_seconds=int(os.getenv("PLUTO_REFRESH_INTERVAL_SECONDS", "600")))
        worker.start()

    @app.errorhandler(StorageCorruptionError)
    def storage_corrupted(exc: StorageCorruptionError):
        _RELIABILITY_LOGGER.critical("storage corruption: %s", exc)
        if _is_api_request():
            return _json_error_response(503, "A trading state file is corrupted; paper trading is paused until it is restored. See server logs.", error_kind="PROVIDER_OUTAGE")
        return render_template("error.html", code=503, title="Trading State Unavailable", message="A state file is corrupted. Paper trading is paused until an operator restores it."), 503

    @app.errorhandler(413)
    def payload_too_large(_error):
        if _is_api_request():
            return _json_error_response(413, "Request body is too large", error_kind="INVALID_TICKER")
        return render_template("error.html", code=413, title="Request Too Large", message="The request body was too large."), 413

    @app.errorhandler(404)
    def not_found(_error):
        if _is_api_request():
            return _json_error_response(404, "API route not found", error_kind="UNSUPPORTED_SECURITY")
        return render_template("error.html", code=404, title="Route Not Found", message="The requested route is unavailable in this preview."), 404

    @app.errorhandler(500)
    def internal_error(_error):
        if _is_api_request():
            return _json_error_response(500, "Internal API error", error_kind="PROVIDER_OUTAGE")
        return render_template("error.html", code=500, title="Server Error", message="PlutoTrade AI encountered an issue. Please retry shortly."), 500

    @app.errorhandler(Exception)
    def unhandled_error(exc: Exception):
        if _is_api_request():
            started = getattr(g, "request_started_at", None)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2) if started is not None else None
            ticker = request.view_args.get("ticker") if isinstance(request.view_args, dict) else None
            symbol = request.view_args.get("symbol") if isinstance(request.view_args, dict) else None
            _RELIABILITY_LOGGER.exception(
                "api_exception endpoint=%s ticker=%s symbol=%s status=500 exception=%s latency_ms=%s timestamp=%s",
                request.path,
                ticker,
                symbol,
                str(exc),
                elapsed_ms,
                _utc_now_iso(),
            )
            return _json_error_response(500, "Unhandled API exception", error_kind="PROVIDER_OUTAGE")
        raise exc

    app.extensions["mission_refresh_worker"] = worker
    app.extensions["socketio"] = socketio
    app.extensions["market_stream_service"] = market_stream
    app.extensions["signal_service"] = signal_service
    app.extensions["paper_engine"] = paper_engine
    app.extensions["order_monitor_worker"] = order_monitor_worker
    app.extensions["research_service"] = research_service
    app.extensions["symbol_universe_service"] = universe_service
    app.extensions["scanner_engine"] = scanner_engine

    return app


app = create_app()


if __name__ == "__main__":
    socket_server = app.extensions.get("socketio")
    if socket_server:
        socket_server.run(
            app,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8000")),
            debug=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
        )
    else:
        app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
        )
