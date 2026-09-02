from __future__ import annotations

import pytest

import app as pluto_app
import order_lifecycle as ol


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
#
# Corrected this session: position sizing used to be `budget // share_price`
# with the stop distance never entering the calculation at all - so "risk
# per trade" was actually a spend cap, not a risk-at-stop cap, and any stock
# priced above the budget got hard-skipped regardless of how tight its stop
# was. Confirmed live in production: MELI (a real $1,946.94 CALL setup, 76%
# confidence) was skipped outright by the old formula purely because its
# share price exceeded a $100 "risk" budget, with its actual stop distance
# never considered.
#
# Tightened a second time after a real error was caught in this test file
# itself: the first version of the MELI regression test used a $1,000,000
# available_buying_power (Webull's real, inflated sandbox seed) instead of
# the $2,000 virtual balance the $100 risk budget was actually derived from,
# which hid the fact that buying power - not risk - is what should have
# bound that specific trade to 1 share, not 2. _compute_position_quantity
# now returns a full structured breakdown specifically so a mistake like
# that is checkable directly instead of trusting a single opaque number.


def test_risk_budget_disabled_when_percent_is_zero():
    assert pluto_app._compute_risk_budget(current_balance=2000, risk_percent_of_balance=0) == 0.0


def test_risk_budget_computed_from_percent_of_balance():
    assert pluto_app._compute_risk_budget(current_balance=2000, risk_percent_of_balance=3.5) == pytest.approx(70.0)


def test_position_exposure_cap_disabled_when_percent_is_zero():
    assert pluto_app._compute_position_exposure_cap(current_balance=2000, max_position_exposure_percent=0) == 0.0


def test_position_exposure_cap_computed_from_percent_of_balance():
    assert pluto_app._compute_position_exposure_cap(
        current_balance=2000, max_position_exposure_percent=25
    ) == pytest.approx(500.0)


def test_quantity_sized_by_risk_at_stop_not_by_share_price():
    # $100 risk budget, $2 of risk per share ($20 entry, $18 stop) -> 50
    # shares, costing $1,000 total exposure - a share price far above the
    # $100 "budget" is fine, because the budget bounds risk, not spend.
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=20.0, stop_price=18.0, available_buying_power=100000, broker_buying_power=10_000_000.0
    )
    assert result["quantity"] == 50
    assert result["reason"] == ""
    assert result["constraints"]["risk"] == 50
    assert result["constraints"]["buying_power"] == 5000  # 100000 // 20
    assert result["binding_constraints"] == ["risk"]


def test_tighter_stop_allows_more_shares_for_the_same_risk_budget():
    # This is the core regression test: two setups, same entry price, same
    # risk budget, different stop distance - the old formula (budget //
    # price) would size these identically. The correct formula must not.
    tight = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    wide = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=30.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert tight["quantity"] == 100  # $100 / $1 risk-per-share
    assert wide["quantity"] == 5  # $100 / $20 risk-per-share
    assert tight["quantity"] > wide["quantity"]


def test_production_regression_meli_final_quantity_is_one_not_two():
    # The exact live production numbers from this session's incident, with
    # the actual $2,000 virtual balance as buying power (not the real,
    # inflated ~$1,000,000 sandbox seed a first draft of this test wrongly
    # used, which hid the real binding constraint). Risk alone would afford
    # 2 shares (100.0 // 38.9388); buying power affords only 1 (2000 // 1946.94).
    # The correct final answer is 1, bound by buying power, not risk.
    entry_price = 1946.94
    stop_price = entry_price * 0.98
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=entry_price, stop_price=stop_price, available_buying_power=2000.0, broker_buying_power=10_000_000.0
    )
    assert result["constraints"]["risk"] == 2
    assert result["constraints"]["buying_power"] == 1
    assert result["quantity"] == 1
    assert result["binding_constraints"] == ["buying_power"]
    assert result["reason"] == ""


def test_quantity_floors_rather_than_rounds():
    # $100 risk / $30 risk-per-share = 3.33 -> floors to 3, not 4.
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=100.0, stop_price=70.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 3
    assert result["reason"] == ""


def test_quantity_boundary_exact_division_does_not_under_floor_from_float_error():
    # A perfectly ordinary $20.10 entry with a $20.00 stop (a 10-cent stop,
    # ordinary for a cheap stock) against a $3.00 risk budget is
    # mathematically exactly 30 shares. The float trap here isn't in the
    # final division - it's in entry_price - stop_price itself:
    # 20.10 - 20.00 == 0.10000000000000142 in raw float, and
    # 3.0 // 0.10000000000000142 floors to 29, silently under-sizing by one
    # share. _compute_position_quantity converts both prices to Decimal
    # before subtracting (not just before the final division) specifically
    # to avoid this - verified directly against raw float below.
    assert 20.10 - 20.00 != 0.10  # documents the float trap this guards against
    assert 3.0 // (20.10 - 20.00) == 29  # the wrong, under-sized answer raw float arithmetic gives

    result = pluto_app._compute_position_quantity(risk_budget=3.0, entry_price=20.10, stop_price=20.00, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["constraints"]["risk"] == 30


def test_quantity_exact_one_share_affordability_boundary():
    # Buying power exactly equal to one share's price must afford exactly 1,
    # not 0 (an off-by-one in the wrong direction here would silently block
    # every trade priced exactly at the edge of what's affordable) and not 2.
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=50.0, broker_buying_power=10_000_000.0)
    assert result["constraints"]["buying_power"] == 1
    assert result["quantity"] == 1


