"""Trade plans across synthetic account sizes. The balances here are test
fixtures, not suggested allocations. Sizing always comes from the scan's own
app._compute_position_quantity, so plan and order can't disagree."""

import pytest

import app as pluto_app
import trade_planner as tp

ENTRY, STOP, TARGET = 50.0, 48.0, 56.0


def _plan(equity, *, risk_percent=1.0, buying_power=None, exposure_cap_percent=0.0, entry=ENTRY, stop=STOP, target=TARGET,
          quote_age=5.0, direction="long", short_permitted=False, existing_exposure=0.0, per_share_cost=0.0, evidence=None, atr=None):
    buying_power = equity if buying_power is None else buying_power
    sizing = pluto_app._compute_position_quantity(
        risk_budget=pluto_app._compute_risk_budget(equity, risk_percent),
        entry_price=entry,
        stop_price=stop,
        available_buying_power=buying_power,
        broker_buying_power=buying_power,
        position_exposure_cap=pluto_app._compute_position_exposure_cap(equity, exposure_cap_percent),
        direction=direction,
    )
    return tp.build_trade_plan(
        ticker="TEST", strategy="Breakout", direction=direction, setup_score=72,
        entry_price=entry, stop_price=stop, target_price=target, quote_price=entry, quote_age_seconds=quote_age,
        plan_equity=equity, equity_source="configured paper balance", existing_exposure=existing_exposure,
        available_buying_power=buying_power, sizing=sizing, per_share_cost=per_share_cost,
        short_permitted=short_permitted, evidence=evidence, atr=atr,
    )


def test_zero_equity_produces_no_trade():
    plan = _plan(0)
    assert plan["decision"] == "reject"
    assert plan["numbers"]["quantity"] == 0
    assert any("equity is zero" in r for r in plan["reasons"])


def test_a_tiny_account_gets_does_not_fit_rather_than_a_forced_share():
    plan = _plan(100)  # 1% of $100 = $1 of risk; one share risks $2
    assert plan["decision"] == "reject"
    assert plan["numbers"]["quantity"] == 0
    assert any(r.startswith("Trade does not fit this account") for r in plan["reasons"])


def test_500_account_keeps_allocation_risk_and_exposure_separate():
    plan = _plan(500)
    n = plan["numbers"]
    assert n["quantity"] == 2                 # $5 risk budget / $2 per share
    assert n["exposure"] == 100.0             # 2 x $50
    assert n["allocation_percent"] == 20.0    # $100 of $500
    assert n["planned_loss"] == 4.0           # 2 x $2
    assert n["risk_percent"] == 0.8           # a 20% allocation is NOT a 20% risk
    assert plan["decision"] == "trade"


def test_3000_account():
    n = _plan(3000)["numbers"]
    assert n["quantity"] == 15 and n["exposure"] == 750.0 and n["risk_percent"] == 1.0 and n["allocation_percent"] == 25.0


def test_large_account_is_limited_by_the_position_cap_not_the_risk_budget():
    plan = _plan(100_000, exposure_cap_percent=5.0)  # cap $5,000 = 100 shares; risk alone would allow 500
    assert plan["numbers"]["quantity"] == 100
    assert "position_cap" in plan["numbers"]["binding_constraints"]
    assert plan["numbers"]["allocation_percent"] == 5.0


def test_reserved_funds_limit_the_size_and_never_go_negative():
    plan = _plan(3000, buying_power=260.0)  # other orders already reserve most of the cash
    assert plan["numbers"]["quantity"] == 5
    assert plan["numbers"]["buying_power_after"] == 10.0
    assert "buying_power" in plan["numbers"]["binding_constraints"]


def test_costs_raise_the_planned_loss():
    assert _plan(3000, per_share_cost=0.05)["numbers"]["planned_loss"] > _plan(3000)["numbers"]["planned_loss"]


def test_gap_scenario_shows_losses_beyond_the_stop():
    plan = _plan(3000)
    losses = [s["loss"] for s in plan["adverse_scenarios"]]
    assert losses[0] == plan["numbers"]["planned_loss"]
    assert losses[1] > losses[0] and losses[2] > losses[1]


