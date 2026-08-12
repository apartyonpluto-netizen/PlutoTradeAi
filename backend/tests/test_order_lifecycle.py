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
        (ol.UNKNOWN_SUBMISSION_STATE, ol.PROTECTION_CONFIRMED_ACTIVE),
        (ol.UNKNOWN_SUBMISSION_STATE, ol.ENTRY_FILLED),
        (ol.UNKNOWN_SUBMISSION_STATE, ol.CLOSED),
        (ol.ENTRY_FAILED, ol.UNKNOWN_SUBMISSION_STATE),
        (ol.PROTECTION_CONFIRMED_ACTIVE, ol.UNKNOWN_SUBMISSION_STATE),
    ],
)
def test_illegal_transitions_are_rejected(from_state, to_state):
    entry = {"lifecycle_state": from_state}
    with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
        ol.transition(entry, to_state)


# --- UNKNOWN_SUBMISSION_STATE: ambiguous entry submission -------------------


def test_entry_submitted_can_go_ambiguous_on_a_lost_response():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="connection timed out")
    assert entry["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert entry["error"] == "connection timed out"


def test_unknown_submission_state_is_not_terminal():
    # The whole point of this state is that it still needs monitoring/
    # reconciliation - it must never be silently treated as done.
    assert ol.UNKNOWN_SUBMISSION_STATE not in ol.TERMINAL_STATES
    assert ol.is_transitional({"lifecycle_state": ol.UNKNOWN_SUBMISSION_STATE}) is True


def test_unknown_submission_state_can_self_loop_on_repeated_inconclusive_reconciliation():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, last_reconciliation_error="still unreachable")
    assert entry["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert entry["last_reconciliation_error"] == "still unreachable"
    assert len(entry["lifecycle_history"]) == 3


def test_unknown_submission_state_resolves_to_entry_submitted_once_confirmed():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    ol.transition(entry, ol.ENTRY_SUBMITTED, error=None)
    assert entry["lifecycle_state"] == ol.ENTRY_SUBMITTED
    # From there, normal fill tracking is valid again.
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10)
    assert entry["lifecycle_state"] == ol.ENTRY_FILLED


# --- MANUALLY_RESOLVED_NO_ORDER: the human, evidence-based outcome ---------


def test_unknown_submission_state_can_resolve_to_manually_resolved_no_order():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    ol.transition(
        entry, ol.MANUALLY_RESOLVED_NO_ORDER,
        administrator="admin-1", reason="confirmed clean via all four checks",
    )
    assert entry["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    assert entry["administrator"] == "admin-1"


def test_manually_resolved_no_order_is_terminal():
    assert ol.MANUALLY_RESOLVED_NO_ORDER in ol.TERMINAL_STATES
    assert ol.is_transitional({"lifecycle_state": ol.MANUALLY_RESOLVED_NO_ORDER}) is False


def test_manually_resolved_no_order_is_distinct_from_entry_failed():
    # The whole point: a human's evidence-based judgment call is never the
    # same claim as a broker's own definite rejection - they must not
    # collapse into the same state.
    assert ol.MANUALLY_RESOLVED_NO_ORDER != ol.ENTRY_FAILED
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    ol.transition(entry, ol.MANUALLY_RESOLVED_NO_ORDER, administrator="admin-1", reason="x")
    assert entry["lifecycle_state"] != ol.ENTRY_FAILED


@pytest.mark.parametrize(
    "from_state",
    [ol.ENTRY_SUBMITTED, ol.ENTRY_FILLED, ol.PROTECTION_CONFIRMED_ACTIVE, ol.ENTRY_FAILED, ol.CLOSED],
)
def test_manually_resolved_no_order_is_only_reachable_from_unknown_submission_state(from_state):
    entry = {"lifecycle_state": from_state}
    with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
        ol.transition(entry, ol.MANUALLY_RESOLVED_NO_ORDER)


# --- MANUAL_LINK_IN_PROGRESS: the non-terminal manual-link marker ----------


def test_unknown_submission_state_can_transition_to_manual_link_in_progress():
    entry = {}
    ol.initialize(entry)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    ol.transition(entry, ol.MANUAL_LINK_IN_PROGRESS, manual_resolution_id="res-1")
    assert entry["lifecycle_state"] == ol.MANUAL_LINK_IN_PROGRESS
    assert entry["manual_resolution_id"] == "res-1"


def test_manual_link_in_progress_is_not_terminal():
    assert ol.MANUAL_LINK_IN_PROGRESS not in ol.TERMINAL_STATES
    assert ol.is_transitional({"lifecycle_state": ol.MANUAL_LINK_IN_PROGRESS}) is True


def test_manual_link_in_progress_is_a_frozen_state():
    assert ol.MANUAL_LINK_IN_PROGRESS in ol.FROZEN_STATES
    assert ol.UNKNOWN_SUBMISSION_STATE in ol.FROZEN_STATES


def test_manual_link_in_progress_can_advance_to_partially_filled_filled_or_failed():
    for target in (ol.ENTRY_PARTIALLY_FILLED, ol.ENTRY_FILLED, ol.ENTRY_FAILED):
        entry = {"lifecycle_state": ol.MANUAL_LINK_IN_PROGRESS}
        ol.transition(entry, target)
        assert entry["lifecycle_state"] == target


def test_manual_link_in_progress_cannot_jump_straight_to_protection_states():
    # _poll_fill_and_protect always passes through an ENTRY_* state first -
    # MANUAL_LINK_IN_PROGRESS mirrors ENTRY_SUBMITTED's own first-jump set,
    # nothing deeper in the chain.
    for target in (ol.PROTECTION_PENDING, ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED, ol.CLOSED, ol.MANUALLY_RESOLVED_NO_ORDER):
        entry = {"lifecycle_state": ol.MANUAL_LINK_IN_PROGRESS}
        with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
            ol.transition(entry, target)


@pytest.mark.parametrize(
    "from_state",
    [ol.ENTRY_SUBMITTED, ol.ENTRY_FILLED, ol.PROTECTION_CONFIRMED_ACTIVE, ol.ENTRY_FAILED, ol.CLOSED, ol.MANUALLY_RESOLVED_NO_ORDER],
)
def test_manual_link_in_progress_is_only_reachable_from_unknown_submission_state(from_state):
    entry = {"lifecycle_state": from_state}
    with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
        ol.transition(entry, ol.MANUAL_LINK_IN_PROGRESS)


def test_frozen_states_are_a_subset_of_non_terminal_states():
    # A frozen state that was somehow also terminal would mean an entry
    # could get stuck blocking new autonomous entries forever, with no
    # transition ever able to clear it - structurally impossible given how
    # TERMINAL_STATES/FROZEN_STATES are each built, but worth asserting
    # directly since the whole freeze mechanism depends on it.
    assert ol.FROZEN_STATES.isdisjoint(ol.TERMINAL_STATES)


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
