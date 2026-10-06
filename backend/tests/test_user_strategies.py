"""User strategies: form, import, backtest, honest samples, anonymous comparison."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import app as pluto_app
import auth
from user_strategies import compare, evaluate, intake, model, store


def _user(suffix: str) -> str:
    user = auth.register_user(f"strat-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _client(user_id: str):
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client


def _bars(closes, opens=None) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=len(closes))
    close = pd.Series(closes, dtype="float64")
    open_ = pd.Series(closes if opens is None else opens, dtype="float64")
    return pd.DataFrame(
        {
            "Open": open_.to_numpy(),
            "High": pd.concat([open_, close], axis=1).max(axis=1).to_numpy() + 0.5,
            "Low": pd.concat([open_, close], axis=1).min(axis=1).to_numpy() - 0.5,
            "Close": close.to_numpy(),
            "Volume": [1_000_000] * len(closes),
        },
        index=index,
    )


def _strategy(**overrides):
    payload = {
        "name": "Opening drive",
        "style": "momentum",
        "direction": "long",
        "timeframe": "daily",
        "instruments": "equity",
        "tickers": ["AAPL"],
        "entry_rules": [{"kind": "day_change_above", "value": 5}],
        "max_hold_days": 5,
        "stop_percent": None,
        "target_type": "none",
    }
    payload.update(overrides)
    return payload


def _closed(strategy: str, net: float, trade_id: str) -> dict:
    return {
        "ticker": "AAPL",
        "strategy": strategy,
        "net_realized_pnl": net,
        "average_entry_price": 100.0,
        "exited_quantity": 1,
        "filled_quantity": 1,
        "pnl_status": "complete",
        "trade_id": trade_id,
    }


def test_unknown_rule_and_bad_stop_are_rejected():
    with pytest.raises(ValueError, match="Unknown entry rule"):
        model.normalize_strategy(_strategy(entry_rules=[{"kind": "buy_the_dip"}]))
    with pytest.raises(ValueError, match="Stop percent"):
        model.normalize_strategy(_strategy(stop_percent=80))
    with pytest.raises(ValueError, match="name"):
        model.normalize_strategy(_strategy(name="  "))


def test_strategies_stay_inside_one_account(user_id, other_user_id):
    saved = store.create_strategy(user_id, _strategy())
    assert store.get_strategy(other_user_id, saved["id"]) is None
    assert store.list_strategies(other_user_id) == []
    store.delete_strategy(user_id, saved["id"])
    with pytest.raises(ValueError):
        store.delete_strategy(user_id, saved["id"])


def test_backtest_fixed_hold_is_labeled_and_uses_next_open():
    closes = [100.0] * 80 + [106.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0]
    opens = [100.0] * 81 + [100.0, 107.0, 108.0, 109.0, 110.0, 111.0]
    frame = _bars(closes, opens)
    result = evaluate.run_strategy_backtest(
        model.normalize_strategy(_strategy()),
        {"AAPL": frame},
        lookback_months=24,
    )
    assert result["label"] == "Backtest"
    assert result["is_backtest"] is True
    assert result["probability"] is None
    assert "not a probability" in result["probability_note"]
    assert "Backtest" in result["methodology_note"]
    assert result["combined"]["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["entry_price"] == 100.0
    assert trade["exit_reason"] == "hold"
    assert trade["pnl_percent"] == 11.0
    assert "proven" not in json.dumps(result).lower()


def test_stop_fills_before_target_when_both_are_in_the_same_bar():
    closes = [100.0] * 80 + [106.0] + [106.0] * 6
    opens = [100.0] * 81 + [106.0] * 6
    frame = _bars(closes, opens)
    frame.loc[frame.index[81], "Low"] = 90
    frame.loc[frame.index[81], "High"] = 130
    strategy = model.normalize_strategy(_strategy(stop_percent=5, target_type="percent", target_value=10))
    result = evaluate.run_on_frame(strategy, frame, ticker="AAPL", lookback_months=24)
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["trades"][0]["pnl_percent"] == -5.0


def test_short_stop_and_too_few_bars():
    closes = [100.0] * 80 + [90.0] + [90.0] * 6
    opens = [100.0] * 81 + [90.0] * 6
    frame = _bars(closes, opens)
    frame.loc[frame.index[81], "High"] = 120
    frame.loc[frame.index[81], "Low"] = 80
    strategy = model.normalize_strategy(
        _strategy(
            direction="short",
            entry_rules=[{"kind": "day_change_below", "value": -5}],
            stop_percent=5,
            target_type="percent",
            target_value=10,
        )
    )
    result = evaluate.run_on_frame(strategy, frame, ticker="AAPL", lookback_months=24)
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["trades"][0]["pnl_percent"] == -5.0
    thin = evaluate.run_on_frame(strategy, frame.iloc[:10], ticker="AAPL", lookback_months=24)
    assert thin["trades"] == []
    assert "Not enough daily bars" in thin["error"]


def test_both_directions_are_not_simulated_as_two_books():
    frame = _bars([100.0] * 90)
    strategy = model.normalize_strategy(_strategy(direction="both"))
    result = evaluate.run_on_frame(strategy, frame, ticker="AAPL", lookback_months=24)
    assert result["trades"] == []
    assert "long or short" in result["error"]


def test_probability_follows_the_thirty_trade_rule(user_id):
    strategy = store.create_strategy(user_id, _strategy(name="Desk fade"))
    small = [_closed("Desk fade", 1.0, f"small-{i}") for i in range(5)]

    def _listed(uid):
        return small if uid == user_id else []

    with patch("autonomy.closed_trades.list_closed_trades", side_effect=_listed):
        record = compare.own_record(user_id, strategy)
    assert record["reconciled"]["reliable"] is False
    assert record["reconciled"]["win_probability_percent"] is None
    assert "not reliably estimated" in record["reconciled"]["note"]

    large = [_closed("Desk fade", 2.0, f"large-{i}") for i in range(30)]
    with patch("autonomy.closed_trades.list_closed_trades", side_effect=lambda uid: large if uid == user_id else []):
        record = compare.own_record(user_id, strategy)
    assert record["reconciled"]["reliable"] is True
    assert record["reconciled"]["sample_size"] == 30
    assert record["reconciled"]["win_probability_percent"] == 100.0


def test_import_is_not_treated_as_a_probability(user_id):
    trades = [
        {
            "ticker": "AAPL",
            "side": "long",
            "pnl_percent": 1.0,
            "closed": True,
            "source": "csv",
            "entry_date": "",
            "exit_date": "",
            "entry_price": 1,
            "exit_price": 1,
        }
        for _ in range(30)
    ]
    strategy = store.create_strategy(user_id, _strategy(name="Imported desk", imported_trades=trades, source="trade_import"))
    record = compare.own_record(user_id, strategy)
    assert record["imported"]["sample_size"] == 30
    assert record["imported"]["reliable"] is False
    assert record["imported"]["win_probability_percent"] is None
    assert "not a probability" in record["imported"]["note"]


def test_builtin_name_collision_is_called_out(user_id):
    strategy = store.create_strategy(user_id, _strategy(name="Breakout"))
    record = compare.own_record(user_id, strategy)
    assert "built-in strategy Breakout" in record["reconciled"]["name_collision"]


def test_similar_strategies_rank_real_results_ahead_of_backtests(user_id):
    strategy = store.create_strategy(
        user_id,
        _strategy(name="Fast tape", style="momentum", direction="both", timeframe="intraday", max_hold_days=1),
    )
    calibration = {
        "strategy_stats": {
            "Momentum": {"trade_count": 40, "win_rate_percent": 55.0, "avg_return_percent": 1.2, "trusted": True},
            "Breakout": {"trade_count": 40, "win_rate_percent": 60.0, "avg_return_percent": 9.0, "trusted": True},
        }
    }
    returns = {"Momentum": [1.5] * 30}
    with patch("user_strategies.compare.get_calibration", return_value=calibration), patch(
        "user_strategies.compare.closed_trade_returns_by_strategy", return_value=returns
    ):
        result = compare.compare_strategy(strategy, set())
    assert result["built_in"][0]["name"] == "Momentum"
    assert result["built_in"][0]["real"]["reliable"] is True
    assert result["recommendations"][0]["basis"] == "real_closed_trades"
    assert result["recommendations"][0]["name"] == "Momentum"
    blob = json.dumps(result).lower()
    assert "proven" not in blob
    assert "backtest" in blob


def test_backtest_recommendation_is_labeled_when_real_sample_is_small(user_id):
    strategy = store.create_strategy(
        user_id,
        _strategy(name="Fast tape 2", style="momentum", direction="both", timeframe="intraday", max_hold_days=1),
    )
    calibration = {"strategy_stats": {"Momentum": {"trade_count": 12, "win_rate_percent": 50, "avg_return_percent": 2.0}}}
    with patch("user_strategies.compare.get_calibration", return_value=calibration), patch(
        "user_strategies.compare.closed_trade_returns_by_strategy", return_value={}
    ):
        result = compare.compare_strategy(strategy, set())
    pick = next(item for item in result["recommendations"] if item["name"] == "Momentum")
    assert pick["basis"] == "backtest"
    assert "backtest" in pick["caution"].lower()
    assert "not reliably estimated" in pick["caution"]


def test_anonymous_cohort_hides_small_groups_and_identity(user_id, other_user_id):
    def _make(uid, name):
        saved = store.create_strategy(
            uid, _strategy(name=name, style="breakout", direction="long", timeframe="daily", max_hold_days=5)
        )
        result = {
            "label": "Backtest",
            "is_backtest": True,
            "combined": {
                "trade_count": 10,
                "avg_return_percent": 1.5,
                "win_rate_percent": 50,
                "win_count": 5,
                "loss_count": 5,
                "total_return_percent": 15,
                "profit_factor": 1,
                "max_drawdown_percent": 1,
                "equity_curve": [],
            },
            "trades": [],
            "errors": [],
            "methodology_note": "Backtest.",
            "probability_note": "not a probability",
            "probability": None,
        }
        return store.save_backtest(uid, saved["id"], result)

    viewer = _make(user_id, "Mine")
    _make(other_user_id, "Theirs hidden name")
    third = _user("third" + user_id[:4])
    fourth = _user("fourth" + user_id[:4])
    cohort = compare.compare_strategy(viewer, store.viewer_anon_ids(user_id))["other_users"]
    assert cohort["shown"] is False
    assert "hidden" in cohort["note"]

    _make(third, "Third book")
    _make(fourth, "Fourth book")
    cohort = compare.compare_strategy(viewer, store.viewer_anon_ids(user_id))["other_users"]
    assert cohort["shown"] is True
    assert cohort["backtest"]["median_avg_return_percent"] == 1.5
    assert cohort["backtest"]["is_backtest"] is True
    assert cohort["real"]["reliable"] is False
    assert "not reliably estimated" in cohort["real"]["note"]
    raw = store.AGGREGATE_FILE.read_text(encoding="utf-8")
    assert user_id not in raw
    assert "Theirs hidden name" not in raw
    assert "AAPL" not in raw
    allowed = {
        "anon_id",
        "style",
        "direction",
        "timeframe",
        "hold_bucket",
        "instrument",
        "backtest_trade_count",
        "backtest_avg_return_percent",
        "real_trade_count",
        "real_win_count",
        "real_avg_return_percent",
    }
    for row in store.aggregate_rows():
        assert set(row) <= allowed


def test_keyword_draft_only_fills_named_rules_and_skips_the_model(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("model should not be called")

    monkeypatch.setattr(intake, "_model_definition", _boom)
    result = intake.translate_description("Buy AAPL when RSI below 30. Stop of 2%. Hold 5 days. VWAP bounce.", api_key="")
    kinds = {rule["kind"] for rule in result["draft"]["entry_rules"]}
    assert kinds == {"rsi_below"}
    assert result["draft"]["stop_percent"] == 2
    assert result["draft"]["max_hold_days"] == 5
    assert "AAPL" in result["draft"]["tickers"]
    assert result["anthropic_used"] is False
    assert "not a model" in result["note"]
    assert "VWAP" in result["note"]


def test_model_translation_must_fit_the_catalog(monkeypatch):
    monkeypatch.setattr(
        intake,
        "_model_definition",
        lambda *_args, **_kwargs: {
            "name": "Quiet pullback",
            "style": "pullback",
            "direction": "long",
            "timeframe": "daily",
            "instruments": "equity",
            "tickers": ["MSFT"],
            "entry_rules": [{"kind": "price_above_ema20"}],
            "max_hold_days": 4,
            "stop_percent": 1.5,
        },
    )
    result = intake.translate_description("pullbacks", api_key="test-key")
    assert result["anthropic_used"] is True
    assert result["source"] == "plain_english"
    assert result["draft"]["entry_rules"][0]["kind"] == "price_above_ema20"

    monkeypatch.setattr(intake, "_model_definition", lambda *_a, **_k: {"name": "Nope", "entry_rules": [{"kind": "not_a_rule"}]})
    fallback = intake.translate_description("RSI below 40", api_key="test-key")
    assert fallback["anthropic_used"] is False
    assert fallback["draft"]["source"] == "keyword_draft"


def test_csv_and_broker_fills_do_not_invent_entry_rules():
    parsed = intake.parse_trade_csv(
        "ticker,side,entry_date,exit_date,entry_price,exit_price\n"
        "AAPL,buy,2026-01-02,2026-01-07,100,110\n"
        "MSFT,sell,2026-02-02,2026-02-03,50,40\n"
    )
    assert parsed["draft"]["entry_rules"] == []
    assert parsed["trades"][0]["pnl_percent"] == 10
    assert parsed["trades"][1]["side"] == "short"
    assert "not the app's reconciled" in parsed["note"]

    paired = intake.pair_broker_fills(
        [
            {"symbol": "INTC", "side": "BUY", "status": "CANCELLED", "instrument_type": "EQUITY", "filled_quantity": "2", "filled_price": "10"},
            {
                "symbol": "INTC",
                "side": "BUY",
                "status": "FILLED",
                "instrument_type": "EQUITY",
                "filled_quantity": "2",
                "filled_price": "100",
                "filled_time_at": "2026-01-02T15:00:00Z",
            },
            {
                "symbol": "INTC",
                "side": "SELL",
                "status": "FILLED",
                "instrument_type": "EQUITY",
                "filled_quantity": "2",
                "filled_price": "110",
                "filled_time_at": "2026-01-06T15:00:00Z",
            },
            {"symbol": "INTC", "side": "BUY", "status": "FILLED", "instrument_type": "OPTION", "filled_quantity": "1", "filled_price": "3"},
        ]
    )
    closed = [trade for trade in paired["trades"] if trade["closed"]]
    assert len(closed) == 1
    assert closed[0]["pnl_percent"] == 10
    assert paired["draft"]["entry_rules"] == []
    assert "non-equity" in paired["note"]


def test_broker_reader_only_reads_history(monkeypatch):
    monkeypatch.setattr("webull_credentials.get_webull_credentials", lambda _user: {"app_key": "k", "app_secret": "s"})
    monkeypatch.setattr(
        "integrations.webull.get_paper_accounts",
        lambda *_a, **_k: [{"account_id": "acct", "account_class": "INDIVIDUAL_CASH"}],
    )
    seen = {}

    def _history(*_args, **_kwargs):
        seen["history"] = True
        return []

    def _forbid(*_args, **_kwargs):
        raise AssertionError("a broker write was called")

    monkeypatch.setattr("integrations.webull.get_order_history", _history)
    monkeypatch.setattr("integrations.webull.place_stock_order", _forbid)
    monkeypatch.setattr("integrations.webull.place_option_order", _forbid)
    monkeypatch.setattr("integrations.webull.cancel_order", _forbid)
    assert intake.read_broker_orders("user") == []
    assert seen["history"] is True


def test_paper_opt_in_is_off_until_the_flag_is_on(user_id, monkeypatch):
    monkeypatch.delenv("PLUTO_USER_STRATEGY_SCORING", raising=False)
    with pytest.raises(ValueError, match="Paper scoring is off"):
        store.create_strategy(user_id, _strategy(name="Paper one", paper_opt_in=True))
    saved = store.create_strategy(user_id, _strategy(name="Paper two"))
    assert store.paper_catalog(user_id) == []
    monkeypatch.setenv("PLUTO_USER_STRATEGY_SCORING", "1")
    updated = store.update_strategy(user_id, saved["id"], {"paper_opt_in": True})
    catalog = store.paper_catalog(user_id)
    assert catalog[0]["id"] == updated["id"]
    assert catalog[0]["places_orders"] is False
    assert catalog[0]["participation"] == "paper_approval"
    monkeypatch.delenv("PLUTO_USER_STRATEGY_SCORING", raising=False)
    assert store.paper_catalog(user_id) == []
    renamed = store.update_strategy(user_id, saved["id"], {"name": "Paper two renamed"})
    assert renamed["paper_opt_in"] is True
    assert store.paper_catalog(user_id) == []


def test_scan_source_does_not_read_user_strategies():
    root = Path(pluto_app.__file__).resolve().parent
    brain = (root / "brains" / "strategy_brain.py").read_text(encoding="utf-8")
    scanner = (root / "market_scanner.py").read_text(encoding="utf-8")
    assert "user_strategies" not in brain
    assert "user_strategies" not in scanner
    assert "user_strategies" not in pluto_app._run_autonomous_trade_scan_locked.__code__.co_names


def test_page_does_not_scan_and_states_the_limits(user_id):
    registered = _user("page" + user_id[:6])
    client = _client(registered)
    with patch.object(pluto_app, "get_market_data") as scan:
        response = client.get("/my-strategies")
    assert response.status_code == 200
    scan.assert_not_called()
    page = response.get_data(as_text=True).lower()
    assert "proven" not in page
    assert "not reliably estimated" in page
    assert "backtest" in page
    assert "no order is sent" in page


def test_api_requires_login_and_hides_broker_errors():
    client = pluto_app.app.test_client()
    assert client.get("/api/my-strategies").status_code == 401
    registered = _user("apihide")
    client = _client(registered)
    with patch("user_strategies.intake.read_broker_orders", side_effect=RuntimeError("app_key=SUPERSECRET")):
        response = client.post("/my-strategies/import-broker", headers={"Accept": "application/json"})
    body = response.get_data(as_text=True)
    assert response.status_code == 502
    assert "SUPERSECRET" not in body
    assert "No order was placed" in body


def test_save_translate_and_backtest_routes(user_id, monkeypatch):
    registered = _user("route" + user_id[:6])
    client = _client(registered)
    saved = client.post(
        "/my-strategies/save",
        json=_strategy(name="Route book"),
        headers={"Accept": "application/json"},
    )
    assert saved.status_code == 201
    strategy_id = saved.get_json()["data"]["strategy"]["id"]
    other = _client(_user("otherview" + user_id[:4]))
    missing = other.get(f"/api/my-strategies/{strategy_id}")
    assert missing.status_code == 404

    translated = client.post(
        "/my-strategies/translate",
        json={"description": "RSI below 25 on MSFT. Stop of 3%."},
        headers={"Accept": "application/json"},
    )
    assert translated.status_code == 200
    payload = translated.get_json()["data"]
    assert payload["anthropic_used"] is False
    assert payload["draft"]["entry_rules"][0]["kind"] == "rsi_below"

    frame = _bars([100.0] * 80 + [106.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0])
    monkeypatch.setattr(evaluate, "load_daily_bars", lambda *_args, **_kwargs: frame)
    ran = client.post(
        f"/my-strategies/{strategy_id}/backtest",
        json={"lookback_months": 6, "tickers": ["AAPL"]},
        headers={"Accept": "application/json"},
    )
    assert ran.status_code == 200
    backtest = ran.get_json()["data"]["backtest"]
    assert backtest["label"] == "Backtest"
    assert backtest["combined"]["trade_count"] >= 1

    page = client.get(f"/my-strategies?strategy={strategy_id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True).lower()
    assert "not reliably estimated" in html
    assert "backtest" in html
    assert "proven" not in html

    catalog = client.get("/api/my-strategies/paper-catalog")
    assert catalog.status_code == 200
    body = catalog.get_json()["data"]
    assert body["scoring_enabled"] is False
    assert body["strategies"] == []
    assert body["read_by_scan"] is False


def test_csv_route_keeps_a_draft_for_review(user_id):
    registered = _user("csv" + user_id[:4])
    client = _client(registered)
    response = client.post(
        "/my-strategies/import-csv",
        data={"csv_text": "symbol,action,entry_date,exit_date,avg_entry,avg_exit\nNVDA,buy,2026-03-02,2026-03-04,100,90\n"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    draft = response.get_json()["data"]["draft"]
    assert draft["entry_rules"] == []
    assert draft["imported_trades"][0]["pnl_percent"] == -10
    page = client.get("/my-strategies")
    assert b"reconciled" in page.data
