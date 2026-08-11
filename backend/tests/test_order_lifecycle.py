from __future__ import annotations

import pytest

import order_lifecycle as ol


def test_default_state_treated_as_entry_submitted_for_a_bare_entry():
    entry = {}
    assert ol.is_transitional(entry) is True


def test_valid_transition_chain_entry_to_confirmed_active():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE
    assert entry["filled_quantity"] == 10
    assert len(entry["lifecycle_history"]) == 4
    assert [h["state"] for h in entry["lifecycle_history"]] == [
        ol.ENTRY_SUBMITTED,
        ol.ENTRY_FILLED,
        ol.PROTECTION_PENDING,
        ol.PROTECTION_CONFIRMED_ACTIVE,
    ]
    for record in entry["lifecycle_history"]:
        assert record["at"]


def test_partial_fill_can_accumulate_before_reaching_full_fill():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_PARTIALLY_FILLED, filled_quantity=3)
    ol.transition(entry, ol.ENTRY_PARTIALLY_FILLED, filled_quantity=7)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10)
    assert entry["filled_quantity"] == 10


def test_partial_fill_can_go_straight_to_protecting_what_filled():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_PARTIALLY_FILLED, filled_quantity=4)
    ol.transition(entry, ol.PROTECTION_PENDING)
    assert entry["lifecycle_state"] == ol.PROTECTION_PENDING


def test_entry_can_fail_directly_from_submitted():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FAILED, error="rejected by broker")
    assert entry["lifecycle_state"] == ol.ENTRY_FAILED
    assert not ol.is_transitional(entry)


def test_protection_can_fail_and_then_retry():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FILLED)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="could not confirm within window")
    assert entry["lifecycle_state"] == ol.PROTECTION_FAILED
    # protection_failed is intentionally still monitorable (retry path), not terminal
    assert ol.is_transitional(entry) is True
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    assert entry["lifecycle_state"] == ol.PROTECTION_CONFIRMED_ACTIVE


def test_protection_failed_can_also_resolve_to_closed_via_failsafe():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FILLED)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED)
    ol.transition(entry, ol.CLOSED, close_reason="fail-safe emergency close - protection could not be confirmed")
    assert entry["lifecycle_state"] == ol.CLOSED
    assert not ol.is_transitional(entry)


def test_confirmed_active_position_closes_when_reconciliation_finds_it_gone():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FILLED)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.CLOSED, close_reason="stop triggered")
    assert entry["lifecycle_state"] == ol.CLOSED


def test_confirmed_active_can_return_to_pending_if_a_leg_goes_missing():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.ENTRY_FILLED)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.PROTECTION_PENDING, error="stop order no longer resting - re-protecting")
    assert entry["lifecycle_state"] == ol.PROTECTION_PENDING


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        (ol.ENTRY_SUBMITTED, ol.PROTECTION_CONFIRMED_ACTIVE),
        (ol.ENTRY_SUBMITTED, ol.CLOSED),
        (ol.PROTECTION_CONFIRMED_ACTIVE, ol.ENTRY_SUBMITTED),
        (ol.ENTRY_FAILED, ol.ENTRY_SUBMITTED),
        (ol.CLOSED, ol.PROTECTION_PENDING),
        (ol.ENTRY_FILLED, ol.ENTRY_SUBMITTED),
    ],
)
def test_illegal_transitions_are_rejected(from_state, to_state):
    entry = {"lifecycle_state": from_state}
    with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
        ol.transition(entry, to_state)


def test_terminal_states_are_never_transitional():
    for state in ol.TERMINAL_STATES:
        assert ol.is_transitional({"lifecycle_state": state}) is False


def test_non_terminal_states_are_always_transitional():
    for state in ol.ALL_STATES - ol.TERMINAL_STATES:
        assert ol.is_transitional({"lifecycle_state": state}) is True


def test_deterministic_id_is_stable_across_calls_for_the_same_attempt():
    id_a = ol.deterministic_client_order_id("user1", "aapl", "2026-08-11", "entry", attempt=1)
    id_b = ol.deterministic_client_order_id("user1", "AAPL", "2026-08-11", "entry", attempt=1)
    assert id_a == id_b, "ticker case should not affect the id - same logical attempt"


def test_deterministic_id_differs_by_user_ticker_day_leg_and_attempt():
    base = ol.deterministic_client_order_id("user1", "AAPL", "2026-08-11", "entry", attempt=1)
    variants = [
        ol.deterministic_client_order_id("user2", "AAPL", "2026-08-11", "entry", attempt=1),
        ol.deterministic_client_order_id("user1", "MSFT", "2026-08-11", "entry", attempt=1),
        ol.deterministic_client_order_id("user1", "AAPL", "2026-08-12", "entry", attempt=1),
        ol.deterministic_client_order_id("user1", "AAPL", "2026-08-11", "stop", attempt=1),
        ol.deterministic_client_order_id("user1", "AAPL", "2026-08-11", "entry", attempt=2),
    ]
    assert base not in variants
    assert len(set(variants)) == len(variants), "every variant should be distinct from each other too"


def test_deterministic_id_is_webull_safe_length_and_charset():
    order_id = ol.deterministic_client_order_id("user1", "AAPL", "2026-08-11", "entry", attempt=1)
    assert 20 <= len(order_id) <= 40
    assert order_id.isalnum()


def test_summarize_fill_extracts_from_real_response_shape():
    # Real shape captured from a live get_order_detail call against the
    # Webull sandbox this session.
    response = {
        "client_order_id": "pt-example",
        "combo_order_id": "HVJKDIDP6LNU0DUMND87DG16TA",
        "orders": [
            {
                "symbol": "AAPL",
                "side": "BUY",
                "status": "FILLED",
                "client_order_id": "pt-example",
                "order_id": "HVJKDIDP6LNU0DUMND87DG16TA",
                "total_quantity": "10",
                "filled_quantity": "10",
            }
        ],
    }
    summary = ol.summarize_fill(response)
    assert summary == {"status": "FILLED", "total_quantity": 10.0, "filled_quantity": 10.0, "order_id": "HVJKDIDP6LNU0DUMND87DG16TA"}


def test_summarize_fill_handles_partial():
    response = {"orders": [{"status": "PARTIAL FILLED", "total_quantity": "10", "filled_quantity": "4", "order_id": "X"}]}
    summary = ol.summarize_fill(response)
    assert summary["filled_quantity"] == 4.0
    assert summary["total_quantity"] == 10.0


def test_summarize_fill_handles_missing_orders_list_without_raising():
    summary = ol.summarize_fill({})
    assert summary["status"] == "UNKNOWN"
    assert summary["filled_quantity"] == 0.0
    assert summary["total_quantity"] == 0.0
