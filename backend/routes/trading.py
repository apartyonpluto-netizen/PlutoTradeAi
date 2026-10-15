# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/api/trade-journal/refresh-positions", methods=["POST"])
@api_guard
def api_refresh_webull_positions():
    payload = _get_live_webull_positions(_current_user_id(), force_refresh=True)
    return _api_success(payload, **payload)

@_route_bp.route("/api/trade-journal/close-position", methods=["POST"])
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
            opens_position=False,  # manual close reduces risk - never blocked by live dollar caps
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

@_route_bp.route("/api/webull/place-test-order", methods=["POST"])
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

@_route_bp.route("/api/webull/cancel-test-order", methods=["POST"])
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

@_route_bp.route("/api/webull/place-stage3-order", methods=["POST"])
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

@_route_bp.route("/api/autonomy/status", methods=["GET"])
@api_guard
def api_autonomy_status():
    payload = get_autonomy_status(_current_user_id())
    return _api_success(payload, autonomy=payload, ok=True)

@_route_bp.route("/api/autonomy/set-mode", methods=["POST"])
@api_guard
def api_autonomy_set_mode():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "OFF"))
    reason = str(payload.get("mode_change_reason", ""))
    result = set_mode(_current_user_id(), mode=mode, reason=reason)
    return _api_success(result, autonomy=result, ok=True)

@_route_bp.route("/api/autonomy/emergency-stop", methods=["POST"])
@api_guard
def api_autonomy_emergency_stop():
    payload = request.get_json(silent=True) or {}
    result = emergency_stop(_current_user_id(), reason=str(payload.get("mode_change_reason", "")))
    return _api_success(result, autonomy=result, ok=True)

@_route_bp.route("/api/safety/close-everything", methods=["POST"])
@api_guard
def api_close_everything():
    """Confirmed flatten. The phrase must match or nothing is sent to the broker."""
    import flatten_account
    import phone_alerts

    payload = request.get_json(silent=True) or {}
    if not flatten_account.confirmed(str(payload.get("confirm") or "")):
        return _api_failure(
            "Type CLOSE EVERYTHING to confirm. Nothing was cancelled or closed.",
            status_code=400,
            error_code="confirmation_required",
            ok=False,
        )
    user_id = _current_user_id()
    emergency_stop(user_id, reason="Close everything confirmed from the stop button.")
    try:
        result = flatten_account.run_for_user(user_id)
    except Exception as error:  # noqa: BLE001 - the stop is already on; report the failure
        phone_alerts.notify("error", "Close everything failed", str(error)[:500])
        raise
    phone_alerts.notify("halt", "Close everything", "Open orders were cancelled and positions were flattened.")
    return _api_success(result, ok=bool(result.get("ok")), **result)

@_route_bp.route("/api/autonomy/reset-stop", methods=["POST"])
@api_guard
def api_autonomy_reset_stop():
    payload = request.get_json(silent=True) or {}
    result = reset_emergency_stop(_current_user_id(), reason=str(payload.get("mode_change_reason", "")))
    return _api_success(result, autonomy=result, ok=True)

@_route_bp.route("/api/autonomy/risk-settings", methods=["POST"])
@api_guard
def api_autonomy_risk_settings():
    payload = request.get_json(silent=True) or {}
    try:
        result = update_risk_settings(
            _current_user_id(),
            daily_loss_limit_percent=payload.get("daily_loss_limit_percent"),
            risk_percent_of_balance=payload.get("risk_percent_of_balance"),
            max_positions=payload.get("max_positions"),
            option_stop_loss_percent=payload.get("option_stop_loss_percent"),
            option_target_gain_percent=payload.get("option_target_gain_percent"),
            option_close_days_before_expiration=payload.get("option_close_days_before_expiration"),
            max_position_exposure_percent=payload.get("max_position_exposure_percent"),
            max_total_exposure_percent=payload.get("max_total_exposure_percent"),
            max_sector_exposure_percent=payload.get("max_sector_exposure_percent"),
            max_drawdown_percent=payload.get("max_drawdown_percent"),
            losing_streak_limit=payload.get("losing_streak_limit"),
        )
    except (ValueError, TypeError) as error:
        raise ValidationError(str(error)) from error
    return _api_success(result, autonomy=result, ok=True)

@_route_bp.route("/api/autonomy/overnight-orders", methods=["GET"])
@api_guard
def api_autonomy_overnight_orders():
    orders = list_overnight_orders(_current_user_id())
    return _api_success({"orders": orders}, orders=orders, ok=True)

