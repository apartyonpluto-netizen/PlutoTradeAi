# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/admin")
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
    position_absent: List[Dict[str, object]] = []
    for user in context["all_users"]:
        target_user_id = user.get("id", "")
        if not target_user_id:
            continue
        # Broker close evidence from the latest read-only reconciliation, by record.
        close_evidence: Dict[str, List[Dict[str, object]]] = {}
        latest_sync = broker_reconciliation.load(target_user_id).get("latest") or {}
        for item in latest_sync.get("items") or []:
            for record_id in item.get("record_ids") or []:
                if item.get("broker_close_evidence"):
                    close_evidence[str(record_id)] = item["broker_close_evidence"]
        for order in list_overnight_orders(target_user_id):
            row = {**order, "user_id": target_user_id, "username": user.get("username", ""),
                   "broker_close_evidence": close_evidence.get(str(order.get("record_id") or order.get("entry_client_order_id") or ""), []),
                   "broker_checked_at": latest_sync.get("checked_at")}
            if order.get("lifecycle_state") == ol.UNKNOWN_SUBMISSION_STATE:
                pending_ambiguous.append(row)
            if order.get("position_absent_unexplained") and order.get("lifecycle_state") in (ol.PROTECTION_FAILED, ol.PROTECTION_CONFIRMED_ACTIVE):
                position_absent.append(row)
    context["pending_ambiguous_submissions"] = pending_ambiguous
    # The UI for _resolve_position_absent_reconciliation, which had only an
    # API route. Records here block their ticker from new entries
    # (tickers_with_open_positions) until a human closes them.
    context["position_absent_entries"] = position_absent
    return render_template("admin.html", **context)

@_route_bp.route("/admin/users/<user_id>")
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

@_route_bp.route("/api/admin/readiness")
def api_admin_readiness():
    """Admin-only "is this safe to run?" report - see readiness.py. Read-only: it never arms live
    trading and never places an order. The market-data probe makes one small Alpaca request."""
    guard = _require_admin()
    if guard:
        return guard
    user_id = _current_user_id()
    try:
        creds = get_webull_credentials(user_id)
        webull_connected = bool(creds and creds.get("app_key") and creds.get("app_secret"))
    except Exception:  # noqa: BLE001 - unreadable credentials just means "not connected" here
        webull_connected = False
    report = build_readiness_report(
        fast_monitor=_fast_monitor_health_status(),
        full_scan=_full_scan_health_status(),
        continuous_monitor=_continuous_monitor_health_status(),
        data_probe=alpaca_data.probe_latest_trade,
        emergency_stop_enabled=bool(get_autonomy_status(user_id).get("emergency_stop_enabled")),
        webull_connected=webull_connected,
        sentry_active=observability_active(),
    )
    return _api_success(report)

@_route_bp.route("/api/admin/setups/validation", methods=["GET", "POST"])
def api_admin_setup_validation():
    """GET: status of the historical setup validation. POST: start a run over
    the scan universe (background, read-only market data)."""
    guard = _require_admin()
    if guard:
        return guard
    if request.method == "POST":
        return _api_success({"run": setups_service.start_validation(list(CORE_SCAN_UNIVERSE), reason=f"manual ({_current_user_id()})"),
                             "status": setups_service.validation_status()})
    return _api_success({"status": setups_service.validation_status()})

@_route_bp.route("/api/admin/backups", methods=["GET", "POST"])
def api_admin_backups():
    """GET: snapshots on disk. POST: take one now. Restores are manual (see
    docs/RUNBOOK_BACKUP_RESTORE.md) - nothing here overwrites records."""
    guard = _require_admin()
    if guard:
        return guard
    created = backups.create(f"manual ({_current_user_id()})") if request.method == "POST" else None
    import offsite_backup
    offsite = offsite_backup.public_status()
    if created and created.get("created"):
        offsite = {**offsite, **offsite_backup.push_created(created)}
    return _api_success({
        "created": created,
        "backups": backups.list_backups(),
        "keep": backups.KEEP,
        "offsite": offsite,
    })

@_route_bp.route("/api/admin/backups/<name>/verify", methods=["GET"])
def api_admin_backup_verify(name: str):
    guard = _require_admin()
    if guard:
        return guard
    return _api_success(backups.verify(name))

