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
        )
    assert result["entry"]["lifecycle_state"] == ol.ENTRY_FAILED
    stored = list_overnight_orders(user_id)
    assert stored[0]["lifecycle_state"] == ol.ENTRY_FAILED


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
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- _resolve_ambiguous_submission: link ------------------------------------


def test_resolve_link_succeeds_when_evidence_found_something(user_id):
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
        result = pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id,
            admin_user_id=ADMIN_ID,
            entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_LINK,
            reason="Found the resting order via open_orders - linking to resume monitoring.",
        )
    assert result["entry"]["lifecycle_state"] == ol.ENTRY_SUBMITTED
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.ENTRY_SUBMITTED


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
            )
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE


# --- validation / preconditions ---------------------------------------------


def test_resolve_requires_a_reason(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="reason"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="   ",
        )


def test_resolve_rejects_unknown_action(user_id):
    entry = _unknown_entry()
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="Unknown resolution action"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-resolve-id",
            action="delete", reason="not a real action",
        )


def test_resolve_refuses_an_entry_that_is_not_actually_unresolved(user_id):
    entry = {"ticker": "AAPL", "entry_client_order_id": "pt-already-done"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id="pt-already-done")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10)
    record_overnight_order(user_id, entry)
    with pytest.raises(pluto_app.ValidationError, match="not currently in an unresolved"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="pt-already-done",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Already resolved on its own.",
        )


def test_resolve_refuses_a_nonexistent_entry(user_id):
    with pytest.raises(pluto_app.ValidationError, match="No matching"):
        pluto_app._resolve_ambiguous_submission(
            target_user_id=user_id, admin_user_id=ADMIN_ID, entry_client_order_id="does-not-exist",
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Nothing to find.",
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
        )
    records = list_ambiguous_resolution_audit(user_id)
    assert len(records) == 1
    record = records[0]
    assert record["administrator"] == ADMIN_ID
    assert record["timestamp"]
    assert record["evidence"]["found"] is False
    assert record["reason"] == "Confirmed clean via all four checks."
    assert record["previous_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert record["new_state"] == ol.ENTRY_FAILED
    assert record["id"]


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
            action=pluto_app.AMBIGUOUS_RESOLUTION_RELEASE, reason="Confirmed clean.",
        )
    from alerts import load_manual_alerts

    alerts = load_manual_alerts(user_id)
    resolved_alerts = [a for a in alerts if a["type"] == "ambiguous_submission_resolved"]
    assert len(resolved_alerts) == 1


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
                },
            )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["entry"]["lifecycle_state"] == ol.ENTRY_FAILED
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.ENTRY_FAILED
    assert len(list_ambiguous_resolution_audit(user_id)) == 1


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
                },
            )

    assert response.status_code == 400
    assert list_overnight_orders(user_id)[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