def test_quantity_zero_when_risk_budget_too_small_for_one_share_at_this_stop():
    # $10 risk budget, $20 risk-per-share -> less than one share's worth of
    # risk is affordable at this stop, even though the account can easily
    # afford to buy the stock outright.
    result = pluto_app._compute_position_quantity(risk_budget=10.0, entry_price=50.0, stop_price=30.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]
    assert result["reason"] == "risk budget too small for one share at this stop"


def test_quantity_zero_when_buying_power_is_the_binding_constraint():
    # Risk budget alone would afford 100 shares, but only $500 of real
    # buying power exists - affordability always applies regardless of the
    # risk setting.
    result = pluto_app._compute_position_quantity(risk_budget=1000.0, entry_price=50.0, stop_price=49.0, available_buying_power=500.0, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 10  # 500 // 50
    assert result["reason"] == ""  # still sizes a real quantity - buying power just capped it lower


def test_quantity_zero_when_buying_power_cannot_afford_even_one_share():
    result = pluto_app._compute_position_quantity(risk_budget=1000.0, entry_price=50.0, stop_price=49.0, available_buying_power=10.0, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["buying_power"]
    assert result["reason"] == "insufficient buying power"


def test_quantity_zero_when_exposure_cap_is_the_binding_constraint():
    result = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0, position_exposure_cap=250.0
    )
    assert result["quantity"] == 5  # 250 // 50
    assert result["reason"] == ""


def test_quantity_zero_when_exposure_cap_too_small_for_one_share():
    result = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0, position_exposure_cap=10.0
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["position_cap"]
    assert result["reason"] == "position exposure cap reached"


def test_exposure_cap_disabled_by_default_reports_none_and_never_binds():
    result = pluto_app._compute_position_quantity(risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0)
    assert result["constraints"]["position_cap"] is None
    assert result["quantity"] == 20000  # bounded only by buying power (1_000_000 // 50)


def test_portfolio_risk_remaining_can_bind():
    # $50,000 remaining across the whole portfolio, $1 risk-per-share -> caps
    # at 50,000 shares even though this trade's own risk budget would allow
    # far more - several positions collectively cannot exceed the intended
    # portfolio-wide limit, only one of which is this trade's own budget.
    result = pluto_app._compute_position_quantity(
        risk_budget=1_000_000.0, entry_price=50.0, stop_price=49.0, available_buying_power=100_000_000.0, broker_buying_power=10_000_000.0, portfolio_risk_remaining=50_000.0
    )
    assert result["constraints"]["portfolio_risk"] == 50000
    assert result["quantity"] == 50000
    assert result["binding_constraints"] == ["portfolio_risk"]


def test_portfolio_risk_remaining_zero_blocks_new_entries():
    result = pluto_app._compute_position_quantity(
        risk_budget=1000.0, entry_price=50.0, stop_price=49.0, available_buying_power=100000.0, broker_buying_power=10_000_000.0, portfolio_risk_remaining=0.0
    )
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["portfolio_risk"]


def test_portfolio_risk_not_tracked_when_none_is_not_evaluated():
    # None (not tracked/unavailable) is different from 0.0 (tracked, no
    # headroom left) - None must not silently become a binding constraint.
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=100000.0, broker_buying_power=10_000_000.0, portfolio_risk_remaining=None
    )
    assert result["constraints"]["portfolio_risk"] is None
    assert "portfolio_risk" not in result["binding_constraints"]


# --- fail-closed: risk disabled/missing must never permit a trade ----------
#
# There is deliberately no fallback-to-N-shares path - a trade that can't be
# risk-sized is a trade this function refuses to size, period. "Risk
# disabled" is defined precisely as risk_budget being None, 0, or negative.


def test_risk_disabled_zero_fails_closed_even_with_ample_buying_power():
    result = pluto_app._compute_position_quantity(risk_budget=0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]
    assert "risk-based sizing is disabled" in result["reason"]


def test_risk_missing_none_fails_closed():
    result = pluto_app._compute_position_quantity(risk_budget=None, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]


def test_risk_negative_fails_closed():
    result = pluto_app._compute_position_quantity(risk_budget=-50.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["binding_constraints"] == ["risk"]


# --- fail-closed: missing/stale account data --------------------------------


def test_buying_power_none_fails_closed_distinctly_from_zero_buying_power():
    # None means "couldn't be determined" (a failed/stale balance fetch) -
    # must not be silently coerced to 0 and treated as "definitely no money",
    # which is a different, more specific claim than "unknown".
    unknown = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=None, broker_buying_power=10_000_000.0)
    known_zero = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=0.0, broker_buying_power=10_000_000.0)
    assert unknown["quantity"] == 0
    assert unknown["binding_constraints"] == ["buying_power"]
    assert "could not be determined" in unknown["reason"]
    assert known_zero["quantity"] == 0
    assert known_zero["constraints"]["buying_power"] == 0
    assert "could not be determined" not in known_zero["reason"]


def test_broker_buying_power_none_fails_closed_distinctly_from_zero():
    # Same "unknown vs definitely zero" distinction as the virtual
    # buying_power check above, applied to the REAL broker figure - a failed
    # get_account_balance() call must refuse to size, not silently size as
    # if the broker had zero buying power (a different, more specific claim).
    unknown = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=None)
    known_zero = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=0.0)
    assert unknown["quantity"] == 0
    assert unknown["binding_constraints"] == ["broker_buying_power"]
    assert "could not be determined" in unknown["reason"]
    assert known_zero["quantity"] == 0
    assert known_zero["constraints"]["broker_buying_power"] == 0
    assert "could not be determined" not in known_zero["reason"]


