from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

AUTH_FILE = Path("data/auth_users.json")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> Tuple[bool, Optional[str]]:
    normalized = normalize_username(username)
    if not normalized:
        return False, "Username is required"
    if not USERNAME_PATTERN.match(normalized):
        return False, "Username must be 3-32 characters and use only letters, numbers, ., -, or _"
    return True, None


def load_users() -> Dict[str, Any]:
    if not AUTH_FILE.exists():
        return {"users": {}}

    try:
        payload = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"users": {}}

    if not isinstance(payload, dict):
        return {"users": {}}

    users = payload.get("users")
    if not isinstance(users, dict):
        return {"users": {}}

    return {"users": users}


def save_users(store: Dict[str, Any]) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps({"users": store.get("users", {})}, indent=2), encoding="utf-8")


def register_user(username: str, password: str) -> Tuple[bool, str]:
    normalized = normalize_username(username)
    valid, error = validate_username(normalized)
    if not valid:
        return False, error or "Invalid username"
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters"

    store = load_users()
    users = store["users"]
    if normalized in users:
        return False, "Username already exists"

    users[normalized] = {
        "username": normalized,
        "password_hash": generate_password_hash(password),
        "created_at": utc_now_iso(),
        "role": "user",
    }
    save_users(store)
    return True, "Account created"


def authenticate_user(username: str, password: str) -> Tuple[bool, str]:
    normalized = normalize_username(username)
    if not normalized or not password:
        return False, "Username and password are required"

    store = load_users()
    user = store.get("users", {}).get(normalized)
    if not user:
        return False, "Invalid username or password"

    if not check_password_hash(user.get("password_hash", ""), password):
        return False, "Invalid username or password"

    return True, "Authenticated"


def user_profile(username: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_username(username)
    return load_users().get("users", {}).get(normalized)


def user_namespace(username: str) -> str:
    normalized = normalize_username(username)
    return normalized if normalized else "guest"
