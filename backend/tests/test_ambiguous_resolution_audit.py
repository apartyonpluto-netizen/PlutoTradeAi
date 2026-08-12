from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from autonomy.ambiguous_resolution_audit import list_ambiguous_resolution_audit, record_ambiguous_resolution_audit


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
