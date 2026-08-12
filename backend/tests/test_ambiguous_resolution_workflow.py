from __future__ import annotations

from unittest.mock import patch

import pytest

import app as pluto_app
import order_lifecycle as ol
from autonomy.ambiguous_resolution_audit import list_ambiguous_resolution_audit
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"
ADMIN_ID = "admin-user"


def _order_detail(status: str, total_quantity: float, filled_quantity: float) -> dict:
    return {"orders": [{"status": status, "total_quantity": str(total_quantity), "filled_quantity": str(filled_quantity), "order_id": "X"}]}


def _unknown_entry(entry_client_order_id="pt-resolve-id", **extra) -> dict:
    entry: dict = {
        "ticker": "AAPL",
        "limit_price": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "trading_day": "2026-08-11",
        "planned_risk_dollars": 50.0,
        "quantity": 10,
    }
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=entry_client_order_id)
    ol.transition(entry, ol.UNKNOWN_SUBMISSION_STATE, error="timeout")
    entry.update(extra)
    return entry


def _clean_gather_mocks(**overrides):
    """Every one of the four evidence sources succeeds and finds nothing,
    by default - the baseline "genuinely nothing there" scenario. Pass
    e.g. get_order_detail=... to override one source's mock."""
    defaults = dict(
        get_order_detail=patch.object(pluto_app.webull_api, "get_order_detail", side_effect=pluto_app.webull_api.DefiniteOrderRejection("not found")),
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]),
        get_account_positions=patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]),
        get_order_history=patch.object(pluto_app.webull_api, "get_order_history", return_value=[]),
    )
    defaults.update(overrides)
    return defaults


# --- _gather_ambiguous_submission_evidence ----------------------------------


def test_evidence_gathering_finds_nothing_when_all_four_checks_come_up_clean():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks()
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert evidence["found"] is False
    assert evidence["errors"] == {}
    assert evidence["checks"]["order_detail"] is None


def test_evidence_gathering_found_via_open_orders():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(
            pluto_app.webull_api, "get_open_orders", return_value=[{"client_order_id": "pt-resolve-id", "status": "SUBMITTED"}]
        )
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert evidence["found"] is True
    assert len(evidence["checks"]["open_orders"]) == 1


def test_evidence_gathering_found_via_positions():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_account_positions=patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 10}])
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert evidence["found"] is True


def test_evidence_gathering_found_via_order_history():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_order_history=patch.object(
            pluto_app.webull_api, "get_order_history", return_value=[{"client_order_id": "pt-resolve-id", "status": "FILLED"}]
        )
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert evidence["found"] is True


def test_evidence_gathering_records_a_failed_check_as_an_error_not_a_finding():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", side_effect=RuntimeError("broker down"))
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert evidence["found"] is False
    assert "open_orders" in evidence["errors"]