def test_atr_sets_the_gap_distance_when_known():
    gap = _plan(3000, atr=5.0)["adverse_scenarios"][2]
    assert gap["exit_price"] == 43.0  # stop 48 minus 1 ATR


def test_stale_quote_is_rejected():
    assert _plan(3000, quote_age=600)["decision"] == "reject"
    assert _plan(3000, quote_age=None)["decision"] == "reject"


def test_stop_on_the_wrong_side_is_rejected():
    plan = _plan(3000, stop=51.0)
    assert plan["decision"] == "reject"
    assert plan["adverse_scenarios"] == []


def test_short_selling_is_blocked_without_permission():
    plan = _plan(3000, direction="short", stop=52.0, target=44.0)
    assert plan["decision"] == "reject"
    assert _plan(3000, direction="short", stop=52.0, target=44.0, short_permitted=True)["decision"] == "trade"


def test_poor_reward_to_risk_is_watch_not_trade():
    plan = _plan(3000, target=51.0)  # $1 reward for $2 risk
    assert plan["decision"] == "watch"
    assert plan["numbers"]["reward_to_risk"] == 0.5


def test_leverage_includes_existing_exposure():
    assert _plan(3000, existing_exposure=2250.0)["numbers"]["leverage_after"] == 1.0  # (2250 + 750) / 3000


def test_small_samples_never_produce_a_probability():
    evidence = tp.evidence_from_returns([5.0, -2.0, 3.0], strategy="Breakout", source="closed trades")
    plan = _plan(3000, evidence=evidence)
    assert plan["probability"]["win_probability_percent"] is None
    assert "not reliably estimated" in plan["probability"]["note"]
    assert plan["expected_value"] is None


def test_enough_real_trades_give_probability_with_an_interval_and_ev():
    returns = [4.0] * 18 + [-2.0] * 12  # 30 trades, 60% wins
    evidence = tp.evidence_from_returns(returns, strategy="Breakout", source="closed trades")
    assert evidence["win_probability_percent"] == 60.0
    interval = evidence["win_probability_interval"]
    assert interval["low"] < 60.0 < interval["high"]
    plan = _plan(3000, evidence=evidence)
    assert plan["expected_value"]["per_trade"] == round(750.0 * 1.6 / 100, 2)  # mean net return 1.6%


def test_a_strategy_with_no_positive_edge_is_watch():
    evidence = tp.evidence_from_returns([-1.0] * 20 + [1.0] * 10, strategy="Breakout", source="closed trades")
    assert _plan(3000, evidence=evidence)["decision"] == "watch"


def test_setup_score_is_labeled_as_not_a_probability():
    plan = _plan(3000)
    assert "not a probability" in plan["setup_score_note"]
    assert plan["probability"]["win_probability_percent"] is None


@pytest.mark.parametrize("equity", [0, 100, 500, 3000, 100_000])
def test_every_plan_carries_its_version_and_limitations(equity):
    plan = _plan(equity)
    assert plan["planner_version"] == tp.PLANNER_VERSION
    assert plan["limitations"]


def test_compact_plan_keeps_the_decision_and_numbers():
    compact = tp.compact_plan(_plan(3000))
    assert compact["decision"] == "trade"
    assert compact["numbers"]["quantity"] == 15
    assert "constraints" not in compact["numbers"]
    assert compact["probability"]["reliable"] is False


def test_a_preview_defers_the_quote_check_instead_of_rejecting():
    sizing = pluto_app._compute_position_quantity(
        risk_budget=30.0, entry_price=ENTRY, stop_price=STOP, available_buying_power=3000.0, broker_buying_power=3000.0,
    )
    common = dict(ticker="TEST", strategy="Breakout", direction="long", setup_score=72, entry_price=ENTRY, stop_price=STOP,
                  target_price=TARGET, quote_price=None, quote_age_seconds=None, plan_equity=3000, equity_source="configured paper balance",
                  existing_exposure=0, available_buying_power=3000.0, sizing=sizing)
    assert tp.build_trade_plan(**common)["decision"] == "reject"
    preview = tp.build_trade_plan(**common, quote_deferred=True)
    assert preview["decision"] == "trade"
    assert any("re-checked immediately before any submission" in note for note in preview["limitations"])
