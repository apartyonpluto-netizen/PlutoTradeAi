from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request

from account_hub import connect_account, disconnect_account, get_accounts, test_account, update_trading_enabled
from alerts import add_manual_alert, build_system_alerts, dismiss_alert, dismiss_alerts, get_alerts_snapshot
from market_scanner import SCAN_LIST, scan_market
from upcoming_opportunities import build_upcoming_opportunities
from watchlist import (
    add_stock,
    delete_stock,
    filter_watchlist,
    get_watchlist,
    get_watchlist_tickers,
    search_watchlist,
    sort_watchlist,
    update_stock,
)

app = Flask(__name__)

SCANNER_CACHE: Dict[str, object] = {"rows": [], "errors": [], "last_updated": "", "expires_at": None}
SCANNER_CACHE_SECONDS = 30
OPPORTUNITIES_CACHE: Dict[str, object] = {"payload": {}, "source_stamp": "", "expires_at": None}
OPPORTUNITIES_CACHE_SECONDS = 90


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _market_status(now_utc: datetime | None = None) -> str:
    eastern = (now_utc or _now_utc()).astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5:
        return "Closed"
    current_minutes = eastern.hour * 60 + eastern.minute
    if 570 <= current_minutes < 960:
        return "Open"
    return "Pre/Post Market"


def get_market_data(force_refresh: bool = False):
    expiry = SCANNER_CACHE.get("expires_at")
    if (
        not force_refresh
        and SCANNER_CACHE.get("rows")
        and isinstance(expiry, datetime)
        and expiry > _now_utc()
    ):
        return SCANNER_CACHE["rows"], SCANNER_CACHE["errors"], SCANNER_CACHE["last_updated"]
    watchlist_tickers = get_watchlist_tickers()
    scan_universe = sorted(set(SCAN_LIST + watchlist_tickers))
    rows, errors, last_updated = scan_market(tickers=scan_universe, watchlist_tickers=watchlist_tickers)
    if rows:
        SCANNER_CACHE.update(
            {
                "rows": rows,
                "errors": errors,
                "last_updated": last_updated,
                "expires_at": _now_utc() + timedelta(seconds=SCANNER_CACHE_SECONDS),
            }
        )
        return rows, errors, last_updated
    if SCANNER_CACHE.get("rows"):
        previous_errors = list(SCANNER_CACHE.get("errors", []))
        SCANNER_CACHE.update(
            {
                "errors": (previous_errors + errors)[:8],
                "expires_at": _now_utc() + timedelta(seconds=10),
            }
        )
        return SCANNER_CACHE["rows"], SCANNER_CACHE["errors"], SCANNER_CACHE["last_updated"]
    SCANNER_CACHE.update(
        {
            "rows": [],
            "errors": errors,
            "last_updated": last_updated,
            "expires_at": _now_utc() + timedelta(seconds=10),
        }
    )
    return rows, errors, last_updated


def build_suggestions(scanner_rows: List[Dict[str, object]], watchlist_tickers: List[str]) -> List[Dict[str, str]]:
    watchlist_set = {ticker.upper() for ticker in watchlist_tickers}
    suggestions: List[Dict[str, str]] = []
    for row in scanner_rows:
        ticker = str(row.get("ticker", "")).upper()
        if not ticker or ticker in watchlist_set:
            continue
        score = float(row.get("scanner_score", 0))
        if score < 65:
            continue
        suggestions.append(
            {
                "ticker": ticker,
                "category": "AI Discovery",
                "status": "Candidate",
                "ai_score": str(int(score)),
                "reason": (
                    f"Score {score:.0f}, move {float(row.get('percent_change', 0)):+.2f}%, "
                    f"relative volume {float(row.get('relative_volume', 0)):.2f}x."
                ),
            }
        )
    return suggestions[:12]