def test_broker_buying_power_binds_tighter_than_virtual_allocation():
    # quantity = min(risk, virtual allocation, broker buying power, position
    # cap) - a real account with less buying power than the virtual model
    # thinks is available (e.g. connected to a smaller live account later)
    # must cap the trade even though the virtual allocation alone would
    # allow more.
    result = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=1_000_000.0, broker_buying_power=300.0
    )
    assert result["constraints"]["buying_power"] == 20000  # 1_000_000 // 50
    assert result["constraints"]["broker_buying_power"] == 6  # 300 // 50
    assert result["quantity"] == 6
    assert result["binding_constraints"] == ["broker_buying_power"]


def test_broker_buying_power_ample_does_not_bind():
    # A large sandbox/live balance shouldn't normally bind - confirms adding
    # the constraint doesn't change outcomes when it isn't the tightest one.
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=20.0, stop_price=18.0, available_buying_power=100000, broker_buying_power=10_000_000.0
    )
    assert result["quantity"] == 50
    assert "broker_buying_power" not in result["binding_constraints"]


def test_broker_buying_power_tied_with_virtual_allocation_reports_both_binding():
    result = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=500.0, broker_buying_power=500.0
    )
    assert result["constraints"]["buying_power"] == 10  # 500 // 50
    assert result["constraints"]["broker_buying_power"] == 10
    assert result["quantity"] == 10
    assert set(result["binding_constraints"]) == {"buying_power", "broker_buying_power"}


def test_entry_price_none_fails_closed():
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=None, stop_price=18.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["reason"] == "no valid entry price"


def test_stop_price_none_fails_closed():
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=None, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert "no valid stop" in result["reason"]


# --- invalid / malformed setups ---------------------------------------------


def test_quantity_zero_for_zero_stop():
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["reason"] == "no valid stop price to size risk against"


def test_quantity_direction_short_requires_stop_above_entry():
    # A stop BELOW entry (a valid LONG stop) is invalid for a short - the
    # mirror-image validation direction="short" adds.
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=50.0, stop_price=45.0,
        available_buying_power=100000, broker_buying_power=10_000_000.0, direction="short",
    )
    assert result["quantity"] == 0
    assert result["reason"] == "no valid stop above entry price to size risk against"


def test_quantity_direction_short_sizes_correctly_with_a_valid_stop_above_entry():
    # entry 50, stop 55 -> risk_per_share = 5 (positive, not -5) - a $100
    # risk budget should size exactly 20 shares, same as the long-side
    # mirror (entry 50, stop 45) would.
    result = pluto_app._compute_position_quantity(
        risk_budget=100.0, entry_price=50.0, stop_price=55.0,
        available_buying_power=100000, broker_buying_power=10_000_000.0, direction="short",
    )
    assert result["quantity"] == 20
    assert result["reason"] == ""


def test_quantity_zero_for_stop_at_or_above_entry():
    at_entry = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=50.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    above_entry = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=50.0, stop_price=55.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert at_entry["quantity"] == 0
    assert "no valid stop" in at_entry["reason"]
    assert above_entry["quantity"] == 0
    assert "no valid stop" in above_entry["reason"]


def test_quantity_zero_for_missing_entry_price():
    result = pluto_app._compute_position_quantity(risk_budget=100.0, entry_price=0, stop_price=18.0, available_buying_power=100000, broker_buying_power=10_000_000.0)
    assert result["quantity"] == 0
    assert result["reason"] == "no valid entry price"


# --- ties between constraints ------------------------------------------------


def test_tie_break_reports_both_constraints_when_genuinely_tied():
    # A genuine tie: risk and buying power both independently size to zero
    # whole shares. binding_constraints must list both, not silently pick one.
    result = pluto_app._compute_position_quantity(risk_budget=0.5, entry_price=10.0, stop_price=9.0, available_buying_power=0.5, broker_buying_power=10_000_000.0)
    assert result["constraints"]["risk"] == 0
    assert result["constraints"]["buying_power"] == 0
    assert result["quantity"] == 0
    assert sorted(result["binding_constraints"]) == ["buying_power", "risk"]


def test_tie_at_a_positive_quantity_reports_both_winners():
    # risk and buying power both land on exactly 4 shares - a real, non-zero
    # tie, not just a shared-failure tie.
    result = pluto_app._compute_position_quantity(risk_budget=40.0, entry_price=25.0, stop_price=15.0, available_buying_power=100.0, broker_buying_power=10_000_000.0)
    assert result["constraints"]["risk"] == 4  # 40 / 10 risk-per-share
    assert result["constraints"]["buying_power"] == 4  # 100 / 25
    assert result["quantity"] == 4
    assert sorted(result["binding_constraints"]) == ["buying_power", "risk"]


# --- reserved buying power - broker-authoritative, not lifecycle_state ----
#
# Redesigned after a real gap was caught in review: nothing in this codebase
# ever transitions an entry to the CLOSED lifecycle state when a position
# actually exits (no code path calls ol.transition(entry, ol.CLOSED, ...)
# anywhere), so trusting lifecycle_state for "is this still committing
# capital" would mean every position ever opened counts forever, even long
# after it's actually closed. _compute_committed_virtual_capital now checks
# live broker state (real open positions + real open orders) instead of this
# app's own bookkeeping - see test_capital_reconciliation.py for the
# specific scenarios this is meant to survive.


