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
    return path / "webull_credentials.json"


def get_webull_credentials(user_id: str) -> Dict[str, str]:
    """Each user brings their own Webull OpenAPI app key/secret - there is no
    shared/global fallback, so one user can never see or trade on another
    user's Webull sandbox account."""
    creds_file = _credentials_file(user_id)
    if not creds_file.exists():
        return {"app_key": "", "app_secret": ""}
    try:
        data = json.loads(creds_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"app_key": "", "app_secret": ""}
    if not isinstance(data, dict):
        return {"app_key": "", "app_secret": ""}
    return {"app_key": str(data.get("app_key", "")), "app_secret": str(data.get("app_secret", ""))}


def is_webull_configured(user_id: str) -> bool:
    creds = get_webull_credentials(user_id)
    return bool(creds["app_key"] and creds["app_secret"])


def set_webull_credentials(user_id: str, app_key: str, app_secret: str) -> None:
    app_key = (app_key or "").strip()
    app_secret = (app_secret or "").strip()
    if not app_key or not app_secret:
        raise ValueError("Both App Key and App Secret are required.")
    _credentials_file(user_id).write_text(json.dumps({"app_key": app_key, "app_secret": app_secret}, indent=2), encoding="utf-8")


def clear_webull_credentials(user_id: str) -> None:
    creds_file = _credentials_file(user_id)
    if creds_file.exists():
        creds_file.write_text(json.dumps({"app_key": "", "app_secret": ""}, indent=2), encoding="utf-8")
