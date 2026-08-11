from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import anthropic_credentials
import crypto_utils
import webull_credentials
from rotate_credential_key import _rotate_file

OLD_KEY = "pytest-fixed-test-key-not-for-real-use"  # matches conftest.py's CREDENTIAL_ENCRYPTION_KEY default
NEW_KEY = "rotated-key-for-this-test-only"


def test_dry_run_does_not_modify_the_file(user_id):
    webull_credentials.set_webull_credentials(user_id, "AK-dryrun", "SECRET-dryrun")
    path = webull_credentials._credentials_file(user_id)
    before = path.read_text(encoding="utf-8")

    changed = _rotate_file(path, ["app_key", "app_secret"], OLD_KEY, NEW_KEY, apply=False)

    assert changed == 2
    assert path.read_text(encoding="utf-8") == before, "dry run must not touch the file on disk"


def test_apply_rotates_webull_credentials_and_old_key_stops_working(user_id):
    webull_credentials.set_webull_credentials(user_id, "AK-rotated", "SECRET-rotated")
    path = webull_credentials._credentials_file(user_id)

    changed = _rotate_file(path, ["app_key", "app_secret"], OLD_KEY, NEW_KEY, apply=True)
    assert changed == 2

    data = json.loads(path.read_text(encoding="utf-8"))
    assert crypto_utils.decrypt_with_key(data["app_key"], NEW_KEY) == "AK-rotated"
    assert crypto_utils.decrypt_with_key(data["app_secret"], NEW_KEY) == "SECRET-rotated"

    with pytest.raises(InvalidToken):
        crypto_utils.decrypt_with_key(data["app_key"], OLD_KEY)


def test_apply_rotates_anthropic_key(user_id):
    anthropic_credentials.set_anthropic_api_key(user_id, "sk-ant-rotate-me")
    path = anthropic_credentials._credentials_file(user_id)

    changed = _rotate_file(path, ["api_key"], OLD_KEY, NEW_KEY, apply=True)
    assert changed == 1

    data = json.loads(path.read_text(encoding="utf-8"))
    assert crypto_utils.decrypt_with_key(data["api_key"], NEW_KEY) == "sk-ant-rotate-me"


def test_empty_field_is_skipped_not_rotated(user_id):
    path = webull_credentials._credentials_file(user_id)
    path.write_text(json.dumps({"app_key": "", "app_secret": ""}), encoding="utf-8")

    changed = _rotate_file(path, ["app_key", "app_secret"], OLD_KEY, NEW_KEY, apply=True)
    assert changed == 0


def test_legacy_plaintext_field_is_skipped_not_double_encrypted(user_id):
    """A field that predates encryption (raw plaintext, no enc:v1: prefix)
    isn't something the rotation script should touch - the normal read path
    (get_webull_credentials) already migrates those to encrypted-under-the-
    current-key on next read, which happens under whatever key is live at the
    time, not whatever key this rotation run happens to be using."""
    path = webull_credentials._credentials_file(user_id)
    path.write_text(json.dumps({"app_key": "legacy-plaintext-key", "app_secret": "legacy-plaintext-secret"}), encoding="utf-8")

    changed = _rotate_file(path, ["app_key", "app_secret"], OLD_KEY, NEW_KEY, apply=True)
    assert changed == 0

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["app_key"] == "legacy-plaintext-key"


def test_round_trip_failure_raises_instead_of_writing_a_bad_value(user_id, monkeypatch):
    webull_credentials.set_webull_credentials(user_id, "AK-verify", "SECRET-verify")
    path = webull_credentials._credentials_file(user_id)
    before = path.read_text(encoding="utf-8")

    def _broken_encrypt(plaintext, key_material):
        return "enc:v1:not-actually-valid-for-this-key"

    monkeypatch.setattr(crypto_utils, "encrypt_with_key", _broken_encrypt)

    with pytest.raises(RuntimeError, match="Round-trip verification failed"):
        _rotate_file(path, ["app_key", "app_secret"], OLD_KEY, NEW_KEY, apply=True)

    assert path.read_text(encoding="utf-8") == before, "a failed round-trip must never partially write the file"
