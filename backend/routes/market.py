# Loaded by routes.load_into into the Flask app module. Not imported directly.

@_route_bp.route("/market-scanner")
@_route_bp.route("/scanner")
def scanner_page() -> str:
    return render_template("scanner.html", **_build_page_context())

@_route_bp.route("/reversal-map")
@_route_bp.route("/support-resistance")
def reversal_map_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=focus_ticker)
    context["analysis_section"] = "support_resistance"
    return render_template("reversal_map.html", **context)

@_route_bp.route("/trend-detection")
@_route_bp.route("/volume-scanner")
def trend_detection_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=focus_ticker)
    context["analysis_section"] = "volume_intelligence"
    return render_template("trend_detection.html", **context)

@_route_bp.route("/lookup")
@_route_bp.route("/lookup/<ticker>")
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

@_route_bp.route("/options")
def options_page() -> str:
    searched_ticker = request.args.get("ticker", "").strip().upper()
    context = _build_page_context(focus_ticker=searched_ticker)
    if searched_ticker:
        context["searched_ticker"] = searched_ticker
    return render_template("options.html", **context)

@_route_bp.route("/api/options/chain", methods=["GET"])
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

@_route_bp.route("/candle-brain")
def candle_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    # include_market_scan=False, include_opportunities=False - candle_brain.html
    # renders no server-side candle_rows/pattern_rows/scanner_rows/
    # opportunities data at all (it's fetched client-side by ticker); this
    # page was paying the full market-scan pipeline for nothing.
    context = _build_page_context(focus_ticker=focus_ticker, include_market_scan=False, include_opportunities=False)
    context["analysis_section"] = "candle_brain"
    return render_template("candle_brain.html", **context)

@_route_bp.route("/pattern-brain")
def pattern_brain_page() -> str:
    focus_ticker = request.args.get("ticker", "").strip().upper()
    # include_market_scan=False, include_opportunities=False - same reason
    # as candle_brain_page: this page renders no server-side scanner/
    # opportunities data.
    context = _build_page_context(focus_ticker=focus_ticker, include_market_scan=False, include_opportunities=False)
    context["analysis_section"] = "pattern_brain"
    return render_template("pattern_brain.html", **context)

@_route_bp.route("/neural-engine")
def neural_engine_page() -> str:
    # neural_engine.html only reads status_summary (always computed
    # regardless of these flags, and already degrades safely to
    # "Monitoring"/"Healthy" on empty scanner data) - it never renders
    # scanner_rows/opportunities directly.
    return render_template(
        "neural_engine.html", **_build_page_context(include_market_scan=False, include_opportunities=False)
    )

@_route_bp.route("/backtest")
def backtest_page() -> str:
    # backtest.html is fully client-side/JS-driven - it renders no
    # server-side scanner/opportunities context at all.
    return render_template("backtest.html", **_build_page_context(include_market_scan=False, include_opportunities=False))

@_route_bp.route("/api/backtest/run", methods=["POST"])
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

@_route_bp.route("/api/ticker-search", methods=["GET"])
@api_guard
def api_ticker_search():
    query = request.args.get("q", "").strip()
    if len(query) < 1:
        return _api_success({"results": []}, results=[], ok=True)

    cache_key = query.lower()
    cached = TICKER_SEARCH_CACHE.get(cache_key)
    if cached and cached.get("expires_at") and cached["expires_at"] > _now_utc():
        return _api_success({"results": cached["results"]}, results=cached["results"], ok=True)

    try:
        results = alpaca_data.search_assets(query, limit=8)
    except Exception as error:
        logger.warning("Ticker search failed for %r: %s", query, error)
        results = []
    if results:
        TICKER_SEARCH_CACHE[cache_key] = {
            "results": results,
            "expires_at": _now_utc() + timedelta(seconds=TICKER_SEARCH_CACHE_SECONDS),
        }
    return _api_success({"results": results}, results=results, ok=True)

@_route_bp.route("/api/scanner", methods=["GET"])
@api_guard
def api_scanner():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    scanner_rows, scanner_errors, last_updated = get_market_data(force_refresh=force_refresh)
    summary = MARKET_CACHE.get("summary") or {}
    return _api_success(
        {"rows": scanner_rows, "errors": scanner_errors, "last_updated": last_updated, "summary": summary},
        rows=scanner_rows,
        errors=scanner_errors,
        last_updated=last_updated,
        summary=summary,
        ok=True,
    )

@_route_bp.route("/api/analysis/<section>/tickers", methods=["GET"])
@api_guard
def api_analysis_section_get(section: str):
    focus_ticker = request.args.get("focus", "").strip().upper()
    payload = _build_section_payload(_current_user_id(), section, focus_ticker=focus_ticker)
    return _api_success(payload, **payload, ok=True)

@_route_bp.route("/api/analysis/<section>/tickers", methods=["POST"])
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