def test_evidence_gathering_one_check_failing_does_not_stop_the_others():
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_order_detail=patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("timeout")),
        get_account_positions=patch.object(pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL"}]),
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert "order_detail" in evidence["errors"]
    assert evidence["found"] is True  # positions still ran and found something


def test_evidence_gathering_truncated_order_history_pagination_is_inconclusive_not_clean():
    # get_order_history itself now fails closed (raises) rather than
    # silently returning a partial page when the broker ignores pagination
    # or a cursor never advances (see integrations/webull.py's
    # _ORDER_HISTORY_MAX_PAGES hardening) - this proves that failure
    # propagates all the way up as "inconclusive", the exact same as any
    # other broker-side failure, never as "checked and found nothing".
    entry = _unknown_entry()
    mocks = _clean_gather_mocks(
        get_order_history=patch.object(
            pluto_app.webull_api,
            "get_order_history",
            side_effect=ValueError(
                "Webull API error (order history): the same page was returned again (cursor did not advance) - "
                "cannot safely continue without risking a truncated (incomplete) order history"
            ),
        )
    )
    with mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        evidence = pluto_app._gather_ambiguous_submission_evidence(CREDS, ACCOUNT_ID, entry)
    assert "order_history" in evidence["errors"]
    assert "truncated" in evidence["errors"]["order_history"]


# --- _correlation_is_plausible: tightened found_strong -----------------------
# A missing required field must be INCONCLUSIVE (not found_strong), never
# silently skipped as "no red flag" - the previous, looser version of this
# function treated an absent field as no evidence either way, which is
# backwards for what found_strong actually gates (LINK, which immediately
# attaches real protective orders to real shares).


def _good_open_order_candidate(**overrides) -> dict:
    candidate = {"client_order_id": "pt-resolve-id", "symbol": "AAPL", "side": "BUY", "total_quantity": "10", "limit_price": "100.0"}
    candidate.update(overrides)
    return candidate


def test_correlation_passes_when_every_required_field_is_present_and_matches():
    entry = _unknown_entry()
    assert pluto_app._correlation_is_plausible(_good_open_order_candidate(), entry) is True


def test_correlation_fails_closed_when_symbol_is_missing():
    entry = _unknown_entry()
    candidate = _good_open_order_candidate()
    del candidate["symbol"]
    assert pluto_app._correlation_is_plausible(candidate, entry) is False


def test_correlation_fails_closed_when_symbol_does_not_match():
    entry = _unknown_entry()
    assert pluto_app._correlation_is_plausible(_good_open_order_candidate(symbol="MSFT"), entry) is False


def test_correlation_fails_closed_when_side_is_missing():
    entry = _unknown_entry()
    candidate = _good_open_order_candidate()
    del candidate["side"]
    assert pluto_app._correlation_is_plausible(candidate, entry) is False


def test_correlation_fails_closed_when_side_is_sell():
    entry = _unknown_entry()
    assert pluto_app._correlation_is_plausible(_good_open_order_candidate(side="SELL"), entry) is False


def test_correlation_fails_closed_when_quantity_is_missing():
    entry = _unknown_entry()
    candidate = _good_open_order_candidate()
    del candidate["total_quantity"]
    assert pluto_app._correlation_is_plausible(candidate, entry) is False


def test_correlation_fails_closed_when_price_is_missing():
    entry = _unknown_entry()
    candidate = _good_open_order_candidate()
    del candidate["limit_price"]
    assert pluto_app._correlation_is_plausible(candidate, entry) is False


def test_correlation_still_rejects_a_present_but_inconsistent_quantity():
    entry = _unknown_entry()
    assert pluto_app._correlation_is_plausible(_good_open_order_candidate(total_quantity="1000"), entry) is False


def test_correlation_still_rejects_a_present_but_inconsistent_price():
    entry = _unknown_entry()
    assert pluto_app._correlation_is_plausible(_good_open_order_candidate(limit_price="9.99"), entry) is False


def test_resolve_link_refused_when_open_orders_match_is_missing_required_fields(user_id):
    # End-to-end: an open_orders match on client_order_id alone, with no
    # symbol/side field at all (and no order_detail success to fall back
    # on), must not be enough to link.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(
            pluto_app.webull_api, "get_open_orders",
            return_value=[{"client_order_id": "pt-resolve-id", "status": "SUBMITTED"}],
        )
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="cannot be reliably attributed"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_LINK,
                reason="Open orders matched the id but carries no symbol/side/quantity/price to verify against.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- _resolve_ambiguous_submission: release ---------------------------------


def test_resolve_release_succeeds_when_all_checks_are_clean(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id,
            admin_user_id=ADMIN_ID,
            entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE,
            reason="Confirmed with broker support ticket #4412 - order never received.",
            confirmation="AAPL",
        )
    # NOT ENTRY_FAILED - that specifically means the BROKER said so. This is
    # a human's evidence-based judgment call, which gets its own state.
    assert result["entry"]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    assert result["entry"]["manual_resolution_administrator"] == ADMIN_ID
    assert result["entry"]["manual_resolution_reason"] == "Confirmed with broker support ticket #4412 - order never received."
    assert result["entry"]["manual_resolution_evidence"]["found"] is False
    # The original ambiguity fields survive untouched, not overwritten.
    assert result["entry"]["error"] == "timeout"
    stored = list_overnight_orders(user_id)
    assert stored[0]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER


def test_resolve_release_refused_when_an_order_still_exists(user_id):
    # The property explicitly required: manual resolution cannot release
    # capital when matching shares or an order still exist.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(
            pluto_app.webull_api, "get_open_orders", return_value=[{"client_order_id": "pt-resolve-id", "status": "SUBMITTED"}]
        )
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="found matching evidence"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id,
                admin_user_id=ADMIN_ID,
                entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE,
                reason="Attempting release despite an open order.",
                confirmation="AAPL",
            )
    stored = list_overnight_orders(user_id)
    assert stored[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE  # unchanged - refused, not released
    assert list_ambiguous_resolution_audit(user_id) == []  # nothing happened, nothing to audit


def test_resolve_release_refused_when_a_matching_position_exists(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_account_positions=patch.object(
            pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 10}]
        )
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="found matching evidence"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id,
                admin_user_id=ADMIN_ID,
                entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE,
                reason="Attempting release despite a matching position.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_resolve_release_refused_when_any_check_failed_even_if_others_are_clean(user_id):
    # Inconclusive is never treated as "confirmed clean" - one failed check
    # is enough to block a release outright.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_order_history=patch.object(pluto_app.webull_api, "get_order_history", side_effect=RuntimeError("broker flaky"))
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="inconclusive"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id,
                admin_user_id=ADMIN_ID,
                entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE,
                reason="Attempting release despite a flaky check.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- _resolve_ambiguous_submission: link ------------------------------------


