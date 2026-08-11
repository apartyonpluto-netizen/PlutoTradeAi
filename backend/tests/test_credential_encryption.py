from __future__ import annotations

import json

import anthropic_credentials
import crypto_utils
import webull_credentials


def test_webull_credentials_stored_encrypted_on_disk(user_id):
    webull_credentials.set_webull_credentials(user_id, "app-key-123", "app-secret-456")
    raw = json.loads(webull_credentials._credentials_file(user_id).read_text(encoding="utf-8"))
    assert raw["app_key"].startswith(crypto_utils.ENCRYPTED_PREFIX)
    assert raw["app_secret"].startswith(crypto_utils.ENCRYPTED_PREFIX)
    assert "app-key-123" not in raw["app_key"]
    assert "app-secret-456" not in raw["app_secret"]


def test_webull_credentials_round_trip(user_id):
    webull_credentials.set_webull_credentials(user_id, "app-key-123", "app-secret-456")
    creds = webull_credentials.get_webull_credentials(user_id)
    assert creds == {"app_key": "app-key-123", "app_secret": "app-secret-456"}


def test_webull_legacy_plaintext_file_still_reads_and_gets_migrated(user_id):
    creds_file = webull_credentials._credentials_file(user_id)
    creds_file.write_text(json.dumps({"app_key": "legacy-key", "app_secret": "legacy-secret"}), encoding="utf-8")

    creds = webull_credentials.get_webull_credentials(user_id)
    assert creds == {"app_key": "legacy-key", "app_secret": "legacy-secret"}

    raw_after = json.loads(creds_file.read_text(encoding="utf-8"))
    assert raw_after["app_key"].startswith(crypto_utils.ENCRYPTED_PREFIX)
    assert raw_after["app_secret"].startswith(crypto_utils.ENCRYPTED_PREFIX)

    creds_again = webull_credentials.get_webull_credentials(user_id)
    assert creds_again == {"app_key": "legacy-key", "app_secret": "legacy-secret"}


def test_webull_credential_swap_resets_seed_balance_correctly(user_id):
    webull_credentials.set_webull_credentials(user_id, "key-one", "secret-one")
    webull_credentials.record_seed_balance_if_unset(user_id, 100000.0)
    assert webull_credentials._read(user_id)["seed_balance"] == 100000.0

    webull_credentials.set_webull_credentials(user_id, "key-two", "secret-two")
    assert "seed_balance" not in webull_credentials._read(user_id)


def test_webull_same_key_rewrite_keeps_seed_balance(user_id):
    webull_credentials.set_webull_credentials(user_id, "key-one", "secret-one")
    webull_credentials.record_seed_balance_if_unset(user_id, 100000.0)

    webull_credentials.set_webull_credentials(user_id, "key-one", "secret-one-rotated")
    assert webull_credentials._read(user_id)["seed_balance"] == 100000.0


def test_anthropic_key_stored_encrypted_on_disk(user_id):
    anthropic_credentials.set_anthropic_api_key(user_id, "sk-ant-abc123")
    raw = json.loads(anthropic_credentials._credentials_file(user_id).read_text(encoding="utf-8"))
    assert raw["api_key"].startswith(crypto_utils.ENCRYPTED_PREFIX)
    assert "sk-ant-abc123" not in raw["api_key"]


def test_anthropic_key_round_trip(user_id):
    anthropic_credentials.set_anthropic_api_key(user_id, "sk-ant-abc123")
    assert anthropic_credentials.get_anthropic_api_key(user_id) == "sk-ant-abc123"
    assert anthropic_credentials.is_anthropic_configured(user_id) is True


def test_anthropic_legacy_plaintext_file_still_reads_and_gets_migrated(user_id):
    creds_file = anthropic_credentials._credentials_file(user_id)
    creds_file.write_text(json.dumps({"api_key": "sk-ant-legacy"}), encoding="utf-8")

    assert anthropic_credentials.get_anthropic_api_key(user_id) == "sk-ant-legacy"

    raw_after = json.loads(creds_file.read_text(encoding="utf-8"))
    assert raw_after["api_key"].startswith(crypto_utils.ENCRYPTED_PREFIX)
    assert anthropic_credentials.get_anthropic_api_key(user_id) == "sk-ant-legacy"


def test_anthropic_key_cleared(user_id):
    anthropic_credentials.set_anthropic_api_key(user_id, "sk-ant-abc123")
    anthropic_credentials.clear_anthropic_api_key(user_id)
    assert anthropic_credentials.get_anthropic_api_key(user_id) == ""
    assert anthropic_credentials.is_anthropic_configured(user_id) is False