def build_mission_brief(scanner_rows: List[Dict[str, object]], scanner_errors: List[str], watchlist_count: int) -> Dict[str, object]:
    score_values = [float(row.get("scanner_score", 0)) for row in scanner_rows]
    avg_score = sum(score_values) / len(score_values) if score_values else 0.0
    avg_change = (
        sum(float(row.get("percent_change", 0)) for row in scanner_rows) / len(scanner_rows) if scanner_rows else 0.0
    )
    top_watch = [row.get("ticker", "") for row in scanner_rows[:3] if row.get("ticker")]
    accounts = get_accounts()
    webull = next((row for row in accounts if row.get("platform") == "webull"), {})
    etrade = next((row for row in accounts if row.get("platform") == "etrade"), {})
    opportunities = [row for row in scanner_rows if float(row.get("scanner_score", 0)) >= 65]
    return {
        "market_status": _market_status(),
        "ai_status": "Online",
        "current_time": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M %p ET"),
        "scanner_status": "Healthy" if not scanner_errors else "Degraded",
        "watchlist_status": f"{watchlist_count} symbols tracked",
        "account_status": f"{sum(1 for row in accounts if row.get('status') not in {'Not Connected'})}/{len(accounts)} connected",
        "market_sentiment": "Positive" if avg_change > 0.35 else ("Negative" if avg_change < -0.35 else "Neutral"),
        "ai_confidence": "High" if avg_score >= 75 else ("Medium" if avg_score >= 62 else "Low"),
        "risk_level": "Controlled" if avg_score >= 75 else ("Moderate" if avg_score >= 62 else "Elevated"),
        "todays_opportunities": len(opportunities),
        "mission_progress": min(100, 45 + len(opportunities) * 6),
        "watch_today": top_watch,
        "paper_trading_status": "Connected" if webull.get("status") == "Paper Mode" else "Disconnected",
        "live_trading_enabled": bool(etrade.get("trading_enabled")),
    }


def get_opportunities_data(
    scanner_rows: List[Dict[str, object]],
    scanner_stamp: str,
    force_refresh: bool = False,
) -> Dict[str, object]:
    expiry = OPPORTUNITIES_CACHE.get("expires_at")
    if (
        not force_refresh
        and OPPORTUNITIES_CACHE.get("payload")
        and isinstance(expiry, datetime)
        and expiry > _now_utc()
        and OPPORTUNITIES_CACHE.get("source_stamp") == scanner_stamp
    ):
        return OPPORTUNITIES_CACHE["payload"]  # type: ignore[return-value]
    payload = build_upcoming_opportunities(scanner_rows=scanner_rows)
    OPPORTUNITIES_CACHE.update(
        {
            "payload": payload,
            "source_stamp": scanner_stamp,
            "expires_at": _now_utc() + timedelta(seconds=OPPORTUNITIES_CACHE_SECONDS),
        }
    )
    return payload


def build_context(force_refresh: bool = False) -> Dict[str, object]:
    watchlist = get_watchlist()
    scanner_rows, scanner_errors, scanner_last_updated = get_market_data(force_refresh=force_refresh)
    opportunities_data = get_opportunities_data(
        scanner_rows=scanner_rows,
        scanner_stamp=scanner_last_updated,
        force_refresh=force_refresh,
    )
    suggestions = build_suggestions(scanner_rows=scanner_rows, watchlist_tickers=[row["ticker"] for row in watchlist])
    alerts = get_alerts_snapshot(
        build_system_alerts(
            scanner_rows=scanner_rows,
            suggestions=suggestions,
            mission_alerts=opportunities_data.get("mission_alerts", []),  # type: ignore[arg-type]
        )
    )
    return {
        "watchlist": watchlist,
        "movers": scanner_rows,
        "scanner_errors": scanner_errors,
        "scanner_last_updated": scanner_last_updated,
        "suggestions": suggestions,
        "alerts": alerts,
        "accounts": get_accounts(),
        "upcoming_opportunities": opportunities_data.get("opportunities", []),
        "mission_queue": opportunities_data.get("mission_queue", []),
        "opportunities_timeline": opportunities_data.get("timeline", {}),
        "mission_alerts": opportunities_data.get("mission_alerts", []),
        "opportunity_integration_status": opportunities_data.get("integration_status", {}),
        "opportunity_engine_errors": opportunities_data.get("errors", []),
        "opportunity_generated_at": opportunities_data.get("generated_at", ""),
        "mission_brief": build_mission_brief(
            scanner_rows=scanner_rows,
            scanner_errors=scanner_errors,
            watchlist_count=len(watchlist),
        ),
    }


@app.route("/")
def mission_briefing():
    return render_template("mission_briefing.html", **build_context())


@app.route("/dashboard")
@app.route("/mission-control")
def dashboard():
    return render_template("dashboard.html", **build_context())