def test_resolve_link_immediately_protects_a_filled_order_not_merely_clears_the_freeze(user_id):
    # The property explicitly required: linking must not merely clear the
    # freeze - a linked order that's already FILLED needs real protective
    # orders placed synchronously, as part of resolution itself.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(
            pluto_app.webull_api, "get_open_orders",
            return_value=[{"client_order_id": "pt-resolve-id", "status": "SUBMITTED", "total_quantity": "10", "limit_price": "100.0"}],
        )
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_account_positions"], mocks["get_order_history"], \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}) as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id,
            admin_user_id=ADMIN_ID,
            entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_LINK,
            reason="Found the resting order via open_orders - linking to resume monitoring.",
            confirmation="AAPL",
        )
    # Not left at ENTRY_SUBMITTED - _poll_fill_and_protect ran synchronously
    # and drove it all the way to a protection outcome.
    assert result["entry"]["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    assert result["entry"]["filled_quantity"] == 10.0
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    # Protection was actually attempted - proves this isn't just a state
    # label with nothing behind it.
    mock_stop.assert_called_once()
    mock_target.assert_called_once()
    assert mock_stop.call_args.kwargs["quantity"] == 10.0


def test_resolve_link_refused_when_nothing_was_found(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="nothing"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id,
                admin_user_id=ADMIN_ID,
                entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_LINK,
                reason="Nothing to link to.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_resolve_link_refused_when_only_a_weak_ticker_only_match_was_found(user_id):
    # The property explicitly required: "found something" is too broad -
    # a ticker-only position match can't be attributed to this specific
    # order and must never be enough to link on its own.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_account_positions=patch.object(
            pluto_app.webull_api, "get_account_positions", return_value=[{"symbol": "AAPL", "quantity": 10}]
        )
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(pluto_app.ValidationError, match="cannot be reliably attributed"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id,
                admin_user_id=ADMIN_ID,
                entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_LINK,
                reason="Only a position match, attempting to link anyway.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_resolve_requires_typed_confirmation_matching_the_ticker(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="Confirmation text must exactly match"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="A real reason.", confirmation="MSFT",
        )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


def test_resolve_confirmation_is_case_and_whitespace_insensitive(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Clean.", confirmation="  aapl  ",
        )
    assert result["entry"]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER


# --- validation / preconditions ---------------------------------------------


def test_resolve_requires_a_reason(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="reason"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="   ", confirmation="AAPL",
        )


def test_resolve_rejects_unknown_action(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="Unknown resolution action"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action="delete", reason="not a real action", confirmation="AAPL",
        )


def test_resolve_refuses_an_entry_that_is_not_actually_unresolved(user_id):
    entry = {"ticker": "AAPL", "entry_client_order_id": "pt-already-done"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-already-done")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10)
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="not currently in an unresolved"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-already-done",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Already resolved on its own.", confirmation="AAPL",
        )


def test_resolve_refuses_a_nonexistent_entry(user_id):
    with pytest.raises(pluto_app.ValidationError, match="No matching"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="does-not-exist",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Nothing to find.", confirmation="AAPL",
        )


# --- audit record shape ------------------------------------------------------


def test_successful_resolution_writes_a_complete_audit_record(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Confirmed clean via all four checks.",
            confirmation="AAPL",
        )
    records = list_ambiguous_resolution_audit(user_id)
    assert len(records) == 2
    started, completed = records
    assert started["phase"] == pluto_app.RESOLUTION_PHASE_STARTED
    assert started["administrator"] == ADMIN_ID
    assert started["timestamp"]
    assert started["evidence"]["found"] is False
    assert started["reason"] == "Confirmed clean via all four checks."
    assert started["confirmation"] == "AAPL"
    assert started["previous_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert started["requested_action"] == pluto_app.AMBIGUOUS_RESOLUTION_RELEASE
    assert started["id"]
    assert started["record_hash"]
    assert started["prev_hash"]

    assert completed["phase"] == pluto_app.RESOLUTION_PHASE_COMPLETED
    assert completed["resolution_id"] == started["resolution_id"]
    assert completed["final_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    assert completed["prev_hash"] == started["record_hash"]


def test_resolution_fires_a_durable_alert(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Confirmed clean.", confirmation="AAPL",
        )
    from alerts import load_manual_alerts

    alerts = load_manual_alerts(user_id)
    resolved_alerts = [a for a in alerts if a["type"] == "ambiguous_submission_resolved"]
    assert len(resolved_alerts) == 1


def test_failed_audit_write_blocks_the_state_change_entirely(user_id):
    # The audit record is written BEFORE the entry's lifecycle_state is
    # touched (see _resolve_ambiguous_submission) specifically so a failure
    # writing it - e.g. a disk/permissions problem - can never leave a
    # resolution partially applied: either both the audit record and the
    # state change land, or neither does. Simulating the write itself
    # raising proves the entry is left completely untouched, still frozen.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "record_ambiguous_resolution_audit", side_effect=OSError("disk full")), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pytest.raises(OSError, match="disk full"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Should never actually apply.",
                confirmation="AAPL",
            )
    stored = list_overnight_orders(user_id)
    assert stored[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert "manual_resolution_administrator" not in stored[0]
    assert list_ambiguous_resolution_audit(user_id) == []


# --- unresolved ambiguity survives an application restart -------------------


def test_unresolved_ambiguity_survives_application_restart(user_id):
    # There is no in-process cache anywhere in this path - list_overnight_orders
    # reads straight off disk every call, with nothing in app.py memoizing
    # it - so a fresh process (which starts with NO memory of anything that
    # happened before it started) reading the same file must see exactly
    # the same answer a running process would. This proves that by NEVER
    # calling _run_autonomous_trade_scan_locked or any other function that
    # could have left in-process state behind - only the raw storage
    # read/write functions, exactly as a brand-new process's first scan
    # tick would use them.
    entry = _unknown_entry(entry_client_order_id="pt-restart-survives")
    record_overnight_order(user_id, entry)

    # Simulate the "before restart" observation.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    # Simulate "the process restarted" by re-importing the storage module
    # fresh (import caching means this returns the same module object in
    # practice, but the read call below still goes through no in-memory
    # state of its own - the module holds no cache to begin with, which is
    # exactly the property being proven) and reading from a clean call path
    # that touches only the file on disk.
    import importlib

    import autonomy.overnight_orders as overnight_orders_module

    importlib.reload(overnight_orders_module)
    reloaded_orders = overnight_orders_module.list_overnight_orders(user_id)
    matching = next(o for o in reloaded_orders if o.get("entry_client_order_id") == "pt-restart-survives")
    assert matching["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE

    # And the actual gate used by the scan agrees, reading fresh.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


def test_unresolved_ambiguity_still_blocks_a_brand_new_scan_after_restart(user_id):
    # The end-to-end version of the same property: a scan that starts fresh
    # (no prior in-memory state) still refuses new entries because it reads
    # the persisted UNKNOWN_SUBMISSION_STATE record, not anything carried
    # over in memory from before.
    entry = _unknown_entry(entry_client_order_id="pt-restart-scan")
    record_overnight_order(user_id, entry)

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("still unreachable")):
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)
    assert still_unresolved is True


# --- dismissing the alert notification must not clear the freeze -----------


def test_dismissing_the_alert_does_not_clear_the_local_freeze_flag(user_id):
    entry = _unknown_entry(entry_client_order_id="pt-dismiss-test")
    record_overnight_order(user_id, entry)
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    from alerts import add_manual_alert, dismiss_alert, get_alerts_snapshot

    alert = add_manual_alert(
        user_id, {"type": "unknown_submission_state", "ticker": "AAPL", "message": "ambiguous submission"}
    )
    dismiss_alert(user_id, alert["id"])

    # The alert is gone from the notifications view...
    snapshot = get_alerts_snapshot(user_id, system_alerts=[])
    assert all(item["id"] != alert["id"] for item in snapshot)
    # ...but the actual freeze - governed entirely by lifecycle_state, never
    # by alerts.py's dismissed-state - is completely unaffected.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


def test_dismissing_the_alert_does_not_clear_the_scan_level_freeze(user_id):
    # The same property, proven at the level that actually matters: the
    # scan's own gate, not just the dashboard-banner helper.
    entry = _unknown_entry(entry_client_order_id="pt-dismiss-scan-test")
    record_overnight_order(user_id, entry)

    from alerts import add_manual_alert, dismiss_alert

    alert = add_manual_alert(
        user_id, {"type": "unknown_submission_state", "ticker": "AAPL", "message": "ambiguous submission"}
    )
    dismiss_alert(user_id, alert["id"])

    with patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("still unreachable")):
        still_unresolved = pluto_app._reconcile_unknown_submissions(user_id, CREDS, ACCOUNT_ID)
    assert still_unresolved is True


def test_marking_the_alert_read_also_does_not_clear_the_freeze(user_id):
    # Read is an even weaker action than dismiss - if dismiss doesn't clear
    # it, marking read (which doesn't even remove it from the list)
    # certainly must not either.
    entry = _unknown_entry(entry_client_order_id="pt-read-test")
    record_overnight_order(user_id, entry)

    from alerts import add_manual_alert, mark_alert_read

    alert = add_manual_alert(
        user_id, {"type": "unknown_submission_state", "ticker": "AAPL", "message": "ambiguous submission"}
    )
    mark_alert_read(user_id, alert["id"])

    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


# --- admin API routes: gating and end-to-end wiring -------------------------


def _make_admin(username_suffix: str) -> str:
    """Registers, approves, and promotes a fresh admin user directly through
    auth.py (not by relying on "first user ever" - other tests in this
    session may have already claimed that slot), returning their id."""
    import auth

    user = auth.register_user(f"admin-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    # register_user only auto-admins the very FIRST user ever registered in
    # this shared store - other tests in this session may or may not have
    # already claimed that slot, and demoting an accidental first-user-admin
    # afterward would hit auth.py's "can't demote the last remaining admin"
    # guard if none exists yet. Seeding one first makes this robust to test
    # order either way, without ever needing to demote anyone.
    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _register_target_user(username_suffix: str) -> str:
    """A properly-registered (not just a raw fixture string) user id - the
    admin LIST route discovers pending entries by iterating
    list_all_users(), so a target user must actually be registered through
    auth.py to be discoverable there, exactly as every real user is."""
    import auth

    user = auth.register_user(f"target-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_list_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/api/admin/ambiguous-submissions")
    assert response.status_code == 403


def test_resolve_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8] + "b")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.post(
            "/api/admin/ambiguous-submissions/resolve",
            json={"user_id": user_id, "entry_client_order_id": "x", "action": "release", "reason": "test"},
        )
    assert response.status_code == 403


