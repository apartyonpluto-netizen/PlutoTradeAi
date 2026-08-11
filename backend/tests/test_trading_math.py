from __future__ import annotations

import pytest

import app as pluto_app


# --- daily loss limit -----------------------------------------------------


def test_daily_loss_limit_disabled_when_percent_is_zero():
    assert pluto_app._is_daily_loss_limit_hit(day_pnl=-999999, current_balance=2000, daily_loss_limit_percent=0) is False


def test_daily_loss_limit_not_hit_when_pnl_within_limit():
    # 5% of 2000 = 100. Lost 50 - still within the limit.
    assert pluto_app._is_daily_loss_limit_hit(day_pnl=-50, current_balance=2000, daily_loss_limit_percent=5) is False


def test_daily_loss_limit_hit_exactly_at_threshold():
    # 5% of 2000 = 100. Losing exactly 100 should trip it (<=, not <).
    assert pluto_app._is_daily_loss_limit_hit(day_pnl=-100, current_balance=2000, daily_loss_limit_percent=5) is True


def test_daily_loss_limit_hit_when_pnl_exceeds_limit():
    assert pluto_app._is_daily_loss_limit_hit(day_pnl=-150, current_balance=2000, daily_loss_limit_percent=5) is True


def test_daily_loss_limit_not_hit_when_positive_pnl():
    assert pluto_app._is_daily_loss_limit_hit(day_pnl=300, current_balance=2000, daily_loss_limit_percent=5) is False


# --- position slots --------------------------------------------------------


def test_available_slots_uncapped_falls_back_to_default():
    assert pluto_app._available_position_slots(max_positions=0, open_position_count=3, default_max_orders=5) == 5


def test_available_slots_with_room_remaining():
    assert pluto_app._available_position_slots(max_positions=10, open_position_count=4, default_max_orders=5) == 6


def test_available_slots_at_cap_returns_zero():
    assert pluto_app._available_position_slots(max_positions=5, open_position_count=5, default_max_orders=5) == 0


def test_available_slots_never_negative_when_over_cap():
    # max_positions was lowered after positions were already opened.
    assert pluto_app._available_position_slots(max_positions=3, open_position_count=7, default_max_orders=5) == 0


# --- position sizing --------------------------------------------------------


def test_max_trade_size_disabled_when_percent_is_zero():
    assert pluto_app._compute_max_trade_size(current_balance=2000, risk_percent_of_balance=0) == 0.0


def test_max_trade_size_computed_from_percent_of_balance():
    assert pluto_app._compute_max_trade_size(current_balance=2000, risk_percent_of_balance=3.5) == pytest.approx(70.0)


def test_position_quantity_exact_case_from_earlier_verified_math():
    # $2,000 balance x 3.5% risk / $20 entry price = exactly 3 shares
    # (the same scenario manually verified earlier this session).
    max_trade_size = pluto_app._compute_max_trade_size(current_balance=2000, risk_percent_of_balance=3.5)
    quantity = pluto_app._compute_position_quantity(max_trade_size, limit_price=20.0, fallback_quantity=1)
    assert quantity == 3


def test_position_quantity_floors_rather_than_rounds():
    # $100 max trade size / $30 price = 3.33 shares -> floors to 3, not 4.
    quantity = pluto_app._compute_position_quantity(max_trade_size=100.0, limit_price=30.0, fallback_quantity=1)
    assert quantity == 3


def test_position_quantity_never_zero_even_if_price_exceeds_max_trade_size():
    # A $500 stock with only $100 of allotted risk still buys at least 1
    # share rather than silently sizing to zero - the caller (candidate
    # selection in _run_autonomous_trade_scan) is responsible for skipping
    # a ticker outright when its price exceeds the risk limit; this
    # function's contract is just "never returns less than 1".
    quantity = pluto_app._compute_position_quantity(max_trade_size=100.0, limit_price=500.0, fallback_quantity=1)
    assert quantity == 1


def test_position_quantity_uses_fallback_when_sizing_disabled():
    assert pluto_app._compute_position_quantity(max_trade_size=0, limit_price=20.0, fallback_quantity=1) == 1


def test_position_quantity_uses_fallback_when_price_is_zero():
    assert pluto_app._compute_position_quantity(max_trade_size=100.0, limit_price=0, fallback_quantity=1) == 1


# --- adaptive stop tightening ------------------------------------------------


def test_tightened_stop_moves_halfway_to_current_price():
    # Matches the scenario verified earlier: stop 190, price 205 -> 197.5.
    assert pluto_app._compute_tightened_stop(current_stop=190.0, current_price=205.0) == pytest.approx(197.5)


def test_tightened_stop_never_exceeds_just_under_current_price():
    # Stop already sitting very close to price - the halfway point (204.95)
    # would land above current price's 99.9% cap (204.795), so the cap must
    # win rather than the halfway formula.
    result = pluto_app._compute_tightened_stop(current_stop=204.9, current_price=205.0)
    assert result < 205.0
    assert result == pytest.approx(205.0 * 0.999, rel=1e-3)


def test_tightened_stop_does_not_move_backwards_when_price_has_fallen_through_stop():
    # Price already fell below the old stop - the formula's own output would
    # be <= current_stop here, and _refresh_stop_confidence's caller-side
    # "tightened_stop <= current_stop: continue" check is what actually
    # prevents loosening; this test locks in that the raw formula supports
    # that invariant rather than fighting it.
    result = pluto_app._compute_tightened_stop(current_stop=200.0, current_price=195.0)
    assert result <= 200.0


# --- CORE-hours-only entries -----------------------------------------------


def test_new_entries_allowed_during_core_session():
    assert pluto_app._new_entries_allowed("CORE") is True


def test_new_entries_blocked_outside_core_session():
    assert pluto_app._new_entries_allowed("ALL") is False
    assert pluto_app._new_entries_allowed("NIGHT") is False