@app.route("/watchlist")
def watchlist_page():
    return render_template("watchlist.html", **build_context())


@app.route("/scanner")
@app.route("/volume-scanner")
def scanner_page():
    return render_template("scanner.html", **build_context())


@app.route("/news-intelligence")
def news_intelligence_page():
    return render_template("news_intelligence.html", **build_context())


@app.route("/account-hub")
def account_hub_page():
    return render_template("account_hub.html", **build_context())


@app.route("/notifications")
def notifications_page():
    return render_template("notifications.html", **build_context())


@app.route("/trade-journal")
def trade_journal_page():
    return render_template("trade_journal.html", **build_context())


@app.route("/settings")
def settings_page():
    return render_template("settings.html", **build_context())


@app.route("/candle-brain")
def candle_brain_page():
    return render_template("candle_brain.html", **build_context())


@app.route("/pattern-brain")
def pattern_brain_page():
    return render_template("pattern_brain.html", **build_context())


@app.route("/support-resistance")
def support_resistance_page():
    return render_template("support_resistance.html", **build_context())


@app.route("/neural-engine")
def neural_engine_page():
    return render_template("neural_engine.html", **build_context())


@app.route("/api/scanner", methods=["GET"])
def api_scanner():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    rows, errors, updated = get_market_data(force_refresh=force_refresh)
    return jsonify({"rows": rows, "errors": errors, "last_updated": updated})


@app.route("/api/suggestions", methods=["GET"])
def api_suggestions():
    context = build_context()
    return jsonify({"suggestions": context["suggestions"]})


@app.route("/api/opportunities", methods=["GET"])
def api_opportunities():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    scanner_rows, _, scanner_last_updated = get_market_data(force_refresh=force_refresh)
    payload = get_opportunities_data(
        scanner_rows=scanner_rows,
        scanner_stamp=scanner_last_updated,
        force_refresh=force_refresh,
    )
    return jsonify(payload)


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist():
    rows = get_watchlist()
    filtered = filter_watchlist(
        rows=rows,
        category=request.args.get("category", ""),
        status=request.args.get("status", ""),
    )
    searched = search_watchlist(query=request.args.get("query", ""), rows=filtered)
    sorted_rows = sort_watchlist(
        rows=searched,
        sort_by=request.args.get("sort_by", "ticker"),
        descending=request.args.get("descending", "false").lower() == "true",
    )
    return jsonify({"watchlist": sorted_rows})


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    payload = request.get_json(silent=True) or {}
    try:
        row = add_stock(payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})


@app.route("/api/watchlist/update", methods=["POST"])
def api_watchlist_update():
    payload = request.get_json(silent=True) or {}
    try:
        row = update_stock(ticker=str(payload.get("ticker", "")), payload=payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})


@app.route("/api/watchlist/delete", methods=["POST"])
def api_watchlist_delete():
    payload = request.get_json(silent=True) or {}
    try:
        delete_stock(ticker=str(payload.get("ticker", "")))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})


@app.route("/api/alerts", methods=["GET", "POST"])
def api_alerts():
    if request.method == "GET":
        context = build_context()
        return jsonify({"alerts": context["alerts"]})
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "add")
    if action == "dismiss":
        try:
            dismiss_alert(str(payload.get("id", "")))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        return jsonify({"ok": True})
    if action == "dismiss_all":
        ids = payload.get("ids", [])
        if not isinstance(ids, list):
            return jsonify({"ok": False, "error": "Alert IDs must be a list."}), 400
        try:
            count = dismiss_alerts(ids)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        return jsonify({"ok": True, "dismissed": count})
    try:
        alert = add_manual_alert(payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "alert": alert})


@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    return jsonify({"accounts": get_accounts()})


@app.route("/api/accounts/connect", methods=["POST"])
def api_accounts_connect():
    payload = request.get_json(silent=True) or {}
    platform = payload.get("platform", "")
    if not platform:
        return jsonify({"ok": False, "error": "Platform is required."}), 400
    try:
        if "trading_enabled" in payload:
            account = update_trading_enabled(platform=platform, trading_enabled=bool(payload.get("trading_enabled")))
        else:
            account = connect_account(platform=platform)
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
        account = disconnect_account(platform=platform)
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
        account = test_account(platform=platform)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "account": account})


if __name__ == "__main__":
    app.run(debug=True)