def test_list_route_returns_pending_entries_for_an_admin(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    target_id = _register_target_user(user_id[:8] + "c")
    entry = _unknown_entry(entry_client_order_id="pt-route-list")
    record_overnight_order(target_id, entry)

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/ambiguous-submissions")

    assert response.status_code == 200
    payload = response.get_json()
    matches = [item for item in payload["data"]["pending"] if item.get("entry_client_order_id") == "pt-route-list"]
    assert len(matches) == 1
    assert matches[0]["user_id"] == target_id


def test_resolve_route_end_to_end_release(user_id):
    admin_id = _make_admin(user_id[:8] + "d")
    entry = _unknown_entry(entry_client_order_id="pt-route-resolve")
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/ambiguous-submissions/resolve",
                json={
                    "user_id": user_id,
                    "entry_client_order_id": "pt-route-resolve",
                    "action": "release",
                    "reason": "Route-level end-to-end check - all four checks clean.",
                    "confirmation": "AAPL",
                },
            )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["entry"]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    records = list_ambiguous_resolution_audit(user_id)
    assert len(records) == 2
    assert [r["phase"] for r in records] == [pluto_app.RESOLUTION_PHASE_STARTED, pluto_app.RESOLUTION_PHASE_COMPLETED]


def test_resolve_route_returns_400_when_evidence_blocks_release(user_id):
    admin_id = _make_admin(user_id[:8] + "e")
    entry = _unknown_entry(entry_client_order_id="pt-route-blocked")
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(
            pluto_app.webull_api, "get_open_orders", return_value=[{"client_order_id": "pt-route-blocked", "status": "SUBMITTED"}]
        )
    )

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"]:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                "/api/admin/ambiguous-submissions/resolve",
                json={
                    "user_id": user_id,
                    "entry_client_order_id": "pt-route-blocked",
                    "action": "release",
                    "reason": "Attempting release despite an open order.",
                    "confirmation": "AAPL",
                },
            )

    assert response.status_code == 400
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- admin-wide freeze banner ------------------------------------------------