def _position(symbol, quantity, last_price):
    return {"symbol": symbol, "quantity": quantity, "last_price": last_price}


def _open_order(symbol, side, total_quantity, filled_quantity, limit_price):
    return {"symbol": symbol, "side": side, "total_quantity": total_quantity, "filled_quantity": filled_quantity, "limit_price": limit_price}


def test_committed_capital_values_held_position_at_current_price_not_entry_cost():
    # Bought at $100, now trading at $150 (a real unrealized gain) - net
    # liquidation value already reflects that higher market value, so this
    # must reserve at $150 (current), not $100 (entry cost) - reserving at
    # entry cost would let the $50/share unrealized gain masquerade as extra
    # available cash it isn't.
    positions = [_position("AAPL", 10, 150.0)]
    committed = pluto_app._compute_committed_virtual_capital(positions, [], tracked_tickers=["AAPL"])
    assert committed == pytest.approx(1500.0)


def test_committed_capital_reserves_only_unfilled_remainder_of_pending_order():
    # Requested 10, 4 already filled (and would show as a real position -
    # see the double-counting test below) - only the unfilled 6 are still a
    # resting reservation.
    orders = [_open_order("AAPL", "BUY", total_quantity=10, filled_quantity=4, limit_price=20.0)]
    committed = pluto_app._compute_committed_virtual_capital([], orders, tracked_tickers=["AAPL"])
    assert committed == pytest.approx(120.0)  # 6 unfilled x $20


def test_partial_fill_position_and_remainder_are_not_double_counted():
    # The filled 4 shares appear as a real open position (counted at current
    # market price); the unfilled 6 appear as the resting order's remainder
    # (counted at limit price). Both together represent the ONE order's
    # total exposure, not two.
    positions = [_position("AAPL", 4, 22.0)]  # the 4 filled shares, now worth $22 each
    orders = [_open_order("AAPL", "BUY", total_quantity=10, filled_quantity=4, limit_price=20.0)]
    committed = pluto_app._compute_committed_virtual_capital(positions, orders, tracked_tickers=["AAPL"])
    assert committed == pytest.approx(88.0 + 120.0)  # 4 x $22 (held) + 6 x $20 (still resting)


def test_committed_capital_ignores_untracked_tickers():
    # A position/order for a ticker this app never traded through autonomous
    # mode (e.g. the user's own manual trade) must not consume this budget.
    positions = [_position("TSLA", 100, 250.0)]
    orders = [_open_order("TSLA", "BUY", 50, 0, 250.0)]
    committed = pluto_app._compute_committed_virtual_capital(positions, orders, tracked_tickers=["AAPL"])
    assert committed == 0.0


def test_committed_capital_ignores_sell_orders():
    # A resting SELL (e.g. a protective stop/target leg) doesn't reserve
    # buying power - only BUY orders do.
    orders = [_open_order("AAPL", "SELL", 10, 0, 20.0)]
    committed = pluto_app._compute_committed_virtual_capital([], orders, tracked_tickers=["AAPL"])
    assert committed == 0.0


def test_committed_capital_empty_is_zero():
    assert pluto_app._compute_committed_virtual_capital([], [], tracked_tickers=[]) == 0.0


def test_available_buying_power_subtracts_committed_capital():
    assert pluto_app._compute_available_buying_power(total_equity=2000.0, committed_capital=400.0) == pytest.approx(1600.0)


def test_available_buying_power_never_negative():
    # Over-committed (shouldn't normally happen, but must fail safely rather
    # than returning a negative buying power a caller might mishandle).
    assert pluto_app._compute_available_buying_power(total_equity=500.0, committed_capital=2000.0) == 0.0


def test_pending_entry_reserves_buying_power_for_the_next_candidate_in_the_same_scan():
    # End-to-end of the actual bug being closed: candidate 1's fully-filled
    # position commits capital, candidate 2 in the SAME batch must see
    # reduced buying power - this is what makes sequential-candidate
    # oversubscription impossible even though the per-user scan lock only
    # prevents CONCURRENT scans, not sequential over-commitment within one.
    total_equity = 2000.0
    positions_after_candidate_1 = [_position("AAPL", 30, 50.0)]  # candidate 1 filled: $1,500 committed
    available_for_candidate_2 = pluto_app._compute_available_buying_power(
        total_equity, pluto_app._compute_committed_virtual_capital(positions_after_candidate_1, [], tracked_tickers=["AAPL"])
    )
    assert available_for_candidate_2 == pytest.approx(500.0)
    result = pluto_app._compute_position_quantity(
        risk_budget=1000.0, entry_price=50.0, stop_price=49.0, available_buying_power=available_for_candidate_2, broker_buying_power=10_000_000.0
    )
    assert result["quantity"] == 10  # 500 // 50, not 1000 // 50 - candidate 1's commitment is respected


# --- multiple simultaneous positions (portfolio-level aggregation) ---------


def test_multiple_open_positions_each_committing_capital_independently():
    positions = [_position("AAPL", 5, 200.0), _position("MSFT", 2, 400.0)]  # $1,000 + $800
    orders = [_open_order("NVDA", "BUY", 3, 0, 100.0)]  # pending, $300
    committed = pluto_app._compute_committed_virtual_capital(positions, orders, tracked_tickers=["AAPL", "MSFT", "NVDA"])
    assert committed == pytest.approx(2100.0)


