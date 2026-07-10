from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
SETTINGS_FILE = BASE_DIR / "data" / "settings.json"


def _default_settings() -> Dict[str, object]:
    return {
        "theme": "Midnight",
        "notifications_enabled": True,
        "ai_confidence_threshold": 68,
        "scanner_frequency_seconds": 20,
        "market_hours": "09:30-16:00 ET",
        "paper_trading_enabled": True,
        "auto_suggestions_enabled": True,
        "show_mission_brief_again": False,
        "mission_brief_last_viewed_date": "",
        "api_status": "Operational",
        "trusted_news_sources": ["Official X API", "Yahoo Finance News", "MarketWatch RSS"],
    }


def _ensure_file() -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        return
    SETTINGS_FILE.write_text(json.dumps(_default_settings(), indent=2), encoding="utf-8")


def get_settings() -> Dict[str, object]:
    _ensure_file()
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_settings()
    merged = {**_default_settings(), **payload}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def update_settings(payload: Dict[str, object]) -> Dict[str, object]:
    settings = get_settings()
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
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def available_themes() -> List[str]:
    return ["Midnight", "Onyx", "Nebula"]