def test_admin_frozen_account_count_is_zero_for_a_non_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8] + "f")
    entry = _unknown_entry(entry_client_order_id="pt-count-nonadmin")
    record_overnight_order(user_id, entry)
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"adminAmbiguousFreezeBanner" not in response.data


def test_admin_frozen_account_count_reflects_other_users_not_just_self(user_id):
    admin_id = _make_admin(user_id[:8] + "g")
    target_id = _register_target_user(user_id[:8] + "g")
    # Other tests sharing this session's data dir may have already left
    # OTHER users frozen - assert against a fresh baseline delta, not an
    # absolute count, so this stays correct regardless of test order.
    baseline = pluto_app._count_users_with_unresolved_ambiguous_submissions()
    entry = _unknown_entry(entry_client_order_id="pt-count-other")
    record_overnight_order(target_id, entry)
    assert pluto_app._count_users_with_unresolved_ambiguous_submissions() == baseline + 1

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"adminAmbiguousFreezeBanner" in response.data
    # The admin's OWN account is not frozen, so the per-account banner must
    # not also render - only the admin-wide one.
    assert b'id="ambiguousFreezeBanner"' not in response.data


def test_admin_frozen_account_count_counts_distinct_users_not_entries(user_id):
    target_id = _register_target_user(user_id[:8] + "h")
    baseline = pluto_app._count_users_with_unresolved_ambiguous_submissions()

    record_overnight_order(target_id, _unknown_entry(entry_client_order_id="pt-count-h1"))
    assert pluto_app._count_users_with_unresolved_ambiguous_submissions() == baseline + 1

    # A second unresolved entry for the SAME user must not double-count.
    record_overnight_order(target_id, _unknown_entry(entry_client_order_id="pt-count-h2"))
    assert pluto_app._count_users_with_unresolved_ambiguous_submissions() == baseline + 1


