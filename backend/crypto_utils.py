from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets as secrets_module
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Plain stdlib logging rather than core.logger.get_logger - app.py resolves
# core via two different import styles depending on how it's launched, and a
# leaf module like this one shouldn't have to guess which applies. Logging
# to the child "plutotrade.crypto_utils" logger still reaches the handlers
# setup_logging() attaches to "plutotrade", since it propagates up.
logger = logging.getLogger("plutotrade.crypto_utils")

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
_KEY_FILE = DATA_DIR / ".credential_encryption_key"

ENCRYPTED_PREFIX = "enc:v1:"


def _resolve_key_material() -> str:
    """Same fallback pattern as the Flask session secret (env var, then a
    locally-persisted file, then freshly generated) so local dev and a fresh
    deploy without CREDENTIAL_ENCRYPTION_KEY set still work. Production
    should always set the env var explicitly - a Render disk wipe without it
    set would make previously-encrypted credentials unrecoverable, since the
    file-persisted fallback lives on that same disk."""
    env_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if env_key:
        return env_key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        stored = _KEY_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets_module.token_hex(32)
    _KEY_FILE.write_text(generated, encoding="utf-8")
    return generated


def _fernet() -> Fernet:
    # Fernet requires a specific 32-byte urlsafe-base64 key - derive one from
    # whatever secret string is configured via SHA-256 so the env var/file
    # value doesn't need to already be in that exact format.
    digest = hashlib.sha256(_resolve_key_material().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return ENCRYPTED_PREFIX + token


def decrypt(value: str) -> str:
    """Transparently handles legacy plaintext values written before this
    module existed - anything not carrying the enc:v1: prefix is assumed to
    be an old plaintext credential and returned as-is, so callers can
    re-encrypt it on next write instead of breaking existing connections."""
    if not value:
        return ""
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("Failed to decrypt a stored credential - CREDENTIAL_ENCRYPTION_KEY may have changed.")
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(ENCRYPTED_PREFIX)