@_route_bp.route("/api/trade-tickets", methods=["GET"])
@api_guard
def api_trade_tickets_list():
    user_id = _current_user_id()
    tickets = trade_tickets.list_tickets(user_id)
    status = get_autonomy_status(user_id)
    limit = max(1, min(request.args.get("limit", default=30, type=int), 200))
    return _api_success({
        "approval_mode": bool(status.get("approval_required_status")),
        "current_mode": status.get("current_mode"),
        "tickets": [_ticket_for_api(t) for t in tickets[:limit]],
        "awaiting_count": sum(1 for t in tickets if t.get("status") == trade_tickets.AWAITING_APPROVAL),
    })

@_route_bp.route("/api/trade-tickets/<ticket_id>/approve", methods=["POST"])
@api_guard
def api_trade_ticket_approve(ticket_id: str):
    """Approve one ticket. The approval must carry the ticket version the
    user saw; the ticket is claimed atomically, re-checked against the
    broker and the market under the account's scan lock, and only then
    submitted through the scan's own submission path."""
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}
    version = str(payload.get("version") or "")
    creds = get_webull_credentials(user_id)
    if not is_webull_configured(user_id):
        return _api_failure("Connect Webull in Account Hub before approving a trade.", status_code=400, error_code="not_configured", ok=False)
    try:
        ticket = trade_tickets.claim_for_approval(user_id, ticket_id, version, approved_by=user_id)
    except trade_tickets.TicketConflict as error:
        return _api_failure(str(error), status_code=409, error_code="ticket_conflict", ok=False)

    def _recheck_and_submit():
        recheck = _recheck_ticket_before_submission(user_id, creds, ticket)
        if not recheck["ok"]:
            def _fail(t):
                t["status"] = trade_tickets.RECHECK_FAILED
                t["recheck"] = recheck
            return trade_tickets.update_ticket(user_id, ticket_id, _fail), None
        entry = _execute_approved_ticket(user_id, creds, ticket)

        def _done(t):
            t["status"] = trade_tickets.SUBMITTED
            t["recheck"] = recheck
            t["submission"] = {
                "status": entry.get("status"), "entry_client_order_id": entry.get("entry_client_order_id"),
                "lifecycle_state": entry.get("lifecycle_state"), "error": entry.get("error"), "record_id": entry.get("record_id"),
                "submitted_at": _now_utc().isoformat(),
            }
        return trade_tickets.update_ticket(user_id, ticket_id, _done), entry

    try:
        updated, entry = _call_holding_user_scan_lock(user_id, _recheck_and_submit)
    except ScanAlreadyRunningError:
        # Give the ticket back rather than leaving it stuck in APPROVED.
        def _release(t):
            t["status"] = trade_tickets.AWAITING_APPROVAL
            t.pop("approved_at", None)
        trade_tickets.update_ticket(user_id, ticket_id, _release)
        return _api_failure("A scan is running for this account - try again in a few seconds.", status_code=409, error_code="busy", ok=False)
    except Exception as error:  # noqa: BLE001 - never leave a ticket stuck in APPROVED
        def _errored(t):
            t["status"] = trade_tickets.RECHECK_FAILED
            t["recheck"] = {"ok": False, "reasons": [f"unexpected error: {str(error)[:200]}"], "checked_at": _now_utc().isoformat()}
        trade_tickets.update_ticket(user_id, ticket_id, _errored)
        logger.exception("Approved ticket %s failed unexpectedly", ticket_id)
        return _api_failure(f"Approval failed: {error}", status_code=500, error_code="approval_failed", ok=False)

    if updated.get("status") == trade_tickets.RECHECK_FAILED:
        reasons = (updated.get("recheck") or {}).get("reasons") or []
        return _api_failure("Not submitted - " + "; ".join(reasons), status_code=409, error_code="recheck_failed", ok=False, ticket=_ticket_for_api(updated))
    return _api_success({"ticket": _ticket_for_api(updated), "entry": entry}, ok=True)

@_route_bp.route("/api/trade-tickets/<ticket_id>/decline", methods=["POST"])
@api_guard
def api_trade_ticket_decline(ticket_id: str):
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}
    try:
        ticket = trade_tickets.decline_ticket(
            user_id, ticket_id, str(payload.get("version") or ""), reason=str(payload.get("reason") or ""), declined_by=user_id,
        )
    except trade_tickets.TicketConflict as error:
        return _api_failure(str(error), status_code=409, error_code="ticket_conflict", ok=False)
    return _api_success({"ticket": _ticket_for_api(ticket)}, ok=True)