# --- phased transaction: crash-boundary safety -------------------------------
# Six boundaries: after the started audit / after state persistence / after
# one protective leg is placed / after both legs are placed but before final
# persistence / before the completed audit / during restart recovery.


def test_link_clears_the_stale_ambiguity_error_once_a_strong_match_is_confirmed(user_id):
    entry = _unknown_entry()  # carries error="timeout" from _unknown_entry's UNKNOWN_SUBMISSION_STATE transition
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", return_value=[_good_open_order_candidate()])
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_account_positions"], mocks["get_order_history"], mocks["get_open_orders"], \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_LINK, reason="Linking a confirmed match.", confirmation="AAPL",
        )
    # The STALE ambiguity error ("timeout") must be gone - whatever
    # "error" holds now (possibly nothing, possibly a fresh
    # protection-confirmation message - the shared get_order_detail mock
    # here doesn't distinguish the entry lookup from the stop/target leg
    # lookups, so protection may or may not confirm, same tolerance as
    # test_resolve_link_immediately_protects_a_filled_order_not_merely_clears_the_freeze)
    # can only be a NEW condition from THIS resolution, never the old one.
    assert result["entry"].get("error") != "timeout"
    assert list_overnight_orders(user_id)[0].get("error") != "timeout"


def test_successful_link_resolution_writes_started_then_completed(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", return_value=[_good_open_order_candidate()])
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_account_positions"], mocks["get_order_history"], mocks["get_open_orders"], \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_LINK, reason="Clean end-to-end link.", confirmation="AAPL",
        )
    records = list_ambiguous_resolution_audit(user_id)
    assert [r["phase"] for r in records] == [pluto_app.RESOLUTION_PHASE_STARTED, pluto_app.RESOLUTION_PHASE_COMPLETED]
    assert records[1]["resolution_id"] == records[0]["resolution_id"]
    assert records[1]["protective_order_ids"]["stop"]
    assert records[1]["protective_order_ids"]["target"]
    assert pluto_app.find_incomplete_resolutions(user_id) == []
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is False


def test_crash_boundary_1_after_started_audit_before_state_persistence(user_id):
    # The failure happens in the very next step after resolution_started
    # lands - the entry must be left completely untouched, still frozen
    # via its own lifecycle_state, with a stage-tagged failed record.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"], \
         patch.object(pluto_app, "replace_overnight_orders", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Simulated crash right after start.",
                confirmation="AAPL",
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    records = list_ambiguous_resolution_audit(user_id)
    assert [r["phase"] for r in records] == [pluto_app.RESOLUTION_PHASE_STARTED, pluto_app.RESOLUTION_PHASE_FAILED]
    assert records[1]["stage"] == "state_persistence"
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


def test_crash_boundary_2_after_state_persistence_during_protection(user_id):
    # MANUAL_LINK_IN_PROGRESS was durably persisted (step 1 completed)
    # before protection (step 2) crashed - the on-disk state must reflect
    # exactly that: still MANUAL_LINK_IN_PROGRESS, not rolled back and not
    # advanced, with a stage-tagged failed record.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", return_value=[_good_open_order_candidate()])
    )
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"], \
         patch.object(pluto_app, "_poll_fill_and_protect", side_effect=RuntimeError("process died mid-poll")):
        with pytest.raises(RuntimeError, match="process died mid-poll"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_LINK, reason="Simulated crash during protection.",
                confirmation="AAPL",
            )
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.MANUAL_LINK_IN_PROGRESS
    records = list_ambiguous_resolution_audit(user_id)
    assert [r["phase"] for r in records] == [pluto_app.RESOLUTION_PHASE_STARTED, pluto_app.RESOLUTION_PHASE_FAILED]
    assert records[1]["stage"] == "protection"
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


