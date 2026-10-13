"""Pages and JSON for user strategies. Registered from app.py. The scan is not involved."""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, redirect, render_template, request, url_for

from . import compare, evaluate, intake, store
from .model import MAX_RULES, form_slots, rule_catalog, scoring_enabled

user_strategies_bp = Blueprint("user_strategies", __name__)


def _user_id() -> str:
    from app import _current_user_id

    return _current_user_id()


def _ok(data: Dict[str, Any], status: int = 200):
    from app import _api_success

    return _api_success(data, status_code=status, ok=True)


def _fail(message: str, status: int = 400, code: str = "validation_error"):
    from app import _api_failure

    return _api_failure(message, status_code=status, error_code=code, ok=False)


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    return request.headers.get("Accept", "").startswith("application/json")


def _rules_from_form() -> list:
    kinds = request.form.getlist("rule_kind")
    values = request.form.getlist("rule_value")
    rules = []
    for kind, value in zip(kinds, values):
        if not str(kind or "").strip():
            continue
        rule: Dict[str, Any] = {"kind": str(kind).strip()}
        if str(value).strip() != "":
            rule["value"] = value
        rules.append(rule)
    return rules


def _payload_from_request() -> Dict[str, Any]:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("Strategy must be an object.")
        return payload
    paper_values = request.form.getlist("paper_opt_in")
    payload = {
        "name": request.form.get("name", ""),
        "description": request.form.get("description", ""),
        "style": request.form.get("style", ""),
        "direction": request.form.get("direction", ""),
        "timeframe": request.form.get("timeframe", ""),
        "instruments": request.form.get("instruments", ""),
        "tickers": request.form.get("tickers", ""),
        "max_hold_days": request.form.get("max_hold_days", ""),
        "stop_percent": request.form.get("stop_percent", ""),
        "target_type": request.form.get("target_type", "none"),
        "target_value": request.form.get("target_value", ""),
        "entry_rules": _rules_from_form(),
        "paper_opt_in": "1" in paper_values,
        "source": request.form.get("source") or "form",
    }
    draft = store.load_draft(_user_id())
    if draft and draft.get("draft"):
        pending = draft["draft"]
        if pending.get("imported_trades") and "imported_trades" not in payload:
            payload["imported_trades"] = pending.get("imported_trades")
            payload["import_note"] = pending.get("import_note", "")
        if not payload["source"] or payload["source"] == "form":
            payload["source"] = pending.get("source") or payload["source"]
    return payload


def _page_context() -> Dict[str, Any]:
    from app import _build_page_context

    return _build_page_context(include_market_scan=False, include_opportunities=False, include_options=False)


def _view(user_id: str, strategy: Dict[str, Any]) -> Dict[str, Any]:
    record = compare.own_record(user_id, strategy)
    returns = []
    # own_record already computed the figure; re-read is cheap and keeps the
    # anonymous aggregate aligned with the same reconciled sample.
    sample = (record.get("reconciled") or {}).get("sample_size") or 0
    if sample:
        from autonomy.closed_trades import list_closed_trades
        from calibration import _pnl_percent_for_closed_trade

        name = str(strategy.get("name") or "").strip().casefold()
        for trade in list_closed_trades(user_id):
            if str(trade.get("strategy") or "").strip().casefold() != name:
                continue
            percent = _pnl_percent_for_closed_trade(trade)
            if percent is not None:
                returns.append(percent)
    store.refresh_aggregate_real(user_id, strategy, returns)
    return {
        "record": record,
        "comparison": compare.compare_strategy(strategy, store.viewer_anon_ids(user_id)),
    }


@user_strategies_bp.route("/my-strategies", methods=["GET"])
def my_strategies_page():
    user_id = _user_id()
    strategies = store.list_strategies(user_id)
    selected_id = request.args.get("strategy", "")
    selected = store.get_strategy(user_id, selected_id) if selected_id else None
    draft_bundle = store.load_draft(user_id)
    form_strategy = (draft_bundle or {}).get("draft") or selected or {}
    detail = _view(user_id, selected) if selected else None
    context = _page_context()
    context.update(
        {
            "strategies": strategies,
            "selected": selected,
            "form_strategy": form_strategy,
            "form_slots": form_slots(form_strategy),
            "rule_catalog": rule_catalog(),
            "draft_note": (draft_bundle or {}).get("note", ""),
            "draft_source": (draft_bundle or {}).get("source", ""),
            "detail": detail,
            "scoring_enabled": scoring_enabled(),
            "max_rules": MAX_RULES,
            "form_error": request.args.get("error", ""),
        }
    )
    return render_template("my_strategies.html", **context)