def test_manual_and_autonomous_positions_on_the_same_ticker_cannot_be_distinguished():
    # Documented, known limitation, demonstrated rather than just claimed:
    # Webull's position API aggregates by symbol only - it has no concept of
    # "which shares came from autonomous mode vs. the user's own manual
    # trade". If autonomous mode holds 5 AAPL shares and the user manually
    # buys 20 more of the SAME ticker, the broker reports one combined
    # position of 25 - this function has no way to see only the 5 that are
    # actually this app's. The result is committed capital gets overstated
    # by the manual portion whenever this overlap happens; there is no false
    # positive in the other direction (untracked tickers are correctly
    # excluded - see test_committed_capital_ignores_untracked_tickers).
    autonomous_only_would_be = 5 * 200.0  # what SHOULD be committed if attribution were possible
    combined_broker_position = [_position("AAPL", 25, 200.0)]  # 5 autonomous + 20 manual, indistinguishable
    committed = pluto_app._compute_committed_virtual_capital(combined_broker_position, [], tracked_tickers=["AAPL"])
    assert committed == pytest.approx(25 * 200.0)
    assert committed > autonomous_only_would_be  # the overstatement this limitation causes, made explicit


# --- capital reconciliation: stale/legacy/missing local records ------------
#
# Point of these tests: prove committed capital is computed from the
# BROKER's current truth, not from this app's own (potentially stale,
# incomplete, or entirely absent) lifecycle bookkeeping - a record can never
# permanently reserve capital after the position is actually closed, and
# can never prematurely release capital while the position is still open.


def test_closed_position_releases_capital_even_though_lifecycle_state_never_reached_closed():
    # Simulates the exact gap found in review: an entry that reached
    # protection_confirmed_active and then genuinely closed (stop or target
    # hit) - nothing in this codebase ever flips its lifecycle_state to
    # CLOSED, so a lifecycle_state-based calculation would keep reserving
    # its capital forever. The broker-authoritative version must not.
    stale_entry_lifecycle_state = "protection_confirmed_active"  # never advanced to "closed" - the actual gap
    assert stale_entry_lifecycle_state != ol.CLOSED  # documents that this record really is stuck non-terminal

    # But the broker shows no position and no resting order for it anymore -
    # the real source of truth says it's closed, regardless of our record.
    committed = pluto_app._compute_committed_virtual_capital([], [], tracked_tickers=["AAPL"])
    assert committed == 0.0


def test_legacy_entry_with_no_lifecycle_state_at_all_is_handled_by_ticker_lookup():
    # An entry from before the state machine existed - no lifecycle_state
    # key whatsoever. is_transitional() would default this to "still open"
    # forever (ENTRY_SUBMITTED default), but the broker-authoritative
    # design never even looks at lifecycle_state for this calculation - only
    # the ticker (to know what to look up) and live broker data (to know
    # what's actually true).
    legacy_entry = {"ticker": "OLDTEST", "status": "placed"}  # no lifecycle_state key at all
    assert "lifecycle_state" not in legacy_entry

    tracked = {legacy_entry["ticker"]}
    # Broker shows nothing currently held or resting for it - correctly zero,
    # whether OLDTEST was a real closed trade or leftover test data.
    committed = pluto_app._compute_committed_virtual_capital([], [], tracked_tickers=tracked)
    assert committed == 0.0

    # If the broker DOES show a real position for that same legacy ticker,
    # it's correctly counted - proving the calculation follows the broker,
    # not whatever (or however little) our own record says.
    committed_with_real_position = pluto_app._compute_committed_virtual_capital(
        [_position("OLDTEST", 5, 30.0)], [], tracked_tickers=tracked
    )
    assert committed_with_real_position == pytest.approx(150.0)


def test_cancelled_or_rejected_order_reserves_nothing():
    # A CANCELLED/FAILED order simply won't appear in real_open_orders at all
    # (Webull's open-orders endpoint only returns orders still resting) -
    # nothing special has to happen for it to stop reserving capital.
    committed = pluto_app._compute_committed_virtual_capital([], [], tracked_tickers=["AAPL"])
    assert committed == 0.0


def test_externally_placed_broker_position_for_an_untracked_ticker_is_excluded():
    # A position the user opened manually (e.g. through Webull's own app),
    # for a ticker autonomous mode has never touched - tracked_tickers
    # (built from this app's own overnight_orders records) correctly
    # excludes it, so a manual trade never distorts this app's own sizing
    # decisions.
    committed = pluto_app._compute_committed_virtual_capital(
        [_position("EXTERNAL", 1000, 50.0)], [], tracked_tickers=["AAPL"]
    )
    assert committed == 0.0


# --- capital reconciliation: schema validation on well-formed containers ---
#
# A non-dict record (None, a string, an int) already raises on the first
# .get() call - covered by test_snapshot_fails_closed_when_positions_are_malformed.
# These cover the narrower, easier-to-miss gap: a real dict, with a required
# field simply MISSING (silently defaulted to 0 by .get(key, 0)) or present
# but NONSENSICAL (negative) - neither of which used to raise, so both used
# to silently UNDER-count committed capital instead (the dangerous
# direction: it overstates buying power, not understates it).


def test_position_missing_symbol_fails_closed():
    with pytest.raises(ValueError, match="symbol"):
        pluto_app._compute_committed_virtual_capital([{"quantity": 10, "last_price": 100.0}], [], tracked_tickers=["AAPL"])


def test_position_missing_quantity_fails_closed():
    with pytest.raises(ValueError, match="quantity"):
        pluto_app._compute_committed_virtual_capital([{"symbol": "AAPL", "last_price": 100.0}], [], tracked_tickers=["AAPL"])


