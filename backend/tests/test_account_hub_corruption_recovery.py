from __future__ import annotations

import json

import account_hub

"""Investigated a real production incident: a live, Webull-connected
account (real balance, real last-sync) was found fully reset to
disconnected (status, balances, last-sync all blanked) with zero trace of
what caused it - confirmed against 7 days of real logs that the one code
path supposed to be the only way this happens (disconnect_account) was
never actually called. It happened the same day as a Render plan upgrade
(an instance migration), and webull_credentials.json - a separate file in
the same per-user directory - survived completely untouched. Root cause:
_load_accounts previously treated "accounts.json doesn't exist" as
unconditionally meaning "brand new user" and silently wrote AND PERSISTED
fresh (disconnected) defaults - indistinguishable from "the persistent disk
isn't actually mounted yet." These tests prove the fix: a user directory
that already holds other data but is missing accounts.json specifically no
longer gets silently overwritten, and self-heals once the real file
reappears."""


def _user_dir(user_id: str):
    path = account_hub.USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_genuinely_new_user_gets_defaults_and_they_are_persisted(user_id):
    """The normal, safe case - an empty/nonexistent user directory really
    is a brand-new user, and creating+persisting defaults is correct."""
    accounts = account_hub.get_accounts(user_id)
    assert all(a["status"] == account_hub.STATUS_NOT_CONNECTED for a in accounts)

    accounts_file = account_hub.USER_DATA_ROOT / user_id / "accounts.json"
    assert accounts_file.exists(), "a genuinely new user's defaults should be persisted"


def test_missing_accounts_json_with_other_user_data_is_not_silently_recreated(user_id):
    """The actual incident, reproduced: webull_credentials.json (or any
    other per-user file) exists, but accounts.json does not - this must
    NOT be treated as a fresh signup."""
    user_dir = _user_dir(user_id)
    (user_dir / "webull_credentials.json").write_text(json.dumps({"app_key": "enc:whatever"}), encoding="utf-8")
    accounts_file = user_dir / "accounts.json"
    assert not accounts_file.exists()

    accounts = account_hub.get_accounts(user_id)

    # Still shows something sane for display...
    assert all(a["status"] == account_hub.STATUS_NOT_CONNECTED for a in accounts)
    # ...but critically, nothing was written - the suspected corruption is
    # never made permanent by this read.
    assert not accounts_file.exists(), "suspected disk-mount corruption must never be persisted"


def test_the_real_account_reappears_once_the_disk_catches_up(user_id):
    """Proves the actual "self-healing" claim: if the real accounts.json
    genuinely was just temporarily unavailable (not actually lost), a
    later request sees the real, connected data - because the earlier
    read never overwrote it."""
    user_dir = _user_dir(user_id)
    (user_dir / "webull_credentials.json").write_text(json.dumps({"app_key": "enc:whatever"}), encoding="utf-8")
    accounts_file = user_dir / "accounts.json"

    # First read: file genuinely missing (simulating the disk-mount race) -
    # must not persist fabricated defaults.
    account_hub.get_accounts(user_id)
    assert not accounts_file.exists()

    # The disk "catches up" - the real, previously-connected data appears.
    real_accounts = account_hub._default_accounts()
    for account in real_accounts:
        if account["platform"] == "webull":
            account["status"] = "Connected"
            account["account_number"] = "DEM2VVW3"
            account["cash_balance"] = "1000000.00"
            account["buying_power"] = "1000000.00"
            account["last_sync"] = "2026-08-21T15:08:43+00:00"
    accounts_file.write_text(json.dumps(real_accounts, indent=2), encoding="utf-8")

    # A later request must see the REAL data, not get stuck on the
    # in-memory-only defaults from the earlier read.
    accounts = account_hub.get_accounts(user_id)
    webull = next(a for a in accounts if a["platform"] == "webull")
    assert webull["status"] == "Connected"
    assert webull["account_number"] == "DEM2VVW3"


def test_empty_user_directory_with_no_files_at_all_is_still_treated_as_new(user_id):
    """An existing-but-empty directory (e.g., created by an earlier
    mkdir(parents=True) call with nothing written into it yet) is
    indistinguishable from "never touched" and should still get real,
    persisted defaults - only OTHER FILES are the corruption signal."""
    _user_dir(user_id)  # directory exists, but is empty
    accounts = account_hub.get_accounts(user_id)
    assert all(a["status"] == account_hub.STATUS_NOT_CONNECTED for a in accounts)
    accounts_file = account_hub.USER_DATA_ROOT / user_id / "accounts.json"
    assert accounts_file.exists()
