from __future__ import annotations

import app as pluto_app

"""_compute_option_contract_quantity - premium-based sizing for long options
(2026-09-03). Mirrors test_trading_math.py's coverage of
_compute_position_quantity, adapted for options: there's no stop-distance
constraint (a bought option's max loss IS the premium paid), so "risk" and
"cost per contract" collapse into ask_price * contract_multiplier."""


def test_quantity_zero_when_ask_price_missing():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=None, available_buying_power=10000, broker_option_buying_power=10000,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["ask_price"]


def test_quantity_zero_when_ask_price_is_zero_or_negative():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=0, available_buying_power=10000, broker_option_buying_power=10000,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["ask_price"]


def test_basic_sizing_by_premium_cost():
    # $500 risk budget, $5.00 ask * 100 multiplier = $500/contract -> 1 contract.
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 1
    assert result["cost_per_contract"] == 500.0
    assert result["reason"] == ""


def test_quantity_floors_rather_than_rounds():
    # $999 risk budget / $500 per contract = 1.998 -> floors to 1, not 2.
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=999, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 1


def test_multiple_contracts_when_budget_allows():
    # $1500 risk budget / $500 per contract = exactly 3 contracts.
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=1500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 3


def test_risk_disabled_zero_fails_closed_even_with_ample_buying_power():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=0, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]


def test_risk_missing_none_fails_closed():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=None, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]


def test_buying_power_none_fails_closed_distinctly_from_zero():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=5.0, available_buying_power=None, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["buying_power"]


def test_broker_buying_power_none_fails_closed_distinctly_from_zero():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=None,
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["broker_buying_power"]


def test_broker_option_buying_power_binds_tighter_than_virtual_allocation():
    # Virtual allocation could afford 3 contracts, but the real broker
    # option_buying_power can only afford 1 - the tighter one must bind.
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=1500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=500,
    )
    assert result["quantity"] == 1
    assert "broker_buying_power" in result["binding_constraints"]


def test_quantity_zero_when_buying_power_cannot_afford_even_one_contract():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=1500, ask_price=5.0, available_buying_power=499, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 0
    assert "buying_power" in result["binding_constraints"]


def test_position_exposure_cap_disabled_by_default_reports_none_and_never_binds():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=1500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
    )
    assert result["constraints"]["position_cap"] is None
    assert "position_cap" not in result["binding_constraints"]


def test_position_exposure_cap_can_bind():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=1500, ask_price=5.0, available_buying_power=100000, broker_option_buying_power=100000,
        position_exposure_cap=500,
    )
    assert result["quantity"] == 1
    assert "position_cap" in result["binding_constraints"]


def test_tied_constraints_all_reported_as_binding():
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=500, ask_price=5.0, available_buying_power=500, broker_option_buying_power=100000,
    )
    assert result["quantity"] == 1
    assert set(result["binding_constraints"]) == {"risk", "buying_power"}


def test_custom_contract_multiplier_is_respected():
    # A hypothetical non-standard multiplier of 10 instead of 100.
    result = pluto_app._compute_option_contract_quantity(
        risk_budget=50, ask_price=5.0, available_buying_power=1000, broker_option_buying_power=1000,
        contract_multiplier=10.0,
    )
    assert result["quantity"] == 1
    assert result["cost_per_contract"] == 50.0