def test_position_missing_last_price_fails_closed():
    with pytest.raises(ValueError, match="last_price"):
        pluto_app._compute_committed_virtual_capital([{"symbol": "AAPL", "quantity": 10}], [], tracked_tickers=["AAPL"])


def test_position_negative_quantity_fails_closed():
    with pytest.raises(ValueError, match="negative"):
        pluto_app._compute_committed_virtual_capital([_position("AAPL", -10, 100.0)], [], tracked_tickers=["AAPL"])


def test_position_negative_price_fails_closed():
    with pytest.raises(ValueError, match="negative"):
        pluto_app._compute_committed_virtual_capital([_position("AAPL", 10, -100.0)], [], tracked_tickers=["AAPL"])


def test_position_missing_fields_on_an_untracked_ticker_still_fails_closed():
    # Can't safely conclude a malformed record is irrelevant just because
    # its (also malformed) symbol doesn't look tracked - if the symbol
    # itself is unreadable, whether it's "ours" is exactly what can't be
    # determined, so this must fail closed rather than silently skip it.
    with pytest.raises(ValueError, match="symbol"):
        pluto_app._compute_committed_virtual_capital([{"quantity": 10, "last_price": 100.0}], [], tracked_tickers=["AAPL"])


def test_open_order_missing_side_fails_closed():
    with pytest.raises(ValueError, match="side"):
        pluto_app._compute_committed_virtual_capital(
            [], [{"symbol": "AAPL", "total_quantity": 10, "filled_quantity": 0, "limit_price": 100.0}], tracked_tickers=["AAPL"]
        )


def test_open_order_sell_side_with_no_other_fields_is_safely_skipped_not_rejected():
    # A well-formed, explicitly non-BUY side is a normal case (protective
    # stop/take-profit legs), not a malformed record - must not fail closed
    # just because it's missing fields this function never even reads for a
    # non-BUY order.
    committed = pluto_app._compute_committed_virtual_capital(
        [], [{"symbol": "AAPL", "side": "SELL"}], tracked_tickers=["AAPL"]
    )
    assert committed == 0.0


def test_open_order_missing_symbol_fails_closed():
    with pytest.raises(ValueError, match="symbol"):
        pluto_app._compute_committed_virtual_capital(
            [], [{"side": "BUY", "total_quantity": 10, "filled_quantity": 0, "limit_price": 100.0}], tracked_tickers=["AAPL"]
        )


def test_open_order_missing_total_quantity_fails_closed():
    with pytest.raises(ValueError, match="total_quantity"):
        pluto_app._compute_committed_virtual_capital(
            [], [{"symbol": "AAPL", "side": "BUY", "filled_quantity": 0, "limit_price": 100.0}], tracked_tickers=["AAPL"]
        )


def test_open_order_negative_limit_price_fails_closed():
    with pytest.raises(ValueError, match="negative"):
        pluto_app._compute_committed_virtual_capital([], [_open_order("AAPL", "BUY", 10, 0, -50.0)], tracked_tickers=["AAPL"])


def test_restart_does_not_lose_or_duplicate_committed_capital():
    # Nothing here is a running total or an in-memory accumulator - every
    # call recomputes from scratch off two live broker reads. A process
    # restart between two calls changes nothing: calling it "fresh" after a
    # simulated restart gives the identical answer as calling it the first
    # time, because there is no persisted intermediate state to lose.
    positions = [_position("AAPL", 10, 100.0)]
    orders = [_open_order("MSFT", "BUY", 5, 2, 50.0)]
    tracked = ["AAPL", "MSFT"]
    before_restart = pluto_app._compute_committed_virtual_capital(positions, orders, tracked_tickers=tracked)
    after_restart = pluto_app._compute_committed_virtual_capital(positions, orders, tracked_tickers=tracked)
    assert before_restart == after_restart == pytest.approx(1000.0 + 150.0)  # 10x$100 held + 3 unfilled x $50


# --- in-scan reservations: broker-side eventual consistency ----------------
#
# Redesigned after a second real gap was caught in review: re-reading the
# broker fresh before every candidate does NOT solve broker-side eventual
# consistency - an order accepted moments ago is not guaranteed to already
# appear in a get_open_orders() read taken immediately afterward. The fix is
# one reconciled snapshot per scan (_build_capital_snapshot) plus an
# in-scan local reservation added the instant each order is accepted
# (_reservation_notional), so candidate 2's available buying power is never
# computed by asking the broker a question it might still answer with
# yesterday's (or ten seconds ago's) truth.


def test_reservation_notional_is_full_requested_amount():
    assert pluto_app._reservation_notional(quantity=15, limit_price=50.0) == pytest.approx(750.0)


def test_available_with_reservations_subtracts_from_snapshot():
    assert pluto_app._compute_available_buying_power_with_reservations(1000.0, 750.0) == pytest.approx(250.0)


def test_available_with_reservations_never_negative():
    assert pluto_app._compute_available_buying_power_with_reservations(100.0, 500.0) == 0.0


def test_available_with_reservations_none_snapshot_propagates_none():
    # The snapshot itself couldn't be determined - no candidate this tick
    # can be sized, not just reduced to zero (0.0 would incorrectly read as
    # "verified zero buying power" rather than "unknown").
    assert pluto_app._compute_available_buying_power_with_reservations(None, 0.0) is None


