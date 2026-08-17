from __future__ import annotations

import auth
import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import record_overnight_order


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be approved,
    which a bare fixture user_id string alone does not satisfy."""
    user = auth.register_user(f"displaystatus-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


# --- unit tests for the mapping function itself -------------------------------------


def test_no_lifecycle_state_falls_back_to_the_raw_status_field():
    """A candidate that never reached order submission (below the
    confidence floor, sizing rejected it, LLM veto) has no lifecycle_state
    at all - order["status"] is the only, and correct, field for those."""
    order = {"status": "skipped", "reason_skipped": "confidence 40 below threshold"}
    assert pluto_app._overnight_order_display_status(order) == "skipped"


def test_missing_status_and_lifecycle_state_shows_unknown_not_a_crash():
    assert pluto_app._overnight_order_display_status({}) == "unknown"


def test_each_lifecycle_state_maps_to_a_distinct_human_label():
    seen_labels = set()
    for state in (
        ol.ENTRY_SUBMITTED, ol.ENTRY_PARTIALLY_FILLED, ol.ENTRY_FILLED, ol.PROTECTION_PENDING,
        ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED, ol.CLOSED, ol.ENTRY_FAILED,
        ol.UNKNOWN_SUBMISSION_STATE, ol.MANUALLY_RESOLVED_NO_ORDER,
    ):
        label = pluto_app._overnight_order_display_status({"status": "placed", "lifecycle_state": state})
        assert label != "placed"  # the whole point - must not still read the frozen status field
        assert label not in seen_labels, f"duplicate label for {state}: {label}"
        seen_labels.add(label)


def test_protection_confirmed_active_reads_as_filled_and_protected_not_placed():
    order = {"status": "placed", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE}
    assert pluto_app._overnight_order_display_status(order) == "Filled & protected"


def test_closed_lifecycle_state_reads_as_closed_not_placed():
    order = {"status": "placed", "lifecycle_state": ol.CLOSED}
    assert pluto_app._overnight_order_display_status(order) == "Closed"


def test_ambiguous_exit_unresolved_takes_priority_over_the_lifecycle_state_label():
    """A frozen, needs-manual-review position must never display as if
    everything is fine, even though its lifecycle_state is still
    PROTECTION_CONFIRMED_ACTIVE (see _flag_ambiguous_exit_unresolved -
    the ambiguous-exit path deliberately never transitions the entry to
    CLOSED)."""
    order = {
        "status": "placed",
        "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE,
        "ambiguous_exit_unresolved": True,
    }
    assert "manual review" in pluto_app._overnight_order_display_status(order).lower()
    assert "ambiguous exit" in pluto_app._overnight_order_display_status(order).lower()


def test_stop_protection_gap_takes_priority_over_the_lifecycle_state_label():
    order = {"status": "placed", "lifecycle_state": ol.PROTECTION_PENDING, "stop_protection_gap": True}
    label = pluto_app._overnight_order_display_status(order)
    assert "manual review" in label.lower()
    assert "protection gap" in label.lower()


def test_target_protection_gap_takes_priority_over_the_lifecycle_state_label():
    order = {"status": "placed", "lifecycle_state": ol.PROTECTION_PENDING, "target_protection_gap": True}
    label = pluto_app._overnight_order_display_status(order)
    assert "manual review" in label.lower()


def test_a_cleared_protection_gap_flag_no_longer_triggers_the_manual_review_label():
    """_confirm_and_finalize_protection clears these to None once
    confirmed - None/False must not be mistaken for "gap present"."""
    order = {"status": "placed", "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE, "stop_protection_gap": None, "target_protection_gap": None}
    assert pluto_app._overnight_order_display_status(order) == "Filled & protected"


# --- integration: the actual page renders the derived label, not the frozen field --


def test_trade_journal_page_shows_filled_and_protected_not_placed(user_id):
    registered_user_id = _registered_user(user_id[:8])
    record_overnight_order(registered_user_id, {
        "ticker": "AAPL", "status": "placed", "quantity": 5, "filled_quantity": 5,
        "limit_price": 100.0, "confidence": 80, "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE,
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    body = response.data.decode("utf-8")
    assert "Filled &amp; protected" in body or "Filled & protected" in body
    # The stale bug this fixes: the frozen status field must not be what's shown.
    assert not _status_cell_says_only_placed(body)


def test_trade_journal_page_shows_needs_manual_review_for_an_ambiguous_exit(user_id):
    registered_user_id = _registered_user(user_id[:8] + "amb")
    record_overnight_order(registered_user_id, {
        "ticker": "MSFT", "status": "placed", "quantity": 3, "filled_quantity": 3,
        "limit_price": 200.0, "confidence": 75, "lifecycle_state": ol.PROTECTION_CONFIRMED_ACTIVE,
        "ambiguous_exit_unresolved": True,
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    body = response.data.decode("utf-8")
    assert "Needs manual review" in body


def test_trade_journal_page_shows_partial_fill_quantity(user_id):
    registered_user_id = _registered_user(user_id[:8] + "pf")
    record_overnight_order(registered_user_id, {
        "ticker": "TSLA", "status": "placed", "quantity": 10, "filled_quantity": 4,
        "limit_price": 300.0, "confidence": 70, "lifecycle_state": ol.ENTRY_PARTIALLY_FILLED,
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    body = response.data.decode("utf-8")
    assert "4 / 10 filled" in body


def test_trade_journal_page_still_shows_skip_reason_for_pre_submission_skips(user_id):
    """A candidate that never reached submission at all (no
    lifecycle_state) must keep showing its original skip reason - this
    fix must not regress that existing, already-correct behavior."""
    registered_user_id = _registered_user(user_id[:8] + "sk")
    record_overnight_order(registered_user_id, {
        "ticker": "NFLX", "status": "skipped", "reason_skipped": "confidence 40 below threshold",
        "confidence": 40,
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    body = response.data.decode("utf-8")
    assert "confidence 40 below threshold" in body


def _status_cell_says_only_placed(html_body: str) -> bool:
    return "<td>placed</td>" in html_body
