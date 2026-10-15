# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/")
def mission_briefing_page() -> str:
    context = _build_page_context(
        include_suggestions=True,
        include_reversal=True,
        include_trend=True,
        include_news=True,
        include_trusted_accounts=True,
        block_on_market_data=False,
        allow_stale=True,
    )
    if not context.get("mission_brief_should_show") and request.args.get("show_brief") != "1":
        context["macro_ticker_rows"] = get_macro_ticker_tape(block=False)
        context["webull_balance"] = _get_live_webull_balance(_current_user_id())
        return render_template("dashboard.html", **context)
    return render_template("mission_briefing.html", **context)

@_route_bp.route("/mission-brief")
def mission_brief_manual_page() -> str:
    context = _build_page_context(
        include_suggestions=True,
        include_reversal=True,
        include_trend=True,
        include_news=True,
        block_on_market_data=False,
        allow_stale=True,
    )
    return render_template("mission_briefing.html", **context)

@_route_bp.route("/dashboard")
@_route_bp.route("/mission-control")
def dashboard_page() -> str:
    context = _build_page_context(
        include_suggestions=True,
        include_reversal=True,
        include_trend=True,
        include_news=True,
        include_trusted_accounts=True,
        block_on_market_data=False,
        allow_stale=True,
    )
    context["macro_ticker_rows"] = get_macro_ticker_tape(block=False)
    context["accounts"] = get_accounts(_current_user_id())
    context["webull_balance"] = _get_live_webull_balance(_current_user_id())
    return render_template("dashboard.html", **context)

@_route_bp.route("/news-intelligence")
def news_intelligence_page() -> str:
    # The template only renders news_rows. The default scan and the
    # per-ticker strategy/options pipeline were running on every open.
    return render_template(
        "news_intelligence.html",
        **_build_page_context(include_news=True, include_market_scan=False, include_opportunities=False, allow_stale=True),
    )

@_route_bp.route("/trade-journal")
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

@_route_bp.route("/performance")
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

@_route_bp.route("/agent-map")
def agent_map_page() -> str:
    """The in-app, LIVE counterpart to docs/AGENT_ARCHITECTURE.md and the
    published Agent Map artifact - answers "is the memory layer actually
    being fed" with this account's own real data, not a static diagram.
    Built 2026-09-11 directly in response to being asked to see the map
    "within the application" so the memory/real-outcomes work (see
    research_log.py's signal_snapshot, calibration.py's real-outcomes
    path, outcomes_analysis.py, and the validated_brains gate) is
    continuously checkable, not just documented once and trusted.

    include_opportunities=False/include_market_scan=False for the same
    reason performance_page/daily_digest_page use them - this page only
    reads this account's own recorded history, never live market data."""
    user_id = _current_user_id()
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    context["agent_map"] = _build_agent_map_data(user_id, context)
    return render_template("agent_map.html", **context)

@_route_bp.route("/agent-map/3d")
def agent_map_3d_page() -> str:
    """Same real data as /agent-map, rendered as an interactive 3D scene that refreshes itself
    from /api/agent-map. Reporting only: nothing on it changes a decision."""
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    return render_template("agent_map_3d.html", **context)

@_route_bp.route("/observatory")
def observatory_page() -> str:
    """3D view of recorded trade events (event_journal): structural paths vs
    observed flows, drilling from the whole agent to sectors to one ticker's
    trade chains. Reporting only."""
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    return render_template("observatory.html", **context)

@_route_bp.route("/api/observatory")
@api_guard
def api_observatory():
    return _api_success(observatory.build(
        _current_user_id(),
        level=request.args.get("level") or "agent",
        window_hours=request.args.get("window_hours", default=24, type=float),
        sector=request.args.get("sector") or None,
        ticker=(request.args.get("ticker") or "").strip()[:12] or None,
    ))

@_route_bp.route("/api/agent-map")
def api_agent_map():
    user_id = _current_user_id()
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    data = _build_agent_map_data(user_id, context)
    lifecycle = data.get("lifecycle") or {}
    return jsonify({
        "success": True,
        "generated_at": _now_utc().isoformat(),
        "data": {
            "nodes": [{k: node[k] for k in ("id", "label", "icon", "category", "detail", "active")} for node in data["pulse_nodes"]],
            "feed": data["pulse_feed"],
            "scan_is_active": data["scan_is_active"],
            "most_recent_logged_at": data["most_recent_logged_at"],
            "memory_feed_total": data["memory_feed_total"],
            "memory_feed_populated": data["memory_feed_populated"],
            "calibration_status": data["calibration_status"],
            "closed_trades_since_last_real_recalibration": data["closed_trades_since_last_real_recalibration"],
            "real_outcomes_recalibration_interval": data["real_outcomes_recalibration_interval"],
            "activity_window_minutes": AGENT_MAP_ACTIVITY_WINDOW_MINUTES,
            "calibration_window_hours": AGENT_MAP_CALIBRATION_WINDOW_HOURS,
            "readiness": {key: lifecycle.get(key) for key in ("since_fixes_clean_count", "readiness_target_clean_trades", "total_closed_trades")},
        },
    })