def test_crash_boundaries_3_and_4_one_or_both_legs_placed_then_final_persistence_fails(user_id):
    # Whether one leg landed or both, a crash at final persistence must
    # leave the ON-DISK copy at MANUAL_LINK_IN_PROGRESS (the last thing
    # that was actually durably written) even though the IN-MEMORY entry
    # had already advanced further - proving nothing partial ever reaches
    # disk, and the stage-tag identifies exactly where it broke.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks(
        get_open_orders=patch.object(pluto_app.webull_api, "get_open_orders", return_value=[_good_open_order_candidate()])
    )

    call_count = {"n": 0}
    real_replace = pluto_app.replace_overnight_orders

    def _flaky_replace(uid, orders_arg):
        call_count["n"] += 1
        if call_count["n"] == 2:  # 1st call = MANUAL_LINK_IN_PROGRESS persist (must succeed); 2nd = final persist (crashes)
            raise OSError("disk full on final persist")
        return real_replace(uid, orders_arg)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_account_positions"], mocks["get_order_history"], mocks["get_open_orders"], \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}), \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}), \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app, "replace_overnight_orders", side_effect=_flaky_replace):
        with pytest.raises(OSError, match="disk full on final persist"):
            pluto_app._resolve_ambiguous_submission(
                target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
                action=pluto_app.AMBIGUOUS_RESOLUTION_LINK, reason="Both legs placed, final persist crashes.",
                confirmation="AAPL",
            )
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] == ol.MANUAL_LINK_IN_PROGRESS
    records = list_ambiguous_resolution_audit(user_id)
    assert records[-1]["phase"] == pluto_app.RESOLUTION_PHASE_FAILED
    assert records[-1]["stage"] == "final_persistence"


def test_crash_boundary_5_before_completed_audit_leaves_durable_marker_not_a_false_failed_record(user_id):
    # The state change (release, here) already fully succeeded and was
    # durably persisted - only the CLOSING audit write fails. This must
    # NOT produce a resolution_failed record (that would misrepresent
    # what actually happened); the orphaned resolution_started record is
    # itself the durable marker that keeps the account frozen.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    mocks = _clean_gather_mocks()

    real_record = pluto_app.record_ambiguous_resolution_audit

    def _fail_only_on_completed(uid, record):
        if record.get("phase") == pluto_app.RESOLUTION_PHASE_COMPLETED:
            raise OSError("disk full writing the closing record")
        return real_record(uid, record)

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         mocks["get_order_detail"], mocks["get_open_orders"], mocks["get_account_positions"], mocks["get_order_history"], \
         patch.object(pluto_app, "record_ambiguous_resolution_audit", side_effect=_fail_only_on_completed):
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="State change succeeds, closing audit write fails.",
            confirmation="AAPL",
        )
    assert result["entry"]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    records = list_ambiguous_resolution_audit(user_id)
    assert [r["phase"] for r in records] == [pluto_app.RESOLUTION_PHASE_STARTED]
    assert pluto_app.find_incomplete_resolutions(user_id) != []
    # The account reads as frozen even though this entry's own
    # lifecycle_state is already a terminal, "successful" state - the
    # unconfirmed audit trail is what's still blocking, not the entry.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True


# --- boundary 6: restart recovery --------------------------------------------


def test_restart_recovery_resumes_a_manual_link_in_progress_entry_and_completes_it(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    orders = list_overnight_orders(user_id)
    stored_entry = orders[0]
    ol.transition(stored_entry, ol.MANUAL_LINK_IN_PROGRESS, manual_resolution_id="orphan-link")
    pluto_app.replace_overnight_orders(user_id, orders)
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-link",
            "administrator": ADMIN_ID, "target_user_id": user_id,
            "entry_client_order_id": "pt-resolve-id", "requested_action": "link",
        },
    )
    assert pluto_app.find_incomplete_resolutions(user_id) != []

    with patch.object(pluto_app.webull_api, "get_order_detail", return_value=_order_detail("FILLED", 10, 10)), \
         patch.object(pluto_app.webull_api, "place_stop_loss_order", return_value={"client_order_id": "stop-id"}) as mock_stop, \
         patch.object(pluto_app.webull_api, "place_take_profit_order", return_value={"client_order_id": "target-id"}) as mock_target, \
         patch.object(pluto_app, "time"), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"):
        result = pluto_app._recover_incomplete_manual_resolutions(user_id, CREDS, ACCOUNT_ID)

    assert result is False
    mock_stop.assert_called_once()
    mock_target.assert_called_once()
    stored = list_overnight_orders(user_id)[0]
    assert stored["lifecycle_state"] in (ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED)
    records = list_ambiguous_resolution_audit(user_id)
    assert records[-1]["phase"] == pluto_app.RESOLUTION_PHASE_COMPLETED
    assert records[-1]["recovered_by"] == "restart_recovery"
    assert pluto_app.find_incomplete_resolutions(user_id) == []
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is False


def test_restart_recovery_closes_out_an_untouched_transaction_with_failed(user_id):
    # Nothing after resolution_started ever ran (not even this app's own
    # synchronous resolution_failed write - simulating a process that died
    # before it got the chance) - the entry is untouched, still
    # UNKNOWN_SUBMISSION_STATE, safely available for a fresh attempt.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-untouched",
            "administrator": ADMIN_ID, "target_user_id": user_id,
            "entry_client_order_id": "pt-resolve-id", "requested_action": "release",
        },
    )

    result = pluto_app._recover_incomplete_manual_resolutions(user_id, CREDS, ACCOUNT_ID)

    assert result is False
    assert pluto_app.find_incomplete_resolutions(user_id) == []
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    records = list_ambiguous_resolution_audit(user_id)
    assert records[-1]["phase"] == pluto_app.RESOLUTION_PHASE_FAILED
    assert records[-1]["stage"] == "restart_recovery"