@_route_bp.route("/api/admin/backups/<name>/download", methods=["GET"])
def api_admin_backup_download(name: str):
    guard = _require_admin()
    if guard:
        return guard
    path = backups.path_for(name)
    if path is None:
        return _api_failure("No such backup.", status_code=404, error_code="not_found", ok=False)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/gzip")

@_route_bp.route("/api/admin/approve-user", methods=["POST"])
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

@_route_bp.route("/api/admin/reject-user", methods=["POST"])
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

@_route_bp.route("/api/admin/set-role", methods=["POST"])
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

@_route_bp.route("/api/admin/set-suspended", methods=["POST"])
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

@_route_bp.route("/api/admin/delete-user", methods=["POST"])
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

@_route_bp.route("/api/admin/reset-user-password", methods=["POST"])
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

@_route_bp.route("/api/admin/recalibrate-strategies", methods=["POST"])
def api_admin_recalibrate_strategies():
    """Runs the walk-forward backtest across the scan universe in a
    background thread and measures each named strategy's real historical
    win rate, so strategy_brain's confidence scores can be nudged by
    evidence instead of trusting the hand-tuned formulas blindly. Can take
    several minutes - the route returns immediately, the UI polls
    /api/admin/calibration-status for progress.

    Also refreshes the real-outcomes multiplier (this deployment's own
    closed trades - see calibration.recalibrate_from_real_outcomes) inline,
    synchronously - unlike the backtest above, that's a fast local-JSON
    aggregation with no network calls, so it doesn't need the same
    background-thread treatment. This is "the existing manual admin
    trigger" the real-outcomes plan calls for keeping, now refreshing both
    sources with the one button rather than needing a second endpoint."""
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
    try:
        recalibrate_from_real_outcomes()
    except Exception:  # noqa: BLE001 - a real-outcomes recompute failure must not block the backtest trigger above
        pass
    return _api_success(result, ok=True)

@_route_bp.route("/api/admin/calibration-status", methods=["GET"])
def api_admin_calibration_status():
    guard = _require_admin()
    if guard:
        return guard
    return _api_success(get_calibration(), ok=True)

@_route_bp.route("/api/admin/global-settings", methods=["GET", "POST"])
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

@_route_bp.route("/api/admin/ambiguous-submissions", methods=["GET"])
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

@_route_bp.route("/api/admin/stuck-monitor-entries", methods=["GET"])
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

@_route_bp.route("/api/admin/diagnostic/sandbox-accounts", methods=["GET"])
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

@_route_bp.route("/api/admin/diagnostic/preview-short-sell", methods=["POST"])
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

@_route_bp.route("/api/admin/diagnostic/preview-raw-order", methods=["POST"])
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

@_route_bp.route("/api/admin/diagnostic/option-contracts", methods=["GET"])
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

@_route_bp.route("/api/admin/diagnostic/option-snapshot", methods=["GET"])
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

@_route_bp.route("/api/admin/diagnostic/preview-raw-option-order", methods=["POST"])
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

@_route_bp.route("/api/admin/diagnostic/capital-snapshot", methods=["GET"])
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

@_route_bp.route("/api/admin/diagnostic/order-detail", methods=["GET"])
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

@_route_bp.route("/api/admin/ambiguous-submissions/resolve", methods=["POST"])
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

@_route_bp.route("/api/admin/reconcile-position-absent", methods=["POST"])
def api_admin_reconcile_position_absent():
    """The admin-facing route for _resolve_position_absent_reconciliation -
    see its own docstring for the full discipline this delegates to."""
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    target_user_id = str(payload.get("user_id", ""))
    try:
        # Under the user's scan lock: the continuous monitor reads the order
        # list, makes broker calls, then writes it back, so a close landing
        # between its read and write would be silently reverted.
        result = _call_holding_user_scan_lock(
            target_user_id,
            lambda: _resolve_position_absent_reconciliation(
                target_user_id=target_user_id,
                admin_user_id=_current_user_id(),
                entry_client_order_id=str(payload.get("entry_client_order_id", "")),
                reason=str(payload.get("reason", "")),
                confirmation=str(payload.get("confirmation", "")),
            ),
        )
    except ScanAlreadyRunningError:
        return _api_failure("A scan or monitor pass is running for this account - try again in a few seconds.", status_code=409, error_code="busy", ok=False)
    except ValidationError as error:
        return _api_failure(str(error), status_code=400, error_code="invalid_request", ok=False)
    return _api_success({"entry": result["entry"]}, ok=True, entry=result["entry"])