@_route_bp.route("/daily-digest")
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
    digest = build_daily_digest(_current_user_id(), monitor_heartbeat=_monitor_heartbeat_snapshot_for_scan_run())
    what_ifs = suggestion_ledger.pending_what_ifs(_current_user_id())
    digest["what_if"] = [item["text"] for item in what_ifs]
    suggestion_ledger.mark_what_ifs_sent(item["id"] for item in what_ifs)
    context["daily_digest"] = digest
    return render_template("daily_digest.html", **context)

@_route_bp.route("/suggestion-ledger")
def suggestion_ledger_page() -> str:
    """Hit rate of logged suggestions. No market scan: this page only reads the ledger."""
    context = _build_page_context(include_opportunities=False, include_market_scan=False)
    user_id = _current_user_id()
    context["hit_rates"] = suggestion_ledger.hit_rates(user_id)
    context["ledger_rows"] = list(reversed(suggestion_ledger.list_rows(user_id)[-100:]))
    try:
        import market_intel

        context["intel_summary"] = market_intel.summary(user_id)
    except Exception:
        context["intel_summary"] = None
    return render_template("suggestion_ledger.html", **context)

@_route_bp.route("/api/suggestions", methods=["GET"])
def api_suggestions():
    context = _build_page_context(
        include_suggestions=True, include_opportunities=False, include_options=False, block_on_market_data=False, allow_stale=True
    )
    return jsonify({"suggestions": context["suggestions"], "scanner_errors": context["scanner_errors"]})

@_route_bp.route("/api/news/x", methods=["GET"])
def api_news_x():
    context = _build_page_context(
        include_news=True,
        include_trusted_accounts=True,
        include_market_scan=False,
        include_opportunities=False,
        allow_stale=True,
    )
    return jsonify(
        {
            "news": context["news_rows"],
            "errors": context["news_errors"],
            "trusted_accounts": context["trusted_accounts"],
            "never_auto_trade": True,
            "guardrail": "X data can support context but cannot be used as a standalone auto-trade trigger.",
        }
    )

@_route_bp.route("/api/events", methods=["GET"])
@api_guard
def api_events_recent():
    """The signed-in user's event journal, newest last. Filters: since (ISO,
    compared to logged_at), type (repeatable), ticker, limit (<= 1000)."""
    user_id = _current_user_id()
    limit = max(1, min(request.args.get("limit", default=200, type=int), 1000))
    events = event_journal.read_events(
        user_id, since=request.args.get("since") or None, types=request.args.getlist("type") or None,
        ticker=request.args.get("ticker") or None, limit=limit,
    )
    return _api_success({"events": events, "count": len(events), "journal_failures": event_journal.failure_stats()["count"]})

@_route_bp.route("/api/events/chains", methods=["GET"])
@api_guard
def api_event_chains():
    """Recent trade ideas that got past signal evaluation, newest first."""
    user_id = _current_user_id()
    limit = max(1, min(request.args.get("limit", default=30, type=int), 200))
    return _api_success({"chains": event_journal.recent_chains(user_id, limit=limit, since=request.args.get("since") or None)})

@_route_bp.route("/api/events/trace/<correlation_id>", methods=["GET"])
@api_guard
def api_event_trace(correlation_id: str):
    """Everything known about one trade idea, signal to exit."""
    user_id = _current_user_id()
    trace = event_journal.trace(user_id, correlation_id[:64], records=list_overnight_orders(user_id))
    if not trace["events"]:
        return _api_failure("No events for that correlation id.", status_code=404, error_code="not_found", ok=False)
    return _api_success({"trace": trace})

@_route_bp.route("/api/tradingview/webhook", methods=["POST"])
@api_guard
def api_tradingview_webhook():
    # TradingView has no session/login - the token in the URL is the only
    # identifying information, so it has to double as both auth and the
    # lookup key for which user this alert belongs to.
    token = request.args.get("token", "")
    # Render's proxy appends the connecting address LAST; earlier entries can be
    # supplied by the client, so they are not trusted for throttling.
    client_address = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[-1].strip()
    import webhook_throttle
    wait = webhook_throttle.retry_after(token, client_address)
    if wait:
        return _api_failure(
            f"Too many webhook requests. Try again in {wait} seconds.",
            status_code=429,
            error_code="webhook_rate_limited",
            ok=False,
        )
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

@_route_bp.route("/api/status", methods=["GET"])
@api_guard
def api_status():
    context = _build_page_context(
        include_news=True,
        block_on_market_data=False,
        allow_stale=True,
        reuse_opportunity_snapshot=True,
    )
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
            "live_trading_enabled": webull_api.is_live_trading_armed(),
            "options_execution_enabled": False,
            "approval_required": True,
            "emergency_kill_switch_placeholder": False,
        },
        "autonomy": get_autonomy_status(_current_user_id()),
    }
    return _api_success(data)

@_route_bp.route("/api/news", methods=["GET"])
@api_guard
def api_news():
    tickers = get_watchlist_tickers(_current_user_id()) or CORE_SCAN_UNIVERSE[:4]
    bundle = fetch_news_bundle(tickers=tickers, limit=30)
    return _api_success(bundle, news=bundle.get("items", []), errors=bundle.get("errors", []))

@_route_bp.route("/api/tradingview/status", methods=["GET"])
@api_guard
def api_tradingview_status():
    return _api_success(get_tradingview_status(_current_user_id()))
