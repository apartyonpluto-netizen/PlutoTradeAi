from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

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

DEFAULT_TICKER = "SPY"

SCANNER_ROUTES = {
    "overview": "overview",
    "movers": "movers",
    "volume": "volume",
    "reversals": "reversals",
    "breakouts": "breakouts",
    "extended-hours": "extended-hours",
    "futures": "futures",
    "watchlist": "watchlist",
    "upcoming": "upcoming",
}

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_admin_mode() -> bool:
    return os.getenv("PLUTO_ADMIN_MODE", "false").lower() in {"1", "true", "yes", "on"}


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
    }


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv("PLUTO_SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("PLUTO_SESSION_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("PLUTO_SESSION_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=int(os.getenv("PLUTO_SESSION_DAYS", "7")))

    scanner_registry = ScannerRegistry()
    brain_registry = BrainRegistry()
    worker = None

    @app.context_processor
    def inject_navigation() -> Dict[str, Any]:
        nav_groups = [
            {
                "name": "Mission Control",
                "href": url_for("mission_control"),
                "children": [
                    {"name": "Mission Assignment Center", "href": url_for("mission_control")},
                    {"name": "Dashboard", "href": url_for("dashboard")},
                ],
            },
            {
                "name": "Market Intelligence",
                "href": url_for("scanners_overview"),
                "children": [
                    {"name": "Scanners", "href": url_for("scanners_overview")},
                    {"name": "Movers", "href": url_for("scanners_workspace", scanner_name="movers")},
                    {"name": "Extended Hours", "href": url_for("scanners_workspace", scanner_name="extended-hours")},
                    {"name": "Futures", "href": url_for("futures_command")},
                ],
            },
            {
                "name": "AI Intelligence",
                "href": url_for("brains_overview"),
                "children": [
                    {"name": "Brains", "href": url_for("brains_overview")},
                    {"name": "Strategy", "href": url_for("brains_workspace", brain_name="strategy")},
                    {"name": "Risk", "href": url_for("brains_workspace", brain_name="risk")},
                    {"name": "Confidence", "href": url_for("brains_workspace", brain_name="confidence")},
                    {"name": "Market Readiness", "href": url_for("brains_workspace", brain_name="market-readiness")},
                ],
            },
            {"name": "Opportunity Center", "href": url_for("stock_workspace", ticker=DEFAULT_TICKER)},
            {"name": "Portfolio Command", "href": url_for("portfolio_command")},
            {"name": "Account Hub", "href": url_for("account_hub")},
            {"name": "Journal", "href": url_for("journal")},
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
        return render_template("workspace_section.html", title="Portfolio Command", subtitle="Portfolio tools are in founder preview and remain research-only.")

    @app.get("/account")
    def account_hub():
        return render_template("workspace_section.html", title="Account Hub", subtitle="Manage preview account settings and authenticated mission persistence.")

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
        return render_template("scanners.html", active_scanner="overview", scanners=scanner_registry.list_scanners())

    @app.get("/scanners/<scanner_name>")
    def scanners_workspace(scanner_name: str):
        normalized = scanner_name.lower()
        active_scanner = SCANNER_ROUTES.get(normalized)
        if not active_scanner:
            return render_template("scanners.html", active_scanner="overview", scanners=scanner_registry.list_scanners()), 404

        return render_template("scanners.html", active_scanner=active_scanner, scanners=scanner_registry.list_scanners())

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
                "data": {"status": "ok", "deployment": "render-ready"},
                "error": None,
                "timestamp": _utc_now_iso(),
                "data_status": "live",
                "provider": "PlutoTradeAI",
            }
        )

    @app.get("/api/status")
    def api_status():
        return api_health()

    @app.get("/api/validate-symbol/<ticker>")
    def api_validate_symbol(ticker: str):
        normalized = normalize_ticker(ticker)
        is_valid, error = validate_ticker(normalized)
        if not is_valid:
            return jsonify(build_api_response(False, error=error, data_status="unavailable")), 400

        snapshot = get_stock_snapshot(normalized)
        if not snapshot.get("success"):
            return jsonify(snapshot), 404

        data = snapshot["data"]
        preview = {
            "ticker": data.get("ticker"),
            "company_name": data.get("company") or data.get("ticker"),
            "current_price": data.get("current_price"),
            "asset_type": data.get("asset_type") or "Stock",
            "market_status": data.get("session_status") or "unavailable",
            "data_source": snapshot.get("provider"),
            "last_updated": data.get("last_updated"),
        }
        return jsonify(build_api_response(True, data=preview, data_status=snapshot.get("data_status", "unavailable"))), 200

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
        return jsonify(scanner_registry.run("overview"))

    @app.get("/api/scanners/<scanner_name>")
    def api_scanner_by_name(scanner_name: str):
        normalized = scanner_name.lower()
        mapped = SCANNER_ROUTES.get(normalized)
        if not mapped:
            return jsonify({"success": False, "data": {}, "error": "Unknown scanner", "timestamp": _utc_now_iso()}), 404
        return jsonify(scanner_registry.run(mapped))

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
        payload = request.get_json(silent=True) or {}
        ticker = normalize_ticker(payload.get("ticker_symbol") or payload.get("ticker"))

        is_valid, error = validate_ticker(ticker)
        if not is_valid:
            return jsonify(build_api_response(False, error=error, data_status="unavailable")), 400

        snapshot_response = get_stock_snapshot(ticker)
        if not snapshot_response.get("success"):
            return jsonify(build_api_response(False, error=snapshot_response.get("error"), data_status="unavailable")), 404

        snapshot = snapshot_response.get("data", {})
        overview = brain_registry.run_overview(ticker)
        scanner_overview = scanner_registry.run("overview")

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
        and not os.getenv("PYTEST_CURRENT_TEST")
    ):
        worker = MissionRefreshWorker(interval_seconds=int(os.getenv("PLUTO_REFRESH_INTERVAL_SECONDS", "600")))
        worker.start()

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, title="Route Not Found", message="The requested route is unavailable in this preview."), 404

    @app.errorhandler(500)
    def internal_error(_error):
        return render_template("error.html", code=500, title="Server Error", message="PlutoTrade AI encountered an issue. Please retry shortly."), 500

    app.extensions["mission_refresh_worker"] = worker

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
    )
