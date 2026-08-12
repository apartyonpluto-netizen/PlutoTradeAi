from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from autonomy.ambiguous_resolution_audit import (
    GENESIS_HASH,
    RESOLUTION_PHASE_COMPLETED,
    RESOLUTION_PHASE_FAILED,
    RESOLUTION_PHASE_STARTED,
    _audit_file,
    find_incomplete_resolutions,
    list_ambiguous_resolution_audit,
    record_ambiguous_resolution_audit,
    verify_audit_chain,
)


def test_record_returns_the_stamped_record_with_an_id(user_id):
    stored = record_ambiguous_resolution_audit(user_id, {"administrator": "admin-1", "action": "release"})
    assert stored["administrator"] == "admin-1"
    assert stored["id"]


def test_list_reflects_every_recorded_entry_in_order(user_id):
    first = record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "AAPL"})
    second = record_ambiguous_resolution_audit(user_id, {"action": "link", "ticker": "MSFT"})
    stored = list_ambiguous_resolution_audit(user_id)
    assert [r["id"] for r in stored] == [first["id"], second["id"]]


def test_no_update_or_delete_function_exists():
    # The module is deliberately append-only - this is a structural
    # assertion, not just a convention, so a future edit can't quietly
    # reintroduce a way to mutate history.
    import autonomy.ambiguous_resolution_audit as audit_module

    public_names = [name for name in dir(audit_module) if not name.startswith("_")]
    assert not any("update" in name or "delete" in name or "remove" in name or "edit" in name for name in public_names)


def test_concurrent_audit_writes_do_not_lose_records(user_id):
    count = 25

    def _write(i: int):
        record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": f"T{i}"})

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(_write, range(count)))

    stored = list_ambiguous_resolution_audit(user_id)
    tickers = {r["ticker"] for r in stored}
    assert tickers == {f"T{i}" for i in range(count)}
    assert len(stored) == count


def test_two_users_audit_trails_are_isolated(user_id, other_user_id):
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "AAPL"})
    assert list_ambiguous_resolution_audit(other_user_id) == []


# --- hash chain: tamper-evident, not immutable ------------------------------


def test_first_record_chains_to_genesis(user_id):
    stored = record_ambiguous_resolution_audit(user_id, {"action": "release"})
    assert stored["prev_hash"] == GENESIS_HASH
    assert stored["seq"] == 0
    assert stored["record_hash"]


def test_second_record_chains_to_the_first_records_hash(user_id):
    first = record_ambiguous_resolution_audit(user_id, {"action": "release"})
    second = record_ambiguous_resolution_audit(user_id, {"action": "link"})
    assert second["prev_hash"] == first["record_hash"]
    assert second["seq"] == 1


def test_verify_audit_chain_passes_on_an_untampered_chain(user_id):
    for i in range(5):
        record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": f"T{i}"})
    result = verify_audit_chain(user_id)
    assert result == {"valid": True, "broken_at_seq": None, "checked": 5}


def test_verify_audit_chain_empty_is_valid(user_id):
    assert verify_audit_chain(user_id) == {"valid": True, "broken_at_seq": None, "checked": 0}


def test_verify_audit_chain_detects_an_edited_middle_record(user_id):
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "AAPL"})
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "MSFT"})
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "TSLA"})

    # Simulate direct disk tampering - editing record #1's content WITHOUT
    # going through this module's own (append-only) API, exactly the class
    # of access this module can never prevent, only detect.
    path = _audit_file(user_id)
    records = json.loads(path.read_text(encoding="utf-8"))
    records[1]["reason"] = "tampered - this was never actually written by an admin"
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    result = verify_audit_chain(user_id)
    assert result["valid"] is False
    assert result["broken_at_seq"] == 1


def test_verify_audit_chain_detects_a_deleted_middle_record(user_id):
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "AAPL"})
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "MSFT"})
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "TSLA"})

    path = _audit_file(user_id)
    records = json.loads(path.read_text(encoding="utf-8"))
    del records[1]  # remove the middle record entirely
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    result = verify_audit_chain(user_id)
    assert result["valid"] is False


def test_verify_audit_chain_detects_reordered_records(user_id):
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "AAPL"})
    record_ambiguous_resolution_audit(user_id, {"action": "release", "ticker": "MSFT"})

    path = _audit_file(user_id)
    records = json.loads(path.read_text(encoding="utf-8"))
    records.reverse()
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    result = verify_audit_chain(user_id)
    assert result["valid"] is False


def test_this_module_is_tamper_evident_not_immutable_by_design():
    # Structural proof of the exact distinction the docstrings make: direct
    # filesystem access (bypassing this module's API entirely) CAN still
    # alter the underlying file - verify_audit_chain can only ever detect
    # that after the fact, never prevent it. This test exists so a future
    # reader can't mistake "hash-chained" for "write-once storage".
    import os

    path = _audit_file("some-user-id-for-this-check")
    path.write_text("[]", encoding="utf-8")
    assert os.access(path, os.W_OK), "if this ever becomes read-only, the docstrings' claims need revisiting"


# --- find_incomplete_resolutions: the durable freeze marker -----------------


def _started(resolution_id: str, **extra) -> dict:
    return {"phase": RESOLUTION_PHASE_STARTED, "resolution_id": resolution_id, **extra}


def test_find_incomplete_resolutions_is_empty_with_no_records(user_id):
    assert find_incomplete_resolutions(user_id) == []


def test_a_started_record_with_no_pair_is_incomplete(user_id):
    record_ambiguous_resolution_audit(user_id, _started("res-1"))
    incomplete = find_incomplete_resolutions(user_id)
    assert len(incomplete) == 1
    assert incomplete[0]["resolution_id"] == "res-1"


def test_a_started_record_paired_with_completed_is_not_incomplete(user_id):
    record_ambiguous_resolution_audit(user_id, _started("res-1"))
    record_ambiguous_resolution_audit(user_id, {"phase": RESOLUTION_PHASE_COMPLETED, "resolution_id": "res-1"})
    assert find_incomplete_resolutions(user_id) == []


def test_a_started_record_paired_with_failed_is_not_incomplete(user_id):
    record_ambiguous_resolution_audit(user_id, _started("res-1"))
    record_ambiguous_resolution_audit(user_id, {"phase": RESOLUTION_PHASE_FAILED, "resolution_id": "res-1"})
    assert find_incomplete_resolutions(user_id) == []


def test_other_users_transactions_dont_affect_this_users_result(user_id, other_user_id):
    record_ambiguous_resolution_audit(other_user_id, _started("res-1"))
    assert find_incomplete_resolutions(user_id) == []


def test_multiple_independent_transactions_each_tracked_separately(user_id):
    record_ambiguous_resolution_audit(user_id, _started("res-1"))
    record_ambiguous_resolution_audit(user_id, _started("res-2"))
    record_ambiguous_resolution_audit(user_id, {"phase": RESOLUTION_PHASE_COMPLETED, "resolution_id": "res-1"})
    incomplete = find_incomplete_resolutions(user_id)
    assert [r["resolution_id"] for r in incomplete] == ["res-2"]


def test_records_without_a_phase_or_resolution_id_are_ignored_not_misread():
    # Every record written before this feature existed (or any other
    # future record type this module doesn't know about) has no "phase"
    # field at all - it must never be misread as an orphaned transaction.
    user_id = "legacy-record-user"
    record_ambiguous_resolution_audit(user_id, {"administrator": "admin-1", "action": "release"})
    assert find_incomplete_resolutions(user_id) == []
