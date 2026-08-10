from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _credentials_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "anthropic_credentials.json"


def get_anthropic_api_key(user_id: str) -> str:
    """Each user brings their own Anthropic API key - the LLM reasoning pass
    calls out per-candidate and costs real money per call, so this is opt-in
    and billed to whoever configured it, not a shared/global key."""
    creds_file = _credentials_file(user_id)
    if not creds_file.exists():
        return ""
    try:
        data = json.loads(creds_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("api_key", ""))


def is_anthropic_configured(user_id: str) -> bool:
    return bool(get_anthropic_api_key(user_id).strip())


def set_anthropic_api_key(user_id: str, api_key: str) -> None:
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("An API key is required.")
    _credentials_file(user_id).write_text(json.dumps({"api_key": api_key}, indent=2), encoding="utf-8")


def clear_anthropic_api_key(user_id: str) -> None:
    creds_file = _credentials_file(user_id)
    if creds_file.exists():
        creds_file.write_text(json.dumps({"api_key": ""}, indent=2), encoding="utf-8")