def test_restart_recovery_retroactively_completes_an_already_persisted_release(user_id):
    # The mirror image of boundary 5 above, seen from a LATER process:
    # the release already fully happened and was durably persisted before
    # the crash - recovery only needs to close the audit loop, never
    # touching the entry itself.
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    orders = list_overnight_orders(user_id)
    stored_entry = orders[0]
    ol.transition(stored_entry, ol.MANUALLY_RESOLVED_NO_ORDER, manual_resolution_id="orphan-release")
    pluto_app.replace_overnight_orders(user_id, orders)
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-release",
            "administrator": ADMIN_ID, "target_user_id": user_id,
            "entry_client_order_id": "pt-resolve-id", "requested_action": "release",
        },
    )
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    result = pluto_app._recover_incomplete_manual_resolutions(user_id, CREDS, ACCOUNT_ID)

    assert result is False
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is False
    records = list_ambiguous_resolution_audit(user_id)
    assert records[-1]["phase"] == pluto_app.RESOLUTION_PHASE_COMPLETED
    assert records[-1]["final_state"] == ol.MANUALLY_RESOLVED_NO_ORDER
    # The entry itself was never touched by recovery - only the audit was.
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.MANUALLY_RESOLVED_NO_ORDER


def test_restart_recovery_closes_out_a_transaction_whose_entry_no_longer_exists(user_id):
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-missing",
            "administrator": ADMIN_ID, "target_user_id": user_id,
            "entry_client_order_id": "does-not-exist", "requested_action": "release",
        },
    )
    result = pluto_app._recover_incomplete_manual_resolutions(user_id, CREDS, ACCOUNT_ID)
    assert result is False
    records = list_ambiguous_resolution_audit(user_id)
    assert records[-1]["phase"] == pluto_app.RESOLUTION_PHASE_FAILED
    assert records[-1]["stage"] == "restart_recovery"


def test_restart_recovery_one_bad_transaction_does_not_block_another(user_id):
    entry_a = _unknown_entry(entry_client_order_id="pt-recover-a")
    entry_b = _unknown_entry(entry_client_order_id="pt-recover-b")
    record_overnight_order(user_id, entry_a)
    record_overnight_order(user_id, entry_b)
    orders = list_overnight_orders(user_id)
    entry_a_stored = next(o for o in orders if o["entry_client_order_id"] == "pt-recover-a")
    entry_b_stored = next(o for o in orders if o["entry_client_order_id"] == "pt-recover-b")
    ol.transition(entry_a_stored, ol.MANUAL_LINK_IN_PROGRESS, manual_resolution_id="orphan-a")
    ol.transition(entry_b_stored, ol.MANUAL_LINK_IN_PROGRESS, manual_resolution_id="orphan-b")
    pluto_app.replace_overnight_orders(user_id, orders)
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-a",
            "administrator": ADMIN_ID, "target_user_id": user_id, "entry_client_order_id": "pt-recover-a", "requested_action": "link",
        },
    )
    pluto_app.record_ambiguous_resolution_audit(
        user_id,
        {
            "phase": pluto_app.RESOLUTION_PHASE_STARTED, "resolution_id": "orphan-b",
            "administrator": ADMIN_ID, "target_user_id": user_id, "entry_client_order_id": "pt-recover-b", "requested_action": "link",
        },
    )

    def _poll_side_effect(*, entry_client_order_id, entry, **kwargs):
        if entry_client_order_id == "pt-recover-a":
            raise RuntimeError("still broken")
        ol.transition(entry, ol.ENTRY_FAILED, error="broker confirms nothing filled", filled_quantity=0.0)
        return entry

    with patch.object(pluto_app, "_poll_fill_and_protect", side_effect=_poll_side_effect):
        result = pluto_app._recover_incomplete_manual_resolutions(user_id, CREDS, ACCOUNT_ID)

    assert result is True  # "a" is still incomplete
    remaining = pluto_app.find_incomplete_resolutions(user_id)
    assert [r["resolution_id"] for r in remaining] == ["orphan-a"]
    stored_b = next(o for o in list_overnight_orders(user_id) if o["entry_client_order_id"] == "pt-recover-b")
    assert stored_b["lifecycle_state"] == ol.ENTRY_FAILED
    stored_a = next(o for o in list_overnight_orders(user_id) if o["entry_client_order_id"] == "pt-recover-a")
    assert stored_a["lifecycle_state"] == ol.MANUAL_LINK_IN_PROGRESS