@_route_bp.route("/api/broker/positions/<record_id>/protect", methods=["POST"])
@api_guard
def api_broker_protect_position(record_id: str):
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}
    try:
        stop_price = float(payload.get("stop_price") or 0)
        target_price = float(payload.get("target_price") or 0)
    except (TypeError, ValueError):
        return _api_failure("Stop and target must be numbers.", status_code=400, error_code="invalid", ok=False)

    def _protect():
        record, account_id, position, _creds = _owner_position_action_guard(user_id, record_id, str(payload.get("confirm_symbol") or ""))
        last = float(position.get("last_price") or 0)
        short = record.get("direction") == "short"
        if stop_price <= 0:
            raise ValidationError("Enter a stop price.")
        if last and ((not short and stop_price >= last) or (short and stop_price <= last)):
            raise ValidationError(f"A stop at {stop_price:.2f} would trigger immediately (last price {last:.2f}).")
        if target_price and last and ((not short and target_price <= last) or (short and target_price >= last)):
            raise ValidationError(f"A target at {target_price:.2f} is already reached (last price {last:.2f}).")
        orders = list_overnight_orders(user_id)
        stored = next(o for o in orders if str(o.get("record_id") or o.get("entry_client_order_id")) == record_id)
        stored["stop"] = round(stop_price, 2)
        if target_price:
            stored["target"] = round(target_price, 2)
        stored["account_id"] = stored.get("account_id") or account_id
        stored["owner_protection_set_at"] = _now_utc().isoformat()
        stored["owner_protection_set_by"] = user_id
        replace_overnight_orders(user_id, orders)
        event_journal.emit(user_id, "protection.owner_stop_set", correlation_id=event_journal.correlation_id_for(stored),
                           ticker=stored.get("ticker"), account_id=account_id, source="owner",
                           data={"stop": stored["stop"], "target": stored.get("target"), "broker_quantity": position.get("quantity"), "last_price": last})
        return stored

    try:
        stored = _call_holding_user_scan_lock(user_id, _protect)
    except ValidationError as error:
        return _api_failure(str(error), status_code=400, error_code="refused", ok=False)
    except ScanAlreadyRunningError:
        return _api_failure("A scan is running for this account - try again in a few seconds.", status_code=409, error_code="busy", ok=False)
    return _api_success({"record_id": record_id, "stop": stored.get("stop"), "target": stored.get("target"),
                         "next": "The monitor places and confirms the stop at Webull on its next pass during market hours (9:30-4:00 ET). Broker sync will show it once it is working."})

@_route_bp.route("/api/broker/positions/<record_id>/close", methods=["POST"])
@api_guard
def api_broker_close_position(record_id: str):
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}
    if _current_webull_trading_session() != "CORE":
        return _api_failure("Closing from here needs regular market hours (9:30-4:00 ET) so the order can fill.", status_code=400, error_code="market_closed", ok=False)

    def _close():
        record, account_id, position, creds = _owner_position_action_guard(user_id, record_id, str(payload.get("confirm_symbol") or ""))
        symbol = str(record.get("ticker")).upper()
        quantity = abs(float(position.get("quantity") or 0))
        last = float(position.get("last_price") or 0)
        if last <= 0:
            raise ValidationError("The broker did not report a last price - cannot price a closing order safely.")
        short = record.get("direction") == "short" or float(position.get("quantity") or 0) < 0
        side = "BUY" if short else "SELL"
        limit = round(last * (1 + MANUAL_CLOSE_SLIPPAGE) if short else last * (1 - MANUAL_CLOSE_SLIPPAGE), 2)
        result = webull_api.place_stock_order(creds["app_key"], creds["app_secret"], account_id, symbol, side, quantity, limit,
                                              trading_session="CORE", opens_position=False)
        orders = list_overnight_orders(user_id)
        stored = next(o for o in orders if str(o.get("record_id") or o.get("entry_client_order_id")) == record_id)
        stored["owner_close"] = {"client_order_id": result.get("client_order_id"), "side": side, "quantity": quantity,
                                 "limit_price": limit, "submitted_at": _now_utc().isoformat(), "submitted_by": user_id}
        replace_overnight_orders(user_id, orders)
        event_journal.emit(user_id, "exit.owner_close_submitted", correlation_id=event_journal.correlation_id_for(stored),
                           ticker=symbol, account_id=account_id, source="owner",
                           data={"side": side, "quantity": quantity, "limit_price": limit, "client_order_id": result.get("client_order_id")})
        return stored["owner_close"]

    try:
        submitted = _call_holding_user_scan_lock(user_id, _close)
    except ValidationError as error:
        return _api_failure(str(error), status_code=400, error_code="refused", ok=False)
    except ScanAlreadyRunningError:
        return _api_failure("A scan is running for this account - try again in a few seconds.", status_code=409, error_code="busy", ok=False)
    except Exception as error:  # noqa: BLE001 - the broker's own answer, shown as-is
        return _api_failure(f"Webull did not accept the order: {str(error)[:200]}", status_code=502, error_code="broker_rejected", ok=False)
    return _api_success({"submitted": submitted,
                         "next": "Submitted to Webull - not yet a fill. Broker sync will show the position gone once it fills; then close the record in Admin (the sale shows there as evidence)."})

