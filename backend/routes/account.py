# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/watchlist")
def watchlist_page() -> str:
    return render_template(
        "watchlist.html",
        **_build_page_context(
            include_suggestions=True,
            include_opportunities=False,
            include_options=False,
            block_on_market_data=False,
            allow_stale=True,
        ),
    )

@_route_bp.route("/settings")
def settings_page() -> str:
    # include_market_scan=False, include_opportunities=False - settings.html
    # never renders scanner_rows/opportunities data (confirmed by grepping
    # the template); this page was paying the full market-scan pipeline for
    # nothing every time someone opened Settings.
    return render_template(
        "settings.html",
        **_build_page_context(include_trusted_accounts=True, include_market_scan=False, include_opportunities=False),
    )

@_route_bp.route("/account-hub")
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

@_route_bp.route("/notifications")
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

@_route_bp.route("/api/watchlist", methods=["GET"])
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

@_route_bp.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    payload = request.get_json(silent=True) or {}
    try:
        row = add_stock(_current_user_id(), payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})

@_route_bp.route("/api/watchlist/dismiss-suggestion", methods=["POST"])
def api_watchlist_dismiss_suggestion():
    payload = request.get_json(silent=True) or {}
    try:
        dismiss_suggestion(_current_user_id(), payload.get("ticker", ""))
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})

@_route_bp.route("/api/watchlist/update", methods=["POST"])
def api_watchlist_update():
    payload = request.get_json(silent=True) or {}
    ticker = payload.get("ticker", "")
    try:
        row = update_stock(_current_user_id(), ticker=ticker, payload=payload)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "item": row})

@_route_bp.route("/api/watchlist/delete", methods=["POST"])
def api_watchlist_delete():
    payload = request.get_json(silent=True) or {}
    ticker = payload.get("ticker", "")
    try:
        delete_stock(_current_user_id(), ticker=ticker)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})

@_route_bp.route("/api/paper-trade/list", methods=["GET"])
@api_guard
def api_paper_trade_list():
    user_id = _current_user_id()
    trades = list_paper_trades(user_id)
    summary = get_paper_trade_summary(user_id)
    return _api_success({"trades": trades, "summary": summary}, trades=trades, summary=summary, ok=True)

@_route_bp.route("/api/paper-trade/execute", methods=["POST"])
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

@_route_bp.route("/api/paper-trade/close", methods=["POST"])
@api_guard
def api_paper_trade_close():
    payload = request.get_json(silent=True) or {}
    try:
        trade = close_paper_trade(_current_user_id(), payload.get("trade_id", ""), payload.get("exit_price") or None)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return _api_success({"trade": trade}, trade=trade, ok=True)

@_route_bp.route("/api/alerts", methods=["GET", "POST"])
@api_guard
def api_alerts():
    # The drawer polls this every 30s from every open page. It needs alert
    # inputs, not a fresh 6-ticker strategy/options pass. Mission alerts
    # come from the last snapshot the dashboard or the scan already built.
    context = _build_page_context(
        include_suggestions=True,
        include_news=True,
        include_reversal=True,
        include_trend=True,
        include_opportunities=False,
        include_options=False,
        block_on_market_data=False,
        allow_stale=True,
        reuse_opportunity_snapshot=True,
    )
    alerts = context["alerts"]
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        action = payload.get("action", "add")
        if action == "dismiss":
            try:
                dismiss_alert(_current_user_id(), payload.get("id", ""))
            except ValueError as error:
                raise ValidationError(str(error)) from error
            context = _build_page_context(
                include_suggestions=True, include_news=True, include_opportunities=False, include_options=False,
                block_on_market_data=False, allow_stale=True, reuse_opportunity_snapshot=True,
            )
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
            context = _build_page_context(
                include_suggestions=True, include_news=True, include_opportunities=False, include_options=False,
                block_on_market_data=False, allow_stale=True, reuse_opportunity_snapshot=True,
            )
            return _api_success(
                {"alerts": context["alerts"], "unread_count": context["unread_alert_count"]},
                alerts=context["alerts"],
                unread_count=context["unread_alert_count"],
                ok=True,
            )
        if action == "mark_all_read":
            mark_all_read(_current_user_id(), alerts)
            context = _build_page_context(
                include_suggestions=True, include_news=True, include_opportunities=False, include_options=False,
                block_on_market_data=False, allow_stale=True, reuse_opportunity_snapshot=True,
            )
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
        context = _build_page_context(
            include_suggestions=True, include_news=True, include_opportunities=False, include_options=False,
            block_on_market_data=False, allow_stale=True, reuse_opportunity_snapshot=True,
        )
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

@_route_bp.route("/api/trusted-accounts", methods=["GET", "POST", "DELETE"])
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

@_route_bp.route("/api/trusted-accounts/verify", methods=["POST"])
def api_trusted_accounts_verify():
    payload = request.get_json(silent=True) or {}
    result = lookup_x_user(payload.get("username", ""))
    return jsonify(result)

@_route_bp.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(
            {"settings": get_settings(_current_user_id()), "themes": available_themes(), "news_roadmap": get_future_news_roadmap()}
        )
    payload = request.get_json(silent=True) or {}
    try:
        settings = update_settings(_current_user_id(), payload)
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "settings": settings})

@_route_bp.route("/api/mission-brief/status", methods=["GET"])
@api_guard
def api_mission_brief_status():
    settings = get_settings(_current_user_id())
    data = {
        "should_show": _mission_brief_should_show(settings),
        "last_viewed_date": settings.get("mission_brief_last_viewed_date", ""),
        "trading_day": _trading_day_key(),
    }
    return _api_success(data, ok=True, **data)

@_route_bp.route("/api/mission-brief/dismiss", methods=["POST"])
@api_guard
def api_mission_brief_dismiss():
    settings = _dismiss_mission_brief()
    data = {
        "ok": True,
        "dismissed": True,
        "last_viewed_date": settings.get("mission_brief_last_viewed_date", ""),
    }
    return _api_success(data, **data)

@_route_bp.route("/api/mission-brief/reset", methods=["POST"])
@api_guard
def api_mission_brief_reset():
    settings = _reset_mission_brief()
    data = {
        "ok": True,
        "show_mission_brief_again": settings.get("show_mission_brief_again", False),
    }
    return _api_success(data, **data)