@user_strategies_bp.route("/my-strategies/save", methods=["POST"])
def save_strategy():
    user_id = _user_id()
    strategy_id = (request.form.get("id") or request.args.get("id") or "").strip()
    if request.is_json:
        body = request.get_json(silent=True) or {}
        strategy_id = str(body.get("id") or strategy_id)
    try:
        payload = _payload_from_request()
        if strategy_id:
            saved = store.update_strategy(user_id, strategy_id, payload)
        else:
            saved = store.create_strategy(user_id, payload)
        store.clear_draft(user_id)
    except ValueError as error:
        if _wants_json():
            return _fail(str(error))
        return redirect(url_for("user_strategies.my_strategies_page", error=str(error), strategy=strategy_id))
    if _wants_json():
        return _ok({"strategy": saved}, 200 if strategy_id else 201)
    return redirect(url_for("user_strategies.my_strategies_page", strategy=saved["id"]))


@user_strategies_bp.route("/my-strategies/<strategy_id>/delete", methods=["POST"])
def delete_strategy(strategy_id: str):
    user_id = _user_id()
    try:
        store.delete_strategy(user_id, strategy_id)
    except ValueError as error:
        if _wants_json():
            return _fail(str(error), 404)
        return redirect(url_for("user_strategies.my_strategies_page", error=str(error)))
    if _wants_json():
        return _ok({"deleted": strategy_id})
    return redirect(url_for("user_strategies.my_strategies_page"))


@user_strategies_bp.route("/my-strategies/translate", methods=["POST"])
def translate_strategy():
    user_id = _user_id()
    if request.is_json:
        description = str((request.get_json(silent=True) or {}).get("description") or "")
    else:
        description = request.form.get("description", "")
    from anthropic_credentials import get_anthropic_api_key

    try:
        result = intake.translate_description(description, get_anthropic_api_key(user_id))
    except ValueError as error:
        if _wants_json():
            return _fail(str(error))
        return redirect(url_for("user_strategies.my_strategies_page", error=str(error)))
    store.save_draft(user_id, result["draft"], result["note"], result["source"])
    if _wants_json():
        return _ok({"draft": result["draft"], "note": result["note"], "source": result["source"], "anthropic_used": result["anthropic_used"]})
    return redirect(url_for("user_strategies.my_strategies_page", draft=1))


@user_strategies_bp.route("/my-strategies/import-csv", methods=["POST"])
def import_csv():
    user_id = _user_id()
    upload = request.files.get("csv_file")
    if upload and upload.filename:
        text = upload.read().decode("utf-8", errors="replace")
    elif request.is_json:
        text = str((request.get_json(silent=True) or {}).get("csv") or "")
    else:
        text = request.form.get("csv_text", "")
    try:
        result = intake.parse_trade_csv(text)
    except ValueError as error:
        if _wants_json():
            return _fail(str(error))
        return redirect(url_for("user_strategies.my_strategies_page", error=str(error)))
    store.save_draft(user_id, result["draft"], result["note"], "trade_import")
    if _wants_json():
        return _ok({"draft": result["draft"], "note": result["note"], "trades": len(result["trades"])})
    return redirect(url_for("user_strategies.my_strategies_page", draft=1))


@user_strategies_bp.route("/my-strategies/import-broker", methods=["POST"])
def import_broker():
    user_id = _user_id()
    try:
        orders = intake.read_broker_orders(user_id)
        result = intake.pair_broker_fills(orders)
    except ValueError as error:
        if _wants_json():
            return _fail(str(error))
        return redirect(url_for("user_strategies.my_strategies_page", error=str(error)))
    except Exception:
        message = "Webull fill history could not be read. No order was placed or changed."
        if _wants_json():
            return _fail(message, 502, "broker_read_failed")
        return redirect(url_for("user_strategies.my_strategies_page", error=message))
    store.save_draft(user_id, result["draft"], result["note"], "trade_import")
    if _wants_json():
        return _ok({"draft": result["draft"], "note": result["note"], "trades": len(result["trades"])})
    return redirect(url_for("user_strategies.my_strategies_page", draft=1))


@user_strategies_bp.route("/my-strategies/dismiss-draft", methods=["POST"])
def dismiss_draft():
    store.clear_draft(_user_id())
    if _wants_json():
        return _ok({"dismissed": True})
    return redirect(url_for("user_strategies.my_strategies_page"))