@_route_bp.route("/api/analysis/<section>/tickers/<ticker>", methods=["DELETE"])
@api_guard
def api_analysis_section_remove(section: str, ticker: str):
    user_id = _current_user_id()
    try:
        remove_section_ticker(user_id, section, ticker, _default_analysis_section_tickers(user_id))
    except ValueError as error:
        raise ValidationError(str(error)) from error
    payload = _build_section_payload(user_id, section)
    return _api_success(payload, **payload, ok=True)

@_route_bp.route("/api/live-data-status", methods=["GET"])
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

@_route_bp.route("/api/options/<ticker>", methods=["GET"])
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

@_route_bp.route("/api/strategy/<ticker>", methods=["GET"])
@api_guard
def api_strategy_ticker(ticker: str):
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    payload = get_strategy_data_for_ticker(ticker=ticker, force_refresh=force_refresh)
    return _api_success(payload, strategy=payload, ok=True)

@_route_bp.route("/api/chart-levels/watchlist", methods=["GET"])
@api_guard
def api_chart_levels_watchlist():
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    query_tickers = [item.strip().upper() for item in request.args.get("tickers", "").split(",") if item.strip()]
    tickers = query_tickers or get_watchlist_tickers(_current_user_id()) or [row["ticker"] for row in get_market_data(force_refresh=False)[0][:6]]
    rows = [get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh) for ticker in tickers]
    return _api_success({"rows": rows, "count": len(rows)}, rows=rows, count=len(rows), ok=True)

@_route_bp.route("/api/chart-levels/<ticker>", methods=["GET"])
@api_guard
def api_chart_levels_ticker(ticker: str):
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    payload = get_chart_levels_for_ticker(ticker=ticker, force_refresh=force_refresh)
    return _api_success(payload, chart_levels=payload, ok=True)

@_route_bp.route("/api/chart-history/<ticker>", methods=["GET"])
@api_guard
def api_chart_history_ticker(ticker: str):
    """Raw OHLC candles for the real dashboard chart (2026-09-04) - paired
    with /api/chart-levels/<ticker> above by the frontend (candles here,
    computed support/resistance/EMA/VWAP there), kept as two separate
    single-responsibility endpoints rather than one combined payload.

    ?interval= (added 2026-09-04, then refined same-day from a coarser
    "range" concept to real per-candle granularity per direct feedback -
    "from 1min to daily... the hourly, 15min, 30min, 10min and even the
    5min one") selects one of CHART_INTERVAL_TO_PERIOD's keys
    (1m/5m/10m/15m/30m/1h/1d) - defaults to 1d (daily bars, this
    endpoint's original, only behavior before any selector existed) for
    any caller that doesn't pass it."""
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    chart_interval = request.args.get("interval", DEFAULT_CHART_INTERVAL)
    payload = get_chart_history_for_ticker(ticker=ticker, chart_interval=chart_interval, force_refresh=force_refresh)
    return _api_success(payload, chart_history=payload, ok=True)

@_route_bp.route("/api/reversal-map", methods=["GET"])
def api_reversal_map():
    # Only reads reversal_rows/trend_errors below - include_opportunities=False
    # skips the entire strategy+chart+options pipeline this route was
    # previously paying for on every poll without ever touching its output.
    context = _build_page_context(include_reversal=True, include_trend=True, include_opportunities=False)
    return jsonify({"rows": context["reversal_rows"], "errors": context["trend_errors"]})

@_route_bp.route("/api/trend-detection", methods=["GET"])
def api_trend_detection():
    # Same reasoning as api_reversal_map just above - only reads
    # trend_rows/trend_errors, never upcoming_opportunities.
    context = _build_page_context(include_reversal=True, include_trend=True, include_opportunities=False)
    return jsonify({"rows": context["trend_rows"], "errors": context["trend_errors"]})

@_route_bp.route("/api/patterns", methods=["GET"])
def api_patterns():
    context = _build_page_context(include_patterns=True)
    return jsonify({"candles": context["candle_rows"], "patterns": context["pattern_rows"], "errors": context["pattern_errors"]})

@_route_bp.route("/api/setups/coverage", methods=["GET"])
@api_guard
def api_setups_coverage():
    """Every setup the engine can detect, its rules and its validation
    status per timeframe - plus named formations it cannot detect yet."""
    return _api_success(setups_service.coverage())

@_route_bp.route("/api/setups/scan", methods=["GET"])
@api_guard
def api_setups_scan():
    """Setup discovery for up to 12 symbols (read-only market data)."""
    symbols = [s.strip().upper() for s in (request.args.get("symbols") or "").split(",") if s.strip()][:12]
    if not symbols:
        return _api_failure("Pass symbols=AAPL,MSFT", status_code=400, error_code="missing_symbols", ok=False)
    timeframes = [t for t in (request.args.get("timeframes") or "1d,5m").split(",") if t in SETUP_TIMEFRAMES] or ["1d"]
    results = setups_service.analyze(symbols, timeframes)
    for result in results:
        for opportunity in result["opportunities"]:
            opportunity.pop("primary_detection", None)
    return _api_success({"results": results, "timeframes": timeframes,
                         "qualifying": [r["trade_candidate"]["symbol"] for r in results if r.get("trade_candidate")]})

@_route_bp.route("/api/neural/status", methods=["GET"])
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
