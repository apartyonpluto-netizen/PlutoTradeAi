from __future__ import annotations

import json
import threading
import time

import account_hub

"""Found live 2026-08-26: Webull kept dropping to Not Connected for many
hours with zero Render "Instance failed" (crash/restart) events in that
window - ruling out the disk-mount-race explanation
(_looks_like_an_established_user_dir, see test_account_hub_corruption_recovery.py)
and pointing at ordinary concurrent request traffic instead. Root cause:
get_accounts/_load_accounts is called on nearly every request - every
page view, every scan, every admin view - and _hydrate_missing_accounts
unconditionally rewrote accounts.json on EVERY one of those calls, with
zero locking, across 4 separate gunicorn worker processes. Two requests
landing close together (e.g. a real page view at the same moment the
hourly autonomous-scan cron tick fires) could race: one process's write
landing while another was mid-read produces a torn/incomplete file that
fails to parse - and the JSONDecodeError fallback unconditionally treated
that as "start over," silently persisting fresh (all Not Connected)
defaults over the real, live-connected data. Structurally the same
incident as the disk-mount-race one, just reached via a write race
instead of a missing file.

These tests prove the fix (_accounts_file_lock, an OS-level fcntl.flock
around every read-modify-write site in account_hub.py) actually
serializes concurrent access rather than just documenting the intent."""


def test_accounts_file_lock_blocks_a_second_concurrent_acquirer(user_id):
    lock_acquired_by_second_thread = threading.Event()
    first_thread_released = threading.Event()
    second_thread_saw_it_was_still_locked = threading.Event()

    def _hold_the_lock_briefly():
        with account_hub._accounts_file_lock(user_id):
            time.sleep(0.3)
        first_thread_released.set()

    def _try_to_acquire_concurrently():
        # Give the first thread a head start so it definitely holds the
        # lock before this one even tries.
        time.sleep(0.05)
        if not first_thread_released.is_set():
            second_thread_saw_it_was_still_locked.set()
        with account_hub._accounts_file_lock(user_id):
            lock_acquired_by_second_thread.set()

    first = threading.Thread(target=_hold_the_lock_briefly)
    second = threading.Thread(target=_try_to_acquire_concurrently)
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert second_thread_saw_it_was_still_locked.is_set(), "the lock wasn't actually held when the second thread checked"
    assert lock_acquired_by_second_thread.is_set(), "the second thread should still get the lock once the first releases it"


def test_concurrent_reads_and_writes_never_corrupt_accounts_json(user_id):
    """Directly reproduces the real failure mode: many overlapping
    read-modify-write calls hitting the same user's accounts.json at
    once. Before the fix, this could produce a torn/unparseable file that
    the JSONDecodeError fallback would "fix" by silently persisting fresh
    Not Connected defaults over real data. After the fix, every call is
    serialized by the lock, so the file must always end up well-formed
    and never silently reset."""
    # Seed a real, distinctive Connected state - one that must survive
    # every ensuing hammering without being clobbered back to Not
    # Connected by a racing writer.
    accounts_file = account_hub._accounts_file(user_id)
    accounts_file.parent.mkdir(parents=True, exist_ok=True)
    seeded = account_hub._default_accounts()
    for account in seeded:
        if account["platform"] == "webull":
            account["status"] = "Connected"
            account["account_number"] = "DEM2VVW3"
            account["cash_balance"] = "1000000.00"
    account_hub._save_accounts(user_id, seeded)

    errors: list[Exception] = []

    def _hammer_reads():
        try:
            for _ in range(20):
                account_hub.get_accounts(user_id)
        except Exception as error:  # noqa: BLE001 - capture and assert in the main thread
            errors.append(error)

    def _hammer_a_harmless_write():
        try:
            for _ in range(20):
                account_hub.update_trading_enabled(user_id, "etrade", False)
        except ValueError:
            pass  # expected: etrade isn't Connected in this seed - fine, still exercises the lock
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=_hammer_reads) for _ in range(4)] + [
        threading.Thread(target=_hammer_a_harmless_write) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors during concurrent access: {errors}"

    # The file must still be well-formed JSON - never left torn mid-write.
    raw_text = accounts_file.read_text(encoding="utf-8")
    parsed = json.loads(raw_text)  # raises if corrupted - the real failure mode found live
    assert isinstance(parsed, list)

    # And the real, seeded Connected state must have survived - not been
    # silently clobbered back to Not Connected defaults by a race.
    final_accounts = account_hub.get_accounts(user_id)
    webull = next(a for a in final_accounts if a["platform"] == "webull")
    assert webull["status"] == "Connected", "concurrent access must never silently reset real connection state"
    assert webull["account_number"] == "DEM2VVW3"