def test_reservation_survives_broker_eventual_consistency():
    # The exact race this design exists to prevent: candidate 2 is sized
    # against the SAME snapshot value candidate 1 saw (simulating a broker
    # whose get_open_orders() has not yet caught up to the order candidate 1
    # just placed) - proving the LOCAL reservation, not a fresh broker read,
    # is what protects candidate 2 from oversubscribing.
    snapshot_available_buying_power = 1000.0  # one broker snapshot, taken once, at the top of the scan
    local_reservations = pluto_app._to_decimal(0.0)  # Decimal from the start - see test_local_reservations_accumulate_as_true_decimal

    available_for_candidate_1 = pluto_app._compute_available_buying_power_with_reservations(
        snapshot_available_buying_power, local_reservations
    )
    assert available_for_candidate_1 == 1000.0
    candidate_1_quantity, candidate_1_price = 15, 50.0  # uses $750 of the $1000
    local_reservations += pluto_app._reservation_notional(candidate_1_quantity, candidate_1_price)
    assert local_reservations == pytest.approx(750.0)

    # Candidate 2 is sized using the exact same snapshot number as candidate
    # 1 (the broker has NOT been re-queried - this is the point).
    available_for_candidate_2 = pluto_app._compute_available_buying_power_with_reservations(
        snapshot_available_buying_power, local_reservations
    )
    assert available_for_candidate_2 == pytest.approx(250.0)  # 1000 - 750, not 1000 again

    # And it actually changes the sizing outcome, not just the raw number:
    without_reservation = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=snapshot_available_buying_power, broker_buying_power=10_000_000.0
    )
    with_reservation = pluto_app._compute_position_quantity(
        risk_budget=100000.0, entry_price=50.0, stop_price=49.0, available_buying_power=available_for_candidate_2, broker_buying_power=10_000_000.0
    )
    assert without_reservation["quantity"] == 20  # what a stale re-read would wrongly allow
    assert with_reservation["quantity"] == 5  # what actually happens with the local reservation


def test_reservation_accumulates_across_three_candidates_against_one_snapshot():
    snapshot = 1000.0
    reservations = pluto_app._to_decimal(0.0)
    reservations += pluto_app._reservation_notional(10, 50.0)  # $500
    reservations += pluto_app._reservation_notional(4, 50.0)  # $200
    available_for_third = pluto_app._compute_available_buying_power_with_reservations(snapshot, reservations)
    assert available_for_third == pytest.approx(300.0)
    reservations += pluto_app._reservation_notional(6, 50.0)  # $300 - exactly exhausts it
    available_for_fourth = pluto_app._compute_available_buying_power_with_reservations(snapshot, reservations)
    assert available_for_fourth == 0.0


# --- local_reservations: true Decimal accumulation, never negative ---------


def test_reservation_notional_returns_a_decimal_not_a_float():
    from decimal import Decimal

    result = pluto_app._reservation_notional(quantity=15, limit_price=50.0)
    assert isinstance(result, Decimal)


def test_local_reservations_accumulate_as_true_decimal():
    # Accumulating in plain float can reintroduce binary-imprecision at the
    # ACCUMULATION step even when each individual term was computed exactly
    # in Decimal first (0.1 + 0.2 != 0.3 in float, for the same reason) -
    # this proves the running total itself stays exact across many additions,
    # not just each individual call.
    from decimal import Decimal

    total = pluto_app._to_decimal(0.0)
    for _ in range(10):
        total += pluto_app._reservation_notional(quantity=1, limit_price=0.1)
    assert total == Decimal("1.0")


def test_reservation_notional_rejects_a_negative_result():
    with pytest.raises(ValueError, match="negative"):
        pluto_app._reservation_notional(quantity=-5, limit_price=50.0)


# --- capital snapshot: broker failure / malformed data fails closed --------


def test_snapshot_succeeds_with_good_data():
    result = pluto_app._build_capital_snapshot(
        fetch_open_orders=lambda: [],
        real_open_positions=[_position("AAPL", 10, 100.0)],
        tracked_tickers=["AAPL"],
        total_equity=2000.0,
    )
    assert result == pytest.approx(1000.0)  # 2000 - 1000 committed


def test_snapshot_fails_closed_when_open_orders_fetch_raises():
    def _raise():
        raise ConnectionError("broker unreachable")

    result = pluto_app._build_capital_snapshot(
        fetch_open_orders=_raise, real_open_positions=[], tracked_tickers=["AAPL"], total_equity=2000.0
    )
    assert result is None


def test_snapshot_fails_closed_on_malformed_open_orders_response():
    # Not a list of dicts at all - processing this must fail closed, not
    # raise an uncaught exception that crashes the whole scan.
    result = pluto_app._build_capital_snapshot(
        fetch_open_orders=lambda: "not-a-list-of-orders",
        real_open_positions=[],
        tracked_tickers=["AAPL"],
        total_equity=2000.0,
    )
    assert result is None


def test_snapshot_fails_closed_when_positions_are_malformed():
    # real_open_positions is passed in already-fetched (see
    # _run_autonomous_trade_scan_locked - it's reused from the
    # open_position_count fetch), so malformed positions data is exercised
    # here as a bad list entry rather than a fetch failure.
    result = pluto_app._build_capital_snapshot(
        fetch_open_orders=lambda: [],
        real_open_positions=[None, "garbage", 42],
        tracked_tickers=["AAPL"],
        total_equity=2000.0,
    )
    assert result is None


