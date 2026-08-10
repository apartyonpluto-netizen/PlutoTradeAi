from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

if __package__:
    from .global_settings import get_global_settings
else:
    from global_settings import get_global_settings

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _settings_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    return USER_DATA_ROOT / user_id / "settings.json"


def _default_settings() -> Dict[str, object]:
    return {
        "theme": "Midnight",
        "notifications_enabled": True,
        "ai_confidence_threshold": get_global_settings()["default_ai_confidence_threshold"],
        "scanner_frequency_seconds": 20,
        "market_hours": "09:30-16:00 ET",
        "paper_trading_enabled": True,
        "auto_suggestions_enabled": True,
        "show_mission_brief_again": False,
        "mission_brief_last_viewed_date": "",
        "api_status": "Operational",
        "trusted_news_sources": ["Official X API", "Yahoo Finance News", "MarketWatch RSS"],
    }


def _ensure_file(user_id: str) -> Path:
    settings_file = _settings_file(user_id)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings_file.exists():
        settings_file.write_text(json.dumps(_default_settings(), indent=2), encoding="utf-8")
    return settings_file


def get_settings(user_id: str) -> Dict[str, object]:
    settings_file = _ensure_file(user_id)
    try:
        payload = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_settings()
    merged = {**_default_settings(), **payload}
    settings_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def update_settings(user_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    settings = get_settings(user_id)
    safe_updates: Dict[str, object] = {}
    for key in [
        "theme",
        "notifications_enabled",
        "ai_confidence_threshold",
        "scanner_frequency_seconds",
        "market_hours",
        "paper_trading_enabled",
        "auto_suggestions_enabled",
        "show_mission_brief_again",
        "mission_brief_last_viewed_date",
        "trusted_news_sources",
    ]:
        if key not in payload:
            continue
        safe_updates[key] = payload[key]
    if bool(safe_updates.get("show_mission_brief_again")):
        safe_updates["mission_brief_last_viewed_date"] = ""
    settings.update(safe_updates)
    _settings_file(user_id).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def available_themes() -> List[str]:
    return ["Midnight", "Onyx", "Nebula"]
