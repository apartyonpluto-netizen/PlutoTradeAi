"""Option trade plans: priced from the contract's own premium, never the stock
risk-per-share formula. The exit trigger is shown as the app runs it (live
bid vs entry premium x (1 - stop%)), separately from the full premium at
risk. Balances are synthetic test fixtures, not suggested allocations."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import calibration
import trade_planner as tp
from autonomy import trade_tickets as tt
from autonomy.autonomous_controller import get_autonomy_status, set_mode

TODAY = date(2026, 9, 29)


def _contract(bid=0.95, ask=1.00, strike=100.0, option_type="CALL", days=21, delta=0.45):
    return {"option_symbol": "TEST261020C00100000", "strike": strike, "option_type": option_type,
            "expiration_date": (TODAY + timedelta(days=days)).isoformat(), "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2 if bid and ask else None, "delta": delta}


def _plan(equity=3000.0, risk_percent=5.0, stop=20.0, target=50.0, close_days=3, quote_age=0.0, deferred=False,
          evidence=None, fee=0, **contract_kwargs):
    contract = _contract(**contract_kwargs)
    sizing = pluto_app._compute_option_contract_quantity(
        risk_budget=pluto_app._compute_risk_budget(equity, risk_percent),
        ask_price=contract["ask"], available_buying_power=equity, broker_option_buying_power=equity,
    )
    return tp.build_option_trade_plan(
        ticker="TEST", strategy="Breakout", setup_score=74, contract=contract, underlying_price=100.5,
        quote_age_seconds=quote_age, plan_equity=equity, equity_source="configured paper balance",
        existing_exposure=0, available_buying_power=equity, sizing=sizing, stop_loss_percent=stop,
        target_gain_percent=target, close_days_before_expiration=close_days, today=TODAY,
        per_contract_cost=fee, evidence=evidence, quote_deferred=deferred,
    )


def test_the_users_example_a_1_dollar_premium_triggers_at_80_cents():
    plan = _plan(stop=20.0)
    n = plan["numbers"]
    assert n["exit_trigger_premium"] == 0.80
    assert n["quantity"] == 1                      # 5% of $3,000 = $150 covers one $100 contract
    assert n["premium_at_risk"] == 100.0           # the most one contract can lose
    assert n["loss_at_trigger"] == 20.40           # sells 0.5% under the $0.80 bid
    assert n["gain_at_target"] == 49.25            # $1.50 target, sold 0.5% under
    assert n["reward_to_risk"] == 2.41
    assert plan["decision"] == "trade"
    assert plan["instrument"] == "OPTION"


def test_premium_at_risk_is_shown_separately_from_the_loss_at_the_trigger():
    plan = _plan()
    n = plan["numbers"]
    assert n["premium_at_risk"] > n["loss_at_trigger"]
    losses = {s["scenario"]: s["loss"] for s in plan["adverse_scenarios"]}
    assert losses["Exit at the trigger"] == n["loss_at_trigger"]
    assert losses["Premium goes to zero"] == n["premium_at_risk"]
    assert losses["Exit one bid-ask spread below the trigger"] > losses["Exit at the trigger"]
    assert any("not a maximum loss" in note for note in plan["limitations"])
    assert "app-monitored" in n["protection"]


def test_a_bid_already_at_the_trigger_is_rejected():
    plan = _plan(bid=0.78, ask=1.00, stop=20.0)  # trigger 0.80; buying at the ask would exit on the first pass
    assert plan["decision"] == "reject"
    assert any("already at or below the exit trigger" in r for r in plan["reasons"])


def test_a_bid_just_above_the_trigger_is_watch():
    plan = _plan(bid=0.83, ask=1.00, stop=20.0)  # within 5% of the $0.80 trigger
    assert plan["decision"] == "watch"


def test_a_wide_spread_is_watch():
    plan = _plan(bid=0.88, ask=1.00, stop=50.0)  # spread 12.8% of mid
    assert plan["decision"] == "watch"
    assert plan["numbers"]["spread_percent"] == 12.8


def test_the_configured_stop_percent_drives_the_trigger():
    assert _plan(stop=50.0)["numbers"]["exit_trigger_premium"] == 0.50
    assert _plan(stop=20.0)["numbers"]["exit_trigger_premium"] == 0.80


@pytest.mark.parametrize("equity,expected", [(0, "reject"), (50, "reject"), (500, "reject"), (2000, "trade"), (3000, "trade"), (100_000, "trade")])
def test_account_sizes(equity, expected):
    """At 5% risk, $500 is a $25 budget - less than one $100 contract."""
    plan = _plan(equity=equity)
    assert plan["decision"] == expected
    if equity in (50, 500):
        assert any(r.startswith("Trade does not fit this account") for r in plan["reasons"])


def test_expiration_inside_the_close_window_is_rejected():
    assert _plan(days=3, close_days=3)["decision"] == "reject"
    assert _plan(days=4, close_days=3)["decision"] == "trade"


def test_stale_or_missing_quotes():
    assert _plan(quote_age=600)["decision"] == "reject"
    assert _plan(quote_age=None)["decision"] == "reject"
    deferred = _plan(quote_age=None, deferred=True)
    assert deferred["decision"] == "trade"
    assert any("re-checks the live premium" in note for note in deferred["limitations"])


def test_one_sided_quote_is_rejected():
    assert _plan(bid=0, ask=1.00)["decision"] == "reject"


def test_breakeven_moneyness_and_delta_adjusted_exposure():
    at_the_money = _plan()["numbers"]                              # underlying 100.5, strike 100: within 0.5%
    assert at_the_money["breakeven_at_expiration"] == 101.0
    assert at_the_money["moneyness"].startswith("at the money")
    assert at_the_money["underlying_notional"] == 10050.0
    assert at_the_money["delta_adjusted_notional"] == 4522.5      # 0.45 x 100.5 x 100
    call = _plan(strike=98.0)["numbers"]
    assert call["breakeven_at_expiration"] == 99.0 and call["moneyness"].startswith("in the money")
    put = _plan(strike=98.0, option_type="PUT")["numbers"]
    assert put["breakeven_at_expiration"] == 97.0 and put["moneyness"].startswith("out of the money")


def test_fees_are_included_when_known_and_flagged_when_not():
    assert any("fees are not included" in note for note in _plan()["limitations"])
    with_fees = _plan(fee=0.65)
    assert with_fees["numbers"]["premium_at_risk"] == 100.65
    assert with_fees["numbers"]["loss_at_trigger"] == 21.70  # 20.40 + open and close fees


def test_option_evidence_gives_ev_on_the_premium():
    evidence = tp.evidence_from_returns([30.0] * 18 + [-20.0] * 12, strategy="Breakout", source="closed option trades")
    plan = _plan(evidence=evidence)
    assert plan["probability"]["win_probability_percent"] == 60.0
    assert plan["expected_value"]["per_trade"] == 10.0  # mean +10% on $100 of premium
    assert plan["decision"] == "trade"


def test_compact_plan_keeps_option_numbers():
    compact = tp.compact_plan(_plan())
    assert compact["numbers"]["exit_trigger_premium"] == 0.80
    assert compact["numbers"]["premium_at_risk"] == 100.0


# --- calibration: option closed trades now count, at the right size ---------------


def test_option_closed_trades_count_with_the_contract_multiplier():
    option_trade = {"pnl_status": "complete", "instrument_type": "OPTION", "premium_paid_per_contract": 1.00,
                    "filled_quantity": 2, "exited_quantity": 2, "net_realized_pnl": 40.0}
    assert calibration._pnl_percent_for_closed_trade(option_trade) == pytest.approx(20.0)  # $40 on $200 of premium
    equity_trade = {"pnl_status": "complete", "average_entry_price": 50.0, "filled_quantity": 10, "net_realized_pnl": 25.0}
    assert calibration._pnl_percent_for_closed_trade(equity_trade) == pytest.approx(5.0)


def test_evidence_is_split_by_instrument():
    trades = [
        {"strategy": "Breakout", "pnl_status": "complete", "average_entry_price": 50.0, "filled_quantity": 10, "net_realized_pnl": 25.0},
        {"strategy": "Breakout", "pnl_status": "complete", "instrument_type": "OPTION", "premium_paid_per_contract": 1.0,
         "filled_quantity": 1, "net_realized_pnl": -30.0},
    ]
    with patch.object(calibration, "list_all_users", return_value=[{"id": "u"}]), \
         patch.object(calibration, "list_closed_trades", return_value=trades):
        assert calibration.closed_trade_returns_by_strategy(instrument_type="EQUITY") == {"Breakout": [5.0]}
        assert calibration.closed_trade_returns_by_strategy(instrument_type="OPTION") == {"Breakout": [-30.0]}
        assert sorted(calibration.closed_trade_returns_by_strategy()["Breakout"]) == [-30.0, 5.0]


# --- settings -------------------------------------------------------------------


def _registered_user(suffix: str) -> str:
    user = auth.register_user(f"optplan-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_option_exit_settings_can_be_saved(user_id):
    uid = _registered_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response = client.post("/api/autonomy/risk-settings", json={
            "option_stop_loss_percent": 20, "option_target_gain_percent": 60, "option_close_days_before_expiration": 2,
        })
    assert response.status_code == 200
    status = get_autonomy_status(uid)
    assert (status["option_stop_loss_percent"], status["option_target_gain_percent"], status["option_close_days_before_expiration"]) == (20.0, 60.0, 2)
    assert pluto_app._option_exit_settings(status) == {"stop_loss_percent": 20.0, "target_gain_percent": 60.0, "close_days_before_expiration": 2}


# --- approval recheck: the fresh bid against the trigger ---------------------------


def test_approving_an_option_ticket_refuses_when_the_bid_is_at_the_trigger(user_id):
    uid = _registered_user(user_id[:8] + "r")
    from autonomy.autonomous_controller import update_risk_settings
    update_risk_settings(uid, option_stop_loss_percent=20.0)
    ticket, _ = tt.create_or_refresh_ticket(uid, {
        "trading_day": pluto_app._trading_day_key(), "ticker": "TEST", "instrument_type": "OPTION", "direction": "long",
        "quantity": 1, "limit_price": 1.00, "account_id": "acct-margin", "option_symbol": "TEST261020C00100000",
        "strike": 100.0, "expiration_date": "2026-10-20", "option_type": "CALL",
    })
    balance = {"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
               "account_currency_assets": [{"buying_power": "1000000", "option_buying_power": "1000000"}]}
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with patch.object(pluto_app, "get_webull_credentials", return_value={"app_key": "k", "app_secret": "s"}), \
             patch.object(pluto_app, "is_webull_configured", return_value=True), \
             patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
             patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[]), \
             patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value=None), \
             patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None), \
             patch.object(pluto_app.webull_api, "get_account_balance", return_value=balance), \
             patch.object(pluto_app.webull_api, "get_option_snapshot", return_value=[{"symbol": "TEST261020C00100000", "bid": "0.79", "ask": "1.00"}]), \
             patch.object(pluto_app, "_submit_and_confirm_option_entry") as submit, \
             patch.object(pluto_app, "time"):
            response = client.post(f"/api/trade-tickets/{ticket['ticket_id']}/approve", json={"version": ticket["version"]})
    assert response.status_code == 409
    assert "at or below the exit trigger ($0.80)" in response.get_json()["error"]["message"]
    submit.assert_not_called()


# --- scan integration: the option branch carries a plan -----------------------------


def test_the_scans_option_branch_attaches_a_plan_sized_like_the_order(user_id):
    from tests.test_option_full_scan_integration import OPTION_SYMBOL, _ai_found_call_candidate, _run_full_autonomous_scan
    uid = _registered_user(user_id[:8] + "s")
    from autonomy.autonomous_controller import update_risk_settings
    update_risk_settings(uid, option_stop_loss_percent=20.0)
    expiration = (pluto_app._now_utc().date() + timedelta(days=21)).isoformat()
    contracts = [{"symbol": OPTION_SYMBOL, "strike_price": "105", "expiration_date": expiration, "option_type": "CALL", "underlying_symbol": "NVDA"}]
    snapshot = [{"symbol": OPTION_SYMBOL, "bid": "4.90", "ask": "5.10", "delta": "0.45"}]
    result, placed_option, _ = _run_full_autonomous_scan(uid, [_ai_found_call_candidate()], option_contracts=contracts, option_snapshot=snapshot)
    entry = next(e for e in result["placed"] if e.get("instrument_type") == "OPTION")
    plan = entry["trade_plan"]
    assert plan["instrument"] == "OPTION"
    assert plan["numbers"]["quantity"] == placed_option["quantity"] == entry["quantity"]
    assert plan["numbers"]["exit_trigger_premium"] == round(5.10 * 0.8, 2)
    assert plan["numbers"]["premium_at_risk"] == round(5.10 * 100 * entry["quantity"], 2)