def test_snapshot_logs_the_actual_exception_instead_of_failing_silently(monkeypatch):
    """Found live 2026-08-28: a candidate reached _compute_position_quantity
    with available_buying_power=None and skipped with "available buying
    power could not be determined" - zero trace anywhere of WHY, the exact
    same silent-degradation shape that caused candidates_found=0 for days
    earlier this session (a ticker-intelligence timeout defaulting to
    confidence=0 with no log line either). _build_capital_snapshot must
    still fail closed (return None) on any exception, but it must also log
    what the exception actually was, so a real cause is visible on the next
    occurrence instead of needing a fresh diagnostic add-and-redeploy cycle
    every time."""
    logged = []
    monkeypatch.setattr(pluto_app.logger, "warning", lambda *args, **kwargs: logged.append((args, kwargs)))

    def _raise():
        raise ConnectionError("broker unreachable")

    result = pluto_app._build_capital_snapshot(
        fetch_open_orders=_raise, real_open_positions=[], tracked_tickers=["AAPL"], total_equity=2000.0
    )

    assert result is None
    assert len(logged) == 1
    logged_args = logged[0][0]
    assert any("broker unreachable" in str(arg) for arg in logged_args), (
        f"expected the real exception message to be logged, got {logged_args!r}"
    )


# --- real broker buying power extraction ------------------------------------


def test_extract_broker_buying_power_reads_the_same_field_path_as_the_balance_display():
    balance = {"account_currency_assets": [{"buying_power": "1234.56"}]}
    assert pluto_app._extract_broker_buying_power(balance) == pytest.approx(1234.56)


def test_extract_broker_buying_power_missing_key_fails_closed_to_none():
    assert pluto_app._extract_broker_buying_power({}) is None


def test_extract_broker_buying_power_empty_assets_list_fails_closed_to_none():
    assert pluto_app._extract_broker_buying_power({"account_currency_assets": []}) is None


def test_extract_broker_buying_power_empty_string_fails_closed_to_none():
    # Webull has been observed to return "" for fields it can't populate -
    # must not be coerced to 0.0 (a real balance claim), and must not raise.
    balance = {"account_currency_assets": [{"buying_power": ""}]}
    assert pluto_app._extract_broker_buying_power(balance) is None


def test_extract_broker_buying_power_non_numeric_fails_closed_to_none():
    balance = {"account_currency_assets": [{"buying_power": "not-a-number"}]}
    assert pluto_app._extract_broker_buying_power(balance) is None


def test_extract_broker_buying_power_negative_fails_closed_to_none():
    # A negative buying power is a nonsensical/malformed read, not a real
    # broker state - fails closed rather than being passed through and
    # sized against as a hard negative ceiling.
    balance = {"account_currency_assets": [{"buying_power": "-5.00"}]}
    assert pluto_app._extract_broker_buying_power(balance) is None


def test_extract_broker_buying_power_malformed_container_fails_closed_to_none():
    assert pluto_app._extract_broker_buying_power({"account_currency_assets": "not-a-list"}) is None
    assert pluto_app._extract_broker_buying_power({"account_currency_assets": [None]}) is None


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "Infinity", "-inf"])
def test_extract_broker_buying_power_non_finite_fails_closed_to_none(raw):
    # float("nan")/float("inf") both parse successfully without raising -
    # and NaN compares False to everything including "< 0", so a NaN
    # reading would otherwise sail straight past the negative-value guard
    # and be returned as though it were a valid buying power.
    balance = {"account_currency_assets": [{"buying_power": raw}]}
    assert pluto_app._extract_broker_buying_power(balance) is None


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


def test_deployment_kill_switch_off_by_default(monkeypatch):
    monkeypatch.delenv("PLUTO_DISABLE_NEW_ENTRIES", raising=False)
    assert pluto_app._new_entries_disabled_by_deployment_kill_switch() is False


def test_deployment_kill_switch_recognizes_truthy_values(monkeypatch):
    for truthy in ("1", "true", "True", "TRUE", "yes", "on"):
        monkeypatch.setenv("PLUTO_DISABLE_NEW_ENTRIES", truthy)
        assert pluto_app._new_entries_disabled_by_deployment_kill_switch() is True, truthy


def test_deployment_kill_switch_ignores_falsy_or_garbage_values(monkeypatch):
    for value in ("0", "false", "", "no", "off", "banana"):
        monkeypatch.setenv("PLUTO_DISABLE_NEW_ENTRIES", value)
        assert pluto_app._new_entries_disabled_by_deployment_kill_switch() is False, value


# --- order fill / protection status interpretation -------------------------


def test_entry_fill_is_final_for_filled_cancelled_failed():
    assert pluto_app._entry_fill_is_final("FILLED") is True
    assert pluto_app._entry_fill_is_final("CANCELLED") is True
    assert pluto_app._entry_fill_is_final("FAILED") is True


def test_entry_fill_not_final_while_still_in_flight():
    assert pluto_app._entry_fill_is_final("SUBMITTED") is False
    assert pluto_app._entry_fill_is_final("PARTIAL FILLED") is False


def test_protective_leg_active_when_resting_or_partially_filled():
    assert pluto_app._protective_leg_is_active("SUBMITTED") is True
    assert pluto_app._protective_leg_is_active("PARTIAL FILLED") is True


def test_protective_leg_not_active_when_filled_cancelled_or_failed():
    assert pluto_app._protective_leg_is_active("FILLED") is False
    assert pluto_app._protective_leg_is_active("CANCELLED") is False
    assert pluto_app._protective_leg_is_active("FAILED") is False