@_route_bp.route("/api/broker/reconciliation", methods=["GET", "POST"])
@api_guard
def api_broker_reconciliation():
    """GET: the last broker-vs-app comparison. POST: run one now (read-only
    broker calls; nothing is placed, cancelled or changed)."""
    user_id = _current_user_id()
    if request.method == "POST":
        if not is_webull_configured(user_id):
            return _api_failure("Connect Webull in Account Hub first.", status_code=400, error_code="not_configured", ok=False)
        stored = _run_broker_reconciliation(user_id)
    else:
        stored = broker_reconciliation.load(user_id)
    return _api_success({**stored, "environment": broker_env.current()["environment"],
                         "interval_seconds": BROKER_RECONCILIATION_INTERVAL_SECONDS})

@_route_bp.route("/api/autonomy/run-overnight-scan", methods=["POST"])
@api_guard
def api_autonomy_run_overnight_scan():
    summary = _run_autonomous_trade_scan(_current_user_id())
    return _api_success(summary, **summary)

@_route_bp.route("/api/autonomy/preview-scan", methods=["POST"])
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

@_route_bp.route("/api/autonomy/activate", methods=["POST"])
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

@_route_bp.route("/api/autonomy/deactivate", methods=["POST"])
@api_guard
def api_autonomy_deactivate():
    user_id = _current_user_id()
    autonomy_result = set_mode(user_id, mode="OFF", reason="Deactivated from the Mission Control planet button.")
    payload = {"ok": True, "autonomy": autonomy_result}
    return _api_success(payload, **payload)

@_route_bp.route("/api/autonomy/scan-runs", methods=["GET"])
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

@_route_bp.route("/api/autonomy/efficiency-report", methods=["GET"])
@api_guard
def api_autonomy_efficiency_report():
    """Authenticated, per-session, same conventions as
    /api/autonomy/scan-runs just above. Wraps
    autonomy/efficiency_report.py's build_efficiency_report - the
    conversion-funnel (found -> qualifying -> placed), ranked skip-reason
    breakdown, and options attempted-vs-found stats behind the "Trading
    Efficiency" dashboard panel. See that module's own docstring for why
    this is reporting-only and never feeds back into the live scan."""
    user_id = _current_user_id()
    days = request.args.get("days", default=7, type=int)
    days = max(1, min(days, 90))
    report = build_efficiency_report(user_id, days=days)
    return _api_success(report, ok=True, efficiency_report=report)