@user_strategies_bp.route("/my-strategies/<strategy_id>/backtest", methods=["POST"])
def backtest_strategy(strategy_id: str):
    user_id = _user_id()
    strategy = store.get_strategy(user_id, strategy_id)
    if strategy is None:
        if _wants_json():
            return _fail("Strategy not found.", 404, "not_found")
        return redirect(url_for("user_strategies.my_strategies_page", error="Strategy not found."))
    body = request.get_json(silent=True) if request.is_json else {}
    body = body or {}
    lookback = body.get("lookback_months") if request.is_json else request.form.get("lookback_months", 6)
    try:
        lookback_months = int(lookback or 6)
    except (TypeError, ValueError):
        return _fail("Lookback months must be a whole number.") if _wants_json() else redirect(
            url_for("user_strategies.my_strategies_page", strategy=strategy_id, error="Lookback months must be a whole number.")
        )
    raw_tickers = body.get("tickers") if request.is_json else request.form.get("tickers", "")
    tickers = strategy.get("tickers") or []
    if raw_tickers:
        from .model import normalize_strategy

        try:
            # Ticker check only. Do not re-validate paper opt-in, or a backtest
            # would fail after the scoring flag is turned back off.
            tickers = normalize_strategy(
                {
                    "name": strategy["name"],
                    "entry_rules": strategy.get("entry_rules") or [],
                    "direction": strategy.get("direction"),
                    "style": strategy.get("style"),
                    "timeframe": strategy.get("timeframe"),
                    "instruments": strategy.get("instruments"),
                    "max_hold_days": strategy.get("max_hold_days"),
                    "stop_percent": strategy.get("stop_percent"),
                    "target": strategy.get("target") or {"type": "none"},
                    "tickers": raw_tickers,
                }
            )["tickers"]
        except ValueError as error:
            if _wants_json():
                return _fail(str(error))
            return redirect(url_for("user_strategies.my_strategies_page", strategy=strategy_id, error=str(error)))
    if not tickers:
        message = "Add at least one ticker before running a backtest."
        if _wants_json():
            return _fail(message)
        return redirect(url_for("user_strategies.my_strategies_page", strategy=strategy_id, error=message))
    frames = {}
    for ticker in tickers:
        try:
            frames[ticker] = evaluate.load_daily_bars(ticker, lookback_months)
        except Exception:
            frames[ticker] = None
    usable = {ticker: frame for ticker, frame in frames.items() if frame is not None and len(getattr(frame, "index", [])) > 0}
    if not usable:
        message = "Market data for those tickers could not be loaded. No backtest was run."
        if _wants_json():
            return _fail(message, 502, "market_data_unavailable")
        return redirect(url_for("user_strategies.my_strategies_page", strategy=strategy_id, error=message))
    try:
        result = evaluate.run_strategy_backtest(strategy, usable, lookback_months=lookback_months)
    except ValueError as error:
        if _wants_json():
            return _fail(str(error))
        return redirect(url_for("user_strategies.my_strategies_page", strategy=strategy_id, error=str(error)))
    missing = [ticker for ticker, frame in frames.items() if ticker not in usable]
    if missing:
        result["errors"] = list(result.get("errors") or []) + [f"{ticker}: market data could not be loaded." for ticker in missing]
    saved = store.save_backtest(user_id, strategy_id, result)
    if _wants_json():
        return _ok({"strategy": saved, "backtest": result})
    return redirect(url_for("user_strategies.my_strategies_page", strategy=strategy_id))


@user_strategies_bp.route("/api/my-strategies", methods=["GET"])
def api_list_strategies():
    user_id = _user_id()
    return _ok({"strategies": store.list_strategies(user_id), "scoring_enabled": scoring_enabled()})


@user_strategies_bp.route("/api/my-strategies/<strategy_id>", methods=["GET"])
def api_get_strategy(strategy_id: str):
    user_id = _user_id()
    strategy = store.get_strategy(user_id, strategy_id)
    if strategy is None:
        return _fail("Strategy not found.", 404, "not_found")
    view = _view(user_id, strategy)
    return _ok({"strategy": strategy, **view})


@user_strategies_bp.route("/api/my-strategies/paper-catalog", methods=["GET"])
def api_paper_catalog():
    catalog = store.paper_catalog(_user_id())
    return _ok(
        {
            "scoring_enabled": scoring_enabled(),
            "strategies": catalog,
            "places_orders": False,
            "read_by_scan": False,
            "note": (
                "Paper scoring is off, so this list is empty."
                if not scoring_enabled()
                else "These strategies are recorded for paper / approval scoring only. The autonomous scan does not read them, and nothing here places an order."
            ),
        }
    )
