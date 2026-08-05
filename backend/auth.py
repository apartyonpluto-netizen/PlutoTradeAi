from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USERS_FILE = DATA_DIR / "users.json"
USER_DATA_ROOT = DATA_DIR / "users"

MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 8

# Filenames that lived directly under data/ before the multi-user rewrite.
# Handed to the very first account created, so pre-existing watchlist/trades/
# webhook setup carries over instead of silently vanishing.
_LEGACY_DATA_FILES = (
    "watchlist.csv",
    "accounts.json",
    "alerts.json",
    "dismissed_alerts.json",
    "read_alerts.json",
    "settings.json",
    "autonomy_settings.json",
    "tradingview_alerts.json",
    "paper_trades.csv",
)


def _migrate_legacy_data(user_id: str) -> None:
    target_dir = user_dir(user_id)
    for filename in _LEGACY_DATA_FILES:
        legacy_path = DATA_DIR / filename
        target_path = target_dir / filename
        if legacy_path.is_file() and not target_path.exists():
            shutil.move(str(legacy_path), str(target_path))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_users_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def _read_users() -> List[Dict[str, Any]]:
    _ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    users = data if isinstance(data, list) else []
    return _backfill_role_fields(users)


def _backfill_role_fields(users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Accounts created before the admin-approval feature existed have no
    role/approved fields. Grandfather them all in as approved (they already
    had access) and make the very first account ever created the admin,
    since there's no other signal for who that should be."""
    if not users or all("approved" in user for user in users):
        return users
    for index, user in enumerate(users):
        user.setdefault("approved", True)
        user.setdefault("role", "admin" if index == 0 else "user")
    _write_users(users)
    return users


def _write_users(users: List[Dict[str, Any]]) -> None:
    _ensure_users_file()
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """User dict with the password hash stripped, safe to put in session/templates."""
    return {
        "id": user.get("id", ""),
        "username": user.get("username", ""),
        "created_at": user.get("created_at", ""),
        "role": user.get("role", "user"),
        "approved": bool(user.get("approved", True)),
    }


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_username(username)
    if not normalized:
        return None
    for user in _read_users():
        if normalize_username(user.get("username", "")) == normalized:
            return user
    return None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    for user in _read_users():
        if user.get("id") == user_id:
            return user
    return None


def list_all_user_ids() -> List[str]:
    """For scheduled/cron jobs that have no session to resolve a single user
    from - they need to check every registered account for autonomy state."""
    return [user["id"] for user in _read_users() if user.get("id")]


def user_dir(user_id: str) -> Path:
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def register_user(username: str, password: str) -> Dict[str, Any]:
    username = (username or "").strip()
    password = password or ""
    if len(username) < MIN_USERNAME_LENGTH:
        raise ValueError(f"Username must be at least {MIN_USERNAME_LENGTH} characters.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if get_user_by_username(username):
        raise ValueError("That username is already taken.")

    users = _read_users()
    is_first_user = len(users) == 0
    user = {
        "id": uuid.uuid4().hex[:12],
        "username": username,
        "password_hash": generate_password_hash(password),
        "created_at": _now_iso(),
        # The first account ever created is the admin and is auto-approved;
        # every account after that needs the admin to approve it before it
        # can log in - this is the invite/approval gate for public signups.
        "role": "admin" if is_first_user else "user",
        "approved": is_first_user,
    }
    users.append(user)
    _write_users(users)
    user_dir(user["id"])
    if is_first_user:
        _migrate_legacy_data(user["id"])
    return user


def is_admin(user_id: str) -> bool:
    user = get_user_by_id(user_id)
    return bool(user and user.get("role") == "admin")


def list_pending_users() -> List[Dict[str, Any]]:
    return [user for user in _read_users() if not user.get("approved", True)]


def approve_user(user_id: str) -> Dict[str, Any]:
    users = _read_users()
    for user in users:
        if user.get("id") == user_id:
            user["approved"] = True
            _write_users(users)
            return user
    raise ValueError("User not found.")


def reject_user(user_id: str) -> None:
    """Rejecting a pending signup deletes the account outright - it was never
    approved, so there's no data or activity worth keeping, and it frees the
    username for someone else to use."""
    users = _read_users()
    remaining = [user for user in users if user.get("id") != user_id]
    if len(remaining) == len(users):
        raise ValueError("User not found.")
    _write_users(remaining)
    target_dir = USER_DATA_ROOT / user_id
    if target_dir.exists():
        shutil.rmtree(target_dir)


def reset_password(username: str, new_password: str) -> Dict[str, Any]:
    """No email/SMS recovery is configured, so this is a username-only reset -
    acceptable for a personal single-operator dashboard, not a public multi-
    tenant product."""
    new_password = new_password or ""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    users = _read_users()
    normalized = normalize_username(username)
    for user in users:
        if normalize_username(user.get("username", "")) == normalized:
            user["password_hash"] = generate_password_hash(new_password)
            _write_users(users)
            return user
    raise ValueError("No account found with that username.")


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_user_by_username(username)
    if not user:
        return None
    if not check_password_hash(user.get("password_hash", ""), password or ""):
        return None
    return user


def find_user_by_webhook_token(token: str) -> Optional[Dict[str, Any]]:
    """TradingView webhooks carry only a token, no session - scan each user's
    accounts.json for a TradingView webhook_url containing this token."""
    if not token:
        return None
    for user in _read_users():
        user_id = user.get("id")
        if not user_id:
            continue
        accounts_file = user_dir(user_id) / "accounts.json"
        if not accounts_file.exists():
            continue
        try:
            accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(accounts, list):
            continue
        for account in accounts:
            if not isinstance(account, dict) or account.get("platform") != "tradingview":
                continue
            webhook_url = str(account.get("webhook_url", "") or "")
            if webhook_url and f"token={token}" in webhook_url:
                return user
    return None
