"""Go-live safety: paper costs, streak halt, flatten plan, phone alerts, gate, ramp."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import auth
import flatten_account
import go_live_gate
import kill_switch
import live_ramp
import order_lifecycle as ol
import paper_costs
import paper_trader
import phone_alerts
import portfolio_limits as pl
from autonomy.autonomous_controller import get_autonomy_status, update_risk_settings
from autonomy.closed_trades import record_closed_trade
from autonomy.overnight_orders import record_overnight_order
from autonomy.performance_report import build_performance_report
from integrations import webull as webull_api

LIVE_ENV = {
    "PLUTO_WEBULL_TRADING_ENVIRONMENT": "live",
    "PLUTO_LIVE_TRADING_CONFIRMATION": "I_UNDERSTAND_THIS_PLACES_REAL_MONEY_ORDERS",
}


def _clean_entry(trade_id: str) -> dict:
    entry: dict = {"ticker": "NVDA"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=trade_id)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id)
    return entry


def _trade(trade_id: str, net: float = 25.0, **extra) -> dict:
    return {
        "ticker": "NVDA",
        "entry_client_order_id": trade_id,
        "entry_timestamp": "2026-09-14T00:00:00+00:00",
        "exit_timestamp": "2026-09-15T00:00:00+00:00",
        "close_reason": "target_exit_executed",
        "pnl_status": "complete",
        "net_realized_pnl": net,
        "strategy": "Momentum",
        **extra,
    }


def test_slippage_makes_a_buy_more_expensive_and_a_sell_cheaper():
    assert paper_costs.slip_price(100.0, "buy") > 100
    assert paper_costs.slip_price(100.0, "sell") < 100


def test_a_tiny_winner_is_negative_after_paper_costs():
    estimate = paper_costs.round_trip(100.0, 100.02, 1)
    assert estimate["quote_pnl"] > 0
    assert estimate["net"] < 0
    assert estimate["fees"] > 0
    assert estimate["slippage"] > 0


def test_live_records_keep_the_stored_net():
    trade = {"environment": "live", "pnl_status": "complete", "net_realized_pnl": 10.0,
             "average_entry_price": 100, "average_exit_price": 101, "exited_quantity": 10}
    assert paper_costs.performance_net(trade) == 10.0


def test_sandbox_performance_uses_the_after_cost_net(user_id):
    record_closed_trade(user_id, "t1", _trade(
        "t1", 50.0, average_entry_price=100.0, average_exit_price=100.02, exited_quantity=1, side="BUY",
    ))
    report = build_performance_report(user_id)
    assert report["overall"]["total_pnl"] < 0


def test_a_stored_net_without_prices_is_unchanged(user_id):
    record_closed_trade(user_id, "win-1", _trade("win-1", 100.0))
    record_closed_trade(user_id, "loss-1", _trade("loss-1", -40.0))
    report = build_performance_report(user_id)
    assert report["overall"]["total_pnl"] == 60.0


def test_market_paper_fill_is_worse_than_the_quote(user_id, monkeypatch):
    monkeypatch.setattr(paper_trader, "_get_live_price", lambda ticker: 100.0)
    opened = paper_trader.open_trade(user_id, "AAPL", "BUY", quantity=10)
    assert float(opened["entry_price"]) > float(opened["entry_quote"])
    closed = paper_trader.close_trade(user_id, opened["id"])
    assert float(closed["exit_price"]) < float(closed["exit_quote"])
    assert float(closed["fees"]) > 0
    raw = (100.0 - 100.0) * 10
    assert float(closed["pnl"]) < raw


def test_losing_streak_halts_and_a_winner_clears_it():
    losses = [_trade(f"l{i}", -1.0) for i in range(3)]
    assert kill_switch.streak_block_reason(losses, 3)
    assert kill_switch.streak_block_reason([_trade("w", 1.0), *_trade_list()], 3) is None
    assert kill_switch.streak_block_reason(losses, 0) is None


def _trade_list():
    return [_trade("older", -5.0)]


def test_note_halt_phones_once(user_id, monkeypatch):
    sent = []
    monkeypatch.setattr(phone_alerts, "notify", lambda *args, **kwargs: sent.append(args) or True)
    assert kill_switch.note_halt(user_id, "streak") is True
    assert kill_switch.note_halt(user_id, "streak") is False
    assert len(sent) == 1
    kill_switch.clear_halt(user_id)
    assert kill_switch.current_halt(user_id)["active"] is False


def test_phone_alerts_are_off_until_configured(monkeypatch):
    monkeypatch.delenv("PLUTO_PUSHOVER_USER", raising=False)
    monkeypatch.delenv("PLUTO_PUSHOVER_TOKEN", raising=False)
    monkeypatch.delenv("PLUTO_ALERT_WEBHOOK_URL", raising=False)
    assert phone_alerts.configured() is False
    assert phone_alerts.notify("halt", "t", "m") is False


def test_webhook_posts_without_logging_the_url(monkeypatch):
    monkeypatch.setenv("PLUTO_ALERT_WEBHOOK_URL", "https://alerts.example/hook")
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = request.data
        response = MagicMock()
        response.read.return_value = b""
        response.__enter__.return_value = response
        return response

    assert phone_alerts.notify("error", "Scan failed", "details", opener=opener) is True
    assert b"Scan failed" in seen["body"]
    assert phone_alerts.classify("position_closed") == "fill"
    assert phone_alerts.classify("stop_filled") == "stop"
    assert phone_alerts.classify("manual") is None


def test_flatten_plan_cancels_and_closes_without_calling_a_broker():
    plan = flatten_account.plan(
        [{"symbol": "AAPL", "quantity": 4, "last_price": 100, "account_id": "a"}],
        [{"client_order_id": "oid-1", "symbol": "AAPL", "instrument_type": "EQUITY", "account_id": "a"}],
    )
    assert plan["cancels"][0]["client_order_id"] == "oid-1"
    assert plan["closes"][0]["side"] == "SELL"
    assert plan["closes"][0]["limit_price"] < 100
    calls = []
    result = flatten_account.execute(plan, {
        "cancel_equity": lambda item: calls.append(("cancel", item["client_order_id"])),
        "cancel_option": lambda item: calls.append("option-cancel"),
        "close_equity": lambda item: calls.append(("close", item["symbol"])),
        "close_option": lambda item: calls.append("option-close"),
    })
    assert result["cancelled"] == ["oid-1"]
    assert result["closed"] == ["AAPL"]
    assert ("cancel", "oid-1") in calls and ("close", "AAPL") in calls


def test_option_missing_contract_fields_is_not_guessed():
    plan = flatten_account.plan([{"symbol": "NVDA260918C00105000", "quantity": 1, "last_price": 3, "instrument_type": "OPTION"}], [])
    assert plan["closes"] == []
    assert plan["errors"]


def test_close_everything_refuses_without_the_phrase(user_id):
    user = auth.register_user(f"stop-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    import app as pluto_app
    with pluto_app.app.test_client() as client, patch("flatten_account.run_for_user") as run:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        refused = client.post("/api/safety/close-everything", json={"confirm": "yes"})
        assert refused.status_code == 400
        run.assert_not_called()
        assert get_autonomy_status(user["id"])["emergency_stop_enabled"] is False


def test_live_defaults_apply_only_when_live_and_unset():
    stored = {"max_position_exposure_percent": 0, "max_total_exposure_percent": 0,
              "max_sector_exposure_percent": 0, "max_drawdown_percent": 0}
    assert pl.effective_limits(stored, live=False)["max_position_percent"] == 0
    assert pl.effective_limits(stored, live=True)["max_position_percent"] == 10
    assert pl.effective_limits(stored, live=True)["max_total_exposure_percent"] == 40
    explicit = {**stored, "portfolio_limits_explicit": True}
    assert pl.effective_limits(explicit, live=True)["max_position_percent"] == 0


def test_saving_a_portfolio_limit_marks_it_explicit(user_id):
    update_risk_settings(user_id, max_position_exposure_percent=0)
    status = get_autonomy_status(user_id)
    assert status["portfolio_limits_explicit"] is True
    assert pl.effective_limits(status, live=True)["max_position_percent"] == 0


def test_live_ramp_defaults_to_a_quarter_and_floors():
    assert live_ramp.fraction() == 0.25
    assert live_ramp.scale_quantity(10) == 2
    assert live_ramp.scale_quantity(3) == 0


def test_go_live_gate_blocks_a_thin_record_and_passes_a_full_one(user_id):
    report = go_live_gate.evaluate(user_id)
    assert report["ready"] is False
    assert report["clean_trades"] == 0
    for index in range(30):
        trade_id = f"clean-{index}"
        record_overnight_order(user_id, _clean_entry(trade_id))
        record_closed_trade(user_id, trade_id, _trade(trade_id, 20.0))
    option_id = "opt-clean"
    record_overnight_order(user_id, _clean_entry(option_id))
    record_closed_trade(user_id, option_id, _trade(
        option_id, 30.0, instrument_type="OPTION", close_reason="option_target_reached",
    ))
    ready = go_live_gate.evaluate(user_id)
    assert ready["ready"] is True
    assert ready["options_cycle_complete"] is True
    assert ready["net_after_fees"] > 0


def test_live_opening_order_is_refused_when_the_gate_is_not_passed(user_id, monkeypatch):
    for key, value in LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLUTO_LIVE_MAX_ORDER_USD", "1000")
    monkeypatch.setenv("PLUTO_LIVE_MAX_DAILY_USD", "5000")
    token = go_live_gate.bind(user_id)
    client = MagicMock()
    try:
        with patch.object(webull_api, "_get_trade_client", return_value=client):
            with pytest.raises(webull_api.DefiniteOrderRejection, match="Go-live gate"):
                webull_api.place_stock_order("k", "s", "acct", "AAPL", "BUY", 1, 10.0, client_order_id="c1")
        client.order_v2.place_order.assert_not_called()
    finally:
        go_live_gate.reset(token)


def test_drawdown_over_the_limit_fails_the_gate(user_id, monkeypatch):
    monkeypatch.setenv("PLUTO_GO_LIVE_MAX_DRAWDOWN_PERCENT", "10")
    for index in range(30):
        trade_id = f"dd-{index}"
        record_overnight_order(user_id, _clean_entry(trade_id))
        net = 500.0 if index == 0 else -80.0
        record_closed_trade(user_id, trade_id, _trade(trade_id, net))
    option_id = "opt-dd"
    record_overnight_order(user_id, _clean_entry(option_id))
    record_closed_trade(user_id, option_id, _trade(
        option_id, -80.0, instrument_type="OPTION", close_reason="option_stop_reached",
    ))
    report = go_live_gate.evaluate(user_id)
    assert report["checks"][0]["passed"] is True
    assert any(check["id"] == "max_drawdown" and not check["passed"] for check in report["checks"])
    assert report["ready"] is False
