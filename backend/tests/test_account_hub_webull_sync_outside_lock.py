from __future__ import annotations

import fcntl
import threading
from unittest.mock import patch

import account_hub

"""Found live 2026-08-26: two real Render "Instance failed - HTTP health
check failed (timed out after 5 seconds)" events landed within minutes of
the _accounts_file_lock fix (see test_account_hub_concurrent_access.py)
going live, and the user personally hit a 502. Root cause: connect_account
and test_account both called _sync_webull_account - a real network round
trip to the Webull OpenAPI sandbox (get_paper_accounts, get_account_balance)
- while still holding _accounts_file_lock. Since _load_accounts/get_accounts
is called on nearly every request, holding that per-user lock across a slow
broker call blocks every other request touching that user's accounts.json
- under real traffic, enough gunicorn sync workers can stall waiting on the
lock to fail Render's own health check.

These tests prove the fix: the lock is released (or never acquired) while
the Webull network call is in flight, so a concurrent request that only
needs the lock (e.g. a plain page load's get_accounts) is never blocked by
someone else's slow broker round trip."""

CREDS = {"app_key": "key", "app_secret": "secret"}


def _patch_webull_sync():
    return (
        patch.object(account_hub, "get_webull_credentials", return_value=CREDS),
        patch.object(account_hub, "is_webull_configured", return_value=True),
        patch.object(
            account_hub.webull_api,
            "get_paper_accounts",
            return_value=[{"account_id": "acct1", "account_type": "individual_cash"}],
        ),
        patch.object(
            account_hub.webull_api,
            "find_individual_cash_account",
            return_value={"account_id": "acct1", "account_number": "DEM2VVW3"},
        ),
        patch.object(
            account_hub.webull_api,
            "get_account_balance",
            return_value={
                "total_cash_balance": "1000000.00",
                "account_currency_assets": [{"buying_power": "2000000.00"}],
                "total_net_liquidation_value": "1000000.00",
            },
        ),
        patch.object(account_hub, "record_seed_balance_if_unset"),
    )


def _probe_lock_is_free_right_now(user_id: str) -> bool:
    """A NON-blocking probe against the exact same lock file
    _accounts_file_lock uses - unlike acquiring the (blocking) real lock,
    this returns immediately either way, so it can prove the lock was free
    at a specific instant rather than just "eventually became free"."""
    lock_path = account_hub.USER_DATA_ROOT / user_id / ".accounts.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        fd.close()


def _make_slow_get_account_balance(network_call_started, network_call_may_finish):
    def _slow_get_account_balance(*args, **kwargs):
        network_call_started.set()
        # Held open by the test thread below until it has finished probing
        # the lock, so the probe is guaranteed to land WHILE this call is
        # still in flight - not racing against a fixed sleep duration.
        network_call_may_finish.wait(timeout=5)
        return {
            "total_cash_balance": "1000000.00",
            "account_currency_assets": [{"buying_power": "2000000.00"}],
            "total_net_liquidation_value": "1000000.00",
        }

    return _slow_get_account_balance


def test_connect_account_does_not_hold_the_lock_during_the_webull_network_call(user_id):
    network_call_started = threading.Event()
    network_call_may_finish = threading.Event()
    lock_was_free_during_the_call = threading.Event()

    def _probe_once_the_call_has_started():
        assert network_call_started.wait(timeout=5), "the mocked network call never started"
        if _probe_lock_is_free_right_now(user_id):
            lock_was_free_during_the_call.set()
        network_call_may_finish.set()

    patches = _patch_webull_sync()
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        account_hub.webull_api,
        "get_account_balance",
        side_effect=_make_slow_get_account_balance(network_call_started, network_call_may_finish),
    ), patches[5]:
        prober = threading.Thread(target=_probe_once_the_call_has_started)
        prober.start()
        account_hub.connect_account(user_id, "webull")
        prober.join(timeout=5)

    assert lock_was_free_during_the_call.is_set(), (
        "a concurrent caller should be able to acquire the lock while connect_account's "
        "Webull network call is still in flight - the lock must not be held across it"
    )


def test_test_account_does_not_hold_the_lock_during_the_webull_network_call(user_id):
    patches = _patch_webull_sync()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        account_hub.connect_account(user_id, "webull")

    network_call_started = threading.Event()
    network_call_may_finish = threading.Event()
    lock_was_free_during_the_call = threading.Event()

    def _probe_once_the_call_has_started():
        assert network_call_started.wait(timeout=5), "the mocked network call never started"
        if _probe_lock_is_free_right_now(user_id):
            lock_was_free_during_the_call.set()
        network_call_may_finish.set()

    with patches[0], patches[1], patches[2], patches[3], patch.object(
        account_hub.webull_api,
        "get_account_balance",
        side_effect=_make_slow_get_account_balance(network_call_started, network_call_may_finish),
    ), patches[5]:
        prober = threading.Thread(target=_probe_once_the_call_has_started)
        prober.start()
        account_hub.test_account(user_id, "webull")
        prober.join(timeout=5)

    assert lock_was_free_during_the_call.is_set(), (
        "a concurrent caller should be able to acquire the lock while test_account's "
        "Webull network call is still in flight - the lock must not be held across it"
    )


def test_connect_account_still_persists_the_synced_fields(user_id):
    patches = _patch_webull_sync()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        account = account_hub.connect_account(user_id, "webull")

    assert account["status"] == "Connected"
    assert account["account_number"] == "DEM2VVW3"
    assert account["cash_balance"] == "1000000.00"

    stored = account_hub.get_accounts(user_id)
    webull = next(a for a in stored if a["platform"] == "webull")
    assert webull["status"] == "Connected"
    assert webull["account_number"] == "DEM2VVW3"