@_route_bp.route("/api/autonomy/cron-trigger", methods=["POST"])
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

        if current_mode not in ("AUTONOMOUS", "APPROVAL"):
            # Existing-position monitoring stays independent of mode - see
            # the docstring above. Only a lightweight reconciliation pass
            # here, never the opportunity-scanning/new-entry work, which
            # is AUTONOMOUS-only.
            if not is_webull_configured(user_id):
                record_scan_run(user_id, {
                    **base_record, "status": "skipped",
                    "reason": f"autonomy mode is {current_mode}, not AUTONOMOUS or APPROVAL, and Webull is not configured for this account - nothing to reconcile",
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
                            f"autonomy mode is {current_mode}, not AUTONOMOUS or APPROVAL - no new-entry scan this tick; "
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
                scan_result = _run_autonomous_trade_scan_waiting_for_lock(user_id)
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

    # Setup-detector evidence is refreshed weekly, after the close, on this
    # same timer (read-only market data; runs in the background).
    setup_validation = None
    data_backup = None
    try:
        if _now_utc().hour >= 20 and backups.due():
            data_backup = backups.create("daily (cron)")
            if data_backup and data_backup.get("created"):
                import offsite_backup
                offsite_result = offsite_backup.push_created(data_backup)
                if not offsite_result.get("uploaded"):
                    logger.warning("off-site backup was not uploaded: %s", offsite_result.get("reason"))
    except Exception as error:  # noqa: BLE001 - a backup failure must never affect trading
        logger.warning("data backup failed: %s", error)
    setup_forward = None
    try:
        if setups_service.forward_tracking_due():
            setup_forward = setups_service.run_forward_tracking(list(CORE_SCAN_UNIVERSE))
    except Exception as error:  # noqa: BLE001 - research must never affect trading
        logger.warning("setup forward tracking failed: %s", error)
    try:
        if _now_utc().hour >= 20 and setups_service.validation_is_stale():
            setup_validation = setups_service.start_validation(list(CORE_SCAN_UNIVERSE), reason="weekly refresh (cron)")
    except Exception as error:  # noqa: BLE001 - research refresh must never affect trading
        logger.warning("setup validation refresh not started: %s", error)
    intel_summary = None
    try:
        import market_intel

        intel_summary = market_intel.summary()
    except Exception as error:  # noqa: BLE001 - the weekly read must never affect trading
        logger.warning("market intel summary failed: %s", error)
    return _api_success({"ran_for_users": len(results), "results": results, "setup_validation": setup_validation,
                         "data_backup": data_backup, "setup_forward": setup_forward, "market_intel": intel_summary},
                        ok=True, ran_for_users=len(results))

@_route_bp.route("/api/autonomy/fast-monitor-trigger", methods=["POST"])
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
            try:
                _maybe_run_broker_reconciliation(user_id)
            except Exception as error:  # noqa: BLE001 - a read-only report must never affect monitoring
                logger.warning("broker reconciliation failed for a user: %s", error)

    record_fast_monitor_run_completed(
        run_id,
        entries_checked=total_entries_checked,
        still_transitional=total_still_transitional,
        failures_by_account=failures_by_account,
    )

    return _api_success({"ran_for_users": len(results), "results": results}, ok=True, ran_for_users=len(results))

@_route_bp.route("/api/autonomy/continuous-monitor-tick", methods=["POST"])
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

@_route_bp.route("/api/autonomy/monitor-health", methods=["GET"])
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

@_route_bp.route("/api/accounts", methods=["GET"])
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
        "emergency_kill_switch_placeholder": False,
    }
    return _api_success(
        {"accounts": accounts, "safety": safety, "broker_framework": broker_framework, "webull_configured": is_webull_configured(user_id)},
        accounts=accounts,
        safety=safety,
        webull_configured=is_webull_configured(user_id),
    )

@_route_bp.route("/api/accounts/connect", methods=["POST"])
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

@_route_bp.route("/api/accounts/disconnect", methods=["POST"])
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

@_route_bp.route("/api/accounts/test", methods=["POST"])
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

@_route_bp.route("/api/accounts/webull-credentials", methods=["POST"])
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

@_route_bp.route("/api/accounts/anthropic-credentials", methods=["POST"])
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


@_route_bp.route("/api/confirm-entry/<token>", methods=["GET", "POST"])
def api_confirm_entry(token: str):
    """Phone button. The token carries Enter or Skip. No session: the signature is the credential."""
    def _price_for(symbol):
        try:
            return alpaca_data.get_latest_trade_price(str(symbol or ""))
        except Exception:
            return None

    try:
        body = confirm_entry.redeem(token, now=_now_utc(), price_for=_price_for)
    except confirm_entry.TokenError as error:
        return (error.message, 400, {"Content-Type": "text/plain; charset=utf-8"})
    if str(body.get("act")) == "skip":
        suggestion_ledger.update(
            str(body.get("sid") or ""),
            decision="skipped",
            skip_category="user_skip",
            reason="Skip",
        )
        return ("Skipped. Nothing was ordered.", 200, {"Content-Type": "text/plain; charset=utf-8"})
    try:
        message = _execute_confirmed_entry(body)
    except Exception as error:
        current = next(
            (row for row in suggestion_ledger.list_rows(str(body.get("uid") or "")) if row.get("id") == body.get("sid")),
            None,
        )
        if current is None or current.get("decision") == "awaiting_confirm":
            suggestion_ledger.update(
                str(body.get("sid") or ""),
                decision="skipped",
                skip_category="confirm_failed",
                reason=str(error),
            )
        return (str(error), 400, {"Content-Type": "text/plain; charset=utf-8"})
    return (message, 200, {"Content-Type": "text/plain; charset=utf-8"})
